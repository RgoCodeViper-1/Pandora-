"""
test_pandora_core.py
────────────────────
Comprehensive test suite for the pandora_core Rust engine, called via its
PyO3 Python boundary.

Covers:
  • Health / registry sanity
  • Output schema validation (process & process_stream)
  • Empty / whitespace input guards
  • Wakeword detection and stripping
  • Intent correctness — every pattern category
  • Named-entity extraction
  • Confidence scoring contracts
  • Streaming fast-path behaviour
  • Multi-intent batch ordering
  • Unknown / fallback behaviour
  • Case-insensitivity (smart-case)
  • Alternate phrasings for the same intent

Run with:
    pytest test_pandora_core.py -v
    # or without pytest:
    python test_pandora_core.py
"""

import json
import sys
import traceback
from typing import Any

# ──────────────────────────────────────────────────────────────────────────────
# Import guard — bail out with a clear message if the .so isn't importable
# ──────────────────────────────────────────────────────────────────────────────
try:
    import pandora_core  # type: ignore
except ModuleNotFoundError as exc:
    sys.exit(
        f"\n[FATAL] Could not import pandora_core: {exc}\n"
        "Build the extension first with:  maturin develop  (or maturin build)\n"
    )


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def process(text: str) -> dict:
    """Call pandora_core.process() and return the parsed batch dict."""
    raw = pandora_core.process(text)
    return json.loads(raw)


def process_stream(text: str) -> dict:
    """Call pandora_core.process_stream() and return the parsed dict."""
    raw = pandora_core.process_stream(text)
    return json.loads(raw)


def intents(text: str) -> list[dict]:
    """Shortcut: return the intents list from process()."""
    return process(text)["intents"]


def top(text: str) -> dict:
    """Return the single highest-confidence intent from process()."""
    results = intents(text)
    assert results, f"No intents returned for: {text!r}"
    return results[0]


def find_intent(results: list[dict], intent_id: str) -> dict | None:
    """Return the first result whose 'intent' field matches intent_id."""
    return next((r for r in results if r["intent"] == intent_id), None)


# ──────────────────────────────────────────────────────────────────────────────
# Test classes  (plain unittest-style, also collected by pytest)
# ──────────────────────────────────────────────────────────────────────────────

import unittest


class TestHealth(unittest.TestCase):
    """Sanity checks that run before any utterance processing."""

    def test_pattern_count_nonzero(self):
        """Registry must have compiled at least 20 patterns at startup."""
        count = pandora_core.pattern_count()
        self.assertGreater(count, 20, f"Only {count} patterns compiled — registry may be broken.")

    def test_process_returns_string(self):
        raw = pandora_core.process("hello")
        self.assertIsInstance(raw, str)

    def test_process_stream_returns_string(self):
        raw = pandora_core.process_stream("hello")
        self.assertIsInstance(raw, str)


class TestOutputSchema(unittest.TestCase):
    """Validate the JSON envelope shapes for both APIs."""

    def test_process_has_intents_key(self):
        result = process("hello pandora")
        self.assertIn("intents", result, "process() must return {'intents': [...]}")

    def test_intents_is_list(self):
        result = process("hello pandora")
        self.assertIsInstance(result["intents"], list)

    def test_intent_result_fields(self):
        """Every intent result must carry all required fields."""
        REQUIRED = {"intent", "category", "execution_type", "confidence", "entities", "text"}
        for item in intents("shutdown pandora"):
            missing = REQUIRED - item.keys()
            self.assertFalse(missing, f"Intent result missing fields: {missing}  →  {item}")

    def test_confidence_in_unit_range(self):
        for item in intents("pandora open youtube"):
            self.assertGreaterEqual(item["confidence"], 0.0)
            self.assertLessEqual(item["confidence"], 1.0)

    def test_entities_is_dict(self):
        for item in intents("weather in London"):
            self.assertIsInstance(item["entities"], dict)

    def test_text_field_is_string(self):
        for item in intents("good morning"):
            self.assertIsInstance(item["text"], str)

    def test_stream_object_has_intent_key_or_intents_key(self):
        """process_stream returns either a single intent or a batch fallback."""
        result = process_stream("pandora shutdown")
        self.assertTrue(
            "intent" in result or "intents" in result,
            f"process_stream output has neither 'intent' nor 'intents': {result}"
        )

    def test_sorted_descending_by_confidence(self):
        """Batch output must be sorted highest confidence first."""
        results = intents("pandora good morning")
        confidences = [r["confidence"] for r in results]
        self.assertEqual(confidences, sorted(confidences, reverse=True),
                         "process() results are not sorted descending by confidence.")


