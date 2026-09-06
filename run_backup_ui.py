from backup_bot import load_env
from backup_ui_product import BackupApp, apply_style, T
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QLabel


# Compatibility shim: the product UI uses tr(key) in most places and
# apply_lang() also needs the full translation dictionary.
def _tr(self, key=None):
    return T[self.lang] if key is None else T[self.lang][key]


BackupApp.tr = _tr


# The form labels for fields implemented with QHBoxLayout are not returned
# by QFormLayout.labelForField(). Rebuild the five label references from the
# actual QLabel children after the UI is built, before apply_lang() runs.
_original_build = BackupApp.build


def _build_with_form_labels(self):
    _original_build(self)
    right = self.name.parentWidget()
    labels = [
        w for w in right.findChildren(QLabel)
        if w.parentWidget() is right and not w.objectName() and not w.text()
    ]
    labels.sort(key=lambda w: w.geometry().top())
    if len(labels) >= 5:
        self.labs = labels[:5]


BackupApp.build = _build_with_form_labels


if __name__ == "__main__":
    load_env()
    app = QApplication([])
    app.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
    apply_style(app)
    window = BackupApp()
    window.show()
    app.exec()
