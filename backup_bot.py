from __future__ import annotations

import json
import os
import threading
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import requests

from backup_jobs import (
    FOLDERS_FILE,
    JOBS_FILE,
    MANIFEST_FILE,
    PROJECT_FILE,
    current_files,
    file_key,
    get_or_create_folder,
    load_and_migrate_jobs,
    load_folders,
    load_manifest,
    load_project,
    new_job,
    pending_files,
    save_jobs,
    save_manifest,
    save_project,
    update_folder,
)
from telegram_forum import TelegramForum


BASE_DIR = Path(__file__).resolve().parent
ENV_FILE = BASE_DIR / ".env"
LOG_FILE = BASE_DIR / "last_backup.json"
HISTORY_FILE = BASE_DIR / "backup_history.json"
STATE_FILES = {LOG_FILE, HISTORY_FILE, JOBS_FILE, MANIFEST_FILE, FOLDERS_FILE, PROJECT_FILE, ENV_FILE}
TELEGRAM_TIMEOUT = 30
TELEGRAM_RETRIES = 3
BACKUP_LOCK = threading.Lock()


def load_env():
    if not ENV_FILE.exists():
        return
    for raw_line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"\''))


def _atomic_write(path: Path, text: str):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def save_backup_time():
    _atomic_write(LOG_FILE, json.dumps({"last_backup": datetime.now().isoformat()}))


def load_history():
    if not HISTORY_FILE.exists():
        return []
    try:
        value = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        return value if isinstance(value, list) else []
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return []


def save_history(history):
    _atomic_write(HISTORY_FILE, json.dumps(history[-5000:], ensure_ascii=False, indent=2))


def _is_state_file(path: Path) -> bool:
    try:
        return path.resolve() in {item.resolve() for item in STATE_FILES}
    except OSError:
        return False


def get_changed_files(folder, since):
    changed = []
    for root, _, files in os.walk(folder):
        for name in files:
            path = Path(root) / name
            if _is_state_file(path):
                continue
            try:
                if datetime.fromtimestamp(path.stat().st_mtime) > since:
                    changed.append(path)
            except OSError:
                continue
    return changed


def get_pending_files(folder):
    return pending_files(folder, {}, STATE_FILES)


def telegram_request(token, method, **kwargs):
    """Call Telegram with bounded retries for transient failures only."""
    url = f"https://api.telegram.org/bot{token}/{method}"
    last_error = None
    for attempt in range(TELEGRAM_RETRIES):
        try:
            response = requests.post(url, timeout=TELEGRAM_TIMEOUT, **kwargs)
            if response.status_code == 429:
                retry_after = 1
                try:
                    retry_after = int(response.json().get("parameters", {}).get("retry_after", 1))
                except (TypeError, ValueError, json.JSONDecodeError):
                    pass
                if attempt < TELEGRAM_RETRIES - 1:
                    time.sleep(max(1, min(retry_after, 60)))
                    continue
            if 500 <= response.status_code < 600 and attempt < TELEGRAM_RETRIES - 1:
                time.sleep(2 ** attempt)
                continue
            response.raise_for_status()
            payload = response.json()
            if not payload.get("ok"):
                raise RuntimeError(payload.get("description", "Telegram API error"))
            return payload["result"]
        except requests.HTTPError:
            raise
        except requests.RequestException as error:
            last_error = error
            if attempt < TELEGRAM_RETRIES - 1:
                time.sleep(2 ** attempt)
                continue
            raise
    raise last_error or RuntimeError("Telegram request failed")


def _ensure_project_history_topic(forum, token, chat_id):
    project = load_project()
    if project.get("history_chat_id") == str(chat_id) and project.get("history_topic_id"):
        return int(project["history_topic_id"])
    chat = forum.get_chat(token, chat_id)
    if not chat.get("is_forum"):
        raise RuntimeError("چت مقصد Forum نیست. برای استفاده از Topic باید Topics گروه فعال باشد.")
    topic_id = forum.create_topic(token, chat_id, project.get("history_topic_name") or "Backup History")
    project.update({
        "history_chat_id": str(chat_id),
        "history_topic_id": topic_id,
        "history_topic_name": project.get("history_topic_name") or "Backup History",
        "created_at": datetime.now().isoformat(timespec="seconds"),
    })
    save_project(project)
    return topic_id


