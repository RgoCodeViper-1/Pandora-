"""
test_stt_pipeline.py
════════════════════
Standalone integration test for the Whisper STT → Intent → Executor pipeline.

Bypasses the Node WebSocket dependency entirely so you can validate the full
Python-side pipeline without Node running.

Tests in order:
  1. Whisper server reachability (HTTP ping)
  2. WhisperSTT.transcribe() with a generated WAV tone
  3. WhisperSTT.transcribe_wav() with a real WAV file (if provided)
  4. Full pipeline: WAV → Whisper → pandora_core → Executor
  5. VADService smoke-test: can it open the mic without a WS connection

Run:
    python test_stt_pipeline.py
    python test_stt_pipeline.py --wav path/to/utterance.wav
"""

from __future__ import annotations

import argparse
import json
import math
import os
import struct
import sys
import tempfile
import time
import wave
from pathlib import Path

# ── Colour helpers ────────────────────────────────────────────────────────────

class C:
    RESET   = "\033[0m"
    RED     = "\033[91m"
    GREEN   = "\033[92m"
    YELLOW  = "\033[93m"
    CYAN    = "\033[96m"
    MAGENTA = "\033[95m"
    BLUE    = "\033[94m"


def log(label: str, msg: str, color: str = C.BLUE) -> None:
    print(f"{color}[{label}]{C.RESET} {msg}")


def ok(msg: str)   -> None: log("  PASS", msg, C.GREEN)
def fail(msg: str) -> None: log("  FAIL", msg, C.RED)
def info(msg: str) -> None: log("  INFO", msg, C.CYAN)
def warn(msg: str) -> None: log("  WARN", msg, C.YELLOW)
def section(title: str) -> None:
    print(f"\n{C.MAGENTA}{'─' * 60}")
    print(f"  {title}")
    print(f"{'─' * 60}{C.RESET}")


# ── Path bootstrap ────────────────────────────────────────────────────────────
# Mirrors pandora_launcher.py path setup so imports resolve the same way.

HERE = Path(__file__).resolve().parent          # python/tests/
PYTHON_ROOT = HERE.parent                       # python/
PROJECT_ROOT = PYTHON_ROOT.parent               # PANDORA/

for p in [str(PYTHON_ROOT), str(PROJECT_ROOT)]:
    if p not in sys.path:
        sys.path.insert(0, p)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def make_sine_wav(
    path: str,
    duration_s: float = 1.5,
    freq_hz: float = 440.0,
    sample_rate: int = 16000,
) -> str:
    """
    Write a pure sine-wave WAV to *path*.
    Whisper will return empty/noise for a pure tone — that is expected and
    correct. We use this only to verify the HTTP round-trip works.
    """
    n_samples = int(sample_rate * duration_s)
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        frames = struct.pack(
            f"<{n_samples}h",
            *[int(32767 * math.sin(2 * math.pi * freq_hz * i / sample_rate))
              for i in range(n_samples)],
        )
        wf.writeframes(frames)
    return path


def make_silence_wav(
    path: str,
    duration_s: float = 1.0,
    sample_rate: int = 16000,
) -> str:
    n_samples = int(sample_rate * duration_s)
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(b"\x00" * n_samples * 2)
    return path


# ─────────────────────────────────────────────────────────────────────────────
# Test 1 — Whisper server reachability
# ─────────────────────────────────────────────────────────────────────────────

