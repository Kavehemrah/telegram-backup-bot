from __future__ import annotations

import os
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

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
    pending_files,
    save_jobs,
)
from telegram_forum import TelegramForum


class BackupApp:
    """Desktop UI for managing independent backup jobs.

    The UI deliberately keeps destructive actions visible and keeps file
    selection in a dedicated dialog instead of squeezing dozens of checkboxes
    into the main editor.
    """

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Telegram Folder Backup")
        self.root.geometry("1240x820")
        self.root.minsize(1050, 700)
        self.root.configure(bg="#eef2f5")

        self.jobs = load_and_migrate_jobs()
        self.chats: dict[str, str] = {}
        self.file_vars: dict[str, tk.BooleanVar] = {}
        self.worker: threading.Thread | None = None
        self.scheduler_thread: threading.Thread | None = None
        self.stop_event = threading.Event()
        self.cancel_event = threading.Event()

        self.token = tk.StringVar(value=os.getenv("TELEGRAM_BOT_TOKEN", ""))
        self.chat_id = tk.StringVar(value=os.getenv("TELEGRAM_CHAT_ID", ""))
        self.job_name = tk.StringVar()
        self.folder = tk.StringVar()
        self.main_topic = tk.StringVar()
        self.schedule = tk.StringVar(value="23:00")
        self.enabled = tk.BooleanVar(value=True)
        self.destination = tk.StringVar(value="topic")
        self.backup_mode = tk.StringVar(value="ALL")
        self.replace_files = tk.BooleanVar(value=True)
        self.history_enabled = tk.BooleanVar(value=True)
        self.file_summary = tk.StringVar(value="فایلی انتخاب نشده")
        self.status = tk.StringVar(value="آماده")
        self.progress = tk.DoubleVar(value=0)

        self._configure_styles()
        self._build_ui()
        self.refresh_jobs()

    def _configure_styles(self):
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure("TFrame", background="#eef2f5")
        style.configure("Card.TFrame", background="#ffffff")
        style.configure("TLabel", background="#ffffff", foreground="#243746", font=("Segoe UI", 10))
        style.configure("Title.TLabel", background="#ffffff", foreground="#17324d", font=("Segoe UI", 12, "bold"))
        style.configure("Muted.TLabel", background="#ffffff", foreground="#687887", font=("Segoe UI", 9))
        style.configure("Header.TLabel", background="#17324d", foreground="#ffffff", font=("Segoe UI", 17, "bold"))
        style.configure("HeaderSub.TLabel", background="#17324d", foreground="#d9e5ef", font=("Segoe UI", 9))
        style.configure("TButton", padding=(12, 8), font=("Segoe UI", 9))
        style.configure("Primary.TButton", padding=(14, 9), font=("Segoe UI", 9, "bold"))
        style.configure("Danger.TButton", padding=(12, 8), font=("Segoe UI", 9, "bold"))
        style.configure("Treeview", rowheight=34, font=("Segoe UI", 9))
        style.configure("Treeview.Heading", font=("Segoe UI", 9, "bold"))
        style.configure("TLabelframe", background="#ffffff")
        style.configure("TLabelframe.Label", background="#ffffff", foreground="#17324d", font=("Segoe UI", 10, "bold"))

    def _build_ui(self):
        outer = ttk.Frame(self.root, padding=14)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=0)
        outer.columnconfigure(1, weight=1)
        outer.rowconfigure(1, weight=1)

        header = tk.Frame(outer, bg="#17324d", height=78)
        header.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 12))
        header.grid_propagate(False)
        ttk.Label(header, text="پشتیبان‌گیری تلگرام", style="Header.TLabel").pack(anchor="w", padx=18, pady=(13, 0))
        ttk.Label(header, text="مدیریت Job مستقل • Topic پایدار • History مرکزی", style="HeaderSub.TLabel").pack(anchor="w", padx=18, pady=(2, 0))

        self._build_sidebar(outer)
        self._build_editor(outer)
        self._build_statusbar(outer)

    def _build_sidebar(self, parent):
        card = ttk.Frame(parent, style="Card.TFrame", padding=12)
        card.grid(row=1, column=0, sticky="ns", padx=(0, 10))
        card.rowconfigure(2, weight=1)
        ttk.Label(card, text="Backup Jobs", style="Title.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(card, text="هر Job وضعیت و تنظیمات مستقل دارد.", style="Muted.TLabel").grid(row=1, column=0, sticky="w", pady=(2, 8))

        tree_frame = ttk.Frame(card, style="Card.TFrame")
        tree_frame.grid(row=2, column=0, sticky="nsew")
        tree_frame.rowconfigure(0, weight=1)
        tree_frame.columnconfigure(0, weight=1)
        self.jobs_tree = ttk.Treeview(tree_frame, columns=("status", "schedule"), show="tree headings", selectmode="browse", height=20)
        self.jobs_tree.heading("#0", text="Job / Folder")
        self.jobs_tree.heading("status", text="وضعیت")
        self.jobs_tree.heading("schedule", text="زمان")
        self.jobs_tree.column("#0", width=205, minwidth=165, stretch=True)
        self.jobs_tree.column("status", width=75, anchor="center", stretch=False)
        self.jobs_tree.column("schedule", width=60, anchor="center", stretch=False)
        scroll = ttk.Scrollbar(tree_frame, orient="vertical", command=self.jobs_tree.yview)
        self.jobs_tree.configure(yscrollcommand=scroll.set)
        self.jobs_tree.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        self.jobs_tree.bind("<<TreeviewSelect>>", self.select_job)

        buttons = ttk.Frame(card, style="Card.TFrame")
        buttons.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        buttons.columnconfigure(0, weight=1)
        buttons.columnconfigure(1, weight=1)
        buttons.columnconfigure(2, weight=1)
        ttk.Button(buttons, text="＋ Job جدید", command=self.new_job_ui).grid(row=0, column=0, sticky="ew", padx=(0, 4))
        ttk.Button(buttons, text="Pause / Resume", command=self.toggle_job).grid(row=0, column=1, sticky="ew", padx=4)
        ttk.Button(buttons, text="حذف Job", command=self.delete_job).grid(row=0, column=2, sticky="ew", padx=(4, 0))
        ttk.Label(card, text="حذف Job فقط Job را حذف می‌کند؛ Folder Identity و Topic باقی می‌مانند.", style="Muted.TLabel", wraplength=340).grid(row=4, column=0, sticky="w", pady=(8, 0))

    def _build_editor(self, parent):
        editor = ttk.Frame(parent, style="Card.TFrame", padding=16)
        editor.grid(row=1, column=1, sticky="nsew")
        editor.columnconfigure(1, weight=1)
        editor.rowconfigure(4, weight=1)

        ttk.Label(editor, text="تنظیمات Job", style="Title.TLabel").grid(row=0, column=0, columnspan=3, sticky="w")

        form = ttk.Frame(editor, style="Card.TFrame")
        form.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(10, 6))
        form.columnconfigure(1, weight=1)
        form.columnconfigure(3, weight=1)
        self._field(form, 0, 0, "نام Job", self.job_name)
        self._field(form, 0, 2, "زمان روزانه", self.schedule)
        self._field(form, 1, 0, "فولدر", self.folder, button=("انتخاب فولدر", self.choose_folder))
        self._field(form, 1, 2, "نام Topic", self.main_topic)
        self._field(form, 2, 0, "چت مقصد", self.chat_id)
        ttk.Button(form, text="بارگذاری چت‌ها", command=self.load_chats).grid(row=2, column=3, sticky="e", padx=(6, 0))

        options = ttk.LabelFrame(editor, text="رفتار Backup", padding=10)
        options.grid(row=2, column=0, columnspan=3, sticky="ew", pady=6)
        ttk.Radiobutton(options, text="کل فولدر", value="ALL", variable=self.backup_mode, command=self.update_file_area).pack(side="left", padx=(0, 14))
        ttk.Radiobutton(options, text="فایل‌های انتخابی", value="SELECTED", variable=self.backup_mode, command=self.update_file_area).pack(side="left", padx=(0, 18))
        ttk.Checkbutton(options, text="جایگذاری نسخه قبلی", variable=self.replace_files).pack(side="left", padx=(0, 18))
        ttk.Checkbutton(options, text="History مرکزی", variable=self.history_enabled).pack(side="left", padx=(0, 18))
        ttk.Checkbutton(options, text="فعال برای Scheduler", variable=self.enabled).pack(side="left")

        files = ttk.LabelFrame(editor, text="فایل‌های Backup", padding=12)
        files.grid(row=3, column=0, columnspan=3, sticky="ew", pady=6)
        files.columnconfigure(0, weight=1)
        ttk.Label(files, textvariable=self.file_summary, style="Muted.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Button(files, text="انتخاب / مدیریت فایل‌ها", command=self.open_file_selector).grid(row=0, column=1, padx=(10, 0))
        ttk.Button(files, text="انتخاب همه", command=lambda: self.set_all_selected(True)).grid(row=0, column=2, padx=(6, 0))
        ttk.Button(files, text="پاک کردن انتخاب‌ها", command=lambda: self.set_all_selected(False)).grid(row=0, column=3, padx=(6, 0))

        info = ttk.Frame(editor, style="Card.TFrame")
        info.grid(row=4, column=0, columnspan=3, sticky="nsew", pady=(8, 0))
        info.columnconfigure(0, weight=1)
        info.rowconfigure(0, weight=1)
        self.preview = tk.Text(info, height=9, state="disabled", wrap="word", relief="flat", bg="#f7f9fb", padx=10, pady=8, font=("Consolas", 9))
        self.preview.grid(row=0, column=0, sticky="nsew")

        actions = ttk.Frame(editor, style="Card.TFrame")
        actions.grid(row=5, column=0, columnspan=3, sticky="ew", pady=(12, 0))
        ttk.Button(actions, text="ذخیره تغییرات", command=self.save_current_job).pack(side="left")
        ttk.Button(actions, text="ساخت / اتصال Topic", command=self.prepare_topics_ui).pack(side="left", padx=6)
        ttk.Button(actions, text="▶  اجرای همین Job", style="Primary.TButton", command=self.start_backup).pack(side="left", padx=6)
        ttk.Button(actions, text="توقف", command=self.stop_scheduler).pack(side="left")
        ttk.Button(actions, text="شروع Scheduler همه Jobهای فعال", command=self.start_scheduler).pack(side="right")

    def _field(self, parent, row, label_col, label, variable, button=None):
        ttk.Label(parent, text=label, style="Muted.TLabel").grid(row=row, column=label_col, sticky="w", padx=(0, 8), pady=5)
        entry_col = label_col + 1
        ttk.Entry(parent, textvariable=variable).grid(row=row, column=entry_col, sticky="ew", pady=5)
        if button:
            ttk.Button(parent, text=button[0], command=button[1]).grid(row=row, column=entry_col + 1, padx=(6, 0), pady=5)

    def _build_statusbar(self, parent):
        bar = ttk.Frame(parent, style="Card.TFrame", padding=(12, 8))
        bar.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        bar.columnconfigure(1, weight=1)
        ttk.Label(bar, text="وضعیت:", style="Muted.TLabel").grid(row=0, column=0, padx=(0, 8))
        ttk.Label(bar, textvariable=self.status).grid(row=0, column=1, sticky="w")
        ttk.Progressbar(bar, variable=self.progress, maximum=100).grid(row=0, column=2, sticky="ew", padx=12)

    def refresh_jobs(self, select_index=None):
        for item in self.jobs_tree.get_children():
            self.jobs_tree.delete(item)
        for index, job in enumerate(self.jobs):
            status = "فعال" if job.get("enabled", True) else "Pause"
            folder = Path(job.get("folder", "")).name or job.get("name", "Job")
            self.jobs_tree.insert("", "end", iid=str(index), text=f"{job.get('name', folder)}\n{folder}", values=(status, job.get("schedule", "23:00")))
        if self.jobs:
            index = 0 if select_index is None else min(select_index, len(self.jobs) - 1)
            self.jobs_tree.selection_set(str(index))
            self.jobs_tree.focus(str(index))
            self.select_job()
        else:
            self.new_job_ui()

    def _selected_index(self):
        selected = self.jobs_tree.selection()
        if not selected:
            return None
        try:
            return int(selected[0])
        except ValueError:
            return None

    def _current_job(self):
        index = self._selected_index()
        return self.jobs[index] if index is not None and index < len(self.jobs) else None

    def new_job_ui(self):
        self.jobs_tree.selection_remove(self.jobs_tree.selection())
        self.job_name.set("")
        self.folder.set("")
        self.main_topic.set("")
        self.schedule.set("23:00")
        self.destination.set("topic")
        self.chat_id.set(self.chat_id.get())
        self.enabled.set(True)
        self.backup_mode.set("ALL")
        self.replace_files.set(True)
        self.history_enabled.set(True)
        self.file_vars = {}
        self.file_summary.set("ابتدا فولدر را انتخاب کنید")
        self._set_preview([])
        self.status.set("Job جدید آماده است")

    def select_job(self, _event=None):
        job = self._current_job()
        if not job:
            return
        self.job_name.set(job.get("name", ""))
        self.folder.set(job.get("folder", ""))
        self.chat_id.set(str(job.get("chat_id", self.chat_id.get())))
        self.main_topic.set(job.get("main_topic_name", job.get("name", "")))
        self.schedule.set(job.get("schedule", "23:00"))
        self.enabled.set(job.get("enabled", True))
        self.destination.set(job.get("destination", "topic"))
        self.backup_mode.set(job.get("backup_mode", "ALL"))
        self.replace_files.set(job.get("replace_files", True))
        self.history_enabled.set(job.get("history_enabled", True))
        self.update_file_area()
        self.status.set(f"Job انتخاب شد: {job.get('name', 'Job')}")

    def choose_folder(self):
        selected = filedialog.askdirectory(title="انتخاب فولدر پشتیبان")
        if not selected:
            return
        self.folder.set(str(Path(selected).resolve()))
        if not self.job_name.get().strip():
            self.job_name.set(Path(selected).name)
        if not self.main_topic.get().strip():
            self.main_topic.set(Path(selected).name)
        self.update_file_area()

    def update_file_area(self):
        folder = self.folder.get().strip()
        if not folder or not Path(folder).is_dir():
            self.file_summary.set("فولدر معتبر نیست")
            self._set_preview([])
            return
        job = self._current_job()
        selected = set(job.get("selected_files", [])) if job else set()
        files = current_files(folder, STATE_FILES)
        self.file_vars = {}
        for path in files:
            self.file_vars[str(path)] = tk.BooleanVar(value=str(path.resolve()) in selected)
        if self.backup_mode.get() == "ALL":
            self.file_summary.set(f"کل فولدر • {len(files)} فایل موجود")
        else:
            count = sum(var.get() for var in self.file_vars.values())
            self.file_summary.set(f"{count} فایل از {len(files)} فایل انتخاب شده")
        self._set_preview(files)

    def _set_preview(self, files):
        self.preview.configure(state="normal")
        self.preview.delete("1.0", "end")
        if not files:
            self.preview.insert("end", "برای دیدن فایل‌ها، یک فولدر انتخاب کنید.")
        else:
            selected = {path for path, var in self.file_vars.items() if var.get()}
            for path in files[:250]:
                marker = "✓" if str(path) in selected else "·"
                self.preview.insert("end", f"{marker} {path.relative_to(Path(self.folder.get()))}\n")
            if len(files) > 250:
                self.preview.insert("end", f"\n... و {len(files) - 250} فایل دیگر")
        self.preview.configure(state="disabled")

    def open_file_selector(self):
        folder = self.folder.get().strip()
        if not folder or not Path(folder).is_dir():
            messagebox.showwarning("انتخاب فایل", "ابتدا یک فولدر معتبر انتخاب کنید.")
            return
        files = current_files(folder, STATE_FILES)
        if not files:
            messagebox.showinfo("انتخاب فایل", "در این فولدر فایلی پیدا نشد.")
            return

        dialog = tk.Toplevel(self.root)
        dialog.title("انتخاب فایل‌های Backup")
        dialog.geometry("760x620")
        dialog.minsize(620, 480)
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.columnconfigure(0, weight=1)
        dialog.rowconfigure(2, weight=1)

        ttk.Label(dialog, text="فایل‌هایی که در حالت «فایل‌های انتخابی» پشتیبان‌گیری می‌شوند", style="Title.TLabel").grid(row=0, column=0, sticky="w", padx=16, pady=(14, 4))
        summary = tk.StringVar()
        ttk.Label(dialog, textvariable=summary, style="Muted.TLabel").grid(row=1, column=0, sticky="w", padx=16, pady=(0, 8))

        body = ttk.Frame(dialog, style="Card.TFrame", padding=8)
        body.grid(row=2, column=0, sticky="nsew", padx=16)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(1, weight=1)

        search = tk.StringVar()
        ttk.Entry(body, textvariable=search).grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ttk.Label(body, text="جست‌وجو در نام/مسیر فایل", style="Muted.TLabel").place(relx=0.01, rely=0.02)

        canvas = tk.Canvas(body, bg="#f7f9fb", highlightthickness=0)
        scrollbar = ttk.Scrollbar(body, orient="vertical", command=canvas.yview)
        inner = ttk.Frame(canvas, style="Card.TFrame")
        window_id = canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.grid(row=1, column=0, sticky="nsew")
        scrollbar.grid(row=1, column=1, sticky="ns")
        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(window_id, width=e.width))

        local_vars = {str(path): self.file_vars.get(str(path), tk.BooleanVar(value=False)) for path in files}

        def redraw(*_):
            for child in inner.winfo_children():
                child.destroy()
            query = search.get().strip().lower()
            shown = 0
            for path in files:
                relative = str(path.relative_to(Path(folder)))
                if query and query not in relative.lower():
                    continue
                ttk.Checkbutton(inner, text=relative, variable=local_vars[str(path)]).pack(anchor="w", fill="x", padx=8, pady=2)
                shown += 1
            total_selected = sum(v.get() for v in local_vars.values())
            summary.set(f"{total_selected} انتخاب از {len(files)} فایل • {shown} فایل نمایش داده شده")

        def set_all(value):
            for var in local_vars.values():
                var.set(value)
            redraw()

        search.trace_add("write", redraw)
        redraw()

        actions = ttk.Frame(dialog, padding=12)
        actions.grid(row=3, column=0, sticky="ew")
        ttk.Button(actions, text="انتخاب همه", command=lambda: set_all(True)).pack(side="left")
        ttk.Button(actions, text="پاک کردن همه", command=lambda: set_all(False)).pack(side="left", padx=6)

        def apply():
            self.file_vars = local_vars
            job = self._current_job()
            if job:
                job["selected_files"] = [path for path, var in local_vars.items() if var.get()]
            self.update_file_area()
            dialog.destroy()

        ttk.Button(actions, text="لغو", command=dialog.destroy).pack(side="right", padx=6)
        ttk.Button(actions, text="تأیید انتخاب‌ها", command=apply).pack(side="right")

    def set_all_selected(self, value):
        if not self.file_vars:
            self.update_file_area()
        for var in self.file_vars.values():
            var.set(value)
        self._set_preview([Path(path) for path in self.file_vars])
        if self.backup_mode.get() == "SELECTED":
            self.file_summary.set(f"{sum(v.get() for v in self.file_vars.values())} فایل انتخاب شده")

    def save_current_job(self):
        folder = self.folder.get().strip()
        chat_id = self.chat_id.get().strip()
        token = self.token.get().strip()
        if not folder or not Path(folder).is_dir():
            messagebox.showerror("تنظیمات ناقص", "فولدر معتبر نیست.")
            return False
        if not chat_id or not token:
            messagebox.showerror("تنظیمات ناقص", "توکن و چت مقصد را وارد کنید.")
            return False
        try:
            datetime.strptime(self.schedule.get().strip(), "%H:%M")
        except ValueError:
            messagebox.showerror("تنظیمات ناقص", "زمان باید با قالب HH:MM باشد؛ مثلاً 23:00")
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
        self.status.set("تغییرات Job ذخیره شد")
        return True

    def toggle_job(self):
        index = self._selected_index()
        if index is None:
            messagebox.showinfo("Job", "ابتدا یک Job را انتخاب کنید.")
            return
        self.jobs[index]["enabled"] = not self.jobs[index].get("enabled", True)
        save_jobs(self.jobs)
        state = "فعال" if self.jobs[index]["enabled"] else "Pause"
        self.status.set(f"Job {self.jobs[index].get('name')} → {state}")
        self.refresh_jobs(index)

    def delete_job(self):
        index = self._selected_index()
        if index is None:
            messagebox.showinfo("حذف Job", "ابتدا یک Job را از فهرست انتخاب کنید.")
            return
        job = self.jobs[index]
        answer = messagebox.askyesno(
            "حذف Job",
            f"Job «{job.get('name', 'Job')}» حذف شود؟\n\nFolder Identity، Topic اصلی و History دست‌نخورده باقی می‌مانند.",
            icon="warning",
        )
        if not answer:
            return
        self.jobs.pop(index)
        save_jobs(self.jobs)
        self.refresh_jobs(max(0, index - 1) if self.jobs else None)
        self.status.set("Job حذف شد؛ Topic و Folder Identity حفظ شدند")

    def validate_current(self):
        if not self.token.get().strip():
            raise ValueError("توکن ربات را وارد کنید.")
        if not self.folder.get().strip() or not Path(self.folder.get()).is_dir():
            raise ValueError("فولدر پشتیبان معتبر نیست.")
        if not self.chat_id.get().strip():
            raise ValueError("چت مقصد را وارد کنید.")
        datetime.strptime(self.schedule.get().strip(), "%H:%M")

    def prepare_topics_ui(self):
        try:
            self.validate_current()
            if not self.save_current_job():
                return
            job = self._current_job()
            if not job:
                return
            forum = TelegramForum(telegram_request)
            main_id, folder_record = _ensure_folder_topic(forum, self.token.get().strip(), job)
            job["main_topic_id"] = main_id
            job["folder_id"] = folder_record["id"]
            if self.history_enabled.get():
                job["history_topic_id"] = _ensure_project_history_topic(forum, self.token.get().strip(), job["chat_id"])
            save_jobs(self.jobs)
            self.status.set(f"Topic آماده است: {main_id}")
        except Exception as error:
            self.status.set("ساخت Topic ناموفق بود")
            messagebox.showerror("Telegram", str(error))

    def start_backup(self):
        try:
            self.validate_current()
            if not self.save_current_job():
                return
            job = self._current_job()
            if not job:
                return
            if self.worker and self.worker.is_alive():
                messagebox.showinfo("Backup", "یک عملیات دیگر در حال اجراست.")
                return
            self.cancel_event.clear()
            self.progress.set(0)
            self.worker = threading.Thread(target=self._run_job, args=(dict(job),), daemon=True)
            self.worker.start()
        except ValueError as error:
            messagebox.showerror("تنظیمات ناقص", str(error))

    def _run_job(self, job):
        self._ui(lambda: self.status.set(f"در حال Backup: {job.get('name', 'Job')}"))
        try:
            count, completed = run_job_backup(self.token.get().strip(), job, self._log, self._progress, self.cancel_event)
            for current in self.jobs:
                if current["id"] == job["id"]:
                    current.update(job)
            save_jobs(self.jobs)
            self._ui(lambda: self.status.set(f"Backup {'کامل شد' if completed else 'متوقف شد'} • {count} فایل"))
        except Exception as error:
            self._log(f"خطا: {error}")
            self._ui(lambda: self.status.set("Backup ناموفق بود"))

    def _progress(self, current, total):
        value = current / total * 100 if total else 100
        self._ui(lambda: self.progress.set(value))

    def _log(self, text):
        self._ui(lambda: self.status.set(text))

    def _ui(self, callback):
        try:
            self.root.after(0, callback)
        except tk.TclError:
            pass

    def start_scheduler(self):
        if self.scheduler_thread and self.scheduler_thread.is_alive():
            self.status.set("Scheduler از قبل فعال است")
            return
        if not self.jobs:
            messagebox.showinfo("Scheduler", "حداقل یک Job بسازید.")
            return
        self.stop_event.clear()
        self.scheduler_thread = threading.Thread(target=self.scheduler_loop, daemon=True)
        self.scheduler_thread.start()
        self.status.set("Scheduler فعال است؛ فقط Jobهای Active اجرا می‌شوند")

    def stop_scheduler(self):
        self.stop_event.set()
        self.cancel_event.set()
        self.status.set("Scheduler / عملیات متوقف شد")

    def scheduler_loop(self):
        last_runs: dict[str, object] = {}
        while not self.stop_event.is_set():
            now = datetime.now()
            jobs = load_and_migrate_jobs()
            for job in jobs:
                if not job.get("enabled", True) or job.get("schedule") != now.strftime("%H:%M"):
                    continue
                if last_runs.get(job["id"]) == now.date():
                    continue
                last_runs[job["id"]] = now.date()
                if self.worker and self.worker.is_alive():
                    self._log(f"Scheduler: Job رد شد چون Backup دیگری در حال اجراست: {job.get('name')}")
                    continue
                self.cancel_event.clear()
                self.worker = threading.Thread(target=self._run_job, args=(dict(job),), daemon=True)
                self.worker.start()
            self.stop_event.wait(10)

    def load_chats(self):
        token = self.token.get().strip()
        if not token:
            messagebox.showerror("خطا", "توکن ربات را وارد کنید.")
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
            self._ui(lambda: self._apply_chats(chats))
        except Exception as error:
            self._log(f"دریافت چت‌ها ناموفق بود: {error}")

    def _apply_chats(self, chats):
        self.chats = chats
        if chats:
            self.chat_id.set(next(iter(chats.values())))
            self.status.set(f"{len(chats)} چت پیدا شد")
        else:
            self.status.set("چتی از getUpdates پیدا نشد")


if __name__ == "__main__":
    root = tk.Tk()
    BackupApp(root)
    root.mainloop()
