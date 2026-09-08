from __future__ import annotations

import json
import logging
import os
import tempfile
import time
import wave
from pathlib import Path
from typing import Any, BinaryIO, Optional, Union

try:
    import requests
except ImportError:  # pragma: no cover - handled gracefully at runtime
    requests = None  # type: ignore[assignment]

logger = logging.getLogger("pandora.whisper_client")

DEFAULT_HOST = os.getenv("PANDORA_WHISPER_HOST", "127.0.0.1")
DEFAULT_PORT = int(os.getenv("PANDORA_WHISPER_PORT", "8080"))
DEFAULT_PATH = os.getenv("PANDORA_WHISPER_PATH", "/inference")
DEFAULT_TIMEOUT = float(os.getenv("PANDORA_WHISPER_TIMEOUT", "30"))


def resolve_whisper_url(host: Optional[str] = None, port: Optional[int] = None, path: Optional[str] = None) -> str:
    """Return the canonical Whisper HTTP inference URL."""
    host = host or DEFAULT_HOST
    port = int(port or DEFAULT_PORT)
    path = path or DEFAULT_PATH
    if not path.startswith("/"):
        path = f"/{path}"
    return f"http://{host}:{port}{path}"


class WhisperClient:
    """HTTP client for the local whisper.cpp server.

    This client is intentionally small and infrastructure-focused. It does not
    own VAD, mode transitions, or intent execution. It only knows how to probe a
    Whisper endpoint and return recognitions in a reliable format.
    """

    def __init__(
        self,
        inference_url: Optional[str] = None,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        retries: int = 2,
        retry_backoff: float = 0.5,
    ) -> None:
        self.inference_url = inference_url or resolve_whisper_url()
        self.timeout = timeout
        self.retries = max(0, retries)
        self.retry_backoff = retry_backoff
        self.last_error: Optional[str] = None

    @property
    def base_url(self) -> str:
        return self.inference_url.rsplit("/", 1)[0] if "/" in self.inference_url else self.inference_url

    def healthcheck(self) -> bool:
        if requests is None:
            return False
        try:
            response = requests.get(self.base_url, timeout=min(5, self.timeout))
            return response.status_code < 500
        except Exception as exc:  # pragma: no cover - network-specific path
            logger.debug("Whisper health check failed: %s", exc)
            return False

    def transcribe_file(self, audio_file: Union[str, Path, BinaryIO], *, timeout: Optional[float] = None) -> str:
        """POST a WAV/PCM-like file to the Whisper inference endpoint."""
        file_obj: Optional[BinaryIO] = None
        opened_here = False
        try:
            if hasattr(audio_file, "read"):
                file_obj = audio_file
                file_name = getattr(audio_file, "name", "upload.wav")
            else:
                path = Path(audio_file)
                file_obj = path.open("rb")
                opened_here = True
                file_name = path.name

            payload = {"file": (file_name, file_obj, "application/octet-stream")}
            try:
                response = self._post(payload, timeout=timeout)
            finally:
                if file_obj is not None and (opened_here or hasattr(audio_file, "read")):
                    try:
                        file_obj.close()
                    except Exception:
                        pass

            if response is None:
                return ""

            if response.status_code != 200:
                self.last_error = f"Whisper HTTP {response.status_code}"
                logger.warning("Whisper request failed: %s", self.last_error)
                return ""

            try:
                data = response.json()
            except ValueError:
                data = {"text": response.text.strip()}

            text = self._extract_text(data)
            self.last_error = None
            return text
        except Exception as exc:
            self.last_error = str(exc)
            logger.warning("Whisper transcribe_file error: %s", exc)
            return ""

    def transcribe_wav(self, wav_path: Union[str, Path], *, timeout: Optional[float] = None) -> str:
        return self.transcribe_file(wav_path, timeout=timeout)

    def transcribe_pcm(self, pcm_bytes: bytes, sample_rate: int = 16000, *, timeout: Optional[float] = None) -> str:
        fd, temp_path = tempfile.mkstemp(suffix=".wav", prefix="pandora_whisper_")
        os.close(fd)
        try:
            with wave.open(temp_path, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(sample_rate)
                wf.writeframes(pcm_bytes)
            return self.transcribe_wav(temp_path, timeout=timeout)
        finally:
            try:
                os.unlink(temp_path)
            except Exception:
                pass

    def _post(self, payload: dict[str, Any], *, timeout: Optional[float] = None) -> Optional[Any]:
        if requests is None:
            raise RuntimeError("The 'requests' package is required for Whisper transcription.")

        timeout_value = timeout if timeout is not None else self.timeout
        for attempt in range(self.retries + 1):
            try:
                response = requests.post(self.inference_url, files=payload, timeout=timeout_value)
                if response.status_code == 200:
                    return response
                if response.status_code in {429, 500, 502, 503, 504} and attempt < self.retries:
                    time.sleep(self.retry_backoff * (attempt + 1))
                    continue
                return response
            except requests.exceptions.RequestException as exc:
                self.last_error = str(exc)
                if attempt >= self.retries:
                    logger.warning("Whisper request failed after %d attempts: %s", attempt + 1, exc)
                    return None
                time.sleep(self.retry_backoff * (attempt + 1))
        return None

    @staticmethod
    def _extract_text(payload: Any) -> str:
        if isinstance(payload, str):
            return payload.strip()
        if not isinstance(payload, dict):
            return ""

        for key in ("text", "transcript", "result", "output", "sentence"):
            value = payload.get(key)
            if isinstance(value, str):
                return value.strip()
        if "segments" in payload and isinstance(payload["segments"], list):
            parts = []
            for segment in payload["segments"]:
                if isinstance(segment, dict):
                    segment_text = segment.get("text")
                    if isinstance(segment_text, str):
                        parts.append(segment_text.strip())
            return " ".join(part for part in parts if part).strip()
        return ""
