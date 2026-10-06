from __future__ import annotations

from unittest import TestCase

from logbook.classifier import classify_transcript


class ClassifierTests(TestCase):
    def test_routes_log_entry_and_strips_prefix(self) -> None:
        result = classify_transcript("Log entry: finished the router tests.")

        self.assertEqual(result.route_kind, "log")
        self.assertEqual(result.matched_alias, "log entry")
        self.assertEqual(result.content, "finished the router tests.")

    def test_routes_article_log_entry_and_strips_prefix(self) -> None:
        result = classify_transcript("A log entry, pick up the kids at 4 p.m.")

        self.assertEqual(result.route_kind, "log")
        self.assertEqual(result.matched_alias, "a log entry")
        self.assertEqual(result.content, "pick up the kids at 4 p.m.")

    def test_routes_constrained_log_entry_variant(self) -> None:
        result = classify_transcript("Okay lock entry follow up on recorder cleanup.")

        self.assertEqual(result.route_kind, "log")
        self.assertEqual(result.content, "follow up on recorder cleanup.")

    def test_routes_asr_log_record_variants(self) -> None:
        lock_record = classify_transcript("Lock record. I bought groceries.")
        block_entry = classify_transcript("Block entry. Onion was rotten.")

        self.assertEqual(lock_record.route_kind, "log")
        self.assertEqual(lock_record.content, "I bought groceries.")
        self.assertEqual(block_entry.route_kind, "log")
        self.assertEqual(block_entry.content, "Onion was rotten.")

    def test_routes_category_prefix(self) -> None:
        result = classify_transcript("To do: add route telemetry later.")

        self.assertEqual(result.route_kind, "category")
        self.assertEqual(result.category, "task")
        self.assertEqual(result.content, "add route telemetry later.")

    def test_unknown_prefix_becomes_dead_letter(self) -> None:
        result = classify_transcript("A wandering note with no command prefix.")

        self.assertEqual(result.route_kind, "dead_letter")
        self.assertEqual(result.content, "A wandering note with no command prefix.")

    def test_observed_diary_marker_variants(self) -> None:
        for prefix in ("a lock entry,", "locked entry"):
            with self.subTest(prefix=prefix):
                result = classify_transcript(prefix + " Today I took a walk.")
                self.assertEqual(result.route_kind, "log")
                self.assertEqual(result.content, "Today I took a walk.")

    def test_marker_words_in_body_do_not_route_as_diary(self) -> None:
        for text in ("I locked entry to the building.", "A lock needs repair.", "Meeting locked entry discussion."):
            self.assertNotEqual(classify_transcript(text).route_kind, "log")
