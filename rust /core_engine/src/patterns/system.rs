/// System pattern registry — system controls, app management, files, config,
/// self-repair, terminal, and query intents.
///
/// Sources: Jarvis `process_command()` system scan block, app manager block,
/// file handler block, config change block, self-repair block, terminal block,
/// time/date/version query block.
use crate::intent::{ExecutionType, IntentCategory, IntentPattern};

pub fn all() -> Vec<IntentPattern> {
    vec![
        // ── Time / Date / Version ─────────────────────────────────────────────
        // Jarvis: "time" → speak datetime; "date" → speak date
        IntentPattern {
            id:             "query_time",
            pattern:        r"(?i)\b(what(?:'?s| is) the time|current time|tell me the time|time (now|please)?)\b",
            category:       IntentCategory::Query,
            execution_type: ExecutionType::Sync,
            weight:         0.92,
            entity_slots:   &[],
        },
        IntentPattern {
            id:             "query_date",
            pattern:        r"(?i)\b(what(?:'?s| is) (the |today'?s )?date|current date|today'?s date|what day is it)\b",
            category:       IntentCategory::Query,
            execution_type: ExecutionType::Sync,
            weight:         0.92,
            entity_slots:   &[],
        },
        // Jarvis: "check your version", "tell me the version"
        IntentPattern {
            id:             "query_version",
            pattern:        r"(?i)\b(check (your |the )?version|tell me the version|what(?:'?s| is) (your )?version|current version)\b",
            category:       IntentCategory::Query,
            execution_type: ExecutionType::Sync,
            weight:         0.88,
            entity_slots:   &[],
        },

        // ── System status / monitor ───────────────────────────────────────────
        // Jarvis: "system status", "system monitor", "system health"
        IntentPattern {
            id:             "system_status",
            pattern:        r"(?i)\b(system (status|monitor|health|report|diagnostics?)|how'?s the system|cpu (usage|load)|memory usage|ram usage|disk usage)\b",
            category:       IntentCategory::System,
            execution_type: ExecutionType::Async,
            weight:         0.90,
            entity_slots:   &[],
        },

        // ── System scans ─────────────────────────────────────────────────────
        // Jarvis: "system scan", "run sfc", "check disk", "flush dns", "cleanup"
        IntentPattern {
            id:             "system_scan_sfc",
            pattern:        r"(?i)\b(run sfc|sfc scan|system file (check|checker)|scan system files)\b",
            category:       IntentCategory::System,
            execution_type: ExecutionType::Subprocess,
            weight:         0.93,
            entity_slots:   &[],
        },
        IntentPattern {
            id:             "system_scan_disk",
            pattern:        r"(?i)\b(disk check|check disk|chkdsk|run chkdsk|check (the )?disk)\b",
            category:       IntentCategory::System,
            execution_type: ExecutionType::Subprocess,
            weight:         0.93,
            entity_slots:   &[],
        },
        IntentPattern {
            id:             "disk_cleanup",
            pattern:        r"(?i)\b(disk cleanup|run cleanup|clean (the )?disk|free up (disk )?space|cleanmgr)\b",
            category:       IntentCategory::System,
            execution_type: ExecutionType::Subprocess,
            weight:         0.90,
            entity_slots:   &[],
        },
        IntentPattern {
            id:             "flush_dns",
            pattern:        r"(?i)\b(flush dns|clear dns (cache)?|dns flush|reset dns)\b",
            category:       IntentCategory::System,
            execution_type: ExecutionType::Subprocess,
            weight:         0.92,
            entity_slots:   &[],
        },

        // ── Microphone test ───────────────────────────────────────────────────
        // Jarvis: "test microphone", "check mic", "voice test"
        IntentPattern {
            id:             "mic_test",
            pattern:        r"(?i)\b(test (the )?microphone|check (the )?mic|voice test|microphone (test|check)|test (my )?voice)\b",
            category:       IntentCategory::System,
            execution_type: ExecutionType::Async,
            weight:         0.88,
            entity_slots:   &[],
        },

        // ── App control ───────────────────────────────────────────────────────
        // Jarvis: "launch", "start", "open", "close", "exit", "terminate", "kill"
        IntentPattern {
            id:             "app_open",
            pattern:        r"(?i)\b(launch|start|open|run|access)\s+(?P<app>[a-zA-Z][\w\s]{1,30}?)\s*(app|application|program|software)?\b",
            category:       IntentCategory::AppControl,
            execution_type: ExecutionType::Async,
            weight:         0.85,
            entity_slots:   &["app"],
        },
        IntentPattern {
            id:             "app_close",
            pattern:        r"(?i)\b(close|exit|terminate|kill|quit|shut down)\s+(?P<app>[a-zA-Z][\w\s]{1,30}?)\s*(app|application|program|window)?\b",
            category:       IntentCategory::AppControl,
            execution_type: ExecutionType::Async,
            weight:         0.87,
            entity_slots:   &["app"],
        },
        IntentPattern {
            id:             "app_close_active",
            pattern:        r"(?i)\b(close (the )?(active|current|this) (window|app|application)|close what'?s (open|running))\b",
            category:       IntentCategory::AppControl,
            execution_type: ExecutionType::Sync,
            weight:         0.88,
            entity_slots:   &[],
        },

        // ── File operations ───────────────────────────────────────────────────
        // Jarvis: "create file", "read file", "delete file", "search files", "organize files"
        IntentPattern {
            id:             "file_create",
            pattern:        r"(?i)\b(create (a )?file|make (a )?new file|new file)\s*(called|named)?\s*(?P<filename>[\w\.\-]+)?\b",
            category:       IntentCategory::Files,
            execution_type: ExecutionType::Sync,
            weight:         0.88,
            entity_slots:   &["filename"],
        },
        IntentPattern {
            id:             "file_read",
            pattern:        r"(?i)\b(read (the )?file|open (the )?file|show (me )?(the )?file|display (the )?file)\s+(?P<filename>[\w\.\-\/\\]+)\b",
            category:       IntentCategory::Files,
            execution_type: ExecutionType::Sync,
            weight:         0.87,
            entity_slots:   &["filename"],
        },
        IntentPattern {
            id:             "file_delete",
            pattern:        r"(?i)\b(delete (the )?file|remove (the )?file)\s+(?P<filename>[\w\.\-\/\\]+)\b",
            category:       IntentCategory::Files,
            execution_type: ExecutionType::Sync,
            weight:         0.92,
            entity_slots:   &["filename"],
        },
        IntentPattern {
            id:             "file_search",
            pattern:        r"(?i)\b(search (for )?files?|find files?|look for files?)\b",
            category:       IntentCategory::Files,
            execution_type: ExecutionType::Async,
            weight:         0.82,
            entity_slots:   &[],
        },
        IntentPattern {
            id:             "file_organise",
            pattern:        r"(?i)\b(organis[ez]|organis[ez] (my )?files?|sort (my )?files?|clean up (my )?(files?|directory|folder|downloads|desktop))\b",
            category:       IntentCategory::Files,
            execution_type: ExecutionType::Async,
            weight:         0.83,
            entity_slots:   &[],
        },
        IntentPattern {
            id:             "file_info",
            pattern:        r"(?i)\b(file info|file (details?|properties?|stats?)|info (about|on|for) (the )?file)\s+(?P<filename>[\w\.\-\/\\]+)\b",
            category:       IntentCategory::Files,
            execution_type: ExecutionType::Sync,
            weight:         0.80,
            entity_slots:   &["filename"],
        },

        // ── AI model config ───────────────────────────────────────────────────
        // Jarvis: "change ai model to gpt|gemini|openrouter|mistral"
        IntentPattern {
            id:             "config_ai_model",
            pattern:        r"(?i)\b(change (ai |the )?model to|switch (to |the )?(ai )?model|use (the )?(ai )?model)\s+(?P<model>gpt|gemini|openrouter|mistral|claude)\b",
            category:       IntentCategory::Config,
            execution_type: ExecutionType::Sync,
            weight:         0.92,
            entity_slots:   &["model"],
        },
        IntentPattern {
            id:             "config_start_web",
            pattern:        r"(?i)\b(start web mode|launch (the )?web interface|web mode|enable web ui)\b",
            category:       IntentCategory::Config,
            execution_type: ExecutionType::Async,
            weight:         0.88,
            entity_slots:   &[],
        },

        // ── Self-repair ───────────────────────────────────────────────────────
        // Jarvis: "self repair", "initiate repair", "diagnose system"
        IntentPattern {
            id:             "self_repair",
            pattern:        r"(?i)\b(self (repair|heal|fix)|initiate (self )?repair|run repair|fix (your)?self|repair (the )?system)\b",
            category:       IntentCategory::SelfRepair,
            execution_type: ExecutionType::Async,
            weight:         0.94,
            entity_slots:   &[],
        },
        IntentPattern {
            id:             "backup_create",
            pattern:        r"(?i)\b(create (a )?backup|backup (jarvis|system|everything|files?)|make (a )?backup)\b",
            category:       IntentCategory::SelfRepair,
            execution_type: ExecutionType::Async,
            weight:         0.90,
            entity_slots:   &[],
        },
        IntentPattern {
            id:             "backup_status",
            pattern:        r"(?i)\b(backup status|check (the )?backups?|how many backups?|list backups?)\b",
            category:       IntentCategory::SelfRepair,
            execution_type: ExecutionType::Sync,
            weight:         0.85,
            entity_slots:   &[],
        },

        // ── Terminal operations ───────────────────────────────────────────────
        // Jarvis: "initiate terminal operation", "start terminal mode"
        IntentPattern {
            id:             "terminal_mode",
            pattern:        r"(?i)\b(initiate terminal (operation|mode)|start terminal mode|launch terminal (operator|mode)|terminal (ops|operations))\b",
            category:       IntentCategory::Terminal,
            execution_type: ExecutionType::Subprocess,
            weight:         0.90,
            entity_slots:   &[],
        },

        // ── Commands cheatsheet ───────────────────────────────────────────────
        // Jarvis: "cheatsheet", "commands list", "commands help"
        IntentPattern {
            id:             "commands_help",
            pattern:        r"(?i)\b(cheat ?sheet|commands? list|list (of )?commands?|commands? help|what can you do|what commands?)\b",
            category:       IntentCategory::Query,
            execution_type: ExecutionType::Sync,
            weight:         0.82,
            entity_slots:   &[],
        },
    ]
}
