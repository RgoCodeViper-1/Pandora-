"""
services/whisper_manager.py
===========================
Infrastructure-only manager for the whisper.cpp HTTP server.

Responsibilities:
    - Probe whether the server is already listening
    - Launch whisper-server.exe if absent
    - Periodic health-check with automatic restart on crash
    - Clean shutdown

Knows nothing about VAD, STT, intents, or the conversation pipeline.
Import-safe: can be imported without whisper.cpp being installed.
"""
from __future__ import annotations

import logging
import subprocess
import threading
import time
from pathlib import Path
from typing import Optional

import requests

logger = logging.getLogger("pandora.whisper_manager")

# ── Paths (mirrors stt.py resolution) ────────────────────────────────────────
_HERE = Path(__file__).resolve()

# Resolve from services/ up to project root, then into native/whisper_cpp
# Works whether this file lives at  python/services/  or  services/
def _resolve_whisper_dir() -> Path:
    for parent in _HERE.parents:
        candidate = parent / "native" / "whisper_cpp"
        if candidate.is_dir():
            return candidate
    # Fallback: assume two levels up from this file
    return _HERE.parents[1] / "native" / "whisper_cpp"


WHISPER_DIR: Path = _resolve_whisper_dir()
WHISPER_SERVER_EXE: Path = WHISPER_DIR / "whisper-server.exe"

# Default endpoint — matches stt.py WHISPER_URL
_DEFAULT_HOST = "127.0.0.1"
_DEFAULT_PORT = 8080
_INFERENCE_PATH = "/inference"
_HEALTH_TIMEOUT = 3   # seconds per probe


class WhisperManager:
    """
    Lifecycle manager for the whisper.cpp server process.

    Usage::

        mgr = WhisperManager()
        ok  = mgr.ensure_running()   # called by VoiceRuntime at startup
        # ...
        mgr.shutdown()

    ``ensure_running()`` is idempotent — safe to call multiple times.
    """

    def __init__(
        self,
        host: str = _DEFAULT_HOST,
        port: int = _DEFAULT_PORT,
        model_path: Optional[Path] = None,
        threads: int = 4,
        health_interval: float = 15.0,
    ) -> None:
        self._host = host
        self._port = port
        self._base_url = f"http://{host}:{port}"
        self._inference_url = f"{self._base_url}{_INFERENCE_PATH}"
        self._model_path = model_path or (WHISPER_DIR / "models" / "ggml-base.en.bin")
        self._threads = threads
        self._health_interval = health_interval

        self._process: Optional[subprocess.Popen] = None  # type: ignore[type-arg]
        self._monitor_thread: Optional[threading.Thread] = None
        self._running = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def inference_url(self) -> str:
        """The full inference endpoint URL — handed to WhisperSTT in stt.py."""
        return self._inference_url

    def is_alive(self) -> bool:
        """Return True if the HTTP server responds within the timeout."""
        try:
            r = requests.get(f"{self._base_url}/", timeout=_HEALTH_TIMEOUT)
            return r.status_code < 500
        except requests.exceptions.ConnectionError:
            return False
        except Exception:
            return False

    def ensure_running(self) -> bool:
        """
        Make sure the whisper.cpp server is up.

        Order of operations:
        1. Probe existing server → return True immediately if already alive.
        2. Check that the executable exists on disk.
        3. Launch the subprocess.
        4. Wait up to 10 s for the server to accept connections.
        5. Start background health-monitor thread.

        Returns True on success, False if the server cannot be started
        (e.g. binary missing).  Caller decides whether to abort or fall
        back to Google STT.
        """
        if self.is_alive():
            logger.info("whisper.cpp server already running at %s", self._base_url)
            self._start_monitor()
            return True

        if not WHISPER_SERVER_EXE.exists():
            logger.warning(
                "whisper.cpp binary not found at %s — Whisper STT unavailable",
                WHISPER_SERVER_EXE,
            )
            return False

        logger.info("Starting whisper.cpp server…")
        self._launch()

        # Wait up to 10 s
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            if self.is_alive():
                logger.info(
                    "whisper.cpp server ready at %s (port %d)",
                    self._base_url, self._port,
                )
                self._start_monitor()
                return True
            time.sleep(0.5)

        logger.error("whisper.cpp server did not become ready within 10 s")
        return False

    def shutdown(self) -> None:
        """Terminate the managed subprocess and stop the monitor."""
        self._running = False
        if self._process is not None:
            try:
                self._process.terminate()
                self._process.wait(timeout=5)
                logger.info("whisper.cpp server terminated")
            except Exception as exc:
                logger.warning("Error shutting down whisper server: %s", exc)
            finally:
                self._process = None

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _launch(self) -> None:
        """Spawn the whisper-server subprocess."""
        cmd = [
            str(WHISPER_SERVER_EXE),
            "--model",   str(self._model_path),
            "--host",    self._host,
            "--port",    str(self._port),
            "--threads", str(self._threads),
        ]
        try:
            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                cwd=str(WHISPER_DIR),
            )
            logger.debug("whisper-server PID %d", self._process.pid)
        except Exception as exc:
            logger.error("Failed to launch whisper-server: %s", exc)
            self._process = None

    def _start_monitor(self) -> None:
        """Start background health-check thread (idempotent)."""
        if self._running:
            return
        self._running = True
        t = threading.Thread(
            target=self._monitor_loop,
            daemon=True,
            name="pandora-whisper-monitor",
        )
        t.start()
        self._monitor_thread = t

    def _monitor_loop(self) -> None:
        """Periodically probe the server; restart on crash."""
        while self._running:
            time.sleep(self._health_interval)
            if not self._running:
                break
            if not self.is_alive():
                logger.warning("whisper.cpp server unresponsive — restarting…")
                self._launch()
                # Give it a moment before next probe
                time.sleep(3.0)
