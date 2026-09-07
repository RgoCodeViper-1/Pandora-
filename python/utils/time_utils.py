"""
utils/time_utils.py
====================
Small date/time helpers shared across handlers.
"""

import datetime


def now() -> datetime.datetime:
    return datetime.datetime.now()


def current_time_str() -> str:
    return now().strftime("%I:%M %p")


def current_date_str() -> str:
    return now().strftime("%B %d %Y")


def iso_timestamp() -> str:
    return now().isoformat()


def is_overdue(deadline_iso: str | None) -> bool:
    if not deadline_iso:
        return False
    try:
        deadline = datetime.datetime.fromisoformat(deadline_iso)
    except ValueError:
        return False
    return deadline < now()
