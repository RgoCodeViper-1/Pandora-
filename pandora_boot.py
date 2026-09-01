"""
pandora_boot.py
===============
Pandora bootstrap — single orchestration entrypoint.

This is the ONLY file that should be called to start the speech pipeline.
Nothing else touches lifecycle directly.

Boot sequence:
    1. Load config
    2. WhisperManager.ensure_running()     — infrastructure gate
    3. Register WhisperManager in ServiceRegistry
    4. Instantiate VoiceRuntime (no heavy imports yet)
    5. Register VoiceRuntime in ServiceRegistry
    6. Start WakewordListener in background thread
    7. Start VoiceRuntime in background thread
    8. Block main thread on a clean shutdown signal

After boot, the flow is fully event-driven:

    WakewordListener detects "pandora"
        → VoiceRuntime.activate()
            → VAD opens mic
            → Speech captured
            → WhisperSTT transcribes
            → Rust intent engine parses
            → Executor dispatches action
            → TTS speaks response
        → VoiceRuntime signals IDLE
    WakewordListener resumes

Usage::

    python pandora_boot.py
    python pandora_boot.py --no-wakeword    # skip idle gating, always listening
    python pandora_boot.py --log DEBUG
"""
from __future__ import annotations

import argparse
import logging
import signal
import sys
import threading
import time

logger = logging.getLogger("pandora.boot")


# ─────────────────────────────────────────────────────────────────────────────
# Config loader
# ─────────────────────────────────────────────────────────────────────────────

def _load_config(path: str = "shared/config/configuration.json") -> dict:
    import json
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        logger.warning("Config file not found (%s) — using defaults", exc)
        return {}


def _flatten_audio_cfg(full: dict) -> dict:
    """Extract audio/vad section into a flat dict for VADService."""
    audio = full.get("audio", {})
    vad   = audio.get("vad", {})
    return {
        "sample_rate":                 audio.get("sampleRate",          16000),
        "vad_aggressiveness":          vad.get("aggressiveness",        2),
        "vad_silence_duration":        vad.get("silenceThresholdMs",    1800) / 1000,
        "silero_confidence_threshold": vad.get("sileroConfidenceThreshold", 0.5),
        "use_silero":                  vad.get("useSilero",             True),
        "node_ws_uri":                 full.get("node", {}).get("wsUri", "ws://localhost:8765"),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Bootstrap
# ─────────────────────────────────────────────────────────────────────────────

def boot(
    config_path: str = "shared/config/configuration.json",
    use_wakeword: bool = True,
    log_level: str = "INFO",
) -> None:
    """
    Start the full Pandora speech pipeline.

    Blocks until SIGINT / SIGTERM or KeyboardInterrupt.
    """
    # ── Logging ──────────────────────────────────────────────────────
    logging.basicConfig(
        level=getattr(logging, log_level.upper(), logging.INFO),
        format="%(asctime)s [%(name)s] %(levelname)s — %(message)s",
        stream=sys.stdout,
    )

    logger.info("═══ Pandora Boot Sequence ═══")

    # ── Config ───────────────────────────────────────────────────────
    full_cfg  = _load_config(config_path)
    audio_cfg = _flatten_audio_cfg(full_cfg)
    node_ws   = audio_cfg.get("node_ws_uri", "ws://localhost:8765")

    # ── ServiceRegistry ──────────────────────────────────────────────
    try:
        from services.service_registry import ServiceRegistry  # type: ignore[import]
    except ImportError:
        from service_registry import ServiceRegistry  # type: ignore[import]

    registry = ServiceRegistry.instance()

    # ── Step 1: WhisperManager ────────────────────────────────────────
    try:
        from services.whisper_manager import WhisperManager  # type: ignore[import]
    except ImportError:
        from whisper_manager import WhisperManager  # type: ignore[import]

    whisper_mgr = WhisperManager()
    whisper_ok  = whisper_mgr.ensure_running()

    registry.register("whisper_manager", whisper_mgr)

    if not whisper_ok:
        logger.warning(
            "Whisper server unavailable — STT will fall back to Google SR"
        )

    whisper_url = whisper_mgr.inference_url

    # ── Step 2: VoiceRuntime ──────────────────────────────────────────
    try:
        from services.voice_runtime import VoiceRuntime, PipelineState  # type: ignore[import]
    except ImportError:
        from voice_runtime import VoiceRuntime, PipelineState  # type: ignore[import]

    _shutdown_event = threading.Event()

    def _on_state(state: PipelineState) -> None:
        """Forward state transitions to the Node WS bridge (future hook)."""
        logger.debug("Pipeline state: %s", state.name)
        if state == PipelineState.STOPPED:
            _shutdown_event.set()

    def _on_response(text: str, intent: dict) -> None:
        logger.debug("Response emitted: %r  intent=%s", text, intent.get("intent"))

    runtime = VoiceRuntime(
        whisper_url      = whisper_url,
        on_state_change  = _on_state,
        on_response      = _on_response,
        node_ws_uri      = node_ws,
        vad_cfg          = audio_cfg,
    )

    registry.register("voice_runtime", runtime)

    # ── Step 3: WakewordListener ──────────────────────────────────────
    wakeword_listener = None

    if use_wakeword:
        try:
            from services.wakeword_listener import WakewordListener  # type: ignore[import]
        except ImportError:
            from wakeword_listener import WakewordListener  # type: ignore[import]

        def _on_wakeword(text: str) -> None:
            """Gate: wakeword heard → activate VoiceRuntime for one session."""
            logger.info("Wakeword gate: activating pipeline for session")
            # Mic is now open inside VoiceRuntime — listener backs off
            # until the runtime signals IDLE again
            try:
                runtime.start()
            finally:
                if wakeword_listener is not None:
                    wakeword_listener.resume()

        wakeword_listener = WakewordListener(on_wakeword=_on_wakeword)
        registry.register("wakeword_listener", wakeword_listener)
        wakeword_listener.start()
        logger.info("WakewordListener active — say 'Pandora' to begin")

    else:
        # No wakeword gating — start the full pipeline immediately
        logger.info("Wakeword gating disabled — starting pipeline immediately")
        runtime_thread = threading.Thread(
            target=runtime.start,
            daemon=True,
            name="pandora-voice-runtime",
        )
        runtime_thread.start()

    # ── Step 4: Shutdown handling ─────────────────────────────────────
    def _handle_signal(sig, frame) -> None:  # type: ignore[no-untyped-def]
        logger.info("Signal %s received — shutting down", sig)
        _shutdown_event.set()

    signal.signal(signal.SIGINT,  _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    logger.info("Pandora running  (Ctrl-C to stop)")

    try:
        while not _shutdown_event.is_set():
            _shutdown_event.wait(timeout=1.0)
    except KeyboardInterrupt:
        pass
    finally:
        logger.info("Pandora shutting down…")
        runtime.stop()
        if wakeword_listener is not None:
            wakeword_listener.stop()
        whisper_mgr.shutdown()
        logger.info("Pandora stopped cleanly")


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Pandora voice assistant bootstrap")
    p.add_argument(
        "--config",
        default="shared/config/configuration.json",
        help="Path to configuration.json",
    )
    p.add_argument(
        "--no-wakeword",
        action="store_true",
        help="Skip idle wakeword gating — pipeline always listening",
    )
    p.add_argument(
        "--log",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Log level",
    )
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    boot(
        config_path  = args.config,
        use_wakeword = not args.no_wakeword,
        log_level    = args.log,
    )