def test_whisper_server_reachable() -> bool:
    section("TEST 1 — Whisper.cpp server reachability")

    try:
        import requests
    except ImportError:
        fail("'requests' not installed — pip install requests")
        return False

    # Mirror the host from stt.py
    host = "http://127.0.0.1:8177/inference"
    info(f"Pinging {host} ...")

    try:
        # POST with no file → expect 4xx, but a response means the server is up
        r = requests.post(host, timeout=3)
        info(f"Server responded: HTTP {r.status_code}")
        ok("Whisper server is reachable")
        return True
    except requests.exceptions.ConnectionError:
        fail(
            "Connection refused — whisper-server.exe is NOT running.\n"
            f"         Start it from: PANDORA/native/whisper_cpp/whisper-server.exe\n"
            f"         Typical command: whisper-server.exe -m models/ggml-base.en.bin --port 8177"
        )
        return False
    except requests.exceptions.Timeout:
        fail("Connection timed out — server may be starting up, try again.")
        return False
    except Exception as exc:
        fail(f"Unexpected error: {exc}")
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Test 2 — WhisperSTT import and instantiation
# ─────────────────────────────────────────────────────────────────────────────

def test_whisper_stt_import() -> tuple[bool, object]:
    section("TEST 2 — WhisperSTT import and instantiation")

    try:
        from speech.stt import WhisperSTT, WHISPER_AVAILABLE, WHISPER_SERVER
        info(f"WHISPER_AVAILABLE = {WHISPER_AVAILABLE}")
        info(f"WHISPER_SERVER    = {WHISPER_SERVER}")

        if not WHISPER_AVAILABLE:
            warn(
                "whisper-server.exe not found at expected path.\n"
                f"         Expected: {WHISPER_SERVER}\n"
                "         WhisperSTT will still try to POST — server must be running."
            )
        else:
            ok("whisper-server.exe detected on disk")

        stt = WhisperSTT()
        ok("WhisperSTT instantiated successfully")
        return True, stt

    except ImportError as exc:
        fail(f"Import failed: {exc}")
        return False, None
    except Exception as exc:
        fail(f"Instantiation failed: {exc}")
        return False, None


# ─────────────────────────────────────────────────────────────────────────────
# Test 3 — WhisperSTT.transcribe_wav() with sine tone
# ─────────────────────────────────────────────────────────────────────────────

def test_whisper_transcribe_sine(stt) -> bool:
    section("TEST 3 — WhisperSTT.transcribe_wav() — sine tone (HTTP round-trip)")

    fd, wav_path = tempfile.mkstemp(suffix=".wav", prefix="pandora_test_sine_")
    os.close(fd)

    try:
        make_sine_wav(wav_path, duration_s=1.5)
        info(f"Generated sine WAV: {wav_path}")

        t0 = time.perf_counter()
        text = stt.transcribe_wav(wav_path)
        elapsed = time.perf_counter() - t0

        info(f"Whisper response ({elapsed:.2f}s): {text!r}")

        # A pure tone returns empty string or noise — both are valid
        # What matters is the call completed without exception
        ok(f"transcribe_wav() completed in {elapsed:.2f}s (text={text!r})")
        return True

    except Exception as exc:
        fail(f"transcribe_wav() raised: {exc}")
        return False
    finally:
        try:
            os.unlink(wav_path)
        except OSError:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# Test 4 — WhisperSTT.transcribe_wav() with real speech WAV
# ─────────────────────────────────────────────────────────────────────────────

def test_whisper_transcribe_real(stt, wav_path: str) -> bool:
    section(f"TEST 4 — WhisperSTT.transcribe_wav() — real speech WAV")
    info(f"WAV file: {wav_path}")

    if not Path(wav_path).exists():
        fail(f"File not found: {wav_path}")
        return False

    try:
        t0 = time.perf_counter()
        text = stt.transcribe_wav(wav_path)
        elapsed = time.perf_counter() - t0

        if text:
            ok(f"Transcription ({elapsed:.2f}s): {text!r}")
        else:
            warn(f"Empty transcription ({elapsed:.2f}s) — check audio quality or model")

        return True

    except Exception as exc:
        fail(f"transcribe_wav() raised: {exc}")
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Test 5 — Full pipeline: WAV → Whisper → pandora_core → Executor
# ─────────────────────────────────────────────────────────────────────────────

