import asyncio
import json
import logging
import os
import queue
import tempfile
#import threading
import time
import wave

from vad.config import VADConfig
#import importlib.util
try : 
    import speech_recognition as sr
except ImportError:
    print("SpeechRecognition not installed — STT unavailable")

from ai.intent_bridge import parse_intent
import websockets 

logger = logging.getLogger("pandora.vad")

# ---------------------------------------------------------------------------
# Optional imports — degrade gracefully so the file can be imported without
# every package installed during early development.
# ---------------------------------------------------------------------------
try:
    #from pandora_venv 
    import webrtcvad
    WEBRTC_AVAILABLE = True
except ImportError:
    WEBRTC_AVAILABLE = False
    logger.warning("webrtcvad not installed — WebRTC VAD unavailable")

try:
    #from pandora_venv 
    import sounddevice as sd
    SOUNDDEVICE_AVAILABLE = True
except ImportError:
    SOUNDDEVICE_AVAILABLE = False
    logger.warning("sounddevice not installed — microphone capture unavailable")

try:
    #from pandora_venv 
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
    "max_recording_duration": 20,     # hard cap on single utterance
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
    Manages the continuous microphone → VAD → Node pipeline.

    State machine (mirrors Node stateMachine.js states):
        IDLE → speech detected → CAPTURING → silence confirmed → FINALISING
    """

    def __init__(self, cfg: dict):
        self.cfg = {** VADConfig().__dict__, **cfg}
        self.sample_rate: int = self.cfg["sample_rate"]
        self.frame_duration_ms: int = 30          # WebRTC requires 10/20/30 ms frames
        self.frame_size: int = int(self.sample_rate * self.frame_duration_ms / 1000)
        self._recognizer = sr.Recognizer()
        self._webrtc_vad = None
        self._audio_buf = AudioBuffer()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._ws = None
        self._running = False

        self._init_webrtc_vad()

    # ------------------------------------------------------------------
    # Initialisation
    # ------------------------------------------------------------------
    def _init_webrtc_vad(self) -> None:
        if not WEBRTC_AVAILABLE:
            return
        self._webrtc_vad = webrtcvad.Vad(self.cfg["vad_aggressiveness"])
        logger.info("WebRTC VAD initialised (aggressiveness=%d)", self.cfg["vad_aggressiveness"])

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
            audio_tensor = torch.frombuffer(pcm_bytes, dtype=torch.int16).float() / 32768.0
            confidence = _silero_model(audio_tensor, self.sample_rate).item()
            logger.debug("Silero confidence: %.3f", confidence)
            return confidence >= self.cfg["silero_confidence_threshold"]
        except Exception as exc:
            logger.warning("Silero inference failed: %s", exc)
            return True  # fail-open: forward to STT anyway

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
        pcm = (indata[:, 0] * 32767).astype("int16").tobytes()
        self._audio_buf.put(pcm)

    # ------------------------------------------------------------------
    # Core VAD loop — runs inside asyncio event loop
    # ------------------------------------------------------------------
    async def _vad_loop(self) -> None:
        """
        Continuous capture → WebRTC frame detection → Silero confirmation.
        Emits USER_STARTED / USER_FINISHED events to Node.
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
                for frame in frames:
                    # Pad or trim to exact frame size required by WebRTC
                    padded = frame.ljust(self.frame_size * 2, b"\x00")[: self.frame_size * 2]
                    try:
                        is_speech = self._webrtc_vad.is_speech(padded, self.sample_rate)
                    except Exception:
                        is_speech = False
                    if is_speech:
                        speech_frames.append(padded)

                if not speech_frames:
                    continue

                # Speech detected — begin capturing utterance
                await self._emit({"event": "USER_STARTED"})

                captured: list[bytes] = list(speech_frames)
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

                        if is_speech:
                            silence_start = None
                        else:
                            if silence_start is None:
                                silence_start = time.monotonic()

                    elapsed = time.monotonic() - utterance_start
                    silence_elapsed = (time.monotonic() - silence_start) if silence_start else 0

                    # Hard cap
                    if elapsed >= max_s:
                        break

                    # Silence timeout → end of utterance
                    if silence_elapsed >= silence_s:
                        break

                # ── Stage 2: Silero confirmation ───────────────────────
                raw_pcm = b"".join(captured)
                utterance_duration = len(raw_pcm) / 2 / self.sample_rate  # bytes → samples → seconds

                if utterance_duration < min_speech_s:
                    logger.debug("Utterance too short (%.2fs) — discarding", utterance_duration)
                    continue

                if not self._silero_confirm_speech(raw_pcm):
                    logger.debug("Silero rejected segment — discarding")
                    continue

                # Save raw audio to temp file → STT picks it up
                audio_path = self._save_audio(raw_pcm)
                
                # 🔥 STT + Rust + Python Intent Pipeline
                text = ""
                
                try:
                    with sr.AudioFile(audio_path) as source:
                        audio = self._recognizer.record(source)
                        text = self._recognizer.recognize_google(audio).lower().strip()
                except Exception as exc:
                    logger.warning("STT failed: %s", exc)
                    text = ""
                
                intent = parse_intent(text)
                
                await self._emit({
                    "event": "INTENT_READY",
                    "text": text,
                    "intent": intent,
                    "audio_path": audio_path
                })
                
                logger.info("Intent processed (%.2fs): %s", utterance_duration, intent.get("intent"))

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
    # WebSocket connection to Node orchestrator
    # ------------------------------------------------------------------
    async def _connect_and_run(self) -> None:
        uri = self.cfg["node_ws_uri"]
        reconnect_delay = 2

        while self._running:
            try:
                async with websockets.connect(uri) as ws:
                    self._ws = ws
                    logger.info("Connected to Node orchestrator at %s", uri)
                    reconnect_delay = 2
                    await self._vad_loop()
            except (OSError, websockets.exceptions.WebSocketException) as exc:
                logger.warning("WS connection lost: %s — retrying in %ds", exc, reconnect_delay)
                self._ws = None
                await asyncio.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 2, 30)

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