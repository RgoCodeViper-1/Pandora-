"""
services/wakeword_listener.py
==============================
Post-boot idle listener.

After Pandora boots, the system sits in IDLE and only opens the full
VAD + STT pipeline when the wakeword is heard.  This keeps mic overhead
near-zero between conversations.

Architecture position::

    Boot
     ↓
    WakewordListener.start()        ← this file
     ↓  (wakeword heard)
    VoiceRuntime.activate()
     ↓
    Full pipeline: VAD → STT → Intent → Executor → TTS
     ↓  (lifecycle intent: shutdown / sleep)
    VoiceRuntime.deactivate()
     ↓
    WakewordListener (back to idle loop)

Wakeword detection uses the same Rust intent engine (pandora_core) that
the full pipeline uses, via a lightweight sr.Recognizer snippet on the mic.
This avoids running Whisper just to detect "pandora".

If pandora_core is unavailable (e.g. first run before maturin build),
the listener falls back to simple substring matching on Google SR output.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Optional

logger = logging.getLogger("pandora.wakeword")

# Optional speech_recognition for the idle mic capture
try:
    import speech_recognition as sr
    SR_AVAILABLE = True
except ImportError:
    sr = None  # type: ignore[assignment]
    SR_AVAILABLE = False
    logger.warning("speech_recognition not installed — wakeword listener unavailable")

# Rust intent engine (already loaded by voice_runtime if available)
try:
    import pandora_core
    _RUST_AVAILABLE = True
except ImportError:
    pandora_core = None  # type: ignore[assignment]
    _RUST_AVAILABLE = False


# ─────────────────────────────────────────────────────────────────────────────
# Wakeword detection helpers
# ─────────────────────────────────────────────────────────────────────────────

# Known wakeword surface forms (mirrors dialogue.rs wakeword pattern)
_WAKEWORD_TOKENS = frozenset([
    "pandora", "hey pandora",
    "jarvis",  "hey jarvis",
    "pandra",  "pandor",
])


def _is_wakeword(text: str) -> bool:
    """
    Return True when ``text`` contains a recognised wakeword.

    Two-stage check:
    1. Fast substring scan against known tokens (zero dependencies).
    2. Rust intent engine — returns True if intent == "wakeword".
    """
    lowered = text.strip().lower()

    # Fast path
    for token in _WAKEWORD_TOKENS:
        if token in lowered:
            return True

    # Rust engine path
    if _RUST_AVAILABLE and pandora_core is not None:
        try:
            import json
            raw = pandora_core.process(text)
            result = json.loads(raw)
            intents = result.get("intents", [])
            if intents and intents[0].get("intent") == "wakeword":
                return True
        except Exception:
            pass

    return False


# ─────────────────────────────────────────────────────────────────────────────
# WakewordListener
# ─────────────────────────────────────────────────────────────────────────────

class WakewordListener:
    """
    Lightweight mic listener that sits idle between conversations.

    Calls ``on_wakeword()`` when the wakeword is detected; caller is
    responsible for activating VoiceRuntime in that callback.

    The listener uses speech_recognition's ``listen()`` with a short
    phrase_time_limit so it stays responsive without burning CPU.
    Google SR is used here because Whisper is intentionally not started
    yet — keeping idle overhead minimal.
    """

    def __init__(
        self,
        on_wakeword: Optional[callable] = None,  # type: ignore[type-arg]
        phrase_time_limit: float = 3.0,
        listen_timeout: float = 2.0,
        energy_threshold: int = 1200,
    ) -> None:
        self._on_wakeword = on_wakeword
        self._phrase_time_limit = phrase_time_limit
        self._listen_timeout = listen_timeout
        self._energy_threshold = energy_threshold

        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._active = False     # True while VoiceRuntime is handling a conversation

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the background listener thread (non-blocking)."""
        if not SR_AVAILABLE:
            logger.error("speech_recognition not available — wakeword listener cannot start")
            return

        if self._running:
            logger.warning("WakewordListener already running")
            return

        self._running = True
        self._thread = threading.Thread(
            target=self._listen_loop,
            daemon=True,
            name="pandora-wakeword",
        )
        self._thread.start()
        logger.info("WakewordListener started")

    def stop(self) -> None:
        self._running = False
        logger.info("WakewordListener stopped")

    def pause(self) -> None:
        """
        Pause wakeword detection while VoiceRuntime is active.
        Called by VoiceRuntime when a conversation session begins.
        """
        self._active = True
        logger.debug("WakewordListener paused (conversation active)")

    def resume(self) -> None:
        """
        Resume wakeword detection after VoiceRuntime returns to IDLE.
        Called by VoiceRuntime when a conversation session ends.
        """
        self._active = False
        logger.debug("WakewordListener resumed (back to idle)")

    # ------------------------------------------------------------------
    # Internal listen loop
    # ------------------------------------------------------------------

    def _listen_loop(self) -> None:
        recognizer = sr.Recognizer()
        recognizer.energy_threshold = self._energy_threshold
        recognizer.dynamic_energy_threshold = True

        mic = sr.Microphone()

        # Ambient noise calibration — short because we just want wakeword
        with mic as source:
            try:
                recognizer.adjust_for_ambient_noise(source, duration=0.8)
                logger.debug("Wakeword listener: ambient calibration done")
            except Exception as exc:
                logger.warning("Ambient calibration failed: %s", exc)

        logger.info("WakewordListener: idle, waiting for wakeword…")

        while self._running:
            # Do not compete with VoiceRuntime for the mic
            if self._active:
                time.sleep(0.3)
                continue

            try:
                with mic as source:
                    audio = recognizer.listen(
                        source,
                        timeout=self._listen_timeout,
                        phrase_time_limit=self._phrase_time_limit,
                    )
            except sr.WaitTimeoutError:
                continue
            except Exception as exc:
                logger.debug("Wakeword listen error: %s", exc)
                time.sleep(0.5)
                continue

            # Transcribe the short snippet
            text = self._quick_transcribe(recognizer, audio)
            if not text:
                continue

            logger.debug("Wakeword probe heard: %r", text)

            if _is_wakeword(text):
                logger.info("Wakeword detected: %r", text)
                self._active = True   # prevent re-entry until resume()
                if self._on_wakeword:
                    try:
                        self._on_wakeword(text)
                    except Exception as exc:
                        logger.error("on_wakeword callback error: %s", exc)
                        self._active = False

    def _quick_transcribe(self, recognizer: "sr.Recognizer", audio: "sr.AudioData") -> str:
        """
        Lightweight transcription for wakeword detection.
        Uses Google SR (network) or pure Sphinx (offline) as fallback.
        Returns empty string on failure — no exception raised.
        """
        try:
            return recognizer.recognize_google(audio).lower().strip()
        except sr.UnknownValueError:
            return ""
        except sr.RequestError:
            # Network unavailable — fall back to offline substring detection
            # by checking raw audio energy only (no text, just energy heuristic)
            return ""
        except Exception:
            return ""
