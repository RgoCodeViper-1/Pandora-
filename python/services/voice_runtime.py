"""
services/voice_runtime.py
==========================
Single orchestration entrypoint for Pandora's speech pipeline.

Owns the lifecycle of every component in the audio chain:

    Mic → VAD → STT (Whisper) → Rust Intent → Executor → TTS

State machine:
    IDLE         — wakeword listener active, mic closed
    LISTENING    — mic open, VAD watching for speech onset
    CAPTURING    — speech detected, accumulating audio
    PROCESSING   — audio complete, STT + intent running
    EXECUTING    — dispatcher running action
    SPEAKING     — TTS playing response
    INTERRUPTED  — ESC / barge-in received, pipeline flushed

No component is imported at the top level — each is resolved lazily from
ServiceRegistry so that this module is safe to import before any
heavyweight dependency (torch, sounddevice, etc.) has been loaded.

External callers only need:

    runtime = VoiceRuntime()
    runtime.start()   # blocks — run in a daemon thread or async task
    runtime.stop()
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from enum import Enum, auto
from typing import Callable, Optional

logger = logging.getLogger("pandora.voice_runtime")


# ─────────────────────────────────────────────────────────────────────────────
# Pipeline states
# ─────────────────────────────────────────────────────────────────────────────

class PipelineState(Enum):
    IDLE        = auto()
    LISTENING   = auto()
    CAPTURING   = auto()
    PROCESSING  = auto()
    EXECUTING   = auto()
    SPEAKING    = auto()
    INTERRUPTED = auto()
    STOPPED     = auto()


# ─────────────────────────────────────────────────────────────────────────────
# VoiceRuntime
# ─────────────────────────────────────────────────────────────────────────────

class VoiceRuntime:
    """
    Orchestrates the full Pandora speech pipeline.

    The runtime does NOT import VADService, WhisperSTT, or SpeechToTextEngine
    directly.  Instead it:
      1. Expects a ``WhisperManager`` to have already ensured the server is up.
      2. Imports VADService + SpeechToTextEngine lazily on ``start()``.
      3. Wires VADService's on-transcript callback to the STT → Intent → Execute
         chain.
      4. Emits state transitions so a wakeword listener or Node IPC layer can
         observe them.

    Constructor kwargs
    ------------------
    whisper_url : str
        Inference endpoint for WhisperSTT (default 127.0.0.1:8080).
    on_state_change : Callable[[PipelineState], None] | None
        Optional callback invoked on every state transition.
    on_response : Callable[[str, dict], None] | None
        Optional callback invoked with (spoken_text, intent_result) after each
        completed utterance — useful for the Node WS bridge to forward UI
        updates.
    node_ws_uri : str
        WebSocket URI for Node orchestrator.  Passed into VADService so
        INTENT_READY events are also forwarded to the UI layer.
    """

    def __init__(
        self,
        whisper_url: str = "http://127.0.0.1:8080/inference",
        on_state_change: Optional[Callable[[PipelineState], None]] = None,
        on_response: Optional[Callable[[str, dict], None]] = None,
        node_ws_uri: str = "ws://localhost:8765",
        vad_cfg: Optional[dict] = None,
    ) -> None:
        self._whisper_url = whisper_url
        self._on_state_change = on_state_change
        self._on_response = on_response
        self._node_ws_uri = node_ws_uri
        self._vad_cfg_override = vad_cfg or {}

        self._state = PipelineState.IDLE
        self._running = False
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._vad_thread: Optional[threading.Thread] = None

        # Lazily resolved service instances
        self._stt = None
        self._executor = None
        self._vad = None

    # ------------------------------------------------------------------
    # State management
    # ------------------------------------------------------------------

    @property
    def state(self) -> PipelineState:
        return self._state

    def _set_state(self, new_state: PipelineState) -> None:
        if new_state == self._state:
            return
        logger.info("VoiceRuntime: %s → %s", self._state.name, new_state.name)
        self._state = new_state
        if self._on_state_change:
            try:
                self._on_state_change(new_state)
            except Exception as exc:
                logger.warning("on_state_change callback error: %s", exc)

    # ------------------------------------------------------------------
    # Startup / shutdown
    # ------------------------------------------------------------------

    def start(self) -> None:
        """
        Start the runtime.  Blocks the calling thread.

        Call from a daemon thread::

            t = threading.Thread(target=runtime.start, daemon=True)
            t.start()
        """
        if self._running:
            logger.warning("VoiceRuntime already running")
            return

        self._running = True
        logger.info("VoiceRuntime starting")

        self._boot_services()
        self._set_state(PipelineState.LISTENING)
        self._run_vad()

    def stop(self) -> None:
        """Signal the runtime and all managed services to stop."""
        self._running = False
        self._set_state(PipelineState.STOPPED)

        if self._vad is not None:
            try:
                self._vad.stop()
            except Exception as exc:
                logger.warning("Error stopping VAD: %s", exc)

        if self._stt is not None:
            try:
                self._stt.stop()
            except Exception:
                pass

        logger.info("VoiceRuntime stopped")

    def interrupt(self) -> None:
        """
        Flush the active utterance — triggered by ESC / barge-in.
        Transitions to INTERRUPTED then immediately back to LISTENING.
        """
        self._set_state(PipelineState.INTERRUPTED)
        logger.debug("VoiceRuntime: interrupt — returning to LISTENING")
        self._set_state(PipelineState.LISTENING)

    # ------------------------------------------------------------------
    # Service bootstrap (lazy imports)
    # ------------------------------------------------------------------

    def _boot_services(self) -> None:
        """
        Lazily import and initialise pipeline components.
        Any heavyweight import (torch, sounddevice) happens here, not at
        module load time.
        """
        # ── STT engine ────────────────────────────────────────────────
        try:
            from speech.stt import SpeechToTextEngine  # type: ignore[import]
        except ImportError:
            try:
                from stt import SpeechToTextEngine  # type: ignore[import]
            except ImportError:
                logger.error("Could not import SpeechToTextEngine — STT disabled")
                SpeechToTextEngine = None  # type: ignore[assignment,misc]

        if SpeechToTextEngine is not None:
            try:
                self._stt = SpeechToTextEngine()
                logger.info("SpeechToTextEngine ready")
            except Exception as exc:
                logger.error("Failed to create SpeechToTextEngine: %s", exc)

        # ── Executor ──────────────────────────────────────────────────
        try:
            from core.executor import Executor  # type: ignore[import]
        except ImportError:
            try:
                from executor import Executor  # type: ignore[import]
            except ImportError:
                logger.error("Could not import Executor")
                Executor = None  # type: ignore[assignment,misc]

        if Executor is not None:
            try:
                self._executor = Executor()
                logger.info("Executor ready")
            except Exception as exc:
                logger.error("Failed to create Executor: %s", exc)

        # ── VADService ────────────────────────────────────────────────
        try:
            from vad.vad_service import VADService  # type: ignore[import]
        except ImportError:
            try:
                from vad_service import VADService  # type: ignore[import]
            except ImportError:
                logger.error("Could not import VADService — voice capture disabled")
                VADService = None  # type: ignore[assignment,misc]

        if VADService is not None:
            cfg = {
                "node_ws_uri": self._node_ws_uri,
                **self._vad_cfg_override,
            }
            try:
                self._vad = VADService(cfg)
                logger.info("VADService ready")
            except Exception as exc:
                logger.error("Failed to create VADService: %s", exc)

        # ── Register in global registry so other components can resolve ──
        try:
            from services.service_registry import ServiceRegistry  # type: ignore[import]
        except ImportError:
            try:
                from service_registry import ServiceRegistry  # type: ignore[import]
            except ImportError:
                ServiceRegistry = None  # type: ignore[assignment]

        if ServiceRegistry is not None:
            reg = ServiceRegistry.instance()
            if self._stt is not None:
                reg.register("stt", self._stt)
            if self._executor is not None:
                reg.register("executor", self._executor)
            if self._vad is not None:
                reg.register("vad", self._vad)

    # ------------------------------------------------------------------
    # VAD wiring
    # ------------------------------------------------------------------

    def _run_vad(self) -> None:
        """
        Wire the on-transcript callback into VADService and start it.

        VADService.start() blocks its thread — we wrap it in a daemon
        thread here so VoiceRuntime.start() can remain non-blocking
        from the caller's perspective when needed.

        NOTE: VADService currently calls Google SR inline and emits
        INTENT_READY.  The _on_transcript callback below intercepts that
        same event and routes it through WhisperSTT → Rust intent engine
        → Executor → TTS, replacing the inline SR path once both are
        active.
        """
        if self._vad is None:
            logger.error("VADService not available — voice capture disabled")
            return

        # Patch the VADService _vad_loop to call our callback after audio
        # is captured.  We do this by monkey-patching _on_transcript into
        # the instance rather than modifying vad_service.py directly.
        self._vad._voice_runtime_callback = self._on_transcript  # type: ignore[attr-defined]

        logger.info("Starting VAD in background thread")
        self._vad_thread = threading.Thread(
            target=self._vad.start,
            daemon=True,
            name="pandora-vad",
        )
        self._vad_thread.start()

    # ------------------------------------------------------------------
    # Pipeline: transcript → intent → execute → speak
    # ------------------------------------------------------------------

    def _on_transcript(self, audio_path: str, raw_pcm: Optional[bytes] = None) -> None:
        """
        Called by VADService after a confirmed utterance is ready.

        Accepts either a WAV path (audio_path) or raw PCM bytes
        (raw_pcm) — matches both VADService calling conventions.

        This is the core pipeline glue:
            WAV → WhisperSTT → Rust intent → Executor → TTS
        """
        if not self._running:
            return

        self._set_state(PipelineState.PROCESSING)

        # ── STT ───────────────────────────────────────────────────────
        text = ""
        if self._stt is not None:
            try:
                if audio_path:
                    text = self._stt.transcribe_wav(audio_path)
                elif raw_pcm:
                    text = self._stt.transcribe_pcm(raw_pcm)
            except Exception as exc:
                logger.warning("STT transcription error: %s", exc)

        if not text:
            logger.debug("Empty transcript — returning to LISTENING")
            self._set_state(PipelineState.LISTENING)
            return

        logger.info("Transcript: %r", text)

        # ── Intent ────────────────────────────────────────────────────
        intent_result: dict = {}
        try:
            from ai.intent_bridge import parse_intent  # type: ignore[import]
            intent_result = parse_intent(text)
        except Exception as exc:
            logger.warning("Intent parse error: %s", exc)
            intent_result = {"intent": "unknown", "entities": {}, "confidence": 0.0}

        logger.info(
            "Intent: %s (confidence=%.2f)",
            intent_result.get("intent", "?"),
            intent_result.get("confidence", 0.0),
        )

        # ── Execute ───────────────────────────────────────────────────
        self._set_state(PipelineState.EXECUTING)
        response_text = ""
        if self._executor is not None:
            try:
                response_text = self._executor.execute(intent_result) or ""
            except Exception as exc:
                logger.warning("Executor error: %s", exc)
                response_text = "I encountered an error executing that."

        # ── TTS ───────────────────────────────────────────────────────
        if response_text:
            self._set_state(PipelineState.SPEAKING)
            try:
                from speech.tts import speak  # type: ignore[import]
                speak(response_text)
            except ImportError:
                try:
                    from tts import speak  # type: ignore[import]
                    speak(response_text)
                except ImportError:
                    logger.warning("TTS not available — response: %s", response_text)

        # ── Optional upstream callback (Node IPC / UI) ─────────────────
        if self._on_response:
            try:
                self._on_response(response_text, intent_result)
            except Exception as exc:
                logger.warning("on_response callback error: %s", exc)

        # ── Reset audio runtime state ──────────────────────────────────
        if self._stt is not None and hasattr(self._stt, "reset_audio_state"):
            self._stt.reset_audio_state()

        self._set_state(PipelineState.LISTENING)
