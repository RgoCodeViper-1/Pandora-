import asyncio
import json
import logging
import math
import os
import queue
import tempfile
#import threading
import time
import wave

from vad.config import VADConfig
from audio.processing import AudioProcessor
try:
    import websockets
except ImportError:
    websockets = None

logger = logging.getLogger("pandora.vad")
if websockets is None:
    logger.warning("websockets not installed - Node event bridge unavailable")

# ---------------------------------------------------------------------------
# Optional imports — degrade gracefully so the file can be imported without
# every package installed during early development.
#
# NOTE: speech_recognition and ai.intent_bridge.parse_intent have been
# REMOVED from this file (see vad_changes_integration.md).  VAD's job ends
# once a confirmed utterance is saved to disk — it hands the audio off via
# a callback (or an AUDIO_READY event) and knows nothing about which STT
# engine or intent parser is used downstream.
# ---------------------------------------------------------------------------
try:
    import webrtcvad
    WEBRTC_AVAILABLE = True
except ImportError:
    WEBRTC_AVAILABLE = False
    logger.warning("webrtcvad not installed — WebRTC VAD unavailable")

try:
    import sounddevice as sd
    SOUNDDEVICE_AVAILABLE = True
except ImportError:
    SOUNDDEVICE_AVAILABLE = False
    logger.warning("sounddevice not installed — microphone capture unavailable")

try:
    import torch
    TORCH_AVAILABLE = True
except ImportError:
    TORCH_AVAILABLE = False

try:
    # Silero VAD is loaded via torch hub — only attempt when torch is present
    if TORCH_AVAILABLE:
        _silero_model, _silero_utils = torch.hub.load(
            repo_or_dir="snakers4/silero-vad",
            model="silero_vad",
            force_reload=False,
            verbose=False,
        )
        SILERO_AVAILABLE = True
    else:
        SILERO_AVAILABLE = False
except Exception as exc:
    SILERO_AVAILABLE = False
    logger.warning("Silero VAD unavailable: %s", exc)


# ---------------------------------------------------------------------------
# Configuration — overridden at runtime from shared/config/configuration.json
# ---------------------------------------------------------------------------
DEFAULT_CFG = {
    "sample_rate": 16000,
    "channels": 1,
    "vad_aggressiveness": 2,          # WebRTC aggressiveness: 0 (lenient) – 3 (strict)
    "vad_silence_duration": 1.8,      # seconds of silence → end-of-speech
    "vad_min_speech_duration": 0.4,   # seconds minimum to count as speech
    "max_recording_duration": 60,     # emergency safety cap; silence normally ends capture
    "silero_confidence_threshold": 0.5,
    "use_silero": True,
    "node_ws_uri": "ws://localhost:8765",
}


class AudioBuffer:
    """Thread-safe ring buffer for raw PCM frames."""

    def __init__(self):
        self._q: queue.Queue = queue.Queue()

    def put(self, frame: bytes) -> None:
        self._q.put(frame)

    def get_all(self) -> list[bytes]:
        frames = []
        while not self._q.empty():
            frames.append(self._q.get_nowait())
        return frames

    def clear(self) -> None:
        while not self._q.empty():
            try:
                self._q.get_nowait()
            except queue.Empty:
                break


