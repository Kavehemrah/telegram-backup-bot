import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import backup_bot


class BackupStateTests(unittest.TestCase):
    def test_history_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            history_file = Path(tmp) / "history.json"
            with patch.object(backup_bot, "HISTORY_FILE", history_file):
                history = [
                    {
                        "path": "example.txt",
                        "modified": 123,
                        "sent_at": "2026-09-06T12:00:00",
                    }
                ]
                backup_bot.save_history(history)
                self.assertEqual(backup_bot.load_history(), history)

    def test_pending_files_contains_new_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = root / "first.txt"
            second = root / "second.txt"
            first.write_text("one", encoding="utf-8")
            second.write_text("two", encoding="utf-8")

            history_file = root / "history.json"
            with patch.object(backup_bot, "HISTORY_FILE", history_file):
                backup_bot.save_history(
                    [
                        {
                            "path": str(first),
                            "modified": first.stat().st_mtime_ns,
                            "sent_at": "2026-09-06T12:00:00",
                        }
                    ]
                )
                pending = backup_bot.get_pending_files(root)

            self.assertEqual(pending, [second])

    def test_modified_file_becomes_pending_again(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "document.txt"
            target.write_text("version 1", encoding="utf-8")
            old_mtime = target.stat().st_mtime_ns

            history_file = root / "history.json"
            with patch.object(backup_bot, "HISTORY_FILE", history_file):
                backup_bot.save_history(
                    [
                        {
                            "path": str(target),
                            "modified": old_mtime,
                            "sent_at": "2026-09-06T12:00:00",
                        }
                    ]
                )
                target.write_text("version 2", encoding="utf-8")
                pending = backup_bot.get_pending_files(root)

            self.assertEqual(pending, [target])


if __name__ == "__main__":
    unittest.main()
