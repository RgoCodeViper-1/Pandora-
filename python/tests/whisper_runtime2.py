"""
Pandora — Production Audio Runtime
====================================
Upgraded from whisper_runtime_test.py.

Architecture:
    Mic PCM
     ↓
    WebRTC fast gate
     ↓
    ConversationalRuntime (MockRustRuntime → future rust_core.process_audio_frame)
     ↓
    ConversationStateEngine (turn/pause/interruption state machine)
     ↓
    Conditional Silero verification (placeholder hook)
     ↓
    Deferred utterance finalization
     ↓
    WAV serialize → Whisper STT
     ↓
    Transcript output / intent trigger hook

Key improvements over v1:
  - Stateful ConversationState enum (IDLE, LISTENING, USER_SPEAKING,
    USER_PAUSED, AI_SPEAKING, INTERRUPTION, PROCESSING)
  - Adaptive silence timeout based on accumulated speech duration
  - Deferred utterance finalization (prevents hesitation clipping)
  - Pause confidence accumulator (graded silence, not binary stop)
  - Speech continuation memory (momentum decays gently during short pauses)
  - Multi-factor speech confidence score (RMS + ZCR + continuity + momentum)
  - Burst grouping (nearby bursts merged before finalize decision)
  - AI-speaking suppression mode (ignores noise, detects interruptions)
  - WAV only serialized on finalized utterances (no disk spam)
  - Silero hook slot (runs only when WebRTC+Rust confidence is ambiguous)
  - rust_core.process_audio_frame() hook (drops in when Rust module ready)
"""

from __future__ import annotations

import os
import sys
import time
import wave
import tempfile
import threading
import collections
from enum import Enum, auto
from dataclasses import dataclass, field
from typing import Callable, Optional, List

import numpy as np
import pyaudio
import requests
import webrtcvad


# =============================================================================
# CONSTANTS / CONFIG
# =============================================================================

WHISPER_URL      = "http://127.0.0.1:8080/inference"  # local Whisper server endpoint

RATE             = 16000          # Hz — must match webrtcvad requirement
CHANNELS         = 1
FRAME_DURATION   = 30             # ms — webrtcvad supports 10/20/30 ms
FRAME_SIZE       = int(RATE * FRAME_DURATION / 1000)  # samples per frame
BYTES_PER_FRAME  = FRAME_SIZE * 2                      # int16 = 2 bytes

# ---- Silence / Pause thresholds (seconds) -----------------------------------
#   Adaptive: short command / medium / long utterance
SILENCE_SHORT    = 0.65
SILENCE_MEDIUM   = 1.25
SILENCE_LONG     = 2.10

# Deferred finalization: wait this long after silence starts before committing
DEFER_WINDOW     = 0.22   # seconds — user may resume in this window

# Burst grouping: if a new burst starts within this gap, merge utterances
BURST_GAP        = 0.35   # seconds

# How many consecutive active frames before speech momentum starts rising
MOMENTUM_ONSET   = 3

# Interruption detection: energy spike while AI is speaking
INTERRUPT_RMS    = 1800   # higher bar to avoid false triggers during TTS

# Silero escalation: only call Silero when confidence sits in this range
SILERO_AMBIG_LO  = 0.35
SILERO_AMBIG_HI  = 0.70

# Rust module readiness flag — flip True once rust_core wheel is built
RUST_CORE_AVAILABLE = False
try:
    import rust_core          # type: ignore
    RUST_CORE_AVAILABLE = True
except ImportError:
    pass


# =============================================================================
# CONVERSATION STATE
# =============================================================================

class ConversationState(Enum):
    IDLE         = "idle"
    LISTENING    = "listening"
    USER_SPEAKING = "user_speaking"
    USER_PAUSED  = "user_paused"
    AI_SPEAKING  = "ai_speaking"
    INTERRUPTION = "interruption"
    PROCESSING   = "processing"


