from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
)

from restore import list_restore_candidates, restore_file


class RestoreWorker(QThread):
    done = Signal(str)
    failed = Signal(str)

    def __init__(self, token: str, entry: dict, destination: str, overwrite: bool):
        super().__init__()
        self.token = token
        self.entry = entry
        self.destination = destination
        self.overwrite = overwrite

    def run(self) -> None:
        try:
            output = restore_file(
                self.token,
                self.entry,
                self.destination,
                overwrite=self.overwrite,
            )
            self.done.emit(str(output))
        except Exception as exc:
            self.failed.emit(str(exc))


class RestoreDialog(QDialog):
    def __init__(self, token: str, lang: str = "fa", parent=None):
        super().__init__(parent)
        self.token = token
        self.lang = lang if lang in ("fa", "en") else "fa"
        self.entries = list_restore_candidates()
        self.worker: RestoreWorker | None = None
        self.setWindowTitle(
            "Telegram Restore Center" if self.lang == "en" else "مرکز بازیابی Telegram"
        )
        self.resize(900, 650)
        self._build()
        self._refresh()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        title = QLabel(
            "Select a backed-up file, choose a destination, then restore it."
            if self.lang == "en"
            else "یک فایل پشتیبان را انتخاب کنید، مقصد را تعیین کنید و آن را بازیابی کنید."
        )
        title.setWordWrap(True)
        title.setObjectName("dialogTitle")
        root.addWidget(title)

        self.search = QLineEdit()
        self.search.setPlaceholderText(
            "Search file name or path..." if self.lang == "en" else "جست‌وجو در نام یا مسیر فایل..."
        )
        self.search.textChanged.connect(self._refresh)
        root.addWidget(self.search)

        self.list = QListWidget()
        self.list.setAlternatingRowColors(True)
        self.list.currentRowChanged.connect(self._selection_changed)
        root.addWidget(self.list, 1)

        destination_row = QHBoxLayout()
        self.destination = QLineEdit()
        self.destination.setPlaceholderText(
            "Destination folder" if self.lang == "en" else "پوشه مقصد"
        )
        self.choose = QPushButton("Choose..." if self.lang == "en" else "انتخاب...")
        self.choose.clicked.connect(self._choose_destination)
        destination_row.addWidget(self.destination, 1)
        destination_row.addWidget(self.choose)
        root.addLayout(destination_row)

        self.overwrite = QCheckBox(
            "Overwrite existing file" if self.lang == "en" else "جایگزینی فایل موجود"
        )
        root.addWidget(self.overwrite)

        self.info = QLabel()
        self.info.setObjectName("muted")
        root.addWidget(self.info)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.hide()
        root.addWidget(self.progress)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.restore_button = QPushButton(
            "Restore selected" if self.lang == "en" else "بازیابی فایل انتخاب‌شده"
        )
        self.restore_button.clicked.connect(self.restore_selected)
        buttons.addButton(self.restore_button, QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _refresh(self) -> None:
        query = self.search.text().casefold().strip()
        self.list.clear()
        for entry in self.entries:
            label = (
                f"{entry.get('relative_path', entry.get('path', ''))}"
                f"  •  v{entry.get('version', '?')}  •  {entry.get('uploaded_at', '')}"
                f"  •  {int(entry.get('size', 0)) / 1024:.1f} KB"
            )
            if query and query not in label.casefold():
                continue
            item = QListWidgetItem(label)
            item.setData(256, entry)
            self.list.addItem(item)
        if self.list.count():
            self.list.setCurrentRow(0)
        self._selection_changed(self.list.currentRow())

    def _selection_changed(self, row: int) -> None:
        if row < 0:
            self.info.setText(
                "No indexed backups. Make a backup with this v1.2 build first."
                if self.lang == "en"
                else "هنوز Backup قابل بازیابی ثبت نشده است. ابتدا با نسخه v1.2 یک Backup انجام دهید."
            )
            return
        item = self.list.item(row)
        entry = item.data(256)
        self.info.setText(
            f"Telegram message: {entry.get('message_id')} | source: {entry.get('path', '')}"
        )

    def _choose_destination(self) -> None:
        selected = QFileDialog.getExistingDirectory(
            self,
            "Choose restore destination" if self.lang == "en" else "انتخاب پوشه مقصد بازیابی",
        )
        if selected:
            self.destination.setText(str(Path(selected).resolve()))

    def restore_selected(self) -> None:
        item = self.list.currentItem()
        if not item:
            QMessageBox.warning(
                self,
                "Restore",
                "Select a backup first." if self.lang == "en" else "ابتدا یک Backup را انتخاب کنید.",
            )
            return
        if not self.token.strip():
            QMessageBox.critical(
                self,
                "Restore",
                "TELEGRAM_BOT_TOKEN is missing." if self.lang == "en" else "TELEGRAM_BOT_TOKEN تنظیم نشده است.",
            )
            return
        destination = self.destination.text().strip()
        if not destination:
            QMessageBox.warning(
                self,
                "Restore",
                "Choose a destination folder first."
                if self.lang == "en"
                else "ابتدا پوشه مقصد را انتخاب کنید.",
            )
            return

        entry = item.data(256)
        self.worker = RestoreWorker(
            self.token,
            entry,
            destination,
            self.overwrite.isChecked(),
        )
        self.restore_button.setEnabled(False)
        self.choose.setEnabled(False)
        self.progress.show()
        self.worker.done.connect(self._restore_done)
        self.worker.failed.connect(self._restore_failed)
        self.worker.start()

    def _restore_done(self, output: str) -> None:
        self.restore_button.setEnabled(True)
        self.choose.setEnabled(True)
        self.progress.hide()
        QMessageBox.information(
            self,
            "Restore",
            f"Restored successfully:\n{output}"
            if self.lang == "en"
            else f"فایل با موفقیت بازیابی شد:\n{output}",
        )

    def _restore_failed(self, message: str) -> None:
        self.restore_button.setEnabled(True)
        self.choose.setEnabled(True)
        self.progress.hide()
        QMessageBox.critical(self, "Restore", message)