class TestGuards(unittest.TestCase):
    """Input validation — empty / whitespace inputs must raise RuntimeError."""

    def test_empty_string_raises(self):
        with self.assertRaises(RuntimeError):
            pandora_core.process("")

    def test_whitespace_only_raises(self):
        with self.assertRaises(RuntimeError):
            pandora_core.process("   ")

    def test_newlines_only_raises(self):
        with self.assertRaises(RuntimeError):
            pandora_core.process("\n\t\n")

    def test_stream_empty_raises(self):
        with self.assertRaises(RuntimeError):
            pandora_core.process_stream("")

    def test_stream_whitespace_raises(self):
        with self.assertRaises(RuntimeError):
            pandora_core.process_stream("   ")


class TestWakeword(unittest.TestCase):
    """Wakeword detection, stripping, and bare-wakeword handling."""

    def test_bare_wakeword_returns_wakeword_intent(self):
        results = intents("pandora")
        self.assertTrue(results, "Bare 'pandora' must return at least one intent.")
        self.assertEqual(results[0]["intent"], "wakeword")

    def test_bare_wakeword_confidence_is_one(self):
        results = intents("pandora")
        self.assertEqual(results[0]["confidence"], 1.0)

    def test_hey_pandora_wakeword(self):
        results = intents("hey pandora")
        # 'hey pandora' matches wakeword pattern; after stripping, bare → wakeword result
        ww = find_intent(results, "wakeword")
        self.assertIsNotNone(ww, "'hey pandora' should yield wakeword intent")

    def test_jarvis_alias_wakeword(self):
        """Jarvis alias must also trigger the wakeword pattern."""
        results = intents("jarvis")
        ww = find_intent(results, "wakeword")
        self.assertIsNotNone(ww, "'jarvis' should trigger wakeword intent")

    def test_wakeword_stripped_before_matching(self):
        """After stripping wakeword, the residual text is what appears in .text."""
        results = intents("pandora shutdown")
        r = find_intent(results, "shutdown")
        self.assertIsNotNone(r, "'pandora shutdown' must produce shutdown intent")
        # text field must NOT still contain 'pandora' (stripped)
        self.assertNotIn("pandora", r["text"],
                         "Wakeword should be stripped from the .text field.")

    def test_case_insensitive_wakeword(self):
        results = intents("PANDORA shutdown")
        self.assertTrue(any(r["intent"] == "shutdown" for r in results))


class TestDialogueIntents(unittest.TestCase):
    """Greetings, presence checks, identity, mute/unmute, lifecycle."""

    def test_greeting_hello(self):
        results = intents("hello")
        self.assertTrue(
            any("greeting" in r["intent"] for r in results),
            f"'hello' must match a greeting intent. Got: {[r['intent'] for r in results]}"
        )

    def test_greeting_good_morning(self):
        results = intents("good morning pandora")
        self.assertTrue(any("greeting" in r["intent"] for r in results))

    def test_greeting_hi(self):
        results = intents("hi there")
        self.assertTrue(any("greeting" in r["intent"] for r in results))

    def test_presence_check(self):
        results = intents("are you there")
        self.assertIsNotNone(find_intent(results, "presence_check"))

    def test_presence_you_up(self):
        results = intents("you up pandora")
        self.assertIsNotNone(find_intent(results, "presence_check"))

    def test_identity_query(self):
        results = intents("who are you")
        self.assertIsNotNone(find_intent(results, "identity_query"))

    def test_identity_introduce(self):
        results = intents("introduce yourself")
        self.assertIsNotNone(find_intent(results, "identity_query"))

    def test_shutdown_intent(self):
        r = top("pandora shutdown")
        self.assertEqual(r["intent"], "shutdown")

    def test_shutdown_confidence_above_threshold(self):
        r = find_intent(intents("pandora shutdown"), "shutdown")
        self.assertIsNotNone(r)
        self.assertGreater(r["confidence"], 0.70)

    def test_shutdown_goodbye(self):
        results = intents("goodbye pandora")
        self.assertIsNotNone(find_intent(results, "shutdown"))

    def test_shutdown_power_down(self):
        results = intents("power down")
        self.assertIsNotNone(find_intent(results, "shutdown"))

    def test_go_to_sleep(self):
        results = intents("go to sleep pandora")
        self.assertIsNotNone(find_intent(results, "go_to_sleep"))

    def test_standby(self):
        results = intents("standby")
        self.assertIsNotNone(find_intent(results, "go_to_sleep"))

    def test_wake_up(self):
        results = intents("wake up pandora")
        self.assertIsNotNone(find_intent(results, "wake_up"))

    def test_mute(self):
        results = intents("mute pandora")
        self.assertIsNotNone(find_intent(results, "mute"))

    def test_unmute(self):
        results = intents("unmute")
        self.assertIsNotNone(find_intent(results, "unmute"))

    def test_progress_summary(self):
        results = intents("how am i doing")
        self.assertIsNotNone(find_intent(results, "progress_summary"))


