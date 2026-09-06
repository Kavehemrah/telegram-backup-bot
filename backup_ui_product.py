from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from datetime import datetime, timedelta
from pathlib import Path

from PySide6.QtCore import QThread, QTimer, Qt, Signal
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
    QSpinBox,
    QTabWidget,
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
    new_job,
    save_jobs,
)
from telegram_forum import TelegramForum


BASE_DIR = Path(__file__).resolve().parent
SETTINGS_FILE = BASE_DIR / "backup_ui_settings.json"
TASK_NAME = "TelegramFolderBackup"


T = {
    "fa": {
        "dashboard": "داشبورد",
        "jobs": "مدیریت Jobها",
        "help": "راهنما و تنظیمات",
        "title": "پشتیبان‌گیری تلگرام",
        "subtitle": "مدیریت Job مستقل • Topic پایدار • History مرکزی",
        "jobs_count": "تعداد Job",
        "active_jobs": "Job فعال",
        "scheduler": "Scheduler",
        "next": "اجرای بعدی",
        "on": "● Scheduler فعال",
        "off": "○ Scheduler خاموش",
        "new": "＋ Job جدید",
        "pause": "توقف / ادامه",
        "delete": "حذف Job انتخاب‌شده",
        "name": "نام Job",
        "time": "زمان روزانه",
        "folder": "فولدر",
        "topic": "نام Topic",
        "chat": "Chat ID",
        "choose": "انتخاب فولدر",
        "load": "بارگذاری چت‌ها",
        "behavior": "رفتار Backup",
        "all": "کل فولدر",
        "selected": "فایل‌های انتخابی",
        "replace": "جایگذاری نسخه قبلی",
        "history": "History مرکزی",
        "enabled": "فعال برای Scheduler",
        "files": "فایل‌های Backup",
        "manage": "انتخاب / مدیریت فایل‌ها",
        "select_all": "انتخاب همه",
        "clear": "پاک کردن",
        "save": "ذخیره تغییرات",
        "topic_btn": "ساخت / اتصال Topic",
        "run": "▶ اجرای همین Job",
        "stop": "توقف",
        "start": "شروع Scheduler",
        "quick": "راهنمای شروع سریع",
        "setup": "وضعیت آماده‌سازی",
        "language": "زبان برنامه",
        "autostart": "اجرای خودکار هنگام ورود به Windows",
        "missed": "Job از دست‌رفته پس از روشن شدن سیستم",
        "never": "هرگز",
        "always": "اجرای اولین بررسی",
        "grace": "فقط تا پنجره تأخیر",
        "minutes": "حداکثر دقیقه تأخیر",
        "apply": "فعال‌سازی Startup ویندوز",
        "remove": "حذف Startup",
        "save_settings": "ذخیره تنظیمات",
        "configured": "✓ تنظیم شده",
        "not": "✗ تنظیم نشده",
        "active": "فعال",
        "paused": "متوقف",
        "none": "-",
        "help_steps": [
            "1) در Telegram با @BotFather یک Bot بسازید و Token بگیرید.",
            "2) یک Supergroup بسازید و Topics را فعال کنید.",
            "3) Bot را اضافه کنید و دسترسی مدیریت Topic و حذف پیام‌ها را بدهید.",
            "4) یک پیام در گروه بفرستید تا Chat در getUpdates دیده شود.",
            "5) «بارگذاری چت‌ها» را بزنید و Chat مقصد را انتخاب کنید.",
            "6) در مدیریت Jobها فولدر و ساعت اجرا را تنظیم کنید.",
            "7) برای Topic، یک‌بار «ساخت / اتصال Topic» را اجرا کنید.",
            "8) Scheduler را روشن کنید. با Auto-start برنامه همراه Windows اجرا می‌شود.",
        ],
    },
    "en": {
        "dashboard": "Dashboard",
        "jobs": "Jobs",
        "help": "Help & Settings",
        "title": "Telegram Backup",
        "subtitle": "Independent Jobs • persistent Topics • central History",
        "jobs_count": "Jobs",
        "active_jobs": "Active Jobs",
        "scheduler": "Scheduler",
        "next": "Next run",
        "on": "● Scheduler active",
        "off": "○ Scheduler off",
        "new": "＋ New Job",
        "pause": "Pause / Resume",
        "delete": "Delete selected Job",
        "name": "Job name",
        "time": "Daily time",
        "folder": "Folder",
        "topic": "Topic name",
        "chat": "Chat ID",
        "choose": "Choose folder",
        "load": "Load chats",
        "behavior": "Backup behavior",
        "all": "Entire folder",
        "selected": "Selected files",
        "replace": "Replace previous version",
        "history": "Central History",
        "enabled": "Enabled for Scheduler",
        "files": "Backup files",
        "manage": "Select / manage files",
        "select_all": "Select all",
        "clear": "Clear",
        "save": "Save changes",
        "topic_btn": "Create / connect Topic",
        "run": "▶ Run this Job",
        "stop": "Stop",
        "start": "Start Scheduler",
        "quick": "Quick start guide",
        "setup": "Setup status",
        "language": "Application language",
        "autostart": "Start automatically when Windows logs in",
        "missed": "Missed Job after startup",
        "never": "Never",
        "always": "Run on first check",
        "grace": "Only within delay window",
        "minutes": "Maximum delay (minutes)",
        "apply": "Enable Windows Startup",
        "remove": "Remove Startup",
        "save_settings": "Save settings",
        "configured": "✓ Configured",
        "not": "✗ Not configured",
        "active": "Active",
        "paused": "Paused",
        "none": "-",
        "help_steps": [
            "1) Create a Bot with @BotFather and copy its Token.",
            "2) Create a Telegram Supergroup and enable Topics.",
            "3) Add the Bot and grant permissions to manage Topics and delete messages.",
            "4) Send a message in the group so it appears in getUpdates.",
            "5) Click “Load chats” and select the destination Chat.",
            "6) Create a Job and choose its folder and daily time.",
            "7) For Topic mode, run “Create / connect Topic” once.",
            "8) Start Scheduler. With Auto-start enabled, the app starts with Windows.",
        ],
    },
}


