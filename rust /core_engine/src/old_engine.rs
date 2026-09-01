/// Core engine orchestration layer.
///
/// Wires together every adapted ripgrep layer in order:
///
/// ```text
/// INPUT TEXT
///   ↓  normalise — trim, lowercase, wakeword strip
///   ↓  guard     — empty-input rejection
///   ↓  prefilter — find_candidate_line() fast literal gate (literal.rs)
///   ↓  per pattern that passes:
///        ↓  IntentMatcher::find()           — full DFA match
///        ↓  extract_entities()              — named capture groups
///        ↓  score_match()                   — weight × coverage
///        ↓  IntentSink::matched()           — push-model collection
///        ↓  early-stop if sink returns Ok(false)
///   ↓  IntentSink::finish()                 — sort by confidence desc
///   ↓  arbitrate()                          — dedup, threshold filter
///   ↓  return structured output
/// ```
///
/// Public entry points (called by lib.rs only):
///
/// * `RUNTIME.process(text)`        — full batch JSON `{"intents":[…]}`
/// * `RUNTIME.process_stream(text)` — single IntentResult JSON if score > 0.7,
///                                    otherwise falls back to full batch
/// * `RUNTIME.pattern_count()`      — compiled pattern count for health-checks

use std::collections::HashMap;

use grep_matcher::{Captures, LineMatchKind, Matcher};
use once_cell::sync::Lazy;
use serde::Serialize;

use crate::error::{EngineError, EngineErrorKind};
use crate::intent::{
    ExecutionType, IntentCategory, IntentPattern, IntentResult, score_match,
};
use crate::matcher::IntentMatcherBuilder;
use crate::patterns::REGISTRY;
use crate::sink::{IntentSink, Sink, SinkSummary};

use mlua::Lua;

// ─────────────────────────────────────────────────────────────────────────────
// CompiledPattern — static metadata + pre-built matcher
// ─────────────────────────────────────────────────────────────────────────────

/// A runtime-ready pattern: the `IntentPattern` descriptor plus a compiled
/// `IntentMatcher`.  Built once at startup, reused for every utterance.
struct CompiledPattern {
    meta:    &'static IntentPattern,
    matcher: crate::matcher::IntentMatcher,
}

// ─────────────────────────────────────────────────────────────────────────────
// EngineRuntime
// ─────────────────────────────────────────────────────────────────────────────

/// The runtime engine.  Created once via `RUNTIME` and reused across all
/// PyO3 calls — construction is thread-safe through `once_cell::Lazy`.
pub struct EngineRuntime {
    patterns:       Vec<CompiledPattern>,
    min_confidence: f32,
    max_intents:    usize,
    /// Wakeword to detect and strip before pattern matching.
    /// `None` disables wakeword gating entirely.
    wakeword:       Option<String>,
    multi_intent:    bool,
    strip_wakeword:  bool,
    stream_threshold: f32,
    //heap_limit:       Option<usize>, // Placeholder for future heap usage limit (currently unused)
}

// ─────────────────────────────────────────────────────────────────────────────
// Global singleton
// ─────────────────────────────────────────────────────────────────────────────

/// The single global engine instance.  Accessed by `lib.rs` as `engine::RUNTIME`.
pub static RUNTIME: Lazy<EngineRuntime> = Lazy::new(EngineRuntime::build);

// ─────────────────────────────────────────────────────────────────────────────
// EngineRuntime impl
// ─────────────────────────────────────────────────────────────────────────────

impl EngineRuntime {
    // ── Construction ─────────────────────────────────────────────────────────

    /// Build the engine from the global pattern `REGISTRY`.
    ///
    /// Patterns that fail to compile are logged and skipped — a single bad
    /// regex cannot crash the runtime.
    /// Load Pandora runtime knobs from `runtime/engine/engine_fields.lua`.
    /// Any missing or malformed key falls back to the hardcoded default
    /// so a broken Lua file can never crash the engine.
    
