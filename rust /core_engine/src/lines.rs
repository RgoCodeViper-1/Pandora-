/// Streaming line stepping utilities.
///
/// Adapted from `grep-searcher/src/lines.rs`.  In the Pandora engine these
/// are used by the streaming `process_stream` path to walk voice transcription
/// text token-by-token (treating whitespace as the "line terminator") rather
/// than the on-disk line-by-line use case of ripgrep.
use bstr::ByteSlice;
use grep_matcher::{LineTerminator, Match};

// ─────────────────────────────────────────────────────────────────────────────
// LineIter
// ─────────────────────────────────────────────────────────────────────────────

/// An iterator over lines in a byte slice.  Line terminators are considered
/// part of the line they terminate.  All yielded slices are non-empty.
#[derive(Debug)]
pub struct LineIter<'b> {
    bytes:   &'b [u8],
    stepper: LineStep,
}

impl<'b> LineIter<'b> {
    pub fn new(line_term: u8, bytes: &'b [u8]) -> LineIter<'b> {
        let stepper = LineStep::new(line_term, 0, bytes.len());
        LineIter { bytes, stepper }
    }
}

impl<'b> Iterator for LineIter<'b> {
    type Item = &'b [u8];

    fn next(&mut self) -> Option<&'b [u8]> {
        self.stepper.next_match(self.bytes).map(|m| &self.bytes[m])
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// LineStep
// ─────────────────────────────────────────────────────────────────────────────

/// An explicit (borrow-free) line stepper.  Callers supply the same byte slice
/// on each call to `next` / `next_match`.
#[derive(Debug)]
pub struct LineStep {
    line_term: u8,
    pos:       usize,
    end:       usize,
}

impl LineStep {
    pub fn new(line_term: u8, start: usize, end: usize) -> LineStep {
        LineStep { line_term, pos: start, end }
    }

    pub fn next(&mut self, bytes: &[u8]) -> Option<(usize, usize)> {
        self.next_impl(bytes)
    }

    #[inline(always)]
    pub(crate) fn next_match(&mut self, bytes: &[u8]) -> Option<Match> {
        self.next_impl(bytes).map(|(s, e)| Match::new(s, e))
    }

    #[inline(always)]
    fn next_impl(&mut self, mut bytes: &[u8]) -> Option<(usize, usize)> {
        bytes = &bytes[..self.end];
        match bytes[self.pos..].find_byte(self.line_term) {
            None => {
                if self.pos < bytes.len() {
                    let m = (self.pos, bytes.len());
                    self.pos = m.1;
                    Some(m)
                } else {
                    None
                }
            }
            Some(line_end) => {
                let m = (self.pos, self.pos + line_end + 1);
                self.pos = m.1;
                Some(m)
            }
        }
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Helpers (mirrors grep-searcher lines.rs helpers)
// ─────────────────────────────────────────────────────────────────────────────

/// Count occurrences of `line_term` in `bytes`.
pub(crate) fn count(bytes: &[u8], line_term: u8) -> u64 {
    memchr::memchr_iter(line_term, bytes).count() as u64
}

/// Strip the trailing line terminator from `bytes`, if present.
#[inline(always)]
pub(crate) fn without_terminator(bytes: &[u8], line_term: LineTerminator) -> &[u8] {
    let line_term = line_term.as_bytes();
    let start = bytes.len().saturating_sub(line_term.len());
    if bytes.get(start..) == Some(line_term) {
        return &bytes[..bytes.len() - line_term.len()];
    }
    bytes
}

/// Return the start/end offsets of the line(s) containing `range`.
#[inline(always)]
pub(crate) fn locate(bytes: &[u8], line_term: u8, range: Match) -> Match {
    let line_start =
        bytes[..range.start()].rfind_byte(line_term).map_or(0, |i| i + 1);
    let line_end =
        if range.end() > line_start && bytes[range.end() - 1] == line_term {
            range.end()
        } else {
            bytes[range.end()..]
                .find_byte(line_term)
                .map_or(bytes.len(), |i| range.end() + i + 1)
        };
    Match::new(line_start, line_end)
}

/// Return the minimal starting offset of the line that occurs `count` lines
/// before the last line in `bytes`.
pub(crate) fn preceding(bytes: &[u8], line_term: u8, count: usize) -> usize {
    preceding_by_pos(bytes, bytes.len(), line_term, count)
}

fn preceding_by_pos(
    bytes: &[u8],
    mut pos: usize,
    line_term: u8,
    mut count: usize,
) -> usize {
    if pos == 0 {
        return 0;
    } else if bytes[pos - 1] == line_term {
        pos -= 1;
    }
    loop {
        match bytes[..pos].rfind_byte(line_term) {
            None => return 0,
            Some(i) => {
                if count == 0 {
                    return i + 1;
                } else if i == 0 {
                    return 0;
                }
                count -= 1;
                pos = i;
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn line_count() {
        assert_eq!(0, count(b"",     b'\n'));
        assert_eq!(1, count(b"\n",   b'\n'));
        assert_eq!(2, count(b"\n\n", b'\n'));
        assert_eq!(2, count(b"a\nb\nc", b'\n'));
    }

    #[test]
    fn line_iter_basic() {
        let bytes = b"one\ntwo\nthree";
        let lines: Vec<&[u8]> = LineIter::new(b'\n', bytes).collect();
        assert_eq!(lines, vec![b"one\n", b"two\n", b"three"]);
    }
}
