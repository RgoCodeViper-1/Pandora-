"""
speech/tts.py
=============
Thin module-level helper that proxies speak() calls to TTSService.

Fixes over previous version:
  • asyncio.get_event_loop() is deprecated in Python ≥ 3.10 — replaced with
    asyncio.get_running_loop() (only valid inside a coroutine) and a separate
    thread-safe path for calls from synchronous code.
  • create_task() on the event loop at import time raised RuntimeError when no
    loop was running — the service is now started lazily on first speak() call.
  • Priority flag is forwarded correctly to TTSService.speak().
"""
from __future__ import annotations
from typing import TYPE_CHECKING, Optional, Any
import asyncio
import logging
import threading

logger = logging.getLogger("pandora.tts")

if TYPE_CHECKING:
    from services.tts_service import TTSService as _TTSServiceType
 
_svc: Optional[Any] = None  # holds a TTSService instance, or None
 
try:
    from services.tts_service import TTSService as _TTSServiceCls
    _svc = _TTSServiceCls()
    logger.debug("TTSService loaded successfully")
except ImportError:
    logger.warning("TTSService not available — speak() calls will be no-ops")

# Background event loop that runs the TTSService coroutines when speak() is
# called from synchronous (non-async) code paths such as the rule executor.
_bg_loop: asyncio.AbstractEventLoop | None = None
_bg_thread: threading.Thread | None = None
_svc_started = False
_lock = threading.Lock()


def _ensure_background_loop() -> asyncio.AbstractEventLoop:
    """
    Create and start a persistent background asyncio loop in a daemon thread
    if one does not already exist.  Thread-safe; idempotent.
    """
    global _bg_loop, _bg_thread, _svc_started

    with _lock:
        if _bg_loop is not None and _bg_loop.is_running():
            return _bg_loop

        loop = asyncio.new_event_loop()
        _bg_loop = loop

        def _run() -> None:
            asyncio.set_event_loop(loop)
            loop.run_forever()

        t = threading.Thread(target=_run, daemon=True, name="pandora-tts-loop")
        t.start()
        _bg_thread = t

        # Start the TTSService consumer on the new loop
        if _svc is not None and not _svc_started:
            asyncio.run_coroutine_threadsafe(_svc.start(), loop)
            _svc_started = True
            logger.debug("TTSService started on background loop")

    return loop


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def speak(text: str, priority: bool = False) -> None:
    """
    Non-blocking TTS call.  Safe to call from both sync and async contexts.

    Args:
        text:     The string to synthesise.  No-op if empty.
        priority: When True, the TTSService places this utterance at the front
                  of its internal queue (interrupts lower-priority speech).
    """
    if not text or _svc is None:
        return

    try:
        # ── Inside a running asyncio loop (e.g. vad_service coroutine) ──
        running_loop = asyncio.get_running_loop()
        running_loop.create_task(_svc.speak(text, priority=priority))
    except RuntimeError:
        # ── Called from synchronous code — use the background loop ───────
        loop = _ensure_background_loop()
        asyncio.run_coroutine_threadsafe(_svc.speak(text, priority=priority), loop)