    // Compile-time embedded fallback — valid even if the live file is missing
    // on the deployed machine. Path is relative to this source file's location.
    const DEFAULT_LUA: &str = include_str!("../../runtime/engine/engine_fields.lua");

    fn lua_config_path() -> std::path::PathBuf {
        // Layer 1 — production: PANDORA_ROOT set by Python launcher or shell profile
        if let Ok(root) = std::env::var("PANDORA_ROOT") {
            return std::path::PathBuf::from(root)
                .join("runtime/engine/engine_fields.lua");
        }
        // Layer 2 — dev: walk up from cwd until runtime/ is found
        // Handles both `cargo test` (cwd = crate root) and direct invocation
        let mut dir = std::env::current_dir().unwrap_or_default();
        loop {
            let candidate = dir.join("runtime/engine/engine_fields.lua");
            if candidate.exists() {
                return candidate;
            }
            if !dir.pop() {
                break;
            }
        }
        // Layer 3 — last resort: bare relative path (works when cwd is PANDORA/)
        std::path::PathBuf::from("runtime/engine/engine_fields.lua")
    }

    fn load_lua_config() -> (f32, bool, usize, Option<String>, bool, f32) {
        // ── Hardcoded defaults — innermost failsafe, always valid ────────────────
        let mut min_confidence   = 0.60_f32;
        let mut multi_intent     = true;
        let mut max_intents      = 8_usize;
        let mut wakeword         = Some("pandora".to_string());
        let mut strip_wakeword   = true;
        let mut stream_threshold = 0.70_f32;

        // ── Read live file, fall back to compile-time embed if missing ────────────
        let contents = match std::fs::read_to_string(lua_config_path()) {
            Ok(s) => {
                log::debug!("[pandora_core] loaded engine_fields.lua from disk");
                s
            }
            Err(_) => {
                eprintln!(
                    "[pandora_core] engine_fields.lua not found on disk — \
                    falling back to embedded compile-time defaults"
                );
                DEFAULT_LUA.to_string()
            }
        };

        // ── Parse Lua ─────────────────────────────────────────────────────────────
        let lua = Lua::new();
        let table = match lua.load(&contents).eval::<mlua::Table>() {
            Ok(t)  => t,
            Err(e) => {
                eprintln!(
                    "[pandora_core] engine_fields.lua parse error: {e} — \
                    using hardcoded Rust defaults"
                );
                // Both live file and embed failed to parse — use Rust defaults
                return (min_confidence, multi_intent, max_intents,
                        wakeword, strip_wakeword, stream_threshold);
            }
        };

        // ── Overlay — each key is independent, one bad value won't affect others ──
        if let Ok(v) = table.get::<f32>("min_confidence")   { min_confidence   = v; }
        if let Ok(v) = table.get::<bool>("multi_intent")    { multi_intent     = v; }
        if let Ok(v) = table.get::<usize>("max_intents")    { max_intents      = v; }
        if let Ok(v) = table.get::<String>("wakeword")      { wakeword         = Some(v); }
        if let Ok(v) = table.get::<bool>("strip_wakeword")  { strip_wakeword   = v; }
        if let Ok(v) = table.get::<f32>("stream_threshold") { stream_threshold = v; }

        (min_confidence, multi_intent, max_intents,
        wakeword, strip_wakeword, stream_threshold)
    }

    pub fn build() -> Self {
        let builder = IntentMatcherBuilder::new();
        let mut patterns: Vec<CompiledPattern> = Vec::new();

        for meta in REGISTRY.iter() {
            match builder.build(meta.pattern) {
                Ok(matcher) => patterns.push(CompiledPattern { meta, matcher }),
                Err(e) => {
                    eprintln!(
                        "[pandora_core] WARNING: pattern '{}' failed to compile: {}",
                        meta.id, e
                    );
                }
            }
        }

        EngineRuntime {
            patterns,
            min_confidence:   0.60,
            max_intents:      8,
            wakeword:         Some("pandora".to_string()),
            multi_intent:     true,
            strip_wakeword:   true,
            stream_threshold: 0.70,
            //heap_limit:     None, // Placeholder for future heap usage limit (currently unused)
        }
    }

