/// Engine configuration layer.
///
/// Adapted from two ripgrep sources:
///   - `grep-regex/src/config.rs`   → matcher-level knobs (case, multi-line, unicode…)
///   - `grep-searcher/src/mod.rs`   → searcher-level knobs (binary detection, heap limit…)
///
/// Here the two are merged into a single `EngineConfig` that drives every
/// layer of the Pandora intent pipeline.  Non-applicable searcher knobs
/// (mmap, passthru, after/before context) are dropped; intent-specific knobs
/// are added.
use grep_matcher::LineTerminator;
use regex_automata::meta::Regex;
use regex_syntax::{ast, hir::{self, Hir}};

use crate::ast::AstAnalysis;
use crate::ban;
use crate::error::EngineError;
use crate::non_matching::non_matching_bytes;
use crate::strip::strip_from_match;

// ─────────────────────────────────────────────────────────────────────────────
// EngineConfig
// ─────────────────────────────────────────────────────────────────────────────

/// All tuneable parameters for the Pandora core engine.
///
/// Defaults mirror the ripgrep defaults where applicable, with Pandora-specific
/// additions for intent scoring, multi-intent routing, and wakeword detection.
#[derive(Clone, Debug)]
pub struct EngineConfig {
    // ── Matcher knobs (from grep-regex Config) ────────────────────────────
    pub case_insensitive:        bool,
    pub case_smart:              bool,
    pub multi_line:              bool,
    pub dot_matches_new_line:    bool,
    pub swap_greed:              bool,
    pub ignore_whitespace:       bool,
    pub unicode:                 bool,
    pub octal:                   bool,
    pub size_limit:              usize,
    pub dfa_size_limit:          usize,
    pub nest_limit:              u32,
    pub line_terminator:         Option<LineTerminator>,
    pub ban_byte:                Option<u8>,
    pub crlf:                    bool,
    pub word:                    bool,
    pub fixed_strings:           bool,
    pub whole_line:              bool,

    // ── Scoring / routing knobs (Pandora additions) ───────────────────────

    /// Minimum confidence (0.0 – 1.0) required to emit an intent.
    pub min_confidence:          f32,

    /// When true, continue matching after the first hit and collect ALL
    /// intents whose confidence exceeds `min_confidence` (multi-intent mode).
    pub multi_intent:            bool,

    /// Maximum number of intents to return in a single process() call.
    pub max_intents:             usize,

    /// Wakeword that must appear (case-insensitive) before a command is
    /// considered valid.  `None` disables wakeword gating.
    pub wakeword:                Option<String>,

    /// When true the engine strips the wakeword token from the text before
    /// pattern matching so patterns do not have to account for it.
    pub strip_wakeword:          bool,

    /// Heap memory limit (bytes) for the line buffer used in streaming mode.
    /// `None` → unlimited (same semantics as grep-searcher heap_limit).
    pub heap_limit:              Option<usize>,
}

impl Default for EngineConfig {
    fn default() -> Self {
        EngineConfig {
            // matcher knobs
            case_insensitive:     false,
            case_smart:           true,   // smart-case on by default for voice
            multi_line:           false,
            dot_matches_new_line: false,
            swap_greed:           false,
            ignore_whitespace:    false,
            unicode:              true,
            octal:                false,
            size_limit:           100 * (1 << 20),
            dfa_size_limit:       1000 * (1 << 20),
            nest_limit:           250,
            line_terminator:      None,
            ban_byte:             None,
            crlf:                 false,
            word:                 false,
            fixed_strings:        false,
            whole_line:           false,
            // scoring knobs
            min_confidence:       0.6,
            multi_intent:         true,
            max_intents:          8,
            wakeword:             Some("pandora".to_string()),
            strip_wakeword:       true,
            heap_limit:           None,
        }
    }
}

