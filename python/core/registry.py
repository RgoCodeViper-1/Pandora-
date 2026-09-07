"""
core/registry.py
=================
Recognises a classified intent name and dispatches it to the headless
"util worker" that actually performs the task (utils/*_handler.py).

Also owns PANDORA's three operating modes (see PANDORA.txt):
    COMMAND         — normal single-shot command execution
    CONVERSATION    — sustained dialogue, dialogue/LLM turns get a
                       pre-rendered response cache check (see executor.py)
    OPTIMISED_IDLE  — lightweight idle, only wake/status/shutdown pass

Bug fixes vs. the previous version:
  - `app_open`/`app_close` used `subprocess.Popen(app, shell=True)` and a
    Windows-only `taskkill` string built from a raw, voice-transcribed
    entity — both a shell-injection shape and platform-specific. Now
    delegates to utils.app_handler, which is cross-platform and does not
    shell-interpolate the entity.
  - `google`/`wikipedia` interpolated the raw query into a URL with no
    encoding — a query containing spaces or `&` would build a broken or
    wrong URL. Now delegates to utils.search_handler, which URL-encodes.
  - `browser` blindly appended ".com" to every `site` slot
    (`f"https://{site}.com"`), which is wrong for most of the actual
    values the Rust engine's `browser_open_known` pattern can capture
    (gmail → mail.google.com, calendar → calendar.google.com,
    "stack overflow" → stackoverflow.com, wikipedia → wikipedia.org,
    etc). Now delegates to utils.browser_handler's KNOWN_SITES map.
  - `note_create` / `task_add` / `note_search` / `task_list` /
    `memory_*` were canned placeholder strings that never persisted or
    read anything — the exact "registry re-implements weaker duplicate
    logic instead of invoking the util worker" gap. Now delegates to the
    real, shared-storage-backed utils.notes_handler / utils.task_handler
    / utils.memory_handler / utils.weather_handler / utils.news_handler.

If `utils/` isn't importable (e.g. a minimal test environment), each
group falls back to a clearly-labelled stub instead of crashing, so this
module stays import-safe on its own.
"""
import datetime
import logging
from enum import Enum
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger("pandora.registry")


class PANDORAMode(Enum):
    COMMAND = "command"
    CONVERSATION = "conversation"
    OPTIMISED_IDLE = "optimised_idle"


# ─────────────────────────────────────────────────────────────────────────────
# Delegate imports — the actual task-execution workers
# ─────────────────────────────────────────────────────────────────────────────

try:
    from utils.app_handler import open_app as _open_app, close_app as _close_app
    from utils.browser_handler import open_site as _open_site
    from utils.search_handler import search_google as _search_google, search_wikipedia as _search_wikipedia
    from utils.weather_handler import fetch_weather as _fetch_weather
    from utils.news_handler import fetch_news as _fetch_news
    from utils.notes_handler import create_note as _create_note, search_notes as _search_notes
    from utils.task_handler import add_task as _add_task, list_tasks as _list_tasks
    from utils.memory_handler import recall_topics as _recall_topics, recall_conversations as _recall_conversations
    _HANDLERS_AVAILABLE = True
except ImportError as exc:
    logger.warning("utils/*_handler modules unavailable (%s) — using inline stubs", exc)
    _HANDLERS_AVAILABLE = False


