from backup_bot import load_env
from backup_ui_v2 import BackupApp, apply_style
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication


if __name__ == "__main__":
    load_env()
    app = QApplication([])
    app.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
    apply_style(app)
    window = BackupApp()
    window.show()
    app.exec()
