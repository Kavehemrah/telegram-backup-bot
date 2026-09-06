import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

import backup_jobs
from telegram_forum import TelegramForum


class TelegramForumTests(unittest.TestCase):
    def test_create_topic_returns_thread_id(self):
        request = Mock(return_value={"message_thread_id": 42})
        forum = TelegramForum(request)
        self.assertEqual(forum.create_topic("token", "-100", "Finance"), 42)
        request.assert_called_once_with("token", "createForumTopic", data={"chat_id": "-100", "name": "Finance"})

    def test_copy_message_does_not_download_file(self):
        request = Mock(return_value={"message_id": 99})
        forum = TelegramForum(request)
        self.assertEqual(forum.copy_message("token", "-100", 12, 77), 99)
        request.assert_called_once_with("token", "copyMessage", data={
            "chat_id": "-100", "from_chat_id": "-100", "message_id": 12, "message_thread_id": 77,
        })


class BackupJobTests(unittest.TestCase):
    def test_pending_files_detects_new_and_modified_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "first.txt"
            second = root / "second.txt"
            first.write_text("one", encoding="utf-8")
            second.write_text("two", encoding="utf-8")
            manifest = {backup_jobs.file_key(first): {"modified": first.stat().st_mtime_ns, "message_id": 10}}
            self.assertEqual(backup_jobs.pending_files(directory, manifest), [second])
            first.write_text("changed", encoding="utf-8")
            self.assertEqual(backup_jobs.pending_files(directory, manifest), [first, second])

    def test_new_job_has_topic_and_history_defaults(self):
        job = backup_jobs.new_job(r"C:\Backup", "-100")
        self.assertEqual(job["destination"], "topic")
        self.assertIsNone(job["main_topic_id"])
        self.assertIsNone(job["history_topic_id"])
        self.assertTrue(job["history_topic_name"].endswith(" History"))


if __name__ == "__main__":
    unittest.main()
