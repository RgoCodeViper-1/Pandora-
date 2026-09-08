"""
speech/stt.py
=============
Speech-to-text engine — faster-whisper (primary, local/offline) with
Google STT as a network fallback.

Pre-STT pipeline (mirrors architecture_details1.md §13):
    WAV / PCM
      ↓  MockRustRuntime.score_frame()   ← Python heuristic shim
      ↓  (future: rust_core.process_audio_frame())
      ↓  pre-filter: discard noise/keyboard clicks before transcription
      ↓  faster-whisper transcribe
      ↓  parse_intent_stream()  →  parse_intent() fallback
      ↓  callback({ "event": "INTENT_READY", "text": ..., "intent": ... })

Swap point for future Rust audio runtime
─────────────────────────────────────────
When rust_core exposes process_audio_frame() (audio_runtime.rs / heuristics.rs),
replace MockRustRuntime.score_frame() with:

    import rust_core
    decision = json.loads(rust_core.process_audio_frame(pcm_bytes))

The callback contract and the rest of this file stay unchanged.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time
import wave
from dataclasses import dataclass
from typing import Callable
import requests
from pathlib import Path

import numpy as np

logger = logging.getLogger("pandora.stt")

# ── speech_recognition (Google STT fallback) ─────────────────────────────────
try:
    import speech_recognition as sr
    SR_AVAILABLE = True
except ImportError:
    sr = None  # type: ignore[assignment]
    SR_AVAILABLE = False
    logger.warning("speech_recognition not installed — Google STT fallback unavailable")

# ── whisper.cpp server (primary local STT) ───────────────────────────────────────

from pathlib import Path
import requests

WHISPER_DIR = (
    Path(__file__)
    .resolve()
    .parents[2]
    / "native"
    / "whisper_cpp"
)

WHISPER_SERVER = WHISPER_DIR / "whisper-server.exe"

WHISPER_AVAILABLE = (
    WHISPER_SERVER.exists()
)

if not WHISPER_AVAILABLE:
    logger.warning(
        f"whisper.cpp not found: {WHISPER_SERVER}"
    )
else:
    logger.info(
        f"whisper.cpp detected: {WHISPER_SERVER}"
    )

# ── rust_core (intent bridge) ─────────────────────────────────────────────────
from ai.intent_bridge import parse_intent, parse_intent_stream

# ── Optional: rust_core.process_audio_frame (future audio runtime) ───────────
try:
    import pandora_core as _rust_core
    _RUST_AUDIO_AVAILABLE = hasattr(_rust_core, "process_audio_frame")
except ImportError:
    _rust_core = None  # type: ignore[assignment]
    _RUST_AUDIO_AVAILABLE = False


# ─────────────────────────────────────────────────────────────────────────────
# MockRustRuntime
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class FrameDecision:
    """
    Mirrors the future FrameDecision struct from audio_runtime.rs.

    When rust_core.process_audio_frame() is available this will be
    populated from its JSON output instead of being computed here.
    """
    probable_speech:  bool  = False
    confidence:       float = 0.0
    continue_capture: bool  = False
    interruption:     bool  = False
    momentum:         float = 0.0
    zcr:              float = 0.0


class MockRustRuntime:
    """
    Python heuristic shim that mirrors the future Rust audio runtime
    (audio_runtime.rs + heuristics.rs + conversation_state.rs).

    Implements RMS energy, ZCR, speech momentum, adaptive silence timeout,
    continuity scoring, and burst grouping — all described in
    audioruntime_details.md.

    SWAP POINT: when rust_core.process_audio_frame() is compiled and exposed,
    replace score_frame() body with:

        raw = _rust_core.process_audio_frame(pcm_bytes)
        d   = json.loads(raw)
        return FrameDecision(**d)

    Everything else stays unchanged.
    """

    # Thresholds — tuned for 16 kHz int16 PCM, 30 ms frames
    RMS_SPEECH_THRESHOLD  = 800     # int16 amplitude units
    ZCR_SPEECH_MAX        = 0.35    # above this = noise/click
    MIN_CONTINUITY_FRAMES = 3       # frames before momentum builds
    FRAME_DURATION_S      = 0.030   # 30 ms

    def __init__(self) -> None:
        self.momentum:         float = 0.0
        self.speech_duration:  float = 0.0
        self.silence_elapsed:  float = 0.0
        self.continuity:       int   = 0

    def reset(self) -> None:
        self.momentum        = 0.0
        self.speech_duration = 0.0
        self.silence_elapsed = 0.0
        self.continuity      = 0

    def adaptive_silence_timeout(self) -> float:
        """
        Longer utterances tolerate longer pauses before finalising.
        Source: audioruntime_details.md §1 (Adaptive Silence Timeout).
        """
        if self.speech_duration < 2.0:
            return 0.7
        elif self.speech_duration < 6.0:
            return 1.2
        else:
            return 2.0

    def score_frame(self, pcm_bytes: bytes, sample_rate: int = 16000) -> FrameDecision:
        """
        Analyse a single 30 ms PCM frame and return a FrameDecision.

        This is the SWAP POINT — replace the body here when
        rust_core.process_audio_frame() is available.
        """
        # ── Fast path: delegate to Rust if available ──────────────────────
        if _RUST_AUDIO_AVAILABLE:
            try:
                raw = _rust_core.process_audio_frame(pcm_bytes)  # type: ignore[union-attr]
                d = json.loads(raw)
                return FrameDecision(
                    probable_speech  = d.get("probable_speech",  False),
                    confidence       = d.get("confidence",       0.0),
                    continue_capture = d.get("continue_capture", False),
                    interruption     = d.get("interruption",     False),
                    momentum         = d.get("momentum",         0.0),
                    zcr              = d.get("zcr",              0.0),
                )
            except Exception as exc:
                logger.debug("rust_core.process_audio_frame failed: %s", exc)

        # ── Python heuristic path ─────────────────────────────────────────
        try:
            n_samples = len(pcm_bytes) // 2
            if n_samples == 0:
                return FrameDecision()

            samples = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32)

            # RMS energy
            rms = float(np.sqrt(np.mean(samples ** 2)))

            # Zero-crossing rate (high ZCR = noise/click, not voice)
            signs = np.sign(samples)
            signs[signs == 0] = 1
            zcr = float(np.sum(np.abs(np.diff(signs))) / (2 * n_samples))

            is_voice = (rms >= self.RMS_SPEECH_THRESHOLD) and (zcr <= self.ZCR_SPEECH_MAX)

            if is_voice:
                self.continuity      += 1
                self.speech_duration += self.FRAME_DURATION_S
                self.silence_elapsed  = 0.0
                # Momentum builds only after MIN_CONTINUITY_FRAMES
                # (prevents keyboard-click momentum spikes)
                if self.continuity >= self.MIN_CONTINUITY_FRAMES:
                    self.momentum = min(self.momentum + 0.3, 2.0)
                else:
                    self.momentum = min(self.momentum + 0.05, 2.0)
            else:
                self.continuity       = 0
                self.silence_elapsed += self.FRAME_DURATION_S
                # Speech continuation memory: short pauses decay gently
                if self.silence_elapsed < 0.3:
                    self.momentum *= 0.95   # brief gap — preserve continuity
                else:
                    self.momentum *= 0.72   # real silence — decay faster

            # Multi-factor confidence (audioruntime_details.md §5)
            rms_score        = min(rms / (self.RMS_SPEECH_THRESHOLD * 2), 1.0)
            zcr_score        = max(0.0, 1.0 - (zcr / max(self.ZCR_SPEECH_MAX, 1e-6)))
            continuity_score = min(self.continuity / 5, 1.0)
            momentum_score   = min(self.momentum / 2.0, 1.0)

            confidence = (
                rms_score        * 0.30 +
                zcr_score        * 0.20 +
                continuity_score * 0.30 +
                momentum_score   * 0.20
            )

            timeout = self.adaptive_silence_timeout()
            continue_capture = (
                confidence >= 0.35
                or self.momentum >= 0.4
                or self.silence_elapsed < timeout
            )

            # Interruption: sudden high-energy burst after a quiet period
            interruption = (
                rms >= self.RMS_SPEECH_THRESHOLD * 2.5
                and self.silence_elapsed >= 0.2
            )

            return FrameDecision(
                probable_speech  = confidence >= 0.40,
                confidence       = confidence,
                continue_capture = continue_capture,
                interruption     = interruption,
                momentum         = self.momentum,
                zcr              = zcr,
            )

        except Exception as exc:
            logger.debug("MockRustRuntime.score_frame error: %s", exc)
            return FrameDecision()


# ─────────────────────────────────────────────────────────────────────────────
# WhisperSTT — faster-whisper wrapper
# ─────────────────────────────────────────────────────────────────────────────

class WhisperSTT:

    def __init__(self):

        # Reconciled with whisper_manager.py default port (8080) and
        # whisper_runtime2.py's WHISPER_URL — previously this pointed at
        # 8177, which silently broke transcription whenever whisper_manager
        # launched the server on its default port.
        self.host = "http://127.0.0.1:8080/inference"

    def transcribe(self, pcm_bytes: bytes, sample_rate=16000):

        fd, path = tempfile.mkstemp(
            suffix=".wav",
            prefix="pandora_stt_"
        )

        os.close(fd)

        try:

            with wave.open(path, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(sample_rate)
                wf.writeframes(pcm_bytes)

            with open(path, "rb") as audio:

                files = {
                    "file": audio
                }

                response = requests.post(
                    self.host,
                    files=files,
                    timeout=30
                )

            if response.status_code != 200:
                return ""

            data = response.json()

            return (
                data
                .get("text", "")
                .strip()
                .lower()
            )

        except Exception as e:

            logger.warning(
                f"whisper.cpp failed: {e}"
            )

            return ""

        finally:

            try:
                os.unlink(path)
            except:
                pass


    def transcribe_wav(self, wav_path):

        try:

            with open(wav_path, "rb") as audio:

                files = {
                    "file": audio
                }

                response = requests.post(
                    self.host,
                    files=files,
                    timeout=30
                )

            if response.status_code != 200:
                return ""

            data = response.json()

            return (
                data
                .get("text", "")
                .strip()
                .lower()
            )

        except Exception as e:

            logger.warning(
                f"whisper.cpp failed: {e}"
            )

            return ""

# ─────────────────────────────────────────────────────────────────────────────
# GoogleSTT — speech_recognition fallback
# ─────────────────────────────────────────────────────────────────────────────

class GoogleSTT:
    """Google Cloud STT via speech_recognition (network, free tier)."""

    def __init__(self) -> None:
        if not SR_AVAILABLE:
            raise RuntimeError("speech_recognition package required")
        self._recognizer = sr.Recognizer()

    def transcribe_wav(self, wav_path: str) -> str:
        try:
            with sr.AudioFile(wav_path) as source:
                audio = self._recognizer.record(source)
            return self._recognizer.recognize_google(audio).lower().strip()
        except sr.UnknownValueError:
            return ""
        except sr.RequestError as exc:
            logger.warning("Google STT request error: %s", exc)
            return ""
        except Exception as exc:
            logger.warning("GoogleSTT.transcribe_wav error: %s", exc)
            return ""


# ─────────────────────────────────────────────────────────────────────────────
# SpeechToTextEngine — main class used by VADService + standalone stream()
# ─────────────────────────────────────────────────────────────────────────────

class SpeechToTextEngine:
    """
    Continuous speech recognition.

    Per-utterance pipeline:
        PCM bytes
          ↓ MockRustRuntime.score_frame()   ← pre-STT heuristic filter
          ↓ [discard if not probable_speech]
          ↓ WhisperSTT.transcribe()          ← primary (local, offline)
          ↓ GoogleSTT.transcribe_wav()       ← fallback (network)
          ↓ parse_intent_stream() → parse_intent()
          ↓ callback(INTENT_READY)

    When VADService is running: VADService captures audio and calls
    transcribe_wav() directly.  The standalone stream() / _mic_loop path
    is only used when running this file without VADService.
    """

    def __init__(
        self,
        whisper_model:   str = "base",
        whisper_device:  str = "cpu",
        whisper_compute: str = "int8",
    ) -> None:
        # Primary STT
        self._whisper: WhisperSTT | None = None
        if WHISPER_AVAILABLE:
            try:
                self._whisper = WhisperSTT()
            except Exception as exc:
                logger.warning("Could not load faster-whisper: %s", exc)

        # Fallback STT
        self._google: GoogleSTT | None = None
        if SR_AVAILABLE:
            try:
                self._google = GoogleSTT()
            except Exception as exc:
                logger.warning("Could not init GoogleSTT: %s", exc)

        if self._whisper is None and self._google is None:
            raise RuntimeError(
                "No STT backend available. "
                "Install faster-whisper (pip install faster-whisper) "
                "or speech_recognition (pip install SpeechRecognition)."
            )

        # Per-utterance audio heuristic shim (swapped for Rust when ready)
        self._audio_rt = MockRustRuntime()

        # For standalone stream() mode
        self._sr_recognizer = sr.Recognizer() if SR_AVAILABLE else None
        self._sr_mic        = sr.Microphone() if SR_AVAILABLE else None
        self._running       = False
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------
    # Transcription helpers  (called directly by VADService)
    # ------------------------------------------------------------------

    def transcribe_pcm(self, pcm_bytes: bytes, sample_rate: int = 16000) -> str:
        """
        Transcribe raw int16 PCM bytes with pre-STT heuristic filter.
        Used by VADService after Silero confirmation for final transcription.
        """
        decision = self._audio_rt.score_frame(pcm_bytes, sample_rate)
        if not decision.probable_speech and self._audio_rt.momentum < 0.3:
            logger.debug(
                "Pre-STT filter: low confidence %.2f momentum %.2f — discarding",
                decision.confidence, self._audio_rt.momentum,
            )
            return ""

        if self._whisper is not None:
            text = self._whisper.transcribe(pcm_bytes, sample_rate)
            if text:
                return text

        # Fallback: write temp WAV for Google
        if self._google is not None:
            fd, path = tempfile.mkstemp(suffix=".wav", prefix="pandora_stt_")
            os.close(fd)
            try:
                with wave.open(path, "wb") as wf:
                    wf.setnchannels(1)
                    wf.setsampwidth(2)
                    wf.setframerate(sample_rate)
                    wf.writeframes(pcm_bytes)
                return self._google.transcribe_wav(path)
            finally:
                try:
                    os.unlink(path)
                except OSError:
                    pass

        return ""

    def transcribe_wav(self, wav_path: str) -> str:
        """
        Transcribe from a WAV file path.
        Called by VADService after _save_audio() + Silero confirmation.
        No pre-filter here — VADService has already gated on WebRTC + Silero.
        """
        if self._whisper is not None:
            text = self._whisper.transcribe_wav(wav_path)
            if text:
                return text
        if self._google is not None:
            return self._google.transcribe_wav(wav_path)
        return ""

    def parse(self, text: str) -> dict:
        """
        Intent parse — streaming fast path (Rust) then full batch fallback.
        """
        if not text:
            return {"intent": "unknown", "entities": {}, "confidence": 0.0}
        result = parse_intent_stream(text)
        if result is None:
            result = parse_intent(text)
        return result

    def reset_audio_state(self) -> None:
        """Reset MockRustRuntime state between utterances."""
        self._audio_rt.reset()

    # ------------------------------------------------------------------
    # Standalone mic stream (not used when VADService is active)
    # ------------------------------------------------------------------

    def stream(self, callback: Callable | None = None) -> None:
        """
        Start a background daemon thread that captures from the mic,
        filters, transcribes, and parses each utterance.

        NOT called when VADService is running — VADService owns the mic.
        """
        if self._running:
            logger.warning("SpeechToTextEngine.stream() already running")
            return
        if self._sr_recognizer is None or self._sr_mic is None:
            logger.error("speech_recognition required for standalone stream() mode")
            return

        self._running = True
        self._thread = threading.Thread(
            target=self._mic_loop,
            args=(callback,),
            daemon=True,
            name="pandora-stt",
        )
        self._thread.start()
        logger.info("STT mic stream started")

    def stop(self) -> None:
        self._running = False
        logger.info("STT stream stopping")

    def _mic_loop(self, callback: Callable | None) -> None:
        with self._sr_mic as source:
            try:
                self._sr_recognizer.adjust_for_ambient_noise(source, duration=1)
                logger.debug("STT ambient noise calibration done")
            except Exception as exc:
                logger.warning("Ambient noise calibration failed: %s", exc)

            while self._running:
                try:
                    audio = self._sr_recognizer.listen(
                        source, timeout=4, phrase_time_limit=10
                    )

                    # Convert AudioData → raw PCM for pre-filter
                    pcm_bytes = audio.get_raw_data(
                        convert_rate=16000, convert_width=2
                    )

                    decision = self._audio_rt.score_frame(pcm_bytes)
                    if not decision.probable_speech and self._audio_rt.momentum < 0.3:
                        logger.debug("Pre-STT filter rejected frame — discarding")
                        continue

                    # Transcribe
                    if self._whisper is not None:
                        text = self._whisper.transcribe(pcm_bytes)
                    else:
                        try:
                            text = self._sr_recognizer.recognize_google(audio).lower().strip()
                        except sr.UnknownValueError:
                            continue
                        except sr.RequestError as exc:
                            logger.warning("Google STT request failed: %s", exc)
                            time.sleep(1)
                            continue

                    if not text:
                        continue

                    logger.debug("STT recognised: %r", text)
                    intent = self.parse(text)
                    self.reset_audio_state()

                    if callback:
                        callback(
                            {"event": "INTENT_READY", "text": text, "intent": intent}
                        )

                except sr.WaitTimeoutError:
                    pass
                except sr.UnknownValueError:
                    pass
                except Exception as exc:
                    logger.error("STT mic loop error: %s", exc)
                    time.sleep(0.2)