class VADService:
    """
    Manages the continuous microphone → VAD → confirmed-audio pipeline.

    State machine (mirrors Node stateMachine.js states):
        IDLE → speech detected → CAPTURING → silence confirmed → FINALISING

    Responsibility boundary (post-refactor)
    ----------------------------------------
    VADService owns:
        - microphone capture
        - WebRTC speech-frame detection
        - Silero confirmation
        - WAV serialisation of a confirmed utterance

    VADService does NOT own:
        - STT (Whisper / Google / Vosk / Web Speech — irrelevant here)
        - intent parsing

    Once an utterance is confirmed and saved, control is handed off via:
        1. ``self._voice_runtime_callback(audio_path, raw_pcm)`` if a caller
           (e.g. VoiceRuntime) has wired itself in, OR
        2. an ``AUDIO_READY`` event over the Node WebSocket bridge, as a
           fallback when no in-process callback is registered.

    Either consumer is then responsible for invoking whatever STT service
    is configured (see services/stt_service.py → speech/stt.py) and, after
    that, ai/intent_bridge.py.
    """

    def __init__(self, cfg: dict):
        self.cfg = {**VADConfig().__dict__, **cfg}
        self.sample_rate: int = self.cfg["sample_rate"]
        self.frame_duration_ms: int = 30          # WebRTC requires 10/20/30 ms frames
        self.frame_size: int = int(self.sample_rate * self.frame_duration_ms / 1000)
        self._webrtc_vad = None
        self._audio_buf = AudioBuffer()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._ws = None
        self._running = False
        self._voice_runtime_interrupt_callback = None
        self._noise_floor = -60.0

        # Optional hand-off callback — VoiceRuntime (or any orchestrator)
        # can set this directly: ``vad._voice_runtime_callback = fn``
        # Signature: fn(audio_path: str, raw_pcm: bytes) -> None
        self._voice_runtime_callback = None
        self._audio_processor = AudioProcessor(sample_rate=self.sample_rate)

        self._init_webrtc_vad()

    # ------------------------------------------------------------------
    # Initialisation
    # ------------------------------------------------------------------
    def _init_webrtc_vad(self) -> None:
        if not WEBRTC_AVAILABLE:
            return
        self._webrtc_vad = webrtcvad.Vad(self.cfg["vad_aggressiveness"])
        logger.info("WebRTC VAD initialised (aggressiveness=%d)", self.cfg["vad_aggressiveness"])

    def _is_meaningful_speech(self, frame: bytes, webrtc_speech: bool) -> bool:
        """Reject low-energy background frames using dBFS hysteresis."""
        rms_linear = self._frame_rms_linear(frame)
        rms_dbfs = self._frame_rms(frame)
        if not webrtc_speech:
            noise_power = 10.0 ** (self._noise_floor / 10.0)
            frame_power = rms_linear * rms_linear
            smoothed_power = (noise_power * 0.98) + (frame_power * 0.02)
            self._noise_floor = 10.0 * math.log10(max(smoothed_power, 1e-10))
            return False

        threshold = max(
            self.cfg.get("minimum_speech_dbfs", -30.0),
            self._noise_floor + self.cfg.get("noise_gate_db_offset", 6.0),
        )
        return rms_dbfs >= threshold

    @staticmethod
    def _frame_rms_linear(frame: bytes) -> float:
        """Return normalized linear RMS amplitude in the range 0.0 to 1.0."""
        samples = memoryview(frame).cast("h")
        if not samples:
            return 0.0
        rms = math.sqrt(sum(sample * sample for sample in samples) / len(samples))
        return rms / 32767.0

    @staticmethod
    def _frame_rms(frame: bytes) -> float:
        """Return frame RMS in dBFS, with -100 dBFS representing silence."""
        rms_linear = VADService._frame_rms_linear(frame)
        if rms_linear == 0:
            return -100.0
        return 20.0 * math.log10(rms_linear)

    # ------------------------------------------------------------------
    # Silero — conditional second-stage confirmation
    # ------------------------------------------------------------------
    def _silero_confirm_speech(self, pcm_bytes: bytes) -> bool:
        """
        Run Silero on collected audio and return True if speech confirmed.
        Only called when cfg['use_silero'] is True and Silero is available.
        Reference: Jarvis config keys use_vad_whisper, vad_aggressiveness.
        """
        if not SILERO_AVAILABLE or not self.cfg.get("use_silero", True):
            return True  # pass-through when Silero unavailable
        try:
            # WebRTC already gated this utterance. Confirm only a bounded
            # prefix so a long capture cannot stall the STT handoff.
            max_confirmation_samples = self.sample_rate * 3
            confirmation_pcm = pcm_bytes[: max_confirmation_samples * 2]
            samples = torch.frombuffer(
                bytearray(confirmation_pcm),
                dtype=torch.int16,
            ).float() / 32768.0
            required_samples = 512 if self.sample_rate == 16000 else 256
            if self.sample_rate not in (8000, 16000):
                logger.warning(
                    "Silero requires an 8 kHz or 16 kHz sample rate; bypassing confirmation for %d Hz",
                    self.sample_rate,
                )
                return True

            scores = []
            with torch.no_grad():
                for start in range(0, samples.numel(), required_samples):
                    chunk = samples[start:start + required_samples]
                    if chunk.numel() < required_samples:
                        chunk = torch.nn.functional.pad(
                            chunk,
                            (0, required_samples - chunk.numel()),
                        )
                    score = _silero_model(chunk, self.sample_rate)
                    scores.append(float(score.item()))

            confidence = max(scores, default=0.0)
            logger.debug(
                "Silero confidence: %.3f across %d audio chunks",
                confidence,
                len(scores),
            )
            return confidence >= self.cfg["silero_confidence_threshold"]
        except Exception as exc:
            logger.warning("Silero inference failed: %s", exc)
            return True  # fail-open: forward downstream anyway

    # ------------------------------------------------------------------
    # Event emission to Node orchestrator
    # ------------------------------------------------------------------
    async def _emit(self, payload: dict) -> None:
        if self._ws:
            try:
                await self._ws.send(json.dumps(payload))
            except Exception as exc:
                logger.warning("WS send failed: %s", exc)

    # ------------------------------------------------------------------
    # Audio capture — sounddevice callback (runs on audio thread)
    # ------------------------------------------------------------------
    def _audio_callback(self, indata, frames, time_info, status):
        """Called by sounddevice for every audio block."""
        if status:
            logger.debug("sounddevice status: %s", status)
        # InputStream is configured as int16, so samples are already in PCM
        # amplitude units. Multiplying by 32767 here clips ordinary speech.
        gain = float(self.cfg.get("input_gain", 1.0))
        pcm_samples = (indata[:, 0].astype("float32") * gain).clip(
            -32768, 32767
        ).astype("int16")
        pcm = self._audio_processor.process(pcm_samples.tobytes())
        self._audio_buf.put(pcm)

    # ------------------------------------------------------------------
    # Core VAD loop — runs inside asyncio event loop
    # ------------------------------------------------------------------
    async def _vad_loop(self) -> None:
        """
        Continuous capture → WebRTC frame detection → Silero confirmation.
        Emits USER_STARTED to Node, then hands the confirmed utterance off
        to whichever downstream STT consumer is wired in (see class
        docstring). This loop no longer performs STT or intent parsing.
        """
        if not SOUNDDEVICE_AVAILABLE or not WEBRTC_AVAILABLE:
            logger.error("Cannot start VAD loop — dependencies missing")
            return

        silence_s = self.cfg["vad_silence_duration"]
        min_speech_s = self.cfg["vad_min_speech_duration"]
        max_s = self.cfg["max_recording_duration"]

        logger.info("VAD loop started — listening continuously")

        with sd.InputStream(
            samplerate=self.sample_rate,
            channels=1,
            dtype="int16",
            blocksize=self.frame_size,
            callback=self._audio_callback,
        ):
            while self._running:
                await asyncio.sleep(0.01)

                frames = self._audio_buf.get_all()
                if not frames:
                    continue

                # ── Stage 1: WebRTC speech detection ──────────────────
                speech_frames: list[bytes] = []
                consecutive_speech = 0
                for frame in frames:
                    # Pad or trim to exact frame size required by WebRTC
                    padded = frame.ljust(self.frame_size * 2, b"\x00")[: self.frame_size * 2]
                    try:
                        is_speech = self._webrtc_vad.is_speech(padded, self.sample_rate)
                    except Exception:
                        is_speech = False
                    if self._is_meaningful_speech(padded, is_speech):
                        consecutive_speech += 1
                        speech_frames.append(padded)
                    else:
                        consecutive_speech = 0
                        speech_frames.clear()

                if consecutive_speech < int(self.cfg.get("speech_start_frames", 4)):
                    continue

                # Speech detected — begin capturing utterance
                logger.info("User speech detected; capturing utterance")
                print("[PANDORA] USER INPUT DETECTED", flush=True)
                if self._voice_runtime_interrupt_callback is not None:
                    self._voice_runtime_interrupt_callback()
                await self._emit({"event": "USER_STARTED"})

                settle_s = float(self.cfg.get("speech_detection_settle_duration", 0.35))
                await asyncio.sleep(settle_s)

                captured: list[bytes] = []
                for frame in self._audio_buf.get_all():
                    captured.append(
                        frame.ljust(self.frame_size * 2, b"\x00")[: self.frame_size * 2]
                    )
                silence_start: float | None = None
                utterance_start = time.monotonic()

                while self._running:
                    await asyncio.sleep(0.01)
                    new_frames = self._audio_buf.get_all()

                    for frame in new_frames:
                        padded = frame.ljust(self.frame_size * 2, b"\x00")[: self.frame_size * 2]
                        try:
                            is_speech = self._webrtc_vad.is_speech(padded, self.sample_rate)
                        except Exception:
                            is_speech = False

                        captured.append(padded)

                        meaningful_speech = self._is_meaningful_speech(padded, is_speech)
                        if meaningful_speech:
                            silence_start = None
                        elif (
                            silence_start is None
                            and time.monotonic() - utterance_start
                            >= self.cfg.get("endpoint_min_capture_duration", 1.2)
                        ):
                            if silence_start is None:
                                silence_start = time.monotonic()

                    elapsed = time.monotonic() - utterance_start
                    silence_elapsed = (time.monotonic() - silence_start) if silence_start else 0

                    # Hard cap
                    if elapsed >= max_s:
                        logger.warning(
                            "Maximum utterance duration reached (%.1fs); resetting capture",
                            max_s,
                        )
                        print("[PANDORA] INPUT TIMEOUT — resetting; LISTENING", flush=True)
                        break

                    # Silence timeout → end of utterance
                    endpoint_silence = (
                        self.cfg.get("endpoint_long_silence", 1.5)
                        if elapsed >= self.cfg.get("endpoint_long_utterance_s", 4.0)
                        else max(
                            self.cfg.get("endpoint_short_silence", 1.0),
                            silence_s,
                        )
                    )
                    if silence_elapsed >= endpoint_silence:
                        break

                # ── Stage 2: Silero confirmation ───────────────────────
                raw_pcm = b"".join(captured)
                utterance_duration = len(raw_pcm) / 2 / self.sample_rate  # bytes → samples → seconds

                if utterance_duration < min_speech_s:
                    logger.debug("Utterance too short (%.2fs) — discarding", utterance_duration)
                    print("[PANDORA] INPUT TIMEOUT/TOO SHORT — LISTENING", flush=True)
                    continue

                if not self._silero_confirm_speech(raw_pcm):
                    logger.debug("Silero rejected segment — discarding")
                    print(
                        "[PANDORA] INPUT DISCARDED — speech confidence too low; LISTENING",
                        flush=True,
                    )
                    continue

                # ── Stage 3: hand off — VAD's job ends here ────────────
                # No STT. No intent parsing. Just a confirmed, serialised
                # utterance passed to whoever is listening.
                audio_path = self._save_audio(raw_pcm)

                if self._voice_runtime_callback is not None:
                    try:
                        self._voice_runtime_callback(audio_path, raw_pcm)
                    except Exception as exc:
                        logger.warning("voice_runtime_callback failed: %s", exc)
                else:
                    # No in-process consumer wired in — fall back to notifying
                    # Node so an external STT service can pick the file up.
                    await self._emit({
                        "event": "AUDIO_READY",
                        "audio_path": audio_path,
                        "duration": utterance_duration,
                    })

                logger.info(
                    "Utterance captured (%.2fs) — handed off (%s)",
                    utterance_duration,
                    "callback" if self._voice_runtime_callback is not None else "AUDIO_READY event",
                )
                print("[PANDORA] INPUT CAPTURED — processing", flush=True)
                print("[PANDORA] LISTENING — waiting for speech", flush=True)

    # ------------------------------------------------------------------
    # WAV serialisation
    # ------------------------------------------------------------------
    def _save_audio(self, pcm: bytes) -> str:
        fd, path = tempfile.mkstemp(suffix=".wav", prefix="pandora_utt_")
        os.close(fd)
        with wave.open(path, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)   # int16 = 2 bytes
            wf.setframerate(self.sample_rate)
            wf.writeframes(pcm)
        return path

    # ------------------------------------------------------------------
    # Optional WebSocket connection to Node orchestrator. It never gates VAD.
    # ------------------------------------------------------------------
    async def _connect_node(self) -> None:
        uri = self.cfg["node_ws_uri"]
        if not uri or websockets is None:
            return
        reconnect_delay = 2

        while self._running:
            try:
                async with websockets.connect(uri) as ws:
                    self._ws = ws
                    logger.info("Connected to Node orchestrator at %s", uri)
                    reconnect_delay = 2
                    while self._running:
                        await asyncio.sleep(0.5)
            except (OSError, websockets.exceptions.WebSocketException) as exc:
                logger.warning("WS connection lost: %s — retrying in %ds", exc, reconnect_delay)
                self._ws = None
                await asyncio.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 2, 30)

    async def _connect_and_run(self) -> None:
        """Run microphone capture independently of the optional Node bridge."""
        vad_task = asyncio.create_task(self._vad_loop())
        node_task = asyncio.create_task(self._connect_node())
        try:
            await vad_task
        finally:
            node_task.cancel()
            await asyncio.gather(node_task, return_exceptions=True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def start(self) -> None:
        """Start the VAD service (blocks calling thread)."""
        self._running = True
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._connect_and_run())
        finally:
            self._loop.close()

    def stop(self) -> None:
        self._running = False
        logger.info("VAD service stopping")

    def interrupt(self) -> None:
        """Drop buffered audio so a barge-in starts a clean utterance."""
        self._audio_buf.clear()
        logger.debug("VAD audio buffer flushed after interruption")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def load_config(path: str = "shared/config/configuration.json") -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            full = json.load(f)
        audio = full.get("audio", {})
        vad = audio.get("vad", {})
        return {
            "sample_rate": audio.get("sampleRate", DEFAULT_CFG["sample_rate"]),
            "vad_aggressiveness": vad.get("aggressiveness", DEFAULT_CFG["vad_aggressiveness"]),
            "vad_silence_duration": vad.get("silenceThresholdMs", 1800) / 1000,
            "silero_confidence_threshold": vad.get("sileroConfidenceThreshold", 0.5),
            "use_silero": vad.get("useSilero", True),
        }
    except Exception as exc:
        logger.warning("Config load failed (%s) — using defaults", exc)
        return {}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
    cfg = load_config()
    service = VADService(cfg)
    service.start()