# =============================================================================
# FRAME DECISION DATACLASS  (mirrors future Rust FrameDecision struct)
# =============================================================================

@dataclass
class FrameDecision:
    probable_speech: bool   = False
    confidence: float       = 0.0   # 0.0 – 1.0 multi-factor score
    continue_capture: bool  = False
    interruption: bool      = False
    rms: float              = 0.0
    zcr: float              = 0.0
    momentum: float         = 0.0
    active_frames: int      = 0


# =============================================================================
# CONVERSATIONAL HEURISTIC RUNTIME
# (Python stand-in for future rust_core.process_audio_frame)
# =============================================================================

class ConversationalRuntime:
    """
    Frame-level conversational intelligence.
    Tracks:
      - RMS / ZCR signal features
      - speech momentum (continuity across frames)
      - pause confidence (graded silence awareness)
      - interruption detection during AI speech
      - multi-factor confidence score

    This class is designed as a 1-to-1 migration target for:
        rust_core.process_audio_frame(frame: &[u8]) -> FrameDecision
    """

    # ---- Signal thresholds --------------------------------------------------
    RMS_THRESHOLD   = 900     # minimum RMS for speech candidacy
    RMS_INTERRUPT   = INTERRUPT_RMS
    ZCR_MIN         = 0.015   # below this → likely impulsive noise / click
    ZCR_MAX         = 0.28    # above this → likely background hiss / static

    # ---- Momentum config ----------------------------------------------------
    MOMENTUM_ONSET_FRAMES = MOMENTUM_ONSET
    MOMENTUM_RISE  = 0.28     # per eligible frame
    MOMENTUM_DECAY = 0.92     # during short pause (gentle)
    MOMENTUM_DROP  = 0.68     # during long silence (aggressive)
    MOMENTUM_MAX   = 6.0
    MOMENTUM_SPEAK_THRESHOLD = 0.55   # above this → speech active

    # ---- Confidence weights -------------------------------------------------
    W_RMS          = 0.30
    W_ZCR          = 0.20
    W_CONTINUITY   = 0.30
    W_MOMENTUM     = 0.20

    def __init__(self) -> None:
        self.speech_momentum: float = 0.0
        self.active_frames: int     = 0
        self.pause_frames: int      = 0
        self._last_active: float    = time.time()

    # ---- Internal helpers ---------------------------------------------------

    @staticmethod
    def _zcr(samples: np.ndarray) -> float:
        zc = np.sum(np.abs(np.diff(np.sign(samples))))
        return float(zc) / max(len(samples) - 1, 1)

    @staticmethod
    def _rms(samples: np.ndarray) -> float:
        return float(np.sqrt(np.mean(samples.astype(np.float64) ** 2)))

    # ---- Main frame processor -----------------------------------------------

    def process_frame(self, pcm: bytes, ai_speaking: bool = False) -> FrameDecision:
        """
        Analyse one PCM frame and return a FrameDecision.

        Args:
            pcm:         Raw 16-bit PCM bytes (FRAME_SIZE * 2 bytes expected).
            ai_speaking: True when TTS is active — tightens interrupt detection.
        """
        samples = np.frombuffer(pcm, dtype=np.int16)

        rms = self._rms(samples)
        zcr = self._zcr(samples)

        # ----- Signal quality gates ------------------------------------------
        rms_ok = rms > (self.RMS_INTERRUPT if ai_speaking else self.RMS_THRESHOLD)
        zcr_ok = self.ZCR_MIN <= zcr <= self.ZCR_MAX
        frame_active = rms_ok and zcr_ok

        # ----- Continuity / active-frame counter -----------------------------
        if frame_active:
            self.active_frames += 1
            self.pause_frames   = 0
            self._last_active   = time.time()
        else:
            self.pause_frames  += 1
            self.active_frames  = max(0, self.active_frames - 1)

        # ----- Momentum update -----------------------------------------------
        if self.active_frames >= self.MOMENTUM_ONSET_FRAMES:
            self.speech_momentum = min(
                self.speech_momentum + self.MOMENTUM_RISE,
                self.MOMENTUM_MAX,
            )
        else:
            # Short pause → gentle decay; long silence → aggressive drop
            decay = (
                self.MOMENTUM_DECAY
                if self.pause_frames < int(0.4 * 1000 / FRAME_DURATION)  # < 400ms
                else self.MOMENTUM_DROP
            )
            self.speech_momentum *= decay

        self.speech_momentum = max(self.speech_momentum, 0.0)

        # ----- Multi-factor confidence score ---------------------------------
        rms_score        = min(rms / 3000.0, 1.0)
        zcr_score        = 1.0 if zcr_ok else 0.0
        continuity_score = min(self.active_frames / 8.0, 1.0)
        momentum_score   = min(self.speech_momentum / self.MOMENTUM_MAX, 1.0)

        confidence = (
            rms_score        * self.W_RMS +
            zcr_score        * self.W_ZCR +
            continuity_score * self.W_CONTINUITY +
            momentum_score   * self.W_MOMENTUM
        )

        probable_speech = self.speech_momentum >= self.MOMENTUM_SPEAK_THRESHOLD

        # ----- Interruption detection (during AI speech) ---------------------
        interruption = ai_speaking and rms > self.RMS_INTERRUPT and zcr_ok

        return FrameDecision(
            probable_speech = probable_speech,
            confidence      = round(confidence, 3),
            continue_capture= probable_speech or self.speech_momentum > 0.2,
            interruption    = interruption,
            rms             = round(float(rms), 2),
            zcr             = round(float(zcr), 4),
            momentum        = round(self.speech_momentum, 3),
            active_frames   = self.active_frames,
        )

    def reset(self) -> None:
        self.speech_momentum = 0.0
        self.active_frames   = 0
        self.pause_frames    = 0


