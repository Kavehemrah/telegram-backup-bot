import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import backup_jobs


class BackupJobsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.jobs_file = self.root / "jobs.json"
        self.manifest_file = self.root / "manifest.json"
        self.folders_file = self.root / "folders.json"
        self.project_file = self.root / "project.json"
        self.folder = self.root / "documents"
        self.folder.mkdir()
        self.patchers = [
            patch.object(backup_jobs, "JOBS_FILE", self.jobs_file),
            patch.object(backup_jobs, "MANIFEST_FILE", self.manifest_file),
            patch.object(backup_jobs, "FOLDERS_FILE", self.folders_file),
            patch.object(backup_jobs, "PROJECT_FILE", self.project_file),
        ]
        for item in self.patchers:
            item.start()

    def tearDown(self):
        for item in reversed(self.patchers):
            item.stop()
        self.temp.cleanup()

    def test_folder_identity_survives_job_deletion_and_reuse(self):
        first = backup_jobs.new_job(str(self.folder), "-100", "23:00")
        folder = backup_jobs.get_or_create_folder(str(self.folder))
        folder["topic_id"] = 12345
        backup_jobs.update_folder(folder)

        jobs = [first]
        backup_jobs.save_jobs(jobs)
        jobs.clear()
        backup_jobs.save_jobs(jobs)

        second = backup_jobs.new_job(str(self.folder), "-100", "23:00")
        self.assertEqual(first["folder_id"], second["folder_id"])
        self.assertEqual(second["main_topic_id"], 12345)

    def test_new_job_has_independent_pause_and_selection_settings(self):
        job = backup_jobs.new_job(str(self.folder), "-100")
        self.assertTrue(job["enabled"])
        self.assertEqual(job["backup_mode"], "ALL")
        self.assertEqual(job["selected_files"], [])
        self.assertTrue(job["replace_files"])
        self.assertTrue(job["history_enabled"])

        job["enabled"] = False
        job["backup_mode"] = "SELECTED"
        job["selected_files"] = [str(self.folder / "a.pdf")]
        backup_jobs.save_jobs([job])
        loaded = backup_jobs.load_and_migrate_jobs()[0]
        self.assertFalse(loaded["enabled"])
        self.assertEqual(loaded["backup_mode"], "SELECTED")
        self.assertEqual(loaded["selected_files"], [str(self.folder / "a.pdf")])

    def test_pending_files_uses_modified_time_and_size(self):
        path = self.folder / "a.txt"
        path.write_text("hello", encoding="utf-8")
        self.assertEqual(backup_jobs.pending_files(str(self.folder), {}), [path.resolve()])
        stat = path.stat()
        manifest = {backup_jobs.file_key(path): {"modified": stat.st_mtime_ns, "size": stat.st_size}}
        self.assertEqual(backup_jobs.pending_files(str(self.folder), manifest), [])


if __name__ == "__main__":
    unittest.main()
