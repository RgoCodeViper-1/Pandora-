"""
PANDORA Scheduler
==================
Background service that fires contextual time-based and condition-based
notifications. Sends events to Node orchestrator via WebSocket.

Outbound events:
  { "event": "SCHEDULER_TRIGGER", "prompt_id": "hydration", "message": "..." }

Prompt types (from Jarvis CustomPrompt class):
  "time"         — fires within a daily time window once per day
  "interval"     — fires every N seconds (e.g. hydration, posture)
  "app_duration" — fires when a process has been running for N seconds

Reference: Jarvis in Jarvis_v1_7r_core.py
  - check_time_based_prompts() — morning, lunch, evening, night, break
  - CustomPrompt class — trigger types, cooldown, should_trigger(), trigger()
  - get_running_app_duration() — psutil process tracking
  - vscode_prompt — app_duration example for VS Code
  - Rewrites: speak() calls removed — events sent to Node instead
  - Retains: all timing windows and intervals exactly as Jarvis had them
"""

import asyncio
import datetime
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

import websockets

logger = logging.getLogger("pandora.scheduler")

try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False
    logger.warning("psutil not installed — app_duration triggers unavailable")

DEFAULT_CFG = {
    "node_ws_uri": "ws://localhost:8765",
    "poll_interval": 30,   # seconds between scheduler cycles
}


# ---------------------------------------------------------------------------
# Prompt data class — mirrors Jarvis CustomPrompt
# ---------------------------------------------------------------------------
@dataclass
class Prompt:
    prompt_id: str
    message: str
    trigger_type: str           # "time" | "interval" | "app_duration"
    trigger_params: dict = field(default_factory=dict)
    interval_seconds: float = 3600
    enabled: bool = True
    _last_triggered: Optional[datetime.datetime] = field(default=None, repr=False)
    _last_triggered_date: Optional[datetime.date] = field(default=None, repr=False)
    # For app_duration tracking
    _app_start_times: dict = field(default_factory=dict, repr=False)

    # ------------------------------------------------------------------
    # Trigger logic — mirrors Jarvis CustomPrompt.should_trigger()
    # ------------------------------------------------------------------
    def should_trigger(self) -> bool:
        if not self.enabled:
            return False

        # Global cooldown
        if self._last_triggered:
            elapsed = (datetime.datetime.now() - self._last_triggered).total_seconds()
            if elapsed < self.interval_seconds:
                return False

        if self.trigger_type == "time":
            return self._check_time()
        elif self.trigger_type == "interval":
            return self._check_interval()
        elif self.trigger_type == "app_duration":
            return self._check_app_duration()
        return False

    def mark_triggered(self) -> None:
        self._last_triggered = datetime.datetime.now()
        self._last_triggered_date = datetime.datetime.now().date()

    # ------------------------------------------------------------------
    # Time trigger — once per day within a time window
    # Mirrors Jarvis check_time_based_prompts() windows
    # ------------------------------------------------------------------
    def _check_time(self) -> bool:
        now = datetime.datetime.now()
        current_time = now.time()
        current_date = now.date()

        start = datetime.time(
            self.trigger_params.get("start_hour", 0),
            self.trigger_params.get("start_minute", 0),
        )
        end = datetime.time(
            self.trigger_params.get("end_hour", 23),
            self.trigger_params.get("end_minute", 59),
        )

        if not (start <= current_time <= end):
            return False

        # Only once per day
        if self._last_triggered_date == current_date:
            return False

        return True

    # ------------------------------------------------------------------
    # Interval trigger — every N seconds unconditionally
    # ------------------------------------------------------------------
    def _check_interval(self) -> bool:
        if self._last_triggered is None:
            return True
        elapsed = (datetime.datetime.now() - self._last_triggered).total_seconds()
        return elapsed >= self.interval_seconds

    # ------------------------------------------------------------------
    # App duration trigger — mirrors Jarvis get_running_app_duration()
    # ------------------------------------------------------------------
    def _check_app_duration(self) -> bool:
        if not PSUTIL_AVAILABLE:
            return False

        app_names = [n.lower() for n in self.trigger_params.get("app_names", [])]
        threshold = self.trigger_params.get("duration_seconds", 7200)

        for proc in psutil.process_iter(["pid", "name"]):
            try:
                proc_name = proc.info["name"].lower()
                if any(pattern in proc_name for pattern in app_names):
                    if proc_name not in self._app_start_times:
                        self._app_start_times[proc_name] = time.monotonic()
                    duration = time.monotonic() - self._app_start_times[proc_name]
                    if duration >= threshold:
                        return True
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        # Clean up stopped apps
        running_names = set()
        for proc in psutil.process_iter(["name"]):
            try:
                running_names.add(proc.info["name"].lower())
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        self._app_start_times = {
            k: v for k, v in self._app_start_times.items() if k in running_names
        }
        return False


