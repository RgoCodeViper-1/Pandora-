Target 1 — python/vad/vad_service.py

Priority: 🔴 Highest

This is currently your actual VAD implementation.

It already correctly handles:

Microphone

 ↓

WebRTC VAD

 ↓

Silero confirmation

 ↓

audio segment

The problem is this section around _vad_loop():

with sr.AudioFile(audio_path) as source:

audio = self._recognizer.record(source)

text = self._recognizer.recognize_google(audio)

and then:

intent = parse_intent(text)

Change it to:

VAD

 ↓

confirmed audio

 ↓

STT service

 ↓

transcript

 ↓

Intent Bridge

So remove STT responsibility from this file.

Conceptually, _vad_loop() should end at:

audio_path = self._save_audio(raw_pcm)

callback(audio_path, raw_pcm)

or through a clean service callback/event.

It should not know whether the STT engine is Whisper, Google, Web Speech, Vosk, etc.

Exactly — **this is the dependency issue created by moving `vad_service.py` into `services/`**. But the important thing is that not every import should be moved with it.

Your screenshot currently has:

```python
from vad.config import VADConfig

import speech_recognition as sr

from ai.intent_bridge import parse_intent
import websockets
```

After the refactor, these imports have different fates.

## 1. `VADConfig` — keep it

This is **not a problem**.

Your new file can remain:

```text
python/
├── services/
│   └── vad_service.py
│
└── vad/
    └── config.py
```

and:

```python
from vad.config import VADConfig
```

is perfectly valid **provided PANDORA is launched from the `python/` package root**.

So:

```text
python/
    pandora_launcher.py
```

runs:

```text
services.vad_service
        ↓
vad.config
```

No need to move `config.py` into `services/`.

---

# 2. `speech_recognition` — remove it from VAD

This is the important one.

Currently you have:

```python
try:
    import speech_recognition as sr
except ImportError:
    print("SpeechRecognition not installed — STT unavailable")
```

But once we implement the architecture we discussed, **VAD should no longer perform STT**.

Your current file does this:

```text
VAD
 ↓
save WAV
 ↓
speech_recognition
 ↓
Google
 ↓
parse_intent()
```

That's exactly what we're trying to separate.

So remove:

```python
import speech_recognition as sr
```

and also remove:

```python
self._recognizer = sr.Recognizer()
```

from `__init__()`.

Then remove this whole section from `_vad_loop()`:

```python
with sr.AudioFile(audio_path) as source:
    audio = self._recognizer.record(source)
    text = self._recognizer.recognize_google(audio).lower().strip()
```

The STT dependency moves to:

```text
services/stt_service.py
```

which will eventually use:

```text
speech/stt.py
```

---

# 3. `ai.intent_bridge` — also remove it from VAD

You currently have:

```python
from ai.intent_bridge import parse_intent
```

Again, VAD shouldn't know about intent.

Remove:

```python
from ai.intent_bridge import parse_intent
```

and remove:

```python
intent = parse_intent(text)
```

from the VAD loop.

The architecture becomes:

```text
services/vad_service.py
        │
        │ audio
        ▼
services/stt_service.py
        │
        │ text
        ▼
ai/intent_bridge.py
        │
        │ IntentResult
        ▼
core/executor.py
```

That is a **dependency inversion**, not an import problem.

---

# 4. `websockets` — this one depends on how we want to wire the system

Your current VAD service directly communicates with Node:

```python
import websockets
```

and:

```python
async def _connect_and_run(...)
```

This is another coupling:

```text
VAD
 ↓
Node
```

But your architecture is supposed to be:

```text
Node
 ↓
Python Runtime
 ↓
VAD/STT/etc.
```

So eventually I would remove the WebSocket responsibility from `VADService` as well.

Instead:

```text
VADService
    ↓
audio callback / event
    ↓
VoiceRuntime
    ↓
STT
```

However, **I would not remove WebSockets in the same first edit unless you're ready to refactor `VoiceRuntime` at the same time**.

For now, you can leave:

```python
import websockets
```

and the Node event functionality intact while we separate STT.

---

# Therefore the new import section should initially look like

```python
import asyncio
import json
import logging
import os
import queue
import tempfile
import time
import wave

from vad.config import VADConfig

import websockets

logger = logging.getLogger("pandora.vad")
```

Notice what's gone:

```python
# REMOVE
import speech_recognition as sr

# REMOVE
from ai.intent_bridge import parse_intent
```

The VAD module now depends on:

```text
VAD
 ├── vad.config
 ├── webrtcvad
 ├── sounddevice
 ├── torch / Silero
 └── websockets       ← temporary, until runtime refactor
```

rather than:

```text
VAD
 ├── STT
 ├── Intent Engine
 └── Node
```

---

# 5. But there's a subtle Python import issue

If you move the file to:

```text
python/services/vad_service.py
```

don't launch it like this from inside `services/`:

```powershell
python vad_service.py
```

because then Python's import root can become:

```text
python/services/
```

and:

```python
from vad.config import VADConfig
```

may fail.

Instead, from the **`python/` directory**:

```powershell
python -m services.vad_service
```

That gives Python the proper package structure:

```text
python/
│
├── services/
│   ├── __init__.py
│   └── vad_service.py
│
├── vad/
│   ├── __init__.py
│   └── config.py
│
├── ai/
│   ├── __init__.py
│   └── intent_bridge.py
│
└── ...
```

Then:

```python
from vad.config import VADConfig
```

works naturally.

---

# 6. I'd actually make one more small structural improvement

If you don't already have them, make sure these are packages:

```text
python/
├── services/
│   └── __init__.py
│
├── vad/
│   └── __init__.py
│
├── speech/
│   └── __init__.py
│
├── ai/
│   └── __init__.py
│
└── core/
    └── __init__.py
```

Then your imports have a consistent package structure.

---

## Final dependency after this particular refactor

```text
                     services/vad_service.py
                              │
             ┌────────────────┼────────────────┐
             │                │                │
             ▼                ▼                ▼
        vad.config       WebRTC VAD        Silero VAD
             │
             ▼
        Audio Segment
             │
             │
             ▼
       VoiceRuntime
             │
             ▼
      services/stt_service.py
             │
             ▼
        speech/stt.py
             │
       ┌─────┴──────┐
       ▼            ▼
  Whisper.cpp    Fast STT
       │            │
       └─────┬──────┘
             ▼
     ai/intent_bridge.py
             │
             ▼
       Rust core_engine
             │
             ▼
        core/executor
```

So **`vad/config.py` stays exactly where it is**. You don't resolve that dependency by moving the config; you resolve it by maintaining the package relationship.

The dependencies that should actually be removed from `vad_service.py` are **`speech_recognition` and `parse_intent`**, because those aren't VAD responsibilities in the architecture we're building. The uploaded file confirms that those two are currently embedded directly in the VAD loop. 

And that separation will make the later `modes/` system much cleaner: **mode selection can choose the STT pipeline without VAD knowing anything about which recognizer was selected.**
