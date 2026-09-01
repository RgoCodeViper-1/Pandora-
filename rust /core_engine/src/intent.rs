/// Intent data structures and confidence scoring.
///
/// This module owns the `IntentResult`, `IntentCategory`, `ExecutionType`
/// and `IntentPattern` types that every other layer references.  Scoring
/// logic lives here so the sink and engine layers stay thin.
use serde::{Deserialize, Serialize};

// ─────────────────────────────────────────────────────────────────────────────
// Intent category taxonomy  (derived from Jarvis process_command coverage)
// ─────────────────────────────────────────────────────────────────────────────

/// High-level category grouping for routing.
#[derive(Clone, Debug, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum IntentCategory {
    /// Wakeword / activation signal.
    Wakeword,
    /// Conversational greetings, small talk, "how are you".
    Dialogue,
    /// Time, date, version queries.
    Query,
    /// Weather lookup.
    Weather,
    /// Wikipedia / Google search.
    Search,
    /// Open a website or known URL.
    Browser,
    /// YouTube / Spotify media playback.
    Media,
    /// WhatsApp / email messaging.
    Messaging,
    /// Note-taking and note recall.
    Notes,
    /// Task management (add, complete, list, delete tasks).
    Tasks,
    /// Habit tracking.
    Habits,
    /// Daily schedule management.
    Schedule,
    /// News headlines.
    News,
    /// System status, scan, DNS flush, cleanup.
    System,
    /// App launch / close / switch.
    AppControl,
    /// File operations (create, read, delete, organise).
    Files,
    /// AI model selection / web mode.
    Config,
    /// Sleep / wake / shutdown / goodbye.
    Lifecycle,
    /// Self-repair, backup, diagnostics.
    SelfRepair,
    /// Terminal operations mode.
    Terminal,
    /// Memory / context recall.
    Memory,
    /// LLM fallback — pass to the configured AI model.
    LlmFallback,
    /// Unrecognised — returned when confidence is below threshold.
    Unknown,
}

// ─────────────────────────────────────────────────────────────────────────────
// Execution type
// ─────────────────────────────────────────────────────────────────────────────

/// How the executor should dispatch this intent.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ExecutionType {
    /// Immediate in-process call (no subprocess).
    Sync,
    /// Async coroutine on the Python asyncio event loop.
    Async,
    /// Spawn a subprocess (e.g. terminal operations).
    Subprocess,
    /// Forward to the AI/LLM router.
    LlmRouter,
    /// Dialogue-only response, no side effects.
    Dialogue,
}

// ─────────────────────────────────────────────────────────────────────────────
// IntentPattern  — static registry entry
// ─────────────────────────────────────────────────────────────────────────────

/// A single entry in the static pattern registry.
///
/// Each pattern is compiled once at startup via `IntentMatcherBuilder` and
/// reused for every utterance.
#[derive(Clone, Debug)]
pub struct IntentPattern {
    /// Human-readable identifier, used as the `intent` field in JSON output.
    pub id: &'static str,

    /// Regex pattern (compiled by `IntentMatcherBuilder`).
    pub pattern: &'static str,

    /// Routing category.
    pub category: IntentCategory,

    /// How the Python executor should dispatch this intent.
    pub execution_type: ExecutionType,

    /// Base confidence weight (0.0 – 1.0).  Higher = tried first.
    pub weight: f32,

    /// Named entity slots that the Python executor can extract.
    /// Each entry is a capture-group name from the regex.
    pub entity_slots: &'static [&'static str],
}

// ─────────────────────────────────────────────────────────────────────────────
// IntentResult  — output from the engine
// ─────────────────────────────────────────────────────────────────────────────

/// A single matched intent, serialised to JSON for the Python bridge.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct IntentResult {
    /// Intent identifier (matches `IntentPattern::id`).
    pub intent: String,

    /// Routing category.
    pub category: IntentCategory,

    /// Execution type hint for the Python executor.
    pub execution_type: ExecutionType,

    /// Confidence score in [0.0, 1.0].
    pub confidence: f32,

    /// Extracted named entity values (capture group name → matched text).
    pub entities: std::collections::HashMap<String, String>,

    /// The normalised (wakeword-stripped, trimmed) input text.
    pub text: String,
}

impl IntentResult {
    /// Build an `unknown` result when nothing matched.
    pub fn unknown(text: impl Into<String>) -> Self {
        IntentResult {
            intent:         "unknown".to_string(),
            category:       IntentCategory::Unknown,
            execution_type: ExecutionType::LlmRouter,
            confidence:     0.0,
            entities:       Default::default(),
            text:           text.into(),
        }
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Scoring helpers
// ─────────────────────────────────────────────────────────────────────────────

/// Compute a confidence score for a match.
///
/// Score = pattern_weight × coverage_bonus
///
/// `coverage_bonus` rewards patterns whose match spans more of the input —
/// longer matches are more specific and therefore more trustworthy.
pub(crate) fn score_match(
    pattern: &IntentPattern,
    match_len: usize,
    input_len: usize,
) -> f32 {
    if input_len == 0 {
        return 0.0;
    }
    let coverage = match_len as f32 / input_len as f32;
    // Clamp coverage to [0, 1] and blend 70% weight + 30% coverage.
    let coverage = coverage.min(1.0);
    (pattern.weight * 0.70 + coverage * 0.30).min(1.0)
}
