"""
PANDORA Memory Manager
=======================
Handles all persistent memory for the assistant:
  - Conversation log (rolling 100-entry window)
  - User topics / focus
  - Task list
  - Notes
  - Schedule
  - Runtime context (last intent, active task)
  - Personality state

Backed by two stores (mirrors Jarvis hybrid memory design):
  JSON  → shared/memory/state/runtime.json      (machine-readable runtime state)
  JSON  → shared/memory/tasks/tasks.json        (tasks)
  JSON  → shared/memory/knowledge/notes.json    (notes — structured)
  MD    → shared/memory/conversations/          (human-readable session logs)

Reference: Jarvis assistant_data.json functions in Jarvis_v1_7r_core.py
  - load_data / save_data / load_memory_context / update_memory_context
  - remember_topic / recall_topics / track_last_discussion
  - store_conversation_entry / recall_recent_conversations
  - summarize_memory_snapshot
  - add_task / mark_task_complete / list_tasks / delete_task / get_overdue_tasks
  - store_note / search_notes / list_note_categories
  - schedule functions
  - load_personality / update_personality_context
  - All rewritten: file paths → PANDORA shared/memory/, speak() removed
"""

import datetime
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger("pandora.memory")


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------
BASE = Path(os.environ.get("PANDORA_ROOT", "shared"))
MEMORY_DIR     = BASE / "memory"
STATE_FILE     = MEMORY_DIR / "state" / "runtime.json"
TASKS_FILE     = MEMORY_DIR / "tasks" / "tasks.json"
NOTES_FILE     = MEMORY_DIR / "knowledge" / "notes.json"
SCHEDULE_FILE  = MEMORY_DIR / "state" / "schedule.json"
CONV_DIR       = MEMORY_DIR / "conversations"

CONV_DIR.mkdir(parents=True, exist_ok=True)
STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
TASKS_FILE.parent.mkdir(parents=True, exist_ok=True)
NOTES_FILE.parent.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Generic JSON I/O helpers
# ---------------------------------------------------------------------------
def _read_json(path: Path, default) -> dict | list:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


# ===========================================================================
# Runtime context (replaces Jarvis memory_context inside assistant_data.json)
# ===========================================================================

def load_context() -> dict:
    default = {
        "user_topics": [],
        "daily_focus": None,
        "last_discussion": "",
        "last_active_time": "",
        "last_intent": None,
        "active_task": None,
    }
    data = _read_json(STATE_FILE, {})
    for key, val in default.items():
        data.setdefault(key, val)
    return data


def save_context(ctx: dict) -> None:
    _write_json(STATE_FILE, ctx)


def update_context(key: str, value) -> None:
    ctx = load_context()
    ctx[key] = value
    save_context(ctx)


def remember_topic(topic: str) -> None:
    """Add a topic to the remembered topics list (Jarvis: remember_topic)."""
    ctx = load_context()
    topics: list = ctx.get("user_topics", [])
    if topic and topic not in topics:
        topics.append(topic)
        ctx["user_topics"] = topics
        save_context(ctx)
        logger.info("Topic remembered: %s", topic)


def recall_topics() -> list[str]:
    """Return last 5 remembered topics (Jarvis: recall_topics)."""
    ctx = load_context()
    return ctx.get("user_topics", [])[-5:]


def set_daily_focus(focus: str) -> None:
    update_context("daily_focus", focus)


def track_last_discussion(text: str) -> None:
    update_context("last_discussion", text)
    update_context("last_active_time", datetime.datetime.now().isoformat())


# ===========================================================================
# Conversation log (Jarvis: store_conversation_entry / recall_recent_conversations)
# JSON log + Markdown session file
# ===========================================================================

_CONV_JSON = MEMORY_DIR / "state" / "conversations.json"
_MAX_ENTRIES = 100


def store_conversation(user_text: str, assistant_text: str) -> None:
    """
    Append a conversation turn.
    Mirrors Jarvis store_conversation_entry() with 100-entry rolling window.
    Also writes a human-readable Markdown file per session day.
    """
    entry = {
        "timestamp": datetime.datetime.now().isoformat(),
        "user": user_text,
        "assistant": assistant_text,
    }

    # JSON store
    conversations: list = _read_json(_CONV_JSON, [])
    conversations.append(entry)
    if len(conversations) > _MAX_ENTRIES:
        conversations = conversations[-_MAX_ENTRIES:]
    _write_json(_CONV_JSON, conversations)

    # Markdown session log — one file per calendar day
    today = datetime.date.today().isoformat()
    md_path = CONV_DIR / f"session_{today}.md"
    with open(md_path, "a", encoding="utf-8") as f:
        f.write(f"\n## {entry['timestamp']}\n")
        f.write(f"**User:** {user_text}\n\n")
        f.write(f"**PANDORA:** {assistant_text}\n\n---\n")


