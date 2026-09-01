/// Banned-byte guard layer.
///
/// Verbatim port of `grep-regex/src/ban.rs`.  The guard is invoked by
/// `ConfiguredHIR::new` and also directly by the engine guard stage so that
/// patterns loaded at runtime can be validated before being compiled.
use regex_syntax::hir::{
    self, ClassBytesRange, ClassUnicodeRange, Hir, HirKind,
};

use crate::error::{EngineError, EngineErrorKind};

/// Returns an error when a sub-expression in `expr` must match `byte`.
///
/// This is used to prevent patterns that would match the NUL byte (or any
/// other operator-defined banned byte) from entering the engine.
pub(crate) fn check(expr: &Hir, byte: u8) -> Result<(), EngineError> {
    assert!(byte.is_ascii(), "ban byte must be ASCII");
    let ch      = char::from(byte);
    let invalid = || Err(EngineError::new(EngineErrorKind::Banned(byte)));

    match *expr.kind() {
        HirKind::Empty => {}
        HirKind::Literal(hir::Literal(ref lit)) => {
            if lit.iter().any(|&b| b == byte) {
                return invalid();
            }
        }
        HirKind::Class(hir::Class::Unicode(ref cls)) => {
            // Only flag when the class collapses to a single codepoint.
            if cls.ranges().iter().map(|r| r.len()).sum::<usize>() == 1 {
                let contains =
                    |r: &ClassUnicodeRange| r.start() <= ch && ch <= r.end();
                if cls.ranges().iter().any(contains) {
                    return invalid();
                }
            }
        }
        HirKind::Class(hir::Class::Bytes(ref cls)) => {
            if cls.ranges().iter().map(|r| r.len()).sum::<usize>() == 1 {
                let contains = |r: &ClassBytesRange| {
                    r.start() <= byte && byte <= r.end()
                };
                if cls.ranges().iter().any(contains) {
                    return invalid();
                }
            }
        }
        HirKind::Look(_) => {}
        HirKind::Repetition(ref x)  => check(&x.sub, byte)?,
        HirKind::Capture(ref x)     => check(&x.sub, byte)?,
        HirKind::Concat(ref xs) => {
            for x in xs {
                check(x, byte)?;
            }
        }
        HirKind::Alternation(ref xs) => {
            for x in xs {
                check(x, byte)?;
            }
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use regex_syntax::Parser;

    fn check(pattern: &str, byte: u8) -> bool {
        let hir = Parser::new().parse(pattern).unwrap();
        super::check(&hir, byte).is_err()
    }

    #[test]
    fn various() {
        assert!( check(r"\x00",   0));
        assert!( check(r"a\x00",  0));
        assert!( check(r"\x00b",  0));
        assert!( check(r"a\x00b", 0));
        assert!( check(r"\x00|ab",0));
        assert!( check(r"ab|\x00",0));
        assert!( check(r"\x00?",  0));
        assert!( check(r"(\x00)", 0));
        assert!( check(r"[\x00]", 0));

        assert!(!check(r"[^\x00]", 0));
        assert!(!check(r"[\x00a]", 0));
    }
}
