from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

import backup_ui_product
from backup_bot import load_env, load_project, save_project, run_job_backup as _run_job_backup
from backup_ui_product import BackupApp, apply_style
from restore_tab import RestoreTab


def _run_job_backup_with_topic_recovery(
    token,
    job,
    log,
    progress=None,
    cancel_event=None,
    selected_files=None,
):
    try:
        return _run_job_backup(token, job, log, progress, cancel_event, selected_files)
    except RuntimeError as exc:
        message = str(exc)
        if "sendMessage" not in message or "message thread not found" not in message.lower():
            raise

        project = load_project()
        old_topic = project.get("history_topic_id")
        if not old_topic or project.get("history_chat_id") != str(job.get("chat_id")):
            raise

        print("\n[Topic Recovery] Stored History Topic is no longer valid.", flush=True)
        print(f"[Topic Recovery] Invalidating history_topic_id={old_topic}", flush=True)
        project["history_topic_id"] = None
        save_project(project)
        log("History Topic قدیمی بود؛ ایجاد Topic جدید...")
        return _run_job_backup(token, job, log, progress, cancel_event, selected_files)


def _install_restore_tab() -> None:
    original_build = BackupApp.build
    original_apply_lang = BackupApp.apply_lang

    def build_with_restore(self: BackupApp) -> None:
        original_build(self)
        self.restore_tab = RestoreTab(
            lambda: self.token.strip() or os.getenv("TELEGRAM_BOT_TOKEN", ""),
            self.lang,
            self,
        )
        title = "بازیابی" if self.lang == "fa" else "Restore"
        self.tabs.insertTab(2, self.restore_tab, title)
        self.tabs.setTabToolTip(2, title)

    def apply_lang_with_restore(self: BackupApp) -> None:
        original_apply_lang(self)
        if hasattr(self, "restore_tab"):
            title = "بازیابی" if self.lang == "fa" else "Restore"
            self.tabs.setTabText(2, title)
            self.tabs.setTabToolTip(2, title)
            self.restore_tab.set_language(self.lang)
            self.restore_tab.refresh()

    BackupApp.build = build_with_restore
    BackupApp.apply_lang = apply_lang_with_restore


backup_ui_product.run_job_backup = _run_job_backup_with_topic_recovery
_install_restore_tab()


if __name__ == "__main__":
    load_env()
    app = QApplication([])
    app.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
    apply_style(app)
    window = BackupApp()
    window.show()
    app.exec()
