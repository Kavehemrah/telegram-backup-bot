from backup_bot import load_env
from backup_ui_product import BackupApp, apply_style
from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QApplication

import backup_ui_product
from backup_bot import load_project, save_project, run_job_backup as _run_job_backup
from restore_ui import RestoreDialog


def _run_job_backup_with_topic_recovery(token, job, log, progress=None, cancel_event=None, selected_files=None):
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


def open_restore(window: BackupApp) -> None:
    dialog = RestoreDialog(window.token, window.lang, window)
    dialog.exec()


backup_ui_product.run_job_backup = _run_job_backup_with_topic_recovery


if __name__ == "__main__":
    load_env()
    app = QApplication([])
    app.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
    apply_style(app)
    window = BackupApp()

    restore_action = QAction("↺ بازیابی" if window.lang == "fa" else "↺ Restore", window)
    restore_action.triggered.connect(lambda: open_restore(window))
    window.menuBar().addAction(restore_action)

    window.show()
    app.exec()
