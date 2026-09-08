"""
api_server.py  —  Pandora WebSocket API server
================================================
Single broadcast hub that:
  1. Accepts WebSocket connections from Node CLI (pandora-ipc.ts).
  2. Provides `emit(event, payload)` used by every Python service.
  3. Routes inbound USER_TEXT messages through:
       rust_core.process() → executor → TOKEN/RESPONSE_DONE events
  4. Handles STATUS and SHUTDOWN commands from Node.

Protocol (matches pandora-ipc.ts EVENT_TO_STATE map):
  OUT → { "event": "EVENT_NAME", "payload": {...} }
  IN  ← { "type": "TYPE",        "data":    {...} }

Inbound types handled:
  USER_TEXT   – process text through intent engine
  STATUS      – reply with current state
  SHUTDOWN    – begin graceful shutdown

Startup events emitted automatically:
  BOOTING → BOOT_COMPLETE → IDLE
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import sys
from pathlib import Path
from typing import Any

import websockets
from websockets.server import WebSocketServerProtocol

# ── Optional rust_core ────────────────────────────────────────────────────────
try:
    import pandora_core as rust_core          # type: ignore
    RUST_AVAILABLE = True
except ImportError:
    rust_core      = None     # type: ignore
    RUST_AVAILABLE = False

# ── Project imports ───────────────────────────────────────────────────────────
# Allow running from python/ directly or from project root.
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from core.executor import Executor
    _executor = Executor()
    EXECUTOR_AVAILABLE = True
except Exception as _exc:
    _executor          = None    # type: ignore
    EXECUTOR_AVAILABLE = False
    logging.getLogger("pandora.api").warning("Executor unavailable: %s", _exc)

# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────

HOST = os.environ.get("PANDORA_WS_HOST", "localhost")
PORT = int(os.environ.get("PANDORA_WS_PORT", "8765"))

logger = logging.getLogger("pandora.api")

# ─────────────────────────────────────────────────────────────────────────────
# PandoraAPI
# ─────────────────────────────────────────────────────────────────────────────

class PandoraAPI:
    """
    Central event emitter + WebSocket server.

    Usage (from any Python service):
        api = PandoraAPI.instance()
        await api.emit("VAD_START")
        await api.emit("INTENT_READY", {"intent": "shutdown", "confidence": 0.97})
    """

    _instance: "PandoraAPI | None" = None

    def __init__(self) -> None:
        self._clients:  set[WebSocketServerProtocol] = set()
        self._state:    str = "BOOTING"
        self._server    = None
        self._shutdown_event = asyncio.Event()

    # ── Singleton ─────────────────────────────────────────────────────────────

    @classmethod
    def instance(cls) -> "PandoraAPI":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ── Core emitter ──────────────────────────────────────────────────────────

    async def emit(self, event: str, payload: dict[str, Any] | None = None) -> None:
        """
        Broadcast a structured event to every connected Node client.

        Matches exactly what pandora-ipc.ts expects:
          { "event": "EVENT_NAME", "payload": {} }
        """
        msg = json.dumps({"event": event, "payload": payload or {}})
        if self._clients:
            await asyncio.gather(
                *[self._safe_send(ws, msg) for ws in self._clients],
                return_exceptions=True,
            )
        # Track state for STATUS replies
        if event in (
            "BOOTING", "BOOT_COMPLETE", "IDLE", "WAKEWORD_DETECTED",
            "VAD_START", "STT_DONE", "INTENT_READY", "EXECUTOR_START",
            "TOOL_START", "TTS_START", "TTS_DONE", "SLEEPING", "SHUTDOWN",
        ):
            self._state = event
        logger.debug("emit → %s %s", event, payload or "")

    @staticmethod
    async def _safe_send(ws: WebSocketServerProtocol, msg: str) -> None:
        try:
            await ws.send(msg)
        except Exception:
            pass  # client disconnected — handled by _handler cleanup

    # ── Broadcast helper (sync-friendly wrapper) ──────────────────────────────

    def emit_sync(self, event: str, payload: dict[str, Any] | None = None) -> None:
        """
        Fire-and-forget emit from synchronous code (e.g. VAD callback thread).
        Safe to call from any thread.
        """
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.run_coroutine_threadsafe(self.emit(event, payload), loop)

    # ── USER_TEXT pipeline ────────────────────────────────────────────────────

    async def _handle_user_text(self, text: str) -> None:
        """
        typed input → USER_TEXT → rust_core.process() → executor → RESPONSE_DONE

        This path is shared by both:
          - Voice (VAD → STT transcription forwarded here)
          - Text  (CLI --text flag or REPL typed input)

        Events emitted (match pandora-ipc.ts EVENT_TO_STATE):
          INTENT_READY  → state: PROCESSING
          EXECUTOR_START→ state: EXECUTING
          TOKEN         → streaming partial response text
          RESPONSE_DONE → state: IDLE  (via TTS_DONE mapping)
        """
        if not text.strip():
            return

        logger.info("user_text: %r", text)

        # ── 1. Intent classification via Rust core ────────────────────────────
        await self.emit("INTENT_READY", {"text": text})

        intent_result: dict[str, Any] = {"intent": "unknown", "entities": {}, "confidence": 0.0}

        if RUST_AVAILABLE and rust_core is not None:
            try:
                raw    = rust_core.process_stream(text)
                parsed = json.loads(raw)
                # Normalise: streaming path returns single intent or batch
                if "intent" in parsed:
                    intent_result = parsed
                elif "intents" in parsed and parsed["intents"]:
                    intent_result = parsed["intents"][0]
            except Exception as exc:
                logger.warning("rust_core failed: %s", exc)

        await self.emit("INTENT_READY", {
            "text":       text,
            "intent":     intent_result.get("intent", "unknown"),
            "confidence": intent_result.get("confidence", 0.0),
            "entities":   intent_result.get("entities", {}),
        })

        # ── 2. Executor dispatch ───────────────────────────────────────────────
        await self.emit("EXECUTOR_START", {"intent": intent_result.get("intent")})

        response_text = ""
        if EXECUTOR_AVAILABLE and _executor is not None:
            try:
                response_text = _executor.execute(intent_result) or ""
            except Exception as exc:
                logger.warning("executor failed: %s", exc)
                response_text = f"I couldn't complete that. ({exc})"
        else:
            # No executor — echo back intent for debugging
            response_text = (
                f"Intent classified: {intent_result.get('intent', 'unknown')} "
                f"({intent_result.get('confidence', 0):.0%} confidence)"
            )

        # ── 3. Stream response tokens to Node ─────────────────────────────────
        # For now we emit the whole response as one TOKEN then RESPONSE_DONE.
        # Replace this loop with real streaming once TTS/LLM streaming lands.
        if response_text:
            # Chunked emit so the UI shimmer animates naturally
            chunk_size = 8
            words = response_text.split()
            for i in range(0, len(words), chunk_size):
                chunk = " ".join(words[i:i + chunk_size])
                if i + chunk_size < len(words):
                    chunk += " "
                await self.emit("TOKEN", {"text": chunk})
                await asyncio.sleep(0.01)  # yield to event loop

        await self.emit("RESPONSE_DONE", {"text": response_text})

        # TTS_DONE → IDLE transition (mirrors pandora-ipc.ts EVENT_TO_STATE)
        await self.emit("TTS_DONE")

    # ── Inbound message router ────────────────────────────────────────────────

    async def _route(self, raw: str) -> None:
        """
        Route inbound messages from Node.

        Expected shape (from pandora-ipc.ts sendText / sendCommand):
          { "type": "USER_TEXT",  "data": {"text": "..."} }
          { "type": "STATUS",     "data": {} }
          { "type": "SHUTDOWN",   "data": {} }
        """
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return

        msg_type: str      = msg.get("type", "")
        data:     dict     = msg.get("data", {})

        if msg_type == "USER_TEXT":
            text = data.get("text", "").strip()
            if text:
                asyncio.create_task(self._handle_user_text(text))

        elif msg_type == "STATUS":
            await self.emit("STATUS", {"state": self._state, "clients": len(self._clients)})

        elif msg_type == "SHUTDOWN":
            logger.info("SHUTDOWN command received from Node")
            await self.emit("SHUTDOWN")
            self._shutdown_event.set()

    # ── WebSocket handler ─────────────────────────────────────────────────────

    async def _handler(self, ws: WebSocketServerProtocol) -> None:
        self._clients.add(ws)
        logger.info("client connected  (total=%d)", len(self._clients))

        # Immediately tell the new client what state we're in
        await self._safe_send(
            ws,
            json.dumps({"event": self._state, "payload": {}}),
        )

        try:
            async for raw in ws:
                await self._route(str(raw))
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            self._clients.discard(ws)
            logger.info("client disconnected (total=%d)", len(self._clients))

    # ── Server lifecycle ──────────────────────────────────────────────────────

    async def start(self) -> None:
        """
        Start the WebSocket server.  Called by launcher.py before services boot.
        Blocks until shutdown is signalled.
        """
        self._server = await websockets.serve(self._handler, HOST, PORT)
        logger.info("WebSocket server listening on ws://%s:%d", HOST, PORT)

        # Emit startup sequence
        await self.emit("BOOTING")

        # Wait for external shutdown signal
        await self._shutdown_event.wait()

        logger.info("shutting down WebSocket server…")
        self._server.close()
        await self._server.wait_closed()

    async def notify_boot_complete(self) -> None:
        """Call from launcher after all services are ready."""
        await self.emit("BOOT_COMPLETE")
        await self.emit("IDLE")


# ─────────────────────────────────────────────────────────────────────────────
# Convenience module-level helpers used by other services
# ─────────────────────────────────────────────────────────────────────────────

def get_api() -> PandoraAPI:
    """Return the singleton PandoraAPI (creates if first call)."""
    return PandoraAPI.instance()


async def emit(event: str, payload: dict[str, Any] | None = None) -> None:
    """Module-level shortcut: `from services.api_server import emit`"""
    await PandoraAPI.instance().emit(event, payload)


# ─────────────────────────────────────────────────────────────────────────────
# Standalone entry-point (python api_server.py — for testing)
# ─────────────────────────────────────────────────────────────────────────────

async def _standalone() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s  %(message)s",
    )
    api = PandoraAPI.instance()

    def _sigterm(*_):
        api._shutdown_event.set()

    signal.signal(signal.SIGTERM, _sigterm)
    signal.signal(signal.SIGINT,  _sigterm)

    await api.start()


if __name__ == "__main__":
    asyncio.run(_standalone())
