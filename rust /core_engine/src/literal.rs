/// Literal prefilter extraction layer.
///
/// Adapted from `grep-regex/src/literal.rs`.  The `InnerLiterals` type and
/// `Extractor` are kept structurally identical to the ripgrep originals;
/// only the import path changes (`crate::config::ConfiguredHIR` instead of
/// the grep-regex crate-local path).
///
/// Purpose in the Pandora engine: before running the full regex on a voice
/// utterance, extract concrete literals (e.g. "shutdown") so the engine can
/// pre-screen with a fast `memmem`-style scan.  This skips the expensive DFA
/// on utterances that clearly don't contain any keyword.
use regex_automata::meta::Regex;
use regex_syntax::hir::{
    self, Hir,
    literal::{Literal as HLiteral, Seq},
};

use crate::config::ConfiguredHIR;
use crate::error::EngineError;

// ─────────────────────────────────────────────────────────────────────────────
// InnerLiterals
// ─────────────────────────────────────────────────────────────────────────────

/// Encapsulates inner literal extraction from a compiled HIR.
#[derive(Clone, Debug)]
pub(crate) struct InnerLiterals {
    seq: Seq,
}

impl InnerLiterals {
    /// Create from a `ConfiguredHIR` and its compiled `Regex`.
    /// Returns a no-op (infinite) literal set when extraction is not valid.
    pub(crate) fn new(chir: &ConfiguredHIR, re: &Regex) -> InnerLiterals {
        // Without a line terminator the prefilter optimisation is unsafe.
        if chir.config().line_terminator.is_none() {
            return InnerLiterals::none();
        }
        if re.is_accelerated() {
            if !chir.hir().properties().look_set().contains_word_unicode() {
                return InnerLiterals::none();
            }
        }
        if chir.hir().properties().is_alternation_literal() {
            return InnerLiterals::none();
        }
        let seq = Extractor::new().extract_untagged(chir.hir());
        InnerLiterals { seq }
    }

    /// Returns an infinite (no-op) literal set.
    pub(crate) fn none() -> InnerLiterals {
        InnerLiterals { seq: Seq::infinite() }
    }

