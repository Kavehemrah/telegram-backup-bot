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
    JOBS_FILE,
    MANIFEST_FILE,
    current_files,
    file_key,
    load_jobs,
    load_manifest,
    new_job,
    pending_files,
    save_jobs,
    save_manifest,
)
from telegram_forum import TelegramForum


BASE_DIR = Path(__file__).resolve().parent
ENV_FILE = BASE_DIR / ".env"
LOG_FILE = BASE_DIR / "last_backup.json"
HISTORY_FILE = BASE_DIR / "backup_history.json"
STATE_FILES = {LOG_FILE, HISTORY_FILE, JOBS_FILE, MANIFEST_FILE}
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


def save_env(values):
    ENV_FILE.write_text("\n".join(f"{key}={value}" for key, value in values.items()) + "\n", encoding="utf-8")
    for key, value in values.items():
        os.environ[key] = value


def load_last_backup():
    if not LOG_FILE.exists():
        return datetime.min
    try:
        return datetime.fromisoformat(json.loads(LOG_FILE.read_text(encoding="utf-8"))["last_backup"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError, OSError):
        return datetime.min


def _atomic_write(path, text):
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
    except (OSError, TypeError, json.JSONDecodeError):
        return []


def save_history(history):
    _atomic_write(HISTORY_FILE, json.dumps(history[-5000:], ensure_ascii=False, indent=2))


def _is_state_file(path):
    try:
        resolved = path.resolve()
        return resolved in {item.resolve() for item in STATE_FILES}
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
    history = load_history()
    if history:
        sent_versions = {(item.get("path"), item.get("modified")) for item in history}
        files = []
        for root, _, names in os.walk(folder):
            for name in names:
                path = Path(root) / name
                if _is_state_file(path):
                    continue
                try:
                    modified = path.stat().st_mtime_ns
                    if (str(path), modified) not in sent_versions:
                        files.append(path)
                except OSError:
                    continue
        return files
    return [
        path
        for root, _, names in os.walk(folder)
        for name in names
        for path in [Path(root) / name]
        if not _is_state_file(path) and path.is_file()
    ]


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


def send_file(token, chat_id, path, thread_id=None):
    return TelegramForum(telegram_request).send_document(token, chat_id, path, thread_id)


def _archive_current_version(forum, token, job, record, log):
    history_id = job.get("history_topic_id")
    message_id = record.get("message_id")
    if not history_id or not message_id:
        return
    forum.copy_message(token, job["chat_id"], int(message_id), int(history_id))
    try:
        forum.delete_message(token, job["chat_id"], int(message_id))
    except Exception as error:
        log(f"نسخه قبلی کپی شد ولی حذف پیام اصلی ممکن نشد: {error}")


def run_job_backup(token, job, log, progress=None, cancel_event=None, selected_files=None):
    if not BACKUP_LOCK.acquire(blocking=False):
        raise RuntimeError("یک عملیات پشتیبان‌گیری دیگر در حال اجراست.")
    try:
        folder = Path(job["folder"])
        if not folder.is_dir():
            raise ValueError(f"فولدر معتبر نیست: {folder}")

        forum = TelegramForum(telegram_request)
        manifest = load_manifest()
        records = manifest.setdefault(job["id"], {})
        destination_thread = None

        if job.get("destination") == "topic":
            main_id, history_id = forum.prepare_topics(
                token,
                job["chat_id"],
                job.get("main_topic_name") or job["name"],
                job.get("history_topic_name") or f"{job['name']} History",
                job.get("main_topic_id"),
                job.get("history_topic_id"),
            )
            job["main_topic_id"] = main_id
            job["history_topic_id"] = history_id
            destination_thread = main_id
        elif job.get("destination") == "general":
            job["main_topic_id"] = None
            job["history_topic_id"] = None

        save_jobs(load_jobs())
        excluded = {LOG_FILE, HISTORY_FILE, JOBS_FILE, MANIFEST_FILE, ENV_FILE}
        paths = selected_files if selected_files is not None else pending_files(job["folder"], records, excluded)
        total = len(paths)
        uploaded = 0

        for index, path in enumerate(paths, start=1):
            if cancel_event and cancel_event.is_set():
                log("ارسال توسط کاربر متوقف شد")
                return uploaded, False
            key = file_key(path)
            old = records.get(key)
            try:
                modified = path.stat().st_mtime_ns
            except OSError:
                log(f"فایل در دسترس نیست و رد شد: {path}")
                continue
            if old and old.get("message_id"):
                _archive_current_version(forum, token, job, old, log)
            message = forum.send_document(token, job["chat_id"], path, destination_thread)
            records[key] = {
                "path": str(path),
                "relative_path": str(path.relative_to(folder)),
                "modified": modified,
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

        existing_keys = {file_key(path) for path in current_files(job["folder"], excluded)}
        for key, record in list(records.items()):
            if record.get("deleted") or key in existing_keys or not record.get("message_id"):
                continue
            _archive_current_version(forum, token, job, record, log)
            if job.get("history_topic_id"):
                forum.send_text(
                    token,
                    job["chat_id"],
                    f"🗑 فایل حذف شد\n{record.get('relative_path', record.get('path', ''))}\nنسخه {record.get('version', 1)} محفوظ است.",
                    int(job["history_topic_id"]),
                )
            record["deleted"] = True
            record["deleted_at"] = datetime.now().isoformat(timespec="seconds")
        save_manifest(manifest)
        save_backup_time()
        return uploaded, True
    finally:
        BACKUP_LOCK.release()


def backup_changed_files(token, chat_id, folder, log, progress=None, cancel_event=None, selected_files=None):
    """Backward-compatible single-folder backup used by the original UI/tests."""
    if not BACKUP_LOCK.acquire(blocking=False):
        raise RuntimeError("یک عملیات پشتیبان‌گیری دیگر در حال اجراست.")
    try:
        changed_files = selected_files if selected_files is not None else get_pending_files(folder)
        total = len(changed_files)
        history = load_history()
        for index, path in enumerate(changed_files, start=1):
            if cancel_event and cancel_event.is_set():
                log("ارسال توسط کاربر متوقف شد")
                return index - 1, False
            try:
                modified = path.stat().st_mtime_ns
            except OSError:
                log(f"فایل در دسترس نیست و رد شد: {path}")
                continue
            send_file(token, chat_id, path)
            history.append({"path": str(path), "modified": modified, "sent_at": datetime.now().isoformat(timespec="seconds")})
            save_history(history)
            log(f"ارسال شد: {path}")
            if progress:
                progress(index, total)
        save_backup_time()
        return total, True
    finally:
        BACKUP_LOCK.release()


class BackupApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Telegram Folder Backup")
        self.root.geometry("1060x780")
        self.stop_event = threading.Event()
        self.cancel_event = threading.Event()
        self.worker = None
        self.jobs = load_jobs()
        self.manifest = load_manifest()
        load_env()
        self.token = tk.StringVar(value=os.getenv("TELEGRAM_BOT_TOKEN", ""))
        self.status = tk.StringVar(value="آماده")
        self.destination = tk.StringVar(value="topic")
        self.job_name = tk.StringVar()
        self.folder = tk.StringVar()
        self.chat_id = tk.StringVar(value=os.getenv("TELEGRAM_CHAT_ID", ""))
        self.main_topic = tk.StringVar()
        self.history_topic = tk.StringVar()
        self.schedule = tk.StringVar(value="23:00")
        self.enabled = tk.BooleanVar(value=True)
        self.progress_value = tk.DoubleVar(value=0)
        self.file_count = tk.StringVar(value="0 فایل")
        self.chats = {}
        self.build_ui()
        self._migrate_legacy_config()
        self.refresh_jobs()

    def build_ui(self):
        self.root.configure(bg="#eef2f5")
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure("TFrame", background="#eef2f5")
        style.configure("Card.TFrame", background="#ffffff")
        style.configure("Title.TLabel", background="#18324b", foreground="#ffffff", font=("Segoe UI", 14, "bold"))
        style.configure("Section.TLabel", background="#ffffff", foreground="#18324b", font=("Segoe UI", 10, "bold"))
        style.configure("Field.TLabel", background="#ffffff", foreground="#627384", font=("Segoe UI", 9))
        style.configure("TButton", padding=(9, 5), font=("Segoe UI", 9))
        style.configure("Accent.TButton", background="#16805f", foreground="#ffffff")

        frame = ttk.Frame(self.root, padding=12)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(3, weight=1)

        header = tk.Frame(frame, bg="#18324b", padx=16, pady=10)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        ttk.Label(header, text="پشتیبان‌گیری حرفه‌ای تلگرام", style="Title.TLabel").pack(anchor="w")

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
        body.columnconfigure(0, weight=0)
        body.columnconfigure(1, weight=1)

        jobs_card = ttk.Frame(body, style="Card.TFrame", padding=8)
        jobs_card.grid(row=0, column=0, sticky="ns", padx=(0, 8))
        ttk.Label(jobs_card, text="Backup Jobs", style="Section.TLabel").pack(anchor="w")
        self.jobs_list = tk.Listbox(jobs_card, width=24, height=15, activestyle="none")
        self.jobs_list.pack(fill="y", expand=True, pady=7)
        self.jobs_list.bind("<<ListboxSelect>>", self.select_job)
        ttk.Button(jobs_card, text="＋ پوشه جدید", command=self.new_job_ui).pack(fill="x")
        ttk.Button(jobs_card, text="حذف Job", command=self.delete_job).pack(fill="x", pady=(5, 0))

        editor = ttk.Frame(body, style="Card.TFrame", padding=10)
        editor.grid(row=0, column=1, sticky="nsew")
        editor.columnconfigure(1, weight=1)
        fields = [
            ("نام Job", self.job_name),
            ("فولدر", self.folder),
            ("چت", self.chat_id),
            ("نام Topic اصلی", self.main_topic),
            ("نام Topic تاریخچه", self.history_topic),
            ("زمان روزانه", self.schedule),
        ]
        for row, (label, variable) in enumerate(fields):
            ttk.Label(editor, text=label, style="Field.TLabel").grid(row=row, column=0, sticky="w", pady=4)
            ttk.Entry(editor, textvariable=variable).grid(row=row, column=1, sticky="ew", pady=4)
            if label == "فولدر":
                ttk.Button(editor, text="انتخاب", command=self.choose_folder).grid(row=row, column=2, padx=5)
        ttk.Label(editor, text="مقصد", style="Field.TLabel").grid(row=6, column=0, sticky="w", pady=4)
        ttk.Combobox(editor, textvariable=self.destination, values=("topic", "general"), state="readonly", width=12).grid(row=6, column=1, sticky="w", pady=4)
        ttk.Checkbutton(editor, text="فعال برای زمان‌بندی", variable=self.enabled).grid(row=7, column=1, sticky="w", pady=4)
        actions = ttk.Frame(editor, style="Card.TFrame")
        actions.grid(row=8, column=0, columnspan=3, sticky="ew", pady=(10, 0))
        ttk.Button(actions, text="ذخیره Job", command=self.save_current_job).pack(side="left")
        ttk.Button(actions, text="ساخت Topicها", command=self.prepare_topics_ui).pack(side="left", padx=5)
        ttk.Button(actions, text="ارسال الآن", style="Accent.TButton", command=self.start_backup).pack(side="left")
        ttk.Button(actions, text="لغو عملیات", command=self.stop_scheduler).pack(side="left", padx=5)

        activity = ttk.Frame(frame, style="Card.TFrame", padding=10)
        activity.grid(row=3, column=0, sticky="nsew")
        activity.columnconfigure(0, weight=1)
        activity.rowconfigure(3, weight=1)
        ttk.Label(activity, text="وضعیت", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(activity, textvariable=self.status).grid(row=0, column=1, sticky="e")
        ttk.Label(activity, textvariable=self.file_count).grid(row=1, column=0, sticky="w", pady=5)
        ttk.Progressbar(activity, variable=self.progress_value, maximum=100).grid(row=1, column=1, sticky="ew", pady=5)
        ttk.Label(activity, text="گزارش فعالیت", style="Section.TLabel").grid(row=2, column=0, columnspan=2, sticky="w", pady=(5, 3))
        self.log = tk.Text(activity, height=8, state="disabled", wrap="word", bg="#fbfcfd", relief="flat")
        self.log.grid(row=3, column=0, columnspan=2, sticky="nsew")
        ttk.Button(activity, text="شروع زمان‌بندی همه Jobها", command=self.start_scheduler).grid(row=4, column=0, sticky="w", pady=7)

    def _migrate_legacy_config(self):
        if self.jobs:
            return
        folder = os.getenv("BACKUP_FOLDER", "").strip()
        chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
        if folder and chat_id and Path(folder).is_dir():
            job = new_job(folder, chat_id, os.getenv("SCHEDULE_TIME", "23:00"))
            self.jobs = [job]
            save_jobs(self.jobs)
            self.write_log("تنظیمات نسخه قبلی به یک Backup Job منتقل شد")

    def refresh_jobs(self):
        self.jobs_list.delete(0, "end")
        for job in self.jobs:
            marker = "✓" if job.get("enabled", True) else "○"
            self.jobs_list.insert("end", f"{marker} {job.get('name', job.get('folder', 'Job'))}")
        if self.jobs:
            self.jobs_list.selection_set(0)
            self.select_job()
        else:
            self.new_job_ui()

    def new_job_ui(self):
        self.job_name.set("")
        self.folder.set("")
        self.chat_id.set(self.chat_id.get().strip())
        self.main_topic.set("")
        self.history_topic.set("")
        self.schedule.set("23:00")
        self.destination.set("topic")
        self.enabled.set(True)
        self.jobs_list.selection_clear(0, "end")
        self.status.set("Job جدید آماده است")

    def select_job(self, _event=None):
        selection = self.jobs_list.curselection()
        if not selection or selection[0] >= len(self.jobs):
            return
        job = self.jobs[selection[0]]
        self.job_name.set(job.get("name", ""))
        self.folder.set(job.get("folder", ""))
        self.chat_id.set(str(job.get("chat_id", "")))
        self.destination.set(job.get("destination", "topic"))
        self.main_topic.set(job.get("main_topic_name", job.get("name", "")))
        self.history_topic.set(job.get("history_topic_name", f"{job.get('name', 'Backup')} History"))
        self.schedule.set(job.get("schedule", "23:00"))
        self.enabled.set(job.get("enabled", True))
        self.refresh_pending_count()

    def _selected_index(self):
        selection = self.jobs_list.curselection()
        return selection[0] if selection else None

    def save_current_job(self):
        folder = self.folder.get().strip()
        chat_id = self.chat_id.get().strip()
        if not folder or not Path(folder).is_dir():
            messagebox.showerror("خطا", "فولدر معتبر نیست.")
            return
        if not chat_id or not self.token.get().strip():
            messagebox.showerror("خطا", "توکن و چت مقصد را وارد کنید.")
            return
        try:
            datetime.strptime(self.schedule.get().strip(), "%H:%M")
        except ValueError:
            messagebox.showerror("خطا", "زمان باید با قالب HH:MM باشد.")
            return
        index = self._selected_index()
        if index is None:
            job = new_job(folder, chat_id, self.schedule.get().strip())
            self.jobs.append(job)
            index = len(self.jobs) - 1
        else:
            job = self.jobs[index]
            job.update({
                "name": self.job_name.get().strip() or Path(folder).name,
                "folder": folder,
                "chat_id": chat_id,
                "destination": self.destination.get(),
                "main_topic_name": self.main_topic.get().strip() or Path(folder).name,
                "history_topic_name": self.history_topic.get().strip() or f"{Path(folder).name} History",
                "schedule": self.schedule.get().strip(),
                "enabled": self.enabled.get(),
            })
        save_jobs(self.jobs)
        self.refresh_jobs()
        self.jobs_list.selection_clear(0, "end")
        self.jobs_list.selection_set(index)
        self.select_job()
        self.status.set("Job ذخیره شد")

    def delete_job(self):
        index = self._selected_index()
        if index is None:
            return
        if not messagebox.askyesno("حذف Job", "این Backup Job حذف شود؟ تاریخچه Telegram حذف نمی‌شود."):
            return
        job_id = self.jobs[index]["id"]
        self.jobs.pop(index)
        save_jobs(self.jobs)
        manifest = load_manifest()
        manifest.pop(job_id, None)
        save_manifest(manifest)
        self.refresh_jobs()

    def choose_folder(self):
        selected = filedialog.askdirectory(title="انتخاب فولدر پشتیبان")
        if selected:
            self.folder.set(selected)
            if not self.job_name.get().strip():
                self.job_name.set(Path(selected).name)
            if not self.main_topic.get().strip():
                self.main_topic.set(Path(selected).name)
            if not self.history_topic.get().strip():
                self.history_topic.set(f"{Path(selected).name} History")
            self.refresh_pending_count()

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
            self.save_current_job()
            index = self._selected_index()
            if index is None:
                return
            job = self.jobs[index]
            forum = TelegramForum(telegram_request)
            main_id, history_id = forum.prepare_topics(
                self.token.get().strip(), job["chat_id"], job["main_topic_name"], job["history_topic_name"], job.get("main_topic_id"), job.get("history_topic_id")
            )
            job["main_topic_id"] = main_id
            job["history_topic_id"] = history_id
            job["destination"] = "topic"
            save_jobs(self.jobs)
            self.select_job()
            self.status.set("Topic اصلی و History آماده شدند")
            self.write_log(f"Topicها آماده شدند: {main_id} / {history_id}")
        except Exception as error:
            self.write_log(f"ساخت Topic ناموفق: {error}")
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

    def refresh_pending_count(self):
        folder = self.folder.get().strip()
        if not folder or not Path(folder).is_dir():
            self.file_count.set("0 فایل")
            return
        index = self._selected_index()
        if index is not None and index < len(self.jobs) and self.jobs[index].get("folder") == folder:
            job_id = self.jobs[index]["id"]
            records = load_manifest().get(job_id, {})
        else:
            records = {}
        excluded = {LOG_FILE, HISTORY_FILE, JOBS_FILE, MANIFEST_FILE, ENV_FILE}
        self.file_count.set(f"{len(pending_files(folder, records, excluded))} فایل در صف")

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
            self.save_current_job()
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
        self.status.set("زمان‌بندی همه Jobهای فعال است")

    def stop_scheduler(self):
        self.stop_event.set()
        self.cancel_event.set()
        self.status.set("عملیات/زمان‌بندی متوقف شد")

    def scheduler_loop(self):
        last_runs = {}
        while not self.stop_event.is_set():
            now = datetime.now()
            for job in load_jobs():
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
