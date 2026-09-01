//! Python boundary — PyO3 module `rust_core`.
//!
//! This is the ONLY file that touches PyO3.  All runtime logic lives in
//! `engine.rs` (orchestration), `intent.rs` (types/scoring), `sink.rs`
//! (collection), `matcher.rs` (regex), and the `patterns/` registry.
//!
//! Exported Python API
//! ───────────────────
//!   rust_core.process(text: str)        -> str   (JSON batch)
//!   rust_core.process_stream(text: str) -> str   (JSON single or batch)
//!   rust_core.pattern_count()           -> int
//!
//! JSON shapes
//! ───────────
//!   process()        → { "intents": [ IntentResult, … ] }
//!   process_stream() → IntentResult  (high confidence)
//!                      OR { "intents": [ … ] }  (fallback batch)
//!
//! IntentResult fields (from intent.rs)
//! ─────────────────────────────────────
//!   intent         : str
//!   category       : str   (snake_case enum variant)
//!   execution_type : str   (snake_case enum variant)
//!   confidence     : float
//!   entities       : dict[str, str]
//!   text           : str   (normalised, wakeword-stripped)

use pyo3::exceptions::PyRuntimeError;
use pyo3::prelude::*;
use pyo3::Bound;

mod ast;
mod ban;
mod config;
mod engine;
mod error;
mod intent;
mod lines;
mod literal;
mod macros;
mod matcher;
mod non_matching;
mod patterns;
mod sink;
mod strip;

// ─────────────────────────────────────────────────────────────────────────────
// PyO3 functions — thin wrappers over engine::RUNTIME
// ─────────────────────────────────────────────────────────────────────────────

/// Process a voice utterance and return ALL matched intents as a JSON batch.
///
/// Returns `{"intents": [...]}` sorted by confidence descending.
/// Raises `RuntimeError` only on empty / whitespace-only input.
#[pyfunction]
fn process(text: &str) -> PyResult<String> {
    engine::RUNTIME
        .process(text)
        .map_err(|e| PyRuntimeError::new_err(e.to_string()))
}

/// Process a voice utterance in streaming / low-latency mode.
///
/// Returns a single `IntentResult` JSON object immediately when one pattern
/// scores above 0.70, otherwise falls back to the full batch JSON.
/// Raises `RuntimeError` only on empty / whitespace-only input.
#[pyfunction]
fn process_stream(text: &str) -> PyResult<String> {
    engine::RUNTIME
        .process_stream(text)
        .map_err(|e| PyRuntimeError::new_err(e.to_string()))
}

/// Return the number of successfully compiled patterns in the registry.
/// Useful for health-checks and startup logging from Python.
#[pyfunction]
fn pattern_count() -> usize {
    engine::RUNTIME.pattern_count()
}

// ─────────────────────────────────────────────────────────────────────────────
// Module registration
// ─────────────────────────────────────────────────────────────────────────────

#[pymodule]
//fn pandora_core(_py: Python, m: &PyModule) -> PyResult<()> {
fn pandora_core(_py: Python, m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(process, m)?)?;
    m.add_function(wrap_pyfunction!(process_stream, m)?)?;
    m.add_function(wrap_pyfunction!(pattern_count, m)?)?;
    Ok(())
}