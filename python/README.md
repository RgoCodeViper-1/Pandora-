# The python module for the Pandora Project 

*The python/ folder holds all the necessary backend logic needed for our assistant and the node ux layer.*

### The dir of this folder will be :

python/
├── core/                                  # 🧠 #brain #execution-layer #central-control
│   ├── executor.py                        # #dispatcher #entrypoint #runs-all-operations
│   ├── registry.py                        # #operation-map #tool-registry
│   ├── guards.py                          # #security #sandbox #rules
│   ├── config.py                          # #config-loader #central-config
│   ├── memory.py                          # #runtime-memory #session-state
│   ├── memory_store.py                    # #persistent-memory #versioning
│   ├── cache.py                           # #cache #performance #ttl
│   ├── locks.py                           # #file-locking #concurrency-control
│   ├── async_ops.py                       # #async-wrapper #thread-executor
│   ├── validator.py                       # #schema-validation
│   └── logger.py                          # #logging #audit-trail
│
├── operations/                            # ⚙️ #pure-functions #no-side-effects
│   ├── file_ops.py                        # #filesystem #read-write-delete-search
│   ├── system_ops.py                      # #os-commands #process-control
│   ├── web_ops.py                         # #http #scraping #api-calls
│   └── data_ops.py                        # #transformations #parsing
│
├── services/                              # 🔵 #daemons #api-layer #external-interface
│   ├── api_server.py                      # #main-api #executor-bridge
│   ├── vad_service.py                     # #vad-daemon #speech-detection-api
│   ├── stt_service.py                     # #speech-to-text-daemon
│   ├── tts_service.py                     # #text-to-speech-daemon
│   ├── file_service.py                    # #optional #file-api-wrapper
│   └── health_service.py                  # #monitoring #status-checks
│
├── vad/                                   # 🎙 #engine #signal-processing #no-api
│   ├── detector.py                        # #core-vad-logic
│   ├── silero.py                          # #ml-model-wrapper
│   ├── webrtc.py                          # #alt-engine
│   ├── pipeline.py                        # #audio→segments
│   └── utils.py                           # #helpers
│
├── speech/                                # 🔊 #audio-processing #engine-layer
│   ├── stt_engine.py                      # #transcription-core
│   ├── tts_engine.py                      # #synthesis-core
│   ├── audio_utils.py                     # #audio-processing
│   ├── pipeline.py                        # #audio-flow
│   └── voices/                            # #voice-configs
│
├── ai/                                    # 🤖 #intelligence-layer
│   ├── planner.py                         # #task-planning #decision-engine
│   ├── intent_parser.py                   # #nlp #intent-detection
│   ├── llm_client.py                      # #llm-bridge
│   ├── tools_router.py                    # #tool-selection
│   └── embeddings.py                      # #semantic-memory
│
├── models/                                # 🧬 #ml-models #weights
│   ├── vad/
│   ├── stt/
│   └── embeddings/
│
├── automation/                            # ⚡ #background-jobs #task-runners
│   ├── scheduler.py                       # #cron-like
│   ├── triggers.py                        # #event-based
│   └── workflows.py                       # #multi-step-automation
│
├── utils/                                 # 🧩 #shared-helpers
│   ├── file_utils.py
│   ├── time_utils.py
│   ├── formatters.py
│   └── decorators.py
│
├── shared/                                # 💾 #persistent-layer #NO-LOGIC
│   ├── config/                            # #system-config
│   │   └── configuration.json
│   │
│   ├── memory/                            # #long-term-memory
│   │   ├── conversations/                 # #chat-history
│   │   ├── sessions/                      # #session-logs #versioned
│   │   ├── tasks/                         # #task-state
│   │   ├── knowledge/                     # #notes #learned-data
│   │   └── state/                         # #runtime-snapshots
│   │       └── runtime.json
│   │
│   ├── cache/                             # #disk-cache #optional
│   │   └── cache.json
│   │
│   ├── logs/                              # 🧾 #audit #debugging
│   │   ├── system.log
│   │   ├── errors.log
│   │   └── access.log
│   │
│   └── schemas/                           # 📐 #contracts #validation
│       ├── intent.json
│       ├── tool.json
│       └── memory.json
│
├── tests/                                 # 🧪 #unit-tests #integration-tests
│   ├── test_executor.py
│   ├── test_file_ops.py
│   └── test_services.py
│
├── requirements.txt
└── main.py                                # 🚀 #dev-entrypoint #optional-runner

*core/ "The only place that controls execution"*
*operations/ "Dumb, pure functions | No memory, no logging, no side effects"*
*services/ "Wrappers that expose functionality (HTTP / daemon)"*
*vad/ + 🔊 speech/ "Actual engines (reusable anywhere)"*
*ai/ "Decision-making layer (not execution)"*
*shared/ "Persistent storage"*

### Full System Flow
*Node CLI*
   *↓*
*Python services (API / daemons)*
   *↓*
*core/executor.py*
   *↓*
*guards → cache → memory → locks*
   *↓*
*operations / vad / speech / ai*
   *↓*
*shared/ (persist data)*