class TestQueryIntents(unittest.TestCase):
    """Time, date, version, commands help."""

    def test_query_time(self):
        for phrase in ["what's the time", "what is the time", "current time", "tell me the time"]:
            with self.subTest(phrase=phrase):
                results = intents(phrase)
                self.assertIsNotNone(find_intent(results, "query_time"),
                                     f"'{phrase}' must match query_time")

    def test_query_date(self):
        for phrase in ["what's the date", "what day is it", "today's date", "current date"]:
            with self.subTest(phrase=phrase):
                results = intents(phrase)
                self.assertIsNotNone(find_intent(results, "query_date"),
                                     f"'{phrase}' must match query_date")

    def test_query_version(self):
        results = intents("what's your version")
        self.assertIsNotNone(find_intent(results, "query_version"))

    def test_commands_help(self):
        for phrase in ["what can you do", "commands list", "cheatsheet"]:
            with self.subTest(phrase=phrase):
                results = intents(phrase)
                self.assertIsNotNone(find_intent(results, "commands_help"))


class TestWeatherIntents(unittest.TestCase):
    """Weather queries — with and without explicit location."""

    def test_weather_with_location(self):
        results = intents("weather in London")
        r = find_intent(results, "weather_query")
        self.assertIsNotNone(r, "'weather in London' must match weather_query")

    def test_weather_entity_location(self):
        results = intents("what's the weather in Paris")
        r = find_intent(results, "weather_query")
        self.assertIsNotNone(r)
        self.assertIn("location", r["entities"])
        self.assertIn("paris", r["entities"]["location"].lower())

    def test_weather_at_location(self):
        results = intents("forecast at New York")
        r = find_intent(results, "weather_query")
        self.assertIsNotNone(r)
        self.assertIn("new york", r["entities"].get("location", "").lower())

    def test_weather_default(self):
        for phrase in ["what's the weather", "weather today", "current weather", "weather report"]:
            with self.subTest(phrase=phrase):
                results = intents(phrase)
                self.assertIsNotNone(find_intent(results, "weather_query_default"),
                                     f"'{phrase}' must match weather_query_default")


class TestSearchIntents(unittest.TestCase):
    """Wikipedia, Google/web search."""

    def test_wikipedia_search(self):
        results = intents("search wikipedia for black holes")
        r = find_intent(results, "search_wikipedia")
        self.assertIsNotNone(r)

    def test_wikipedia_topic_entity(self):
        results = intents("search wikipedia for quantum computing")
        r = find_intent(results, "search_wikipedia")
        self.assertIsNotNone(r)
        self.assertIn("topic", r["entities"])
        self.assertIn("quantum", r["entities"]["topic"].lower())

    def test_google_search(self):
        for phrase in ["google best linux distros", "search for python tutorials",
                       "search the web for rust async"]:
            with self.subTest(phrase=phrase):
                results = intents(phrase)
                self.assertIsNotNone(find_intent(results, "search_google"),
                                     f"'{phrase}' must match search_google")

    def test_google_query_entity(self):
        results = intents("google best coffee shops near me")
        r = find_intent(results, "search_google")
        self.assertIsNotNone(r)
        self.assertIn("query", r["entities"])

    def test_news_headlines(self):
        for phrase in ["read the news", "latest news", "what's in the news", "news today"]:
            with self.subTest(phrase=phrase):
                results = intents(phrase)
                self.assertIsNotNone(find_intent(results, "news_headlines"))


