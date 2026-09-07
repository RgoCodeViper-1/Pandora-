"""
utils/util_response.py
=======================
Central response router. Every handler calls `send_response(text)` instead
of speaking directly — this is the single point that decides how output
reaches the user (TTS today, could fan out to the Node UI later).
"""

import sys
from pathlib import Path

# utils/ -> python/ -> project root
BASE_DIR = Path(__file__).resolve().parent.parent.parent
PYTHON_DIR = BASE_DIR / "python"

for p in (str(BASE_DIR), str(PYTHON_DIR)):
    if p not in sys.path:
        sys.path.append(p)

try:
    from speech.tts import speak as _tts_speak
except ImportError:
    def _tts_speak(text: str) -> None:
        print(f"[TTS Output]: {text}")


def send_response(message: str) -> str:
    """Centralised response router for handler output."""
    if message:
        _tts_speak(message)
    return message
