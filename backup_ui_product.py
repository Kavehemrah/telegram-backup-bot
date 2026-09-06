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
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QFileDialog, QFormLayout, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMainWindow, QMessageBox, QPlainTextEdit,
    QProgressBar, QPushButton, QRadioButton, QSpinBox, QTabWidget,
    QVBoxLayout, QWidget,
)

from backup_bot import STATE_FILES, _ensure_folder_topic, _ensure_project_history_topic, run_job_backup, telegram_request
from backup_jobs import current_files, get_or_create_folder, load_and_migrate_jobs, new_job, save_jobs
from telegram_forum import TelegramForum

BASE_DIR = Path(__file__).resolve().parent
SETTINGS_FILE = BASE_DIR / "backup_ui_settings.json"
TASK_NAME = "TelegramFolderBackup"

T = {
    "fa": {
        "dashboard":"داشبورد", "jobs":"مدیریت Jobها", "help":"راهنما و تنظیمات",
        "title":"پشتیبان‌گیری تلگرام", "subtitle":"مدیریت Job مستقل • Topic پایدار • History مرکزی",
        "jobs_count":"تعداد Job", "active_jobs":"Job فعال", "scheduler":"Scheduler", "next":"اجرای بعدی",
        "on":"● Scheduler فعال", "off":"○ Scheduler خاموش", "new":"＋ Job جدید", "pause":"Pause / Resume",
        "delete":"حذف Job انتخاب‌شده", "name":"نام Job", "time":"زمان روزانه", "folder":"فولدر", "topic":"نام Topic", "chat":"Chat ID",
        "choose":"انتخاب فولدر", "load":"بارگذاری چت‌ها", "behavior":"رفتار Backup", "all":"کل فولدر", "selected":"فایل‌های انتخابی",
        "replace":"جایگذاری نسخه قبلی", "history":"History مرکزی", "enabled":"فعال برای Scheduler", "files":"فایل‌های Backup",
        "manage":"انتخاب / مدیریت فایل‌ها", "select_all":"انتخاب همه", "clear":"پاک کردن", "save":"ذخیره تغییرات",
        "topic_btn":"ساخت / اتصال Topic", "run":"▶ اجرای همین Job", "stop":"توقف", "start":"شروع Scheduler",
        "quick":"راهنمای شروع سریع", "setup":"وضعیت آماده‌سازی", "language":"زبان برنامه", "autostart":"اجرای خودکار هنگام ورود به Windows",
        "missed":"Job از دست‌رفته پس از روشن شدن سیستم", "never":"هرگز", "always":"اجرای اولین بررسی", "grace":"فقط تا پنجره تأخیر",
        "minutes":"حداکثر دقیقه تأخیر", "apply":"فعال‌سازی Startup ویندوز", "remove":"حذف Startup", "save_settings":"ذخیره تنظیمات",
        "configured":"✓ تنظیم شده", "not":"✗ تنظیم نشده", "help_steps":[
            "1) در Telegram با @BotFather یک Bot بسازید و Token بگیرید.",
            "2) یک Supergroup بسازید و Topics را فعال کنید.",
            "3) Bot را اضافه کنید و دسترسی مدیریت Topic و حذف پیام‌ها را بدهید.",
            "4) یک پیام در گروه بفرستید تا Chat در getUpdates دیده شود.",
            "5) «بارگذاری چت‌ها» را بزنید و Chat مقصد را انتخاب کنید.",
            "6) در مدیریت Jobها فولدر و ساعت اجرا را تنظیم کنید.",
            "7) برای Topic، یک‌بار «ساخت / اتصال Topic» را اجرا کنید.",
            "8) Scheduler را روشن کنید. با Auto-start برنامه همراه Windows اجرا می‌شود.",
        ]
    },
    "en": {
        "dashboard":"Dashboard", "jobs":"Jobs", "help":"Help & Settings", "title":"Telegram Backup",
        "subtitle":"Independent Jobs • persistent Topics • central History", "jobs_count":"Jobs", "active_jobs":"Active Jobs",
        "scheduler":"Scheduler", "next":"Next run", "on":"● Scheduler active", "off":"○ Scheduler off",
        "new":"＋ New Job", "pause":"Pause / Resume", "delete":"Delete selected Job", "name":"Job name", "time":"Daily time",
        "folder":"Folder", "topic":"Topic name", "chat":"Chat ID", "choose":"Choose folder", "load":"Load chats",
        "behavior":"Backup behavior", "all":"Entire folder", "selected":"Selected files", "replace":"Replace previous version",
        "history":"Central History", "enabled":"Enabled for Scheduler", "files":"Backup files", "manage":"Select / manage files",
        "select_all":"Select all", "clear":"Clear", "save":"Save changes", "topic_btn":"Create / connect Topic",
        "run":"▶ Run this Job", "stop":"Stop", "start":"Start Scheduler", "quick":"Quick start guide", "setup":"Setup status",
        "language":"Application language", "autostart":"Start automatically when Windows logs in", "missed":"Missed Job after startup",
        "never":"Never", "always":"Run on first check", "grace":"Only within delay window", "minutes":"Maximum delay (minutes)",
        "apply":"Enable Windows Startup", "remove":"Remove Startup", "save_settings":"Save settings", "configured":"✓ Configured", "not":"✗ Not configured",
        "help_steps":[
            "1) Create a Bot with @BotFather and copy its Token.", "2) Create a Telegram Supergroup and enable Topics.",
            "3) Add the Bot and grant permissions to manage Topics and delete messages.", "4) Send a message in the group so it appears in getUpdates.",
            "5) Click “Load chats” and select the destination Chat.", "6) Create a Job and choose its folder and daily time.",
            "7) For Topic mode, run “Create / connect Topic” once.", "8) Start Scheduler. With Auto-start enabled, the app starts with Windows.",
        ]
    }
}


