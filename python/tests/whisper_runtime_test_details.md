Here's a structured handoff you can paste into a new chat:

# Pandora STT Pipeline Debugging & whisper.cpp Integration Summary

## Objective

Fix Pandora's speech-to-text pipeline and remove manual startup friction by automatically launching and managing the local whisper.cpp server.

---

# Current Pandora Architecture

## Audio Pipeline

Mic Input
↓
WebRTC VAD
↓
ConversationalRuntime (Python placeholder for future Rust runtime)
↓
ConversationStateEngine
↓
Silero verification hook (stub)
↓
Utterance finalization
↓
WAV serialization
↓
whisper.cpp STT server
↓
Transcript
↓
Intent Routing
↓
Python Orchestrator
↓
Rust Core Task Execution

Notes:

* Intent routing currently occurs in Python orchestration layer.
* Rust core is responsible for lower-level task execution.
* `whisper_runtime2.py` is currently being used as the STT runtime test harness.
* Future plan is replacing Python conversational runtime with Rust implementation.

---

# Initial Problem

Pandora produced:

[STT] Whisper server not reachable — is it running?

Audio capture, VAD, silence detection, and utterance finalization were functioning correctly.

Failure occurred during STT submission.

---

# Root Cause #1

Original runtime used:

```python
WHISPER_URL = "127.0.0.1:8080/inference"
```

Python requests requires a protocol.

Fixed to:

```python
WHISPER_URL = "http://127.0.0.1:8080/inference"
```

Without `http://`, requests raised:

```text
No connection adapters were found for '127.0.0.1:8080/inference'
```

---

# whisper.cpp Installation Layout

Actual installation:

native/
└── whisper_cpp/
├── whisper-server.exe
├── whisper-cli.exe
├── whisper-stream.exe
├── whisper.dll
├── ggml.dll
├── models/
│ └── ggml-base.en-q4_0.bin

Important:

The executable is NOT inside:

build/bin/Release/

It resides directly in:

native/whisper_cpp/

---

# Root Cause #2

Attempted startup commands:

```powershell
.\whisper-server.exe -m models\ggml-small.en.bin
```

and

```powershell
.\whisper-server.exe -m models\ggml-base.bin
```

failed because those model files do not exist.

Actual model:

```text
ggml-base.en-q4_0.bin
```

Correct command:

```powershell
.\whisper-server.exe -m .\models\ggml-base.en-q4_0.bin
```

---

# Current Status

Whisper server successfully launches.

Browser confirms server availability:

```text
http://127.0.0.1:8080
```

Server exposes:

```text
/inference
/load
```

UI page loads successfully.

This confirms:

* whisper-server.exe works
* model loads correctly
* server listens on port 8080

---

# Remaining Goal

Remove manual startup requirement.

Currently user must manually run:

```powershell
.\whisper-server.exe -m .\models\ggml-base.en-q4_0.bin
```

before launching Pandora.

Need Pandora to auto-launch whisper.cpp if it is not already running.

---

# Proposed Auto-Launch Design

Add helper:

```python
ensure_whisper_running()
```

Behavior:

1. Check localhost:8080
2. If reachable:

   * continue
3. If not reachable:

   * start whisper-server.exe
4. Wait until endpoint responds
5. Continue Pandora boot

Pseudo-flow:

Pandora Start
↓
Check Whisper
↓
Running?
├── Yes → Continue
└── No
↓
Launch whisper-server.exe
↓
Wait for localhost:8080
↓
Continue

---

# Recommended Implementation

Add:

```python
import subprocess
import pathlib
import requests
import time
```

Server path:

```python
WHISPER_SERVER = (
    pathlib.Path(__file__).resolve().parents[2]
    / "native"
    / "whisper_cpp"
    / "whisper-server.exe"
)
```

Model path:

```python
WHISPER_MODEL = (
    pathlib.Path(__file__).resolve().parents[2]
    / "native"
    / "whisper_cpp"
    / "models"
    / "ggml-base.en-q4_0.bin"
)
```

Auto-start:

```python
def ensure_whisper_running():
    ...
```

Call before PyAudio initialization inside `run()`.

---

# Future Improvement

Instead of hardcoding model name:

```python
ggml-base.en-q4_0.bin
```

auto-discover:

```python
models/*.bin
```

Benefits:

* model swaps require no code changes
* supports future upgrades
* avoids filename mismatches

---

# Process Management Improvement

Store subprocess handle:

```python
_whisper_process = subprocess.Popen(...)
```

When Pandora exits:

```python
_whisper_process.terminate()
```

only if Pandora started the server.

This prevents orphaned whisper-server.exe processes.

---

# Next Tasks

Priority 1:

* Implement ensure_whisper_running()
* Test auto-launch behavior

Priority 2:

* Add automatic model discovery

Priority 3:

* Add process ownership tracking and cleanup

Priority 4:

* Move whisper server management into Pandora service/bootstrap layer instead of keeping it inside test runtime

Priority 5:

* Replace Python ConversationalRuntime with Rust runtime when rust_core is ready

---

# Relevant File

Current runtime under modification:

```text
whisper_runtime2.py
```

Contains:

* WebRTC VAD
* ConversationStateEngine
* ConversationalRuntime
* Silero stub
* WAV serialization
* whisper.cpp STT integration

The STT endpoint is already set to:

```python
WHISPER_URL = "http://127.0.0.1:8080/inference"
```

and should be retained.

This summary should allow continuation of the Pandora whisper.cpp integration work without losing any context.
