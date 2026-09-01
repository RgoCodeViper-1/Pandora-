/// Search & information pattern registry — web search, Wikipedia, weather,
/// news, notes, and memory/context recall intents.
///
/// Sources: Jarvis `process_command()` search block, weather block, news
/// block, note-taking block, and memory recall block.
use crate::intent::{ExecutionType, IntentCategory, IntentPattern};

pub fn all() -> Vec<IntentPattern> {
    vec![
        // ── Weather ───────────────────────────────────────────────────────────
        // Jarvis: "weather in <location>", "weather at <location>", "weather for <location>"
        IntentPattern {
            id:             "weather_query",
            pattern:        r"(?i)\b(weather|forecast|temperature|how(?:'?s| is) the weather)\s+(?:in|at|for|of)\s+(?P<location>[a-zA-Z][\w\s,]{1,40})\b",
            category:       IntentCategory::Weather,
            execution_type: ExecutionType::Async,
            weight:         0.92,
            entity_slots:   &["location"],
        },
        // Simple "what's the weather" without location (uses default from config)
        IntentPattern {
            id:             "weather_query_default",
            pattern:        r"(?i)\b(what'?s the weather|how'?s the weather|weather today|current weather|weather report)\b",
            category:       IntentCategory::Weather,
            execution_type: ExecutionType::Async,
            weight:         0.85,
            entity_slots:   &[],
        },

        // ── Wikipedia ─────────────────────────────────────────────────────────
        // Jarvis: "search wikipedia for <topic>", "search wikipedia <topic>"
        IntentPattern {
            id:             "search_wikipedia",
            pattern:        r"(?i)\b(search wikipedia(?: for)?|wikipedia (search|lookup|tell me about)|look up on wikipedia)\s+(?P<topic>.+)",
            category:       IntentCategory::Search,
            execution_type: ExecutionType::Async,
            weight:         0.91,
            entity_slots:   &["topic"],
        },

        // ── Google / Web search ───────────────────────────────────────────────
        // Jarvis: "google <query>", "search for <query>", "search on google for <query>"
        IntentPattern {
            id:             "search_google",
            pattern:        r"(?i)\b(google|search (on google )?for|search (the )?web for|look up|find information (on|about))\s+(?P<query>.{3,})",
            category:       IntentCategory::Search,
            execution_type: ExecutionType::Async,
            weight:         0.88,
            entity_slots:   &["query"],
        },

        // ── News ──────────────────────────────────────────────────────────────
        // Jarvis: "read the news", "tell me the news", "latest news"
        IntentPattern {
            id:             "news_headlines",
            pattern:        r"(?i)\b(read (the )?news|tell me (the )?news|latest news|what'?s (in the )?news|news (today|headlines?)|top (stories|headlines?))\b",
            category:       IntentCategory::News,
            execution_type: ExecutionType::Async,
            weight:         0.88,
            entity_slots:   &[],
        },

        // ── Note-taking ───────────────────────────────────────────────────────
        // Jarvis: "take a note", "note down", "store this information"
        IntentPattern {
            id:             "note_create",
            pattern:        r"(?i)\b(take (a )?note|note (this |it )?down|jot (this )?down|store this (information|info)?|make a note|write (this )?down|remember this for me)\b",
            category:       IntentCategory::Notes,
            execution_type: ExecutionType::Sync,
            weight:         0.87,
            entity_slots:   &[],
        },
        // Note search (enhanced store_note_enhanced category search)
        IntentPattern {
            id:             "note_search",
            pattern:        r"(?i)\b(search (my )?notes?|find (my )?notes?|look (through|in) (my )?notes?|notes? (about|on|for|containing))\s+(?P<query>.+)",
            category:       IntentCategory::Notes,
            execution_type: ExecutionType::Sync,
            weight:         0.83,
            entity_slots:   &["query"],
        },
        IntentPattern {
            id:             "note_categories",
            pattern:        r"(?i)\b(list (note )?categor(y|ies)|show (note )?categor(y|ies)|what (note )?categor(y|ies))\b",
            category:       IntentCategory::Notes,
            execution_type: ExecutionType::Sync,
            weight:         0.78,
            entity_slots:   &[],
        },

        // ── Memory / context recall ───────────────────────────────────────────
        // Jarvis: "remind me what we discussed", "what do you remember", "recall topics"
        IntentPattern {
            id:             "memory_recall_conversations",
            pattern:        r"(?i)\b(remind me what we discussed|what did (we talk|i say)|recall (our )?conversation|recent conversations?|what did you hear)\b",
            category:       IntentCategory::Memory,
            execution_type: ExecutionType::Sync,
            weight:         0.83,
            entity_slots:   &[],
        },
        IntentPattern {
            id:             "memory_recall_topics",
            pattern:        r"(?i)\b(what do you remember|recall (my )?topics?|what have we (talked|discussed)|what topics|remember topics?)\b",
            category:       IntentCategory::Memory,
            execution_type: ExecutionType::Sync,
            weight:         0.80,
            entity_slots:   &[],
        },
        // Jarvis: "remember this", "remember <topic>"
        IntentPattern {
            id:             "memory_store_topic",
            pattern:        r"(?i)\bremember (this|that|the following)\b",
            category:       IntentCategory::Memory,
            execution_type: ExecutionType::Sync,
            weight:         0.85,
            entity_slots:   &[],
        },
        // Jarvis: "set focus" / "focus for today"
        IntentPattern {
            id:             "memory_set_focus",
            pattern:        r"(?i)\b(set (my )?(daily )?focus|focus for today|today'?s focus (is|should be)?|my focus today)\b",
            category:       IntentCategory::Memory,
            execution_type: ExecutionType::Sync,
            weight:         0.80,
            entity_slots:   &[],
        },
    ]
}
