# Pandora Adaptive Brain Architecture (Working Design)

## Overview

Today's architectural changes introduce an **Adaptive Brain Layer**
between the deterministic routing/execution pipeline and the final
executor. The objective is to keep execution deterministic while
allowing planning, routing, resource allocation, and interaction style
to improve over time through small-scale neural networks and
evolutionary computing.

Core principle:

-   Deterministic execution
-   Adaptive decision making
-   Independent learning modules
-   Shared reward/history
-   Safe fallbacks

------------------------------------------------------------------------

# High-Level Architecture

``` text
                 User Input
                      │
                      ▼
        Node CLI / Frontend Interface
                      │
                      ▼
      Python Services (VAD / STT / Daemons)
                      │
                      ▼
          Rust Core Intent Engine
                      │
                      ▼
             Intent Router (Base)
                      │
                      ▼
═══════════════════════════════════════════════
            ADAPTIVE BRAIN LAYER
═══════════════════════════════════════════════

        ┌──────────────┬──────────────┬──────────────┐
        │              │              │
 Routing Engine   Selection Engine   Personality Engine
        │              │              │
        └──────────────┴──────────────┘
                     │
             Meta Controller
                     │
             Optimizer Engine
                     │
═══════════════════════════════════════════════
                      │
                      ▼
             Execution Planner
                      │
                      ▼
               Core Executor
                      │
                      ▼
              Response Generation
```

------------------------------------------------------------------------

# Design Philosophy

The adaptive components do **not** execute user actions.

Instead they decide:

-   where to route requests
-   which resources to use
-   which tools to activate
-   which services should remain alive
-   how Pandora communicates

Once a decision has been made, the Core Executor performs deterministic
execution.

------------------------------------------------------------------------

# Adaptive Routing Engine

Purpose: Select the optimal execution path.

Inputs

-   Intent confidence
-   Conversation state
-   Previous intent
-   Context length
-   Available resources
-   System load
-   Failure history
-   Memory relevance

Output

``` text
Rule Engine
Memory Retrieval
Planner
LLM
Clarification
Search
Executor
```

Possible future implementation

-   Small neural network
-   Evolutionary parameter tuning
-   Contextual bandit
-   Reinforcement reward updates

------------------------------------------------------------------------

# Selection Engine

Purpose: Choose required capabilities before execution.

Instead of

``` text
Intent
   │
 Tool
```

use

``` text
Intent
   │
Capability Graph
   │
Selection Engine
   │
Execution Plan
```

Responsible for activating only the required services.

Examples

-   STT
-   Whisper
-   Memory
-   OCR
-   SQL
-   Browser
-   Planner
-   Vision
-   Scheduler
-   Embeddings

The engine becomes resource-aware and may choose lighter alternatives
under constrained conditions.

------------------------------------------------------------------------

# Personality Engine

Purpose

Adapt communication style using long-term interaction history.

Adaptive dimensions

-   Humor
-   Formality
-   Verbosity
-   Explanation depth
-   Vocabulary
-   Preferred examples
-   Interaction pacing

Fixed dimensions

-   Safety
-   Ethics
-   Privacy
-   Core behavioural boundaries

The engine evolves a personalized interaction style while remaining
inside fixed safety constraints.

------------------------------------------------------------------------

# Meta Controller

Coordinates all adaptive engines.

Responsibilities

-   Shared reward propagation
-   Conflict resolution
-   Confidence tracking
-   Policy synchronization
-   Global constraints
-   Shared adaptive state

Example

Routing Engine │ Use Cloud LLM

Selection Engine │ Offline only

↓

Meta Controller

↓

Choose Local Model

------------------------------------------------------------------------

# Optimizer Engine

Purpose

Resource allocation and subsystem lifecycle management.

Lifecycle

``` text
OFF
 │
 ▼
WARMING
 │
 ▼
READY
 │
 ▼
ACTIVE
 │
 ▼
IDLE
 │
 ▼
SUSPENDED
 │
 ▼
OFF
```

Responsibilities

-   Predict required subsystems
-   Keep frequently used services warm
-   Suspend unused components
-   Reduce CPU usage
-   Reduce RAM usage
-   Improve startup latency
-   Manage daemon lifetime

Example

User repeatedly performs voice requests

↓

Keep STT active

User idle

↓

Unload STT

------------------------------------------------------------------------

# Learning Strategy

Online adaptation

-   Reward updates
-   Routing statistics
-   Tool success
-   User preference learning
-   Confidence calibration

Offline evolution

-   Genetic optimization
-   Neural-network tuning
-   Policy evaluation
-   Candidate comparison
-   Promotion of improved policies

------------------------------------------------------------------------

# Overall Flow

``` text
User Input
      │
      ▼
Frontend
      │
      ▼
Python Services
      │
      ▼
Rust Intent Engine
      │
      ▼
Base Intent Router
      │
      ▼
Adaptive Routing Engine
      │
      ▼
Selection Engine
      │
      ▼
Meta Controller
      │
      ├──────────────┐
      │              │
      ▼              ▼
Personality      Optimizer
      │              │
      └──────┬───────┘
             ▼
     Execution Planner
             │
             ▼
       Core Executor
             │
             ▼
      Response Output
```

------------------------------------------------------------------------

# Long-Term Vision

The adaptive brain should gradually optimize:

1.  Routing policies
2.  Tool selection
3.  Resource scheduling
4.  Communication style
5.  Runtime efficiency

while preserving deterministic execution and stable system behaviour.

The long-term objective is an assistant that becomes increasingly
efficient and personalized without sacrificing reliability or
explainability.