def _ensure_folder_topic(forum, token, job):
    folder = get_or_create_folder(job["folder"])
    topic_id = folder.get("topic_id")
    if topic_id:
        return int(topic_id), folder
    topic_id = forum.prepare_topic(token, job["chat_id"], job.get("main_topic_name") or folder.get("topic_name") or job["name"])
    folder["topic_id"] = topic_id
    folder["topic_name"] = job.get("main_topic_name") or folder.get("topic_name") or job["name"]
    update_folder(folder)
    return topic_id, folder


def _records_for_folder(manifest, folder_record, legacy_job_id=None):
    folder_id = folder_record["id"]
    records = manifest.setdefault("folders", {}).setdefault(folder_id, {})
    if not records and legacy_job_id and isinstance(manifest.get(legacy_job_id), dict):
        records.update(manifest[legacy_job_id])
    return records


def _archive_current_version(forum, token, job, record, history_id, log):
    message_id = record.get("message_id")
    if not history_id or not message_id:
        return
    relative = record.get("relative_path", record.get("path", ""))
    version = record.get("version", 1)
    forum.send_text(
        token,
        job["chat_id"],
        f"📦 نسخه قبلی\nفولدر: {job.get('name', '')}\nفایل: {relative}\nنسخه: {version}\nزمان: {record.get('sent_at', '')}",
        int(history_id),
    )
    forum.copy_message(token, job["chat_id"], int(message_id), int(history_id))
    log(f"نسخه قبلی به History منتقل شد: {relative} | v{version}")


def run_job_backup(token, job, log, progress=None, cancel_event=None, selected_files=None):
    if not BACKUP_LOCK.acquire(blocking=False):
        raise RuntimeError("یک عملیات پشتیبان‌گیری دیگر در حال اجراست.")
    try:
        folder = Path(job["folder"])
        if not folder.is_dir():
            raise ValueError(f"فولدر معتبر نیست: {folder}")

        forum = TelegramForum(telegram_request)
        manifest = load_manifest()
        folder_record = get_or_create_folder(str(folder))
        records = _records_for_folder(manifest, folder_record, job.get("id"))
        destination_thread = None
        history_id = None

        if job.get("destination", "topic") == "topic":
            destination_thread, folder_record = _ensure_folder_topic(forum, token, job)
            if job.get("history_enabled", True):
                history_id = _ensure_project_history_topic(forum, token, job["chat_id"])
            job["main_topic_id"] = destination_thread
            job["history_topic_id"] = history_id
            job["folder_id"] = folder_record["id"]
        else:
            job["main_topic_id"] = None
            job["history_topic_id"] = None

        excluded = STATE_FILES
        all_current = current_files(str(folder), excluded)
        if selected_files is None:
            if job.get("backup_mode") == "SELECTED":
                selected_set = {str(Path(p).resolve()) for p in job.get("selected_files", [])}
                paths = [p for p in all_current if str(p.resolve()) in selected_set]
                paths = [p for p in paths if pending_files(str(folder), records, excluded) and p in pending_files(str(folder), records, excluded)]
            else:
                paths = pending_files(str(folder), records, excluded)
        else:
            paths = [Path(p) for p in selected_files]
        total = len(paths)
        uploaded = 0

        for index, path in enumerate(paths, start=1):
            if cancel_event and cancel_event.is_set():
                log("ارسال توسط کاربر متوقف شد")
                return uploaded, False
            key = file_key(path)
            old = records.get(key)
            try:
                stat = path.stat()
                modified = stat.st_mtime_ns
                size = stat.st_size
            except OSError:
                log(f"فایل در دسترس نیست و رد شد: {path}")
                continue
            if old and old.get("message_id"):
                if job.get("history_enabled", True) and history_id:
                    _archive_current_version(forum, token, job, old, history_id, log)
                if job.get("replace_files", True):
                    try:
                        forum.delete_message(token, job["chat_id"], int(old["message_id"]))
                    except Exception as error:
                        log(f"حذف نسخه قبلی ممکن نشد: {error}")
            message = forum.send_document(token, job["chat_id"], path, destination_thread)
            records[key] = {
                "path": str(path),
                "relative_path": str(path.relative_to(folder)),
                "modified": modified,
                "size": size,
                "message_id": int(message["message_id"]),
                "version": int(old.get("version", 0)) + 1 if old else 1,
                "sent_at": datetime.now().isoformat(timespec="seconds"),
                "deleted": False,
            }
            save_manifest(manifest)
            uploaded += 1
            log(f"ارسال شد: {path} | نسخه {records[key]['version']}")
            if progress:
                progress(index, total)

        existing_keys = {file_key(path) for path in all_current}
        for key, record in list(records.items()):
            if record.get("deleted") or key in existing_keys or not record.get("message_id"):
                continue
            if job.get("history_enabled", True) and history_id:
                _archive_current_version(forum, token, job, record, history_id, log)
                forum.send_text(
                    token,
                    job["chat_id"],
                    f"🗑 فایل حذف شد\nفایل: {record.get('relative_path', record.get('path', ''))}\nنسخه {record.get('version', 1)} محفوظ است.",
                    int(history_id),
                )
            if job.get("replace_files", True):
                try:
                    forum.delete_message(token, job["chat_id"], int(record["message_id"]))
                except Exception as error:
                    log(f"پیام فایل حذف‌شده پاک نشد: {error}")
            record["deleted"] = True
            record["deleted_at"] = datetime.now().isoformat(timespec="seconds")
        save_manifest(manifest)
        save_backup_time()
        return uploaded, True
    finally:
        BACKUP_LOCK.release()