def settings_load():
    base = {"language":"fa", "autostart":False, "missed_policy":"never", "missed_minutes":30, "last_runs":{}}
    try:
        data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8")) if SETTINGS_FILE.exists() else {}
        if isinstance(data, dict): base.update(data)
    except Exception:
        pass
    if not isinstance(base.get("last_runs"), dict): base["last_runs"] = {}
    return base


def settings_save(data):
    tmp = SETTINGS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(SETTINGS_FILE)


class Worker(QThread):
    progress = Signal(int); log = Signal(str); done = Signal(object); failed = Signal(str)
    def __init__(self, token, job):
        super().__init__(); self.token, self.job = token, job; self.cancel_event = threading.Event()
    def run(self):
        try:
            result = run_job_backup(self.token, self.job, self.log.emit, lambda c,t: self.progress.emit(int(c/t*100) if t else 100), self.cancel_event)
            self.done.emit((result, self.job))
        except Exception as exc: self.failed.emit(str(exc))
    def cancel(self): self.cancel_event.set()


class ChatLoader(QThread):
    loaded = Signal(object); failed = Signal(str)
    def __init__(self, token): super().__init__(); self.token = token
    def run(self):
        try:
            chats = {}
            for update in telegram_request(self.token, "getUpdates"):
                msg = update.get("message") or update.get("channel_post") or {}; chat = msg.get("chat") or {}
                if chat.get("id") is not None:
                    cid = str(chat["id"]); chats[cid] = f"{chat.get('title') or chat.get('first_name') or 'Unnamed'} ({cid})"
            self.loaded.emit(chats)
        except Exception as exc: self.failed.emit(str(exc))


class ChatDialog(QDialog):
    def __init__(self, chats, current, lang, parent=None):
        super().__init__(parent); self.lang=lang; self.value=current; self.setWindowTitle("Select Telegram chat" if lang=="en" else "انتخاب چت Telegram"); self.resize(650,540)
        v=QVBoxLayout(self); title=QLabel("Select destination chat" if lang=="en" else "چت مقصد را انتخاب کنید"); title.setObjectName("dialogTitle"); v.addWidget(title)
        self.search=QLineEdit(); self.search.setPlaceholderText("Search..." if lang=="en" else "جست‌وجو..."); v.addWidget(self.search)
        self.list=QListWidget(); v.addWidget(self.list,1)
        for cid,label in chats.items():
            it=QListWidgetItem(label); it.setData(Qt.ItemDataRole.UserRole,cid); self.list.addItem(it)
            if cid==str(current): self.list.setCurrentItem(it)
        if self.list.currentRow()<0 and self.list.count(): self.list.setCurrentRow(0)
        self.info=QLabel(); self.info.setObjectName("muted"); v.addWidget(self.info); self.filter("")
        self.search.textChanged.connect(self.filter); self.list.itemDoubleClicked.connect(lambda _:self.accept_selected())
        b=QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel|QDialogButtonBox.StandardButton.Ok); b.accepted.connect(self.accept_selected); b.rejected.connect(self.reject); v.addWidget(b)
    def filter(self,text):
        q=text.casefold().strip(); n=0
        for i in range(self.list.count()):
            it=self.list.item(i); show=not q or q in it.text().casefold(); it.setHidden(not show); n+=show
        self.info.setText(f"{n} chats" if self.lang=="en" else f"{n} چت")
    def accept_selected(self):
        it=self.list.currentItem()
        if not it or it.isHidden(): QMessageBox.warning(self,"Chat","Select a chat first." if self.lang=="en" else "یک چت را انتخاب کنید."); return
        self.value=str(it.data(Qt.ItemDataRole.UserRole)); self.accept()


