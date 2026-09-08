"""Central response text for registered intents and runtime modes."""

from __future__ import annotations

import random
from datetime import datetime
from typing import Any, Mapping


class DialogueManager:
    def __init__(self):
        self.last_greet = 0

    def startup(self) -> str:
        return "Pandora is online and waiting in Optimized Idle."

    def wake_response(self) -> str:
        return "I am active. Please wait until I finish subsystem booting."

    def boot_completed(self) -> str:
        return "Boot and initialization completed."

    def greeting(self) -> str:
        return random.choice(("Hello.", "Good to hear from you.", "At your service."))

    def response(self, intent: str, result: Mapping[str, Any] | None = None) -> str:
        """Return a speakable response for a registry result."""
        result = result or {}
        text = result.get("response")
        if isinstance(text, str) and text.strip():
            return text.strip()

        entities = result.get("entities", result.get("slots", {}))
        if not isinstance(entities, Mapping):
            entities = {}
        query = str(
            entities.get(
                "query",
                entities.get("raw_text", result.get("text", "")),
            )
        ).strip()
        target = str(entities.get("app", entities.get("site", ""))).strip()
        templates = {
            "wakeword": self.wake_response,
            "wake_up": lambda: "Wake phrase acknowledged. I am listening in Command mode.",
            "greeting_hello": self.greeting,
            "greeting_general": self.greeting,
            "presence_check": lambda: "I'm operating normally and ready to help.",
            "identity_query": lambda: "I am Pandora, your desktop assistant.",
            "system_status": lambda: f"Pandora is operational in {result.get('mode', 'unknown')} mode.",
            "go_to_sleep": lambda: "Returning to Optimized Idle.",
            "shutdown": lambda: "Shutting down safely.",
            "mute": lambda: "Voice output muted.",
            "unmute": lambda: "Voice output restored.",
            "query_time": lambda: datetime.now().strftime("The time is %I:%M %p."),
            "query_date": lambda: datetime.now().strftime("Today is %B %d, %Y."),
            "query_version": lambda: "Pandora version 1.0.",
            "search_google": lambda: f"Searching the web for {query or 'that'}.",
            "search_wikipedia": lambda: f"Searching Wikipedia for {query or 'that'}.",
            "weather_query": lambda: "I am checking the weather.",
            "news_headlines": lambda: "I am checking the latest headlines.",
            "browser_open_known": lambda: f"Opening {target or 'the requested site'}.",
            "browser_open_url": lambda: f"Opening {target or 'the requested URL'}.",
            "app_open": lambda: f"Opening {target or 'the application'}.",
            "app_close": lambda: f"Closing {target or 'the application'}.",
            "unknown": lambda: self._unknown_response(query),
        }
        handler = templates.get(intent)
        return handler() if handler is not None else f"Completed {intent.replace('_', ' ')}."

    def format_result(self, result: Any) -> str:
        if isinstance(result, str):
            return result
        if isinstance(result, Mapping):
            return self.response(str(result.get("intent", "unknown")), result)
        return "The task completed without a spoken response."

    @staticmethod
    def _unknown_response(query: str) -> str:
        lowered = query.casefold()
        if any(phrase in lowered for phrase in ("how are you", "how are you doing")):
            return "I'm operating normally and ready to help."
        if "thank" in lowered:
            return "You're welcome."
        if query:
            return f"I heard {query!r}, but I do not have an action for it yet."
        return "I did not understand that request."