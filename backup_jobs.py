"""Persistent job, folder identity and file-manifest storage."""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
JOBS_FILE = BASE_DIR / "backup_jobs.json"
MANIFEST_FILE = BASE_DIR / "backup_manifest.json"
FOLDERS_FILE = BASE_DIR / "backup_folders.json"
PROJECT_FILE = BASE_DIR / "backup_project.json"


def _read_json(path: Path, default):
    if not path.exists():
        return default
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, type(default)) else default
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return default


def _write_json(path: Path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def load_jobs() -> list[dict]:
    return _read_json(JOBS_FILE, [])


def save_jobs(jobs: list[dict]) -> None:
    _write_json(JOBS_FILE, jobs)


def load_folders() -> list[dict]:
    return _read_json(FOLDERS_FILE, [])


def save_folders(folders: list[dict]) -> None:
    _write_json(FOLDERS_FILE, folders)


def load_project() -> dict:
    return _read_json(PROJECT_FILE, {})


def save_project(project: dict) -> None:
    _write_json(PROJECT_FILE, project)


def normalize_folder(folder: str) -> str:
    return os.path.normcase(str(Path(folder).expanduser().resolve()))


def get_or_create_folder(folder: str) -> dict:
    normalized = normalize_folder(folder)
    folders = load_folders()
    for item in folders:
        if item.get("path_key") == normalized:
            item.setdefault("id", uuid.uuid4().hex[:12])
            item.setdefault("topic_id", None)
            item.setdefault("topic_name", Path(folder).name or folder)
            return item
    item = {
        "id": uuid.uuid4().hex[:12],
        "path": str(Path(folder).expanduser().resolve()),
        "path_key": normalized,
        "topic_id": None,
        "topic_name": Path(folder).name or folder,
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    folders.append(item)
    save_folders(folders)
    return item


def update_folder(folder_record: dict) -> None:
    folders = load_folders()
    for index, item in enumerate(folders):
        if item.get("id") == folder_record.get("id"):
            folders[index] = folder_record
            save_folders(folders)
            return
    folders.append(folder_record)
    save_folders(folders)


def new_job(folder: str, chat_id: str, schedule: str = "23:00") -> dict:
    folder_record = get_or_create_folder(folder)
    name = Path(folder).name or folder
    return {
        "id": uuid.uuid4().hex[:12],
        "name": name,
        "folder": str(Path(folder).expanduser().resolve()),
        "folder_id": folder_record["id"],
        "chat_id": chat_id,
        "destination": "topic",
        "main_topic_id": folder_record.get("topic_id"),
        "history_topic_id": None,
        "main_topic_name": folder_record.get("topic_name") or name,
        "history_topic_name": "Backup History",
        "schedule": schedule,
        "enabled": True,
        "backup_mode": "ALL",
        "selected_files": [],
        "replace_files": True,
        "history_enabled": True,
    }


def normalize_job(job: dict) -> dict:
    """Add new settings to old jobs without destroying existing topic ids."""
    job.setdefault("id", uuid.uuid4().hex[:12])
    job.setdefault("name", Path(job.get("folder", "Job")).name or "Job")
    job.setdefault("schedule", "23:00")
    job.setdefault("enabled", True)
    job.setdefault("backup_mode", "ALL")
    job.setdefault("selected_files", [])
    job.setdefault("replace_files", True)
    job.setdefault("history_enabled", True)
    if job.get("folder"):
        folder_record = get_or_create_folder(job["folder"])
        job.setdefault("folder_id", folder_record["id"])
        if not folder_record.get("topic_id") and job.get("main_topic_id"):
            folder_record["topic_id"] = job["main_topic_id"]
            update_folder(folder_record)
        elif folder_record.get("topic_id") and not job.get("main_topic_id"):
            job["main_topic_id"] = folder_record["topic_id"]
        # Legacy per-job history IDs are deliberately not promoted to the
        # project history topic. A new central topic is created on demand.
        job["history_topic_id"] = None
    return job


def load_and_migrate_jobs() -> list[dict]:
    jobs = load_jobs()
    changed = False
    for job in jobs:
        before = dict(job)
        normalize_job(job)
        changed = changed or job != before
    if changed:
        save_jobs(jobs)
    return jobs


def load_manifest() -> dict:
    return _read_json(MANIFEST_FILE, {})


def save_manifest(manifest: dict) -> None:
    _write_json(MANIFEST_FILE, manifest)


def file_key(path: Path) -> str:
    return str(path.resolve())


def current_files(folder: str, excluded: set[Path] | None = None) -> list[Path]:
    excluded = {p.resolve() for p in (excluded or set())}
    root = Path(folder).resolve()
    files = []
    if not root.is_dir():
        return files
    for path in root.rglob("*"):
        if not path.is_file() or path.resolve() in excluded:
            continue
        try:
            path.stat()
        except OSError:
            continue
        files.append(path)
    return sorted(files, key=lambda p: str(p).lower())


def pending_files(folder: str, manifest: dict, excluded: set[Path] | None = None) -> list[Path]:
    result = []
    for path in current_files(folder, excluded):
        record = manifest.get(file_key(path), {})
        try:
            stat = path.stat()
        except OSError:
            continue
        modified_ok = record.get("modified") == stat.st_mtime_ns
        size_ok = "size" not in record or record.get("size") == stat.st_size
        if not modified_ok or not size_ok or record.get("deleted"):
            result.append(path)
    return result
