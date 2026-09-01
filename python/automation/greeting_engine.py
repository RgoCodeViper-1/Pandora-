"""
PANDORA Greeting Engine
========================
Generates contextual, time-aware greeting messages for the assistant.
Output is a plain string — no speak() call inside.
Node orchestrator triggers a SPEAK event with the returned text.

Reference: Jarvis smart_greet_response() in Jarvis_v1_7r_core.py
  - Retains: time period detection (morning/afternoon/evening/night)
  - Retains: randomised greeting pool per period
  - Retains: 3-minute cooldown to avoid repeat greetings
  - Retains: optional weather fetch on 50% probability
  - Retains: memory snapshot integration (summarize_memory_snapshot)
  - Retains: dry-wit follow-up on 5% probability
  - Removes: direct speak() calls — returns text instead
  - Removes: get_weather() scraping via BeautifulSoup
    (weather now fetched via web.py task handler using weatherapi.com key)
"""

import datetime
import logging
import random
import time
from typing import Optional

logger = logging.getLogger("pandora.greeting")

# Track last greeting to enforce cooldown
_last_greet_ts: float = 0
_last_greeting_idx: int = -1
COOLDOWN_SECONDS: int = 180     # 3 minutes — matches Jarvis 180s cooldown


def _time_period(hour: int) -> str:
    if 5 <= hour < 12:
        return "morning"
    elif 12 <= hour < 18:
        return "afternoon"
    elif 18 <= hour < 22:
        return "evening"
    return "night"


# Greeting pools per time period — extended and rewritten from Jarvis greetings_map
_GREETINGS: dict[str, list[str]] = {
    "morning": [
        "Good morning, {honorific}. A fresh start to {day}.",
        "Morning, {honorific}. Ready to conquer the day?",
        "A bright morning to you, {honorific}.",
        "Good morning, {honorific}. Systems prepped and routines loaded.",
        "Rise and process, {honorific}. All systems nominal.",
    ],
    "afternoon": [
        "Good afternoon, {honorific}. Productivity still holding strong, I hope.",
        "Afternoon, {honorific}. Everything running at optimal efficiency.",
        "A fine afternoon to you, {honorific}.",
        "Good afternoon, {honorific}. Your focus remains admirable.",
        "Afternoon check-in, {honorific}. How may I assist?",
    ],
    "evening": [
        "Good evening, {honorific}. The day is almost complete — shall we review progress?",
        "Evening, {honorific}. System temperatures nominal; your workload, perhaps less so.",
        "A relaxing evening to you, {honorific}.",
        "Good evening, {honorific}. It is a pleasure to assist you once again.",
        "Evening, {honorific}. You have earned a moment of calm.",
    ],
    "night": [
        "Working late again, {honorific}? Shall I prepare a nightly summary?",
        "It is quite late, {honorific}. Do not forget to rest eventually.",
        "A quiet night, {honorific}. System diagnostics all green.",
        "Still active, {honorific}? Ever the dedicated one.",
        "Late session, {honorific}. I am here whenever you need me.",
    ],
}

_DRY_WIT_LINES: list[str] = [
    "Also, if I may say, I am running smoother than your CPU cooling system right now.",
    "On a personal note, {honorific}, I have not crashed once today. Quite the milestone.",
    "I processed your last query in 47 milliseconds. Just thought you should know.",
]


def build_greeting(
    honorific: str = "Sir",
    user_name: str = "",
    memory_snapshot: str = "",
    weather_summary: str = "",
    trigger_manual: bool = False,
) -> Optional[str]:
    """
    Build a contextual greeting string.

    Args:
        honorific: "Sir" or "Mam"
        user_name: User's name for personalisation
        memory_snapshot: Short text from memory_manager.summarize_memory_snapshot()
        weather_summary: Short weather string from web task handler (optional)
        trigger_manual: True when user explicitly greeted first; skips cooldown

    Returns:
        Greeting string, or None if cooldown is active and trigger_manual=False.
    """
    global _last_greet_ts, _last_greeting_idx

    now = datetime.datetime.now()
    ts = time.monotonic()

    # Cooldown — mirrors Jarvis 3-minute guard
    if not trigger_manual and ts - _last_greet_ts < COOLDOWN_SECONDS:
        return f"Still here, {honorific}. Always attentive."

    _last_greet_ts = ts

    period = _time_period(now.hour)
    day = now.strftime("%A")
    date_str = now.strftime("%B %d, %Y")
    time_str = now.strftime("%I:%M %p").lstrip("0")

    # Pick a non-repeating greeting from the pool
    pool = _GREETINGS[period]
    idx = random.randint(0, len(pool) - 1)
    while idx == _last_greeting_idx and len(pool) > 1:
        idx = random.randint(0, len(pool) - 1)
    _last_greeting_idx = idx
    base = pool[idx].format(honorific=honorific, day=day)

    parts = [base, f"It is {time_str} on {day}, {date_str}."]

    # Weather — 50% chance, only when available
    if weather_summary and random.random() < 0.5:
        parts.append(f"Today's weather: {weather_summary}.")

    # Memory context — 45% chance
    if memory_snapshot and random.random() < 0.45:
        parts.append(f"By the way, {honorific}, {memory_snapshot}.")

    # Dry wit — 5% chance
    if random.random() < 0.05:
        wit = random.choice(_DRY_WIT_LINES).format(honorific=honorific)
        parts.append(wit)

    # Manual greeting — add follow-up question
    if trigger_manual:
        parts.append(f"How are things going today, {honorific}?")

    return " ".join(parts)


def standby_response(honorific: str = "Sir") -> str:
    """Short response for wake-from-sleep. Mirrors Jarvis slept=True branch."""
    responses = [
        f"Yes, {honorific}.",
        f"At your service, {honorific}.",
        f"For you, {honorific}, always.",
        f"Standing by, {honorific}.",
    ]
    return random.choice(responses)


def activation_prompt(honorific: str = "Sir") -> str:
    """Spoken after wake word detected and system becomes LISTENING."""
    return (
        f"{honorific}, now that I am active, feel free to tell me what I can assist you with."
    )
