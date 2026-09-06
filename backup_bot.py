import json
import os
import threading
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import requests


BASE_DIR = Path(__file__).resolve().parent
ENV_FILE = BASE_DIR / ".env"
LOG_FILE = BASE_DIR / "last_backup.json"
HISTORY_FILE = BASE_DIR / "backup_history.json"
TELEGRAM_TIMEOUT = 30
TELEGRAM_RETRIES = 3
BACKUP_LOCK = threading.Lock()


def load_env():
    """Load the small set of KEY=VALUE settings used by this app."""
    if not ENV_FILE.exists():
        return
    for raw_line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('\"\''))


def save_env(values):
    lines = [f"{key}={value}" for key, value in values.items()]
    ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    for key, value in values.items():
        os.environ[key] = value


def load_last_backup():
    if not LOG_FILE.exists():
        return datetime.min
    try:
        value = json.loads(LOG_FILE.read_text(encoding="utf-8"))["last_backup"]
        return datetime.fromisoformat(value)
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
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
        history = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        return history if isinstance(history, list) else []
    except (OSError, TypeError, json.JSONDecodeError):
        return []


def save_history(history):
    _atomic_write(
        HISTORY_FILE,
        json.dumps(history[-5000:], ensure_ascii=False, indent=2),
    )


def _is_state_file(path):
    """Return True for local application state files that must not be backed up."""
    try:
        return path.resolve() in {LOG_FILE.resolve(), HISTORY_FILE.resolve()}
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
    files = []
    for root, _, names in os.walk(folder):
        for name in names:
            path = Path(root) / name
            if _is_state_file(path):
                continue
            if path.is_file():
                files.append(path)
    return files


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


def send_file(token, chat_id, path):
    with path.open("rb") as document:
        telegram_request(
            token,
            "sendDocument",
            files={"document": document},
            data={"chat_id": chat_id},
        )


