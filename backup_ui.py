from __future__ import annotations

import os
import threading
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSplitter,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from backup_bot import (
    STATE_FILES,
    _ensure_folder_topic,
    _ensure_project_history_topic,
    run_job_backup,
    telegram_request,
)
from backup_jobs import (
    current_files,
    get_or_create_folder,
    load_and_migrate_jobs,
    load_manifest,
    new_job,
    save_jobs,
)
from telegram_forum import TelegramForum


class _Worker(QThread):
    progress = Signal(int)
    log = Signal(str)
    success = Signal(object)
    failure = Signal(str)

    def __init__(self, token, job, selected_files=None):
        super().__init__()
        self.token = token
        self.job = job
        self.selected_files = selected_files
        self.cancel_event = threading.Event()

    def run(self):
        try:
            count, completed = run_job_backup(
                self.token,
                self.job,
                self.log.emit,
                lambda current, total: self.progress.emit(int(current / total * 100) if total else 100),
                self.cancel_event,
                self.selected_files,
            )
            self.success.emit((count, completed, self.job))
        except Exception as exc:
            self.failure.emit(str(exc))

    def cancel(self):
        self.cancel_event.set()


class _ChatLoader(QThread):
    loaded = Signal(object)
    failed = Signal(str)

    def __init__(self, token):
        super().__init__()
        self.token = token

    def run(self):
        try:
            updates = telegram_request(self.token, "getUpdates")
            chats = {}
            for update in updates:
                message = update.get("message") or update.get("channel_post") or {}
                chat = message.get("chat") or {}
                if chat.get("id") is None:
                    continue
                title = chat.get("title") or chat.get("first_name") or "بدون نام"
                key = str(chat["id"])
                chats[key] = f"{title}   ({key})"
            self.loaded.emit(chats)
        except Exception as exc:
            self.failed.emit(str(exc))


class ChatPicker(QDialog):
    def __init__(self, chats: dict[str, str], current_id: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("انتخاب چت Telegram")
        self.resize(620, 520)
        self.selected_id = current_id
        layout = QVBoxLayout(self)
        title = QLabel("چت مقصد را انتخاب کنید")
        title.setObjectName("dialogTitle")
        layout.addWidget(title)
        self.search = QLineEdit()
        self.search.setPlaceholderText("جست‌وجوی نام یا Chat ID ...")
        layout.addWidget(self.search)
        self.list = QListWidget()
        self.list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        layout.addWidget(self.list, 1)
        for chat_id, label in chats.items():
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, chat_id)
            self.list.addItem(item)
            if str(chat_id) == str(current_id):
                self.list.setCurrentItem(item)
        self.search.textChanged.connect(self._filter)
        self.list.itemDoubleClicked.connect(self._accept_item)
        self.count = QLabel(f"{len(chats)} چت")
        layout.addWidget(self.count)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok)
        buttons.accepted.connect(self._accept_item)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _filter(self, text: str):
        query = text.strip().casefold()
        visible = 0
        for i in range(self.list.count()):
            item = self.list.item(i)
            show = not query or query in item.text().casefold()
            item.setHidden(not show)
            visible += int(show)
        self.count.setText(f"{visible} چت نمایش داده شد")

    def _accept_item(self, *_):
        item = self.list.currentItem()
        if not item or item.isHidden():
            QMessageBox.warning(self, "انتخاب چت", "یک چت را انتخاب کنید.")
            return
        self.selected_id = str(item.data(Qt.ItemDataRole.UserRole))
        self.accept()