impl EngineConfig {
    /// Determine whether case-insensitive matching should be used, honouring
    /// the `case_smart` flag the same way grep-regex does.
    pub(crate) fn is_case_insensitive(&self, analysis: &AstAnalysis) -> bool {
        if self.case_insensitive {
            return true;
        }
        if !self.case_smart {
            return false;
        }
        // smart-case: enable i-flag only when the pattern has literals AND
        // none of them are upper-case (mirrors AstAnalysis::from_pattern logic).
        analysis.any_literal() && !analysis.any_uppercase()
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// ConfiguredHIR  (adapted from grep-regex ConfiguredHIR)
// ─────────────────────────────────────────────────────────────────────────────

/// A compiled, config-aware HIR expression ready for matcher construction.
///
/// This is a near-verbatim port of `grep-regex/src/config.rs::ConfiguredHIR`
/// with the public API narrowed to what the Pandora engine actually needs.
#[derive(Clone, Debug)]
pub(crate) struct ConfiguredHIR {
    pub(crate) config: EngineConfig,
    pub(crate) hir:    Hir,
}

impl ConfiguredHIR {
    /// Build a `ConfiguredHIR` from a raw pattern string using the supplied
    /// `EngineConfig`.  Mirrors `Config::build_many` in grep-regex.
    pub(crate) fn new(config: EngineConfig, pattern: &str) -> Result<Self, EngineError> {
        let ast = ast::parse::ParserBuilder::new()
            .nest_limit(config.nest_limit)
            .octal(config.octal)
            .ignore_whitespace(config.ignore_whitespace)
            .build()
            .parse(&format!("(?:{})", pattern))
            .map_err(EngineError::generic)?;

        let analysis = AstAnalysis::from_ast(&ast);

        let mut hir = hir::translate::TranslatorBuilder::new()
            .utf8(false)
            .case_insensitive(config.is_case_insensitive(&analysis))
            .multi_line(config.multi_line)
            .dot_matches_new_line(config.dot_matches_new_line)
            .crlf(config.crlf)
            .swap_greed(config.swap_greed)
            .unicode(config.unicode)
            .build()
            .translate(&format!("(?:{})", pattern), &ast)
            .map_err(EngineError::generic)?;

        // Guard: banned byte check (from ban.rs)
        if let Some(byte) = config.ban_byte {
            ban::check(&hir, byte)?;
        }

        // Guard: strip line terminator from match (from strip.rs)
        if let Some(line_term) = config.line_terminator {
            hir = strip_from_match(hir, line_term)?;
        }

        Ok(ConfiguredHIR { config, hir })
    }

    pub(crate) fn config(&self) -> &EngineConfig {
        &self.config
    }

    pub(crate) fn hir(&self) -> &Hir {
        &self.hir
    }

    /// Build the `regex_automata` `Regex` that will perform actual matching.
    /// Mirrors `ConfiguredHIR::to_regex` in grep-regex.
    pub(crate) fn to_regex(&self) -> Result<Regex, EngineError> {
        let meta = Regex::config()
            .utf8_empty(false)
            .nfa_size_limit(Some(self.config.size_limit))
            .onepass_size_limit(Some(10 * (1 << 20)))
            .dfa_size_limit(Some(1 * (1 << 20)))
            .dfa_state_limit(Some(1_000))
            .hybrid_cache_capacity(self.config.dfa_size_limit);

        Regex::builder()
            .configure(meta)
            .build_from_hir(&self.hir)
            .map_err(EngineError::regex)
    }

    /// Returns the non-matching byte-set for this HIR (from non_matching.rs).
    pub(crate) fn non_matching_bytes(&self) -> grep_matcher::ByteSet {
        non_matching_bytes(&self.hir)
    }

    /// Returns the configured line terminator only when the HIR doesn't
    /// contain haystack anchors — mirrors the identical logic in grep-regex.
    pub(crate) fn line_terminator(&self) -> Option<LineTerminator> {
        if self.hir.properties().look_set().contains_anchor_haystack() {
            None
        } else {
            self.config.line_terminator
        }
    }

    /// Wraps the HIR in whole-line anchors (mirrors `into_whole_line`).
    pub(crate) fn into_whole_line(self) -> ConfiguredHIR {
        let start = Hir::look(if self.config.crlf {
            hir::Look::StartCRLF
        } else {
            hir::Look::StartLF
        });
        let end = Hir::look(if self.config.crlf {
            hir::Look::EndCRLF
        } else {
            hir::Look::EndLF
        });
        let hir = Hir::concat(vec![start, self.hir, end]);
        ConfiguredHIR { config: self.config, hir }
    }
}