    // ── Public API ────────────────────────────────────────────────────────────

    /// Return the number of successfully compiled patterns.
    /// Used for startup health-checks from Python.
    pub fn pattern_count(&self) -> usize {
        self.patterns.len()
    }

    /// Process a voice utterance and return **all** matched intents as a JSON
    /// batch.
    ///
    /// Output shape: `{"intents": [ IntentResult, … ]}` sorted by confidence.
    /// Always returns at least one entry — falls back to `unknown` when nothing
    /// passes the confidence threshold.
    /// Errors only on empty / whitespace-only input.
    pub fn process(&self, text: &str) -> Result<String, EngineError> {
        if text.trim().is_empty() {
            return Err(EngineError::new(EngineErrorKind::EmptyInput));
        }

        let (normalised, wakeword_detected) = self.normalise(text);
        let raw   = self.run_pipeline(&normalised, wakeword_detected, 0.0);
        let final_ = self.arbitrate(raw);

        let output = if final_.is_empty() {
            vec![IntentResult::unknown(&normalised)]
        } else {
            final_
        };

        let batch = IntentBatch { intents: output };
        serde_json::to_string(&batch).map_err(EngineError::generic)
    }

    /// Process a voice utterance in **streaming / low-latency mode**.
    ///
    /// Returns a single `IntentResult` JSON object when one pattern scores ≥ 0.70
    /// so the Python VAD/STT layer can begin executing without waiting for the
    /// full scan.  Falls back to `process()` batch output when no result clears
    /// the streaming threshold.
    pub fn process_stream(&self, text: &str) -> Result<String, EngineError> {
        if text.trim().is_empty() {
            return Err(EngineError::new(EngineErrorKind::EmptyInput));
        }

        //const STREAM_THRESHOLD: f32 = 0.70;
        let STREAM_THRESHOLD = self.stream_threshold;
        
        let (normalised, wakeword_detected) = self.normalise(text);
        let raw     = self.run_pipeline(&normalised, wakeword_detected, STREAM_THRESHOLD);
        let results = self.arbitrate(raw);

        // Fast path: return the single top result if it clears the threshold.
        if let Some(top) = results.into_iter().next() {
            if top.confidence >= STREAM_THRESHOLD {
                return serde_json::to_string(&top).map_err(EngineError::generic);
            }
        }

        // Fallback: full batch (re-runs pipeline without the early-exit limit).
        self.process(text)
    }

    // ── Normalisation ─────────────────────────────────────────────────────────

    /// Trim, lowercase, and strip the wakeword from `raw`.
    ///
    /// Returns `(normalised_text, wakeword_was_detected)`.
    fn normalise(&self, raw: &str) -> (String, bool) {
        let trimmed = raw.trim();
        let lower   = trimmed.to_lowercase();

        let wakeword_detected = self.wakeword
            .as_deref()
            .map_or(false, |ww| lower.contains(ww));

        let normalised = if wakeword_detected && self.strip_wakeword {
            if let Some(ref ww) = self.wakeword {
                lower
                    .replace(&format!("hey {}", ww), "")
                    .replace(ww.as_str(), "")
                    .trim()
                    .to_string()
            } else {
                lower
            }
        } else {
            lower
        };

        (normalised, wakeword_detected)
    }

    // ── Prefilter gate ────────────────────────────────────────────────────────

    /// Fast literal prefilter using `find_candidate_line()` (mirrors ripgrep).
    ///
    /// Returns `true` when the matcher signals a `Candidate` or `Confirmed`
    /// literal hit, meaning the full DFA scan is warranted.  Patterns that
    /// produce no literal set skip this gate and always proceed to the DFA.
    #[inline]
    fn passes_candidate_check(compiled: &CompiledPattern, text_bytes: &[u8]) -> bool {
        matches!(
            compiled.matcher.find_candidate_line(text_bytes),
            Ok(Some(LineMatchKind::Candidate(_) | LineMatchKind::Confirmed(_)))
        )
    }

