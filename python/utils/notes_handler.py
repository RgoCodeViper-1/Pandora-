"""
utils/notes_handler.py
=======================
Note storage / recall execution worker.

Invoked by core/registry.py for "note_create" and "note_search".
Storage lives under shared/memory/notes/notes.json via utils.file_utils —
no dedicated per-module data folder.
"""

import time
import datetime

from utils.decorators import safe_execute
from utils.file_utils import load_json, save_json, shared_path
from utils.formatters import format_count
from utils.util_response import send_response

NOTES_FILE = shared_path("memory", "notes", "notes.json")


def _load() -> dict:
    return load_json(NOTES_FILE, default={"notes": []})


def _save(data: dict) -> bool:
    return save_json(NOTES_FILE, data)


@safe_execute("I couldn't save that note.")
def create_note(e: dict) -> str:
    """Store a note. Expects a 'content' slot (falls back to 'description'
    or raw text if the intent captured it under a different name)."""
    content = (e.get("content") or e.get("description") or e.get("query") or "").strip()

    if not content:
        return send_response("What would you like me to note down?")

    category = (e.get("category") or "general").strip()
    title = content[:50] + "..." if len(content) > 50 else content

    note = {
        "id": str(int(time.time() * 1000)),
        "title": title,
        "content": content,
        "category": category,
        "timestamp": datetime.datetime.now().isoformat(),
        "tags": [],
    }

    data = _load()
    data.setdefault("notes", []).append(note)
    _save(data)

    return send_response(f"Note saved under '{category}'.")


@safe_execute("I couldn't search your notes.")
def search_notes(e: dict) -> str:
    """Search notes by content, title, or category."""
    query = (e.get("query") or "").strip()
    if not query:
        return send_response("What would you like me to search your notes for?")

    query_lower = query.lower()
    data = _load()
    notes = data.get("notes", [])

    matches = [
        n for n in notes
        if query_lower in n.get("title", "").lower()
        or query_lower in n.get("content", "").lower()
        or query_lower in n.get("category", "").lower()
    ]

    if not matches:
        return send_response(f"No notes found matching '{query}'.")

    summary = format_count(len(matches), "note")
    return send_response(f"Found {summary} matching '{query}'.")
