/// Intent sink — multi-intent collection runtime.
///
/// Adapted from `grep-searcher/src/sink.rs`.  The Sink trait contract is
/// preserved (matched / context / finish) but the payload type changes from
/// `SinkMatch` to `IntentResult` so the engine can collect multiple intents
/// from a single utterance in streaming order.
///
/// The `IntentSink` struct is the primary concrete implementation: it buffers
/// results into a `Vec<IntentResult>` that the engine drains once matching is
/// complete.
use crate::intent::{IntentCategory, IntentResult};

// ─────────────────────────────────────────────────────────────────────────────
// SinkError  (mirrors grep-searcher SinkError trait)
// ─────────────────────────────────────────────────────────────────────────────

/// Error type that can be reported by sink implementations.
pub trait SinkError: Sized {
    fn error_message<T: std::fmt::Display>(message: T) -> Self;
}

impl SinkError for std::io::Error {
    fn error_message<T: std::fmt::Display>(message: T) -> std::io::Error {
        std::io::Error::new(std::io::ErrorKind::Other, message.to_string())
    }
}

impl SinkError for String {
    fn error_message<T: std::fmt::Display>(message: T) -> String {
        message.to_string()
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Sink trait
// ─────────────────────────────────────────────────────────────────────────────

/// Push-model intent consumer.
///
/// The engine drives execution and *pushes* each matched intent into the sink.
/// Returning `Ok(false)` from `matched` stops iteration early (used to cap
/// at `max_intents`).
pub trait Sink {
    type Error: SinkError;

    /// Called for every intent match whose confidence exceeds the threshold.
    /// Return `Ok(true)` to continue or `Ok(false)` to stop early.
    fn matched(&mut self, result: IntentResult) -> Result<bool, Self::Error>;

    /// Called once at the end of a `process()` run with summary stats.
    fn finish(&mut self, summary: SinkSummary) -> Result<(), Self::Error> {
        let _ = summary;
        Ok(())
    }
}

/// Summary reported at the end of a single `process()` call.
#[derive(Clone, Debug)]
pub struct SinkSummary {
    pub total_patterns_tested: usize,
    pub total_matches:         usize,
    pub was_truncated:         bool,
}

// ─────────────────────────────────────────────────────────────────────────────
// IntentSink  (primary concrete impl)
// ─────────────────────────────────────────────────────────────────────────────

/// Collects `IntentResult` values into an internal buffer.
///
/// Respects `max_intents` — once the cap is reached, `matched` returns
/// `Ok(false)` to stop the engine's pattern loop (mirrors the `Sink` early-
/// stop mechanism used by grep-searcher).
pub struct IntentSink {
    results:     Vec<IntentResult>,
    max_intents: usize,
}

impl IntentSink {
    pub fn new(max_intents: usize) -> Self {
        IntentSink {
            results: Vec::with_capacity(max_intents),
            max_intents,
        }
    }

    /// Drain collected results.
    pub fn into_results(self) -> Vec<IntentResult> {
        self.results
    }

    /// Borrow collected results without consuming the sink.
    pub fn results(&self) -> &[IntentResult] {
        &self.results
    }
}

impl Sink for IntentSink {
    type Error = String;

    fn matched(&mut self, result: IntentResult) -> Result<bool, String> {
        self.results.push(result);
        // Signal stop when cap is reached.
        if self.results.len() >= self.max_intents {
            return Ok(false);
        }
        Ok(true)
    }

    fn finish(&mut self, summary: SinkSummary) -> Result<(), String> {
        // Sort by descending confidence so the Python bridge always sees the
        // best match first — same ordering convention as ripgrep's printer.
        self.results.sort_by(|a, b| {
            b.confidence.partial_cmp(&a.confidence).unwrap_or(std::cmp::Ordering::Equal)
        });
        log::trace!(
            "sink finish: {} matches from {} patterns (truncated={})",
            summary.total_matches,
            summary.total_patterns_tested,
            summary.was_truncated
        );
        Ok(())
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Forwarding impls (mirrors grep-searcher &mut S and Box<S> impls)
// ─────────────────────────────────────────────────────────────────────────────

impl<'a, S: Sink> Sink for &'a mut S {
    type Error = S::Error;

    fn matched(&mut self, result: IntentResult) -> Result<bool, S::Error> {
        (**self).matched(result)
    }

    fn finish(&mut self, summary: SinkSummary) -> Result<(), S::Error> {
        (**self).finish(summary)
    }
}

impl<S: Sink + ?Sized> Sink for Box<S> {
    type Error = S::Error;

    fn matched(&mut self, result: IntentResult) -> Result<bool, S::Error> {
        (**self).matched(result)
    }

    fn finish(&mut self, summary: SinkSummary) -> Result<(), S::Error> {
        (**self).finish(summary)
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// DialogueSink  — collects only Dialogue-category results for the REPL path
// ─────────────────────────────────────────────────────────────────────────────

/// Lightweight sink that discards everything except `Dialogue` category
/// intents.  Used by the streaming partial-intent path.
pub struct DialogueSink {
    results: Vec<IntentResult>,
}

impl DialogueSink {
    pub fn new() -> Self {
        DialogueSink { results: Vec::new() }
    }

    pub fn into_results(self) -> Vec<IntentResult> {
        self.results
    }
}

impl Sink for DialogueSink {
    type Error = String;

    fn matched(&mut self, result: IntentResult) -> Result<bool, String> {
        if result.category == IntentCategory::Dialogue
            || result.category == IntentCategory::Wakeword
        {
            self.results.push(result);
        }
        Ok(true) // always continue — we want all dialogue hits
    }
}