class ActionRegistry:

    def __init__(self):
        self._mode: PANDORAMode = PANDORAMode.COMMAND

        # Consolidated routing dictionary combining control intents,
        # local actions, and standard fallback handlers.
        self.routes: Dict[str, Callable[[Dict[str, Any]], str]] = {
            # --- System & Mode Control ---
            "set_mode": self._handle_set_mode,
            "wake_up": self.wake_up,
            "system_status": self._handle_status,

            # --- Dialogue ---
            "wakeword": self.wakeword,
            "greeting_hello": self.greeting,
            "greeting_general": self.greeting,
            "presence_check": self.presence,
            "identity_query": self.identity,
            "go_to_sleep": self.sleep,
            "shutdown": self.shutdown,
            "mute": self.mute,
            "unmute": self.unmute,
            "progress_summary": self.progress,

            # --- Query ---
            "query_time": self.query_time,
            "query_date": self.query_date,
            "query_version": self.query_version,

            # --- Search ---
            "search_google": self.google,
            "search_wikipedia": self.wikipedia,
            "weather_query": self.weather,
            "weather_query_default": self.weather,
            "news_headlines": self.news,

            # --- Browser ---
            "browser_open_known": self.browser,
            "browser_open_url": self.browser,
            "browser_open_generic": self.browser,

            # --- Apps ---
            "app_open": self.app_open,
            "app_close": self.app_close,

            # --- Notes ---
            "note_create": self.note_create,
            "note_search": self.note_search,

            # --- Tasks ---
            "task_add": self.task_add,
            "task_list": self.task_list,

            # --- Memory ---
            "memory_recall_topics": self.memory_topics,
            "memory_recall_conversations": self.memory,

            # --- Fallback ---
            "unknown": self._handle_unknown,
        }

        # Intents allowed through even while OPTIMISED_IDLE (PANDORA.txt
        # Case 1: idle only reacts to wake/interaction — extended here to
        # also allow a status check and a clean shutdown at any time).
        self.IDLE_ALLOWED = frozenset({"wake_up", "set_mode", "system_status", "shutdown"})

    @property
    def current_mode(self) -> PANDORAMode:
        return self._mode

    @current_mode.setter
    def current_mode(self, new_mode: PANDORAMode):
        if new_mode != self._mode:
            logger.info("ActionRegistry mode: %s → %s", self._mode.value, new_mode.value)
        self._mode = new_mode

    def register(self, intent_name: str):
        """Decorator to register a dynamic handler function for a given intent."""
        def decorator(func: Callable[[Dict[str, Any]], str]):
            self.routes[intent_name] = func
            return func
        return decorator

    def get(self, intent_name: str) -> Optional[Callable[[Dict[str, Any]], str]]:
        """Retrieves a handler function directly by intent name (None if unmapped)."""
        return self.routes.get(intent_name)

    def execute(self, intent_name: str, slots: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Executes the mapped intent handler and returns a structured response payload."""
        if slots is None:
            slots = {}

        handler = self.routes.get(intent_name)
        if handler is None:
            response_text = self._handle_unknown(slots, intent_name)
        else:
            try:
                response_text = handler(slots)
            except Exception as exc:
                logger.warning("handler for %r raised: %s", intent_name, exc)
                response_text = f"I ran into a problem completing '{intent_name}'."

        return {
            "status": "success",
            "intent": intent_name,
            "response": response_text,
            "mode": self._mode.value,
        }

    # ==========================================
    # System & Mode Handlers
    # ==========================================

    def _handle_set_mode(self, slots: Dict[str, Any]) -> str:
        raw_mode = str(slots.get("mode", "")).lower().strip()
        try:
            self.current_mode = PANDORAMode(raw_mode)
            return f"PANDORA system transition: Activated [{self._mode.value.upper()}] mode."
        except ValueError:
            return f"Invalid operational mode '{raw_mode}'. Valid modes: command, conversation, optimised_idle."

    def _handle_status(self, slots: Dict[str, Any]) -> str:
        return f"PANDORA Architecture Status: Active | Mode: {self._mode.value.upper()}"

    def _handle_unknown(self, slots: Dict[str, Any], intent_name: str = "") -> str:
        if intent_name:
            return f"'{intent_name}' isn't wired to an action yet."
        return "Command intent unrecognised by local registry."

    # ==========================================
    # Dialogue
    # ==========================================

    def wakeword(self, e: Dict[str, Any]) -> str:
        return "Yes?"

    def greeting(self, e: Dict[str, Any]) -> str:
        return "Hello"

    def presence(self, e: Dict[str, Any]) -> str:
        return "I am here"

    def identity(self, e: Dict[str, Any]) -> str:
        return "I am Pandora"

    def wake_up(self, e: Dict[str, Any]) -> str:
        self.current_mode = PANDORAMode.COMMAND
        return "Wake phrase acknowledged. PANDORA online in COMMAND mode."

    def sleep(self, e: Dict[str, Any]) -> str:
        self.current_mode = PANDORAMode.OPTIMISED_IDLE
        return "Entering standby"

    def shutdown(self, e: Dict[str, Any]) -> str:
        return "Shutting down Pandora"

    def mute(self, e: Dict[str, Any]) -> str:
        return "Muted"

    def unmute(self, e: Dict[str, Any]) -> str:
        return "Speaking again"

    def progress(self, e: Dict[str, Any]) -> str:
        return "Generating summary"

    # ==========================================
    # Query
    # ==========================================

    def query_time(self, e: Dict[str, Any]) -> str:
        return datetime.datetime.now().strftime("Current time is %I:%M %p")

    def query_date(self, e: Dict[str, Any]) -> str:
        return datetime.datetime.now().strftime("Today is %B %d %Y")

    def query_version(self, e: Dict[str, Any]) -> str:
        return "Pandora version 1.0"

    # ==========================================
    # Search / Weather / News — delegate to utils/*_handler
    # ==========================================

    def google(self, e: Dict[str, Any]) -> str:
        if _HANDLERS_AVAILABLE:
            return _search_google(e)
        return f"Searching {e.get('query', '')}"

    def wikipedia(self, e: Dict[str, Any]) -> str:
        if _HANDLERS_AVAILABLE:
            return _search_wikipedia(e)
        return f"Searching Wikipedia for {e.get('topic', '')}"

    def weather(self, e: Dict[str, Any]) -> str:
        if _HANDLERS_AVAILABLE:
            return _fetch_weather(e)
        return f"Fetching weather for {e.get('location', 'current location')}"

    def news(self, e: Dict[str, Any]) -> str:
        if _HANDLERS_AVAILABLE:
            return _fetch_news(e)
        return "Fetching latest news"

    # ==========================================
    # Browser — delegate to utils.browser_handler (real site map, no
    # blind ".com" concatenation)
    # ==========================================

    def browser(self, e: Dict[str, Any]) -> str:
        if _HANDLERS_AVAILABLE:
            return _open_site(e)
        target = e.get("url") or e.get("site")
        return f"Opening {target}" if target else "Opening browser"

    # ==========================================
    # Apps — delegate to utils.app_handler (cross-platform, no shell=True
    # string built from a raw voice entity)
    # ==========================================

    def app_open(self, e: Dict[str, Any]) -> str:
        if _HANDLERS_AVAILABLE:
            return _open_app(e)
        return f"Opening {e.get('app', '')}"

    def app_close(self, e: Dict[str, Any]) -> str:
        if _HANDLERS_AVAILABLE:
            return _close_app(e)
        return f"Closing {e.get('app', '')}"

    # ==========================================
    # Notes — delegate to utils.notes_handler (persists to shared/memory)
    # ==========================================

    def note_create(self, e: Dict[str, Any]) -> str:
        if _HANDLERS_AVAILABLE:
            return _create_note(e)
        return "Ready for note"

    def note_search(self, e: Dict[str, Any]) -> str:
        if _HANDLERS_AVAILABLE:
            return _search_notes(e)
        return f"Searching notes: {e.get('query', '')}"

    # ==========================================
    # Tasks — delegate to utils.task_handler (persists to shared/memory)
    # ==========================================

    def task_add(self, e: Dict[str, Any]) -> str:
        if _HANDLERS_AVAILABLE:
            return _add_task(e)
        return f"Task added: {e.get('description', '')}"

    def task_list(self, e: Dict[str, Any]) -> str:
        if _HANDLERS_AVAILABLE:
            return _list_tasks(e)
        return "Listing tasks"

    # ==========================================
    # Memory — delegate to utils.memory_handler
    #
    # Two distinct routes now call two distinct recall functions instead
    # of both landing on one canned "Searching memory" string — that was
    # the previous mismatch (memory_recall_topics and
    # memory_recall_conversations are different intents with different
    # underlying data in shared/memory/state/runtime.json).
    # ==========================================

    def memory(self, e: Dict[str, Any]) -> str:
        if _HANDLERS_AVAILABLE:
            return _recall_conversations(e)
        return "Searching memory"

    def memory_topics(self, e: Dict[str, Any]) -> str:
        if _HANDLERS_AVAILABLE:
            return _recall_topics(e)
        return "Searching memory"