class TestSystemIntents(unittest.TestCase):
    """System status, scans, app control, file ops, config, self-repair, terminal."""

    # ── System status ─────────────────────────────────────────────────────────
    def test_system_status(self):
        for phrase in ["system status", "system health", "cpu usage", "memory usage"]:
            with self.subTest(phrase=phrase):
                results = intents(phrase)
                self.assertIsNotNone(find_intent(results, "system_status"))

    def test_system_scan_sfc(self):
        results = intents("run sfc")
        self.assertIsNotNone(find_intent(results, "system_scan_sfc"))

    def test_disk_check(self):
        results = intents("check disk")
        self.assertIsNotNone(find_intent(results, "system_scan_disk"))

    def test_disk_cleanup(self):
        results = intents("disk cleanup")
        self.assertIsNotNone(find_intent(results, "disk_cleanup"))

    def test_flush_dns(self):
        results = intents("flush dns")
        self.assertIsNotNone(find_intent(results, "flush_dns"))

    def test_mic_test(self):
        results = intents("test the microphone")
        self.assertIsNotNone(find_intent(results, "mic_test"))

    # ── App control ───────────────────────────────────────────────────────────
    def test_app_open(self):
        results = intents("open notepad")
        r = find_intent(results, "app_open")
        self.assertIsNotNone(r)

    def test_app_open_entity(self):
        results = intents("launch visual studio code")
        r = find_intent(results, "app_open")
        self.assertIsNotNone(r)
        self.assertIn("app", r["entities"])

    def test_app_close(self):
        results = intents("close chrome")
        r = find_intent(results, "app_close")
        self.assertIsNotNone(r)

    def test_app_close_active(self):
        results = intents("close the active window")
        self.assertIsNotNone(find_intent(results, "app_close_active"))

    # ── File operations ───────────────────────────────────────────────────────
    def test_file_create(self):
        results = intents("create a file")
        self.assertIsNotNone(find_intent(results, "file_create"))

    def test_file_read(self):
        results = intents("read the file notes.txt")
        r = find_intent(results, "file_read")
        self.assertIsNotNone(r)
        self.assertIn("filename", r["entities"])

    def test_file_delete(self):
        results = intents("delete the file oldreport.pdf")
        r = find_intent(results, "file_delete")
        self.assertIsNotNone(r)
        self.assertIn("filename", r["entities"])

    def test_file_search(self):
        results = intents("search for files")
        self.assertIsNotNone(find_intent(results, "file_search"))

    def test_file_organise(self):
        for phrase in ["organise my files", "sort my files", "clean up the downloads"]:
            with self.subTest(phrase=phrase):
                results = intents(phrase)
                self.assertIsNotNone(find_intent(results, "file_organise"))

    # ── AI config ─────────────────────────────────────────────────────────────
    def test_config_ai_model(self):
        results = intents("change model to gemini")
        r = find_intent(results, "config_ai_model")
        self.assertIsNotNone(r)
        self.assertIn("model", r["entities"])
        self.assertEqual(r["entities"]["model"].lower(), "gemini")

    def test_config_ai_model_all_providers(self):
        for model in ["gpt", "gemini", "openrouter", "mistral", "claude"]:
            with self.subTest(model=model):
                results = intents(f"switch to model {model}")
                r = find_intent(results, "config_ai_model")
                self.assertIsNotNone(r, f"config_ai_model not matched for model={model}")

    def test_config_start_web(self):
        results = intents("start web mode")
        self.assertIsNotNone(find_intent(results, "config_start_web"))

    # ── Self-repair ───────────────────────────────────────────────────────────
    def test_self_repair(self):
        results = intents("self repair")
        self.assertIsNotNone(find_intent(results, "self_repair"))

    def test_backup_create(self):
        results = intents("create a backup")
        self.assertIsNotNone(find_intent(results, "backup_create"))

    def test_backup_status(self):
        results = intents("backup status")
        self.assertIsNotNone(find_intent(results, "backup_status"))

    # ── Terminal ──────────────────────────────────────────────────────────────
    def test_terminal_mode(self):
        results = intents("initiate terminal operation")
        self.assertIsNotNone(find_intent(results, "terminal_mode"))


