from __future__ import annotations

from unittest import TestCase

from logbook.classifier import classify_transcript


class ClassifierTests(TestCase):
    def test_routes_log_entry_and_strips_prefix(self) -> None:
        result = classify_transcript("Log entry: finished the router tests.")

        self.assertEqual(result.route_kind, "log")
        self.assertEqual(result.matched_alias, "log entry")
        self.assertEqual(result.content, "finished the router tests.")

    def test_routes_constrained_log_entry_variant(self) -> None:
        result = classify_transcript("Okay lock entry follow up on recorder cleanup.")

        self.assertEqual(result.route_kind, "log")
        self.assertEqual(result.content, "follow up on recorder cleanup.")

    def test_routes_category_prefix(self) -> None:
        result = classify_transcript("To do: add route telemetry later.")

        self.assertEqual(result.route_kind, "category")
        self.assertEqual(result.category, "task")
        self.assertEqual(result.content, "add route telemetry later.")

    def test_unknown_prefix_becomes_dead_letter(self) -> None:
        result = classify_transcript("A wandering note with no command prefix.")

        self.assertEqual(result.route_kind, "dead_letter")
        self.assertEqual(result.content, "A wandering note with no command prefix.")
