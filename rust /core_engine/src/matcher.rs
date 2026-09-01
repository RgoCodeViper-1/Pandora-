/// Matcher builder and runtime — adapted from `grep-regex/src/matcher.rs`.
///
/// The `RegexMatcherBuilder` / `RegexMatcher` pair from grep-regex is
/// preserved here as the low-level building block.  A thin `IntentMatcher`
/// wrapper is added on top so that each `IntentPattern` in the registry owns
/// a compiled matcher that the engine can call through the grep-matcher
/// `Matcher` trait.
use grep_matcher::{
    ByteSet, Captures, LineMatchKind, LineTerminator, Match, Matcher, NoError,
};
use regex_automata::{Input, PatternID, meta::Regex,
                     util::captures::Captures as AutomataCaptures};

use crate::config::{ConfiguredHIR, EngineConfig};
use crate::error::EngineError;
use crate::literal::InnerLiterals;

// ─────────────────────────────────────────────────────────────────────────────
// IntentMatcherBuilder
// ─────────────────────────────────────────────────────────────────────────────

/// Fluent builder for `IntentMatcher`.  Mirrors `RegexMatcherBuilder` from
/// grep-regex — exposes the same knobs so existing pattern authors can reuse
/// familiar APIs.
#[derive(Clone, Debug)]
pub struct IntentMatcherBuilder {
    config: EngineConfig,
}

impl Default for IntentMatcherBuilder {
    fn default() -> Self {
        IntentMatcherBuilder::new()
    }
}

impl IntentMatcherBuilder {
    pub fn new() -> Self {
        IntentMatcherBuilder { config: EngineConfig::default() }
    }

    /// Build a matcher for a single pattern string.
    pub fn build(&self, pattern: &str) -> Result<IntentMatcher, EngineError> {
        let mut chir = ConfiguredHIR::new(self.config.clone(), pattern)?;

        if chir.config().whole_line {
            chir = chir.into_whole_line();
        }

        let regex              = chir.to_regex()?;
        let non_matching_bytes = chir.non_matching_bytes();
        let fast_line_regex    = InnerLiterals::new(&chir, &regex).one_regex()?;

        let mut config           = self.config.clone();
        config.line_terminator   = chir.line_terminator();

        Ok(IntentMatcher {
            config,
            regex,
            fast_line_regex,
            non_matching_bytes,
        })
    }

    // ── Builder knobs (mirrors grep-regex RegexMatcherBuilder) ────────────