def recall_recent_conversations(days: int = 1) -> list[dict]:
    """
    Return conversations from the past N days.
    Mirrors Jarvis recall_recent_conversations().
    """
    conversations: list = _read_json(_CONV_JSON, [])
    cutoff = datetime.datetime.now() - datetime.timedelta(days=days)
    return [
        c for c in conversations
        if datetime.datetime.fromisoformat(c["timestamp"]) >= cutoff
    ]


# ===========================================================================
# Memory snapshot (Jarvis: summarize_memory_snapshot)
# ===========================================================================

def summarize_memory_snapshot() -> str:
    """
    Return a short plain-text summary of current context.
    Used by the greeting system to surface pending work.
    """
    ctx = load_context()
    tasks: list = _read_json(TASKS_FILE, {}).get("tasks", [])
    pending = [t for t in tasks if not t.get("completed")]
    parts: list[str] = []

    if pending:
        parts.append(f"you have {len(pending)} pending task{'s' if len(pending) != 1 else ''}")
    if ctx.get("daily_focus"):
        parts.append(f"today's focus is {ctx['daily_focus']}")

    overdue = _get_overdue_tasks_raw(tasks)
    if overdue:
        parts.append(f"{len(overdue)} overdue task{'s' if len(overdue) != 1 else ''} need attention")

    return ". ".join(parts) if parts else "All systems clear and routines are stable."


# ===========================================================================
# Tasks (Jarvis: add_task, mark_task_complete, list_tasks, delete_task)
# ===========================================================================

def _load_tasks() -> list[dict]:
    return _read_json(TASKS_FILE, {}).get("tasks", [])


def _save_tasks(tasks: list[dict]) -> None:
    _write_json(TASKS_FILE, {"tasks": tasks})


def _get_overdue_tasks_raw(tasks: list[dict]) -> list[dict]:
    today = datetime.datetime.now().date().isoformat()
    return [
        t for t in tasks
        if not t.get("completed")
        and t.get("deadline")
        and t["deadline"] < today
    ]


def add_task(description: str, priority: str = "medium",
             deadline: Optional[str] = None, category: Optional[str] = None) -> dict:
    """
    Create and persist a task. Returns the task dict.
    Mirrors Jarvis add_task() — same schema, no speak() call.
    """
    task = {
        "id": str(int(time.time() * 1000)),
        "description": description,
        "priority": priority.lower(),
        "deadline": deadline,
        "category": category,
        "created_at": datetime.datetime.now().isoformat(),
        "completed": False,
        "completed_at": None,
    }
    tasks = _load_tasks()
    tasks.append(task)
    _save_tasks(tasks)
    logger.info("Task added: %s [%s]", description, priority)
    return task


def mark_task_complete(id_or_description: str) -> Optional[dict]:
    """
    Mark a task complete by ID or fuzzy description match.
    Returns the updated task dict or None if not found.
    Mirrors Jarvis mark_task_complete().
    """
    tasks = _load_tasks()
    task = next((t for t in tasks if t.get("id") == id_or_description), None)
    if not task:
        lower = id_or_description.lower()
        task = next((t for t in tasks if lower in t.get("description", "").lower()), None)
    if not task:
        return None
    if task.get("completed"):
        return task   # already done
    task["completed"] = True
    task["completed_at"] = datetime.datetime.now().isoformat()
    _save_tasks(tasks)
    return task


def list_tasks(filter_type: str = "active", priority: Optional[str] = None) -> list[dict]:
    """
    Return tasks with optional filters. Mirrors Jarvis list_tasks().
    filter_type: "active" | "completed" | "all"
    """
    tasks = _load_tasks()
    if filter_type == "active":
        tasks = [t for t in tasks if not t.get("completed")]
    elif filter_type == "completed":
        tasks = [t for t in tasks if t.get("completed")]
    if priority:
        tasks = [t for t in tasks if t.get("priority") == priority.lower()]

    priority_order = {"high": 0, "medium": 1, "low": 2}
    tasks.sort(key=lambda t: (
        priority_order.get(t.get("priority", "medium"), 1),
        t.get("deadline") or "9999-12-31",
    ))
    return tasks


def delete_task(id_or_description: str) -> bool:
    tasks = _load_tasks()
    original_len = len(tasks)
    tasks = [t for t in tasks if t.get("id") != id_or_description]
    if len(tasks) == original_len:
        lower = id_or_description.lower()
        tasks = [t for t in tasks if lower not in t.get("description", "").lower()]
    if len(tasks) == original_len:
        return False
    _save_tasks(tasks)
    return True


def get_overdue_tasks() -> list[dict]:
    return _get_overdue_tasks_raw(_load_tasks())


# ===========================================================================
# Notes (Jarvis: store_note_enhanced, search_notes, list_note_categories)
# ===========================================================================

def _load_notes() -> list[dict]:
    return _read_json(NOTES_FILE, {}).get("notes", [])


