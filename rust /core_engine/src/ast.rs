/// AST analysis for smart-case and literal-awareness.
///
/// Verbatim port of `grep-regex/src/ast.rs`.  No changes needed — the
/// AstAnalysis contract is identical in Pandora: given a pattern, decide
/// whether any uppercase literals are present so the engine can auto-enable
/// case-insensitive matching when the user hasn't capitalised anything.
use regex_syntax::ast::{self, Ast};

/// The results of analysing the AST of a regular expression.
#[derive(Clone, Debug)]
pub(crate) struct AstAnalysis {
    /// True if and only if a literal upper-case character occurs in the regex.
    any_uppercase: bool,
    /// True if and only if the regex contains any literal at all.
    any_literal: bool,
}

impl AstAnalysis {
    /// Parse `pattern` and return an analysis, or `None` if it is not valid.
    #[cfg(test)]
    pub(crate) fn from_pattern(pattern: &str) -> Option<AstAnalysis> {
        regex_syntax::ast::parse::Parser::new()
            .parse(pattern)
            .map(|ast| AstAnalysis::from_ast(&ast))
            .ok()
    }

    /// Perform an analysis given an already-parsed AST.
    pub(crate) fn from_ast(ast: &Ast) -> AstAnalysis {
        let mut analysis = AstAnalysis::new();
        analysis.from_ast_impl(ast);
        analysis
    }

    /// True if at least one literal upper-case character occurs in the pattern.
    pub(crate) fn any_uppercase(&self) -> bool {
        self.any_uppercase
    }

    /// True if the pattern contains any literal at all.
    pub(crate) fn any_literal(&self) -> bool {
        self.any_literal
    }

    fn new() -> AstAnalysis {
        AstAnalysis { any_uppercase: false, any_literal: false }
    }

    fn from_ast_impl(&mut self, ast: &Ast) {
        if self.done() {
            return;
        }
        match *ast {
            Ast::Empty(_) => {}
            Ast::Flags(_)
            | Ast::Dot(_)
            | Ast::Assertion(_)
            | Ast::ClassUnicode(_)
            | Ast::ClassPerl(_) => {}
            Ast::Literal(ref x) => {
                self.from_ast_literal(x);
            }
            Ast::ClassBracketed(ref x) => {
                self.from_ast_class_set(&x.kind);
            }
            Ast::Repetition(ref x) => {
                self.from_ast_impl(&x.ast);
            }
            Ast::Group(ref x) => {
                self.from_ast_impl(&x.ast);
            }
            Ast::Alternation(ref alt) => {
                for x in &alt.asts {
                    self.from_ast_impl(x);
                }
            }
            Ast::Concat(ref alt) => {
                for x in &alt.asts {
                    self.from_ast_impl(x);
                }
            }
        }
    }

    fn from_ast_class_set(&mut self, ast: &ast::ClassSet) {
        if self.done() {
            return;
        }
        match *ast {
            ast::ClassSet::Item(ref item) => {
                self.from_ast_class_set_item(item);
            }
            ast::ClassSet::BinaryOp(ref x) => {
                self.from_ast_class_set(&x.lhs);
                self.from_ast_class_set(&x.rhs);
            }
        }
    }

    fn from_ast_class_set_item(&mut self, ast: &ast::ClassSetItem) {
        if self.done() {
            return;
        }
        match *ast {
            ast::ClassSetItem::Empty(_)
            | ast::ClassSetItem::Ascii(_)
            | ast::ClassSetItem::Unicode(_)
            | ast::ClassSetItem::Perl(_) => {}
            ast::ClassSetItem::Literal(ref x) => {
                self.from_ast_literal(x);
            }
            ast::ClassSetItem::Range(ref x) => {
                self.from_ast_literal(&x.start);
                self.from_ast_literal(&x.end);
            }
            ast::ClassSetItem::Bracketed(ref x) => {
                self.from_ast_class_set(&x.kind);
            }
            ast::ClassSetItem::Union(ref union) => {
                for x in &union.items {
                    self.from_ast_class_set_item(x);
                }
            }
        }
    }

    fn from_ast_literal(&mut self, ast: &ast::Literal) {
        self.any_literal   = true;
        self.any_uppercase = self.any_uppercase || ast.c.is_uppercase();
    }

    /// Returns true when both attributes are saturated — no further AST walk
    /// can change the result.
    fn done(&self) -> bool {
        self.any_uppercase && self.any_literal
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn analysis(pattern: &str) -> AstAnalysis {
        AstAnalysis::from_pattern(pattern).unwrap()
    }

    #[test]
    fn various() {
        assert!(!analysis("").any_uppercase());
        assert!(!analysis("").any_literal());

        assert!(!analysis("foo").any_uppercase());
        assert!( analysis("foo").any_literal());

        assert!( analysis("Foo").any_uppercase());
        assert!( analysis("Foo").any_literal());

        assert!(!analysis(r"foo\w").any_uppercase());
        assert!( analysis(r"foo\w").any_literal());

        assert!(!analysis(r"foo[a-z]").any_uppercase());
        assert!( analysis(r"foo[a-z]").any_literal());

        assert!( analysis(r"foo[A-Z]").any_uppercase());
        assert!( analysis(r"foo[A-Z]").any_literal());

        assert!(!analysis(r"\p{Ll}").any_uppercase());
        assert!(!analysis(r"\p{Ll}").any_literal());
    }
}
