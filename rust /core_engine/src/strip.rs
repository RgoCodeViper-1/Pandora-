/// Strip forbidden line-terminator literals from an HIR.
///
/// Verbatim port of `grep-regex/src/strip.rs`.  Used by `ConfiguredHIR::new`
/// to guarantee that compiled patterns can never produce a match containing
/// the configured line terminator (required for streaming line-by-line mode).
use grep_matcher::LineTerminator;
use regex_syntax::hir::{self, Hir, HirKind};

use crate::error::{EngineError, EngineErrorKind};

/// Return an HIR guaranteed never to match the given line terminator, or an
/// error if that is not structurally possible.
pub(crate) fn strip_from_match(
    expr: Hir,
    line_term: LineTerminator,
) -> Result<Hir, EngineError> {
    if line_term.is_crlf() {
        let expr1 = strip_from_match_ascii(expr, b'\r')?;
        strip_from_match_ascii(expr1, b'\n')
    } else {
        strip_from_match_ascii(expr, line_term.as_byte())
    }
}

fn strip_from_match_ascii(expr: Hir, byte: u8) -> Result<Hir, EngineError> {
    if !byte.is_ascii() {
        return Err(EngineError::new(EngineErrorKind::InvalidLineTerminator(byte)));
    }
    let ch      = char::from(byte);
    let invalid = || {
        Err(EngineError::new(EngineErrorKind::NotAllowed(ch.to_string())))
    };

    Ok(match expr.into_kind() {
        HirKind::Empty => Hir::empty(),
        HirKind::Literal(hir::Literal(lit)) => {
            if lit.iter().any(|&b| b == byte) {
                return invalid();
            }
            Hir::literal(lit)
        }
        HirKind::Class(hir::Class::Unicode(mut cls)) => {
            if cls.ranges().is_empty() {
                return Ok(Hir::class(hir::Class::Unicode(cls)));
            }
            let remove = hir::ClassUnicode::new(Some(
                hir::ClassUnicodeRange::new(ch, ch),
            ));
            cls.difference(&remove);
            if cls.ranges().is_empty() {
                return invalid();
            }
            Hir::class(hir::Class::Unicode(cls))
        }
        HirKind::Class(hir::Class::Bytes(mut cls)) => {
            if cls.ranges().is_empty() {
                return Ok(Hir::class(hir::Class::Bytes(cls)));
            }
            let remove = hir::ClassBytes::new(Some(
                hir::ClassBytesRange::new(byte, byte),
            ));
            cls.difference(&remove);
            if cls.ranges().is_empty() {
                return invalid();
            }
            Hir::class(hir::Class::Bytes(cls))
        }
        HirKind::Look(x) => Hir::look(x),
        HirKind::Repetition(mut x) => {
            x.sub = Box::new(strip_from_match_ascii(*x.sub, byte)?);
            Hir::repetition(x)
        }
        HirKind::Capture(mut x) => {
            x.sub = Box::new(strip_from_match_ascii(*x.sub, byte)?);
            Hir::capture(x)
        }
        HirKind::Concat(xs) => {
            let xs = xs
                .into_iter()
                .map(|e| strip_from_match_ascii(e, byte))
                .collect::<Result<Vec<Hir>, EngineError>>()?;
            Hir::concat(xs)
        }
        HirKind::Alternation(xs) => {
            let xs = xs
                .into_iter()
                .map(|e| strip_from_match_ascii(e, byte))
                .collect::<Result<Vec<Hir>, EngineError>>()?;
            Hir::alternation(xs)
        }
    })
}

#[cfg(test)]
mod tests {
    use regex_syntax::Parser;
    use super::{LineTerminator, strip_from_match};
    use crate::error::EngineError;

    fn roundtrip(pattern: &str, byte: u8) -> String {
        let expr1 = Parser::new().parse(pattern).unwrap();
        let expr2 = strip_from_match(expr1, LineTerminator::byte(byte)).unwrap();
        expr2.to_string()
    }

    fn roundtrip_err(pattern: &str, byte: u8) -> Result<String, EngineError> {
        let expr1 = Parser::new().parse(pattern).unwrap();
        let expr2 = strip_from_match(expr1, LineTerminator::byte(byte))?;
        Ok(expr2.to_string())
    }

    #[test]
    fn various() {
        assert_eq!(roundtrip(r"[a\n]", b'\n'), "a");
        assert_eq!(roundtrip(r"[a\n]", b'a'),  "\n");
        assert!(roundtrip_err(r"\n",     b'\n').is_err());
        assert!(roundtrip_err(r"abc\n",  b'\n').is_err());
        assert!(roundtrip_err(r"\nabc",  b'\n').is_err());
    }
}