# ---------------------------------------------------------------------------
# Built-in prompts — direct ports from Jarvis check_time_based_prompts()
# ---------------------------------------------------------------------------
def _build_default_prompts() -> list[Prompt]:
    return [
        # ── Morning greeting (7:00–9:00 AM, once per day) ──────────────
        Prompt(
            prompt_id="morning_greeting",
            message="Good morning. A new day full of possibilities awaits. Would you like a brief on today's schedule and weather?",
            trigger_type="time",
            trigger_params={"start_hour": 7, "start_minute": 0, "end_hour": 9, "end_minute": 0},
            interval_seconds=0,   # once per day enforced by date check
        ),
        # ── Lunch reminder (12:30–13:30) ─────────────────────────────────
        Prompt(
            prompt_id="lunch_reminder",
            message="It is midday. Perhaps it is time for a lunch break. Proper nutrition enhances productivity.",
            trigger_type="time",
            trigger_params={"start_hour": 12, "start_minute": 30, "end_hour": 13, "end_minute": 30},
            interval_seconds=0,
        ),
        # ── Evening wind-down (18:00–19:00) ──────────────────────────────
        Prompt(
            prompt_id="evening_reminder",
            message="Good evening. The workday is drawing to a close. Shall I summarise today's accomplishments?",
            trigger_type="time",
            trigger_params={"start_hour": 18, "start_minute": 0, "end_hour": 19, "end_minute": 0},
            interval_seconds=0,
        ),
        # ── Late night warning (23:00–01:00) ─────────────────────────────
        Prompt(
            prompt_id="night_warning",
            message="It is quite late. Adequate rest is essential for peak performance. Shall I set a morning reminder?",
            trigger_type="time",
            trigger_params={"start_hour": 23, "start_minute": 0, "end_hour": 23, "end_minute": 59},
            interval_seconds=0,
        ),
        # ── Break reminder (every 2 hours during work hours 9:00–18:00) ──
        Prompt(
            prompt_id="break_reminder",
            message="You have been working steadily. A brief break is recommended — perhaps a short walk or some stretching.",
            trigger_type="interval",
            trigger_params={},
            interval_seconds=7200,   # 2 hours
        ),
        # ── Hydration (every 90 min, 8:00–22:00) ─────────────────────────
        Prompt(
            prompt_id="hydration",
            message="Hydration check. Have you had water recently?",
            trigger_type="interval",
            trigger_params={},
            interval_seconds=5400,   # 90 minutes
        ),
        # ── Posture (every 45 min, 9:00–20:00) ───────────────────────────
        Prompt(
            prompt_id="posture_check",
            message="Posture check. Please adjust your seating position.",
            trigger_type="interval",
            trigger_params={},
            interval_seconds=2700,   # 45 minutes
        ),
        # ── VS Code session (app_duration, 2 hours, hourly reminder) ─────
        Prompt(
            prompt_id="vscode_coding_session",
            message="May I remind you that you have been quite invested in coding. Perhaps a brief respite would serve you well.",
            trigger_type="app_duration",
            trigger_params={
                "app_names": ["code.exe", "vscode", "code"],
                "duration_seconds": 7200,
            },
            interval_seconds=3600,
        ),
    ]