    pub fn case_insensitive(&mut self, yes: bool) -> &mut Self {
        self.config.case_insensitive = yes; self
    }
    pub fn case_smart(&mut self, yes: bool) -> &mut Self {
        self.config.case_smart = yes; self
    }
    pub fn multi_line(&mut self, yes: bool) -> &mut Self {
        self.config.multi_line = yes; self
    }
    pub fn dot_matches_new_line(&mut self, yes: bool) -> &mut Self {
        self.config.dot_matches_new_line = yes; self
    }
    pub fn unicode(&mut self, yes: bool) -> &mut Self {
        self.config.unicode = yes; self
    }
    pub fn line_terminator(&mut self, lt: Option<u8>) -> &mut Self {
        self.config.line_terminator = lt.map(LineTerminator::byte); self
    }
    pub fn ban_byte(&mut self, byte: Option<u8>) -> &mut Self {
        self.config.ban_byte = byte; self
    }
    pub fn whole_line(&mut self, yes: bool) -> &mut Self {
        self.config.whole_line = yes; self
    }
    pub fn size_limit(&mut self, bytes: usize) -> &mut Self {
        self.config.size_limit = bytes; self
    }
    pub fn dfa_size_limit(&mut self, bytes: usize) -> &mut Self {
        self.config.dfa_size_limit = bytes; self
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// IntentMatcher
// ─────────────────────────────────────────────────────────────────────────────

/// A compiled, config-aware regex matcher for a single intent pattern.
/// Implements the grep-matcher `Matcher` trait so it plugs straight into
/// the ripgrep-derived searcher infrastructure used by the engine.
#[derive(Clone, Debug)]
pub struct IntentMatcher {
    config:            EngineConfig,
    regex:             Regex,
    /// Optional fast prefilter regex extracted from literals (from literal.rs).
    fast_line_regex:   Option<Regex>,
    non_matching_bytes: ByteSet,
}

impl IntentMatcher {
    /// Convenience constructor with default config.
    pub fn new(pattern: &str) -> Result<Self, EngineError> {
        IntentMatcherBuilder::new().build(pattern)
    }

    /// Returns true if the text contains at least one match.
    pub fn is_match_text(&self, text: &str) -> bool {
        self.is_match(text.as_bytes()).unwrap_or(false)
    }

    /// Returns the byte offset of the first match end, if any.
    pub fn find_text(&self, text: &str) -> Option<Match> {
        self.find(text.as_bytes()).unwrap_or(None)
    }
}

// ── grep-matcher Matcher impl ─────────────────────────────────────────────────

impl Matcher for IntentMatcher {
    type Captures = IntentCaptures;
    type Error    = NoError;

    #[inline]
    fn find_at(&self, haystack: &[u8], at: usize)
        -> Result<Option<Match>, NoError>
    {
        let input = Input::new(haystack).span(at..haystack.len());
        Ok(self.regex.find(input).map(|m| Match::new(m.start(), m.end())))
    }

    #[inline]
    fn new_captures(&self) -> Result<IntentCaptures, NoError> {
        Ok(IntentCaptures::new(self.regex.create_captures()))
    }

    #[inline]
    fn capture_count(&self) -> usize {
        self.regex.captures_len()
    }

    #[inline]
    fn capture_index(&self, name: &str) -> Option<usize> {
        self.regex.group_info().to_index(PatternID::ZERO, name)
    }

    #[inline]
    fn captures_at(
        &self,
        haystack: &[u8],
        at: usize,
        caps: &mut IntentCaptures,
    ) -> Result<bool, NoError> {
        let input = Input::new(haystack).span(at..haystack.len());
        let caps  = caps.captures_mut();
        self.regex.search_captures(&input, caps);
        Ok(caps.is_match())
    }

    #[inline]
    fn shortest_match_at(&self, haystack: &[u8], at: usize)
        -> Result<Option<usize>, NoError>
    {
        let input = Input::new(haystack).span(at..haystack.len());
        Ok(self.regex.search_half(&input).map(|hm| hm.offset()))
    }

    #[inline]
    fn non_matching_bytes(&self) -> Option<&ByteSet> {
        Some(&self.non_matching_bytes)
    }

    #[inline]
    fn line_terminator(&self) -> Option<LineTerminator> {
        self.config.line_terminator
    }

    /// Returns a `Candidate` match via the fast literal prefilter when one is
    /// available — mirrors the identical optimisation in grep-regex.
    #[inline]
    fn find_candidate_line(&self, haystack: &[u8])
        -> Result<Option<LineMatchKind>, NoError>
    {
        Ok(match self.fast_line_regex {
            Some(ref re) => {
                let input = Input::new(haystack);
                re.search_half(&input)
                  .map(|hm| LineMatchKind::Candidate(hm.offset()))
            }
            None => {
                self.shortest_match(haystack)?.map(LineMatchKind::Confirmed)
            }
        })
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// IntentCaptures
// ─────────────────────────────────────────────────────────────────────────────

/// Capturing-group storage for `IntentMatcher`.
/// Mirrors `RegexCaptures` in grep-regex.
#[derive(Clone, Debug)]
pub struct IntentCaptures {
    caps: AutomataCaptures,
}

impl Captures for IntentCaptures {
    #[inline]
    fn len(&self) -> usize {
        self.caps.group_info().all_group_len()
    }

    #[inline]
    fn get(&self, i: usize) -> Option<Match> {
        self.caps.get_group(i).map(|sp| Match::new(sp.start, sp.end))
    }
}

impl IntentCaptures {
    pub(crate) fn new(caps: AutomataCaptures) -> Self {
        IntentCaptures { caps }
    }

    pub(crate) fn captures_mut(&mut self) -> &mut AutomataCaptures {
        &mut self.caps
    }
}