class FileDialog(QDialog):
    def __init__(self, folder, files, selected, lang, parent=None):
        super().__init__(parent); self.selected=set(selected); self.checks={}; self.lang=lang; self.setWindowTitle("Select backup files" if lang=="en" else "انتخاب فایل‌های Backup"); self.resize(920,680)
        v=QVBoxLayout(self); title=QLabel("Tick the files to include in Selected files mode." if lang=="en" else "تیک فایل‌هایی را بزنید که باید در حالت فایل‌های انتخابی Backup شوند."); title.setWordWrap(True); title.setObjectName("dialogTitle"); v.addWidget(title)
        self.search=QLineEdit(); self.search.setPlaceholderText("Search file name or path..." if lang=="en" else "جست‌وجو در نام یا مسیر فایل..."); v.addWidget(self.search)
        self.list=QListWidget(); self.list.setAlternatingRowColors(True); v.addWidget(self.list,1)
        for p in files:
            full=str(p.resolve()); it=QListWidgetItem(); cb=QCheckBox(str(p.relative_to(Path(folder)))); cb.setChecked(full in self.selected); self.checks[full]=cb; self.list.addItem(it); it.setSizeHint(cb.sizeHint()); self.list.setItemWidget(it,cb); cb.stateChanged.connect(self.count)
        self.search.textChanged.connect(self.filter); self.info=QLabel(); self.info.setObjectName("muted"); v.addWidget(self.info)
        tools=QHBoxLayout(); a=QPushButton("انتخاب همه" if lang=="fa" else "Select all"); n=QPushButton("پاک کردن" if lang=="fa" else "Clear"); a.setObjectName("secondary"); n.setObjectName("secondary"); a.clicked.connect(lambda:self.set_all(True)); n.clicked.connect(lambda:self.set_all(False)); tools.addWidget(a); tools.addWidget(n); tools.addStretch(); v.addLayout(tools)
        b=QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel|QDialogButtonBox.StandardButton.Ok); b.accepted.connect(self.accept_selected); b.rejected.connect(self.reject); v.addWidget(b); self.count()
    def filter(self,text):
        q=text.casefold().strip()
        for i in range(self.list.count()): self.list.item(i).setHidden(bool(q) and q not in self.list.itemWidget(self.list.item(i)).text().casefold())
    def set_all(self,value):
        for cb in self.checks.values(): cb.setChecked(value)
        self.count()
    def count(self,*_): self.info.setText(f"{sum(c.isChecked() for c in self.checks.values())} / {len(self.checks)}")
    def accept_selected(self): self.selected={p for p,c in self.checks.items() if c.isChecked()}; self.accept()


