"""
Intent routing for Pandora.

This module owns the boundary between intent classification and execution:

    text -> ai.intent_bridge -> normalized intent payload -> Executor

It deliberately does not parse patterns or execute handlers itself.  The Rust
engine/Python fallback remain the parsing authority, while Executor remains the
execution authority.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Union

from ai.intent_bridge import parse_intent, parse_intent_stream
from core.executor import Executor


IntentPayload = Dict[str, Any]
IntentInput = Union[IntentPayload, List[IntentPayload]]


class IntentRouter:
    """Classify text and dispatch the resulting intent(s) to an executor."""

    def __init__(self, executor: Executor | None = None) -> None:
        self.executor = executor or Executor()

    def route_text(self, text: str, *, streaming: bool = False) -> Dict[str, Any]:
        """
        Parse and execute one utterance.

        Streaming mode uses the Rust fast path first and falls back to the
        normal parser when no streaming result is available.
        """
        if not isinstance(text, str) or not text.strip():
            raise ValueError("Intent text must be a non-empty string")

        intent = parse_intent_stream(text) if streaming else None
        if intent is None:
            intent = parse_intent(text)

        return self.route_intent(intent)

    def route_intent(self, intent: IntentPayload) -> Dict[str, Any]:
        """Execute one already-classified intent payload."""
        if not isinstance(intent, dict):
            raise TypeError("Intent payload must be a dictionary")

        intent_name = intent.get("intent")
        if not isinstance(intent_name, str) or not intent_name.strip():
            raise ValueError("Intent payload must contain a non-empty 'intent'")

        return self.executor.execute_intent(intent)

    def route_batch(self, payload: IntentInput) -> Dict[str, Any]:
        """Execute a batch payload returned by the Rust engine."""
        if isinstance(payload, dict):
            intents = payload.get("intents")
            if intents is None:
                intents = [payload]
        else:
            intents = payload

        if not isinstance(intents, list):
            raise TypeError("Intent batch must contain a list of intent payloads")
        if not all(isinstance(intent, dict) for intent in intents):
            raise TypeError("Every item in an intent batch must be a dictionary")

        return self.executor.execute_batch(intents)

    def classify_and_route_batch(self, text: str) -> Dict[str, Any]:
        """Parse all matching intents for text, then execute them in order."""
        if not isinstance(text, str) or not text.strip():
            raise ValueError("Intent text must be a non-empty string")

        from ai.intent_bridge import pandora_core

        if pandora_core is not None:
            try:
                parsed = json.loads(pandora_core.process(text))
            except (json.JSONDecodeError, RuntimeError):
                parsed = None

            if parsed is not None:
                return self.route_batch(parsed)

        return self.route_intent(parse_intent(text))


__all__ = ["IntentRouter", "IntentInput", "IntentPayload"]