class FilePicker(QDialog):
    def __init__(self, files: list[Path], selected: set[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("انتخاب فایل‌های Backup")
        self.resize(850, 650)
        self.files = files
        self.selected = set(selected)
        layout = QVBoxLayout(self)
        title = QLabel("فقط فایل‌هایی که تیک دارند در حالت «فایل‌های انتخابی» Backup می‌شوند.")
        title.setObjectName("dialogTitle")
        layout.addWidget(title)
        self.search = QLineEdit()
        self.search.setPlaceholderText("جست‌وجو در نام یا مسیر فایل ...")
        layout.addWidget(self.search)
        self.list = QListWidget()
        self.list.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        layout.addWidget(self.list, 1)
        for path in files:
            full = str(path.resolve())
            item = QListWidgetItem(str(path.relative_to(Path(files[0]).anchor)) if False else str(path))
            item.setData(Qt.ItemDataRole.UserRole, full)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if full in self.selected else Qt.CheckState.Unchecked)
            self.list.addItem(item)
        self.search.textChanged.connect(self._filter)
        self.list.itemChanged.connect(self._count)
        self.count = QLabel()
        layout.addWidget(self.count)
        tools = QHBoxLayout()
        all_btn = QPushButton("انتخاب همه")
        none_btn = QPushButton("پاک کردن همه")
        all_btn.clicked.connect(lambda: self._set_all(True))
        none_btn.clicked.connect(lambda: self._set_all(False))
        tools.addWidget(all_btn)
        tools.addWidget(none_btn)
        tools.addStretch()
        layout.addLayout(tools)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._count()

    def _filter(self, text: str):
        query = text.strip().casefold()
        for i in range(self.list.count()):
            item = self.list.item(i)
            item.setHidden(bool(query) and query not in item.text().casefold())

    def _set_all(self, value: bool):
        self.list.blockSignals(True)
        state = Qt.CheckState.Checked if value else Qt.CheckState.Unchecked
        for i in range(self.list.count()):
            self.list.item(i).setCheckState(state)
        self.list.blockSignals(False)
        self._count()

    def _count(self, *_):
        total = 0
        for i in range(self.list.count()):
            total += int(self.list.item(i).checkState() == Qt.CheckState.Checked)
        self.count.setText(f"{total} فایل انتخاب شده از {self.list.count()}")

    def _accept(self):
        self.selected = {
            str(self.list.item(i).data(Qt.ItemDataRole.UserRole))
            for i in range(self.list.count())
            if self.list.item(i).checkState() == Qt.CheckState.Checked
        }
        self.accept()


class BackupApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Telegram Folder Backup")
        self.resize(1280, 820)
        self.setMinimumSize(1080, 700)
        self.jobs = load_and_migrate_jobs()
        self.chats: dict[str, str] = {}
        self.worker: _Worker | None = None
        self.chat_loader: _ChatLoader | None = None
        self.scheduler_thread: threading.Thread | None = None
        self.scheduler_stop = threading.Event()
        self.token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        self._new_job = False
        self._build_ui()
        self._load_jobs()

    def _build_ui(self):
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(12)
        self.setCentralWidget(central)

        header = QFrame()
        header.setObjectName("header")
        h = QHBoxLayout(header)
        h.setContentsMargins(18, 14, 18, 14)
        title_box = QVBoxLayout()
        title = QLabel("پشتیبان‌گیری تلگرام")
        title.setObjectName("headerTitle")
        subtitle = QLabel("مدیریت Job مستقل • Topic پایدار • History مرکزی")
        subtitle.setObjectName("headerSubtitle")
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        h.addLayout(title_box)
        h.addStretch()
        root.addWidget(header)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        root.addWidget(splitter, 1)
        splitter.setSizes([330, 900])

        splitter.addWidget(self._build_jobs_panel())
        splitter.addWidget(self._build_editor_panel())

        status = QFrame()
        sh = QHBoxLayout(status)
        sh.setContentsMargins(10, 7, 10, 7)
        self.status_label = QLabel("آماده")
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setTextVisible(False)
        self.progress.setMinimumWidth(260)
        self.progress.setMaximumWidth(420)
        sh.addWidget(self.status_label, 1)
        sh.addWidget(self.progress)
        root.addWidget(status)

    def _build_jobs_panel(self):
        frame = QFrame()
        frame.setObjectName("card")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(14, 14, 14, 14)
        title = QLabel("Backup Jobs")
        title.setObjectName("sectionTitle")
        layout.addWidget(title)
        info = QLabel("هر Job مستقل است. حذف Job، Folder Identity و Topic را حذف نمی‌کند.")
        info.setWordWrap(True)
        info.setObjectName("muted")
        layout.addWidget(info)

        self.jobs_list = QListWidget()
        self.jobs_list.setAlternatingRowColors(True)
        self.jobs_list.currentRowChanged.connect(self._select_job)
        layout.addWidget(self.jobs_list, 1)

        row = QHBoxLayout()
        new_btn = QPushButton("＋ Job جدید")
        pause_btn = QPushButton("Pause / Resume")
        delete_btn = QPushButton("حذف Job")
        delete_btn.setObjectName("danger")
        new_btn.clicked.connect(self.new_job)
        pause_btn.clicked.connect(self.toggle_job)
        delete_btn.clicked.connect(self.delete_job)
        row.addWidget(new_btn)
        row.addWidget(pause_btn)
        row.addWidget(delete_btn)
        layout.addLayout(row)
        return frame

    def _build_editor_panel(self):
        panel = QFrame()
        panel.setObjectName("card")
        root = QVBoxLayout(panel)
        root.setContentsMargins(18, 18, 18, 18)
        title = QLabel("تنظیمات Job")
        title.setObjectName("sectionTitle")
        root.addWidget(title)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(10)
        self.name_edit = QLineEdit()
        self.schedule_edit = QLineEdit("23:00")
        self.folder_edit = QLineEdit()
        self.folder_edit.setReadOnly(True)
        choose_folder = QPushButton("انتخاب فولدر")
        choose_folder.clicked.connect(self.choose_folder)
        folder_row = QHBoxLayout()
        folder_row.addWidget(self.folder_edit, 1)
        folder_row.addWidget(choose_folder)
        self.topic_edit = QLineEdit()
        self.chat_edit = QLineEdit()
        self.chat_edit.setPlaceholderText("مثلاً -1001234567890")
        self.load_chats_btn = QPushButton("بارگذاری چت‌ها")
        self.load_chats_btn.clicked.connect(self.load_chats)
        chat_row = QHBoxLayout()
        chat_row.addWidget(self.chat_edit, 1)
        chat_row.addWidget(self.load_chats_btn)
        form.addRow("نام Job", self.name_edit)
        form.addRow("زمان روزانه", self.schedule_edit)
        form.addRow("فولدر", folder_row)
        form.addRow("نام Topic", self.topic_edit)
        form.addRow("Chat ID", chat_row)
        root.addLayout(form)

        behavior = QFrame()
        behavior.setObjectName("softCard")
        bv = QVBoxLayout(behavior)
        bv.setContentsMargins(12, 10, 12, 10)
        lbl = QLabel("رفتار Backup")
        lbl.setObjectName("subTitle")
        bv.addWidget(lbl)
        mode = QHBoxLayout()
        self.all_radio = QRadioButton("کل فولدر")
        self.selected_radio = QRadioButton("فایل‌های انتخابی")
        self.all_radio.setChecked(True)
        self.all_radio.toggled.connect(self._update_file_area)
        mode.addWidget(self.all_radio)
        mode.addWidget(self.selected_radio)
        mode.addStretch()
        bv.addLayout(mode)
        checks = QHBoxLayout()
        self.replace_check = QCheckBox("جایگذاری نسخه قبلی")
        self.history_check = QCheckBox("History مرکزی")
        self.enabled_check = QCheckBox("فعال برای Scheduler")
        self.replace_check.setChecked(True)
        self.history_check.setChecked(True)
        self.enabled_check.setChecked(True)
        checks.addWidget(self.replace_check)
        checks.addWidget(self.history_check)
        checks.addWidget(self.enabled_check)
        checks.addStretch()
        bv.addLayout(checks)
        root.addWidget(behavior)

        files_box = QFrame()
        files_box.setObjectName("softCard")
        fv = QVBoxLayout(files_box)
        fv.setContentsMargins(12, 10, 12, 10)
        head = QHBoxLayout()
        self.file_summary = QLabel("ابتدا فولدر را انتخاب کنید")
        self.file_summary.setObjectName("muted")
        head.addWidget(self.file_summary, 1)
        manage = QPushButton("انتخاب / مدیریت فایل‌ها")
        manage.clicked.connect(self.open_file_selector)
        all_files = QPushButton("انتخاب همه")
        all_files.clicked.connect(lambda: self.set_all_selected(True))
        clear_files = QPushButton("پاک کردن انتخاب‌ها")
        clear_files.clicked.connect(lambda: self.set_all_selected(False))
        head.addWidget(manage)
        head.addWidget(all_files)
        head.addWidget(clear_files)
        fv.addLayout(head)

        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setMinimumHeight(160)
        fv.addWidget(self.preview)
        root.addWidget(files_box, 1)

        actions = QHBoxLayout()
        save = QPushButton("ذخیره تغییرات")
        topic = QPushButton("ساخت / اتصال Topic")
        run = QPushButton("▶ اجرای همین Job")
        run.setObjectName("primary")
        stop = QPushButton("توقف")
        scheduler = QPushButton("شروع Scheduler همه Jobهای فعال")
        save.clicked.connect(self.save_current_job)
        topic.clicked.connect(self.prepare_topics_ui)
        run.clicked.connect(self.start_backup)
        stop.clicked.connect(self.stop_all)
        scheduler.clicked.connect(self.start_scheduler)
        actions.addWidget(save)
        actions.addWidget(topic)
        actions.addWidget(run)
        actions.addWidget(stop)
        actions.addStretch()
        actions.addWidget(scheduler)
        root.addLayout(actions)
        return panel

    def _load_jobs(self):
        self.jobs_list.blockSignals(True)
        self.jobs_list.clear()
        for job in self.jobs:
            folder = Path(job.get("folder", "")).name or "بدون فولدر"
            state = "فعال" if job.get("enabled", True) else "Pause"
            item = QListWidgetItem(f"{job.get('name', folder)}\n{folder}   •   {state}   •   {job.get('schedule', '23:00')}")
            item.setToolTip(job.get("folder", ""))
            self.jobs_list.addItem(item)
        self.jobs_list.blockSignals(False)
        if self.jobs:
            self.jobs_list.setCurrentRow(0)
        else:
            self.new_job()

    def _select_job(self, row: int):
        if row < 0 or row >= len(self.jobs):
            return
        job = self.jobs[row]
        self._new_job = False
        self.name_edit.setText(job.get("name", ""))
        self.folder_edit.setText(job.get("folder", ""))
        self.topic_edit.setText(job.get("main_topic_name", job.get("name", "")))
        self.chat_edit.setText(str(job.get("chat_id", "")))
        self.schedule_edit.setText(job.get("schedule", "23:00"))
        self.enabled_check.setChecked(job.get("enabled", True))
        self.replace_check.setChecked(job.get("replace_files", True))
        self.history_check.setChecked(job.get("history_enabled", True))
        if job.get("backup_mode", "ALL") == "SELECTED":
            self.selected_radio.setChecked(True)
        else:
            self.all_radio.setChecked(True)
        self._update_file_area()
        self.status_label.setText(f"Job انتخاب شد: {job.get('name', 'Job')}")

    def _current_index(self):
        row = self.jobs_list.currentRow()
        return row if row >= 0 and row < len(self.jobs) else None

    def _current_job(self):
        row = self._current_index()
        return self.jobs[row] if row is not None else None

    def new_job(self):
        self.jobs_list.blockSignals(True)
        self.jobs_list.clearSelection()
        self.jobs_list.blockSignals(False)
        self._new_job = True
        self.name_edit.clear()
        self.folder_edit.clear()
        self.topic_edit.clear()
        self.schedule_edit.setText("23:00")
        self.chat_edit.setText(self.chat_edit.text() or os.getenv("TELEGRAM_CHAT_ID", ""))
        self.all_radio.setChecked(True)
        self.replace_check.setChecked(True)
        self.history_check.setChecked(True)
        self.enabled_check.setChecked(True)
        self.file_summary.setText("ابتدا فولدر را انتخاب کنید")
        self.preview.clear()
        self.status_label.setText("Job جدید آماده است")

    def choose_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "انتخاب فولدر پشتیبان")
        if not folder:
            return
        path = str(Path(folder).resolve())
        self.folder_edit.setText(path)
        if not self.name_edit.text().strip():
            self.name_edit.setText(Path(path).name)
        if not self.topic_edit.text().strip():
            self.topic_edit.setText(Path(path).name)
        self._update_file_area()

    def _selected_files_for_job(self):
        job = self._current_job()
        return set(str(Path(p).resolve()) for p in (job or {}).get("selected_files", []))

    def _update_file_area(self):
        folder = self.folder_edit.text().strip()
        if not folder or not Path(folder).is_dir():
            self.file_summary.setText("فولدر معتبر نیست")
            self.preview.clear()
            return
        files = current_files(folder, STATE_FILES)
        selected = self._selected_files_for_job()
        mode_selected = self.selected_radio.isChecked()
        chosen = [p for p in files if str(p.resolve()) in selected]
        if mode_selected:
            self.file_summary.setText(f"{len(chosen)} فایل از {len(files)} فایل انتخاب شده")
        else:
            self.file_summary.setText(f"کل فولدر • {len(files)} فایل موجود")
        lines = []
        for path in files[:500]:
            mark = "✓" if str(path.resolve()) in selected else "·"
            lines.append(f"{mark}  {path.relative_to(Path(folder))}")
        if len(files) > 500:
            lines.append(f"... و {len(files) - 500} فایل دیگر")
        self.preview.setPlainText("\n".join(lines))

    def open_file_selector(self):
        folder = self.folder_edit.text().strip()
        if not folder or not Path(folder).is_dir():
            QMessageBox.warning(self, "انتخاب فایل", "ابتدا یک فولدر معتبر انتخاب کنید.")
            return
        files = current_files(folder, STATE_FILES)
        if not files:
            QMessageBox.information(self, "انتخاب فایل", "در این فولدر فایلی پیدا نشد.")
            return
        dialog = FilePicker(files, self._selected_files_for_job(), self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            job = self._current_job()
            if job and not self._new_job:
                job["selected_files"] = sorted(dialog.selected)
            elif self._new_job:
                self._pending_selected = sorted(dialog.selected)
            else:
                self._pending_selected = sorted(dialog.selected)
            self._update_file_area()

    def set_all_selected(self, value: bool):
        folder = self.folder_edit.text().strip()
        if not folder or not Path(folder).is_dir():
            return
        selected = [str(p.resolve()) for p in current_files(folder, STATE_FILES)] if value else []
        job = self._current_job()
        if job and not self._new_job:
            job["selected_files"] = selected
        else:
            self._pending_selected = selected
        self._update_file_area()

    def _pending_selected_files(self):
        if hasattr(self, "_pending_selected"):
            return list(self._pending_selected)
        return []

    def _validate(self):
        folder = self.folder_edit.text().strip()
        token = self.token.strip() or os.getenv("TELEGRAM_BOT_TOKEN", "")
        chat_id = self.chat_edit.text().strip()
        if not folder or not Path(folder).is_dir():
            raise ValueError("فولدر معتبر نیست.")
        if not token:
            raise ValueError("TELEGRAM_BOT_TOKEN در .env یا محیط برنامه تنظیم نشده است.")
        if not chat_id:
            raise ValueError("Chat ID مقصد را وارد کنید یا از «بارگذاری چت‌ها» انتخاب کنید.")
        try:
            datetime.strptime(self.schedule_edit.text().strip(), "%H:%M")
        except ValueError:
            raise ValueError("زمان باید با قالب HH:MM باشد؛ مثلاً 23:00")
        return token, folder, chat_id

    def save_current_job(self):
        try:
            _, folder, chat_id = self._validate()
        except ValueError as exc:
            QMessageBox.critical(self, "تنظیمات ناقص", str(exc))
            return False
        selected = self._pending_selected_files() if self._new_job else list(self._selected_files_for_job())
        row = self._current_index()
        if self._new_job or row is None:
            job = new_job(folder, chat_id, self.schedule_edit.text().strip())
            self.jobs.append(job)
            row = len(self.jobs) - 1
        else:
            job = self.jobs[row]
        folder_record = get_or_create_folder(folder)
        job.update({
            "name": self.name_edit.text().strip() or Path(folder).name,
            "folder": folder,
            "folder_id": folder_record["id"],
            "chat_id": chat_id,
            "destination": "topic",
            "main_topic_name": self.topic_edit.text().strip() or Path(folder).name,
            "schedule": self.schedule_edit.text().strip(),
            "enabled": self.enabled_check.isChecked(),
            "backup_mode": "SELECTED" if self.selected_radio.isChecked() else "ALL",
            "selected_files": selected,
            "replace_files": self.replace_check.isChecked(),
            "history_enabled": self.history_check.isChecked(),
        })
        if folder_record.get("topic_id"):
            job["main_topic_id"] = folder_record["topic_id"]
        save_jobs(self.jobs)
        self._new_job = False
        self._pending_selected = []
        self._load_jobs()
        self.jobs_list.setCurrentRow(row)
        self.status_label.setText("تغییرات Job ذخیره شد")
        return True

    def toggle_job(self):
        row = self._current_index()
        if row is None:
            QMessageBox.information(self, "Job", "ابتدا یک Job را انتخاب کنید.")
            return
        job = self.jobs[row]
        job["enabled"] = not job.get("enabled", True)
        save_jobs(self.jobs)
        self._load_jobs()
        self.jobs_list.setCurrentRow(row)
        self.status_label.setText("Job فعال شد" if job["enabled"] else "Job روی Pause قرار گرفت")

    def delete_job(self):
        row = self._current_index()
        if row is None:
            QMessageBox.information(self, "حذف Job", "ابتدا یک Job را انتخاب کنید.")
            return
        job = self.jobs[row]
        reply = QMessageBox.question(
            self,
            "حذف Job",
            f"Job «{job.get('name', 'Job')}» حذف شود؟\n\nFolder Identity، Topic موجود و History حذف نمی‌شوند.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        del self.jobs[row]
        save_jobs(self.jobs)
        self._load_jobs()
        self.status_label.setText("Job حذف شد؛ Folder Identity و Topic حفظ شدند")

    def load_chats(self):
        token = self.token.strip() or os.getenv("TELEGRAM_BOT_TOKEN", "")
        if not token:
            QMessageBox.critical(self, "Telegram", "TELEGRAM_BOT_TOKEN در .env یا محیط برنامه تنظیم نشده است.")
            return
        self.load_chats_btn.setEnabled(False)
        self.status_label.setText("در حال دریافت چت‌ها از Telegram ...")
        self.chat_loader = _ChatLoader(token)
        self.chat_loader.loaded.connect(self._apply_chats)
        self.chat_loader.failed.connect(self._chat_failed)
        self.chat_loader.finished.connect(lambda: self.load_chats_btn.setEnabled(True))
        self.chat_loader.start()

    def _apply_chats(self, chats):
        self.chats = chats
        if not chats:
            QMessageBox.information(self, "Telegram", "از getUpdates چتی پیدا نشد. یک پیام در گروه بفرستید و دوباره بارگذاری کنید.")
            self.status_label.setText("هیچ چتی پیدا نشد")
            return
        dialog = ChatPicker(chats, self.chat_edit.text().strip(), self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.chat_edit.setText(dialog.selected_id)
            self.status_label.setText(f"چت انتخاب شد: {chats[dialog.selected_id]}")
        else:
            self.status_label.setText(f"{len(chats)} چت پیدا شد")

    def _chat_failed(self, error):
        self.status_label.setText("دریافت چت‌ها ناموفق بود")
        QMessageBox.critical(self, "Telegram", error)

    def prepare_topics_ui(self):
        if not self.save_current_job():
            return
        job = self._current_job()
        token = self.token.strip() or os.getenv("TELEGRAM_BOT_TOKEN", "")
        if not job or not token:
            return
        try:
            forum = TelegramForum(telegram_request)
            main_id, folder_record = _ensure_folder_topic(forum, token, job)
            job["main_topic_id"] = main_id
            job["folder_id"] = folder_record["id"]
            if job.get("history_enabled", True):
                job["history_topic_id"] = _ensure_project_history_topic(forum, token, job["chat_id"])
            save_jobs(self.jobs)
            self.status_label.setText(f"Topic آماده است: {main_id}")
            QMessageBox.information(self, "Telegram", f"Topic فولدر آماده شد.\nTopic ID: {main_id}")
        except Exception as exc:
            QMessageBox.critical(self, "Telegram", str(exc))

    def start_backup(self):
        if self.worker and self.worker.isRunning():
            QMessageBox.information(self, "Backup", "یک عملیات دیگر در حال اجراست.")
            return
        if not self.save_current_job():
            return
        job = self._current_job()
        if not job:
            return
        token = self.token.strip() or os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.progress.setValue(0)
        self.worker = _Worker(token, dict(job))
        self.worker.progress.connect(self.progress.setValue)
        self.worker.log.connect(self.status_label.setText)
        self.worker.success.connect(self._backup_done)
        self.worker.failure.connect(self._backup_failed)
        self.worker.start()
        self.status_label.setText(f"در حال Backup: {job.get('name', 'Job')}")

    def _backup_done(self, result):
        count, completed, job = result
        for current in self.jobs:
            if current.get("id") == job.get("id"):
                current.update(job)
        save_jobs(self.jobs)
        self.status_label.setText(f"Backup {'کامل شد' if completed else 'متوقف شد'} • {count} فایل")
        self.progress.setValue(100 if completed else self.progress.value())

    def _backup_failed(self, error):
        self.status_label.setText("Backup ناموفق بود")
        QMessageBox.critical(self, "Backup", error)

    def stop_all(self):
        if self.worker and self.worker.isRunning():
            self.worker.cancel()
        self.scheduler_stop.set()
        self.status_label.setText("Scheduler / عملیات متوقف شد")

    def start_scheduler(self):
        if self.scheduler_thread and self.scheduler_thread.is_alive():
            self.status_label.setText("Scheduler از قبل فعال است")
            return
        if not self.jobs:
            QMessageBox.information(self, "Scheduler", "حداقل یک Job بسازید.")
            return
        self.scheduler_stop.clear()
        self.scheduler_thread = threading.Thread(target=self._scheduler_loop, daemon=True)
        self.scheduler_thread.start()
        self.status_label.setText("Scheduler فعال است؛ فقط Jobهای Active اجرا می‌شوند")

    def _scheduler_loop(self):
        last_runs: dict[str, object] = {}
        while not self.scheduler_stop.is_set():
            now = datetime.now()
            jobs = load_and_migrate_jobs()
            for job in jobs:
                if self.scheduler_stop.is_set():
                    break
                if not job.get("enabled", True) or job.get("schedule") != now.strftime("%H:%M"):
                    continue
                if last_runs.get(job["id"]) == now.date():
                    continue
                last_runs[job["id"]] = now.date()
                if self.worker and self.worker.isRunning():
                    continue
                token = self.token.strip() or os.getenv("TELEGRAM_BOT_TOKEN", "")
                self.worker = _Worker(token, dict(job))
                self.worker.progress.connect(self.progress.setValue)
                self.worker.log.connect(self.status_label.setText)
                self.worker.success.connect(self._backup_done)
                self.worker.failure.connect(self._backup_failed)
                self.worker.start()
            self.scheduler_stop.wait(10)

    def closeEvent(self, event):
        self.stop_all()
        if self.worker and self.worker.isRunning():
            self.worker.wait(1000)
        if self.chat_loader and self.chat_loader.isRunning():
            self.chat_loader.wait(1000)
        event.accept()


def apply_style(app: QApplication):
    app.setStyleSheet("""
        QWidget { font-family: 'Segoe UI'; font-size: 10pt; }
        QMainWindow { background: #edf1f5; }
        QFrame#header { background: #17324d; border-radius: 10px; }
        QLabel#headerTitle { color: white; font-size: 19pt; font-weight: 700; }
        QLabel#headerSubtitle { color: #d6e1ea; font-size: 10pt; }
        QFrame#card { background: white; border: 1px solid #d8e0e7; border-radius: 10px; }
        QFrame#softCard { background: #f6f8fa; border: 1px solid #e2e7ec; border-radius: 8px; }
        QLabel#sectionTitle { color: #17324d; font-size: 13pt; font-weight: 700; }
        QLabel#subTitle { color: #17324d; font-weight: 700; }
        QLabel#muted { color: #657586; }
        QLabel#dialogTitle { color: #17324d; font-size: 12pt; font-weight: 700; padding: 4px; }
        QPushButton { min-height: 34px; padding: 0 12px; border: 1px solid #c8d1da; border-radius: 6px; background: #ffffff; }
        QPushButton:hover { background: #f0f4f7; }
        QPushButton#primary { background: #17324d; color: white; border-color: #17324d; font-weight: 700; }
        QPushButton#danger { color: #a32626; border-color: #e2baba; }
        QPushButton#danger:hover { background: #fff2f2; }
        QLineEdit, QPlainTextEdit, QListWidget, QComboBox { border: 1px solid #c9d3dc; border-radius: 6px; background: white; padding: 6px; }
        QLineEdit { min-height: 28px; }
        QListWidget::item { padding: 9px 7px; }
        QListWidget::item:selected { background: #e8eff6; color: #17324d; }
        QProgressBar { border: 1px solid #cad4dd; border-radius: 5px; background: #f4f6f8; height: 10px; }
        QProgressBar::chunk { background: #17324d; border-radius: 5px; }
        QSplitter::handle { background: #dce2e8; }
    """)


if __name__ == "__main__":
    app = QApplication.instance() or QApplication([])
    apply_style(app)
    window = BackupApp()
    window.show()
    app.exec()
