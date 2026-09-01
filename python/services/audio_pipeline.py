"""
python/services/audio_pipeline.py
==================================
Canonical entrypoint for the Pandora audio pipeline.

This is the ONE file that should be run to exercise the mic → VAD →
STT → intent chain during development, replacing the old implicit
dependency where vad_service.py gated mic-open behind a live Node
WebSocket connection (_connect_and_run → _vad_loop).

Wires together:
    whisper_runtime2.run()             — mic capture + conversational
                                          heuristic runtime (ConversationalRuntime /
                                          ConversationStateEngine) + WAV finalize
    whisper_runtime2._transcribe()     — patched in-process to always hit the
                                          reconciled Whisper endpoint (port 8080,
                                          matches whisper_manager.py + stt.py)
    _NodeBridge                        — OPTIONAL, non-blocking WS connection
                                          to the Node orchestrator. Node is now
                                          purely additive: absent/unreachable
                                          Node never blocks or breaks the pipeline.
    ai.intent_bridge.parse_intent{_stream} — intent parsing after transcript

Port reconciliation
--------------------
whisper_manager.py launches whisper.cpp on 127.0.0.1:8080 by default, and
whisper_runtime2.py already posts to that port. stt.py's WhisperSTT was
previously pointed at 8177 (fixed alongside this file — see stt.py). This
module additionally hard-pins WHISPER_INFERENCE_URL so nothing downstream
can silently drift back to a stale port.

CLI modes
---------
    python audio_pipeline.py
        Normal operation: opens the mic via whisper_runtime2.run(), tries
        to connect to the Node WS bridge in the background (non-blocking),
        emits INTENT_READY events to Node when connected.

    python audio_pipeline.py --no-ws
        Same mic loop, but the Node bridge is never started. Fully
        standalone — useful for testing the audio/STT/intent chain without
        Node running at all.

    python audio_pipeline.py --text "open chrome"
        Skips audio and Whisper entirely. Runs the given string straight
        through parse_intent_stream/parse_intent and prints the result.
        Useful for testing intent patterns without a mic or Whisper server.

    python audio_pipeline.py --wav path/to/utterance.wav
        No mic. Transcribes the given WAV via the patched _transcribe(),
        then parses intent. Node bridge still optional (respects --no-ws).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import threading
from typing import Optional

logger = logging.getLogger("pandora.audio_pipeline")

# ─────────────────────────────────────────────────────────────────────────────
# Reconciled Whisper endpoint — single source of truth for this module
# ─────────────────────────────────────────────────────────────────────────────
WHISPER_INFERENCE_URL = "http://127.0.0.1:8080/inference"

import whisper_runtime2  # noqa: E402  (import after logger/config setup is intentional)


# ─────────────────────────────────────────────────────────────────────────────
# In-process patch of whisper_runtime2._transcribe
# ─────────────────────────────────────────────────────────────────────────────
# whisper_runtime2._transcribe() already reads a module-level WHISPER_URL
# constant, but we patch the *function* here (not just the constant) so this
# file stays the single owner of the reconciled endpoint plus any future
# retry/backoff behaviour, without needing another edit to whisper_runtime2.py.

_ORIGINAL_TRANSCRIBE = whisper_runtime2._transcribe


def _patched_transcribe(audio_path: str) -> str:
    """
    Drop-in replacement for whisper_runtime2._transcribe().

    Forces WHISPER_INFERENCE_URL rather than trusting whatever
    whisper_runtime2.WHISPER_URL currently holds, so a stale import
    elsewhere in the process can't silently point at the old port.
    """
    try:
        import requests
        with open(audio_path, "rb") as f:
            resp = requests.post(WHISPER_INFERENCE_URL, files={"file": f}, timeout=60)
        if resp.status_code != 200:
            logger.warning("[audio_pipeline] Whisper HTTP %s", resp.status_code)
            return ""
        return resp.json().get("text", "").strip()
    except requests.exceptions.ConnectionError:
        logger.warning("[audio_pipeline] Whisper server unreachable at %s", WHISPER_INFERENCE_URL)
        return ""
    except Exception as exc:
        logger.warning("[audio_pipeline] transcription failed: %s", exc)
        return ""


# Patch both the function reference the module's run() loop actually calls
# and the constant, so any code path reading either stays consistent.
whisper_runtime2._transcribe = _patched_transcribe
whisper_runtime2.WHISPER_URL = WHISPER_INFERENCE_URL


# ─────────────────────────────────────────────────────────────────────────────
# _NodeBridge — optional, non-blocking WebSocket connection to Node
# ─────────────────────────────────────────────────────────────────────────────

class _NodeBridge:
    """
    Best-effort, non-blocking bridge to the Node orchestrator's WebSocket
    server (default ws://localhost:8765).

    This directly fixes the vad_service.py problem where mic-open was
    gated behind `_connect_and_run()` completing a WS handshake first.
    Here:

      - The bridge runs its own asyncio event loop on a daemon thread —
        it never blocks the caller (the mic/VAD loop keeps running whether
        or not Node is reachable).
      - Connection failures are swallowed and retried with exponential
        backoff, capped at 30s, mirroring the reconnect logic already used
        in vad_service.py and llm_client.py.
      - emit() is a plain synchronous call, safe to invoke from any thread
        (including the mic capture thread) — it hands the payload to the
        bridge's own loop via call_soon_threadsafe/run_coroutine_threadsafe.
      - When disabled (--no-ws) or not yet connected, emit() is a silent
        no-op. The pipeline never errors or stalls because Node is absent.
    """

    def __init__(self, uri: str = "ws://localhost:8765", enabled: bool = True) -> None:
        self._uri = uri
        self._enabled = enabled
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._ws = None
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._connected = threading.Event()

    # ── public, thread-safe API ────────────────────────────────────────────

    def start(self) -> None:
        if not self._enabled:
            logger.info("[NodeBridge] disabled (--no-ws) — running standalone")
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._run_loop, daemon=True, name="pandora-node-bridge"
        )
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._loop is not None and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)

    def emit(self, payload: dict) -> None:
        """
        Fire-and-forget emit, safe to call from any thread. No-op when
        Node is disabled or not currently connected.
        """
        if not self._enabled or self._loop is None or not self._connected.is_set():
            return
        try:
            asyncio.run_coroutine_threadsafe(self._send(payload), self._loop)
        except Exception as exc:
            logger.debug("[NodeBridge] emit failed: %s", exc)

    @property
    def connected(self) -> bool:
        return self._connected.is_set()

    @property
    def enabled(self) -> bool:
        return self._enabled

    # ── internal ────────────────────────────────────────────────────────────

    def _run_loop(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._connect_and_run())
        finally:
            self._loop.close()

    async def _connect_and_run(self) -> None:
        import websockets

        reconnect_delay = 2
        while self._running:
            try:
                async with websockets.connect(self._uri) as ws:
                    self._ws = ws
                    self._connected.set()
                    logger.info("[NodeBridge] connected to %s", self._uri)
                    reconnect_delay = 2
                    while self._running:
                        await asyncio.sleep(0.5)
            except (OSError, Exception) as exc:
                self._connected.clear()
                self._ws = None
                logger.debug(
                    "[NodeBridge] connection unavailable: %s — retry in %ds",
                    exc, reconnect_delay,
                )
                await asyncio.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 2, 30)

    async def _send(self, payload: dict) -> None:
        if self._ws is not None:
            try:
                await self._ws.send(json.dumps(payload))
            except Exception as exc:
                logger.debug("[NodeBridge] send failed: %s", exc)
                self._connected.clear()


# ─────────────────────────────────────────────────────────────────────────────
# Intent dispatch — shared by all three CLI modes
# ─────────────────────────────────────────────────────────────────────────────

def _process_transcript(text: str, bridge: Optional[_NodeBridge]) -> dict:
    """Run parse_intent_stream → parse_intent fallback, emit to Node if connected."""
    from ai.intent_bridge import parse_intent, parse_intent_stream

    if not text:
        result = {"intent": "unknown", "entities": {}, "confidence": 0.0}
    else:
        result = parse_intent_stream(text) or parse_intent(text)

    logger.info(
        "[audio_pipeline] intent=%s confidence=%.2f",
        result.get("intent"), result.get("confidence", 0.0),
    )

    if bridge is not None:
        bridge.emit({
            "event": "INTENT_READY",
            "text": text,
            "intent": result,
        })

    return result


# ─────────────────────────────────────────────────────────────────────────────
# CLI modes
# ─────────────────────────────────────────────────────────────────────────────

def _run_text_mode(text: str) -> None:
    """--text "..." — skip audio entirely, run one utterance through intent parsing."""
    logger.info("[audio_pipeline] --text mode: %r", text)
    result = _process_transcript(text, bridge=None)
    print(json.dumps(result, indent=2))


def _run_wav_mode(wav_path: str, bridge: Optional[_NodeBridge]) -> None:
    """--wav path — transcribe an existing WAV file, no mic, then parse intent."""
    logger.info("[audio_pipeline] --wav mode: %s", wav_path)
    text = whisper_runtime2._transcribe(wav_path)
    logger.info("[audio_pipeline] transcript: %r", text)
    result = _process_transcript(text, bridge)
    print(json.dumps(result, indent=2))


def _run_mic_mode(bridge: Optional[_NodeBridge]) -> None:
    """
    Normal operation — mic capture via whisper_runtime2.run(). The Node
    bridge (if enabled) connects in the background and never blocks this
    loop from starting, unlike the old vad_service.py gating behaviour.
    """
    def _on_transcript(text: str) -> None:
        _process_transcript(text, bridge)

    logger.info(
        "[audio_pipeline] starting mic loop (Node bridge %s)",
        "enabled" if bridge is not None and bridge.enabled else "disabled",
    )
    whisper_runtime2.run(on_transcript=_on_transcript)


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Pandora canonical audio pipeline entrypoint")
    parser.add_argument(
        "--text", type=str, default=None,
        help="Skip audio entirely; run this text through intent parsing.",
    )
    parser.add_argument(
        "--wav", type=str, default=None,
        help="Transcribe this WAV file instead of opening the mic.",
    )
    parser.add_argument(
        "--no-ws", action="store_true",
        help="Disable the Node WebSocket bridge entirely (fully standalone).",
    )
    parser.add_argument(
        "--node-uri", type=str, default="ws://localhost:8765",
        help="Node orchestrator WebSocket URI (default: ws://localhost:8765).",
    )
    parser.add_argument(
        "--log", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log.upper(), logging.INFO),
        format="%(asctime)s [%(name)s] %(levelname)s — %(message)s",
    )

    # --text mode never needs a bridge, Whisper, or the mic stack.
    if args.text is not None:
        _run_text_mode(args.text)
        return

    bridge = _NodeBridge(uri=args.node_uri, enabled=not args.no_ws)
    bridge.start()

    try:
        if args.wav is not None:
            _run_wav_mode(args.wav, bridge)
        else:
            _run_mic_mode(bridge)
    except KeyboardInterrupt:
        logger.info("[audio_pipeline] interrupted — shutting down")
    finally:
        bridge.stop()


if __name__ == "__main__":
    main()