# =============================================================================
# SILERO STUB
# Replace the body of _silero_verify() with your real Silero call.
# =============================================================================

class SileroVerifier:
    """
    Placeholder for Silero VAD verification.

    Real usage:
        model, utils = torch.hub.load('snakers4/silero-vad', 'silero_vad')
        (get_speech_timestamps, ...) = utils
    """

    def verify(self, pcm_frames: List[bytes]) -> float:
        """
        Returns speech probability 0.0–1.0.
        Stub always returns 0.9 so runtime logic is exercised.
        """
        # ----- REAL IMPLEMENTATION (drop in when torch available) -----------
        # import torch
        # audio_tensor = torch.from_numpy(
        #     np.frombuffer(b"".join(pcm_frames), dtype=np.int16).astype(np.float32)
        # ) / 32768.0
        # with torch.no_grad():
        #     prob = model(audio_tensor, RATE).item()
        # return prob
        return 0.90   # stub


# =============================================================================
# CONVERSATION STATE ENGINE
# =============================================================================

class ConversationStateEngine:
    """
    High-level turn-taking and pause arbitration.

    Responsibilities:
      - maintains ConversationState
      - adaptive silence timeout (short / medium / long based on speech duration)
      - deferred finalization (waits DEFER_WINDOW before committing)
      - burst grouping (merges nearby speech bursts)
      - pause confidence accumulator
    """

    def __init__(self) -> None:
        self.state: ConversationState = ConversationState.IDLE
        self.ai_speaking: bool        = False
        self.user_interrupting: bool  = False

        # Speech timing
        self._speech_start:    Optional[float] = None
        self._speech_duration: float           = 0.0
        self._silence_start:   Optional[float] = None
        self._last_burst_end:  Optional[float] = None

        # Pause confidence accumulator (seconds of silence weighted)
        self._pause_confidence: float = 0.0

        # Deferred finalize window
        self._deferred_pending: bool  = False
        self._deferred_at:      Optional[float] = None

    # ---- External signals ---------------------------------------------------

    def notify_ai_started(self) -> None:
        self.ai_speaking      = True
        self.user_interrupting = False
        self.state            = ConversationState.AI_SPEAKING

    def notify_ai_stopped(self) -> None:
        self.ai_speaking       = False
        self.user_interrupting = False
        if self.state == ConversationState.AI_SPEAKING:
            self.state = ConversationState.LISTENING

    def notify_processing(self) -> None:
        self.state = ConversationState.PROCESSING

    def notify_idle(self) -> None:
        self.state            = ConversationState.IDLE
        self._reset_speech()

    # ---- Adaptive silence timeout -------------------------------------------

    @property
    def silence_timeout(self) -> float:
        d = self._speech_duration
        if d < 2.0:
            return SILENCE_SHORT
        elif d < 6.0:
            return SILENCE_MEDIUM
        else:
            return SILENCE_LONG

    # ---- Frame-level update -------------------------------------------------

    def update(
        self,
        decision: FrameDecision,
        webrtc_active: bool,
    ) -> str:
        """
        Feed one frame decision in.

        Returns action string:
          "capture"   — keep accumulating audio
          "finalize"  — utterance complete, send to STT
          "interrupt" — interrupt TTS immediately
          "idle"      — nothing happening
        """
        now = time.time()

        # ----- Interruption during AI speech ---------------------------------
        if self.ai_speaking and decision.interruption:
            self.user_interrupting = True
            self.state             = ConversationState.INTERRUPTION
            return "interrupt"

        # Suppress false triggers during TTS
        if self.ai_speaking and not decision.interruption:
            return "idle"

        # ----- Active speech frame -------------------------------------------
        if decision.probable_speech and webrtc_active:
            # Track burst grouping: if we were in deferred/paused and resume
            # quickly, absorb back into the same utterance
            if (
                self._last_burst_end is not None
                and (now - self._last_burst_end) < BURST_GAP
                and self.state in (
                    ConversationState.USER_PAUSED,
                    ConversationState.LISTENING,
                )
            ):
                # Resume previous utterance
                pass

            if self.state not in (ConversationState.USER_SPEAKING,):
                self.state         = ConversationState.USER_SPEAKING
                if self._speech_start is None:
                    self._speech_start = now

            # Accumulate speech duration
            self._speech_duration += FRAME_DURATION / 1000.0

            # Reset silence / pause trackers
            self._silence_start    = None
            self._pause_confidence = 0.0
            self._deferred_pending = False
            self._deferred_at      = None

            return "capture"

        # ----- Silence / pause frame -----------------------------------------
        if self.state == ConversationState.USER_SPEAKING:
            if self._silence_start is None:
                self._silence_start   = now
                self._last_burst_end  = now
                self.state            = ConversationState.USER_PAUSED

        if self.state == ConversationState.USER_PAUSED:
            elapsed = (now - self._silence_start) if self._silence_start else 0.0
            self._pause_confidence = elapsed

            # Graded pause confidence
            # < 120ms   → sub-phoneme gap, keep capturing
            # < timeout → likely hesitation
            # ≥ timeout → start deferred finalization

            if elapsed < 0.12:
                return "capture"

            if elapsed < self.silence_timeout:
                return "capture"  # probable hesitation, hold

            # ----- Deferred finalization check --------------------------------
            if not self._deferred_pending:
                self._deferred_pending = True
                self._deferred_at      = now

            deferred_elapsed = now - self._deferred_at
            if deferred_elapsed < DEFER_WINDOW:
                # Still in wait window — user might resume
                return "capture"

            # Both silence timeout AND defer window elapsed → commit finalize
            self._reset_speech()
            return "finalize"

        return "idle"

    def _reset_speech(self) -> None:
        self._speech_start     = None
        self._speech_duration  = 0.0
        self._silence_start    = None
        self._pause_confidence = 0.0
        self._deferred_pending = False
        self._deferred_at      = None
        if self.state not in (
            ConversationState.AI_SPEAKING,
            ConversationState.PROCESSING,
        ):
            self.state = ConversationState.LISTENING


