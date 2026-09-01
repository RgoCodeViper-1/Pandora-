/// Dialogue pattern registry — wakeword, greetings, lifecycle, small-talk.
///
/// Sources: Jarvis `process_command()` greeting block, wake/sleep/shutdown
/// branches, and "are you there" / "you up" conversational paths.
/// Every pattern here has `ExecutionType::Dialogue` so the executor knows
/// no side-effects are triggered — only a spoken response is required.
use crate::intent::{ExecutionType, IntentCategory, IntentPattern};

/// All dialogue patterns.  Returned by `all()` and merged into the global
/// registry inside `engine.rs`.
pub fn all() -> Vec<IntentPattern> {
    vec![
        // ── Wakeword ────────────────────────────────────────────────────────
        IntentPattern {
            id:             "wakeword",
            pattern:        r"(?i)\b(pandora|hey pandora|jarvis|hey jarvis |pandra |pandor)\b",
            category:       IntentCategory::Wakeword,
            execution_type: ExecutionType::Dialogue,
            weight:         1.00,
            entity_slots:   &[],
        },

        // ── Greetings ───────────────────────────────────────────────────────
        // Jarvis: "hello" → speak "Oh hello"; "hi|hey|good morning|…" → smart_greet_response
        IntentPattern {
            id:             "greeting_hello",
            pattern:        r"(?i)^\s*(hello|hi there|howdy|hey|hi)\b",
            category:       IntentCategory::Dialogue,
            execution_type: ExecutionType::Dialogue,
            weight:         0.85,
            entity_slots:   &[],
        },
        IntentPattern {
            id:             "greeting_general",
            pattern:        r"(?i)\b(hi|hey|good morning|good afternoon|good evening|good night|yo|greetings|what'?s up|how are you|how'?s it going)\b",
            category:       IntentCategory::Dialogue,
            execution_type: ExecutionType::Dialogue,
            weight:         0.80,
            entity_slots:   &[],
        },

        // ── Presence checks (Jarvis: "are you there", "you up") ─────────────
        IntentPattern {
            id:             "presence_check",
            pattern:        r"(?i)\b(are you there|you up|you awake|still there|you listening|you online)\b",
            category:       IntentCategory::Dialogue,
            execution_type: ExecutionType::Dialogue,
            weight:         0.82,
            entity_slots:   &[],
        },

        // ── Identity ("who are you", "introduce yourself") ──────────────────
        IntentPattern {
            id:             "identity_query",
            pattern:        r"(?i)\b(who are you|what are you|introduce yourself|tell me about yourself)\b",
            category:       IntentCategory::Dialogue,
            execution_type: ExecutionType::Dialogue,
            weight:         0.83,
            entity_slots:   &[],
        },

        // ── Wake-up from sleep ───────────────────────────────────────────────
        // Jarvis: "wake up" → speak "I am awake and at your service"
        IntentPattern {
            id:             "wake_up",
            pattern:        r"(?i)\b(wake up|wake yourself|come online|activate|reactivate)\b",
            category:       IntentCategory::Lifecycle,
            execution_type: ExecutionType::Dialogue,
            weight:         0.90,
            entity_slots:   &[],
        },

        // ── Go to sleep ─────────────────────────────────────────────────────
        // Jarvis: "go to sleep" → standby mode
        IntentPattern {
            id:             "go_to_sleep",
            pattern:        r"(?i)\b(go to sleep|standby|sleep mode|enter standby|hibernate)\b",
            category:       IntentCategory::Lifecycle,
            execution_type: ExecutionType::Sync,
            weight:         0.93,
            entity_slots:   &[],
        },

        // ── Shutdown / goodbye ───────────────────────────────────────────────
        // Jarvis: "go offline", "shut down", "power down", "goodbye", "bye"
        IntentPattern {
            id:             "shutdown",
            pattern:        r"(?i)\b(go offline|shut down|shutdown|power down|power off|goodbye|bye|farewell|exit|quit)\b",
            category:       IntentCategory::Lifecycle,
            execution_type: ExecutionType::Sync,
            weight:         0.97,
            entity_slots:   &[],
        },

        // ── Mute / unmute ────────────────────────────────────────────────────
        // Jarvis: "mute", "stop talking", "be quiet", "unmute", "speak again"
        IntentPattern {
            id:             "mute",
            pattern:        r"(?i)\b(mute|stop talking|be quiet|shut up|silence yourself|shush)\b",
            category:       IntentCategory::Dialogue,
            execution_type: ExecutionType::Sync,
            weight:         0.95,
            entity_slots:   &[],
        },
        IntentPattern {
            id:             "unmute",
            pattern:        r"(?i)\b(unmute|speak again|you can talk|resume talking|continue)\b",
            category:       IntentCategory::Dialogue,
            execution_type: ExecutionType::Sync,
            weight:         0.95,
            entity_slots:   &[],
        },

        // ── Greet someone else ("introduce to someone", "greet other") ───────
        IntentPattern {
            id:             "greet_other",
            pattern:        r"(?i)\b(greet someone|greet other|introduce (yourself )?to someone|say hello to (them|someone))\b",
            category:       IntentCategory::Dialogue,
            execution_type: ExecutionType::Dialogue,
            weight:         0.75,
            entity_slots:   &[],
        },

        // ── How am I doing / progress summary ───────────────────────────────
        // Jarvis: "how am i doing", "summarize my progress"
        IntentPattern {
            id:             "progress_summary",
            pattern:        r"(?i)\b(how am i doing|summarize my progress|give me a summary|what'?s my status|how'?s my progress)\b",
            category:       IntentCategory::Dialogue,
            execution_type: ExecutionType::Async,
            weight:         0.78,
            entity_slots:   &[],
        },
    ]
}