class BackupApp(QMainWindow):
    def __init__(self):
        super().__init__(); self.s=settings_load(); self.lang=self.s.get("language","fa") if self.s.get("language") in T else "fa"; self.jobs=load_and_migrate_jobs(); self.worker=None; self.loader=None; self.new_mode=False; self.pending_selected=[]; self.token=os.getenv("TELEGRAM_BOT_TOKEN",""); self.sched=False; self.timer=QTimer(self); self.timer.setInterval(10000); self.timer.timeout.connect(self.scheduler_tick); self.build(); self.apply_lang(); self.reload_jobs()
        if self.s.get("autostart"): self.start_scheduler(True)

    def tr(self,k): return T[self.lang][k]
    def build(self):
        c=QWidget(); root=QVBoxLayout(c); root.setContentsMargins(18,18,18,18); root.setSpacing(12); self.setCentralWidget(c)
        h=QFrame(); h.setObjectName("header"); hv=QVBoxLayout(h); hv.setContentsMargins(22,15,22,15); self.ht=QLabel(); self.ht.setObjectName("headerTitle"); self.hs=QLabel(); self.hs.setObjectName("headerSubtitle"); hv.addWidget(self.ht); hv.addWidget(self.hs); root.addWidget(h)
        self.tabs=QTabWidget(); self.tab_dash=self.dashboard_tab(); self.tab_jobs=self.jobs_tab(); self.tab_help=self.help_tab(); self.tabs.addTab(self.tab_dash,""); self.tabs.addTab(self.tab_jobs,""); self.tabs.addTab(self.tab_help,""); root.addWidget(self.tabs,1)
        st=QFrame(); st.setObjectName("status"); sv=QHBoxLayout(st); self.status=QLabel(); self.progress=QProgressBar(); self.progress.setTextVisible(False); self.progress.setMaximumWidth(420); sv.addWidget(self.status,1); sv.addWidget(self.progress); root.addWidget(st)
    def dashboard_tab(self):
        w=QWidget(); v=QVBoxLayout(w); cards=QHBoxLayout(); self.metrics=[]
        for _ in range(4):
            f=QFrame(); f.setObjectName("metric"); x=QVBoxLayout(f); a=QLabel(); a.setObjectName("metricTitle"); b=QLabel("-"); b.setObjectName("metricValue"); x.addWidget(a); x.addWidget(b); f.title=f._title=a; f.value=f._value=b; cards.addWidget(f,1); self.metrics.append(f)
        v.addLayout(cards); box=QFrame(); box.setObjectName("softCard"); x=QVBoxLayout(box); self.dtitle=QLabel(); self.dtitle.setObjectName("sectionTitle"); self.dtext=QPlainTextEdit(); self.dtext.setReadOnly(True); x.addWidget(self.dtitle); x.addWidget(self.dtext,1); r=QHBoxLayout(); self.d_sched=QPushButton(); self.d_sched.setObjectName("scheduler"); self.d_sched.clicked.connect(self.toggle_scheduler); self.d_jobs=QPushButton(); self.d_jobs.setObjectName("primary"); self.d_jobs.clicked.connect(lambda:self.tabs.setCurrentIndex(1)); r.addWidget(self.d_sched); r.addWidget(self.d_jobs); r.addStretch(); x.addLayout(r); v.addWidget(box,1); return w
    def jobs_tab(self):
        w=QWidget(); root=QHBoxLayout(w); left=QFrame(); left.setObjectName("card"); lv=QVBoxLayout(left); self.jtitle=QLabel(); self.jtitle.setObjectName("sectionTitle"); self.jinfo=QLabel(); self.jinfo.setWordWrap(True); self.jinfo.setObjectName("muted"); self.jobs_list=QListWidget(); self.jobs_list.currentRowChanged.connect(self.select_job); lv.addWidget(self.jtitle); lv.addWidget(self.jinfo); lv.addWidget(self.jobs_list,1); br=QHBoxLayout(); self.newb=QPushButton(); self.newb.setObjectName("primary"); self.pb=QPushButton(); self.pb.setObjectName("secondary"); self.newb.clicked.connect(self.new_job); self.pb.clicked.connect(self.toggle_job); br.addWidget(self.newb,1); br.addWidget(self.pb,1); lv.addLayout(br); self.delb=QPushButton(); self.delb.setObjectName("danger"); self.delb.clicked.connect(self.delete_job); lv.addWidget(self.delb); root.addWidget(left,1)
        right=QFrame(); right.setObjectName("card"); rv=QVBoxLayout(right); self.et=QLabel(); self.et.setObjectName("sectionTitle"); rv.addWidget(self.et); form=QFormLayout(); self.name=QLineEdit(); self.time=QLineEdit("23:00"); self.folder=QLineEdit(); self.folder.setReadOnly(True); self.topic=QLineEdit(); self.chat=QLineEdit(); fb=QPushButton(); fb.setObjectName("secondary"); fb.clicked.connect(self.choose_folder); fr=QHBoxLayout(); fr.addWidget(self.folder,1); fr.addWidget(fb); self.fb=fb; lb=QPushButton(); lb.setObjectName("secondary"); lb.clicked.connect(self.load_chats); cr=QHBoxLayout(); cr.addWidget(self.chat,1); cr.addWidget(lb); self.lb=lb; self.labs=[]
        for text,field in (("",self.name),("",self.time),("",fr),("",self.topic),("",cr)): form.addRow(text,field); self.labs.append(form.labelForField(field.itemAt(0).widget() if hasattr(field,"itemAt") else field))
        rv.addLayout(form); be=QFrame(); be.setObjectName("softCard"); bv=QVBoxLayout(be); self.bt=QLabel(); self.bt.setObjectName("subTitle"); bv.addWidget(self.bt); mr=QHBoxLayout(); self.all=QRadioButton(); self.sel=QRadioButton(); self.all.setChecked(True); self.all.toggled.connect(self.update_files); mr.addWidget(self.all); mr.addWidget(self.sel); mr.addStretch(); bv.addLayout(mr); checks=QHBoxLayout(); self.rep=QCheckBox(); self.hist=QCheckBox(); self.en=QCheckBox(); self.rep.setChecked(True); self.hist.setChecked(True); self.en.setChecked(True); [checks.addWidget(x) for x in (self.rep,self.hist,self.en)]; checks.addStretch(); bv.addLayout(checks); rv.addWidget(be)
        ff=QFrame(); ff.setObjectName("softCard"); fv=QVBoxLayout(ff); self.ft=QLabel(); self.ft.setObjectName("subTitle"); fv.addWidget(self.ft); rr=QHBoxLayout(); self.fs=QLabel(); self.fs.setObjectName("muted"); self.manage=QPushButton(); self.manage.setObjectName("secondary"); self.sa=QPushButton(); self.sa.setObjectName("secondary"); self.clear=QPushButton(); self.clear.setObjectName("secondary"); self.manage.clicked.connect(self.open_files); self.sa.clicked.connect(lambda:self.set_all(True)); self.clear.clicked.connect(lambda:self.set_all(False)); rr.addWidget(self.fs,1); [rr.addWidget(x) for x in (self.manage,self.sa,self.clear)]; fv.addLayout(rr); self.preview=QPlainTextEdit(); self.preview.setReadOnly(True); fv.addWidget(self.preview,1); rv.addWidget(ff,1)
        ar=QHBoxLayout(); self.saveb=QPushButton(); self.saveb.setObjectName("primary"); self.runb=QPushButton(); self.runb.setObjectName("run"); self.topb=QPushButton(); self.topb.setObjectName("secondary"); self.stopb=QPushButton(); self.stopb.setObjectName("danger"); self.schb=QPushButton(); self.schb.setObjectName("scheduler"); self.saveb.clicked.connect(self.save); self.runb.clicked.connect(self.start_backup); self.topb.clicked.connect(self.prepare_topic); self.stopb.clicked.connect(self.stop_all); self.schb.clicked.connect(self.toggle_scheduler); [ar.addWidget(x) for x in (self.saveb,self.runb,self.topb,self.stopb)]; ar.addStretch(); ar.addWidget(self.schb); rv.addLayout(ar); root.addWidget(right,2); return w
    def help_tab(self):
        w=QWidget(); h=QHBoxLayout(w); box=QFrame(); box.setObjectName("card"); v=QVBoxLayout(box); self.h_title=QLabel(); self.h_title.setObjectName("sectionTitle"); self.h_text=QPlainTextEdit(); self.h_text.setReadOnly(True); self.h_note=QLabel(); self.h_note.setWordWrap(True); self.h_note.setObjectName("muted"); v.addWidget(self.h_title); v.addWidget(self.h_text,1); v.addWidget(self.h_note); h.addWidget(box,2)
        s=QFrame(); s.setObjectName("card"); sv=QVBoxLayout(s); self.setup_title=QLabel(); self.setup_title.setObjectName("sectionTitle"); self.ts=QLabel(); self.cs=QLabel(); self.js=QLabel(); [sv.addWidget(x) for x in (self.setup_title,self.ts,self.cs,self.js)]; self.ll=QLabel(); self.ll.setObjectName("subTitle"); self.langbox=QComboBox(); self.langbox.addItem("فارسی","fa"); self.langbox.addItem("English","en"); self.langbox.currentIndexChanged.connect(self.change_lang); sv.addWidget(self.ll); sv.addWidget(self.langbox); self.auto=QCheckBox(); self.auto.setChecked(self.s.get("autostart",False)); sv.addWidget(self.auto); self.ml=QLabel(); self.ml.setObjectName("subTitle"); sv.addWidget(self.ml); self.mc=QComboBox(); sv.addWidget(self.mc); self.min=QSpinBox(); self.min.setRange(1,1440); self.min.setValue(int(self.s.get("missed_minutes",30))); sv.addWidget(self.min); self.ab=QPushButton(); self.ab.setObjectName("primary"); self.ab.clicked.connect(self.autostart); sv.addWidget(self.ab); self.rb=QPushButton(); self.rb.setObjectName("danger"); self.rb.clicked.connect(self.remove_autostart); sv.addWidget(self.rb); self.sb=QPushButton(); self.sb.setObjectName("secondary"); self.sb.clicked.connect(self.save_settings); sv.addWidget(self.sb); sv.addStretch(); h.addWidget(s,1); return w
    def apply_lang(self):
        t=self.tr(); self.setWindowTitle(t["title"]); self.ht.setText(t["title"]); self.hs.setText(t["subtitle"]); self.tabs.setTabText(0,t["dashboard"]); self.tabs.setTabText(1,t["jobs"]); self.tabs.setTabText(2,t["help"]); self.jtitle.setText(t["jobs"]); self.jinfo.setText("حذف Job، Folder Identity و Topic را حذف نمی‌کند." if self.lang=="fa" else "Deleting a Job does not delete its Folder Identity or Topic."); self.et.setText(t["jobs"]+" / "+("تنظیمات" if self.lang=="fa" else "Settings")); self.fb.setText(t["choose"]); self.lb.setText(t["load"]); self.bt.setText(t["behavior"]); self.all.setText(t["all"]); self.sel.setText(t["selected"]); self.rep.setText(t["replace"]); self.hist.setText(t["history"]); self.en.setText(t["enabled"]); self.ft.setText(t["files"]); self.manage.setText(t["manage"]); self.sa.setText(t["select_all"]); self.clear.setText(t["clear"]); self.saveb.setText(t["save"]); self.runb.setText(t["run"]); self.topb.setText(t["topic_btn"]); self.stopb.setText(t["stop"]); self.schb.setText(t["off"] if self.sched else t["start"]); self.newb.setText(t["new"]); self.pb.setText(t["pause"]); self.delb.setText(t["delete"]); [x.setText(y) for x,y in zip(self.labs,[t["name"],t["time"],t["folder"],t["topic"],t["chat"]])]; self.h_title.setText(t["quick"]); self.h_text.setPlainText("\n".join(t["help_steps"])); self.h_note.setText("Windows must be running for the built-in Scheduler." if self.lang=="en" else "برای اجرای داخلی Scheduler، ویندوز و برنامه باید در حال اجرا باشند."); self.setup_title.setText(t["setup"]); self.ll.setText(t["language"]); self.ml.setText(t["missed"]); self.mc.clear(); self.mc.addItem(t["never"],"never"); self.mc.addItem(t["always"],"always"); self.mc.addItem(t["grace"],"grace"); self.mc.setCurrentIndex(max(0,self.mc.findData(self.s.get("missed_policy","never")))); self.ab.setText(t["apply"]); self.rb.setText(t["remove"]); self.sb.setText(t["save_settings"]); self.update_dashboard(); self.setup_status()
    def change_lang(self):
        x=self.langbox.currentData();
        if x in T and x!=self.lang: self.lang=x; self.s["language"]=x; settings_save(self.s); self.apply_lang()
    def reload_jobs(self,row=None):
        self.jobs_list.blockSignals(True); self.jobs_list.clear()
        for j in self.jobs:
            p=Path(j.get("folder","")).name or "No folder"; state=self.tr("active") if j.get("enabled",True) else self.tr("paused"); m=self.tr("selected") if j.get("backup_mode")=="SELECTED" else self.tr("all"); self.jobs_list.addItem(QListWidgetItem(f"{j.get('name',p)}\n{p} • {state} • {j.get('schedule','23:00')} • {m}"))
        self.jobs_list.blockSignals(False)
        if self.jobs:self.jobs_list.setCurrentRow(0 if row is None else min(row,len(self.jobs)-1))
        else:self.new_job()
        self.update_dashboard()
    def select_job(self,row):
        if row<0 or row>=len(self.jobs):return
        j=self.jobs[row]; self.new_mode=False; self.name.setText(j.get("name","")); self.folder.setText(j.get("folder","")); self.topic.setText(j.get("main_topic_name",j.get("name",""))); self.chat.setText(str(j.get("chat_id",""))); self.time.setText(j.get("schedule","23:00")); self.en.setChecked(j.get("enabled",True)); self.rep.setChecked(j.get("replace_files",True)); self.hist.setChecked(j.get("history_enabled",True)); self.sel.setChecked(j.get("backup_mode","ALL")=="SELECTED"); self.all.setChecked(not self.sel.isChecked()); self.update_files()
    def new_job(self):
        self.jobs_list.clearSelection(); self.new_mode=True; self.pending_selected=[]; [x.clear() for x in (self.name,self.folder,self.topic,self.chat)]; self.chat.setText(os.getenv("TELEGRAM_CHAT_ID","")); self.time.setText("23:00"); self.all.setChecked(True); self.rep.setChecked(True); self.hist.setChecked(True); self.en.setChecked(True); self.preview.clear(); self.fs.setText("ابتدا فولدر را انتخاب کنید" if self.lang=="fa" else "Choose a folder")
    def choose_folder(self):
        f=QFileDialog.getExistingDirectory(self,self.tr("choose"));
        if not f:return
        p=str(Path(f).resolve()); cur=self._current_job()
        if cur and not self.new_mode and p!=str(Path(cur.get("folder","")).resolve()):
            b=QMessageBox(self); b.setWindowTitle(self.tr("folder")); b.setText("This Job already exists." if self.lang=="en" else "این Job از قبل وجود دارد."); a=b.addButton("Edit this Job" if self.lang=="en" else "ویرایش همین Job",QMessageBox.ButtonRole.AcceptRole); n=b.addButton(self.tr("new"),QMessageBox.ButtonRole.ActionRole); b.addButton("Cancel" if self.lang=="en" else "لغو",QMessageBox.ButtonRole.RejectRole); b.exec(); c=b.clickedButton();
            if c is n:self.new_job()
            elif c is not a:return
        self.folder.setText(p); self.name.setText(self.name.text().strip() or Path(p).name); self.topic.setText(self.topic.text().strip() or Path(p).name); self.update_files()
    def _current_job(self):
        r=self.jobs_list.currentRow(); return self.jobs[r] if 0<=r<len(self.jobs) else None
    def _selected(self): return set(self.pending_selected) if self.new_mode else {str(Path(p).resolve()) for p in (self._current_job() or {}).get("selected_files",[])}
    def update_files(self):
        f=self.folder.text().strip();
        if not f or not Path(f).is_dir(): self.preview.clear(); return
        files=current_files(f,STATE_FILES); selected=self._selected(); chosen=sum(str(p.resolve()) in selected for p in files); self.fs.setText(f"{chosen}/{len(files)} selected" if self.lang=="en" and self.sel.isChecked() else (f"{chosen} فایل از {len(files)} فایل انتخاب شده" if self.sel.isChecked() else (f"Entire folder • {len(files)} files" if self.lang=="en" else f"کل فولدر • {len(files)} فایل موجود"))); self.preview.setPlainText("\n".join(("✓  " if str(p.resolve()) in selected else "·  ")+str(p.relative_to(Path(f))) for p in files[:500]))
    def open_files(self):
        f=self.folder.text().strip();
        if not f or not Path(f).is_dir(): QMessageBox.warning(self,self.tr("files"),"Choose a valid folder first." if self.lang=="en" else "ابتدا یک فولدر معتبر انتخاب کنید."); return
        files=current_files(f,STATE_FILES); d=FileDialog(f,files,self._selected(),self.lang,self); 
        if d.exec()==QDialog.DialogCode.Accepted:
            if self.new_mode:self.pending_selected=sorted(d.selected)
            elif self._current_job():self._current_job()["selected_files"]=sorted(d.selected)
            self.update_files()
    def set_all(self,v):
        f=self.folder.text().strip();
        if not f or not Path(f).is_dir():return
        s=[str(p.resolve()) for p in current_files(f,STATE_FILES)] if v else []
        if self.new_mode:self.pending_selected=s
        elif self._current_job():self._current_job()["selected_files"]=s
        self.update_files()
    def validate(self):
        if not self.folder.text().strip() or not Path(self.folder.text()).is_dir():raise ValueError("Invalid folder" if self.lang=="en" else "فولدر معتبر نیست")
        if not(self.token.strip() or os.getenv("TELEGRAM_BOT_TOKEN")):raise ValueError("TELEGRAM_BOT_TOKEN is missing" if self.lang=="en" else "TELEGRAM_BOT_TOKEN تنظیم نشده است")
        if not self.chat.text().strip():raise ValueError("Chat ID is missing" if self.lang=="en" else "Chat ID وارد نشده است")
        datetime.strptime(self.time.text().strip(),"%H:%M")
    def save(self):
        try:self.validate()
        except ValueError as e:QMessageBox.critical(self,self.tr("save"),str(e));return False
        row=self.jobs_list.currentRow(); f=str(Path(self.folder.text()).resolve()); selected=sorted(self._selected())
        if self.new_mode or row<0:self.jobs.append(new_job(f,self.chat.text().strip(),self.time.text().strip())); row=len(self.jobs)-1
        j=self.jobs[row]; r=get_or_create_folder(f); j.update({"name":self.name.text().strip() or Path(f).name,"folder":f,"folder_id":r["id"],"chat_id":self.chat.text().strip(),"destination":"topic","main_topic_name":self.topic.text().strip() or Path(f).name,"schedule":self.time.text().strip(),"enabled":self.en.isChecked(),"backup_mode":"SELECTED" if self.sel.isChecked() else "ALL","selected_files":selected,"replace_files":self.rep.isChecked(),"history_enabled":self.hist.isChecked()});
        if r.get("topic_id"):j["main_topic_id"]=r["topic_id"]
        save_jobs(self.jobs); self.new_mode=False; self.pending_selected=[]; self.reload_jobs(row); self.jobs_list.setCurrentRow(row); return True
    def toggle_job(self):
        r=self.jobs_list.currentRow();
        if r<0:return
        self.jobs[r]["enabled"]=not self.jobs[r].get("enabled",True); save_jobs(self.jobs); self.reload_jobs(r)
    def delete_job(self):
        r=self.jobs_list.currentRow();
        if r<0:return
        j=self.jobs[r]; q=QMessageBox.question(self,self.tr("delete"),f"Delete {j.get('name','Job')}?" if self.lang=="en" else f"Job «{j.get('name','Job')}» حذف شود؟\nFolder Identity و Topic حذف نمی‌شوند.",QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,QMessageBox.StandardButton.No)
        if q==QMessageBox.StandardButton.Yes:del self.jobs[r];save_jobs(self.jobs);self.reload_jobs()
    def load_chats(self):
        token=self.token.strip() or os.getenv("TELEGRAM_BOT_TOKEN","");
        if not token:return
        self.lb.setEnabled(False); self.loader=ChatLoader(token); self.loader.loaded.connect(self.apply_chats); self.loader.failed.connect(lambda e:QMessageBox.critical(self,"Telegram",e)); self.loader.finished.connect(lambda:self.lb.setEnabled(True)); self.loader.start()
    def apply_chats(self,chats):
        if not chats:QMessageBox.information(self,"Telegram","No chats found." if self.lang=="en" else "چتی پیدا نشد.");return
        d=ChatDialog(chats,self.chat.text().strip(),self.lang,self); 
        if d.exec()==QDialog.DialogCode.Accepted:self.chat.setText(d.value)
    def prepare_topic(self):
        if not self.save():return
        j=self._current_job();
        try:
            f=TelegramForum(telegram_request); mid,fr=_ensure_folder_topic(f,self.token.strip() or os.getenv("TELEGRAM_BOT_TOKEN",""),j); j["main_topic_id"]=mid; j["folder_id"]=fr["id"]; j["history_topic_id"]=_ensure_project_history_topic(f,self.token.strip() or os.getenv("TELEGRAM_BOT_TOKEN",""),j["chat_id"]) if j.get("history_enabled",True) else None; save_jobs(self.jobs); QMessageBox.information(self,"Telegram",f"Topic ready: {mid}" if self.lang=="en" else f"Topic آماده شد: {mid}")
        except Exception as e:QMessageBox.critical(self,"Telegram",str(e))
    def start_backup(self):
        if self.worker and self.worker.isRunning():return
        if not self.save():return
        j=self._current_job(); self.progress.setValue(0); self.worker=Worker(self.token.strip() or os.getenv("TELEGRAM_BOT_TOKEN",""),dict(j)); self.worker.progress.connect(self.progress.setValue); self.worker.log.connect(self.status.setText); self.worker.done.connect(self.backup_done); self.worker.failed.connect(lambda e:QMessageBox.critical(self,"Backup",e)); self.worker.start()
    def backup_done(self,result):
        (count,done),j=result; self.s.setdefault("last_runs",{})[j["id"]]=datetime.now().isoformat(timespec="seconds"); settings_save(self.s);save_jobs(self.jobs);self.progress.setValue(100 if done else self.progress.value());self.update_dashboard()
    def stop_all(self):
        if self.worker and self.worker.isRunning():self.worker.cancel()
        self.timer.stop();self.sched=False;self.update_dashboard()
    def toggle_scheduler(self):self.stop_all() if self.sched else self.start_scheduler()
    def start_scheduler(self,silent=False):
        self.sched=True;self.timer.start();self.scheduler_tick();self.update_dashboard();
        if not silent:self.status.setText(self.tr("on"))
    def scheduler_tick(self):
        if not self.sched or (self.worker and self.worker.isRunning()):self.update_dashboard();return
        now=datetime.now();today=now.date().isoformat();policy=self.s.get("missed_policy","never"); grace=int(self.s.get("missed_minutes",30))
        for j in load_and_migrate_jobs():
            if not j.get("enabled",True):continue
            try:hh,mm=map(int,j.get("schedule","23:00").split(":"));target=now.replace(hour=hh,minute=mm,second=0,microsecond=0)
            except ValueError:continue
            last=self.s.setdefault("last_runs",{}).get(j.get("id"),"");
            if last.startswith(today) or now<target:continue
            age=(now-target).total_seconds()/60
            if policy=="never" and age>1:continue
            if policy=="grace" and age>grace:continue
            self.worker=Worker(self.token.strip() or os.getenv("TELEGRAM_BOT_TOKEN",""),dict(j));self.worker.progress.connect(self.progress.setValue);self.worker.log.connect(self.status.setText);self.worker.done.connect(self.backup_done);self.worker.failed.connect(lambda e:QMessageBox.critical(self,"Backup",e));self.worker.start();break
        self.update_dashboard()
    def next_run(self):
        now=datetime.now(); c=[]
        for j in self.jobs:
            if not j.get("enabled",True):continue
            try:hh,mm=map(int,j.get("schedule","23:00").split(":"));t=now.replace(hour=hh,minute=mm,second=0,microsecond=0);t=t+timedelta(days=1) if t<=now else t;c.append((t,j))
            except ValueError:pass
        return min(c,key=lambda x:x[0]) if c else (None,None)
    def update_dashboard(self):
        active=sum(bool(j.get("enabled",True)) for j in self.jobs); t,j=self.next_run(); nxt=(f"{t:%Y-%m-%d %H:%M} • {j.get('name')}" if t else self.tr("none") if "none" in T[self.lang] else "-"); titles=[self.tr("jobs_count"),self.tr("active_jobs"),self.tr("scheduler"),self.tr("next")];vals=[str(len(self.jobs)),str(active),"ON" if self.sched else "OFF",nxt];
        for m,a,b in zip(self.metrics,titles,vals):m.title.setText(a);m.value.setText(b)
        self.dtitle.setText(self.tr("on") if self.sched else self.tr("off"));self.dtext.setPlainText(("Scheduler checks active Jobs every 10 seconds." if self.lang=="en" else "Scheduler هر ۱۰ ثانیه Jobهای فعال را بررسی می‌کند."));self.d_sched.setText(self.tr("off") if self.sched else self.tr("start"));self.d_jobs.setText(self.tr("jobs"));self.schb.setText(self.tr("off") if self.sched else self.tr("start"))
    def setup_status(self):
        tok=bool(self.token.strip() or os.getenv("TELEGRAM_BOT_TOKEN")); self.ts.setText(f"Bot Token: {self.tr('configured') if tok else self.tr('not')}");self.cs.setText(f"Chat: {self.tr('configured') if any(j.get('chat_id') for j in self.jobs) else self.tr('not')}");self.js.setText(f"{self.tr('active_jobs')}: {sum(bool(j.get('enabled',True)) for j in self.jobs)}")
    def save_settings(self):
        self.s.update({"language":self.lang,"autostart":self.auto.isChecked(),"missed_policy":self.mc.currentData(),"missed_minutes":self.min.value()});settings_save(self.s);self.status.setText("Settings saved." if self.lang=="en" else "تنظیمات ذخیره شد.")
    def autostart(self):
        self.s["autostart"]=True;settings_save(self.s);cmd=f'"{sys.executable}" "{str((BASE_DIR/"run_backup_ui.py").resolve())}"';p=subprocess.run(["schtasks","/Create","/SC","ONLOGON","/TN",TASK_NAME,"/TR",cmd,"/F"],capture_output=True,text=True,creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0));
        if p.returncode!=0:self.s["autostart"]=False;settings_save(self.s);QMessageBox.critical(self,"Windows",p.stderr.strip() or p.stdout.strip())
        else:self.auto.setChecked(True);self.status.setText("Auto-start enabled." if self.lang=="en" else "اجرای خودکار فعال شد.")
    def remove_autostart(self):
        subprocess.run(["schtasks","/Delete","/TN",TASK_NAME,"/F"],capture_output=True,creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0));self.s["autostart"]=False;settings_save(self.s);self.auto.setChecked(False);self.status.setText("Auto-start removed." if self.lang=="en" else "اجرای خودکار حذف شد.")
    def closeEvent(self,e):
        if self.worker and self.worker.isRunning():self.worker.cancel();self.worker.wait(1000)
        if self.loader and self.loader.isRunning():self.loader.wait(1000)
        e.accept()