def test_full_pipeline(stt, wav_path: str | None) -> bool:
    section("TEST 5 — Full pipeline: Whisper → pandora_core → Executor")

    # ── pandora_core ──────────────────────────────────────────────────────────
    try:
        import pandora_core
        info(f"pandora_core loaded — {pandora_core.pattern_count()} patterns")
    except ImportError as exc:
        fail(f"pandora_core not importable: {exc}")
        return False

    # ── Executor ──────────────────────────────────────────────────────────────
    try:
        from core.executor import Executor
        executor = Executor()
        ok("Executor instantiated")
    except Exception as exc:
        fail(f"Executor import/init failed: {exc}")
        return False

    # ── Get transcription text ────────────────────────────────────────────────
    if wav_path and Path(wav_path).exists():
        info("Using provided WAV for pipeline test...")
        text = stt.transcribe_wav(wav_path)
        if not text:
            warn("Whisper returned empty — using fallback phrase 'pandora shutdown'")
            text = "pandora shutdown"
    else:
        # No real WAV — use a known phrase to exercise the rest of the pipeline
        text = "pandora shutdown"
        warn(f"No real WAV provided — injecting text directly: {text!r}")

    info(f"Input text: {text!r}")

    # ── Intent classification ─────────────────────────────────────────────────
    try:
        raw    = pandora_core.process_stream(text)
        result = json.loads(raw)

        # Normalise batch vs single
        if "intents" in result and result["intents"]:
            result = result["intents"][0]

        info(
            f"Intent: {result.get('intent')}  "
            f"confidence={result.get('confidence', 0):.2f}  "
            f"entities={result.get('entities', {})}"
        )
        ok("pandora_core.process_stream() succeeded")
    except Exception as exc:
        fail(f"pandora_core failed: {exc}")
        return False

    # ── Executor dispatch ─────────────────────────────────────────────────────
    try:
        response = executor.execute(result)
        if response:
            ok(f"Executor response: {response!r}")
        else:
            warn("Executor returned None/empty — intent may not have a handler yet")
        return True
    except Exception as exc:
        fail(f"Executor.execute() raised: {exc}")
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Test 6 — VADService mic open (no WebSocket required)
# ─────────────────────────────────────────────────────────────────────────────

def test_vad_mic_open() -> bool:
    section("TEST 6 — VADService mic open (no WebSocket)")

    try:
        import sounddevice as sd
    except ImportError:
        warn("sounddevice not installed — skipping mic test")
        return True  # not a hard failure

    try:
        import webrtcvad
    except ImportError:
        warn("webrtcvad not installed — skipping mic test")
        return True

    # Patch VADService to skip the WS connect and run the mic for 2 seconds
    try:
        from vad.vad_service import VADService, DEFAULT_CFG
        import asyncio
        import queue

        captured_frames: list[bytes] = []
        frame_q: queue.Queue = queue.Queue()

        sample_rate = 16000
        frame_size  = int(sample_rate * 30 / 1000)   # 30 ms

        def mic_callback(indata, frames, time_info, status):
            pcm = (indata[:, 0] * 32767).astype("int16").tobytes()
            frame_q.put(pcm)

        info("Opening mic for 2 seconds...")
        with sd.InputStream(
            samplerate=sample_rate,
            channels=1,
            dtype="int16",
            blocksize=frame_size,
            callback=mic_callback,
        ):
            time.sleep(2.0)

        while not frame_q.empty():
            captured_frames.append(frame_q.get_nowait())

        total_bytes = sum(len(f) for f in captured_frames)
        duration_s  = total_bytes / 2 / sample_rate

        ok(f"Mic captured {len(captured_frames)} frames ({duration_s:.2f}s of audio)")

        # Basic sanity: did we actually get audio data?
        if captured_frames:
            import struct
            first = captured_frames[0]
            samples = struct.unpack(f"<{len(first)//2}h", first)
            max_amp = max(abs(s) for s in samples)
            info(f"Peak amplitude in first frame: {max_amp} (0 = silence / mic not working)")
            if max_amp == 0:
                warn("All-zero audio — mic may be muted or wrong device selected")
            else:
                ok("Non-zero audio detected — mic is live")

        return True

    except Exception as exc:
        fail(f"Mic open failed: {exc}")
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Test 7 — VADService WS dependency diagnosis
# ─────────────────────────────────────────────────────────────────────────────