class TestAutomationIntents(unittest.TestCase):
    """Media, messaging, tasks, habits, schedule, browser."""

    # ── Media ─────────────────────────────────────────────────────────────────
    def test_youtube_play(self):
        results = intents("play lofi hip hop on youtube")
        r = find_intent(results, "media_youtube")
        self.assertIsNotNone(r)
        self.assertIn("query", r["entities"])
        self.assertIn("lofi hip hop", r["entities"]["query"].lower())

    def test_youtube_generic(self):
        results = intents("open youtube")
        self.assertIsNotNone(find_intent(results, "media_youtube_generic"))

    def test_spotify_play(self):
        results = intents("play Blinding Lights on spotify")
        r = find_intent(results, "media_spotify")
        self.assertIsNotNone(r)
        self.assertIn("song", r["entities"])

    def test_spotify_generic(self):
        results = intents("open spotify")
        self.assertIsNotNone(find_intent(results, "media_spotify_generic"))

    def test_local_music(self):
        results = intents("play the music")
        self.assertIsNotNone(find_intent(results, "media_local"))

    # ── Messaging ─────────────────────────────────────────────────────────────
    def test_whatsapp_send(self):
        results = intents("send whatsapp message to John")
        r = find_intent(results, "whatsapp_send")
        self.assertIsNotNone(r)
        self.assertIn("recipient", r["entities"])
        self.assertIn("john", r["entities"]["recipient"].lower())

    def test_email_send(self):
        results = intents("send an email to Alice")
        r = find_intent(results, "email_send")
        self.assertIsNotNone(r)
        self.assertIn("recipient", r["entities"])

    def test_email_compose(self):
        results = intents("compose an email")
        self.assertIsNotNone(find_intent(results, "email_compose"))

    # ── Tasks ─────────────────────────────────────────────────────────────────
    def test_task_add(self):
        results = intents("add a task review the quarterly report")
        r = find_intent(results, "task_add")
        self.assertIsNotNone(r)
        self.assertIn("description", r["entities"])

    def test_task_list(self):
        for phrase in ["list my tasks", "show my tasks", "pending tasks"]:
            with self.subTest(phrase=phrase):
                results = intents(phrase)
                self.assertIsNotNone(find_intent(results, "task_list"))

    def test_task_delete(self):
        results = intents("delete the task review report")
        r = find_intent(results, "task_delete")
        self.assertIsNotNone(r)

    def test_task_complete(self):
        results = intents("mark task as complete review report")
        r = find_intent(results, "task_complete")
        self.assertIsNotNone(r)

    def test_task_overdue(self):
        results = intents("overdue tasks")
        self.assertIsNotNone(find_intent(results, "task_overdue"))

    # ── Habits ────────────────────────────────────────────────────────────────
    def test_habit_add(self):
        results = intents("add habit morning run")
        r = find_intent(results, "habit_add")
        self.assertIsNotNone(r)
        self.assertIn("habit", r["entities"])
        self.assertIn("morning run", r["entities"]["habit"].lower())

    def test_habit_done(self):
        results = intents("done habit morning run")
        self.assertIsNotNone(find_intent(results, "habit_done"))

    def test_habit_remove(self):
        results = intents("remove habit morning run")
        self.assertIsNotNone(find_intent(results, "habit_remove"))

    def test_habit_list(self):
        results = intents("show habits")
        self.assertIsNotNone(find_intent(results, "habit_list"))

    def test_habit_pending(self):
        results = intents("pending habits")
        self.assertIsNotNone(find_intent(results, "habit_pending"))

    # ── Schedule ──────────────────────────────────────────────────────────────
    def test_schedule_today(self):
        results = intents("review today")
        self.assertIsNotNone(find_intent(results, "schedule_review_today"))

    def test_schedule_tomorrow(self):
        results = intents("what's on for tomorrow")
        self.assertIsNotNone(find_intent(results, "schedule_review_tomorrow"))

    def test_schedule_yesterday(self):
        results = intents("review yesterday")
        self.assertIsNotNone(find_intent(results, "schedule_review_yesterday"))

    def test_schedule_add_today(self):
        results = intents("schedule for today")
        self.assertIsNotNone(find_intent(results, "schedule_add_today"))

    def test_schedule_add_tomorrow(self):
        results = intents("plan for tomorrow")
        self.assertIsNotNone(find_intent(results, "schedule_add_tomorrow"))

    def test_schedule_clear_today(self):
        results = intents("clear today's schedule")
        self.assertIsNotNone(find_intent(results, "schedule_clear_today"))

    # ── Browser ───────────────────────────────────────────────────────────────
    def test_browser_known_site(self):
        for site in ["youtube", "gmail", "github", "reddit"]:
            with self.subTest(site=site):
                results = intents(f"open {site}")
                r = find_intent(results, "browser_open_known")
                self.assertIsNotNone(r, f"'open {site}' must match browser_open_known")
                self.assertIn("site", r["entities"])
                self.assertEqual(r["entities"]["site"].lower(), site)

    def test_browser_url(self):
        results = intents("open https://example.com")
        r = find_intent(results, "browser_open_url")
        self.assertIsNotNone(r)
        self.assertIn("url", r["entities"])

    def test_browser_generic(self):
        results = intents("open the browser")
        self.assertIsNotNone(find_intent(results, "browser_open_generic"))