def apply_style(app):
    app.setStyleSheet("""
    QWidget{font-family:'Segoe UI';font-size:10pt;color:#253746} QMainWindow{background:#eef3f7}
    QFrame#header{background:#173b5b;border-radius:12px} QLabel#headerTitle{color:white;font-size:20pt;font-weight:700} QLabel#headerSubtitle{color:#cfe0ed}
    QFrame#card,QFrame#metric,QFrame#status{background:white;border:1px solid #d5dfe7;border-radius:12px} QFrame#softCard{background:#f5f8fa;border:1px solid #dbe4eb;border-radius:9px}
    QLabel#sectionTitle{color:#173b5b;font-size:13pt;font-weight:700} QLabel#subTitle{color:#173b5b;font-weight:700} QLabel#muted{color:#65798a} QLabel#dialogTitle{color:#173b5b;font-size:12.5pt;font-weight:700}
    QLabel#metricTitle{color:#697b8b} QLabel#metricValue{color:#173b5b;font-size:17pt;font-weight:700}
    QLineEdit,QPlainTextEdit,QListWidget,QComboBox,QSpinBox{border:1px solid #c8d4df;border-radius:7px;background:white;padding:7px} QListWidget::item{padding:9px 8px;min-height:34px} QListWidget::item:selected{background:#e7f1f8;color:#173b5b}
    QCheckBox{spacing:8px;min-height:30px} QCheckBox::indicator{width:20px;height:20px;border:2px solid #9aaebe;border-radius:5px;background:white} QCheckBox::indicator:checked{background:#2f7ea8;border-color:#2f7ea8}
    QRadioButton{spacing:7px;min-height:28px} QPushButton{min-height:36px;padding:0 14px;border:1px solid #c7d2dc;border-radius:7px;background:white;color:#294052;font-weight:600} QPushButton:hover{background:#f1f5f8}
    QPushButton#primary{background:#2f7ea8;color:white;border-color:#2f7ea8} QPushButton#run{background:#31845d;color:white;border-color:#31845d} QPushButton#scheduler{background:#7658a8;color:white;border-color:#7658a8} QPushButton#secondary{background:#f2f6f9} QPushButton#danger{color:#a82d2d;background:#fff8f8;border-color:#e1b7b7}
    QProgressBar{border:1px solid #cad5df;border-radius:5px;background:#f3f6f8;height:11px} QProgressBar::chunk{background:#2f7ea8;border-radius:5px} QTabBar::tab{background:#dfe8ee;color:#294052;padding:10px 20px;margin-right:4px;border-radius:7px} QTabBar::tab:selected{background:#2f7ea8;color:white}
    """)