def test_vad_ws_dependency() -> None:
    section("TEST 7 — VADService WebSocket dependency diagnosis")

    info(
        "VADService.start() calls _connect_and_run() which blocks until\n"
        "         ws://localhost:8765 accepts a connection.\n"
        "         If Node is not running it retries silently: 2s→4s→8s→…→30s\n"
        "         This is why the launcher appears to hang with no output."
    )

    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(1)
    try:
        s.connect(("localhost", 8765))
        ok("Port 8765 is open — Node WS server appears to be running")
    except (ConnectionRefusedError, TimeoutError, OSError):
        warn(
            "Port 8765 is closed — Node orchestrator is NOT running.\n"
            "         VADService will silently retry the WS connection and the\n"
            "         mic will never open until Node starts.\n"
            "         For standalone Python testing use api_server.py instead:\n"
            "           python python/services/api_server.py"
        )
    finally:
        s.close()


# ─────────────────────────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────────────────────────

def print_summary(results: dict[str, bool | None]) -> None:
    section("SUMMARY")
    passed = sum(1 for v in results.values() if v is True)
    failed = sum(1 for v in results.values() if v is False)
    skipped = sum(1 for v in results.values() if v is None)

    for name, result in results.items():
        if result is True:
            ok(name)
        elif result is False:
            fail(name)
        else:
            warn(f"{name} (skipped)")

    print()
    color = C.GREEN if failed == 0 else C.RED
    print(
        f"{color}  {passed} passed  {failed} failed  {skipped} skipped{C.RESET}\n"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Pandora STT + pipeline integration test"
    )
    parser.add_argument(
        "--wav",
        metavar="PATH",
        help="Path to a real speech WAV file for transcription tests (optional)",
        default=None,
    )
    args = parser.parse_args()

    print(f"\n{C.CYAN}Pandora STT Pipeline Integration Test{C.RESET}")
    print(f"{C.CYAN}Python {sys.version.split()[0]}  |  {Path.cwd()}{C.RESET}\n")

    results: dict[str, bool | None] = {}

    # 1 — Server reachable
    server_ok = test_whisper_server_reachable()
    results["Whisper server reachable"] = server_ok

    # 2 — Import
    import_ok, stt = test_whisper_stt_import()
    results["WhisperSTT import"] = import_ok

    if import_ok and stt is not None:
        # 3 — Sine round-trip (only meaningful if server is up)
        if server_ok:
            results["transcribe_wav (sine tone)"] = test_whisper_transcribe_sine(stt)
        else:
            warn("Skipping sine transcription — server not reachable")
            results["transcribe_wav (sine tone)"] = None

        # 4 — Real WAV (optional)
        if args.wav:
            if server_ok:
                results["transcribe_wav (real speech)"] = test_whisper_transcribe_real(stt, args.wav)
            else:
                warn("Skipping real WAV transcription — server not reachable")
                results["transcribe_wav (real speech)"] = None
        else:
            info("No --wav file provided — skipping real speech transcription test")
            results["transcribe_wav (real speech)"] = None

        # 5 — Full pipeline
        results["Full pipeline (Whisper→Intent→Executor)"] = test_full_pipeline(
            stt, args.wav
        )
    else:
        results["transcribe_wav (sine tone)"]          = None
        results["transcribe_wav (real speech)"]        = None
        results["Full pipeline (Whisper→Intent→Executor)"] = None

    # 6 — Mic open
    results["Mic open (no WS)"] = test_vad_mic_open()

    # 7 — WS diagnosis (informational, not pass/fail)
    test_vad_ws_dependency()

    print_summary(results)


if __name__ == "__main__":
    main()