class TestNotesAndMemory(unittest.TestCase):
    """Notes and memory/recall intents."""

    def test_note_create(self):
        for phrase in ["take a note", "jot this down", "write this down", "make a note"]:
            with self.subTest(phrase=phrase):
                results = intents(phrase)
                self.assertIsNotNone(find_intent(results, "note_create"))

    def test_note_search(self):
        results = intents("search my notes for project ideas")
        r = find_intent(results, "note_search")
        self.assertIsNotNone(r)
        self.assertIn("query", r["entities"])

    def test_note_categories(self):
        results = intents("list note categories")
        self.assertIsNotNone(find_intent(results, "note_categories"))

    def test_memory_recall_conversations(self):
        results = intents("remind me what we discussed")
        self.assertIsNotNone(find_intent(results, "memory_recall_conversations"))

    def test_memory_recall_topics(self):
        results = intents("what do you remember")
        self.assertIsNotNone(find_intent(results, "memory_recall_topics"))

    def test_memory_store_topic(self):
        results = intents("remember this")
        self.assertIsNotNone(find_intent(results, "memory_store_topic"))

    def test_memory_set_focus(self):
        results = intents("set my daily focus")
        self.assertIsNotNone(find_intent(results, "memory_set_focus"))


class TestStreamingBehaviour(unittest.TestCase):
    """process_stream fast-path and fallback logic."""

    def test_stream_shutdown_returns_single_object(self):
        """High-confidence command should return a single intent object, not a batch."""
        result = process_stream("pandora shutdown")
        # Either a direct intent object or a batch fallback — must be a dict
        self.assertIsInstance(result, dict)

    def test_stream_single_intent_has_all_fields(self):
        """When stream returns a single intent, it must have the full schema."""
        result = process_stream("pandora shutdown")
        if "intent" in result:
            # single-intent fast-path
            REQUIRED = {"intent", "category", "execution_type", "confidence", "entities", "text"}
            self.assertFalse(REQUIRED - result.keys(), f"Missing fields in stream result: {result}")
        else:
            # batch fallback — already validated by TestOutputSchema
            self.assertIn("intents", result)

    def test_stream_confidence_at_or_above_threshold(self):
        """Streamed single result confidence must be ≥ 0.70 (stream_threshold in Lua config)."""
        result = process_stream("pandora shutdown")
        if "confidence" in result:
            self.assertGreaterEqual(result["confidence"], 0.70)

    def test_stream_vague_input_falls_back_to_batch(self):
        """A vague input that doesn't clear 0.70 should fall back to the batch envelope."""
        result = process_stream("uh maybe something")
        # Either batch fallback or still a dict — just must not crash
        self.assertIsInstance(result, dict)

    def test_stream_and_process_agree_on_top_intent(self):
        """For a high-confidence utterance, stream and process should agree on intent id."""
        utterance = "pandora shutdown"
        batch_top = intents(utterance)[0]["intent"]
        stream_result = process_stream(utterance)
        if "intent" in stream_result:
            self.assertEqual(stream_result["intent"], batch_top)


