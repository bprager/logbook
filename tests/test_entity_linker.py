import tempfile
from datetime import date
from pathlib import Path
from unittest import TestCase

from logbook.entity_linker import discover_entities, link_daily_log_entities


class EntityLinkerTests(TestCase):
    def test_links_daily_logs_in_window_without_touching_frontmatter_or_existing_links(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            vault = Path(temp_dir)
            _write(
                vault / "04 - People" / "Quinn Wolf Prager.md",
                """---
aliases:
  - QWP
---
# Quinn
""",
            )
            _write(vault / "04 - People" / "Samadarshee Panda.md", "# Samadarshee\n")
            _write(
                vault / "03 - Objects" / "2026-04-26 Sony ICD-PX370 Mono Digital Voice Recorder.md",
                "# Sony ICD-PX370\n",
            )
            _write(
                vault
                / "06 - Timestamps"
                / "Meetings"
                / "2026"
                / "2026-04-01 School Transfer.md",
                "# School Transfer\n",
            )
            daily_log = (
                vault
                / "06 - Timestamps"
                / "2026"
                / "04-April"
                / "2026-04-30-Thursday-Log.md"
            )
            _write(
                daily_log,
                """---
type: "daily_log"
note: "Quinn should stay plain here"
---

# Thursday, April 30, 2026 Log

Quinn talked to QWP about the Sony ICD-PX370 Mono Digital Voice Recorder.
Existing [[04 - People/Quinn Wolf Prager|Quinn]] stays as-is.
[Quinn docs](https://example.test) stay as-is.

```text
Quinn in code stays plain.
```

School Transfer was discussed.
""",
            )
            old_log = (
                vault
                / "06 - Timestamps"
                / "2025"
                / "12-December"
                / "2025-12-31-Wednesday-Log.md"
            )
            _write(old_log, "Quinn stays outside the window.\n")

            dry_run = link_daily_log_entities(
                vault_root=vault,
                months=3,
                execute=False,
                today=date(2026, 5, 1),
            )

            self.assertEqual(dry_run.files_considered, 1)
            self.assertEqual(dry_run.files_changed, 1)
            self.assertEqual(dry_run.inserted_count, 4)
            self.assertIn("Quinn talked to QWP", daily_log.read_text(encoding="utf-8"))

            result = link_daily_log_entities(
                vault_root=vault,
                months=3,
                execute=True,
                today=date(2026, 5, 1),
            )

            self.assertEqual(result.inserted_count, 4)
            content = daily_log.read_text(encoding="utf-8")
            self.assertIn('note: "Quinn should stay plain here"', content)
            self.assertIn("[[04 - People/Quinn Wolf Prager|Quinn]] talked", content)
            self.assertIn("[[04 - People/Quinn Wolf Prager|QWP]]", content)
            self.assertIn(
                "[[03 - Objects/2026-04-26 Sony ICD-PX370 Mono Digital Voice Recorder|"
                "Sony ICD-PX370 Mono Digital Voice Recorder]]",
                content,
            )
            self.assertIn("[[06 - Timestamps/Meetings/2026/2026-04-01 School Transfer|School Transfer]]", content)
            self.assertIn("[Quinn docs](https://example.test) stay as-is", content)
            self.assertIn("Quinn in code stays plain.", content)
            self.assertEqual(old_log.read_text(encoding="utf-8"), "Quinn stays outside the window.\n")

    def test_ambiguous_first_names_are_not_linked(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            vault = Path(temp_dir)
            _write(vault / "04 - People" / "Sam One.md", "# Sam One\n")
            _write(vault / "04 - People" / "Sam Two.md", "# Sam Two\n")
            daily_log = (
                vault
                / "06 - Timestamps"
                / "2026"
                / "04-April"
                / "2026-04-30-Thursday-Log.md"
            )
            _write(daily_log, "Sam met Sam One.\n")

            result = link_daily_log_entities(
                vault_root=vault,
                months=3,
                execute=True,
                today=date(2026, 5, 1),
            )

            self.assertEqual(result.inserted_count, 1)
            self.assertEqual(
                daily_log.read_text(encoding="utf-8"),
                "Sam met [[04 - People/Sam One|Sam One]].\n",
            )

    def test_discovers_aliases_from_supported_entity_roots(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            vault = Path(temp_dir)
            _write(
                vault / "04 - People" / "Quinn Wolf Prager.md",
                """---
aliases: ["QWP", "Quinny"]
---
""",
            )
            _write(
                vault / "03 - Objects" / "2026-04-26 Sony ICD-PX370 Mono Digital Voice Recorder.md",
                "",
            )

            entities = discover_entities(vault)
            aliases = {
                alias
                for entity in entities
                for alias in entity.aliases
            }

            self.assertIn("Quinn Wolf Prager", aliases)
            self.assertIn("Quinn", aliases)
            self.assertIn("QWP", aliases)
            self.assertIn("Sony ICD-PX370 Mono Digital Voice Recorder", aliases)


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
