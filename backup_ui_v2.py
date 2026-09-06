from __future__ import annotations

import os
import threading
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QDialog, QDialogButtonBox, QFileDialog,
    QFormLayout, QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar,
    QPushButton, QRadioButton, QSplitter, QVBoxLayout, QWidget
)

from backup_bot import STATE_FILES, _ensure_folder_topic, _ensure_project_history_topic, run_job_backup, telegram_request
from backup_jobs import current_files, get_or_create_folder, load_and_migrate_jobs, new_job, save_jobs
from telegram_forum import TelegramForum


class Worker(QThread):
    progress = Signal(int)
    log = Signal(str)
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, token, job):
        super().__init__()
        self.token, self.job = token, job
        self.cancel_event = threading.Event()

    def run(self):
        try:
            result = run_job_backup(
                self.token, self.job, self.log.emit,
                lambda cur, total: self.progress.emit(int(cur / total * 100) if total else 100),
                self.cancel_event,
            )
            self.done.emit((result, self.job))
        except Exception as exc:
            self.failed.emit(str(exc))

    def cancel(self):
        self.cancel_event.set()


class ChatLoader(QThread):
    loaded = Signal(object)
    failed = Signal(str)

    def __init__(self, token):
        super().__init__()
        self.token = token

    def run(self):
        try:
            chats = {}
            for update in telegram_request(self.token, "getUpdates"):
                message = update.get("message") or update.get("channel_post") or {}
                chat = message.get("chat") or {}
                if chat.get("id") is None:
                    continue
                cid = str(chat["id"])
                title = chat.get("title") or chat.get("first_name") or "بدون نام"
                chats[cid] = f"{title}   ({cid})"
            self.loaded.emit(chats)
        except Exception as exc:
            self.failed.emit(str(exc))


class ChatDialog(QDialog):
    def __init__(self, chats, current, parent=None):
        super().__init__(parent)
        self.setWindowTitle("انتخاب چت Telegram")
        self.resize(650, 540)
        self.value = current
        layout = QVBoxLayout(self)
        title = QLabel("چت مقصد را انتخاب کنید")
        title.setObjectName("dialogTitle")
        layout.addWidget(title)
        self.search = QLineEdit()
        self.search.setPlaceholderText("جست‌وجوی نام یا Chat ID ...")
        layout.addWidget(self.search)
        self.list = QListWidget()
        layout.addWidget(self.list, 1)
        for cid, label in chats.items():
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, cid)
            self.list.addItem(item)
            if cid == str(current):
                self.list.setCurrentItem(item)
        if self.list.currentRow() < 0 and self.list.count():
            self.list.setCurrentRow(0)
        self.info = QLabel(f"{len(chats)} چت")
        self.info.setObjectName("muted")
        layout.addWidget(self.info)
        self.search.textChanged.connect(self.filter)
        self.list.itemDoubleClicked.connect(lambda _: self.accept_selected())
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok)
        buttons.accepted.connect(self.accept_selected)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def filter(self, text):
        q = text.strip().casefold()
        visible = 0
        for i in range(self.list.count()):
            item = self.list.item(i)
            show = not q or q in item.text().casefold()
            item.setHidden(not show)
            visible += int(show)
        self.info.setText(f"{visible} چت نمایش داده شد")
        if self.list.currentItem() is None or self.list.currentItem().isHidden():
            for i in range(self.list.count()):
                if not self.list.item(i).isHidden():
                    self.list.setCurrentRow(i)
                    break

    def accept_selected(self):
        item = self.list.currentItem()
        if not item or item.isHidden():
            QMessageBox.warning(self, "انتخاب چت", "یک چت را انتخاب کنید.")
            return
        self.value = str(item.data(Qt.ItemDataRole.UserRole))
        self.accept()


