"""
nvidia_voice_client.py
─────────────────────────────────────────────────────────────────────────────
Voice-driven client for NVIDIA-hosted google/gemma-4-31b-it.

Pipeline
  Microphone → SpeechRecognition → NVIDIA API (SSE stream) → edge-tts → speaker

Architecture mirrors Jarvis_v1_7r_core.py:
  • listen()     – same calibration + recognition stack
  • speak()      – same edge-tts async path with playsound fallback
  • chat()       – streams SSE chunks, buffers sentences, fires TTS per sentence
  • main_loop()  – wake-word guard, sleep/quit commands, continuous listen

Requirements (pip install):
  speechrecognition pyaudio edge-tts playsound requests colorama
  (pyaudio may need portaudio: brew install portaudio / apt install portaudio19-dev)
─────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import tempfile
import time
import threading
from typing import Generator

import requests
from colorama import Fore, Style, init as colorama_init

# ── Optional imports (mirror Jarvis guard pattern) ───────────────────────────
try:
    import speech_recognition as sr
    VOICE_AVAILABLE = True
except ImportError:
    VOICE_AVAILABLE = False
    print("[WARN] speech_recognition not installed – falling back to text input.")

try:
    import edge_tts
    EDGE_TTS_AVAILABLE = True
except ImportError:
    EDGE_TTS_AVAILABLE = False
    print("[WARN] edge-tts not installed – TTS disabled.")

try:
    import playsound
    PLAYSOUND_AVAILABLE = True
except ImportError:
    PLAYSOUND_AVAILABLE = False

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG  (edit here or move to config.json following Jarvis pattern)
# ─────────────────────────────────────────────────────────────────────────────
NVIDIA_API_KEY  = "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"
INVOKE_URL      = "https://integrate.api.nvidia.com/v1/chat/completions"  
MODEL           = "google/gemma-4-31b-it" 
MAX_TOKENS      = 16384
TEMPERATURE     = 1.00
TOP_P           = 0.95
ENABLE_THINKING = True          # Gemma-4 extended thinking

# Voice settings (same keys as Jarvis SETTINGS)
WAKE_WORD            = "pandora"   # change to your preferred wake word
VOICE_ID             = "en-US-EricNeural"
VOICE_RATE           = "+10%"
VOICE_PITCH          = "+0Hz"
VOICE_TIMEOUT        = 5.0        # seconds to wait for speech start
PHRASE_TIME_LIMIT    = 20         # max seconds per utterance
PAUSE_THRESHOLD      = 1.3
ENERGY_THRESHOLD     = 1000
LANGUAGE             = "en-us"
ACCESSIBILITY_MODE   = False      # True → text input only

# System prompt – Jarvis persona (mirrors Jarvis chat_with_* functions)
SYSTEM_PROMPT = (
    "You are JARVIS, Tony Stark's AI assistant. Your persona is polite, witty, "
    "and exceptionally intelligent with a formal British tone. "
    "Always address the user as 'Sir'. Keep answers concise and never use emoji "
    "while answering any queries or generating a response. "
    "You are proactive, efficient, and occasionally humorous."
)

# ─────────────────────────────────────────────────────────────────────────────
# GLOBALS
# ─────────────────────────────────────────────────────────────────────────────
colorama_init(autoreset=True)
is_muted    = False
chat_history: list[dict] = []


# ─────────────────────────────────────────────────────────────────────────────
# TTS  (mirrors Jarvis speak() with edge-tts primary path)
# ─────────────────────────────────────────────────────────────────────────────
def speak(text: str, allow_interrupt: bool = True) -> None:
    """
    Speak text via edge-tts → temp mp3 → playsound.
    Falls back to print-only if TTS not available.
    Mirrors Jarvis speak() structure exactly.
    """
    global is_muted

    text = text.strip()
    if not text:
        return

    print(f"{Fore.CYAN}Pandora:{Style.RESET_ALL} {text}")

    if is_muted or ACCESSIBILITY_MODE:
        return

    if not EDGE_TTS_AVAILABLE:
        return

    async def _tts_task() -> None:
        tmp_path = tempfile.mktemp(suffix=".mp3")
        try:
            communicate = edge_tts.Communicate(
                text, voice=VOICE_ID, rate=VOICE_RATE, pitch=VOICE_PITCH
            )
            await communicate.save(tmp_path)

            if PLAYSOUND_AVAILABLE:
                playsound.playsound(tmp_path, block=True)
            else:
                # pygame fallback (mirrors Jarvis)
                try:
                    import pygame
                    pygame.mixer.init()
                    pygame.mixer.music.load(tmp_path)
                    pygame.mixer.music.play()
                    while pygame.mixer.music.get_busy():
                        if is_muted:
                            pygame.mixer.music.stop()
                            break
                        pygame.time.Clock().tick(10)
                except Exception as pg_err:
                    print(f"[TTS Playback Error] {pg_err}")
        finally:
            try:
                os.remove(tmp_path)
            except OSError:
                pass

    try:
        asyncio.run(_tts_task())
    except Exception as e:
        print(f"[Edge-TTS Error] {e}")


# ─────────────────────────────────────────────────────────────────────────────
# STT  (mirrors Jarvis listen())
# ─────────────────────────────────────────────────────────────────────────────
def listen() -> str:
    """
    Capture one utterance from the microphone.
    Falls back to input() when voice is unavailable or ACCESSIBILITY_MODE is on.
    Mirrors Jarvis listen() parameter handling exactly.
    """
    if ACCESSIBILITY_MODE or not VOICE_AVAILABLE:
        try:
            return input("You: ").lower().strip()
        except (EOFError, KeyboardInterrupt):
            return ""

    r = sr.Recognizer()
    r.pause_threshold      = PAUSE_THRESHOLD
    r.energy_threshold     = ENERGY_THRESHOLD
    r.dynamic_energy_threshold = True

    # Microphone selection (mirrors Jarvis)
    mic_index = None
    try:
        mic_names = sr.Microphone.list_microphone_names()
        for i, name in enumerate(mic_names):
            if "microphone" in (name or "").lower() or "default" in (name or "").lower():
                mic_index = i
                break
        if mic_index is None and mic_names:
            mic_index = 0
    except Exception:
        mic_index = None

    try:
        mic = sr.Microphone(device_index=mic_index) if mic_index is not None else sr.Microphone()
    except Exception:
        print("[Mic] No working microphone detected.")
        return ""

    with mic as source:
        try:
            print(f"{Fore.YELLOW}[Listening...]{Style.RESET_ALL}")
            audio = r.listen(
                source,
                timeout=VOICE_TIMEOUT,
                phrase_time_limit=PHRASE_TIME_LIMIT,
            )
            print(f"{Fore.YELLOW}[Recognizing...]{Style.RESET_ALL}")
            query = r.recognize_google(audio, language=LANGUAGE).lower().strip()
            if query:
                print(f"{Fore.GREEN}You:{Style.RESET_ALL} {query}")
                return query
            return ""
        except sr.UnknownValueError:
            time.sleep(0.4)
            return ""
        except sr.RequestError as e:
            print(f"[STT RequestError] {e}")
            time.sleep(2)
            return ""
        except sr.WaitTimeoutError:
            time.sleep(0.3)
            return ""
        except Exception:
            time.sleep(0.5)
            return ""


# ─────────────────────────────────────────────────────────────────────────────
# NVIDIA SSE STREAMING  (mirrors Jarvis chat_with_openrouter() pattern)
# ─────────────────────────────────────────────────────────────────────────────
def _build_messages() -> list[dict]:
    """Prepend system prompt and return full message list."""
    system_msg = {"role": "system", "content": SYSTEM_PROMPT}
    # Trim history to last 10 exchanges to stay within context (mirrors Jarvis trimmed_history)
    trimmed = chat_history[-20:] if len(chat_history) > 20 else chat_history
    return [system_msg] + trimmed


def _stream_nvidia(user_text: str) -> Generator[str, None, None]:
    """
    Call NVIDIA API with SSE streaming enabled.
    Yields incremental text chunks as they arrive.
    Architecture matches NVIDIA's own example + Jarvis requests.post pattern.
    """
    headers = {
        "Authorization": f"Bearer {NVIDIA_API_KEY}",
        "Accept":        "text/event-stream",
        "Content-Type":  "application/json",
    }

    # Append user turn before sending
    chat_history.append({"role": "user", "content": user_text})

    payload = {
        "model":       MODEL,
        "messages":    _build_messages(),
        "max_tokens":  MAX_TOKENS,
        "temperature": TEMPERATURE,
        "top_p":       TOP_P,
        "stream":      True,
        "chat_template_kwargs": {"enable_thinking": ENABLE_THINKING},
    }

    try:
        response = requests.post(
            INVOKE_URL,
            headers=headers,
            json=payload,
            stream=True,
            timeout=60,
        )
        response.raise_for_status()

        for raw_line in response.iter_lines():
            if not raw_line:
                continue
            line = raw_line.decode("utf-8")
            if line.startswith("data: "):
                data_str = line[6:].strip()
                if data_str == "[DONE]":
                    break
                try:
                    chunk = json.loads(data_str)
                    delta = chunk["choices"][0].get("delta", {})
                    token = delta.get("content") or ""
                    if token:
                        yield token
                except (json.JSONDecodeError, KeyError, IndexError):
                    continue

    except requests.exceptions.RequestException as e:
        speak(f"I'm afraid there is a network issue, Sir. Unable to reach the AI service. {e}")


# ─────────────────────────────────────────────────────────────────────────────
# SENTENCE SPLITTER  (needed for streaming TTS hand-off)
# ─────────────────────────────────────────────────────────────────────────────
_SENTENCE_END = re.compile(r'(?<=[.!?])\s+')

def _flush_sentence(buf: str) -> tuple[list[str], str]:
    """
    Split buffer at sentence boundaries.
    Returns (list_of_complete_sentences, remainder_buffer).
    """
    parts = _SENTENCE_END.split(buf)
    if len(parts) <= 1:
        return [], buf
    # last element is the incomplete sentence tail
    return parts[:-1], parts[-1]


# ─────────────────────────────────────────────────────────────────────────────
# MAIN CHAT FUNCTION  (streaming + sentence-paced TTS)
# ─────────────────────────────────────────────────────────────────────────────
def chat(user_text: str) -> str:
    """
    Stream LLM response, print tokens as they arrive, and speak sentence-by-sentence.
    Mirrors Jarvis architecture: collect → speak per sentence → store history.
    """
    print(f"\n{Fore.MAGENTA}[Streaming response...]{Style.RESET_ALL}")

    full_response = ""
    buffer        = ""
    tts_queue: list[str] = []
    tts_lock  = threading.Lock()
    tts_done  = threading.Event()

    # Background TTS worker – speaks sentences sequentially
    def _tts_worker():
        while not tts_done.is_set() or tts_queue:
            sentence = None
            with tts_lock:
                if tts_queue:
                    sentence = tts_queue.pop(0)
            if sentence:
                speak(sentence)
            else:
                time.sleep(0.05)

    tts_thread = threading.Thread(target=_tts_worker, daemon=True)
    tts_thread.start()

    # Stream tokens from NVIDIA API
    sys.stdout.write(f"{Fore.CYAN}Pandora: {Style.RESET_ALL}")
    sys.stdout.flush()

    for token in _stream_nvidia(user_text):
        sys.stdout.write(token)
        sys.stdout.flush()
        full_response += token
        buffer        += token

        # Check if we have complete sentences ready for TTS
        sentences, buffer = _flush_sentence(buffer)
        if sentences:
            with tts_lock:
                tts_queue.extend(sentences)

    print()  # newline after streamed output

    # Flush any remaining text in buffer
    if buffer.strip():
        with tts_lock:
            tts_queue.append(buffer.strip())

    # Signal TTS worker to finish and wait
    tts_done.set()
    tts_thread.join(timeout=120)

    # Store assistant reply in history (mirrors Jarvis chat_with_openrouter)
    if full_response.strip():
        chat_history.append({"role": "assistant", "content": full_response.strip()})

    return full_response.strip()


# ─────────────────────────────────────────────────────────────────────────────
# MICROPHONE CALIBRATION  (mirrors Jarvis main_loop one-time calibration)
# ─────────────────────────────────────────────────────────────────────────────
def _calibrate_microphone() -> None:
    if not VOICE_AVAILABLE or ACCESSIBILITY_MODE:
        return
    try:
        r = sr.Recognizer()
        with sr.Microphone() as source:
            print("[Mic] Calibrating for ambient noise...")
            r.adjust_for_ambient_noise(source, duration=1.0)
            print("[Mic] Calibration complete.")
    except Exception as e:
        print(f"[Mic] Calibration failed: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# MAIN LOOP  (mirrors Jarvis main_loop() structure)
# ─────────────────────────────────────────────────────────────────────────────
def main_loop() -> None:
    global is_muted

    print(Fore.BLUE + Style.BRIGHT + "\n══════════════════════════════════════════")
    print("       PANDORA VOICE CLIENT  –  ONLINE")
    print("══════════════════════════════════════════" + Style.RESET_ALL)
    print(f"  Model      : {MODEL}")
    print(f"  Wake word  : '{WAKE_WORD}'")
    print(f"  Voice      : {VOICE_ID}")
    print(f"  TTS        : {'edge-tts' if EDGE_TTS_AVAILABLE else 'disabled'}")
    print(f"  STT        : {'speech_recognition' if VOICE_AVAILABLE else 'text input'}")
    print("  Say 'go to sleep' to pause, 'wake up' to resume.")
    print("  Say 'go offline' / 'shut down' to exit.\n")

    _calibrate_microphone()
    speak("Pandora is online, Sir. All systems nominal. How may I assist you today?")

    slept = False

    while True:
        # ── Standby / wake-word guard (mirrors Jarvis main_loop) ──────────────
        print(f"\n{Fore.YELLOW}[Waiting for wake word: '{WAKE_WORD}']{Style.RESET_ALL}")
        wake_cmd = listen()

        if not wake_cmd:
            time.sleep(0.4)
            continue

        if WAKE_WORD.lower() not in wake_cmd.lower():
            continue

        # Wake-word detected
        if slept:
            speak("Reactivated, Sir. I am at your service.")
            slept = False
        else:
            speak("Yes, Sir?")

        # ── Active command loop ────────────────────────────────────────────────
        while True:
            query = listen()

            if not query:
                time.sleep(0.3)
                continue

            q = query.lower().strip()

            # ── Control commands (mirrors Jarvis main_loop checks) ─────────────
            if any(w in q for w in ["go to sleep", "standby mode", "go standby"]):
                speak("Entering standby mode, Sir. Say my name to reactivate.")
                slept = True
                break

            if any(w in q for w in ["go offline", "shut down", "power down", "goodbye", "bye"]):
                speak("Shutting down, Sir. Farewell.")
                sys.exit(0)

            if any(w in q for w in ["wake up", "are you there", "you up"]):
                speak("I am awake and fully operational, Sir.")
                continue

            if any(w in q for w in ["mute", "stop talking", "be quiet", "silence"]):
                is_muted = True
                print("[Muted]")
                time.sleep(0.5)
                is_muted = False
                continue

            if any(w in q for w in ["unmute", "speak again", "resume speaking"]):
                is_muted = False
                speak("Audio restored, Sir.")
                continue

            if any(w in q for w in ["clear history", "reset conversation", "new conversation"]):
                chat_history.clear()
                speak("Conversation history cleared, Sir. Starting fresh.")
                continue

            if any(w in q for w in ["change model", "switch model"]):
                speak("Model switching is not yet implemented in this client, Sir.")
                continue

            # ── Route to LLM (all unrecognised commands go to the AI) ──────────
            chat(query)


# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    try:
        main_loop()
    except KeyboardInterrupt:
        speak("Interrupted, Sir. Shutting down.")
        print("\n[Pandora] Offline.")
