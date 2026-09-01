-- runtime/engine/config.lua
-- Pandora runtime knobs only.
-- Matcher/regex knobs stay in Rust defaults (EngineConfig) — they are
-- consumed at compile time during REGISTRY startup and cannot be
-- reloaded without a full engine rebuild.

return {
    -- Intent routing
    min_confidence   = 0.60,   -- drop results below this threshold
    multi_intent     = true,   -- collect all matches above threshold
    max_intents      = 8,      -- sink cap per utterance

    -- Wakeword
    wakeword         = "pandora",
    strip_wakeword   = true,

    -- Streaming path
    stream_threshold = 0.70,   -- process_stream() early-exit confidence
}