class FileDialog(QDialog):
    def __init__(self, folder: str, files: list[Path], selected: set[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("انتخاب فایل‌های Backup")
        self.resize(920, 680)
        self.checks = {}
        self.selected = set(selected)
        layout = QVBoxLayout(self)
        title = QLabel("تیک هر فایل مشخص می‌کند که در حالت «فایل‌های انتخابی» Backup شود.")
        title.setObjectName("dialogTitle")
        title.setWordWrap(True)
        layout.addWidget(title)
        self.search = QLineEdit()
        self.search.setPlaceholderText("جست‌وجو در نام یا مسیر فایل ...")
        layout.addWidget(self.search)
        self.list = QListWidget()
        self.list.setAlternatingRowColors(True)
        layout.addWidget(self.list, 1)
        for path in files:
            full = str(path.resolve())
            item = QListWidgetItem()
            checkbox = QCheckBox(str(path.relative_to(Path(folder))))
            checkbox.setChecked(full in self.selected)
            checkbox.stateChanged.connect(self.update_count)
            self.checks[full] = checkbox
            self.list.addItem(item)
            item.setSizeHint(checkbox.sizeHint())
            self.list.setItemWidget(item, checkbox)
        self.search.textChanged.connect(self.filter)
        self.info = QLabel()
        self.info.setObjectName("muted")
        layout.addWidget(self.info)
        tools = QHBoxLayout()
        all_btn = QPushButton("انتخاب همه")
        none_btn = QPushButton("پاک کردن همه")
        all_btn.setObjectName("secondary")
        none_btn.setObjectName("secondary")
        all_btn.clicked.connect(lambda: self.set_all(True))
        none_btn.clicked.connect(lambda: self.set_all(False))
        tools.addWidget(all_btn)
        tools.addWidget(none_btn)
        tools.addStretch()
        layout.addLayout(tools)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok)
        buttons.accepted.connect(self.accept_selected)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.update_count()

    def filter(self, text):
        q = text.strip().casefold()
        for i in range(self.list.count()):
            item = self.list.item(i)
            checkbox = self.list.itemWidget(item)
            item.setHidden(bool(q) and q not in checkbox.text().casefold())

    def set_all(self, value):
        for checkbox in self.checks.values():
            checkbox.setChecked(value)
        self.update_count()

    def update_count(self, *_):
        self.info.setText(f"{sum(c.isChecked() for c in self.checks.values())} فایل انتخاب شده از {len(self.checks)}")

    def accept_selected(self):
        self.selected = {p for p, checkbox in self.checks.items() if checkbox.isChecked()}
        self.accept()


class BackupApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Telegram Folder Backup")
        self.resize(1280, 840)
        self.setMinimumSize(1080, 720)
        self.jobs = load_and_migrate_jobs()
        self.worker = None
        self.chat_loader = None
        self.scheduler = None
        self.scheduler_stop = threading.Event()
        self.token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.new_mode = False
        self.pending_selected: list[str] = []
        self.build_ui()
        self.reload_jobs()

    def build_ui(self):
        central = QWidget(); root = QVBoxLayout(central)
        root.setContentsMargins(18, 18, 18, 18); root.setSpacing(12)
        self.setCentralWidget(central)

        header = QFrame(); header.setObjectName("header")
        h = QVBoxLayout(header); h.setContentsMargins(20, 14, 20, 14)
        t = QLabel("پشتیبان‌گیری تلگرام"); t.setObjectName("headerTitle")
        s = QLabel("مدیریت Job مستقل  •  Topic پایدار  •  History مرکزی"); s.setObjectName("headerSubtitle")
        h.addWidget(t); h.addWidget(s); root.addWidget(header)

        splitter = QSplitter(Qt.Orientation.Horizontal); splitter.setChildrenCollapsible(False); splitter.setHandleWidth(7)
        splitter.addWidget(self.jobs_panel()); splitter.addWidget(self.editor_panel()); splitter.setSizes([360, 880])
        root.addWidget(splitter, 1)

        status = QFrame(); status.setObjectName("status")
        sh = QHBoxLayout(status); sh.setContentsMargins(12, 7, 12, 7)
        self.status_label = QLabel("آماده")
        self.progress = QProgressBar(); self.progress.setRange(0, 100); self.progress.setTextVisible(False); self.progress.setMaximumWidth(420)
        sh.addWidget(self.status_label, 1); sh.addWidget(self.progress); root.addWidget(status)

    def jobs_panel(self):
        frame = QFrame(); frame.setObjectName("card"); layout = QVBoxLayout(frame); layout.setContentsMargins(15, 15, 15, 15)
        title = QLabel("Backup Jobs"); title.setObjectName("sectionTitle"); layout.addWidget(title)
        info = QLabel("حذف Job فقط Job را حذف می‌کند؛ Folder Identity، Topic و History باقی می‌مانند."); info.setWordWrap(True); info.setObjectName("muted"); layout.addWidget(info)
        self.jobs_list = QListWidget(); self.jobs_list.setAlternatingRowColors(True); self.jobs_list.currentRowChanged.connect(self.select_job); layout.addWidget(self.jobs_list, 1)
        top = QHBoxLayout(); new_btn = QPushButton("＋ Job جدید"); new_btn.setObjectName("primary"); pause = QPushButton("Pause / Resume"); pause.setObjectName("secondary")
        new_btn.clicked.connect(self.new_job); pause.clicked.connect(self.toggle_job); top.addWidget(new_btn, 1); top.addWidget(pause, 1); layout.addLayout(top)
        delete = QPushButton("حذف Job انتخاب‌شده"); delete.setObjectName("danger"); delete.clicked.connect(self.delete_job); layout.addWidget(delete)
        return frame

    def editor_panel(self):
        panel = QFrame(); panel.setObjectName("card"); root = QVBoxLayout(panel); root.setContentsMargins(18, 18, 18, 18); root.setSpacing(10)
        title = QLabel("تنظیمات Job"); title.setObjectName("sectionTitle"); root.addWidget(title)
        form = QFormLayout(); form.setHorizontalSpacing(14); form.setVerticalSpacing(9)
        self.name_edit = QLineEdit(); self.schedule_edit = QLineEdit("23:00"); self.folder_edit = QLineEdit(); self.folder_edit.setReadOnly(True); self.topic_edit = QLineEdit(); self.chat_edit = QLineEdit(); self.chat_edit.setPlaceholderText("مثلاً -1001234567890")
        folder_btn = QPushButton("انتخاب فولدر"); folder_btn.setObjectName("secondary"); folder_btn.clicked.connect(self.choose_folder)
        frow = QHBoxLayout(); frow.addWidget(self.folder_edit, 1); frow.addWidget(folder_btn)
        chats = QPushButton("بارگذاری چت‌ها"); chats.setObjectName("secondary"); chats.clicked.connect(self.load_chats)
        crow = QHBoxLayout(); crow.addWidget(self.chat_edit, 1); crow.addWidget(chats); self.load_chats_btn = chats
        form.addRow("نام Job", self.name_edit); form.addRow("زمان روزانه", self.schedule_edit); form.addRow("فولدر", frow); form.addRow("نام Topic", self.topic_edit); form.addRow("Chat ID", crow)
        root.addLayout(form)

        behavior = QFrame(); behavior.setObjectName("softCard"); bv = QVBoxLayout(behavior); bv.setContentsMargins(12, 9, 12, 9)
        lab = QLabel("رفتار Backup"); lab.setObjectName("subTitle"); bv.addWidget(lab)
        mr = QHBoxLayout(); self.all_radio = QRadioButton("کل فولدر"); self.sel_radio = QRadioButton("فایل‌های انتخابی"); self.all_radio.setChecked(True); mr.addWidget(self.all_radio); mr.addWidget(self.sel_radio); mr.addStretch(); bv.addLayout(mr)
        checks = QHBoxLayout(); self.replace = QCheckBox("جایگذاری نسخه قبلی"); self.history = QCheckBox("History مرکزی"); self.enabled = QCheckBox("فعال برای Scheduler")
        self.replace.setChecked(True); self.history.setChecked(True); self.enabled.setChecked(True)
        for w in (self.replace, self.history, self.enabled): checks.addWidget(w)
        checks.addStretch(); bv.addLayout(checks); root.addWidget(behavior)

        files = QFrame(); files.setObjectName("softCard"); fv = QVBoxLayout(files); fv.setContentsMargins(12, 9, 12, 9)
        row = QHBoxLayout(); self.file_summary = QLabel("ابتدا فولدر را انتخاب کنید"); self.file_summary.setObjectName("muted"); row.addWidget(self.file_summary, 1)
        manage = QPushButton("انتخاب / مدیریت فایل‌ها"); manage.setObjectName("secondary"); manage.clicked.connect(self.open_files)
        all_btn = QPushButton("همه"); all_btn.setObjectName("secondary"); all_btn.clicked.connect(lambda: self.set_all(True)); clear = QPushButton("پاک کردن"); clear.setObjectName("secondary"); clear.clicked.connect(lambda: self.set_all(False))
        row.addWidget(manage); row.addWidget(all_btn); row.addWidget(clear); fv.addLayout(row)
        self.preview = QPlainTextEdit(); self.preview.setReadOnly(True); self.preview.setMinimumHeight(170); fv.addWidget(self.preview); root.addWidget(files, 1)

        actions = QHBoxLayout(); actions.setSpacing(7)
        save = QPushButton("ذخیره تغییرات"); save.setObjectName("primary"); run = QPushButton("▶ اجرای همین Job"); run.setObjectName("run"); topic = QPushButton("ساخت / اتصال Topic"); topic.setObjectName("secondary"); stop = QPushButton("توقف"); stop.setObjectName("danger"); sched = QPushButton("شروع Scheduler"); sched.setObjectName("scheduler")
        save.clicked.connect(self.save); run.clicked.connect(self.start_backup); topic.clicked.connect(self.prepare_topic); stop.clicked.connect(self.stop_all); sched.clicked.connect(self.start_scheduler)
        actions.addWidget(save); actions.addWidget(run); actions.addWidget(topic); actions.addWidget(stop); actions.addStretch(); actions.addWidget(sched); root.addLayout(actions)
        return panel

    def reload_jobs(self, select=None):
        self.jobs_list.blockSignals(True); self.jobs_list.clear()
        for job in self.jobs:
            folder = Path(job.get("folder", "")).name or "بدون فولدر"; state = "فعال" if job.get("enabled", True) else "Pause"; mode = "انتخابی" if job.get("backup_mode") == "SELECTED" else "کل فولدر"
            item = QListWidgetItem(f"{job.get('name', folder)}\n{folder}   •   {state}   •   {job.get('schedule', '23:00')}   •   {mode}"); item.setToolTip(job.get("folder", "")); self.jobs_list.addItem(item)
        self.jobs_list.blockSignals(False)
        if self.jobs: self.jobs_list.setCurrentRow(0 if select is None else min(select, len(self.jobs)-1))
        else: self.new_job()

    def select_job(self, row):
        if row < 0 or row >= len(self.jobs): return
        job = self.jobs[row]; self.new_mode = False; self.pending_selected = []
        self.name_edit.setText(job.get("name", "")); self.folder_edit.setText(job.get("folder", "")); self.topic_edit.setText(job.get("main_topic_name", job.get("name", ""))); self.chat_edit.setText(str(job.get("chat_id", ""))); self.schedule_edit.setText(job.get("schedule", "23:00"))
        self.enabled.setChecked(job.get("enabled", True)); self.replace.setChecked(job.get("replace_files", True)); self.history.setChecked(job.get("history_enabled", True)); self.sel_radio.setChecked(job.get("backup_mode", "ALL") == "SELECTED"); self.all_radio.setChecked(not self.sel_radio.isChecked()); self.update_files(); self.status_label.setText(f"Job انتخاب شد: {job.get('name', 'Job')}")

    def current(self):
        row = self.jobs_list.currentRow(); return self.jobs[row] if 0 <= row < len(self.jobs) else None

    def new_job(self):
        self.jobs_list.clearSelection(); self.new_mode = True; self.pending_selected = []
        self.name_edit.clear(); self.folder_edit.clear(); self.topic_edit.clear(); self.schedule_edit.setText("23:00"); self.all_radio.setChecked(True); self.replace.setChecked(True); self.history.setChecked(True); self.enabled.setChecked(True); self.file_summary.setText("ابتدا فولدر را انتخاب کنید"); self.preview.clear(); self.status_label.setText("Job جدید آماده است")

    def choose_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "انتخاب فولدر پشتیبان")
        if not folder: return
        path = str(Path(folder).resolve()); current = self.current(); current_path = str(Path(current.get("folder", "")).resolve()) if current else ""
        if current and not self.new_mode and path != current_path:
            box = QMessageBox(self); box.setIcon(QMessageBox.Icon.Question); box.setWindowTitle("تغییر فولدر"); box.setText("این Job از قبل وجود دارد."); box.setInformativeText("فولدر جدید را برای همین Job ثبت کنم یا یک Job جدید بسازم؟")
            edit = box.addButton("ویرایش همین Job", QMessageBox.ButtonRole.AcceptRole); new = box.addButton("ساخت Job جدید", QMessageBox.ButtonRole.ActionRole); box.addButton("لغو", QMessageBox.ButtonRole.RejectRole); box.exec()
            clicked = box.clickedButton()
            if clicked is new: self.new_job()
            elif clicked is not edit: return
        self.folder_edit.setText(path)
        if not self.name_edit.text().strip(): self.name_edit.setText(Path(path).name)
        if not self.topic_edit.text().strip(): self.topic_edit.setText(Path(path).name)
        self.update_files()

    def selected_files(self):
        if self.new_mode: return set(self.pending_selected)
        job = self.current(); return {str(Path(p).resolve()) for p in (job or {}).get("selected_files", [])}

    def update_files(self):
        folder = self.folder_edit.text().strip()
        if not folder or not Path(folder).is_dir(): self.file_summary.setText("فولدر معتبر نیست"); self.preview.clear(); return
        files = current_files(folder, STATE_FILES); selected = self.selected_files(); chosen = sum(1 for p in files if str(p.resolve()) in selected)
        self.file_summary.setText(f"{chosen} فایل از {len(files)} فایل انتخاب شده" if self.sel_radio.isChecked() else f"کل فولدر • {len(files)} فایل موجود")
        lines = [("✓  " if str(p.resolve()) in selected else "·  ") + str(p.relative_to(Path(folder))) for p in files[:500]]; self.preview.setPlainText("\n".join(lines))

    def open_files(self):
        folder = self.folder_edit.text().strip()
        if not folder or not Path(folder).is_dir(): QMessageBox.warning(self, "انتخاب فایل", "ابتدا یک فولدر معتبر انتخاب کنید."); return
        files = current_files(folder, STATE_FILES)
        if not files: QMessageBox.information(self, "انتخاب فایل", "در این فولدر فایلی پیدا نشد."); return
        dialog = FileDialog(folder, files, self.selected_files(), self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            if self.new_mode: self.pending_selected = sorted(dialog.selected)
            elif self.current(): self.current()["selected_files"] = sorted(dialog.selected)
            self.update_files()

    def set_all(self, value):
        folder = self.folder_edit.text().strip()
        if not folder or not Path(folder).is_dir(): return
        selected = [str(p.resolve()) for p in current_files(folder, STATE_FILES)] if value else []
        if self.new_mode: self.pending_selected = selected
        elif self.current(): self.current()["selected_files"] = selected
        self.update_files()

    def validate(self):
        folder = self.folder_edit.text().strip(); token = self.token.strip(); chat = self.chat_edit.text().strip()
        if not folder or not Path(folder).is_dir(): raise ValueError("فولدر معتبر نیست.")
        if not token: raise ValueError("TELEGRAM_BOT_TOKEN در .env تنظیم نشده است.")
        if not chat: raise ValueError("Chat ID مقصد را وارد کنید یا از بارگذاری چت‌ها انتخاب کنید.")
        datetime.strptime(self.schedule_edit.text().strip(), "%H:%M")
        return token, folder, chat

    def save(self):
        try: _, folder, chat = self.validate()
        except ValueError as exc: QMessageBox.critical(self, "تنظیمات ناقص", str(exc)); return False
        row = self.jobs_list.currentRow()
        if self.new_mode or row < 0:
            job = new_job(folder, chat, self.schedule_edit.text().strip()); self.jobs.append(job); row = len(self.jobs)-1
        else: job = self.jobs[row]
        rec = get_or_create_folder(folder)
        job.update({"name": self.name_edit.text().strip() or Path(folder).name, "folder": folder, "folder_id": rec["id"], "chat_id": chat, "destination": "topic", "main_topic_name": self.topic_edit.text().strip() or Path(folder).name, "schedule": self.schedule_edit.text().strip(), "enabled": self.enabled.isChecked(), "backup_mode": "SELECTED" if self.sel_radio.isChecked() else "ALL", "selected_files": sorted(self.selected_files()), "replace_files": self.replace.isChecked(), "history_enabled": self.history.isChecked()})
        if rec.get("topic_id"): job["main_topic_id"] = rec["topic_id"]
        save_jobs(self.jobs); self.new_mode = False; self.pending_selected = []; self.reload_jobs(row); self.status_label.setText("تغییرات Job ذخیره شد"); return True

    def toggle_job(self):
        row = self.jobs_list.currentRow()
        if row < 0: QMessageBox.information(self, "Job", "ابتدا یک Job را انتخاب کنید."); return
        self.jobs[row]["enabled"] = not self.jobs[row].get("enabled", True); save_jobs(self.jobs); self.reload_jobs(row); self.status_label.setText("Job فعال شد" if self.jobs[row]["enabled"] else "Job روی Pause قرار گرفت")

    def delete_job(self):
        row = self.jobs_list.currentRow()
        if row < 0: QMessageBox.information(self, "حذف Job", "ابتدا یک Job را انتخاب کنید."); return
        job = self.jobs[row]; reply = QMessageBox.question(self, "حذف Job", f"Job «{job.get('name','Job')}» حذف شود؟\n\nFolder Identity، Topic و History حذف نمی‌شوند.", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes: del self.jobs[row]; save_jobs(self.jobs); self.reload_jobs(); self.status_label.setText("Job حذف شد؛ Folder Identity و Topic حفظ شدند")

    def load_chats(self):
        token = self.token.strip()
        if not token: QMessageBox.critical(self, "Telegram", "TELEGRAM_BOT_TOKEN در .env تنظیم نشده است."); return
        self.load_chats_btn.setEnabled(False); self.status_label.setText("در حال دریافت چت‌ها..."); self.chat_loader = ChatLoader(token); self.chat_loader.loaded.connect(self.apply_chats); self.chat_loader.failed.connect(lambda e: QMessageBox.critical(self, "Telegram", e)); self.chat_loader.finished.connect(lambda: self.load_chats_btn.setEnabled(True)); self.chat_loader.start()

    def apply_chats(self, chats):
        self.load_chats_btn.setEnabled(True)
        if not chats: QMessageBox.information(self, "Telegram", "چتی از getUpdates پیدا نشد."); return
        dialog = ChatDialog(chats, self.chat_edit.text().strip(), self)
        if dialog.exec() == QDialog.DialogCode.Accepted: self.chat_edit.setText(dialog.value); self.status_label.setText(f"چت انتخاب شد: {chats[dialog.value]}")
        else: self.status_label.setText(f"{len(chats)} چت پیدا شد")

    def prepare_topic(self):
        if not self.save(): return
        job = self.current(); token = self.token.strip()
        try:
            forum = TelegramForum(telegram_request); mid, rec = _ensure_folder_topic(forum, token, job); job["main_topic_id"] = mid; job["folder_id"] = rec["id"]
            if job.get("history_enabled", True): job["history_topic_id"] = _ensure_project_history_topic(forum, token, job["chat_id"])
            save_jobs(self.jobs); QMessageBox.information(self, "Telegram", f"Topic آماده شد.\nTopic ID: {mid}")
        except Exception as exc: QMessageBox.critical(self, "Telegram", str(exc))

    def start_backup(self):
        if self.worker and self.worker.isRunning(): QMessageBox.information(self, "Backup", "یک عملیات دیگر در حال اجراست."); return
        if not self.save(): return
        job = self.current(); self.progress.setValue(0); self.worker = Worker(self.token.strip(), dict(job)); self.worker.progress.connect(self.progress.setValue); self.worker.log.connect(self.status_label.setText); self.worker.done.connect(self.backup_done); self.worker.failed.connect(lambda e: QMessageBox.critical(self, "Backup", e)); self.worker.start(); self.status_label.setText(f"در حال Backup: {job.get('name','Job')}")

    def backup_done(self, data):
        (count, completed), job = data
        for cur in self.jobs:
            if cur.get("id") == job.get("id"): cur.update(job)
        save_jobs(self.jobs); self.status_label.setText(f"Backup {'کامل شد' if completed else 'متوقف شد'} • {count} فایل"); self.progress.setValue(100 if completed else self.progress.value())

    def stop_all(self):
        if self.worker and self.worker.isRunning(): self.worker.cancel()
        self.scheduler_stop.set(); self.status_label.setText("Scheduler / عملیات متوقف شد")

    def start_scheduler(self):
        if self.scheduler and self.scheduler.is_alive(): self.status_label.setText("Scheduler از قبل فعال است"); return
        self.scheduler_stop.clear(); self.scheduler = threading.Thread(target=self.scheduler_loop, daemon=True); self.scheduler.start(); self.status_label.setText("Scheduler فعال است؛ فقط Jobهای Active اجرا می‌شوند")

    def scheduler_loop(self):
        last = {}
        while not self.scheduler_stop.is_set():
            now = datetime.now()
            for job in load_and_migrate_jobs():
                if not job.get("enabled", True) or job.get("schedule") != now.strftime("%H:%M") or last.get(job["id"]) == now.date(): continue
                last[job["id"]] = now.date()
                if self.worker and self.worker.isRunning(): continue
                self.worker = Worker(self.token.strip(), dict(job)); self.worker.progress.connect(self.progress.setValue); self.worker.log.connect(self.status_label.setText); self.worker.done.connect(self.backup_done); self.worker.failed.connect(lambda e: self.status_label.setText(f"Scheduler: {e}")); self.worker.start()
            self.scheduler_stop.wait(10)

    def closeEvent(self, event):
        self.stop_all()
        if self.worker and self.worker.isRunning(): self.worker.wait(1000)
        if self.chat_loader and self.chat_loader.isRunning(): self.chat_loader.wait(1000)
        event.accept()


def apply_style(app):
    app.setStyleSheet("""
        QWidget { font-family: 'Segoe UI'; font-size: 10pt; color: #263746; }
        QMainWindow { background: #edf2f6; }
        QFrame#header { background: #173b5b; border-radius: 12px; }
        QLabel#headerTitle { color: white; font-size: 20pt; font-weight: 700; }
        QLabel#headerSubtitle { color: #d2e2ee; }
        QFrame#card { background: #fff; border: 1px solid #d4dee7; border-radius: 12px; }
        QFrame#softCard, QFrame#status { background: #f6f9fb; border: 1px solid #dbe4eb; border-radius: 9px; }
        QFrame#status { background: #fff; }
        QLabel#sectionTitle { color: #173b5b; font-size: 13.5pt; font-weight: 700; }
        QLabel#subTitle { color: #173b5b; font-weight: 700; }
        QLabel#muted { color: #65798a; }
        QLabel#dialogTitle { color: #173b5b; font-size: 12.5pt; font-weight: 700; padding: 4px; }
        QLineEdit, QPlainTextEdit, QListWidget { border: 1px solid #c8d4df; border-radius: 7px; background: #fff; padding: 7px; selection-background-color: #dcecf8; }
        QLineEdit { min-height: 30px; }
        QCheckBox { spacing: 8px; min-height: 30px; font-weight: 500; }
        QCheckBox::indicator { width: 20px; height: 20px; border: 2px solid #9aaebe; border-radius: 5px; background: #fff; }
        QCheckBox::indicator:checked { background: #2f7ea8; border-color: #2f7ea8; }
        QCheckBox::indicator:checked:hover { background: #256d92; border-color: #256d92; }
        QRadioButton { spacing: 7px; min-height: 28px; }
        QPushButton { min-height: 36px; padding: 0 14px; border: 1px solid #c7d2dc; border-radius: 7px; background: #fff; font-weight: 600; }
        QPushButton:hover { background: #f0f4f7; border-color: #9db0bf; }
        QPushButton#primary { background: #2f7ea8; color: #fff; border-color: #2f7ea8; font-weight: 700; }
        QPushButton#run { background: #31845d; color: #fff; border-color: #31845d; font-weight: 700; }
        QPushButton#scheduler { background: #7658a8; color: #fff; border-color: #7658a8; font-weight: 700; }
        QPushButton#secondary { background: #f2f6f9; }
        QPushButton#danger { color: #a82d2d; background: #fff8f8; border-color: #e1b7b7; }
        QPushButton#danger:hover { background: #ffecec; }
        QListWidget::item { padding: 8px; }
        QListWidget::item:selected { background: #e7f1f8; color: #173b5b; }
        QProgressBar { border: 1px solid #cad5df; border-radius: 5px; background: #f3f6f8; height: 11px; }
        QProgressBar::chunk { background: #2f7ea8; border-radius: 5px; }
        QDialogButtonBox QPushButton { min-width: 95px; }
    """)


if __name__ == "__main__":
    app = QApplication.instance() or QApplication([])
    app.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
    apply_style(app)
    win = BackupApp(); win.show(); app.exec()