def backup_changed_files(token, chat_id, folder, log, progress=None, cancel_event=None, selected_files=None):
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
            history.append({
                "path": str(path),
                "modified": modified,
                "sent_at": datetime.now().isoformat(timespec="seconds"),
            })
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
        self.root.geometry("1000x760")
        self.stop_event = threading.Event()
        self.cancel_event = threading.Event()
        self.worker = None
        load_env()

        self.token = tk.StringVar(value=os.getenv("TELEGRAM_BOT_TOKEN", ""))
        self.folder = tk.StringVar(value=os.getenv("BACKUP_FOLDER", ""))
        self.chat_id = tk.StringVar(value=os.getenv("TELEGRAM_CHAT_ID", ""))
        self.schedule = tk.StringVar(value=os.getenv("SCHEDULE_TIME", "23:00"))
        self.status = tk.StringVar(value="آماده")
        self.progress_value = tk.DoubleVar(value=0)
        self.file_count = tk.StringVar(value="0 فایل")
        self.chats = {}
        self.pending_checks = {}
        self.pending_paths = []
        self.build_ui()
        self.root.after(150, self.refresh_file_lists)

    def build_ui(self):
        self.root.minsize(860, 650)
        self.root.configure(bg="#eef2f5")
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure("TFrame", background="#eef2f5")
        style.configure("Card.TFrame", background="#ffffff", relief="flat", borderwidth=0)
        style.configure("Title.TLabel", background="#18324b", foreground="#ffffff", font=("Segoe UI", 14, "bold"))
        style.configure("Subtitle.TLabel", background="#18324b", foreground="#b9cbd8", font=("Segoe UI", 9))
        style.configure("Section.TLabel", background="#ffffff", foreground="#18324b", font=("Segoe UI", 10, "bold"))
        style.configure("Field.TLabel", background="#ffffff", foreground="#627384", font=("Segoe UI", 9))
        style.configure("TButton", padding=(10, 5), font=("Segoe UI", 9), relief="flat")
        style.configure("Accent.TButton", background="#16805f", foreground="#ffffff")
        style.map("Accent.TButton", background=[("active", "#0f684d")])
        style.configure("Soft.TButton", background="#e7edf1", foreground="#294255")
        style.map("Soft.TButton", background=[("active", "#d8e2e8")])
        style.configure("Status.TLabel", background="#e9f5ef", foreground="#176b4e", padding=(8, 5))

        frame = ttk.Frame(self.root, padding=12)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(3, weight=1)

        header = tk.Frame(frame, bg="#18324b", padx=16, pady=8)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 14))
        ttk.Label(header, text="پشتیبان‌گیری تلگرام", style="Title.TLabel").pack(anchor="w")
        ttk.Label(header, text="ارسال فایل‌های انتخاب‌شده با زمان‌بندی روزانه", style="Subtitle.TLabel").pack(anchor="w", pady=(2, 0))

        settings = ttk.Frame(frame, style="Card.TFrame", padding=10)
        settings.grid(row=1, column=0, sticky="ew")
        settings.columnconfigure(0, weight=1)
        settings.columnconfigure(1, weight=1)
        ttk.Label(settings, text="تنظیمات اتصال و پشتیبان", style="Section.TLabel").grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 7))

        connection = ttk.Frame(settings, style="Card.TFrame")
        connection.grid(row=1, column=0, sticky="nsew", padx=(0, 8))
        connection.columnconfigure(0, weight=1)
        ttk.Label(connection, text="توکن ربات", style="Field.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Entry(connection, textvariable=self.token, show="*").grid(row=1, column=0, sticky="ew", pady=(2, 6))
        ttk.Label(connection, text="چت مقصد", style="Field.TLabel").grid(row=2, column=0, sticky="w")
        self.chat_combo = ttk.Combobox(connection, textvariable=self.chat_id, state="normal")
        self.chat_combo.grid(row=3, column=0, sticky="ew", pady=(2, 0))
        self.chat_combo.bind("<<ComboboxSelected>>", self.select_chat)
        ttk.Button(connection, text="بارگذاری چت‌ها", style="Soft.TButton", command=self.load_chats).grid(row=4, column=0, sticky="w", pady=(6, 0))

        backup_settings = ttk.Frame(settings, style="Card.TFrame")
        backup_settings.grid(row=1, column=1, sticky="nsew", padx=(8, 0))
        backup_settings.columnconfigure(0, weight=1)
        ttk.Label(backup_settings, text="فولدر پشتیبان", style="Field.TLabel").grid(row=0, column=0, sticky="w")
        folder_row = ttk.Frame(backup_settings, style="Card.TFrame")
        folder_row.grid(row=1, column=0, sticky="ew", pady=(2, 6))
        folder_row.columnconfigure(0, weight=1)
        ttk.Entry(folder_row, textvariable=self.folder).grid(row=0, column=0, sticky="ew")
        ttk.Button(folder_row, text="انتخاب", style="Soft.TButton", command=self.choose_folder).grid(row=0, column=1, padx=(6, 0))
        ttk.Label(backup_settings, text="زمان اجرای روزانه  |  HH:MM", style="Field.TLabel").grid(row=2, column=0, sticky="w")
        ttk.Entry(backup_settings, textvariable=self.schedule, width=12).grid(row=3, column=0, sticky="w", pady=(2, 0))

        actions = ttk.Frame(frame)
        actions.grid(row=2, column=0, sticky="ew", pady=8)
        ttk.Button(actions, text="ذخیره تنظیمات", style="Soft.TButton", command=self.save_settings).pack(side="left")
        ttk.Button(actions, text="تست اتصال", style="Soft.TButton", command=self.test_connection).pack(side="left", padx=6)
        ttk.Button(actions, text="ارسال الآن", style="Accent.TButton", command=self.start_backup).pack(side="left")
        ttk.Button(actions, text="شروع زمان‌بندی", style="Soft.TButton", command=self.start_scheduler).pack(side="left", padx=6)
        ttk.Button(actions, text="توقف", style="Soft.TButton", command=self.stop_scheduler).pack(side="left")

        activity = ttk.Frame(frame, style="Card.TFrame", padding=8)
        activity.grid(row=3, column=0, sticky="nsew")
        activity.columnconfigure(0, weight=1)
        activity.columnconfigure(1, weight=1)
        activity.rowconfigure(3, weight=1, minsize=280)
        ttk.Label(activity, text="وضعیت اجرا", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        summary = ttk.Frame(activity, style="Card.TFrame")
        summary.grid(row=0, column=1, sticky="e")
        ttk.Label(summary, textvariable=self.file_count, foreground="#13795b").pack(padx=10, pady=4)
        ttk.Label(activity, textvariable=self.status, style="Status.TLabel").grid(row=1, column=0, columnspan=2, sticky="ew", pady=(5, 4))
        self.progress = ttk.Progressbar(activity, variable=self.progress_value, maximum=100)
        self.progress.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(0, 5))

        pending = ttk.Frame(activity, style="Card.TFrame", padding=8)
        pending.grid(row=3, column=0, sticky="nsew", padx=(0, 6))
        pending.columnconfigure(0, weight=1)
        pending.rowconfigure(2, weight=1)
        pending_actions = ttk.Frame(pending, style="Card.TFrame")
        pending_actions.grid(row=0, column=0, sticky="ew")
        ttk.Label(pending_actions, text="در انتظار ارسال", style="Section.TLabel").pack(side="left")
        ttk.Button(pending_actions, text="به‌روزرسانی", command=self.refresh_file_lists).pack(side="right")
        selection_actions = ttk.Frame(pending, style="Card.TFrame")
        selection_actions.grid(row=1, column=0, sticky="ew", pady=(6, 4))
        ttk.Button(selection_actions, text="انتخاب همه", command=self.select_all_pending).pack(side="left")
        ttk.Button(selection_actions, text="لغو همه", command=self.clear_all_pending).pack(side="left", padx=5)
        ttk.Button(selection_actions, text="ارسال انتخاب‌شده‌ها", style="Accent.TButton", command=self.start_selected_backup).pack(side="right")
        self.pending_canvas, self.pending_list = self.make_scroll_list(pending)

        sent = ttk.Frame(activity, style="Card.TFrame", padding=8)
        sent.grid(row=3, column=1, sticky="nsew", padx=(6, 0))
        sent.columnconfigure(0, weight=1)
        sent.rowconfigure(1, weight=1)
        sent_header = ttk.Frame(sent, style="Card.TFrame")
        sent_header.grid(row=0, column=0, sticky="ew")
        ttk.Label(sent_header, text="ارسال‌شده‌ها", style="Section.TLabel").pack(side="left")
        ttk.Button(sent_header, text="پاک کردن تاریخچه", command=self.clear_history).pack(side="right")
        self.sent_list = tk.Listbox(sent, height=8, activestyle="none", bg="#fbfcfd", relief="flat", highlightthickness=0)
        self.sent_list.grid(row=1, column=0, sticky="nsew", pady=(8, 0))

        log_actions = ttk.Frame(activity, style="Card.TFrame")
        log_actions.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        ttk.Label(log_actions, text="گزارش فعالیت", style="Section.TLabel").pack(side="left")
        ttk.Button(log_actions, text="پاک کردن گزارش", command=self.clear_log).pack(side="right")
        self.log = tk.Text(activity, height=3, state="disabled", wrap="word", bg="#fbfcfd", relief="flat", padx=8, pady=6)
        self.log.grid(row=5, column=0, columnspan=2, sticky="nsew")

    def make_scroll_list(self, parent):
        list_frame = ttk.Frame(parent, style="Card.TFrame")
        list_frame.grid(row=2, column=0, sticky="nsew")
        list_frame.columnconfigure(0, weight=1)
        list_frame.rowconfigure(0, weight=1)
        canvas = tk.Canvas(list_frame, bg="#fbfcfd", highlightthickness=0)
        scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        content = ttk.Frame(canvas, style="Card.TFrame")
        content_window = canvas.create_window((0, 0), window=content, anchor="nw")
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(content_window, width=event.width))
        content.bind("<Configure>", lambda _event: canvas.configure(scrollregion=canvas.bbox("all")))
        return canvas, content

    def write_log(self, text):
        self.root.after(0, self._write_log, text)

    def _write_log(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", f"{datetime.now():%H:%M:%S}  {text}\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def clear_log(self):
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    def choose_folder(self):
        selected = filedialog.askdirectory(title="انتخاب فولدر پشتیبان")
        if selected:
            self.folder.set(selected)
            self.refresh_file_lists()

    def refresh_file_lists(self):
        for child in self.pending_list.winfo_children():
            child.destroy()
        self.pending_checks = {}
        self.pending_paths = []
        folder = self.folder.get().strip()
        if folder and Path(folder).is_dir():
            try:
                self.pending_paths = sorted(get_pending_files(folder), key=lambda path: str(path).lower())
            except OSError as error:
                self.write_log(f"خطا در خواندن فولدر: {error}")
        for path in self.pending_paths:
            checked = tk.BooleanVar(value=True)
            self.pending_checks[path] = checked
            try:
                size = path.stat().st_size / 1024
                label = f"{path.name}  |  {size:.1f} KB"
            except OSError:
                label = path.name
            ttk.Checkbutton(self.pending_list, text=label, variable=checked).pack(anchor="w", fill="x", padx=6, pady=2)
        self.render_sent_history()
        self.file_count.set(f"{len(self.pending_paths)} فایل در صف")

    def render_sent_history(self):
        self.sent_list.delete(0, "end")
        for item in reversed(load_history()[-200:]):
            sent_at = item.get("sent_at", "")
            self.sent_list.insert("end", f"{Path(item.get('path', '')).name}  |  {sent_at}")

    def select_all_pending(self):
        for checked in self.pending_checks.values():
            checked.set(True)

    def clear_all_pending(self):
        for checked in self.pending_checks.values():
            checked.set(False)

    def clear_history(self):
        if not load_history():
            return
        if not messagebox.askyesno("پاک کردن تاریخچه", "تاریخچه‌ی ارسال پاک شود؟ فایل‌ها دوباره در صف قرار می‌گیرند."):
            return
        save_history([])
        if LOG_FILE.exists():
            LOG_FILE.unlink()
        self.refresh_file_lists()
        self.write_log("تاریخچه‌ی ارسال پاک شد")

    def select_chat(self, _event=None):
        selected = self.chat_combo.get()
        if selected in self.chats:
            self.chat_id.set(self.chats[selected])

    def validate(self):
        if not self.token.get().strip():
            raise ValueError("توکن ربات را وارد کنید.")
        if not self.folder.get().strip() or not Path(self.folder.get()).is_dir():
            raise ValueError("فولدر پشتیبان معتبر نیست.")
        if not self.chat_id.get().strip():
            raise ValueError("چت مقصد را انتخاب کنید.")
        datetime.strptime(self.schedule.get().strip(), "%H:%M")

    def save_settings(self):
        try:
            self.validate()
        except ValueError as error:
            messagebox.showerror("تنظیمات ناقص", str(error))
            return
        save_env({
            "TELEGRAM_BOT_TOKEN": self.token.get().strip(),
            "TELEGRAM_CHAT_ID": self.chat_id.get().strip(),
            "BACKUP_FOLDER": self.folder.get().strip(),
            "SCHEDULE_TIME": self.schedule.get().strip(),
        })
        self.status.set("تنظیمات ذخیره شد")
        self.write_log("تنظیمات در .env ذخیره شد")

    def test_connection(self):
        token = self.token.get().strip()
        if not token:
            messagebox.showerror("خطا", "ابتدا توکن ربات را وارد کنید.")
            return
        threading.Thread(target=self._test_connection, args=(token,), daemon=True).start()

    def _test_connection(self, token):
        try:
            bot = telegram_request(token, "getMe")
            self.write_log(f"اتصال موفق: @{bot.get('username', 'بدون نام')}")
            self.root.after(0, lambda: self.status.set("اتصال به Telegram برقرار است"))
        except Exception as error:
            self.write_log(f"تست اتصال ناموفق: {error}")
            self.root.after(0, lambda: self.status.set("اتصال برقرار نشد"))

    def load_chats(self):
        token = self.token.get().strip()
        if not token:
            messagebox.showerror("خطا", "ابتدا توکن ربات را وارد کنید.")
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
            self.root.after(0, lambda: messagebox.showerror("خطای تلگرام", str(error)))

    def _apply_chats(self, chats):
        self.chats = chats
        self.chat_combo["values"] = list(chats)
        if chats:
            self.chat_combo.current(0)
            self.chat_id.set(next(iter(chats.values())))
            self.write_log(f"{len(chats)} چت پیدا شد")
        else:
            self.write_log("چتی در getUpdates پیدا نشد؛ به ربات پیام بدهید و دوباره تلاش کنید")

    def _start_backup_thread(self, config, selected_files=None):
        if self.worker and self.worker.is_alive():
            self.status.set("یک عملیات پشتیبان‌گیری در حال اجراست")
            return False
        self.cancel_event.clear()
        self.progress_value.set(0)
        self.worker = threading.Thread(target=self.run_backup, args=(config, selected_files), daemon=True)
        self.worker.start()
        return True

    def start_backup(self):
        try:
            self.validate()
        except ValueError as error:
            messagebox.showerror("تنظیمات ناقص", str(error))
            return
        config = (self.token.get().strip(), self.chat_id.get().strip(), self.folder.get().strip())
        self._start_backup_thread(config)

    def start_selected_backup(self):
        try:
            self.validate()
        except ValueError as error:
            messagebox.showerror("تنظیمات ناقص", str(error))
            return
        selected = [path for path, checked in self.pending_checks.items() if checked.get()]
        if not selected:
            messagebox.showinfo("صف خالی", "حداقل یک فایل را انتخاب کنید.")
            return
        config = (self.token.get().strip(), self.chat_id.get().strip(), self.folder.get().strip())
        self._start_backup_thread(config, selected)

    def run_backup(self, config, selected_files=None):
        self.root.after(0, lambda: self.status.set("در حال ارسال..."))
        try:
            count, completed = backup_changed_files(
                *config,
                self.write_log,
                self.update_progress,
                self.cancel_event,
                selected_files,
            )
            self.root.after(0, lambda: self.file_count.set(f"{count} فایل ارسال‌شده"))
            self.root.after(0, lambda: self.status.set("ارسال کامل شد" if completed else "ارسال متوقف شد"))
            self.root.after(0, self.refresh_file_lists)
        except Exception as error:
            self.write_log(f"خطا: {error}")
            self.root.after(0, lambda: self.status.set("ارسال ناموفق بود"))

    def update_progress(self, current, total):
        value = current / total * 100 if total else 100
        self.root.after(0, lambda: self.progress_value.set(value))

    def start_scheduler(self):
        try:
            self.validate()
        except ValueError as error:
            messagebox.showerror("تنظیمات ناقص", str(error))
            return
        self.save_settings()
        if self.worker and self.worker.is_alive():
            return
        self.stop_event.clear()
        self.cancel_event.clear()
        schedule_time = self.schedule.get().strip()
        config = (self.token.get().strip(), self.chat_id.get().strip(), self.folder.get().strip())
        self.worker = threading.Thread(target=self.scheduler_loop, args=(schedule_time, config), daemon=True)
        self.worker.start()
        self.status.set(f"زمان‌بندی فعال است: هر روز ساعت {schedule_time}")

    def stop_scheduler(self):
        self.stop_event.set()
        self.cancel_event.set()
        self.status.set("زمان‌بندی متوقف شد")

    def scheduler_loop(self, schedule_time, config):
        last_run_date = None
        while not self.stop_event.is_set():
            now = datetime.now()
            if now.strftime("%H:%M") == schedule_time and last_run_date != now.date():
                last_run_date = now.date()
                if BACKUP_LOCK.acquire(blocking=False):
                    BACKUP_LOCK.release()
                    self.run_backup(config)
                else:
                    self.write_log("زمان‌بندی رد شد؛ یک Backup دیگر در حال اجراست")
            self.stop_event.wait(10)


if __name__ == "__main__":
    root = tk.Tk()
    BackupApp(root)
    root.mainloop()