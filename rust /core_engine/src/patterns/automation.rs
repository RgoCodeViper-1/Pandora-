/// Automation pattern registry — media playback, messaging, tasks, habits,
/// schedule, and browser navigation intents.
///
/// Sources: Jarvis `process_command()` media block, WhatsApp/email block,
/// task management block, habit tracking block, schedule block, and website
/// open block.
use crate::intent::{ExecutionType, IntentCategory, IntentPattern};

pub fn all() -> Vec<IntentPattern> {
    vec![
        // ── Media: YouTube ────────────────────────────────────────────────────
        // Jarvis: "play on youtube", "play <video> on youtube"
        IntentPattern {
            id:             "media_youtube",
            pattern:        r"(?i)\b(play|watch|show me)\s+(?P<query>.{2,60}?)\s+on youtube\b",
            category:       IntentCategory::Media,
            execution_type: ExecutionType::Async,
            weight:         0.92,
            entity_slots:   &["query"],
        },
        IntentPattern {
            id:             "media_youtube_generic",
            pattern:        r"(?i)\b(play on youtube|open youtube|youtube (search|play|find))\b",
            category:       IntentCategory::Media,
            execution_type: ExecutionType::Async,
            weight:         0.80,
            entity_slots:   &[],
        },

        // ── Media: Spotify ────────────────────────────────────────────────────
        // Jarvis: "play song on spotify", "play <song> on spotify"
        IntentPattern {
            id:             "media_spotify",
            pattern:        r"(?i)\b(play|listen to)\s+(?P<song>.{2,60}?)\s+(on spotify|in spotify|spotify)\b",
            category:       IntentCategory::Media,
            execution_type: ExecutionType::Async,
            weight:         0.92,
            entity_slots:   &["song"],
        },
        IntentPattern {
            id:             "media_spotify_generic",
            pattern:        r"(?i)\b(play (song|music|track) on spotify|open spotify|spotify (search|play))\b",
            category:       IntentCategory::Media,
            execution_type: ExecutionType::Async,
            weight:         0.82,
            entity_slots:   &[],
        },
        // Local music play
        IntentPattern {
            id:             "media_local",
            pattern:        r"(?i)\bplay the music\b",
            category:       IntentCategory::Media,
            execution_type: ExecutionType::Sync,
            weight:         0.82,
            entity_slots:   &[],
        },

        // ── Messaging: WhatsApp ───────────────────────────────────────────────
        // Jarvis: "send message to <name>", "send whatsapp message to <name>"
        IntentPattern {
            id:             "whatsapp_send",
            pattern:        r"(?i)\b(send (whatsapp )?message to|whatsapp (message to|send to))\s+(?P<recipient>[\w\s]{1,40})\b",
            category:       IntentCategory::Messaging,
            execution_type: ExecutionType::Async,
            weight:         0.93,
            entity_slots:   &["recipient"],
        },
        IntentPattern {
            id:             "whatsapp_send_another",
            pattern:        r"(?i)\b(send another (whatsapp )?message|another message to (them|same person)|reply (to them|to that person))\b",
            category:       IntentCategory::Messaging,
            execution_type: ExecutionType::Async,
            weight:         0.88,
            entity_slots:   &[],
        },

        // ── Messaging: Email ──────────────────────────────────────────────────
        // Jarvis: "send email to <name>", "compose email", "write an email"
        IntentPattern {
            id:             "email_send",
            pattern:        r"(?i)\b(send (an )?email to|email (to )?)\s+(?P<recipient>[\w\s]{1,40})\b",
            category:       IntentCategory::Messaging,
            execution_type: ExecutionType::Async,
            weight:         0.92,
            entity_slots:   &["recipient"],
        },
        IntentPattern {
            id:             "email_compose",
            pattern:        r"(?i)\b(compose (an )?email|write (an )?email|auto mail|draft (an )?email)\b",
            category:       IntentCategory::Messaging,
            execution_type: ExecutionType::Async,
            weight:         0.87,
            entity_slots:   &[],
        },

        // ── Tasks ─────────────────────────────────────────────────────────────
        // Jarvis full task management block
        IntentPattern {
            id:             "task_add",
            pattern:        r"(?i)\b(add (a )?task|create (a )?task|new task|remind me to)\s+(?P<description>.{3,100})\b",
            category:       IntentCategory::Tasks,
            execution_type: ExecutionType::Sync,
            weight:         0.88,
            entity_slots:   &["description"],
        },
        IntentPattern {
            id:             "task_complete",
            pattern:        r"(?i)\b(mark (task|it|that) (as )?(complete|done|finished)|complete (the )?task|finish (the )?task|done with)\s+(?P<task>.{3,80})\b",
            category:       IntentCategory::Tasks,
            execution_type: ExecutionType::Sync,
            weight:         0.88,
            entity_slots:   &["task"],
        },
        IntentPattern {
            id:             "task_list",
            pattern:        r"(?i)\b(list (my )?tasks?|show (my )?tasks?|what (are my|'?s on my) tasks?|pending tasks?|active tasks?)\b",
            category:       IntentCategory::Tasks,
            execution_type: ExecutionType::Sync,
            weight:         0.85,
            entity_slots:   &[],
        },
        IntentPattern {
            id:             "task_delete",
            pattern:        r"(?i)\b(delete (the )?task|remove (the )?task)\s+(?P<task>.{3,80})\b",
            category:       IntentCategory::Tasks,
            execution_type: ExecutionType::Sync,
            weight:         0.88,
            entity_slots:   &["task"],
        },
        IntentPattern {
            id:             "task_overdue",
            pattern:        r"(?i)\b(overdue tasks?|what tasks? (are )?overdue|missed tasks?)\b",
            category:       IntentCategory::Tasks,
            execution_type: ExecutionType::Sync,
            weight:         0.84,
            entity_slots:   &[],
        },

        // ── Habits ────────────────────────────────────────────────────────────
        // Jarvis habit tracking block
        IntentPattern {
            id:             "habit_add",
            pattern:        r"(?i)\badd habit\s+(?P<habit>.{2,60})\b",
            category:       IntentCategory::Habits,
            execution_type: ExecutionType::Sync,
            weight:         0.88,
            entity_slots:   &["habit"],
        },
        IntentPattern {
            id:             "habit_remove",
            pattern:        r"(?i)\b(remove|delete) habit\s+(?P<habit>.{2,60})\b",
            category:       IntentCategory::Habits,
            execution_type: ExecutionType::Sync,
            weight:         0.90,
            entity_slots:   &["habit"],
        },
        IntentPattern {
            id:             "habit_done",
            pattern:        r"(?i)\b(mark|done|complete) habit\s+(?P<habit>.{2,60})\b",
            category:       IntentCategory::Habits,
            execution_type: ExecutionType::Sync,
            weight:         0.87,
            entity_slots:   &["habit"],
        },
        IntentPattern {
            id:             "habit_reset",
            pattern:        r"(?i)\breset habit\s+(?P<habit>.{2,60})\b",
            category:       IntentCategory::Habits,
            execution_type: ExecutionType::Sync,
            weight:         0.88,
            entity_slots:   &["habit"],
        },
        IntentPattern {
            id:             "habit_list",
            pattern:        r"(?i)\b(show|list) habits?\b",
            category:       IntentCategory::Habits,
            execution_type: ExecutionType::Sync,
            weight:         0.84,
            entity_slots:   &[],
        },
        IntentPattern {
            id:             "habit_pending",
            pattern:        r"(?i)\b(pending habits?|habits? (pending|today)|what habits? (are )?(left|remaining|pending))\b",
            category:       IntentCategory::Habits,
            execution_type: ExecutionType::Sync,
            weight:         0.83,
            entity_slots:   &[],
        },

        // ── Schedule ──────────────────────────────────────────────────────────
        // Jarvis full schedule management block (today / tomorrow / yesterday)
        IntentPattern {
            id:             "schedule_review_today",
            pattern:        r"(?i)\b(review today|today'?s (plan|schedule|agenda)|what'?s (on |for )?today|today'?s plans?)\b",
            category:       IntentCategory::Schedule,
            execution_type: ExecutionType::Sync,
            weight:         0.88,
            entity_slots:   &[],
        },
        IntentPattern {
            id:             "schedule_review_tomorrow",
            pattern:        r"(?i)\b(review tomorrow|tomorrow'?s (plan|schedule|agenda)|what'?s (on |for )?tomorrow|tomorrow'?s plans?)\b",
            category:       IntentCategory::Schedule,
            execution_type: ExecutionType::Sync,
            weight:         0.88,
            entity_slots:   &[],
        },
        IntentPattern {
            id:             "schedule_review_yesterday",
            pattern:        r"(?i)\b(review yesterday|yesterday'?s (plan|schedule)|what (was |'s )?(on |for )?yesterday)\b",
            category:       IntentCategory::Schedule,
            execution_type: ExecutionType::Sync,
            weight:         0.85,
            entity_slots:   &[],
        },
        IntentPattern {
            id:             "schedule_add_today",
            pattern:        r"(?i)\b(schedule for today|plan for today|add today'?s plan|set (my |up )?(today'?s )?schedule)\b",
            category:       IntentCategory::Schedule,
            execution_type: ExecutionType::Async,
            weight:         0.87,
            entity_slots:   &[],
        },
        IntentPattern {
            id:             "schedule_add_tomorrow",
            pattern:        r"(?i)\b(schedule for tomorrow|plan for tomorrow|add tomorrow'?s plan)\b",
            category:       IntentCategory::Schedule,
            execution_type: ExecutionType::Async,
            weight:         0.87,
            entity_slots:   &[],
        },
        IntentPattern {
            id:             "schedule_create_now",
            pattern:        r"(?i)\b(create schedule|set schedule now|add schedule now|force schedule)\b",
            category:       IntentCategory::Schedule,
            execution_type: ExecutionType::Async,
            weight:         0.85,
            entity_slots:   &[],
        },
        IntentPattern {
            id:             "schedule_modify_today",
            pattern:        r"(?i)\b(modify today|change today'?s plan|update today'?s (schedule|plan))\b",
            category:       IntentCategory::Schedule,
            execution_type: ExecutionType::Async,
            weight:         0.83,
            entity_slots:   &[],
        },
        IntentPattern {
            id:             "schedule_clear_today",
            pattern:        r"(?i)\b(clear today'?s (schedule|plans?)|delete today'?s plans?|remove today'?s (schedule|plans?))\b",
            category:       IntentCategory::Schedule,
            execution_type: ExecutionType::Sync,
            weight:         0.87,
            entity_slots:   &[],
        },

        // ── Browser / websites ────────────────────────────────────────────────
        // Jarvis: "open youtube", "open github", "open <url>"
        IntentPattern {
            id:             "browser_open_known",
            pattern:        r"(?i)\bopen\s+(?P<site>youtube|gmail|github|reddit|google|wikipedia|stackoverflow|stack overflow|calendar|spotify)\b",
            category:       IntentCategory::Browser,
            execution_type: ExecutionType::Sync,
            weight:         0.92,
            entity_slots:   &["site"],
        },
        IntentPattern {
            id:             "browser_open_url",
            pattern:        r"(?i)\b(open|navigate to|go to|visit)\s+(website\s+)?(?P<url>https?://[\S]+|[\w\-]+\.[\w\-\.]+)\b",
            category:       IntentCategory::Browser,
            execution_type: ExecutionType::Sync,
            weight:         0.90,
            entity_slots:   &["url"],
        },
        IntentPattern {
            id:             "browser_open_generic",
            pattern:        r"(?i)\bopen (the )?(website|webpage|site|browser|page)\b",
            category:       IntentCategory::Browser,
            execution_type: ExecutionType::Async,
            weight:         0.78,
            entity_slots:   &[],
        },
    ]
}