def settings_load() -> dict:
    base = {
        "language": "fa",
        "autostart": False,
        "missed_policy": "never",
        "missed_minutes": 30,
        "last_runs": {},
    }
    try:
        if SETTINGS_FILE.exists():
            data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                base.update(data)
    except Exception:
        pass
    if not isinstance(base.get("last_runs"), dict):
        base["last_runs"] = {}
    return base


def settings_save(data: dict) -> None:
    tmp = SETTINGS_FILE.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tmp.replace(SETTINGS_FILE)


class Worker(QThread):
    progress = Signal(int)
    log = Signal(str)
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, token: str, job: dict):
        super().__init__()
        self.token = token
        self.job = job
        self.cancel_event = threading.Event()

    def run(self) -> None:
        try:
            result = run_job_backup(
                self.token,
                self.job,
                self.log.emit,
                lambda current, total: self.progress.emit(
                    int(current / total * 100) if total else 100
                ),
                self.cancel_event,
            )
            self.done.emit((result, self.job))
        except Exception as exc:
            self.failed.emit(str(exc))

    def cancel(self) -> None:
        self.cancel_event.set()


class ChatLoader(QThread):
    loaded = Signal(object)
    failed = Signal(str)

    def __init__(self, token: str):
        super().__init__()
        self.token = token

    def run(self) -> None:
        try:
            chats: dict[str, str] = {}
            for update in telegram_request(self.token, "getUpdates"):
                message = update.get("message") or update.get("channel_post") or {}
                chat = message.get("chat") or {}
                if chat.get("id") is None:
                    continue
                chat_id = str(chat["id"])
                title = chat.get("title") or chat.get("first_name") or "Unnamed"
                chats[chat_id] = f"{title} ({chat_id})"
            self.loaded.emit(chats)
        except Exception as exc:
            self.failed.emit(str(exc))


class ChatDialog(QDialog):
    def __init__(self, chats: dict[str, str], current: str, lang: str, parent=None):
        super().__init__(parent)
        self.lang = lang
        self.value = current
        self.setWindowTitle(
            "Select Telegram chat" if lang == "en" else "انتخاب چت Telegram"
        )
        self.resize(650, 540)

        layout = QVBoxLayout(self)
        title = QLabel(
            "Select destination chat" if lang == "en" else "چت مقصد را انتخاب کنید"
        )
        title.setObjectName("dialogTitle")
        layout.addWidget(title)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search..." if lang == "en" else "جست‌وجو...")
        layout.addWidget(self.search)

        self.list = QListWidget()
        layout.addWidget(self.list, 1)

        for chat_id, label in chats.items():
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, chat_id)
            self.list.addItem(item)
            if chat_id == str(current):
                self.list.setCurrentItem(item)

        if self.list.currentRow() < 0 and self.list.count():
            self.list.setCurrentRow(0)

        self.info = QLabel()
        self.info.setObjectName("muted")
        layout.addWidget(self.info)

        self.search.textChanged.connect(self.filter)
        self.list.itemDoubleClicked.connect(lambda _item: self.accept_selected())
        self.filter("")

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel
            | QDialogButtonBox.StandardButton.Ok
        )
        buttons.accepted.connect(self.accept_selected)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def filter(self, text: str) -> None:
        query = text.casefold().strip()
        visible = 0
        for index in range(self.list.count()):
            item = self.list.item(index)
            show = not query or query in item.text().casefold()
            item.setHidden(not show)
            visible += int(show)
        self.info.setText(f"{visible} chats" if self.lang == "en" else f"{visible} چت")

    def accept_selected(self) -> None:
        item = self.list.currentItem()
        if not item or item.isHidden():
            QMessageBox.warning(
                self,
                "Chat",
                "Select a chat first." if self.lang == "en" else "یک چت را انتخاب کنید.",
            )
            return
        self.value = str(item.data(Qt.ItemDataRole.UserRole))
        self.accept()


class FileDialog(QDialog):
    def __init__(self, folder: str, files: list[Path], selected: set[str], lang: str, parent=None):
        super().__init__(parent)
        self.selected = set(selected)
        self.checks: dict[str, QCheckBox] = {}
        self.lang = lang
        self.folder = folder
        self.setWindowTitle(
            "Select backup files" if lang == "en" else "انتخاب فایل‌های Backup"
        )
        self.resize(920, 680)

        layout = QVBoxLayout(self)
        title = QLabel(
            "Tick the files to include in Selected files mode."
            if lang == "en"
            else "تیک فایل‌هایی را بزنید که باید در حالت فایل‌های انتخابی Backup شوند."
        )
        title.setWordWrap(True)
        title.setObjectName("dialogTitle")
        layout.addWidget(title)

        self.search = QLineEdit()
        self.search.setPlaceholderText(
            "Search file name or path..."
            if lang == "en"
            else "جست‌وجو در نام یا مسیر فایل..."
        )
        layout.addWidget(self.search)

        self.list = QListWidget()
        self.list.setAlternatingRowColors(True)
        layout.addWidget(self.list, 1)

        for path in files:
            full = str(path.resolve())
            item = QListWidgetItem()
            check = QCheckBox(str(path.relative_to(Path(folder))))
            check.setChecked(full in self.selected)
            self.checks[full] = check
            self.list.addItem(item)
            item.setSizeHint(check.sizeHint())
            self.list.setItemWidget(item, check)
            check.stateChanged.connect(self.count)

        self.search.textChanged.connect(self.filter)
        self.info = QLabel()
        self.info.setObjectName("muted")
        layout.addWidget(self.info)

        tools = QHBoxLayout()
        select_all = QPushButton("انتخاب همه" if lang == "fa" else "Select all")
        clear = QPushButton("پاک کردن" if lang == "fa" else "Clear")
        select_all.setObjectName("secondary")
        clear.setObjectName("secondary")
        select_all.clicked.connect(lambda: self.set_all(True))
        clear.clicked.connect(lambda: self.set_all(False))
        tools.addWidget(select_all)
        tools.addWidget(clear)
        tools.addStretch()
        layout.addLayout(tools)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel
            | QDialogButtonBox.StandardButton.Ok
        )
        buttons.accepted.connect(self.accept_selected)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.count()

    def filter(self, text: str) -> None:
        query = text.casefold().strip()
        for index in range(self.list.count()):
            item = self.list.item(index)
            check = self.list.itemWidget(item)
            item.setHidden(bool(query) and query not in check.text().casefold())

    def set_all(self, value: bool) -> None:
        for check in self.checks.values():
            check.setChecked(value)
        self.count()

    def count(self, *_args) -> None:
        checked = sum(check.isChecked() for check in self.checks.values())
        self.info.setText(f"{checked} / {len(self.checks)}")

    def accept_selected(self) -> None:
        self.selected = {
            path for path, check in self.checks.items() if check.isChecked()
        }
        self.accept()


class BackupApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.s = settings_load()
        self.lang = self.s.get("language", "fa")
        if self.lang not in T:
            self.lang = "fa"
        self.jobs = load_and_migrate_jobs()
        self.worker: Worker | None = None
        self.loader: ChatLoader | None = None
        self.new_mode = False
        self.pending_selected: list[str] = []
        self.token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.sched = False

        self.timer = QTimer(self)
        self.timer.setInterval(10_000)
        self.timer.timeout.connect(self.scheduler_tick)

        self.build()
        self.apply_lang()
        self.reload_jobs()

        if self.s.get("autostart"):
            self.start_scheduler(silent=True)

    def tr(self, key: str | None = None):
        data = T[self.lang]
        return data if key is None else data.get(key, key)

    def build(self) -> None:
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(12)
        self.setCentralWidget(central)

        header = QFrame()
        header.setObjectName("header")
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(22, 15, 22, 15)
        self.ht = QLabel()
        self.ht.setObjectName("headerTitle")
        self.hs = QLabel()
        self.hs.setObjectName("headerSubtitle")
        header_layout.addWidget(self.ht)
        header_layout.addWidget(self.hs)
        root.addWidget(header)

        self.tabs = QTabWidget()
        self.tab_dash = self.dashboard_tab()
        self.tab_jobs = self.jobs_tab()
        self.tab_help = self.help_tab()
        self.tabs.addTab(self.tab_dash, "")
        self.tabs.addTab(self.tab_jobs, "")
        self.tabs.addTab(self.tab_help, "")
        root.addWidget(self.tabs, 1)

        status_frame = QFrame()
        status_frame.setObjectName("status")
        status_layout = QHBoxLayout(status_frame)
        self.status = QLabel()
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setMaximumWidth(420)
        status_layout.addWidget(self.status, 1)
        status_layout.addWidget(self.progress)
        root.addWidget(status_frame)

    def dashboard_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        cards = QHBoxLayout()
        self.metrics: list[QFrame] = []

        for _ in range(4):
            frame = QFrame()
            frame.setObjectName("metric")
            card = QVBoxLayout(frame)
            title = QLabel()
            title.setObjectName("metricTitle")
            value = QLabel("-")
            value.setObjectName("metricValue")
            card.addWidget(title)
            card.addWidget(value)
            frame.title = title
            frame.value = value
            cards.addWidget(frame, 1)
            self.metrics.append(frame)

        layout.addLayout(cards)

        box = QFrame()
        box.setObjectName("softCard")
        box_layout = QVBoxLayout(box)
        self.dtitle = QLabel()
        self.dtitle.setObjectName("sectionTitle")
        self.dtext = QPlainTextEdit()
        self.dtext.setReadOnly(True)
        box_layout.addWidget(self.dtitle)
        box_layout.addWidget(self.dtext, 1)

        buttons = QHBoxLayout()
        self.d_sched = QPushButton()
        self.d_sched.setObjectName("scheduler")
        self.d_sched.clicked.connect(self.toggle_scheduler)
        self.d_jobs = QPushButton()
        self.d_jobs.setObjectName("primary")
        self.d_jobs.clicked.connect(lambda: self.tabs.setCurrentIndex(1))
        buttons.addWidget(self.d_sched)
        buttons.addWidget(self.d_jobs)
        buttons.addStretch()
        box_layout.addLayout(buttons)
        layout.addWidget(box, 1)
        return widget

    def jobs_tab(self) -> QWidget:
        widget = QWidget()
        root = QHBoxLayout(widget)

        left = QFrame()
        left.setObjectName("card")
        left_layout = QVBoxLayout(left)
        self.jtitle = QLabel()
        self.jtitle.setObjectName("sectionTitle")
        self.jinfo = QLabel()
        self.jinfo.setWordWrap(True)
        self.jinfo.setObjectName("muted")
        self.jobs_list = QListWidget()
        self.jobs_list.currentRowChanged.connect(self.select_job)
        left_layout.addWidget(self.jtitle)
        left_layout.addWidget(self.jinfo)
        left_layout.addWidget(self.jobs_list, 1)

        list_buttons = QHBoxLayout()
        self.newb = QPushButton()
        self.newb.setObjectName("primary")
        self.pb = QPushButton()
        self.pb.setObjectName("secondary")
        self.newb.clicked.connect(self.new_job_ui)
        self.pb.clicked.connect(self.toggle_job)
        list_buttons.addWidget(self.newb, 1)
        list_buttons.addWidget(self.pb, 1)
        left_layout.addLayout(list_buttons)

        self.delb = QPushButton()
        self.delb.setObjectName("danger")
        self.delb.clicked.connect(self.delete_job)
        left_layout.addWidget(self.delb)
        root.addWidget(left, 1)

        right = QFrame()
        right.setObjectName("card")
        right_layout = QVBoxLayout(right)
        self.et = QLabel()
        self.et.setObjectName("sectionTitle")
        right_layout.addWidget(self.et)

        form = QFormLayout()
        self.name = QLineEdit()
        self.time = QLineEdit("23:00")
        self.folder = QLineEdit()
        self.folder.setReadOnly(True)
        self.topic = QLineEdit()
        self.chat = QLineEdit()

        choose_folder = QPushButton()
        choose_folder.setObjectName("secondary")
        choose_folder.clicked.connect(self.choose_folder)
        folder_row = QHBoxLayout()
        folder_row.addWidget(self.folder, 1)
        folder_row.addWidget(choose_folder)
        self.fb = choose_folder

        load_chats = QPushButton()
        load_chats.setObjectName("secondary")
        load_chats.clicked.connect(self.load_chats)
        chat_row = QHBoxLayout()
        chat_row.addWidget(self.chat, 1)
        chat_row.addWidget(load_chats)
        self.lb = load_chats

        # Explicit QLabel instances prevent QFormLayout from returning None for rows
        # whose field is a nested QHBoxLayout.
        self.name_label = QLabel()
        self.time_label = QLabel()
        self.folder_label = QLabel()
        self.topic_label = QLabel()
        self.chat_label = QLabel()

        form.addRow(self.name_label, self.name)
        form.addRow(self.time_label, self.time)
        form.addRow(self.folder_label, folder_row)
        form.addRow(self.topic_label, self.topic)
        form.addRow(self.chat_label, chat_row)
        right_layout.addLayout(form)

        behavior = QFrame()
        behavior.setObjectName("softCard")
        behavior_layout = QVBoxLayout(behavior)
        self.bt = QLabel()
        self.bt.setObjectName("subTitle")
        behavior_layout.addWidget(self.bt)

        mode_row = QHBoxLayout()
        self.all = QRadioButton()
        self.sel = QRadioButton()
        self.all.toggled.connect(self.update_files)
        mode_row.addWidget(self.all)
        mode_row.addWidget(self.sel)
        mode_row.addStretch()
        behavior_layout.addLayout(mode_row)

        check_row = QHBoxLayout()
        self.rep = QCheckBox()
        self.hist = QCheckBox()
        self.en = QCheckBox()
        self.rep.setChecked(True)
        self.hist.setChecked(True)
        self.en.setChecked(True)
        check_row.addWidget(self.rep)
        check_row.addWidget(self.hist)
        check_row.addWidget(self.en)
        check_row.addStretch()
        behavior_layout.addLayout(check_row)
        right_layout.addWidget(behavior)

        files_box = QFrame()
        files_box.setObjectName("softCard")
        files_layout = QVBoxLayout(files_box)
        self.ft = QLabel()
        self.ft.setObjectName("subTitle")
        files_layout.addWidget(self.ft)

        file_controls = QHBoxLayout()
        self.fs = QLabel()
        self.fs.setObjectName("muted")
        self.manage = QPushButton()
        self.manage.setObjectName("secondary")
        self.sa = QPushButton()
        self.sa.setObjectName("secondary")
        self.clear = QPushButton()
        self.clear.setObjectName("secondary")
        self.manage.clicked.connect(self.open_files)
        self.sa.clicked.connect(lambda: self.set_all(True))
        self.clear.clicked.connect(lambda: self.set_all(False))
        file_controls.addWidget(self.fs, 1)
        file_controls.addWidget(self.manage)
        file_controls.addWidget(self.sa)
        file_controls.addWidget(self.clear)
        files_layout.addLayout(file_controls)

        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        files_layout.addWidget(self.preview, 1)
        right_layout.addWidget(files_box, 1)

        actions = QHBoxLayout()
        self.saveb = QPushButton()
        self.saveb.setObjectName("primary")
        self.runb = QPushButton()
        self.runb.setObjectName("run")
        self.topb = QPushButton()
        self.topb.setObjectName("secondary")
        self.stopb = QPushButton()
        self.stopb.setObjectName("danger")
        self.schb = QPushButton()
        self.schb.setObjectName("scheduler")
        self.saveb.clicked.connect(self.save)
        self.runb.clicked.connect(self.start_backup)
        self.topb.clicked.connect(self.prepare_topic)
        self.stopb.clicked.connect(self.stop_all)
        self.schb.clicked.connect(self.toggle_scheduler)
        actions.addWidget(self.saveb)
        actions.addWidget(self.runb)
        actions.addWidget(self.topb)
        actions.addWidget(self.stopb)
        actions.addStretch()
        actions.addWidget(self.schb)
        right_layout.addLayout(actions)

        root.addWidget(right, 2)
        return widget

    def help_tab(self) -> QWidget:
        widget = QWidget()
        root = QHBoxLayout(widget)

        help_box = QFrame()
        help_box.setObjectName("card")
        help_layout = QVBoxLayout(help_box)
        self.h_title = QLabel()
        self.h_title.setObjectName("sectionTitle")
        self.h_text = QPlainTextEdit()
        self.h_text.setReadOnly(True)
        self.h_note = QLabel()
        self.h_note.setWordWrap(True)
        self.h_note.setObjectName("muted")
        help_layout.addWidget(self.h_title)
        help_layout.addWidget(self.h_text, 1)
        help_layout.addWidget(self.h_note)
        root.addWidget(help_box, 2)

        setup_box = QFrame()
        setup_box.setObjectName("card")
        setup_layout = QVBoxLayout(setup_box)
        self.setup_title = QLabel()
        self.setup_title.setObjectName("sectionTitle")
        self.ts = QLabel()
        self.cs = QLabel()
        self.js = QLabel()
        setup_layout.addWidget(self.setup_title)
        setup_layout.addWidget(self.ts)
        setup_layout.addWidget(self.cs)
        setup_layout.addWidget(self.js)

        self.ll = QLabel()
        self.ll.setObjectName("subTitle")
        self.langbox = QComboBox()
        self.langbox.addItem("فارسی", "fa")
        self.langbox.addItem("English", "en")
        self.langbox.currentIndexChanged.connect(self.change_lang)
        setup_layout.addWidget(self.ll)
        setup_layout.addWidget(self.langbox)

        self.auto = QCheckBox()
        self.auto.setChecked(bool(self.s.get("autostart")))
        setup_layout.addWidget(self.auto)

        self.ml = QLabel()
        self.ml.setObjectName("subTitle")
        setup_layout.addWidget(self.ml)
        self.mc = QComboBox()
        setup_layout.addWidget(self.mc)

        self.minutes_label = QLabel()
        self.minutes_label.setObjectName("muted")
        setup_layout.addWidget(self.minutes_label)
        self.min = QSpinBox()
        self.min.setRange(1, 1440)
        self.min.setValue(int(self.s.get("missed_minutes", 30)))
        setup_layout.addWidget(self.min)

        self.ab = QPushButton()
        self.ab.setObjectName("primary")
        self.ab.clicked.connect(self.autostart)
        setup_layout.addWidget(self.ab)

        self.rb = QPushButton()
        self.rb.setObjectName("danger")
        self.rb.clicked.connect(self.remove_autostart)
        setup_layout.addWidget(self.rb)

        self.sb = QPushButton()
        self.sb.setObjectName("secondary")
        self.sb.clicked.connect(self.save_settings)
        setup_layout.addWidget(self.sb)
        setup_layout.addStretch()
        root.addWidget(setup_box, 1)
        return widget

    def apply_lang(self) -> None:
        t = self.tr()
        self.setWindowTitle(t["title"])
        self.ht.setText(t["title"])
        self.hs.setText(t["subtitle"])
        self.tabs.setTabText(0, t["dashboard"])
        self.tabs.setTabText(1, t["jobs"])
        self.tabs.setTabText(2, t["help"])

        self.jtitle.setText(t["jobs"])
        self.jinfo.setText(
            "حذف Job، Folder Identity و Topic را حذف نمی‌کند."
            if self.lang == "fa"
            else "Deleting a Job does not delete its Folder Identity or Topic."
        )
        self.et.setText(
            t["jobs"] + " / " + ("تنظیمات" if self.lang == "fa" else "Settings")
        )

        self.fb.setText(t["choose"])
        self.lb.setText(t["load"])
        self.bt.setText(t["behavior"])
        self.all.setText(t["all"])
        self.sel.setText(t["selected"])
        self.rep.setText(t["replace"])
        self.hist.setText(t["history"])
        self.en.setText(t["enabled"])
        self.ft.setText(t["files"])
        self.manage.setText(t["manage"])
        self.sa.setText(t["select_all"])
        self.clear.setText(t["clear"])
        self.saveb.setText(t["save"])
        self.runb.setText(t["run"])
        self.topb.setText(t["topic_btn"])
        self.stopb.setText(t["stop"])
        self.schb.setText(t["off"] if self.sched else t["start"])
        self.newb.setText(t["new"])
        self.pb.setText(t["pause"])
        self.delb.setText(t["delete"])

        self.name_label.setText(t["name"])
        self.time_label.setText(t["time"])
        self.folder_label.setText(t["folder"])
        self.topic_label.setText(t["topic"])
        self.chat_label.setText(t["chat"])

        self.h_title.setText(t["quick"])
        self.h_text.setPlainText("\n".join(t["help_steps"]))
        self.h_note.setText(
            "Windows must be running for the built-in Scheduler."
            if self.lang == "en"
            else "برای اجرای داخلی Scheduler، ویندوز و برنامه باید در حال اجرا باشند."
        )
        self.setup_title.setText(t["setup"])
        self.ll.setText(t["language"])
        self.ml.setText(t["missed"])
        self.minutes_label.setText(t["minutes"])

        current_policy = self.s.get("missed_policy", "never")
        self.mc.blockSignals(True)
        self.mc.clear()
        self.mc.addItem(t["never"], "never")
        self.mc.addItem(t["always"], "always")
        self.mc.addItem(t["grace"], "grace")
        index = self.mc.findData(current_policy)
        self.mc.setCurrentIndex(max(0, index))
        self.mc.blockSignals(False)

        self.ab.setText(t["apply"])
        self.rb.setText(t["remove"])
        self.sb.setText(t["save_settings"])
        self.update_dashboard()
        self.setup_status()
        self.update_files()

        self.langbox.blockSignals(True)
        self.langbox.setCurrentIndex(0 if self.lang == "fa" else 1)
        self.langbox.blockSignals(False)

    def change_lang(self) -> None:
        language = self.langbox.currentData()
        if language in T and language != self.lang:
            self.lang = language
            self.s["language"] = language
            settings_save(self.s)
            self.apply_lang()

    def reload_jobs(self, row: int | None = None) -> None:
        self.jobs_list.blockSignals(True)
        self.jobs_list.clear()
        for job in self.jobs:
            folder_name = Path(job.get("folder", "")).name or "No folder"
            state = self.tr("active") if job.get("enabled", True) else self.tr("paused")
            mode = self.tr("selected") if job.get("backup_mode") == "SELECTED" else self.tr("all")
            self.jobs_list.addItem(
                QListWidgetItem(
                    f"{job.get('name', folder_name)}\n"
                    f"{folder_name} • {state} • {job.get('schedule', '23:00')} • {mode}"
                )
            )
        self.jobs_list.blockSignals(False)

        if self.jobs:
            target = 0 if row is None else min(row, len(self.jobs) - 1)
            self.jobs_list.setCurrentRow(target)
        else:
            self.new_job_ui()
        self.update_dashboard()
        self.setup_status()

    def select_job(self, row: int) -> None:
        if row < 0 or row >= len(self.jobs):
            return
        job = self.jobs[row]
        self.new_mode = False
        self.name.setText(job.get("name", ""))
        self.folder.setText(job.get("folder", ""))
        self.topic.setText(job.get("main_topic_name", job.get("name", "")))
        self.chat.setText(str(job.get("chat_id", "")))
        self.time.setText(job.get("schedule", "23:00"))
        self.en.setChecked(job.get("enabled", True))
        self.rep.setChecked(job.get("replace_files", True))
        self.hist.setChecked(job.get("history_enabled", True))
        selected_mode = job.get("backup_mode", "ALL") == "SELECTED"
        self.sel.setChecked(selected_mode)
        self.all.setChecked(not selected_mode)
        self.update_files()

    def new_job_ui(self) -> None:
        self.jobs_list.clearSelection()
        self.new_mode = True
        self.pending_selected = []
        self.name.clear()
        self.folder.clear()
        self.topic.clear()
        self.chat.setText(os.getenv("TELEGRAM_CHAT_ID", ""))
        self.time.setText("23:00")
        self.all.setChecked(True)
        self.rep.setChecked(True)
        self.hist.setChecked(True)
        self.en.setChecked(True)
        self.preview.clear()
        self.fs.setText(
            "ابتدا فولدر را انتخاب کنید" if self.lang == "fa" else "Choose a folder"
        )

    def choose_folder(self) -> None:
        selected_folder = QFileDialog.getExistingDirectory(
            self, self.tr("choose")
        )
        if not selected_folder:
            return

        folder = str(Path(selected_folder).resolve())
        current = self._current_job()
        if current and not self.new_mode:
            current_folder = str(Path(current.get("folder", "")).resolve())
            if folder != current_folder:
                box = QMessageBox(self)
                box.setWindowTitle(self.tr("folder"))
                box.setText(
                    "This Job already exists."
                    if self.lang == "en"
                    else "این Job از قبل وجود دارد."
                )
                edit_button = box.addButton(
                    "Edit this Job" if self.lang == "en" else "ویرایش همین Job",
                    QMessageBox.ButtonRole.AcceptRole,
                )
                new_button = box.addButton(
                    self.tr("new"), QMessageBox.ButtonRole.ActionRole
                )
                box.addButton(
                    "Cancel" if self.lang == "en" else "لغو",
                    QMessageBox.ButtonRole.RejectRole,
                )
                box.exec()
                clicked = box.clickedButton()
                if clicked is new_button:
                    self.new_job_ui()
                elif clicked is not edit_button:
                    return

        self.folder.setText(folder)
        if not self.name.text().strip():
            self.name.setText(Path(folder).name)
        if not self.topic.text().strip():
            self.topic.setText(Path(folder).name)
        self.update_files()

    def _current_job(self) -> dict | None:
        row = self.jobs_list.currentRow()
        return self.jobs[row] if 0 <= row < len(self.jobs) else None

    def _selected(self) -> set[str]:
        if self.new_mode:
            return set(self.pending_selected)
        job = self._current_job() or {}
        return {str(Path(path).resolve()) for path in job.get("selected_files", [])}

    def update_files(self) -> None:
        folder = self.folder.text().strip()
        if not folder or not Path(folder).is_dir():
            self.preview.clear()
            return

        files = current_files(folder, STATE_FILES)
        selected = self._selected()
        chosen = sum(str(path.resolve()) in selected for path in files)

        if self.sel.isChecked():
            if self.lang == "en":
                self.fs.setText(f"{chosen}/{len(files)} selected")
            else:
                self.fs.setText(f"{chosen} فایل از {len(files)} فایل انتخاب شده")
        else:
            self.fs.setText(
                f"Entire folder • {len(files)} files"
                if self.lang == "en"
                else f"کل فولدر • {len(files)} فایل موجود"
            )

        lines = []
        base = Path(folder)
        for path in files[:500]:
            prefix = "✓  " if str(path.resolve()) in selected else "·  "
            lines.append(prefix + str(path.relative_to(base)))
        self.preview.setPlainText("\n".join(lines))

    def open_files(self) -> None:
        folder = self.folder.text().strip()
        if not folder or not Path(folder).is_dir():
            QMessageBox.warning(
                self,
                self.tr("files"),
                "Choose a valid folder first."
                if self.lang == "en"
                else "ابتدا یک فولدر معتبر انتخاب کنید.",
            )
            return

        files = current_files(folder, STATE_FILES)
        dialog = FileDialog(folder, files, self._selected(), self.lang, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            if self.new_mode:
                self.pending_selected = sorted(dialog.selected)
            elif self._current_job():
                self._current_job()["selected_files"] = sorted(dialog.selected)
            self.update_files()

    def set_all(self, value: bool) -> None:
        folder = self.folder.text().strip()
        if not folder or not Path(folder).is_dir():
            return
        selected = (
            [str(path.resolve()) for path in current_files(folder, STATE_FILES)]
            if value
            else []
        )
        if self.new_mode:
            self.pending_selected = selected
        elif self._current_job():
            self._current_job()["selected_files"] = selected
        self.update_files()

    def validate(self) -> None:
        folder = self.folder.text().strip()
        if not folder or not Path(folder).is_dir():
            raise ValueError(
                "Invalid folder" if self.lang == "en" else "فولدر معتبر نیست"
            )
        if not (self.token.strip() or os.getenv("TELEGRAM_BOT_TOKEN")):
            raise ValueError(
                "TELEGRAM_BOT_TOKEN is missing"
                if self.lang == "en"
                else "TELEGRAM_BOT_TOKEN تنظیم نشده است"
            )
        if not self.chat.text().strip():
            raise ValueError(
                "Chat ID is missing" if self.lang == "en" else "Chat ID وارد نشده است"
            )
        datetime.strptime(self.time.text().strip(), "%H:%M")

    def save(self) -> bool:
        try:
            self.validate()
        except ValueError as exc:
            QMessageBox.critical(self, self.tr("save"), str(exc))
            return False

        row = self.jobs_list.currentRow()
        folder = str(Path(self.folder.text()).resolve())
        selected = sorted(self._selected())

        if self.new_mode or row < 0:
            self.jobs.append(
                new_job(folder, self.chat.text().strip(), self.time.text().strip())
            )
            row = len(self.jobs) - 1

        job = self.jobs[row]
        folder_record = get_or_create_folder(folder)
        job.update(
            {
                "name": self.name.text().strip() or Path(folder).name,
                "folder": folder,
                "folder_id": folder_record["id"],
                "chat_id": self.chat.text().strip(),
                "destination": "topic",
                "main_topic_name": self.topic.text().strip() or Path(folder).name,
                "schedule": self.time.text().strip(),
                "enabled": self.en.isChecked(),
                "backup_mode": "SELECTED" if self.sel.isChecked() else "ALL",
                "selected_files": selected,
                "replace_files": self.rep.isChecked(),
                "history_enabled": self.hist.isChecked(),
            }
        )
        if folder_record.get("topic_id"):
            job["main_topic_id"] = folder_record["topic_id"]

        save_jobs(self.jobs)
        self.new_mode = False
        self.pending_selected = []
        self.reload_jobs(row)
        self.jobs_list.setCurrentRow(row)
        return True

    def toggle_job(self) -> None:
        row = self.jobs_list.currentRow()
        if row < 0:
            return
        self.jobs[row]["enabled"] = not self.jobs[row].get("enabled", True)
        save_jobs(self.jobs)
        self.reload_jobs(row)

    def delete_job(self) -> None:
        row = self.jobs_list.currentRow()
        if row < 0:
            return
        job = self.jobs[row]
        question = (
            f"Delete {job.get('name', 'Job')}?"
            if self.lang == "en"
            else f"Job «{job.get('name', 'Job')}» حذف شود؟\nFolder Identity و Topic حذف نمی‌شوند."
        )
        result = QMessageBox.question(
            self,
            self.tr("delete"),
            question,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if result == QMessageBox.StandardButton.Yes:
            del self.jobs[row]
            save_jobs(self.jobs)
            self.reload_jobs()

    def load_chats(self) -> None:
        token = self.token.strip() or os.getenv("TELEGRAM_BOT_TOKEN", "")
        if not token:
            QMessageBox.warning(
                self,
                "Telegram",
                "TELEGRAM_BOT_TOKEN is missing."
                if self.lang == "en"
                else "TELEGRAM_BOT_TOKEN تنظیم نشده است.",
            )
            return

        self.lb.setEnabled(False)
        self.loader = ChatLoader(token)
        self.loader.loaded.connect(self.apply_chats)
        self.loader.failed.connect(
            lambda error: QMessageBox.critical(self, "Telegram", error)
        )
        self.loader.finished.connect(lambda: self.lb.setEnabled(True))
        self.loader.start()

    def apply_chats(self, chats: dict[str, str]) -> None:
        if not chats:
            QMessageBox.information(
                self,
                "Telegram",
                "No chats found." if self.lang == "en" else "چتی پیدا نشد.",
            )
            return
        dialog = ChatDialog(chats, self.chat.text().strip(), self.lang, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.chat.setText(dialog.value)

    def prepare_topic(self) -> None:
        if not self.save():
            return
        job = self._current_job()
        if not job:
            return
        try:
            forum = TelegramForum(telegram_request)
            main_topic_id, folder_record = _ensure_folder_topic(
                forum,
                self.token.strip() or os.getenv("TELEGRAM_BOT_TOKEN", ""),
                job,
            )
            job["main_topic_id"] = main_topic_id
            job["folder_id"] = folder_record["id"]
            if job.get("history_enabled", True):
                job["history_topic_id"] = _ensure_project_history_topic(
                    forum,
                    self.token.strip() or os.getenv("TELEGRAM_BOT_TOKEN", ""),
                    job["chat_id"],
                )
            else:
                job["history_topic_id"] = None
            save_jobs(self.jobs)
            QMessageBox.information(
                self,
                "Telegram",
                f"Topic ready: {main_topic_id}"
                if self.lang == "en"
                else f"Topic آماده شد: {main_topic_id}",
            )
        except Exception as exc:
            QMessageBox.critical(self, "Telegram", str(exc))

    def start_backup(self) -> None:
        if self.worker and self.worker.isRunning():
            return
        if not self.save():
            return
        job = self._current_job()
        if not job:
            return

        self.progress.setValue(0)
        self.worker = Worker(
            self.token.strip() or os.getenv("TELEGRAM_BOT_TOKEN", ""),
            dict(job),
        )
        self.worker.progress.connect(self.progress.setValue)
        self.worker.log.connect(self.status.setText)
        self.worker.done.connect(self.backup_done)
        self.worker.failed.connect(
            lambda error: QMessageBox.critical(self, "Backup", error)
        )
        self.worker.start()

    def backup_done(self, result) -> None:
        (_count, completed), job = result
        self.s.setdefault("last_runs", {})[job["id"]] = datetime.now().isoformat(
            timespec="seconds"
        )
        settings_save(self.s)
        save_jobs(self.jobs)
        if completed:
            self.progress.setValue(100)
        self.update_dashboard()

    def stop_all(self) -> None:
        if self.worker and self.worker.isRunning():
            self.worker.cancel()
        self.timer.stop()
        self.sched = False
        self.update_dashboard()

    def toggle_scheduler(self) -> None:
        if self.sched:
            self.stop_all()
        else:
            self.start_scheduler()

    def start_scheduler(self, silent: bool = False) -> None:
        self.sched = True
        self.timer.start()
        self.scheduler_tick()
        self.update_dashboard()
        if not silent:
            self.status.setText(self.tr("on"))

    def scheduler_tick(self) -> None:
        if not self.sched or (self.worker and self.worker.isRunning()):
            self.update_dashboard()
            return

        now = datetime.now()
        today = now.date().isoformat()
        policy = self.s.get("missed_policy", "never")
        grace = int(self.s.get("missed_minutes", 30))

        for job in load_and_migrate_jobs():
            if not job.get("enabled", True):
                continue
            try:
                hour, minute = map(int, job.get("schedule", "23:00").split(":"))
                target = now.replace(
                    hour=hour,
                    minute=minute,
                    second=0,
                    microsecond=0,
                )
            except ValueError:
                continue

            last_run = self.s.setdefault("last_runs", {}).get(job.get("id"), "")
            if last_run.startswith(today) or now < target:
                continue

            age_minutes = (now - target).total_seconds() / 60
            if policy == "never" and age_minutes > 1:
                continue
            if policy == "grace" and age_minutes > grace:
                continue

            self.worker = Worker(
                self.token.strip() or os.getenv("TELEGRAM_BOT_TOKEN", ""),
                dict(job),
            )
            self.worker.progress.connect(self.progress.setValue)
            self.worker.log.connect(self.status.setText)
            self.worker.done.connect(self.backup_done)
            self.worker.failed.connect(
                lambda error: QMessageBox.critical(self, "Backup", error)
            )
            self.worker.start()
            break

        self.update_dashboard()

    def next_run(self):
        now = datetime.now()
        candidates = []
        for job in self.jobs:
            if not job.get("enabled", True):
                continue
            try:
                hour, minute = map(int, job.get("schedule", "23:00").split(":"))
                target = now.replace(
                    hour=hour,
                    minute=minute,
                    second=0,
                    microsecond=0,
                )
                if target <= now:
                    target += timedelta(days=1)
                candidates.append((target, job))
            except ValueError:
                continue
        return min(candidates, key=lambda item: item[0]) if candidates else (None, None)

    def update_dashboard(self) -> None:
        active = sum(bool(job.get("enabled", True)) for job in self.jobs)
        target, job = self.next_run()
        if target and job:
            next_text = f"{target:%Y-%m-%d %H:%M} • {job.get('name', '')}"
        else:
            next_text = self.tr("none")

        titles = [
            self.tr("jobs_count"),
            self.tr("active_jobs"),
            self.tr("scheduler"),
            self.tr("next"),
        ]
        values = [
            str(len(self.jobs)),
            str(active),
            "ON" if self.sched else "OFF",
            next_text,
        ]
        for metric, title, value in zip(self.metrics, titles, values):
            metric.title.setText(title)
            metric.value.setText(value)

        self.dtitle.setText(self.tr("on") if self.sched else self.tr("off"))
        self.dtext.setPlainText(
            "Scheduler checks active Jobs every 10 seconds."
            if self.lang == "en"
            else "Scheduler هر ۱۰ ثانیه Jobهای فعال را بررسی می‌کند."
        )
        self.d_sched.setText(self.tr("off") if self.sched else self.tr("start"))
        self.d_jobs.setText(self.tr("jobs"))
        self.schb.setText(self.tr("off") if self.sched else self.tr("start"))

    def setup_status(self) -> None:
        token_configured = bool(
            self.token.strip() or os.getenv("TELEGRAM_BOT_TOKEN")
        )
        chat_configured = any(job.get("chat_id") for job in self.jobs)
        active_count = sum(bool(job.get("enabled", True)) for job in self.jobs)
        self.ts.setText(
            f"Bot Token: {self.tr('configured') if token_configured else self.tr('not')}"
        )
        self.cs.setText(
            f"Chat: {self.tr('configured') if chat_configured else self.tr('not')}"
        )
        self.js.setText(f"{self.tr('active_jobs')}: {active_count}")

    def save_settings(self) -> None:
        self.s.update(
            {
                "language": self.lang,
                "autostart": self.auto.isChecked(),
                "missed_policy": self.mc.currentData(),
                "missed_minutes": self.min.value(),
            }
        )
        settings_save(self.s)
        self.status.setText(
            "Settings saved." if self.lang == "en" else "تنظیمات ذخیره شد."
        )

    def autostart(self) -> None:
        self.s["autostart"] = True
        settings_save(self.s)
        script_path = str((BASE_DIR / "run_backup_ui.py").resolve())
        command = f'"{sys.executable}" "{script_path}"'
        result = subprocess.run(
            [
                "schtasks",
                "/Create",
                "/SC",
                "ONLOGON",
                "/TN",
                TASK_NAME,
                "/TR",
                command,
                "/F",
            ],
            capture_output=True,
            text=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode != 0:
            self.s["autostart"] = False
            settings_save(self.s)
            QMessageBox.critical(
                self,
                "Windows",
                result.stderr.strip() or result.stdout.strip(),
            )
            return
        self.auto.setChecked(True)
        self.status.setText(
            "Auto-start enabled." if self.lang == "en" else "اجرای خودکار فعال شد."
        )

    def remove_autostart(self) -> None:
        subprocess.run(
            ["schtasks", "/Delete", "/TN", TASK_NAME, "/F"],
            capture_output=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        self.s["autostart"] = False
        settings_save(self.s)
        self.auto.setChecked(False)
        self.status.setText(
            "Auto-start removed." if self.lang == "en" else "اجرای خودکار حذف شد."
        )

    def closeEvent(self, event) -> None:
        if self.worker and self.worker.isRunning():
            self.worker.cancel()
            self.worker.wait(1000)
        if self.loader and self.loader.isRunning():
            self.loader.wait(1000)
        event.accept()


def apply_style(app: QApplication) -> None:
    app.setStyleSheet(
        """
        QWidget { font-size: 10.5pt; }
        QMainWindow { background: #f4f7fb; }
        QFrame#header { background: #1f6feb; border-radius: 14px; }
        QLabel#headerTitle { color: white; font-size: 19pt; font-weight: 700; }
        QLabel#headerSubtitle { color: #e8f0ff; font-size: 10pt; }
        QFrame#card, QFrame#softCard, QFrame#metric, QFrame#status { background: white; border: 1px solid #dde4ee; border-radius: 12px; }
        QLabel#sectionTitle { font-size: 14pt; font-weight: 700; }
        QLabel#subTitle { font-size: 11pt; font-weight: 600; }
        QLabel#muted { color: #667085; }
        QLabel#metricTitle { color: #667085; font-size: 9.5pt; }
        QLabel#metricValue { font-size: 16pt; font-weight: 700; }
        QLabel#dialogTitle { font-size: 13pt; font-weight: 700; }
        QPushButton { padding: 8px 12px; border-radius: 8px; border: 1px solid #ccd5e3; background: white; }
        QPushButton#primary { background: #1f6feb; color: white; border: none; }
        QPushButton#run { background: #2da44e; color: white; border: none; }
        QPushButton#scheduler { background: #8250df; color: white; border: none; }
        QPushButton#danger { background: #cf222e; color: white; border: none; }
        QPushButton#secondary { background: #eef3f8; border: none; }
        QLineEdit, QPlainTextEdit, QListWidget, QComboBox, QSpinBox { border: 1px solid #ccd5e3; border-radius: 8px; padding: 6px; background: white; }
        QTabBar::tab { padding: 9px 16px; }
        QProgressBar { border: 1px solid #ccd5e3; border-radius: 6px; background: white; }
        QProgressBar::chunk { background: #1f6feb; border-radius: 6px; }
        """
    )