    // ── Entity extraction ─────────────────────────────────────────────────────

    /// Run the matcher in capture mode and extract every named entity slot
    /// declared in the pattern's `entity_slots` list.
    ///
    /// Uses `Matcher::captures()` → `Captures::get()` (both trait methods
    /// imported at the top of this file via `grep_matcher`).
    fn extract_entities(
        compiled:  &CompiledPattern,
        haystack: &[u8],
    ) -> HashMap<String, String> {
        let mut entities = HashMap::new();

        let mut caps = match compiled.matcher.new_captures() {
            Ok(c)  => c,
            Err(_) => return entities,
        };

        if compiled.matcher.captures(haystack, &mut caps).unwrap_or(false) {
            for &slot in compiled.meta.entity_slots {
                if let Some(idx) = compiled.matcher.capture_index(slot) {
                    if let Some(m) = caps.get(idx) {
                        let value = std::str::from_utf8(&haystack[m.start()..m.end()])
                            .unwrap_or("")
                            .trim()
                            .to_string();
                        if !value.is_empty() {
                            entities.insert(slot.to_string(), value);
                        }
                    }
                }
            }
        }

        entities
    }

    // ── Core matching loop ────────────────────────────────────────────────────

    /// Run the full five-stage pipeline over `normalised`.
    ///
    /// `early_exit_threshold > 0.0` enables the streaming fast-path: the loop
    /// breaks as soon as any collected result reaches that confidence level.
    fn run_pipeline(
        &self,
        normalised:            &str,
        wakeword_detected:      bool,
        early_exit_threshold:   f32,
    ) -> Vec<IntentResult> {
        let haystack  = normalised.as_bytes();
        let input_len = normalised.len();

        // Bare wakeword utterance — nothing to match after stripping.
        if normalised.trim().is_empty() && wakeword_detected {
            return vec![IntentResult {
                intent:         "wakeword".to_string(),
                category:       IntentCategory::Wakeword,
                execution_type: ExecutionType::Dialogue,
                confidence:     1.0,
                entities:       HashMap::new(),
                text:           normalised.to_string(),
            }];
        }

        let mut sink             = IntentSink::new(self.max_intents);
        let mut patterns_tested  = 0usize;
        let mut total_matches    = 0usize;
        let mut was_truncated    = false;

        'outer: for compiled in &self.patterns {
            patterns_tested += 1;

            // Stage 1 — literal prefilter gate
            if !Self::passes_candidate_check(compiled, haystack) {
                continue;
            }

            // Stage 2 — full DFA match
            let m = match compiled.matcher.find(haystack) {
                Ok(Some(m)) => m,
                _           => continue,
            };

            // Stage 3 — confidence scoring
            let match_len  = m.end() - m.start();
            let confidence = score_match(compiled.meta, match_len, input_len);

            if confidence < self.min_confidence {
                continue;
            }

            // Stage 4 — named entity extraction
            let entities = if !compiled.meta.entity_slots.is_empty() {
                Self::extract_entities(compiled, haystack)
            } else {
                HashMap::new()
            };

            // Stage 5 — push into sink
            let result = IntentResult {
                intent:         compiled.meta.id.to_string(),
                category:       compiled.meta.category.clone(),
                execution_type: compiled.meta.execution_type.clone(),
                confidence,
                entities,
                text:           normalised.to_string(),
            };

            total_matches += 1;

            match sink.matched(result) {
                Ok(true)  => {}
                Ok(false) => { was_truncated = true; break 'outer; }
                Err(_)    => break 'outer,
            }

            // ── multi_intent gate — stop after first successful push ──────────────
            if !self.multi_intent {
                was_truncated = true;
                break 'outer;
            }

            // Streaming early exit — stop as soon as we have a confident result.
            if early_exit_threshold > 0.0 {
                let best = sink.results().iter()
                    .map(|r| r.confidence)
                    .fold(0.0_f32, f32::max);
                if best >= early_exit_threshold {
                    was_truncated = true;
                    break 'outer;
                }
            }
        }

        let _ = sink.finish(SinkSummary {
            total_patterns_tested: patterns_tested,
            total_matches,
            was_truncated,
        });

        sink.into_results()
    }

