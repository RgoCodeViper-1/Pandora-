"""Canonical Pandora audio pipeline entrypoint.

The production path is owned by :class:`VoiceRuntime`:

    microphone -> VAD -> SpeechToTextEngine -> intent bridge -> Executor -> TTS

This module is only a thin CLI adapter. It deliberately does not import the
development-only ``tests/whisper_runtime2.py`` implementation.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

logger = logging.getLogger("pandora.audio_pipeline")


def _process_text(text: str) -> dict:
    from ai.intent_bridge import parse_intent, parse_intent_stream

    result = parse_intent_stream(text) or parse_intent(text)
    print(json.dumps(result, indent=2))
    return result


def _process_wav(wav_path: str) -> dict:
    from ai.intent_bridge import parse_intent, parse_intent_stream
    from speech.stt import SpeechToTextEngine

    stt = SpeechToTextEngine()
    text = stt.transcribe_wav(wav_path)
    logger.info("Transcript: %r", text)
    result = parse_intent_stream(text) or parse_intent(text)
    print(json.dumps(result, indent=2))
    return result


def _run_mic(node_uri: str | None) -> None:
    from services.voice_runtime import VoiceRuntime

    runtime = VoiceRuntime(node_ws_uri=node_uri or "", on_response=_on_response)
    runtime.start()
    try:
        while runtime.state.name != "STOPPED":
            time.sleep(0.25)
    except KeyboardInterrupt:
        logger.info("Audio pipeline interrupted")
    finally:
        runtime.stop()


def _on_response(text: str, intent: dict) -> None:
    logger.info("PANDORA: %s", text)


def main() -> None:
    parser = argparse.ArgumentParser(description="Pandora production audio pipeline")
    parser.add_argument("--text", help="Process text without opening the microphone")
    parser.add_argument("--wav", help="Transcribe and process an existing WAV file")
    parser.add_argument("--no-ws", action="store_true", help="Disable the optional Node bridge")
    parser.add_argument("--node-uri", default="ws://localhost:8765")
    parser.add_argument("--log", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log),
        format="%(asctime)s [%(name)s] %(levelname)s - %(message)s",
    )

    if args.text is not None:
        _process_text(args.text)
    elif args.wav is not None:
        _process_wav(args.wav)
    else:
        _run_mic(None if args.no_ws else args.node_uri)


if __name__ == "__main__":
    main()
