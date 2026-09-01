import os
import time
import wave
import numpy as np
import tempfile
import requests
import threading
import collections

import pyaudio
import webrtcvad


# =========================================================
# CONFIG
# =========================================================

WHISPER_URL = "http://127.0.0.1:8177/inference"

RATE = 16000
CHANNELS = 1
FRAME_DURATION = 30
FRAME_SIZE = int(RATE * FRAME_DURATION / 1000)

SILENCE_TIMEOUT = 1.2

vad = webrtcvad.Vad(2)


# =========================================================
# MOCK RUST HEURISTIC RUNTIME
# (simulates future rust conversational runtime)
# =========================================================

class MockRustRuntime:

    def __init__(self):

        self.speech_momentum = 0.0
        self.last_active = time.time()

        self.active_frames = 0

        # ============================================
        # TUNING
        # ============================================

        self.rms_threshold = 1000
        self.zcr_min = 0.02
        self.zcr_max = 0.25

    # =================================================
    # ZERO CROSSING RATE
    # =================================================

    def zero_crossing_rate(self, samples):

        zero_crossings = np.sum(
            np.abs(
                np.diff(
                    np.sign(samples)
                )
            )
        )

        return zero_crossings / len(samples)

    # =================================================
    # MAIN FRAME PROCESSOR
    # =================================================

    def process_frame(self, pcm: bytes):

        # ============================================
        # PCM → NUMPY
        # ============================================

        samples = np.frombuffer(
            pcm,
            dtype=np.int16
        ).astype(np.float32)

        # ============================================
        # RMS
        # ============================================

        rms = np.sqrt(
            np.mean(samples ** 2)
        )

        # ============================================
        # ZCR
        # ============================================

        zcr = self.zero_crossing_rate(samples)

        # ============================================
        # SPEECH HEURISTIC
        # ============================================

        rms_active = rms > self.rms_threshold

        zcr_active = (
            self.zcr_min <= zcr <= self.zcr_max
        )

        active = rms_active and zcr_active

        # ============================================
        # STABILITY FRAMES
        # ============================================

        if active:
            self.active_frames += 1
        else:
            self.active_frames = 0

        # ============================================
        # MOMENTUM BUILDUP
        # ============================================

        if self.active_frames >= 3:

            self.speech_momentum += 0.25

            self.last_active = time.time()

        else:

            self.speech_momentum *= 0.75

        # ============================================
        # CLAMP
        # ============================================

        self.speech_momentum = min(
            self.speech_momentum,
            5.0
        )

        # ============================================
        # FINAL DECISION
        # ============================================

        probable_speech = (
            self.speech_momentum > 0.6
        )

        return {

            "probable_speech": probable_speech,

            "rms": round(float(rms), 2),

            "zcr": round(float(zcr), 4),

            "momentum": round(
                self.speech_momentum,
                2
            ),

            "active_frames": self.active_frames
        }

rust_runtime = MockRustRuntime()


# =========================================================
# AUDIO HELPERS
# =========================================================

def save_wav(frames):

    temp = tempfile.NamedTemporaryFile(
        suffix=".wav",
        delete=False
    )

    wf = wave.open(temp.name, "wb")

    wf.setnchannels(CHANNELS)
    wf.setsampwidth(2)
    wf.setframerate(RATE)

    wf.writeframes(b"".join(frames))
    wf.close()

    return temp.name


def transcribe(audio_path):

    with open(audio_path, "rb") as audio:

        files = {
            "file": audio
        }

        response = requests.post(
            WHISPER_URL,
            files=files,
            timeout=60
        )

    if response.status_code != 200:
        print("STT ERROR")
        return ""

    data = response.json()

    return data.get("text", "").strip()


# =========================================================
# MAIN RUNTIME
# =========================================================

def run():

    print("\n[PANDORA TEST RUNTIME STARTED]")
    print("[Speak naturally]")
    print()

    pa = pyaudio.PyAudio()

    stream = pa.open(
        format=pyaudio.paInt16,
        channels=CHANNELS,
        rate=RATE,
        input=True,
        frames_per_buffer=FRAME_SIZE
    )

    speaking = False

    speech_frames = []

    silence_start = None

    try:

        while True:

            frame = stream.read(
                FRAME_SIZE,
                exception_on_overflow=False
            )

            # =================================================
            # WEBRTC FAST GATE
            # =================================================

            webrtc_active = vad.is_speech(frame, RATE)

            # =================================================
            # MOCK RUST RUNTIME
            # =================================================

            rust = rust_runtime.process_frame(frame)

            probable_speech = (
                webrtc_active
                and rust["probable_speech"]
            )

            print(
                f"\r"
                f"RMS={rust['rms']} | "
                f"ZCR={rust['zcr']} | "
                f"MOM={rust['momentum']} | "
                f"FRAMES={rust['active_frames']} | "
                f"SPEECH={probable_speech}",
                end=""
            )

            # =================================================
            # SPEECH START
            # =================================================

            if probable_speech:

                if not speaking:
                    print("\n\n[Speech started]\n")

                    speaking = True
                    silence_start = None

                speech_frames.append(frame)

            # =================================================
            # SILENCE LOGIC
            # =================================================

            else:

                if speaking:

                    if silence_start is None:
                        silence_start = time.time()

                    elapsed = (
                        time.time() - silence_start
                    )

                    if elapsed >= SILENCE_TIMEOUT:

                        print("\n[Finalizing utterance...]")

                        speaking = False
                        silence_start = None

                        # =============================
                        # SAVE AUDIO
                        # =============================

                        audio_path = save_wav(
                            speech_frames
                        )

                        speech_frames.clear()

                        # =============================
                        # STT
                        # =============================

                        text = transcribe(audio_path)

                        os.remove(audio_path)

                        print("\n====================")
                        print("TRANSCRIPT:")
                        print(text)
                        print("====================\n")

    except KeyboardInterrupt:

        print("\n\n[Stopping runtime]")

    finally:

        stream.stop_stream()
        stream.close()

        pa.terminate()


# =========================================================
# ENTRY
# =========================================================

if __name__ == "__main__":
    run()