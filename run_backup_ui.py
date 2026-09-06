from PySide6.QtWidgets import QApplication

from backup_bot import load_env
from backup_ui import BackupApp, apply_style


if __name__ == "__main__":
    load_env()
    app = QApplication([])
    apply_style(app)
    window = BackupApp()
    window.show()
    raise SystemExit(app.exec())
