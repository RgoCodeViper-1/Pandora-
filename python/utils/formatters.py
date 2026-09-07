"""
utils/formatters.py
====================
Text-formatting helpers shared by handlers that need to turn a list of
records (tasks, notes, headlines) into a single spoken/printed string.
"""

from typing import Iterable

PRIORITY_MARKS = {"high": "[!]", "medium": "[-]", "low": "[.]"}


def format_list(items: Iterable[str], empty_message: str = "There's nothing to show.") -> str:
    """Join items into a single newline-separated string, or return
    `empty_message` if the iterable is empty."""
    items = list(items)
    if not items:
        return empty_message
    return "\n".join(items)


def format_count(n: int, singular: str, plural: str | None = None) -> str:
    """'1 task' / '3 tasks' style pluralisation."""
    plural = plural or f"{singular}s"
    return f"{n} {singular if n == 1 else plural}"


def truncate(text: str, max_len: int = 100, suffix: str = "...") -> str:
    text = (text or "").strip()
    if len(text) <= max_len:
        return text
    return text[: max_len - len(suffix)].rstrip() + suffix
