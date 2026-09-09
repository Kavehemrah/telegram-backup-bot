from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from backup_jobs import load_folders
from restore import restore_file
from restore_catalog import list_restore_candidates


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


class RestoreTab(QWidget):
    def __init__(self, token_provider, lang: str = "fa", parent=None):
        super().__init__(parent)
        self.token_provider = token_provider
        self.lang = lang if lang in ("fa", "en") else "fa"
        self.entries: list[dict] = []
        self.worker: RestoreWorker | None = None
        self._build()
        self.set_language(self.lang)
        self.refresh()

    def _build(self) -> None:
        root = QVBoxLayout(self)

        header = QFrame()
        header.setObjectName("softCard")
        header_layout = QVBoxLayout(header)
        self.title = QLabel()
        self.title.setObjectName("sectionTitle")
        self.subtitle = QLabel()
        self.subtitle.setObjectName("muted")
        self.subtitle.setWordWrap(True)
        header_layout.addWidget(self.title)
        header_layout.addWidget(self.subtitle)
        root.addWidget(header)

        toolbar = QHBoxLayout()
        self.search = QLineEdit()
        self.refresh_button = QPushButton()
        self.refresh_button.setObjectName("secondary")
        self.refresh_button.clicked.connect(self.refresh)
        toolbar.addWidget(self.search, 1)
        toolbar.addWidget(self.refresh_button)
        self.search.textChanged.connect(self.refresh_view)
        root.addLayout(toolbar)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        left = QFrame()
        left.setObjectName("card")
        left_layout = QVBoxLayout(left)
        self.tree_title = QLabel()
        self.tree_title.setObjectName("subTitle")
        self.topic_list = QListWidget()
        self.topic_list.currentItemChanged.connect(self.topic_changed)
        left_layout.addWidget(self.tree_title)
        left_layout.addWidget(self.topic_list, 1)
        splitter.addWidget(left)

        right = QFrame()
        right.setObjectName("card")
        right_layout = QVBoxLayout(right)
        self.files_title = QLabel()
        self.files_title.setObjectName("subTitle")
        self.file_list = QListWidget()
        self.file_list.currentItemChanged.connect(self.file_changed)
        right_layout.addWidget(self.files_title)
        right_layout.addWidget(self.file_list, 1)

        destination = QHBoxLayout()
        self.destination = QLineEdit()
        self.choose_button = QPushButton()
        self.choose_button.clicked.connect(self.choose_destination)
        destination.addWidget(self.destination, 1)
        destination.addWidget(self.choose_button)
        right_layout.addLayout(destination)

        options = QHBoxLayout()
        self.overwrite = QCheckBox()
        self.info = QLabel()
        self.info.setObjectName("muted")
        options.addWidget(self.overwrite)
        options.addWidget(self.info, 1)
        right_layout.addLayout(options)

        actions = QHBoxLayout()
        self.restore_button = QPushButton()
        self.restore_button.setObjectName("primary")
        self.restore_button.clicked.connect(self.restore_selected)
        self.open_folder_button = QPushButton()
        self.open_folder_button.setObjectName("secondary")
        self.open_folder_button.clicked.connect(self.choose_destination)
        actions.addWidget(self.restore_button)
        actions.addWidget(self.open_folder_button)
        actions.addStretch()
        right_layout.addLayout(actions)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.hide()
        right_layout.addWidget(self.progress)
        splitter.addWidget(right)
        splitter.setSizes([360, 820])
        root.addWidget(splitter, 1)

    def set_language(self, lang: str) -> None:
        self.lang = lang if lang in ("fa", "en") else "fa"
        fa = self.lang == "fa"
        self.title.setText("مرکز بازیابی" if fa else "Restore Center")
        self.subtitle.setText(
            "ساختار: Chat → Topic → File → Version. برای Restore، فایل را انتخاب کنید و مقصد محلی را تعیین کنید."
            if fa
            else "Browse known Telegram chats/topics, choose a backed-up file version, and restore it locally."
        )
        self.search.setPlaceholderText(
            "جست‌وجو در Chat، Topic یا فایل..." if fa else "Search chat, topic, or file..."
        )
        self.refresh_button.setText("↻ به‌روزرسانی" if fa else "↻ Refresh")
        self.tree_title.setText("پوشه‌های Telegram" if fa else "Telegram folders")
        self.files_title.setText("فایل‌ها / نسخه‌ها" if fa else "Files / versions")
        self.choose_button.setText("انتخاب مقصد" if fa else "Choose destination")
        self.open_folder_button.setText("انتخاب پوشه" if fa else "Choose folder")
        self.overwrite.setText("جایگزینی فایل موجود" if fa else "Overwrite existing file")
        self.restore_button.setText("↺ بازیابی نسخه انتخاب‌شده" if fa else "↺ Restore selected version")
        self.refresh_view()

    def _topic_key(self, entry: dict) -> tuple[str, str]:
        return str(entry.get("chat_id", "")), str(entry.get("thread_id", "general"))

    def _topic_name(self, entry: dict) -> str:
        return str(entry.get("topic_name") or f"Topic {entry.get('thread_id', 'General')}")

    def refresh(self) -> None:
        self.entries = list_restore_candidates()
        self.refresh_view()

    def refresh_view(self) -> None:
        query = self.search.text().casefold().strip()
        current_topic = self.topic_list.currentItem()
        current_key = current_topic.data(Qt.ItemDataRole.UserRole) if current_topic else None

        topic_map: dict[tuple[str, str], dict] = {}
        folders = load_folders()
        for folder in folders:
            if folder.get("topic_id") is None:
                continue
            key = (str(folder.get("chat_id", "")), str(folder.get("topic_id")))
            topic_map.setdefault(
                key,
                {
                    "chat_id": key[0],
                    "thread_id": key[1],
                    "topic_name": folder.get("topic_name") or Path(folder.get("path", "")).name,
                    "entries": [],
                },
            )

        for entry in self.entries:
            key = self._topic_key(entry)
            topic_map.setdefault(
                key,
                {
                    "chat_id": key[0],
                    "thread_id": key[1],
                    "topic_name": self._topic_name(entry),
                    "entries": [],
                },
            )
            topic_map[key]["entries"].append(entry)
            if entry.get("topic_name"):
                topic_map[key]["topic_name"] = entry["topic_name"]

        self.topic_list.blockSignals(True)
        self.topic_list.clear()
        for key, topic in sorted(topic_map.items(), key=lambda item: (item[1]["chat_id"], item[1]["topic_name"].casefold())):
            haystack = " ".join(
                [topic["chat_id"], topic["topic_name"]]
                + [str(e.get("relative_path", e.get("path", ""))) for e in topic["entries"]]
            ).casefold()
            if query and query not in haystack:
                continue
            label = f"{topic['topic_name']}  •  {topic['chat_id']}"
            if topic["entries"]:
                label += f"  •  {len(topic['entries'])} file/version"
            else:
                label += "  •  no indexed files"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, key)
            self.topic_list.addItem(item)
        self.topic_list.blockSignals(False)

        if self.topic_list.count():
            target = 0
            if current_key:
                for i in range(self.topic_list.count()):
                    if self.topic_list.item(i).data(Qt.ItemDataRole.UserRole) == current_key:
                        target = i
                        break
            self.topic_list.setCurrentRow(target)
        else:
            self.file_list.clear()
            self.info.setText(
                "فایل قابل بازیابی پیدا نشد." if self.lang == "fa" else "No indexed restore files found."
            )

    def topic_changed(self, current, _previous) -> None:
        self.file_list.clear()
        if not current:
            return
        key = current.data(Qt.ItemDataRole.UserRole)
        entries = [entry for entry in self.entries if self._topic_key(entry) == tuple(key)]
        query = self.search.text().casefold().strip()
        for entry in entries:
            relative = entry.get("relative_path", entry.get("path", ""))
            label = (
                f"{relative}  •  v{entry.get('version', '?')}  •  "
                f"{entry.get('uploaded_at', '')}  •  {int(entry.get('size', 0)) / 1024:.1f} KB"
            )
            if query and query not in label.casefold() and query not in str(entry.get("topic_name", "")).casefold():
                continue
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, entry)
            self.file_list.addItem(item)
        if self.file_list.count():
            self.file_list.setCurrentRow(0)
        else:
            self.info.setText(
                "این Topic هنوز فایل قابل بازیابی ثبت‌شده ندارد."
                if self.lang == "fa"
                else "This topic has no indexed downloadable files yet."
            )

    def file_changed(self, current, _previous) -> None:
        if not current:
            return
        entry = current.data(Qt.ItemDataRole.UserRole)
        self.info.setText(
            (f"Message: {entry.get('message_id')} • File ID: {entry.get('file_id')}" if self.lang == "en"
             else f"Message: {entry.get('message_id')} • File ID: {entry.get('file_id')}")
        )

    def choose_destination(self) -> None:
        selected = QFileDialog.getExistingDirectory(
            self,
            "Choose restore destination" if self.lang == "en" else "انتخاب پوشه مقصد بازیابی",
        )
        if selected:
            self.destination.setText(str(Path(selected).resolve()))

    def restore_selected(self) -> None:
        item = self.file_list.currentItem()
        if not item:
            QMessageBox.warning(
                self,
                "Restore",
                "یک فایل/نسخه را انتخاب کنید." if self.lang == "fa" else "Select a file/version first.",
            )
            return
        token = str(self.token_provider() or "").strip()
        if not token:
            QMessageBox.critical(
                self,
                "Restore",
                "TELEGRAM_BOT_TOKEN تنظیم نشده است." if self.lang == "fa" else "TELEGRAM_BOT_TOKEN is missing.",
            )
            return
        destination = self.destination.text().strip()
        if not destination:
            self.choose_destination()
            destination = self.destination.text().strip()
        if not destination:
            return

        entry = item.data(Qt.ItemDataRole.UserRole)
        self.worker = RestoreWorker(
            token,
            entry,
            destination,
            self.overwrite.isChecked(),
        )
        self.restore_button.setEnabled(False)
        self.refresh_button.setEnabled(False)
        self.progress.show()
        self.worker.done.connect(self.restore_done)
        self.worker.failed.connect(self.restore_failed)
        self.worker.start()

    def restore_done(self, output: str) -> None:
        self.restore_button.setEnabled(True)
        self.refresh_button.setEnabled(True)
        self.progress.hide()
        QMessageBox.information(
            self,
            "Restore",
            f"فایل با موفقیت بازیابی شد:\n{output}" if self.lang == "fa" else f"Restored successfully:\n{output}",
        )

    def restore_failed(self, message: str) -> None:
        self.restore_button.setEnabled(True)
        self.refresh_button.setEnabled(True)
        self.progress.hide()
        QMessageBox.critical(self, "Restore", message)
