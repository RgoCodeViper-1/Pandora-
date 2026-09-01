/// Engine-level errors for the Pandora core engine.
///
/// Adapted from grep-regex `error.rs`. The `EngineErrorKind` variants carry
/// over the ripgrep originals verbatim and are extended with Pandora-specific
/// kinds so that every layer (config, matcher, guard, sink, scoring) can
/// surface structured errors back to Python via PyO3.
use bstr::ByteSlice;

// ─────────────────────────────────────────────────────────────────────────────
// EngineError
// ─────────────────────────────────────────────────────────────────────────────

/// A structured error that can occur at any stage of the engine pipeline.
#[derive(Clone, Debug)]
pub struct EngineError {
    kind: EngineErrorKind,
}

impl EngineError {
    pub(crate) fn new(kind: EngineErrorKind) -> EngineError {
        EngineError { kind }
    }

    /// Construct from a `regex_automata` build error.
    /// Mirrors `grep-regex Error::regex`.
    pub(crate) fn regex(err: regex_automata::meta::BuildError) -> EngineError {
        if let Some(size_limit) = err.size_limit() {
            EngineError {
                kind: EngineErrorKind::Regex(format!(
                    "compiled regex exceeds size limit of {}",
                    size_limit
                )),
            }
        } else if let Some(ref syntax_err) = err.syntax_error() {
            EngineError::generic(syntax_err)
        } else {
            EngineError::generic(err)
        }
    }

    /// Construct from any `Display`-able error.
    /// Mirrors `grep-regex Error::generic`.
    pub(crate) fn generic<E: std::fmt::Display>(err: E) -> EngineError {
        EngineError {
            kind: EngineErrorKind::Regex(err.to_string()),
        }
    }

    pub fn kind(&self) -> &EngineErrorKind {
        &self.kind
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// EngineErrorKind
// ─────────────────────────────────────────────────────────────────────────────

/// All error variants recognised by the Pandora engine pipeline.
#[derive(Clone, Debug)]
#[non_exhaustive]
pub enum EngineErrorKind {
    // ── Ripgrep-origin variants (kept verbatim) ───────────────────────────

    /// Regex parse / compilation error.
    Regex(String),

    /// A literal that is not allowed appeared in the pattern
    /// (e.g. a raw `\n` when line-terminator stripping is active).
    NotAllowed(String),

    /// A non-ASCII byte was supplied as a line terminator.
    InvalidLineTerminator(u8),

    /// A banned byte was found in a pattern.
    Banned(u8),

    // ── Pandora-specific extensions ───────────────────────────────────────

    /// Input text was empty or contained only whitespace.
    EmptyInput,

    /// The pattern registry holds no patterns at all.
    EmptyPatternRegistry,

    /// A pattern references an unknown intent category.
    UnknownCategory(String),

    /// The sink collected zero intents even though input was non-empty.
    NoIntentsMatched,

    /// A configuration value is out of its accepted range.
    InvalidConfig(String),
}

// ─────────────────────────────────────────────────────────────────────────────
// Trait impls
// ─────────────────────────────────────────────────────────────────────────────

impl std::error::Error for EngineError {}

impl std::fmt::Display for EngineError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self.kind {
            EngineErrorKind::Regex(ref s) => {
                write!(f, "{}", s)
            }
            EngineErrorKind::NotAllowed(ref lit) => {
                write!(f, "the literal {:?} is not allowed in a pattern", lit)
            }
            EngineErrorKind::InvalidLineTerminator(byte) => {
                write!(
                    f,
                    "line terminators must be ASCII, but {:?} is not",
                    [byte].as_bstr()
                )
            }
            EngineErrorKind::Banned(byte) => {
                write!(
                    f,
                    "pattern contains {:?} but it is impossible to match",
                    [byte].as_bstr()
                )
            }
            EngineErrorKind::EmptyInput => {
                write!(f, "engine error: input text was empty")
            }
            EngineErrorKind::EmptyPatternRegistry => {
                write!(f, "engine error: pattern registry contains no patterns")
            }
            EngineErrorKind::UnknownCategory(ref cat) => {
                write!(f, "engine error: unknown intent category {:?}", cat)
            }
            EngineErrorKind::NoIntentsMatched => {
                write!(f, "engine error: no intents matched the input")
            }
            EngineErrorKind::InvalidConfig(ref msg) => {
                write!(f, "engine config error: {}", msg)
            }
        }
    }
}

/// Convert an `EngineError` into a PyO3 `PyErr` so Python callers receive a
/// clean `RuntimeError`.
///
/// pyo3 is an unconditional dependency — no feature gate needed here.
impl From<EngineError> for pyo3::PyErr {
    fn from(e: EngineError) -> pyo3::PyErr {
        pyo3::exceptions::PyRuntimeError::new_err(e.to_string())
    }
}