def _save_notes(notes: list[dict]) -> None:
    _write_json(NOTES_FILE, {"notes": notes})


def create_note(content: str, category: str = "general",
                title: Optional[str] = None) -> dict:
    """Mirrors Jarvis store_note_enhanced()."""
    if not title:
        title = content[:50] + ("..." if len(content) > 50 else "")
    note = {
        "id": str(int(time.time() * 1000)),
        "title": title,
        "content": content,
        "category": category,
        "timestamp": datetime.datetime.now().isoformat(),
        "tags": [],
    }
    notes = _load_notes()
    notes.append(note)
    _save_notes(notes)
    logger.info("Note created: %s (%s)", title[:40], category)
    return note


def search_notes(query: str) -> list[dict]:
    """Mirrors Jarvis search_notes() — searches title, content, category."""
    q = query.lower()
    return [
        n for n in _load_notes()
        if q in n.get("title", "").lower()
        or q in n.get("content", "").lower()
        or q in n.get("category", "").lower()
    ]


def list_note_categories() -> dict[str, int]:
    """Return {category: count} dict. Mirrors Jarvis list_note_categories()."""
    categories: dict[str, int] = {}
    for note in _load_notes():
        cat = note.get("category", "general")
        categories[cat] = categories.get(cat, 0) + 1
    return categories


# ===========================================================================
# Schedule (Jarvis: add_schedule_for_today, review_schedule, modify_schedule)
# ===========================================================================

def _load_schedule() -> dict:
    return _read_json(SCHEDULE_FILE, {})


def _save_schedule(schedule: dict) -> None:
    _write_json(SCHEDULE_FILE, schedule)


def _date_key(offset: int = 0) -> tuple[str, str]:
    """Return (iso_date_string, day_name) for today + offset."""
    target = datetime.datetime.now().date() + datetime.timedelta(days=offset)
    return target.isoformat(), target.strftime("%A")


def add_schedule(plans_text: str, offset: int = 0) -> list[str]:
    """
    Parse and save a schedule entry. Returns list of parsed plan strings.
    Mirrors Jarvis add_schedule_for_today() parsing logic.
    """
    plans_text = plans_text.replace(" comma ", ", ")
    items = re.split(
        r"\s+and then\s+|\s+then\s+|\s+after that\s+|\s+followed by\s+|"
        r"\s+also\s+|\s+plus\s+|\s+and\s+|,\s*|\n+|\d+\.\s*",
        plans_text,
        flags=re.IGNORECASE,
    )
    filler = {"um", "uh", "like", "you know", "so", "well", "okay", "ok"}
    items = [i.strip() for i in items if i.strip() and len(i.strip()) > 2 and i.lower() not in filler]

    date_key, day_name = _date_key(offset)
    schedule = _load_schedule()
    schedule[date_key] = {
        "day_name": day_name,
        "plans": items,
        "created_at": datetime.datetime.now().isoformat(),
        "reviewed": False,
    }
    _save_schedule(schedule)
    logger.info("Schedule for %s saved (%d items)", date_key, len(items))
    return items


def get_schedule(offset: int = 0) -> Optional[dict]:
    """Return the schedule entry for a given day offset, or None."""
    date_key, _ = _date_key(offset)
    schedule = _load_schedule()
    entry = schedule.get(date_key)
    if entry:
        schedule[date_key]["reviewed"] = True
        _save_schedule(schedule)
    return entry


def clear_schedule(offset: int = 0) -> bool:
    date_key, _ = _date_key(offset)
    schedule = _load_schedule()
    if date_key in schedule:
        del schedule[date_key]
        _save_schedule(schedule)
        return True
    return False


# ===========================================================================
# Personality state (Jarvis: load_personality, update_personality_context)
# ===========================================================================

_PERSONALITY_FILE = MEMORY_DIR / "state" / "personality.json"

_DEFAULT_PERSONALITY = {
    "mode": "neutral",
    "positive_interactions": 0,
    "last_updated": None,
}


def load_personality() -> dict:
    data = _read_json(_PERSONALITY_FILE, {})
    for k, v in _DEFAULT_PERSONALITY.items():
        data.setdefault(k, v)
    return data


def update_personality(positive: bool) -> str:
    """
    Adjust personality based on interaction outcome.
    Mirrors Jarvis update_personality_context() scoring logic.
    Returns the new personality mode string.
    """
    state = load_personality()
    if positive:
        state["positive_interactions"] = state.get("positive_interactions", 0) + 1
    else:
        state["positive_interactions"] = max(0, state.get("positive_interactions", 0) - 1)

    score = state["positive_interactions"]
    if score >= 10:
        mode = "cheerful"
    elif score >= 5:
        mode = "neutral"
    elif score >= 2:
        mode = "calm"
    else:
        mode = "serious"

    state["mode"] = mode
    state["last_updated"] = datetime.datetime.now().isoformat()
    _write_json(_PERSONALITY_FILE, state)
    return mode
