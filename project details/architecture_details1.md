# Pandora — Final Architecture Summary (Current State + Planned Engine)

This is the consolidated and corrected overview of the current Pandora architecture, including all discussed changes, Rust integration plans, workflow routing, Python orchestration, daemon model, and the new unified Rust core engine.

---

# 1. Core Philosophy

Pandora is no longer a monolithic “Jarvis script”.

The system is now designed as:

```text id="7pj0jz"
Node.js = UI + lightweight runtime management
Python  = orchestration + services + execution
Rust    = high-performance intent/runtime engine
```

---

# 2. Final High-Level Runtime Flow

```text id="9gdjlwm"
Node CLI (ASCII / UI / Spinners)
    ↓
Python Services Layer
    ├── VAD
    ├── STT
    ├── TTS
    └── Event Runtime
    ↓
Rust Core Engine ⚡
    ├── Config Layer
    ├── AST Layer
    ├── Literal Extraction
    ├── Matcher Layer
    ├── Sink Runtime
    ├── Guardrails
    ├── Intent Scoring
    └── Multi-Intent Routing
    ↓
Python Intent Bridge
    ↓
Executor Runtime
    ├── Operations
    ├── Automation
    ├── AI / LLM
    ├── System Controls
    ├── Memory
    ├── Cache
    └── File Ops
    ↓
Dialogue Layer
    ↓
TTS Service
    ↓
Node CLI Output
    ↓
shared/ persistence
```

---

# 3. Technology Stack

| Layer                | Technology                        |
| -------------------- | --------------------------------- |
| CLI UI               | Node.js                           |
| CLI visuals          | chalk, ora, blessed, cli-spinners |
| Runtime services     | Python                            |
| STT                  | faster-whisper / whisper          |
| VAD                  | Silero VAD + WebRTC VAD           |
| TTS                  | edge-tts                          |
| Async runtime        | asyncio                           |
| Rust bindings        | PyO3 + maturin                    |
| Rust matching engine | ripgrep internals                 |
| Persistence          | JSON / SQLite / shared cache      |
| Memory               | Python-managed memory layer       |
| IPC                  | subprocess / sockets / event bus  |

---

# 4. Why Node.js Exists

Node is NOT the assistant brain.

Node is only responsible for:

```text id="r17qaq"
- CLI rendering
- ASCII dashboards
- spinners
- animations
- keyboard interaction
- launching Python daemons
- low-overhead runtime control
```

Python remains the orchestration layer.

---

# 5. Python Runtime Responsibilities

Python acts as the system orchestrator.

## Python handles:

```text id="mr4rn8"
- VAD lifecycle
- STT streaming
- Event emission
- Memory
- Automation
- Executor logic
- AI/LLM access
- Tool routing
- TTS orchestration
- Shared persistence
```

---

# 6. Rust Core Engine (NEW FINAL DESIGN)

The old `rust_searcher` intent flow is deprecated.

It has now evolved into:

```text id="nxv0c6"
rust_core
```

which merges:

```text id="5a7m9e"
matcher + intent + scoring + streaming
```

---

# 7. Rust Core Engine Layers

## Final Engine Pipeline

```text id="7xw1gh"
INPUT
 ↓
⚙️ Config Layer
 ↓
🧠 AST Analysis
 ↓
🛡️ Guardrails / Ban Checks
 ↓
⚡ Literal Extraction
 ↓
🚫 Non-Matching Byte Elimination
 ↓
🔍 Matcher Builder
 ↓
📥 Sink Runtime
 ↓
🧠 Intent Scoring
 ↓
📤 Structured Output
```

---

# 8. Ripgrep Internal Files Being Adapted

The engine is NOT using generic rewritten logic.

The following uploaded ripgrep internals are being adapted into Pandora:

| File              | Usage                            |
| ----------------- | -------------------------------- |
| `config.rs`       | runtime matcher config           |
| `matcher.rs`      | matcher builder API              |
| `literal.rs`      | prefilter optimization           |
| `sink.rs`         | intent collection                |
| `ast.rs`          | smart-case analysis              |
| `ban.rs`          | forbidden pattern checks         |
| `error.rs`        | structured engine errors         |
| `non_matching.rs` | impossible byte elimination      |
| `strip.rs`        | invalid line stripping           |
| `lines.rs`        | streaming/line stepping          |
| `macros.rs`       | runtime/debug helpers            |
| `lib.rs`          | module exports/runtime structure |

Referenced from uploaded files:




---

# 9. Final Rust Folder Structure

```text id="l6o1ep"
rust/
└── core_engine/
    ├── Cargo.toml
    └── src/
        ├── lib.rs
        ├── engine.rs
        ├── intent.rs
        ├── sink.rs
        ├── config.rs
        ├── matcher.rs
        ├── literal.rs
        ├── ast.rs
        ├── ban.rs
        ├── error.rs
        ├── strip.rs
        ├── non_matching.rs
        ├── lines.rs
        ├── macros.rs
        └── patterns/
            ├── dialogue.rs
            ├── system.rs
            ├── search.rs
            └── automation.rs
```

---

# 10. Rust Engine Capabilities

## Current planned capabilities:

```text id="f0k67v"
- multi-intent extraction
- streaming intent detection
- wakeword detection
- smart-case matching
- literal prefiltering
- sink-driven collection
- configurable matcher runtime
- guardrails / bans
- intent scoring
- dialogue classification
- entity extraction
```

---

# 11. Python ↔ Rust Integration

Bindings use:

```text id="a6m1yl"
PyO3 + maturin
```

---

## Python import

```python id="z4l8qq"
import rust_core
```

---

## Python usage

```python id="r6ecll"
result = rust_core.process(text)
```

or:

```python id="fhpdwt"
result = rust_core.process_stream(text)
```

---

# 12. Current Intent Flow

```text id="f7knqj"
Audio
 ↓
VAD
 ↓
STT
 ↓
rust_core.process()
 ↓
intent_bridge.py
 ↓
executor.py
 ↓
dialogue.py
 ↓
tts_service.py
```

---

# 13. STT Pipeline

## STT responsibilities

```text id="t80afh"
- receive audio
- stream transcription
- emit events
- invoke rust_core
```

---

## Example

```python id="tz14ws"
text = stt_engine.transcribe(audio)
intent = parse_intent(text)

await self._emit({
    "event": "INTENT_READY",
    "text": text,
    "intent": intent
})
```

---

# 14. VAD Architecture

The VAD layer combines:

```text id="0ln4sy"
- WebRTC VAD
- Silero VAD
```

Purpose:

| Engine | Role                         |
| ------ | ---------------------------- |
| WebRTC | ultra-fast speech detection  |
| Silero | accurate speech segmentation |

---

# 15. Executor Role

The executor is NOT the intent parser.

Executor responsibilities:

```text id="ujfbrw"
- execute actions
- trigger operations
- manage routing
- trigger memory/cache
- call dialogue layer
```

---

# 16. Dialogue Layer (Restored Jarvis Behavior)

Pandora originally lost conversational behavior.

This has now been separated into:

```text id="8mlr0x"
core/dialogue.py
```

Responsibilities:

```text id="qu7n2n"
- greetings
- wake responses
- acknowledgements
- confirmations
- conversational tone
```

---

# 17. TTS System

## Current TTS Stack

```text id="5bgg9n"
edge-tts
```

wrapped inside:

```text id="vjlwm1"
services/tts_service.py
```

---

## Features

```text id="i0p2ji"
- async queue
- non-blocking speech
- priority speech
- daemon-compatible
- executor integration
```

---

# 18. Shared Persistence Layer

Shared data lives under:

```text id="70v64x"
python/shared/
```

---

