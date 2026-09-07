"""
utils/task_handler.py
======================
Task management execution worker.

Invoked by core/registry.py for "task_add", "task_list", "task_complete",
"task_delete", "task_overdue". Storage lives at shared/memory/tasks/tasks.json.
"""

import time
import datetime

from utils.decorators import safe_execute
from utils.file_utils import load_json, save_json, shared_path
from utils.formatters import format_count
from utils.time_utils import is_overdue
from utils.util_response import send_response

TASKS_FILE = shared_path("memory", "tasks", "tasks.json")

PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2}
PRIORITY_MARK = {"high": "[!]", "medium": "[-]", "low": "[.]"}


def _load() -> list[dict]:
    return load_json(TASKS_FILE, default={"tasks": []}).get("tasks", [])


def _save(tasks: list[dict]) -> bool:
    return save_json(TASKS_FILE, {"tasks": tasks})


@safe_execute("I couldn't add that task.")
def add_task(e: dict) -> str:
    """Add a new task. Expects a 'description' slot."""
    description = (e.get("description") or "").strip()
    if not description:
        return send_response("What task would you like me to add?")

    priority = (e.get("priority") or "medium").lower()
    deadline = e.get("deadline")

    task = {
        "id": str(int(time.time() * 1000)),
        "description": description,
        "priority": priority,
        "deadline": deadline,
        "created_at": datetime.datetime.now().isoformat(),
        "completed": False,
        "completed_at": None,
    }

    tasks = _load()
    tasks.append(task)
    _save(tasks)

    deadline_str = f" with deadline {deadline}" if deadline else ""
    return send_response(f"Task added with {priority} priority{deadline_str}.")


@safe_execute("I couldn't list your tasks.")
def list_tasks(e: dict) -> str:
    """List active tasks, sorted by priority then deadline."""
    tasks = [t for t in _load() if not t.get("completed")]
    if not tasks:
        return send_response("You have no active tasks.")

    tasks.sort(key=lambda t: (
        PRIORITY_ORDER.get(t.get("priority", "medium"), 1),
        t.get("deadline") or "9999-12-31",
    ))

    lines = [
        f"{PRIORITY_MARK.get(t.get('priority', 'medium'), '[-]')} {t['description']}"
        + (f" (due {t.get('deadline')})" if t.get("deadline") else "")
        for t in tasks
    ]
    for line in lines:
        print(line)

    return send_response(f"You have {format_count(len(tasks), 'task')}.")


@safe_execute("I couldn't complete that task.")
def complete_task(e: dict) -> str:
    """Mark a task as complete by fuzzy substring match on description."""
    query = (e.get("task") or "").strip().lower()
    if not query:
        return send_response("Which task should I mark complete?")

    tasks = _load()
    for t in tasks:
        if not t.get("completed") and query in t.get("description", "").lower():
            t["completed"] = True
            t["completed_at"] = datetime.datetime.now().isoformat()
            _save(tasks)
            return send_response(f"Marked '{t['description']}' as complete.")

    return send_response(f"I couldn't find an active task matching '{query}'.")


@safe_execute("I couldn't delete that task.")
def delete_task(e: dict) -> str:
    """Delete a task by fuzzy substring match on description."""
    query = (e.get("task") or "").strip().lower()
    if not query:
        return send_response("Which task should I delete?")

    tasks = _load()
    remaining = [t for t in tasks if query not in t.get("description", "").lower()]

    if len(remaining) == len(tasks):
        return send_response(f"I couldn't find a task matching '{query}'.")

    _save(remaining)
    return send_response(f"Deleted the task matching '{query}'.")


@safe_execute("I couldn't check for overdue tasks.")
def overdue_tasks(e: dict) -> str:
    """List active tasks whose deadline has passed."""
    tasks = [
        t for t in _load()
        if not t.get("completed") and is_overdue(t.get("deadline"))
    ]
    if not tasks:
        return send_response("You have no overdue tasks.")

    for t in tasks:
        print(f"{PRIORITY_MARK.get(t.get('priority', 'medium'), '[-]')} {t['description']} (due {t.get('deadline')})")

    return send_response(f"You have {format_count(len(tasks), 'overdue task')}.")