    /// Returns a fast `Regex` over the extracted literals, if the set is
    /// considered worth using.
    pub(crate) fn one_regex(&self) -> Result<Option<Regex>, EngineError> {
        let Some(lits) = self.seq.literals() else { return Ok(None) };
        if lits.is_empty() {
            return Ok(None);
        }
        let alts: Vec<Hir> = lits.iter()
            .map(|l| Hir::literal(l.as_bytes()))
            .collect();
        let hir = Hir::alternation(alts);
        let re  = Regex::builder()
            .configure(Regex::config().utf8_empty(false))
            .build_from_hir(&hir)
            .map_err(EngineError::regex)?;
        Ok(Some(re))
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Extractor  (verbatim from grep-regex literal.rs)
// ─────────────────────────────────────────────────────────────────────────────

#[derive(Debug)]
struct Extractor {
    limit_class:       usize,
    limit_repeat:      usize,
    limit_literal_len: usize,
    limit_total:       usize,
}

impl Extractor {
    fn new() -> Extractor {
        Extractor {
            limit_class:       10,
            limit_repeat:      10,
            limit_literal_len: 100,
            limit_total:       64,
        }
    }

    fn extract_untagged(&self, hir: &Hir) -> Seq {
        let mut seq = self.extract(hir);
        seq.seq.optimize_for_prefix_by_preference();
        if !seq.is_good() {
            seq.make_infinite();
        }
        seq.seq
    }

    fn extract(&self, hir: &Hir) -> TSeq {
        use regex_syntax::hir::HirKind::*;
        match *hir.kind() {
            Empty | Look(_) => TSeq::singleton(HLiteral::exact(vec![])),
            Literal(hir::Literal(ref bytes)) => {
                let mut seq = TSeq::singleton(HLiteral::exact(bytes.to_vec()));
                self.enforce_literal_len(&mut seq);
                seq
            }
            Class(hir::Class::Unicode(ref cls)) => self.extract_class_unicode(cls),
            Class(hir::Class::Bytes(ref cls))   => self.extract_class_bytes(cls),
            Repetition(ref rep)                 => self.extract_repetition(rep),
            Capture(hir::Capture { ref sub, .. }) => self.extract(sub),
            Concat(ref hirs)                    => self.extract_concat(hirs.iter()),
            Alternation(ref hirs)               => self.extract_alternation(hirs.iter()),
        }
    }

    fn extract_concat<'a, I: Iterator<Item = &'a Hir>>(&self, it: I) -> TSeq {
        let mut seq  = TSeq::singleton(HLiteral::exact(vec![]));
        let mut prev: Option<TSeq> = None;
        for hir in it {
            if seq.is_inexact() {
                if seq.is_empty() { return seq; }
                if seq.is_really_good() { return seq; }
                prev = Some(match prev {
                    None    => seq,
                    Some(p) => p.choose(seq),
                });
                seq = TSeq::singleton(HLiteral::exact(vec![]));
                seq.make_not_prefix();
            }
            seq = self.cross(seq, self.extract(hir));
        }
        if let Some(prev) = prev { prev.choose(seq) } else { seq }
    }

    fn extract_alternation<'a, I: Iterator<Item = &'a Hir>>(&self, it: I) -> TSeq {
        let mut seq = TSeq::empty();
        for hir in it {
            if !seq.is_finite() { break; }
            seq = self.union(seq, &mut self.extract(hir));
        }
        seq
    }

    fn extract_repetition(&self, rep: &hir::Repetition) -> TSeq {
        let mut subseq = self.extract(&rep.sub);
        match *rep {
            hir::Repetition { min: 0, max, greedy, .. } => {
                if max != Some(1) { subseq.make_inexact(); }
                let mut empty = TSeq::singleton(HLiteral::exact(vec![]));
                if !greedy { std::mem::swap(&mut subseq, &mut empty); }
                self.union(subseq, &mut empty)
            }
            hir::Repetition { min, max: Some(max), .. } if min == max => {
                assert!(min > 0);
                let limit = u32::try_from(self.limit_repeat).unwrap_or(u32::MAX);
                let mut seq = TSeq::singleton(HLiteral::exact(vec![]));
                for _ in 0..std::cmp::min(min, limit) {
                    if seq.is_inexact() { break; }
                    seq = self.cross(seq, subseq.clone());
                }
                if usize::try_from(min).is_err() || min > limit {
                    seq.make_inexact();
                }
                seq
            }
            hir::Repetition { min, max: Some(max), .. } if min < max => {
                assert!(min > 0);
                let limit = u32::try_from(self.limit_repeat).unwrap_or(u32::MAX);
                let mut seq = TSeq::singleton(HLiteral::exact(vec![]));
                for _ in 0..std::cmp::min(min, limit) {
                    if seq.is_inexact() { break; }
                    seq = self.cross(seq, subseq.clone());
                }
                seq.make_inexact();
                seq
            }
            _ => { subseq.make_inexact(); subseq }
        }
    }

    fn extract_class_unicode(&self, cls: &hir::ClassUnicode) -> TSeq {
        if self.class_over_limit_unicode(cls) { return TSeq::infinite(); }
        let mut seq = TSeq::empty();
        for r in cls.iter() {
            for ch in r.start()..=r.end() {
                seq.push(HLiteral::from(ch));
            }
        }
        self.enforce_literal_len(&mut seq);
        seq
    }

    fn extract_class_bytes(&self, cls: &hir::ClassBytes) -> TSeq {
        if self.class_over_limit_bytes(cls) { return TSeq::infinite(); }
        let mut seq = TSeq::empty();
        for r in cls.iter() {
            for b in r.start()..=r.end() {
                seq.push(HLiteral::from(b));
            }
        }
        self.enforce_literal_len(&mut seq);
        seq
    }

    fn class_over_limit_unicode(&self, cls: &hir::ClassUnicode) -> bool {
        let mut count = 0usize;
        for r in cls.iter() {
            if count > self.limit_class { return true; }
            count += r.len();
        }
        count > self.limit_class
    }

    fn class_over_limit_bytes(&self, cls: &hir::ClassBytes) -> bool {
        let mut count = 0usize;
        for r in cls.iter() {
            if count > self.limit_class { return true; }
            count += r.len();
        }
        count > self.limit_class
    }

    fn cross(&self, mut seq1: TSeq, mut seq2: TSeq) -> TSeq {
        if !seq2.prefix { return seq1.choose(seq2); }
        if seq1.max_cross_len(&seq2).map_or(false, |l| l > self.limit_total) {
            seq2.make_infinite();
        }
        seq1.cross_forward(&mut seq2);
        self.enforce_literal_len(&mut seq1);
        seq1
    }

    fn union(&self, mut seq1: TSeq, seq2: &mut TSeq) -> TSeq {
        if seq1.max_union_len(seq2).map_or(false, |l| l > self.limit_total) {
            seq1.keep_first_bytes(4);
            seq2.keep_first_bytes(4);
            seq1.dedup();
            seq2.dedup();
            if seq1.max_union_len(seq2).map_or(false, |l| l > self.limit_total) {
                seq2.make_infinite();
            }
        }
        seq1.union(seq2);
        seq1.prefix = seq1.prefix && seq2.prefix;
        seq1
    }

    fn enforce_literal_len(&self, seq: &mut TSeq) {
        seq.keep_first_bytes(self.limit_literal_len);
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// TSeq helper (verbatim from grep-regex literal.rs)
// ─────────────────────────────────────────────────────────────────────────────

#[derive(Clone, Debug)]
struct TSeq {
    seq:    Seq,
    prefix: bool,
}

impl TSeq {
    fn empty()              -> TSeq { TSeq { seq: Seq::empty(),    prefix: true } }
    fn infinite()           -> TSeq { TSeq { seq: Seq::infinite(), prefix: true } }
    fn singleton(l: HLiteral)-> TSeq { TSeq { seq: Seq::singleton(l), prefix: true } }

    fn push(&mut self, l: HLiteral) { self.seq.push(l); }
    fn make_inexact(&mut self)     { self.seq.make_inexact(); }
    fn make_infinite(&mut self)    { self.seq.make_infinite(); }
    fn cross_forward(&mut self, other: &mut TSeq) {
        assert!(other.prefix);
        self.seq.cross_forward(&mut other.seq);
    }
    fn union(&mut self, other: &mut TSeq)     { self.seq.union(&mut other.seq); }
    fn dedup(&mut self)                        { self.seq.dedup(); }
    fn keep_first_bytes(&mut self, n: usize)   { self.seq.keep_first_bytes(n); }
    fn is_finite(&self)      -> bool { self.seq.is_finite() }
    fn is_empty(&self)       -> bool { self.seq.is_empty() }
    fn len(&self)            -> Option<usize> { self.seq.len() }
    fn is_exact(&self)       -> bool { self.seq.is_exact() }
    fn is_inexact(&self)     -> bool { self.seq.is_inexact() }
    fn max_union_len(&self, other: &TSeq) -> Option<usize> {
        self.seq.max_union_len(&other.seq)
    }
    fn max_cross_len(&self, other: &TSeq) -> Option<usize> {
        assert!(other.prefix);
        self.seq.max_cross_len(&other.seq)
    }
    fn min_literal_len(&self) -> Option<usize> { self.seq.min_literal_len() }
    fn literals(&self) -> Option<&[HLiteral]>   { self.seq.literals() }
    fn make_not_prefix(&mut self)              { self.prefix = false; }

    fn is_good(&self) -> bool {
        if self.has_poisonous_literal() { return false; }
        let Some(min) = self.min_literal_len() else { return false };
        let Some(len) = self.len()             else { return false };
        if min <= 1 { return len <= 3; }
        min >= 2 && len <= 64
    }

    fn is_really_good(&self) -> bool {
        if self.has_poisonous_literal() { return false; }
        let Some(min) = self.min_literal_len() else { return false };
        let Some(len) = self.len()             else { return false };
        min >= 3 && len <= 8
    }

    fn has_poisonous_literal(&self) -> bool {
        let Some(lits) = self.literals() else { return false };
        lits.iter().any(is_poisonous)
    }

    fn choose(self, other: TSeq) -> TSeq {
        let (mut s1, mut s2) = (self, other);
        s1.make_inexact();
        s2.make_inexact();
        if !s1.is_finite()            { return s2; }
        if !s2.is_finite()            { return s1; }
        if s1.has_poisonous_literal() { return s2; }
        if s2.has_poisonous_literal() { return s1; }
        let Some(min1) = s1.min_literal_len() else { return s2 };
        let Some(min2) = s2.min_literal_len() else { return s1 };
        if min1 < min2 { return s2; }
        if min2 < min1 { return s1; }
        let l1 = s1.len().unwrap();
        let l2 = s2.len().unwrap();
        if l1 < l2 { return s2; }
        if l2 < l1 { return s1; }
        s1
    }
}

impl FromIterator<HLiteral> for TSeq {
    fn from_iter<T: IntoIterator<Item = HLiteral>>(it: T) -> TSeq {
        TSeq { seq: Seq::from_iter(it), prefix: true }
    }
}

fn is_poisonous(lit: &HLiteral) -> bool {
    use regex_syntax::hir::literal::rank;
    lit.is_empty() || (lit.len() == 1 && rank(lit.as_bytes()[0]) >= 250)
}