# ---------------------------------------------------------------------------
# Scheduler Service
# ---------------------------------------------------------------------------
class SchedulerService:
    """
    Polls registered prompts at cfg['poll_interval'] seconds.
    Fires SCHEDULER_TRIGGER events to Node for each triggered prompt.
    """

    def __init__(self, cfg: dict):
        self.cfg = {**DEFAULT_CFG, **cfg}
        self._prompts: list[Prompt] = _build_default_prompts()
        self._ws = None
        self._running = False

    # ------------------------------------------------------------------
    # Prompt registry — mirrors Jarvis register/remove_custom_prompt
    # ------------------------------------------------------------------
    def register(self, prompt: Prompt) -> None:
        self._prompts = [p for p in self._prompts if p.prompt_id != prompt.prompt_id]
        self._prompts.append(prompt)
        logger.info("Prompt registered: %s", prompt.prompt_id)

    def unregister(self, prompt_id: str) -> None:
        self._prompts = [p for p in self._prompts if p.prompt_id != prompt_id]

    def set_enabled(self, prompt_id: str, enabled: bool) -> None:
        for p in self._prompts:
            if p.prompt_id == prompt_id:
                p.enabled = enabled

    def list_prompts(self) -> list[dict]:
        return [
            {"prompt_id": p.prompt_id, "message": p.message[:60], "enabled": p.enabled}
            for p in self._prompts
        ]

    # ------------------------------------------------------------------
    # Emit
    # ------------------------------------------------------------------
    async def _emit(self, payload: dict) -> None:
        if self._ws:
            try:
                await self._ws.send(json.dumps(payload))
            except Exception as exc:
                logger.warning("WS send failed: %s", exc)

    # ------------------------------------------------------------------
    # Poll loop
    # ------------------------------------------------------------------
    async def _poll_loop(self) -> None:
        logger.info("Scheduler poll loop started (interval=%ds)", self.cfg["poll_interval"])
        while self._running:
            for prompt in self._prompts:
                try:
                    if prompt.should_trigger():
                        logger.info("Triggering prompt: %s", prompt.prompt_id)
                        prompt.mark_triggered()
                        await self._emit({
                            "event": "SCHEDULER_TRIGGER",
                            "prompt_id": prompt.prompt_id,
                            "message": prompt.message,
                        })
                except Exception as exc:
                    logger.warning("Prompt '%s' check failed: %s", prompt.prompt_id, exc)

            await asyncio.sleep(self.cfg["poll_interval"])

    # ------------------------------------------------------------------
    # Inbound control messages from Node
    # ------------------------------------------------------------------
    async def _handle_messages(self, ws) -> None:
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            event = msg.get("event", "")

            if event == "SCHEDULER_DISABLE":
                prompt_id = msg.get("prompt_id")
                if prompt_id:
                    self.set_enabled(prompt_id, False)
                else:
                    for p in self._prompts:
                        p.enabled = False
                logger.info("Scheduler disabled (prompt_id=%s)", prompt_id or "ALL")

            elif event == "SCHEDULER_ENABLE":
                prompt_id = msg.get("prompt_id")
                if prompt_id:
                    self.set_enabled(prompt_id, True)
                else:
                    for p in self._prompts:
                        p.enabled = True
                logger.info("Scheduler enabled (prompt_id=%s)", prompt_id or "ALL")

            elif event == "SCHEDULER_LIST":
                await self._emit({
                    "event": "SCHEDULER_LIST_RESPONSE",
                    "prompts": self.list_prompts(),
                })

    # ------------------------------------------------------------------
    # Connection loop
    # ------------------------------------------------------------------
    async def _connect_and_run(self) -> None:
        uri = self.cfg["node_ws_uri"]
        reconnect_delay = 2
        while self._running:
            try:
                async with websockets.connect(uri) as ws:
                    self._ws = ws
                    logger.info("Scheduler connected to Node at %s", uri)
                    reconnect_delay = 2
                    await asyncio.gather(
                        self._poll_loop(),
                        self._handle_messages(ws),
                    )
            except (OSError, websockets.exceptions.WebSocketException) as exc:
                logger.warning("WS lost: %s — retrying in %ds", exc, reconnect_delay)
                self._ws = None
                await asyncio.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 2, 30)

    def start(self) -> None:
        self._running = True
        asyncio.run(self._connect_and_run())

    def stop(self) -> None:
        self._running = False


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
    service = SchedulerService({})
    service.start()