# =============================================================================
# AUDIO HELPERS
# =============================================================================

def _save_wav(frames: List[bytes]) -> str:
    """Serialize PCM frames to a temp WAV file. Only called on finalized utterances."""
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    wf  = wave.open(tmp.name, "wb")
    wf.setnchannels(CHANNELS)
    wf.setsampwidth(2)   # int16
    wf.setframerate(RATE)
    wf.writeframes(b"".join(frames))
    wf.close()
    return tmp.name


def _transcribe(audio_path: str) -> str:
    """Send WAV to local Whisper server and return transcript."""
    try:
        with open(audio_path, "rb") as f:
            resp = requests.post(
                WHISPER_URL,
                files={"file": f},
                timeout=60,
            )
        if resp.status_code != 200:
            print(f"[STT] HTTP {resp.status_code}")
            return ""
        return resp.json().get("text", "").strip()
    except requests.exceptions.ConnectionError:
        print("[STT] Whisper server not reachable — is it running?")
        return ""
    except Exception as exc:
        print(f"[STT] Error: {exc}")
        return ""


# =============================================================================
# RUNTIME ENTRY POINT
# =============================================================================

def run(
    on_transcript: Optional[Callable[[str], None]] = None,
    webrtc_aggressiveness: int = 2,
) -> None:
    """
    Main audio capture and processing loop.

    Args:
        on_transcript:         Callback fired with the final transcript string.
                               When None, transcripts are printed to stdout.
        webrtc_aggressiveness: 0 (permissive) – 3 (aggressive).
    """

    print("\n[PANDORA AUDIO RUNTIME]")
    print(f"  Rust audio runtime : {'ACTIVE' if RUST_CORE_AVAILABLE else 'PYTHON MOCK (future: rust_core)'}")
    print(f"  Whisper endpoint   : {WHISPER_URL}")
    print(f"  Sample rate        : {RATE} Hz  |  Frame : {FRAME_DURATION}ms")
    print(f"  WebRTC aggressiveness: {webrtc_aggressiveness}")
    print("\n  Speak naturally. CTRL-C to stop.\n")

    # ---- Component init --------------------------------------------------------
    vad             = webrtcvad.Vad(webrtc_aggressiveness)
    conv_runtime    = ConversationalRuntime()
    state_engine    = ConversationStateEngine()
    silero          = SileroVerifier()

    state_engine.state = ConversationState.LISTENING

    pa = pyaudio.PyAudio()
    stream = pa.open(
        format             = pyaudio.paInt16,
        channels           = CHANNELS,
        rate               = RATE,
        input              = True,
        frames_per_buffer  = FRAME_SIZE,
    )

    # Speech accumulator — kept in memory until finalized
    speech_frames: List[bytes] = []
    # Pre-roll: keep a small ring buffer to capture onset frames before detection fires
    pre_roll: collections.deque = collections.deque(
        maxlen=int(0.25 * 1000 / FRAME_DURATION)  # 250 ms
    )

    try:
        while True:
            frame = stream.read(FRAME_SIZE, exception_on_overflow=False)

            # Ensure frame is exactly the right length for webrtcvad
            if len(frame) < BYTES_PER_FRAME:
                frame = frame.ljust(BYTES_PER_FRAME, b"\x00")
            elif len(frame) > BYTES_PER_FRAME:
                frame = frame[:BYTES_PER_FRAME]

            # ---- WebRTC fast gate ------------------------------------------
            try:
                webrtc_active = vad.is_speech(frame, RATE)
            except Exception:
                webrtc_active = False

            # ---- Heuristic runtime (Python or Rust) ------------------------
            if RUST_CORE_AVAILABLE:
                # Future: decision = rust_core.process_audio_frame(frame)
                # (rust_core returns a dict matching FrameDecision fields)
                raw = rust_core.process_audio_frame(frame)   # type: ignore
                decision = FrameDecision(**raw) if isinstance(raw, dict) else raw
            else:
                decision = conv_runtime.process_frame(
                    frame,
                    ai_speaking=state_engine.ai_speaking,
                )

            # ---- Conditional Silero (ambiguous confidence band) ------------
            silero_confirmed = True  # default: trust heuristics
            if SILERO_AMBIG_LO <= decision.confidence <= SILERO_AMBIG_HI:
                # Only run Silero on the accumulated pre-roll to keep latency low
                silero_prob      = silero.verify(list(pre_roll) + [frame])
                silero_confirmed = silero_prob >= 0.55
                decision.probable_speech = decision.probable_speech and silero_confirmed

            # ---- Pre-roll ring buffer --------------------------------------
            pre_roll.append(frame)

            # ---- Conversation state engine ---------------------------------
            action = state_engine.update(decision, webrtc_active)

            # ---- Live debug line -------------------------------------------
            state_label = state_engine.state.value.upper()[:8]
            print(
                f"\r"
                f"[{state_label:<8}] "
                f"RMS={decision.rms:>7.1f} "
                f"ZCR={decision.zcr:.4f} "
                f"MOM={decision.momentum:.3f} "
                f"CONF={decision.confidence:.3f} "
                f"ACT={decision.active_frames:>2} "
                f"| {action.upper():<8}",
                end="",
                flush=True,
            )

            # ---- Action dispatch -------------------------------------------

            if action == "capture":
                if not speech_frames:
                    # Prepend pre-roll so we don't clip onset
                    speech_frames.extend(pre_roll)
                    print(f"\n\n  ▶ Speech started ({state_engine.state.value})\n")
                speech_frames.append(frame)

            elif action == "interrupt":
                print("\n\n  ⚡ INTERRUPTION DETECTED — stopping TTS\n")
                # Caller is responsible for stopping TTS via on_transcript
                # or a separate TTS-stop callback.
                # Clear buffer and treat as new utterance start.
                speech_frames.clear()
                speech_frames.extend(pre_roll)
                speech_frames.append(frame)

            elif action == "finalize":
                if len(speech_frames) < int(0.4 * 1000 / FRAME_DURATION):
                    # Too short — likely noise burst, discard
                    speech_frames.clear()
                    print("\n  [Discard: too short]\n")
                    continue

                print(f"\n  ◼ Utterance finalized — {len(speech_frames)} frames "
                      f"({len(speech_frames) * FRAME_DURATION / 1000:.2f}s)")

                # --- WAV serialization (only on finalized utterances) --------
                audio_path = _save_wav(speech_frames)
                speech_frames.clear()

                # --- STT -----------------------------------------------------
                state_engine.notify_processing()
                print("  ↳ Transcribing…")

                text = _transcribe(audio_path)

                try:
                    os.remove(audio_path)
                except OSError:
                    pass

                state_engine.notify_idle()
                state_engine.state = ConversationState.LISTENING

                # Reset momentum so fresh utterance starts clean
                conv_runtime.reset()

                print(f"\n{'─'*60}")
                print(f"  TRANSCRIPT: {text if text else '(empty)'}")
                print(f"{'─'*60}\n")

                if text:
                    if on_transcript:
                        on_transcript(text)
                    # else: printed above

            # action == "idle": nothing to do

    except KeyboardInterrupt:
        print("\n\n[RUNTIME STOPPED]\n")

    finally:
        stream.stop_stream()
        stream.close()
        pa.terminate()


# =============================================================================
# DEMO ENTRY
# =============================================================================

if __name__ == "__main__":
    def _handle_transcript(text: str) -> None:
        """
        Drop-in intent hook.
        Replace with:
            intent = rust_core.process(text)
            executor.dispatch(intent)
        """
        print(f"[INTENT HOOK] → '{text}'")

    run(on_transcript=_handle_transcript)