def backup_changed_files(token, chat_id, folder, log, progress=None, cancel_event=None, selected_files=None):
    """Backward-compatible single-folder backup used by the original API/tests."""
    job = new_job(folder, chat_id)
    return run_job_backup(token, job, log, progress, cancel_event, selected_files)


class BackupApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Telegram Folder Backup")
        self.root.geometry("1180x820")
        self.stop_event = threading.Event()
        self.cancel_event = threading.Event()
        self.worker = None
        self.jobs = load_and_migrate_jobs()
        self.manifest = load_manifest()
        load_env()
        self.token = tk.StringVar(value=os.getenv("TELEGRAM_BOT_TOKEN", ""))
        self.status = tk.StringVar(value="آماده")
        self.destination = tk.StringVar(value="topic")
        self.job_name = tk.StringVar()
        self.folder = tk.StringVar()
        self.chat_id = tk.StringVar(value=os.getenv("TELEGRAM_CHAT_ID", ""))
        self.main_topic = tk.StringVar()
        self.schedule = tk.StringVar(value="23:00")
        self.enabled = tk.BooleanVar(value=True)
        self.replace_files = tk.BooleanVar(value=True)
        self.history_enabled = tk.BooleanVar(value=True)
        self.backup_mode = tk.StringVar(value="ALL")
        self.progress_value = tk.DoubleVar(value=0)
        self.file_count = tk.StringVar(value="0 فایل")
        self.chats = {}
        self.file_vars = {}
        self.build_ui()
        self._migrate_legacy_config()
        self.refresh_jobs()

    def build_ui(self):
        self.root.configure(bg="#eef2f5")
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure("TFrame", background="#eef2f5")
        style.configure("Card.TFrame", background="#ffffff")
        style.configure("Section.TLabel", background="#ffffff", foreground="#18324b", font=("Segoe UI", 10, "bold"))
        style.configure("Field.TLabel", background="#ffffff", foreground="#627384", font=("Segoe UI", 9))
        style.configure("TButton", padding=(9, 5), font=("Segoe UI", 9))
        style.configure("Accent.TButton", background="#16805f", foreground="#ffffff")

        frame = ttk.Frame(self.root, padding=12)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(2, weight=1)

        header = tk.Frame(frame, bg="#18324b", padx=16, pady=10)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        tk.Label(header, text="پشتیبان‌گیری حرفه‌ای تلگرام", bg="#18324b", fg="white", font=("Segoe UI", 14, "bold")).pack(anchor="w")
        tk.Label(header, text="Job مستقل • Topic پایدار • History مرکزی", bg="#18324b", fg="#d9e5ef", font=("Segoe UI", 9)).pack(anchor="w", pady=(3, 0))

        connection = ttk.Frame(frame, style="Card.TFrame", padding=10)
        connection.grid(row=1, column=0, sticky="ew")
        connection.columnconfigure(1, weight=1)
        ttk.Label(connection, text="توکن ربات", style="Field.TLabel").grid(row=0, column=0, padx=(0, 8))
        ttk.Entry(connection, textvariable=self.token, show="*").grid(row=0, column=1, sticky="ew")
        ttk.Button(connection, text="تست اتصال", command=self.test_connection).grid(row=0, column=2, padx=6)
        ttk.Label(connection, text="چت مقصد", style="Field.TLabel").grid(row=1, column=0, pady=(7, 0))
        self.chat_combo = ttk.Combobox(connection, textvariable=self.chat_id, state="normal")
        self.chat_combo.grid(row=1, column=1, sticky="ew", pady=(7, 0))
        self.chat_combo.bind("<<ComboboxSelected>>", self.select_chat)
        ttk.Button(connection, text="بارگذاری چت‌ها", command=self.load_chats).grid(row=1, column=2, padx=6, pady=(7, 0))

        body = ttk.Frame(frame)
        body.grid(row=2, column=0, sticky="nsew", pady=8)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)

        jobs_card = ttk.Frame(body, style="Card.TFrame", padding=8)
        jobs_card.grid(row=0, column=0, sticky="ns", padx=(0, 8))
        ttk.Label(jobs_card, text="Backup Jobs", style="Section.TLabel").pack(anchor="w")
        self.jobs_list = tk.Listbox(jobs_card, width=28, height=22, activestyle="none")
        self.jobs_list.pack(fill="y", expand=True, pady=7)
        self.jobs_list.bind("<<ListboxSelect>>", self.select_job)
        ttk.Button(jobs_card, text="＋ پوشه جدید", command=self.new_job_ui).pack(fill="x")
        ttk.Button(jobs_card, text="تغییر وضعیت Pause/Resume", command=self.toggle_job).pack(fill="x", pady=(5, 0))
        ttk.Button(jobs_card, text="حذف Job", command=self.delete_job).pack(fill="x", pady=(5, 0))

        editor = ttk.Frame(body, style="Card.TFrame", padding=10)
        editor.grid(row=0, column=1, sticky="nsew")
        editor.columnconfigure(1, weight=1)
        fields = [("نام Job", self.job_name), ("فولدر", self.folder), ("چت", self.chat_id), ("نام Topic اصلی", self.main_topic), ("زمان روزانه", self.schedule)]
        for row, (label, variable) in enumerate(fields):
            ttk.Label(editor, text=label, style="Field.TLabel").grid(row=row, column=0, sticky="w", pady=4)
            ttk.Entry(editor, textvariable=variable).grid(row=row, column=1, sticky="ew", pady=4)
            if label == "فولدر":
                ttk.Button(editor, text="انتخاب", command=self.choose_folder).grid(row=row, column=2, padx=5)
        ttk.Label(editor, text="مقصد", style="Field.TLabel").grid(row=5, column=0, sticky="w", pady=4)
        ttk.Combobox(editor, textvariable=self.destination, values=("topic", "general"), state="readonly", width=12).grid(row=5, column=1, sticky="w", pady=4)
        ttk.Checkbutton(editor, text="فعال برای زمان‌بندی", variable=self.enabled).grid(row=6, column=1, sticky="w", pady=4)

        options = ttk.Frame(editor, style="Card.TFrame")
        options.grid(row=7, column=0, columnspan=3, sticky="ew", pady=(8, 4))
        ttk.Label(options, text="حالت Backup", style="Field.TLabel").pack(side="left", padx=(0, 8))
        ttk.Radiobutton(options, text="کل فولدر", value="ALL", variable=self.backup_mode, command=self.refresh_file_list).pack(side="left")
        ttk.Radiobutton(options, text="فایل‌های انتخابی", value="SELECTED", variable=self.backup_mode, command=self.refresh_file_list).pack(side="left", padx=10)
        ttk.Checkbutton(options, text="جایگذاری فایل قبلی", variable=self.replace_files).pack(side="left", padx=10)
        ttk.Checkbutton(options, text="History مرکزی", variable=self.history_enabled).pack(side="left")

        files_card = ttk.Frame(editor, style="Card.TFrame")
        files_card.grid(row=8, column=0, columnspan=3, sticky="nsew", pady=6)
        editor.rowconfigure(8, weight=1)
        ttk.Label(files_card, text="فهرست فایل‌های قابل انتخاب", style="Section.TLabel").pack(anchor="w")
        self.file_canvas = tk.Canvas(files_card, height=210, bg="#fbfcfd", highlightthickness=0)
        self.file_scroll = ttk.Scrollbar(files_card, orient="vertical", command=self.file_canvas.yview)
        self.file_inner = ttk.Frame(self.file_canvas, style="Card.TFrame")
        self.file_inner.bind("<Configure>", lambda e: self.file_canvas.configure(scrollregion=self.file_canvas.bbox("all")))
        self.file_canvas.create_window((0, 0), window=self.file_inner, anchor="nw")
        self.file_canvas.configure(yscrollcommand=self.file_scroll.set)
        self.file_canvas.pack(side="left", fill="both", expand=True)
        self.file_scroll.pack(side="right", fill="y")

        actions = ttk.Frame(editor, style="Card.TFrame")
        actions.grid(row=9, column=0, columnspan=3, sticky="ew", pady=(7, 0))
        ttk.Button(actions, text="ذخیره Job", command=self.save_current_job).pack(side="left")
        ttk.Button(actions, text="ساخت/اتصال Topic", command=self.prepare_topics_ui).pack(side="left", padx=5)
        ttk.Button(actions, text="ارسال الآن", style="Accent.TButton", command=self.start_backup).pack(side="left")
        ttk.Button(actions, text="لغو عملیات", command=self.stop_scheduler).pack(side="left", padx=5)

        activity = ttk.Frame(frame, style="Card.TFrame", padding=10)
        activity.grid(row=3, column=0, sticky="ew")
        activity.columnconfigure(1, weight=1)
        ttk.Label(activity, text="وضعیت", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(activity, textvariable=self.status).grid(row=0, column=1, sticky="e")
        ttk.Label(activity, textvariable=self.file_count).grid(row=1, column=0, sticky="w", pady=5)
        ttk.Progressbar(activity, variable=self.progress_value, maximum=100).grid(row=1, column=1, sticky="ew", pady=5)
        ttk.Label(activity, text="گزارش فعالیت", style="Section.TLabel").grid(row=2, column=0, columnspan=2, sticky="w", pady=(5, 3))
        self.log = tk.Text(activity, height=7, state="disabled", wrap="word", bg="#fbfcfd", relief="flat")
        self.log.grid(row=3, column=0, columnspan=2, sticky="ew")
        ttk.Button(activity, text="شروع زمان‌بندی همه Jobهای فعال", command=self.start_scheduler).grid(row=4, column=0, sticky="w", pady=7)

    def _migrate_legacy_config(self):
        if self.jobs:
            return
        folder = os.getenv("BACKUP_FOLDER", "").strip()
        chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
        if folder and chat_id and Path(folder).is_dir():
            self.jobs = [new_job(folder, chat_id, os.getenv("SCHEDULE_TIME", "23:00"))]
            save_jobs(self.jobs)
            self.write_log("تنظیمات نسخه قبلی به یک Backup Job منتقل شد")

    def refresh_jobs(self, select_index=None):
        self.jobs_list.delete(0, "end")
        for job in self.jobs:
            marker = "▶" if job.get("enabled", True) else "Ⅱ"
            mode = "انتخابی" if job.get("backup_mode") == "SELECTED" else "کل فولدر"
            self.jobs_list.insert("end", f"{marker} {job.get('name', 'Job')}  [{mode}]")
        if self.jobs:
            index = 0 if select_index is None else min(select_index, len(self.jobs) - 1)
            self.jobs_list.selection_set(index)
            self.jobs_list.activate(index)
            self.select_job()
        else:
            self.new_job_ui()

    def new_job_ui(self):
        self.job_name.set("")
        self.folder.set("")
        self.main_topic.set("")
        self.schedule.set("23:00")
        self.destination.set("topic")
        self.enabled.set(True)
        self.replace_files.set(True)
        self.history_enabled.set(True)
        self.backup_mode.set("ALL")
        self.file_vars = {}
        for child in self.file_inner.winfo_children():
            child.destroy()
        self.jobs_list.selection_clear(0, "end")
        self.status.set("Job جدید آماده است")

    def select_job(self, _event=None):
        index = self._selected_index()
        if index is None or index >= len(self.jobs):
            return
        job = self.jobs[index]
        self.job_name.set(job.get("name", ""))
        self.folder.set(job.get("folder", ""))
        self.chat_id.set(str(job.get("chat_id", self.chat_id.get())))
        self.destination.set(job.get("destination", "topic"))
        self.main_topic.set(job.get("main_topic_name", job.get("name", "")))
        self.schedule.set(job.get("schedule", "23:00"))
        self.enabled.set(job.get("enabled", True))
        self.replace_files.set(job.get("replace_files", True))
        self.history_enabled.set(job.get("history_enabled", True))
        self.backup_mode.set(job.get("backup_mode", "ALL"))
        self.refresh_file_list()
        self.refresh_pending_count()

    def _selected_index(self):
        selection = self.jobs_list.curselection()
        return selection[0] if selection else None

    def _job_for_current_form(self):
        index = self._selected_index()
        return self.jobs[index] if index is not None and index < len(self.jobs) else None

    def save_current_job(self):
        folder = self.folder.get().strip()
        chat_id = self.chat_id.get().strip()
        if not folder or not Path(folder).is_dir():
            messagebox.showerror("خطا", "فولدر معتبر نیست.")
            return False
        if not chat_id or not self.token.get().strip():
            messagebox.showerror("خطا", "توکن و چت مقصد را وارد کنید.")
            return False
        try:
            datetime.strptime(self.schedule.get().strip(), "%H:%M")
        except ValueError:
            messagebox.showerror("خطا", "زمان باید با قالب HH:MM باشد.")
            return False
        selected = [path for path, var in self.file_vars.items() if var.get()]
        index = self._selected_index()
        if index is None:
            job = new_job(folder, chat_id, self.schedule.get().strip())
            self.jobs.append(job)
            index = len(self.jobs) - 1
        else:
            job = self.jobs[index]
        folder_record = get_or_create_folder(folder)
        job.update({
            "name": self.job_name.get().strip() or Path(folder).name,
            "folder": str(Path(folder).expanduser().resolve()),
            "folder_id": folder_record["id"],
            "chat_id": chat_id,
            "destination": self.destination.get(),
            "main_topic_name": self.main_topic.get().strip() or Path(folder).name,
            "schedule": self.schedule.get().strip(),
            "enabled": self.enabled.get(),
            "backup_mode": self.backup_mode.get(),
            "selected_files": selected,
            "replace_files": self.replace_files.get(),
            "history_enabled": self.history_enabled.get(),
        })
        if folder_record.get("topic_id"):
            job["main_topic_id"] = folder_record["topic_id"]
        save_jobs(self.jobs)
        self.refresh_jobs(index)
        self.status.set("Job ذخیره شد")
        return True

    def toggle_job(self):
        index = self._selected_index()
        if index is None:
            return
        self.jobs[index]["enabled"] = not self.jobs[index].get("enabled", True)
        save_jobs(self.jobs)
        state = "فعال" if self.jobs[index]["enabled"] else "Paused"
        self.write_log(f"Job {self.jobs[index].get('name')} اکنون {state} است")
        self.refresh_jobs(index)

    def delete_job(self):
        index = self._selected_index()
        if index is None:
            return
        if not messagebox.askyesno("حذف Job", "Job حذف شود؟ Folder Identity و Topic تلگرام حفظ می‌شوند."):
            return
        self.jobs.pop(index)
        save_jobs(self.jobs)
        self.refresh_jobs(max(0, index - 1))
        self.write_log("Job حذف شد؛ Folder Identity و Topic حذف نشدند")

    def choose_folder(self):
        selected = filedialog.askdirectory(title="انتخاب فولدر پشتیبان")
        if selected:
            self.folder.set(selected)
            if not self.job_name.get().strip():
                self.job_name.set(Path(selected).name)
            if not self.main_topic.get().strip():
                self.main_topic.set(Path(selected).name)
            self.refresh_file_list()
            self.refresh_pending_count()

    def refresh_file_list(self):
        for child in self.file_inner.winfo_children():
            child.destroy()
        self.file_vars = {}
        folder = self.folder.get().strip()
        if not folder or not Path(folder).is_dir():
            ttk.Label(self.file_inner, text="ابتدا یک فولدر انتخاب کنید.", style="Field.TLabel").pack(anchor="w", padx=8, pady=8)
            return
        job = self._job_for_current_form()
        selected = set(job.get("selected_files", [])) if job else set()
        files = current_files(folder, STATE_FILES)
        if not files:
            ttk.Label(self.file_inner, text="فایلی پیدا نشد.", style="Field.TLabel").pack(anchor="w", padx=8, pady=8)
            return
        for path in files:
            relative = str(path.relative_to(Path(folder)))
            value = str(path.resolve()) in selected
            var = tk.BooleanVar(value=value)
            self.file_vars[str(path)] = var
            ttk.Checkbutton(self.file_inner, text=relative, variable=var).pack(anchor="w", padx=8, pady=2)
        state = "normal" if self.backup_mode.get() == "SELECTED" else "disabled"
        for child in self.file_inner.winfo_children():
            if isinstance(child, ttk.Checkbutton):
                child.configure(state=state)

    def refresh_pending_count(self):
        folder = self.folder.get().strip()
        if not folder or not Path(folder).is_dir():
            self.file_count.set("0 فایل")
            return
        job = self._job_for_current_form()
        records = {}
        if job:
            folder_record = get_or_create_folder(folder)
            records = load_manifest().get("folders", {}).get(folder_record["id"], {})
        pending = pending_files(folder, records, STATE_FILES)
        if job and job.get("backup_mode") == "SELECTED":
            selected = set(job.get("selected_files", []))
            pending = [p for p in pending if str(p.resolve()) in selected]
        self.file_count.set(f"{len(pending)} فایل در صف")

    def validate_current(self):
        if not self.token.get().strip():
            raise ValueError("توکن ربات را وارد کنید.")
        if not self.folder.get().strip() or not Path(self.folder.get()).is_dir():
            raise ValueError("فولدر پشتیبان معتبر نیست.")
        if not self.chat_id.get().strip():
            raise ValueError("چت مقصد را انتخاب کنید.")
        datetime.strptime(self.schedule.get().strip(), "%H:%M")

    def prepare_topics_ui(self):
        try:
            self.validate_current()
            if not self.save_current_job():
                return
            index = self._selected_index()
            if index is None:
                return
            job = self.jobs[index]
            forum = TelegramForum(telegram_request)
            main_id, folder_record = _ensure_folder_topic(forum, self.token.get().strip(), job)
            job["main_topic_id"] = main_id
            job["folder_id"] = folder_record["id"]
            if self.history_enabled.get():
                job["history_topic_id"] = _ensure_project_history_topic(forum, self.token.get().strip(), job["chat_id"])
            save_jobs(self.jobs)
            self.select_job()
            self.status.set("Topic اصلی متصل شد؛ History مرکزی آماده است")
            self.write_log(f"Topic فولدر: {main_id} | History مرکزی: {job.get('history_topic_id')}")
        except Exception as error:
            self.write_log(f"ساخت/اتصال Topic ناموفق: {error}")
            messagebox.showerror("Telegram", str(error))

    def test_connection(self):
        token = self.token.get().strip()
        if not token:
            messagebox.showerror("خطا", "توکن را وارد کنید.")
            return
        threading.Thread(target=self._test_connection, args=(token,), daemon=True).start()

    def _test_connection(self, token):
        try:
            bot = telegram_request(token, "getMe")
            self.write_log(f"اتصال موفق: @{bot.get('username', 'بدون نام')}")
            self.root.after(0, lambda: self.status.set("اتصال برقرار است"))
        except Exception as error:
            self.write_log(f"تست اتصال ناموفق: {error}")

    def load_chats(self):
        token = self.token.get().strip()
        if not token:
            messagebox.showerror("خطا", "توکن را وارد کنید.")
            return
        threading.Thread(target=self._load_chats, args=(token,), daemon=True).start()

    def _load_chats(self, token):
        try:
            updates = telegram_request(token, "getUpdates")
            chats = {}
            for update in updates:
                message = update.get("message") or update.get("channel_post") or {}
                chat = message.get("chat") or {}
                if chat.get("id") is not None:
                    label = f"{chat.get('title') or chat.get('first_name') or 'بدون نام'} ({chat['id']})"
                    chats[label] = str(chat["id"])
            self.root.after(0, lambda: self._apply_chats(chats))
        except Exception as error:
            self.write_log(f"خطای دریافت چت‌ها: {error}")

    def _apply_chats(self, chats):
        self.chats = chats
        self.chat_combo["values"] = list(chats)
        if chats:
            self.chat_combo.current(0)
            self.chat_id.set(next(iter(chats.values())))
            self.write_log(f"{len(chats)} چت پیدا شد")

    def select_chat(self, _event=None):
        selected = self.chat_combo.get()
        if selected in self.chats:
            self.chat_id.set(self.chats[selected])

    def _start_backup_thread(self, job):
        if self.worker and self.worker.is_alive():
            self.status.set("یک عملیات در حال اجراست")
            return
        self.cancel_event.clear()
        self.progress_value.set(0)
        self.worker = threading.Thread(target=self.run_job, args=(job,), daemon=True)
        self.worker.start()

    def start_backup(self):
        try:
            self.validate_current()
            if not self.save_current_job():
                return
            index = self._selected_index()
            if index is None:
                return
            self._start_backup_thread(dict(self.jobs[index]))
        except ValueError as error:
            messagebox.showerror("تنظیمات ناقص", str(error))

    def run_job(self, job):
        self.root.after(0, lambda: self.status.set(f"در حال بکاپ: {job['name']}"))
        try:
            count, completed = run_job_backup(self.token.get().strip(), job, self.write_log, self.update_progress, self.cancel_event)
            for current in self.jobs:
                if current["id"] == job["id"]:
                    current.update(job)
            save_jobs(self.jobs)
            self.root.after(0, lambda: self.file_count.set(f"{count} فایل پردازش شد"))
            self.root.after(0, lambda: self.status.set("بکاپ کامل شد" if completed else "بکاپ متوقف شد"))
        except Exception as error:
            self.write_log(f"خطا: {error}")
            self.root.after(0, lambda: self.status.set("بکاپ ناموفق بود"))

    def update_progress(self, current, total):
        value = current / total * 100 if total else 100
        self.root.after(0, lambda: self.progress_value.set(value))

    def start_scheduler(self):
        if self.worker and self.worker.is_alive():
            return
        if not self.jobs:
            messagebox.showinfo("Scheduler", "حداقل یک Job بسازید.")
            return
        self.stop_event.clear()
        self.cancel_event.clear()
        self.worker = threading.Thread(target=self.scheduler_loop, daemon=True)
        self.worker.start()
        self.status.set("زمان‌بندی Jobهای فعال است؛ Jobهای Pause اجرا نمی‌شوند")

    def stop_scheduler(self):
        self.stop_event.set()
        self.cancel_event.set()
        self.status.set("عملیات/زمان‌بندی متوقف شد")

    def scheduler_loop(self):
        last_runs = {}
        while not self.stop_event.is_set():
            now = datetime.now()
            for job in load_and_migrate_jobs():
                if not job.get("enabled", True) or job.get("schedule") != now.strftime("%H:%M"):
                    continue
                key = job["id"]
                if last_runs.get(key) == now.date():
                    continue
                last_runs[key] = now.date()
                if BACKUP_LOCK.acquire(blocking=False):
                    BACKUP_LOCK.release()
                    self.run_job(job)
                else:
                    self.write_log(f"زمان‌بندی Job رد شد: {job.get('name')} | Backup دیگری در حال اجراست")
            self.stop_event.wait(10)

    def write_log(self, text):
        self.root.after(0, self._write_log, text)

    def _write_log(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", f"{datetime.now():%H:%M:%S}  {text}\n")
        self.log.see("end")
        self.log.configure(state="disabled")


if __name__ == "__main__":
    root = tk.Tk()
    BackupApp(root)
    root.mainloop()