## Planned contents

```text id="j76e9o"
shared/
├── memory/
├── cache/
├── logs/
├── configs/
├── embeddings/
└── sessions/
```

---

# 19. Guardrails + Constraints

Planned guard layers include:

```text id="kz3xys"
- banned patterns
- malformed regex rejection
- recursion protection
- unsafe execution prevention
- intent validation
- lock checks
```

using logic adapted from:

```text id="exjlwm"
ban.rs
strip.rs
error.rs
```

---

# 20. Memory + Cache Design

The runtime pipeline is now intended to include:

```text id="sk1l8u"
guards
 ↓
cache
 ↓
memory
 ↓
execution
```

---

## Planned cache uses

```text id="qig5yr"
- repeated intent acceleration
- response caching
- memory retrieval
- semantic lookup
```

---

# 21. Planned Multi-Intent Runtime

Supported future input:

```text id="4o4hnr"
"open file and search python and send email"
```

Flow:

```text id="y5vk08"
rust_core
 ↓
multiple intents
 ↓
executor chain
 ↓
tool graph execution
```

---

# 22. Final Python Directory Structure

```text id="4l2sl5"
python/
├── ai/
│   ├── intent_bridge.py
│   ├── intent_parser.py
│   ├── planner.py
│   ├── tools_router.py
│   └── memory_manager.py
│
├── core/
│   ├── executor.py
│   ├── dialogue.py
│   ├── event_bus.py
│   └── runtime.py
│
├── speech/
│   ├── stt.py
│   ├── tts.py
│   ├── wakeword.py
│   └── audio_stream.py
│
├── services/
│   ├── vad_service.py
│   ├── stt_service.py
│   ├── tts_service.py
│   └── daemon_manager.py
│
├── operations/
│   ├── system/
│   ├── files/
│   ├── browser/
│   └── automation/
│
├── shared/
│   ├── cache/
│   ├── configs/
│   ├── memory/
│   ├── logs/
│   └── sessions/
│
└── utils/
    ├── locks.py
    ├── guards.py
    ├── async_tools.py
    └── logger.py
```

---

# 23. Final Node Runtime Structure

```text id="n0jlwm"
node/
├── cli/
├── dashboard/
├── spinners/
├── runtime/
├── ipc/
└── launcher/
```

---

# 24. Current State vs Planned State

## Already Designed / Implemented

```text id="ph5wzf"
✔ STT flow
✔ VAD structure
✔ TTS async queue
✔ executor layer
✔ dialogue layer
✔ Rust integration
✔ PyO3 workflow
✔ maturin builds
✔ rust_core architecture
```

---

## Planned / In Progress

```text id="4m4o7m"
⬜ full engine.rs assembly
⬜ sink integration
⬜ builder integration
⬜ config adaptation
⬜ multi-intent executor
⬜ graph execution
⬜ streaming partial intent
⬜ memory-aware dialogue
⬜ tool chaining
```

---

# 25. Final Architectural Goal

Pandora is evolving toward:

```text id="1ujsm7"
event-driven modular AI runtime
```

NOT:

```text id="3r6dcl"
single-script assistant
```

The major shift is:

| Old Jarvis       | Pandora                |
| ---------------- | ---------------------- |
| monolithic       | modular                |
| sync             | async                  |
| Python-only      | Python + Rust          |
| direct execution | event/runtime pipeline |
| hardcoded logic  | configurable engine    |

---

# 26. Most Important Final Clarification

## `rust_searcher`

Old role:

```text id="78lj2u"
intent parsing
```

New role:

```text id="w6uvfb"
optional heavy filesystem / memory search only
```

---

## `rust_core`

New role:

```text id="uh1hmu"
PRIMARY runtime intent engine
```

---

# 27. Final Mental Model

```text id="95vnhx"
Rust   = fast runtime intelligence
Python = orchestration + cognition
Node   = interface/runtime shell
```
