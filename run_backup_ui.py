from backup_bot import load_env
from backup_ui_product import BackupApp, apply_style, T
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication


# Compatibility shim: the product UI uses tr(key) in most places and
# apply_lang() also needs the full translation dictionary.
def _tr(self, key=None):
    return T[self.lang] if key is None else T[self.lang][key]


BackupApp.tr = _tr


if __name__ == "__main__":
    load_env()
    app = QApplication([])
    app.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
    apply_style(app)
    window = BackupApp()
    window.show()
    app.exec()
