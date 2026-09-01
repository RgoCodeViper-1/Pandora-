/// Pattern registry aggregator.
///
/// Merges every sub-registry into the single static `REGISTRY` that the
/// engine consumes.  Adding a new category means (1) creating a new file in
/// this directory and (2) adding it to the `all()` call below — nothing else
/// needs changing.
pub mod automation;
pub mod dialogue;
pub mod search;
pub mod system;

use crate::intent::IntentPattern;
use once_cell::sync::Lazy;

/// The complete static intent pattern registry, sorted by descending weight
/// so the engine always tests the highest-priority patterns first.
pub static REGISTRY: Lazy<Vec<IntentPattern>> = Lazy::new(|| {
    let mut all: Vec<IntentPattern> = Vec::new();
    all.extend(dialogue::all());
    all.extend(system::all());
    all.extend(search::all());
    all.extend(automation::all());
    // Sort highest weight first — the engine will short-circuit on the first
    // sufficiently confident match when streaming.
    all.sort_by(|a, b| b.weight.partial_cmp(&a.weight).unwrap_or(std::cmp::Ordering::Equal));
    all
});