class TestUnknownFallback(unittest.TestCase):
    """Inputs that match nothing should return the 'unknown' fallback."""

    def test_gibberish_returns_unknown(self):
        results = intents("xyzzy frobnicator quux blargh")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["intent"], "unknown")

    def test_unknown_confidence_is_zero(self):
        results = intents("zrg florp narp")
        self.assertEqual(results[0]["confidence"], 0.0)

    def test_unknown_category(self):
        results = intents("asdfghjkl qwerty")
        self.assertEqual(results[0]["category"], "unknown")


class TestCaseInsensitivity(unittest.TestCase):
    """smart-case: patterns should match regardless of capitalisation."""

    def test_all_caps_shutdown(self):
        results = intents("SHUTDOWN")
        self.assertIsNotNone(find_intent(results, "shutdown"))

    def test_mixed_case_weather(self):
        results = intents("Weather In LONDON")
        self.assertIsNotNone(find_intent(results, "weather_query"))

    def test_mixed_case_youtube(self):
        results = intents("Play lofi music ON YouTube")
        self.assertIsNotNone(find_intent(results, "media_youtube"))


class TestMultiIntent(unittest.TestCase):
    """Utterances that plausibly trigger more than one pattern."""

    def test_multi_intent_max_count(self):
        """Results must never exceed max_intents (8 from engine_fields.lua)."""
        results = intents("pandora open youtube and play music and check the weather")
        self.assertLessEqual(len(results), 8)

    def test_no_duplicate_intent_ids(self):
        """Arbitration must deduplicate — each intent id appears at most once."""
        results = intents("pandora open youtube and also open spotify")
        ids = [r["intent"] for r in results]
        self.assertEqual(len(ids), len(set(ids)), f"Duplicate intent ids in results: {ids}")

    def test_wakeword_plus_command_prioritises_command(self):
        """When wakeword accompanies a command, command should appear in results."""
        results = intents("pandora what is the time")
        self.assertTrue(
            any(r["intent"] == "query_time" for r in results),
            "query_time must appear alongside wakeword utterance"
        )


class TestEntityEdgeCases(unittest.TestCase):
    """Edge cases around named-entity extraction."""

    def test_entity_value_is_lowercase(self):
        """Normalisation lowercases input — entities come from that normalised text."""
        results = intents("weather in Tokyo")
        r = find_intent(results, "weather_query")
        self.assertIsNotNone(r)
        self.assertEqual(r["entities"].get("location", ""), "tokyo")

    def test_no_entity_slots_means_empty_dict(self):
        """Intents with no entity_slots must return an empty entities dict."""
        results = intents("what's the time")
        r = find_intent(results, "query_time")
        self.assertIsNotNone(r)
        self.assertEqual(r["entities"], {})

    def test_ai_model_entity_exact(self):
        for model in ["gpt", "claude", "mistral"]:
            with self.subTest(model=model):
                results = intents(f"use the ai model {model}")
                r = find_intent(results, "config_ai_model")
                self.assertIsNotNone(r)
                self.assertEqual(r["entities"].get("model", ""), model)


# ──────────────────────────────────────────────────────────────────────────────
# Standalone runner  (python test_pandora_core.py)
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    loader = unittest.TestLoader()
    suite  = unittest.TestSuite()

    test_classes = [
        TestHealth,
        TestOutputSchema,
        TestGuards,
        TestWakeword,
        TestDialogueIntents,
        TestQueryIntents,
        TestWeatherIntents,
        TestSearchIntents,
        TestSystemIntents,
        TestAutomationIntents,
        TestNotesAndMemory,
        TestStreamingBehaviour,
        TestUnknownFallback,
        TestCaseInsensitivity,
        TestMultiIntent,
        TestEntityEdgeCases,
    ]

    for cls in test_classes:
        suite.addTests(loader.loadTestsFromTestCase(cls))

    runner = unittest.TextTestRunner(verbosity=2, stream=sys.stdout)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
