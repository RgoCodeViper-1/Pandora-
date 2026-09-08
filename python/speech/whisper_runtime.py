from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
from enum import Enum
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("pandora.whisper_runtime")


class RuntimeMode(str, Enum):
    COMMAND = "command"
    CONVERSATION = "conversation"
    FOCUSED = "focused"
    OPTIMISED_IDLE = "optimised_idle"


class WhisperRuntimePolicy:
    """Small policy layer to resolve which backend should be used.

    The broader project already separates mode selection from execution. This
    runtime simply matches the intended architecture:

    - command / focused / idle: prefer fast local or lightweight STT
    - conversation: prefer Whisper.cpp
    - fallback: use the existing SpeechToTextEngine if Whisper is unavailable
    """

    def __init__(self, mode: str = RuntimeMode.CONVERSATION.value) -> None:
        self.mode = mode.lower()

    @property
    def prefers_whisper(self) -> bool:
        return self.mode in {RuntimeMode.CONVERSATION.value, RuntimeMode.FOCUSED.value}

    @property
    def prefers_lightweight(self) -> bool:
        return self.mode in {RuntimeMode.COMMAND.value, RuntimeMode.OPTIMISED_IDLE.value}

    @staticmethod
    def from_name(mode: Optional[str]) -> "WhisperRuntimePolicy":
        if not mode:
            return WhisperRuntimePolicy(RuntimeMode.CONVERSATION.value)
        return WhisperRuntimePolicy(str(mode).lower())


class WhisperRuntime:
    """Production entry point for the Whisper-backed audio pipeline.

    The runtime is intentionally split into two responsibilities:
    1. infrastructure: health and transcription via WhisperClient
    2. orchestration: decision-making about when to route by mode

    It remains compatible with the existing SpeechToTextEngine and PandoraAPI.
    """

    def __init__(
        self,
        inference_url: Optional[str] = None,
        *,
        mode: str = RuntimeMode.CONVERSATION.value,
        client: Optional[Any] = None,
        auto_ensure_server: bool = True,
    ) -> None:
        self.mode = mode.lower()
        self.policy = WhisperRuntimePolicy(self.mode)
        self.inference_url = inference_url or self._resolve_inference_url()
        self.client = client
        self.auto_ensure_server = auto_ensure_server
        self._lock = threading.RLock()
        self._server_ready = False

    @property
    def mode_name(self) -> str:
        return self.mode

    def _build_client(self) -> Any:
        if self.client is not None:
            return self.client

        from speech.whisper_client import WhisperClient

        return WhisperClient(inference_url=self.inference_url)

    def _resolve_inference_url(self) -> Optional[str]:
        if os.getenv("PANDORA_WHISPER_URL"):
            return os.getenv("PANDORA_WHISPER_URL")

        host = os.getenv("PANDORA_WHISPER_HOST", "127.0.0.1")
        port = os.getenv("PANDORA_WHISPER_PORT", "8080")
        path = os.getenv("PANDORA_WHISPER_PATH", "/inference")
        return f"http://{host}:{port}{path}"

    def ensure_ready(self, *, allow_google_fallback: bool = True) -> bool:
        """Ensure the server is healthy or fall back gracefully."""
        client = self._build_client()
        if client.healthcheck():
            with self._lock:
                self._server_ready = True
            return True

        if self.auto_ensure_server:
            try:
                from services.whisper_manager import WhisperManager

                manager = WhisperManager()
                started = manager.ensure_running()
                if started:
                    self.client = WhisperClient(inference_url=manager.inference_url)
                    with self._lock:
                        self._server_ready = True
                    return True
            except Exception as exc:  # pragma: no cover - runtime environment dependent
                logger.warning("WhisperManager startup failed: %s", exc)

        if allow_google_fallback:
            logger.info("Whisper unavailable; runtime will fall back to SpeechToTextEngine")
        with self._lock:
            self._server_ready = False
        return False

    def transcribe_file(self, audio_path: str | Path, *, mode: Optional[str] = None) -> str:
        chosen_mode = (mode or self.mode).lower()
        if self.auto_ensure_server:
            self.ensure_ready(allow_google_fallback=True)

        client = self._build_client()
        if client.healthcheck():
            return client.transcribe_wav(str(audio_path))

        return self._fallback_transcribe_file(str(audio_path), mode=chosen_mode)

    def transcribe_pcm(self, pcm_bytes: bytes, sample_rate: int = 16000, *, mode: Optional[str] = None) -> str:
        chosen_mode = (mode or self.mode).lower()
        if self.auto_ensure_server:
            self.ensure_ready(allow_google_fallback=True)

        client = self._build_client()
        if client.healthcheck():
            return client.transcribe_pcm(pcm_bytes, sample_rate=sample_rate)

        return self._fallback_transcribe_pcm(pcm_bytes, sample_rate=sample_rate, mode=chosen_mode)

    def transcribe_wav(self, wav_path: str | Path, *, mode: Optional[str] = None) -> str:
        return self.transcribe_file(wav_path, mode=mode)

    def _fallback_transcribe_file(self, audio_path: str, *, mode: str) -> str:
        try:
            from speech.stt import SpeechToTextEngine

            engine = SpeechToTextEngine()
            return engine.transcribe_wav(audio_path)
        except Exception as exc:
            logger.warning("SpeechToTextEngine fallback failed for file %s: %s", audio_path, exc)
            return ""

    def _fallback_transcribe_pcm(self, pcm_bytes: bytes, sample_rate: int = 16000, *, mode: str) -> str:
        try:
            from speech.stt import SpeechToTextEngine

            engine = SpeechToTextEngine()
            return engine.transcribe_pcm(pcm_bytes, sample_rate=sample_rate)
        except Exception as exc:
            logger.warning("SpeechToTextEngine fallback failed for PCM stream: %s", exc)
            return ""

    def process_utterance(
        self,
        audio_path: str | Path,
        *,
        mode: Optional[str] = None,
        emit_api: bool = False,
        api_payload: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Return a normalised transcript result for a completed utterance."""
        transcript = self.transcribe_file(audio_path, mode=mode)
        result = {
            "text": transcript.strip(),
            "mode": (mode or self.mode).lower(),
            "backend": "whisper" if self._server_ready else "fallback",
            "confidence": 0.0,
        }

        if emit_api:
            self._notify_api(result, api_payload=api_payload)
        return result

    def _notify_api(self, result: dict[str, Any], *, api_payload: Optional[dict[str, Any]] = None) -> None:
        try:
            from services.api_server import PandoraAPI

            payload = api_payload or {}
            payload = {**payload, **result}
            loop = asyncio.get_running_loop()
            loop.create_task(PandoraAPI.instance().emit("STT_DONE", payload))
        except RuntimeError:
            try:
                from services.api_server import PandoraAPI

                payload = api_payload or {}
                payload = {**payload, **result}
                asyncio.run(PandoraAPI.instance().emit("STT_DONE", payload))
            except Exception as exc:
                logger.debug("API emission for STT_DONE failed: %s", exc)
        except Exception as exc:
            logger.debug("API emission for STT_DONE failed: %s", exc)


def create_whisper_runtime(*, mode: str = RuntimeMode.CONVERSATION.value, inference_url: Optional[str] = None) -> WhisperRuntime:
    return WhisperRuntime(mode=mode, inference_url=inference_url)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    rt = WhisperRuntime(mode=RuntimeMode.CONVERSATION.value)
    print(rt.ensure_ready(allow_google_fallback=True))