    // ── Arbitration ───────────────────────────────────────────────────────────

    /// Post-collect arbitration:
    ///
    /// 1. Dedup — keep only the highest-confidence entry per `intent` id.
    /// 2. Threshold filter — drop anything still below `min_confidence`.
    /// 3. Re-sort descending by confidence.
    fn arbitrate(&self, mut results: Vec<IntentResult>) -> Vec<IntentResult> {
        let mut seen:   HashMap<String, usize> = HashMap::new();
        let mut deduped: Vec<IntentResult>     = Vec::new();

        for result in results.drain(..) {
            if let Some(&idx) = seen.get(&result.intent) {
                if result.confidence > deduped[idx].confidence {
                    deduped[idx] = result;
                }
            } else {
                seen.insert(result.intent.clone(), deduped.len());
                deduped.push(result);
            }
        }

        deduped.retain(|r| r.confidence >= self.min_confidence);
        deduped.sort_by(|a, b| {
            b.confidence
                .partial_cmp(&a.confidence)
                .unwrap_or(std::cmp::Ordering::Equal)
        });
        deduped
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// IntentBatch — JSON envelope for process() output
// ─────────────────────────────────────────────────────────────────────────────

#[derive(Serialize)]
struct IntentBatch {
    intents: Vec<IntentResult>,
}

// ─────────────────────────────────────────────────────────────────────────────
// Tests
// ─────────────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    /// Build a fresh engine and run `process()`, returning the intents vec.
    fn run(text: &str) -> Vec<IntentResult> {
        let engine = EngineRuntime::build();
        let json   = engine.process(text).expect("process failed");
        let batch: serde_json::Value = serde_json::from_str(&json).unwrap();
        serde_json::from_value(batch["intents"].clone()).unwrap()
    }

    #[test]
    fn wakeword_alone_returns_wakeword_intent() {
        let results = run("pandora");
        assert!(!results.is_empty());
        assert_eq!(results[0].intent, "wakeword");
    }

    #[test]
    fn greeting_detected() {
        let results = run("pandora good morning");
        assert!(results.iter().any(|r| r.intent.contains("greeting")));
    }

    #[test]
    fn shutdown_high_confidence() {
        let results = run("pandora shutdown");
        let r = results.iter().find(|r| r.intent == "shutdown");
        assert!(r.is_some(), "shutdown intent not found");
        assert!(r.unwrap().confidence > 0.7);
    }

    #[test]
    fn weather_entity_extracted() {
        let results = run("what's the weather in London");
        let r = results.iter().find(|r| r.intent == "weather_query");
        assert!(r.is_some(), "weather_query intent not found");
        assert_eq!(
            r.unwrap().entities.get("location").map(|s| s.as_str()),
            Some("london")
        );
    }

    #[test]
    fn streaming_returns_valid_json_object() {
        let engine = EngineRuntime::build();
        let json   = engine.process_stream("pandora shutdown the system").unwrap();
        let val: serde_json::Value = serde_json::from_str(&json).unwrap();
        assert!(val.is_object());
    }

    #[test]
    fn empty_input_errors() {
        let engine = EngineRuntime::build();
        assert!(engine.process("   ").is_err());
        assert!(engine.process_stream("").is_err());
    }

    #[test]
    fn pattern_registry_non_empty() {
        let engine = EngineRuntime::build();
        assert!(engine.pattern_count() > 20);
    }

    #[test]
    fn unknown_fallback_on_no_match() {
        let results = run("xyzzy frobnicator quux");
        assert_eq!(results.len(), 1);
        assert_eq!(results[0].intent, "unknown");
    }
}