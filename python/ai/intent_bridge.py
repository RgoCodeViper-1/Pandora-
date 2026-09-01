"""
ai/intent_bridge.py
===================
Primary Python ↔ Rust boundary for intent parsing.

Rust engine (rust_core) exports:
    process(text: str)        -> str  JSON: {"intents": [IntentResult, ...]}
    process_stream(text: str) -> str  JSON: IntentResult  OR  {"intents": [...]}
    pattern_count()           -> int

IntentResult JSON shape (from intent.rs / IntentResult struct):
    {
        "intent":         str,       # pattern id, e.g. "shutdown"
        "category":       str,       # snake_case IntentCategory variant
        "execution_type": str,       # snake_case ExecutionType variant
        "confidence":     float,     # 0.0 – 1.0
        "entities":       {str: str},
        "text":           str        # normalised, wakeword-stripped input
    }

Fallback chain:
    rust_core.process()  → confidence ≥ 0.70  →  return Rust result
    otherwise            → Python IntentParser fallback
"""

import json
import logging

logger = logging.getLogger("pandora.intent_bridge")

# ── Rust engine (built via maturin — `maturin develop` or installed wheel) ──
try:
    import pandora_core  # noqa: F401 — imported for side-effects (lazy RUNTIME init)
    _RUST_AVAILABLE = True
    logger.info(
        "pandora_core loaded — %d patterns compiled", pandora_core.pattern_count()
    )
except ImportError:
    pandora_core = None  # type: ignore[assignment]
    _RUST_AVAILABLE = False
    logger.warning(
        "pandora_core not available — falling back to Python intent parser for all requests"
    )

# ── Python rule-engine fallback ──────────────────────────────────────────────
try:
    from ai.intent_parser import IntentParser as _IntentParser

    _py_parser = _IntentParser()
except Exception as exc:  # pragma: no cover
    logger.error("Failed to initialise Python IntentParser: %s", exc)
    _py_parser = None  # type: ignore[assignment]


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

# Confidence threshold that must be met for the Rust result to be preferred
# over the Python fallback.  Mirrors engine::STREAM_THRESHOLD (0.70).
_RUST_CONFIDENCE_THRESHOLD: float = 0.70

# Sentinel returned when nothing at all could be parsed.
_UNKNOWN_INTENT: dict = {"intent": "unknown", "entities": {}, "confidence": 0.0}


def _normalize_rust_result(parsed: dict | list) -> dict | None:
    """
    Normalise whatever rust_core returns into a single IntentResult dict.

    Handles both shapes the engine can emit:
      • Single IntentResult  — process_stream() high-confidence fast path
        {"intent": "...", "confidence": 0.9, ...}
      • Batch envelope       — process() / process_stream() fallback
        {"intents": [{"intent": "...", ...}, ...]}
    """
    if not parsed:
        return None

    # Single IntentResult object (streaming fast path)
    if isinstance(parsed, dict) and "intent" in parsed:
        return parsed

    # Batch envelope — pick the highest-confidence entry (already sorted by
    # the Rust engine's IntentSink::finish() sort)
    if isinstance(parsed, dict) and "intents" in parsed:
        intents = parsed["intents"]
        if intents and isinstance(intents, list):
            return intents[0]

    return None


def _py_fallback(text: str) -> dict:
    """
    Run the Python rule-engine parser and return a normalised result dict.
    Returns _UNKNOWN_INTENT when the parser is unavailable.
    """
    if _py_parser is None:
        return {**_UNKNOWN_INTENT, "text": text}
    try:
        return _py_parser.parse(text).to_dict()
    except Exception as exc:
        logger.debug("Python IntentParser failed: %s", exc)
        return {**_UNKNOWN_INTENT, "text": text}


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def parse_intent(text: str) -> dict:
    """
    Parse a transcribed utterance and return a single IntentResult dict.

    Strategy:
      1. Try rust_core.process(text) — full batch scan.
      2. If the top result confidence ≥ 0.70, return it.
      3. Otherwise fall back to the Python rule engine.

    Always returns a dict with at least:
        {"intent": str, "entities": dict, "confidence": float}
    """
    if not text or not text.strip():
        return _UNKNOWN_INTENT

    rust_result: dict | None = None

    if _RUST_AVAILABLE:
        try:
            raw = pandora_core.process(text)
            parsed = json.loads(raw)
            rust_result = _normalize_rust_result(parsed)
        except Exception as exc:
            logger.debug("pandora_core.process() failed: %s", exc)
            rust_result = None

    if rust_result and rust_result.get("confidence", 0.0) >= _RUST_CONFIDENCE_THRESHOLD:
        return rust_result

    return _py_fallback(text)


def parse_intent_stream(text: str) -> dict | None:
    """
    Low-latency streaming parse — returns as soon as one intent clears 0.70.

    Returns:
        dict  — an IntentResult if pandora_core is available and confident.
        None  — if pandora_core is unavailable (caller falls back to parse_intent).

    Does NOT fall back to the Python parser because it is intended for the
    VAD hot path where latency matters more than coverage.
    """
    if not _RUST_AVAILABLE:
        return None

    if not text or not text.strip():
        return None

    try:
        raw = pandora_core.process_stream(text)
        parsed = json.loads(raw)
        return _normalize_rust_result(parsed)
    except Exception as exc:
        logger.debug("pandora_core.process_stream() failed: %s", exc)
        return None