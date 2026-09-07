"""
utils/memory_handler.py
========================
Memory / conversation context recall execution worker.

Invoked by core/registry.py for "memory_recall_topics" and
"memory_recall_conversations". Storage lives at
shared/memory/state/runtime.json.

Fixes vs. the legacy version:
  - Removed the import of `utils.util_handler`, which doesn't exist in
    this project — replaced with utils.file_utils (the real shared-storage
    access point).
  - Fixed `store_conversation_entry` referencing an undefined
    `pandora_response` variable (was `jarvis_response` param, never renamed).
"""

import datetime

from utils.decorators import safe_execute
from utils.file_utils import load_json, save_json, shared_path
from utils.util_response import send_response

MEMORY_FILE = shared_path("memory", "state", "runtime.json")
MAX_CONVERSATION_HISTORY = 100


def _load() -> dict:
    return load_json(MEMORY_FILE, default={})


def _save(data: dict) -> bool:
    return save_json(MEMORY_FILE, data)


@safe_execute("I couldn't recall that right now.")
def recall_topics(e: dict) -> str:
    """Handle 'memory_recall_topics' — recent discussion topics."""
    data = _load()
    topics = data.get("memory_context", {}).get("user_topics", [])

    if not topics:
        return send_response("We haven't recorded any specific topics recently.")

    for topic in topics[-5:]:
        print(f"- {topic}")

    return send_response("Here are the main topics we've talked about recently.")


@safe_execute("I couldn't recall our conversation history right now.")
def recall_conversations(e: dict) -> str:
    """Handle 'memory_recall_conversations' — recent conversation log."""
    data = _load()
    conversations = data.get("conversations", [])

    if not conversations:
        return send_response("We don't have any recorded conversations yet.")

    return send_response(f"We've had {len(conversations)} recorded exchanges recently.")


def store_conversation_entry(user_query: str, pandora_response: str) -> None:
    """
    Append a conversation entry to shared memory. Not routed through the
    intent registry directly — called by the executor/planner after every
    turn to keep memory_recall_* handlers useful.
    """
    data = _load()
    conversations = data.setdefault("conversations", [])

    conversations.append({
        "timestamp": datetime.datetime.now().isoformat(),
        "user": user_query,
        "pandora": pandora_response,
    })

    if len(conversations) > MAX_CONVERSATION_HISTORY:
        data["conversations"] = conversations[-MAX_CONVERSATION_HISTORY:]

    _save(data)
