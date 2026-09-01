# create_ai_assistant_structure.py
# Creates the full ai-assistant project directory tree
# with folders + placeholder temp files with exact names.

from pathlib import Path
import json

ROOT = Path("PANDORA")

# ----------------------------
# Folder list
# ----------------------------
folders = [
    "python/vad",
    "python/ai",
    "python/speech",
    "python/models",

    "node/core",
    "node/logic",
    "node/tasks",
    "node/services",

    "shared/config",
    "shared/memory/conversations",
    "shared/memory/tasks",
    "shared/memory/knowledge",
    "shared/memory/state",
    "shared/schemas",
]

# ----------------------------
# Files with starter content
# ----------------------------
files = {
    # Root
    "README.md": "# AI Assistant Project\n",

    # ---------------- Python ----------------
    "python/vad/vad_service.py": "# VAD service entrypoint\n",
    "python/ai/llm_client.py": "# LLM client logic\n",
    "python/ai/intent_parser.py": "# Intent parsing logic\n",
    "python/ai/planner.py": "# Planning engine\n",

    "python/speech/stt.py": "# Speech-to-text module\n",
    "python/speech/tts.py": "# Text-to-speech module\n",

    "python/requirements.txt": "\n".join([
        "numpy",
        "webrtcvad",
        "sounddevice",
        "vosk",
        "edge-tts",
        "websockets"
    ]) + "\n",

    # ---------------- Node ----------------
    "node/core/stateMachine.js": "// State machine logic\n",
    "node/core/eventBus.js": "// Event bus\n",
    "node/core/orchestrator.js": "// Main orchestrator\n",

    "node/logic/router.js": "// Intent router\n",
    "node/logic/permissions.js": "// Permissions layer\n",
    "node/logic/contextManager.js": "// Memory/context manager\n",

    "node/tasks/file.js": "// File task handler\n",
    "node/tasks/web.js": "// Web task handler\n",
    "node/tasks/system.js": "// System task handler\n",
    "node/tasks/index.js": "// Export task handlers\n",

    "node/services/pythonBridge.js": "// Python websocket bridge\n",
    "node/services/llmBridge.js": "// LLM bridge\n",

    "node/package.json": json.dumps({
        "name": "ai-assistant",
        "version": "1.0.0",
        "main": "core/orchestrator.js",
        "dependencies": {
            "ws": "^8.0.0"
        }
    }, indent=2),

    # ---------------- Shared Config ----------------
    "shared/config/configuration.json": json.dumps({
        "system": {
            "name": "Pandora",
            "mode": "development",
            "logLevel": "info"
        },
        "audio": {
            "sampleRate": 16000,
            "vad": {
                "engine": "webrtc",
                "aggressiveness": 2,
                "silenceThresholdMs": 500,
                "useSilero": True,
                "sileroConfidenceThreshold": 0.5
            }
        },
        "speech": {
            "stt": {
                "engine": "vosk",
                "modelPath": "python/models/"
            },
            "tts": {
                "engine": "edge",
                "voice": "en-US-GuyNeural"
            }
        },
        "memory": {
            "enabled": True
        }
    }, indent=2),

    # ---------------- Shared Memory ----------------
    "shared/memory/conversations/session_001.md":
"""# Session 001

## User
Hello

## Assistant
Hello, how can I help you?

---
""",

    "shared/memory/tasks/tasks.json": json.dumps({
        "tasks": []
    }, indent=2),

    "shared/memory/knowledge/notes.md":
"""# Knowledge Notes

Store learned facts, summaries, references here.
""",

    "shared/memory/state/runtime.json": json.dumps({
        "lastIntent": None,
        "activeTask": None,
        "context": {}
    }, indent=2),

    # ---------------- Shared Schemas ----------------
    "shared/schemas/intent.json": json.dumps({
        "intent": "",
        "entities": {},
        "confidence": 0.0
    }, indent=2),
}

# ----------------------------
# Create folders
# ----------------------------
for folder in folders:
    (ROOT / folder).mkdir(parents=True, exist_ok=True)

# ----------------------------
# Create files
# ----------------------------
for rel_path, content in files.items():
    file_path = ROOT / rel_path
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content, encoding="utf-8")

print(f"Project structure created at: {ROOT.resolve()}")