"""Persistent job and version-manifest storage for multi-folder backups."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
JOBS_FILE = BASE_DIR / "backup_jobs.json"
MANIFEST_FILE = BASE_DIR / "backup_manifest.json"


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


def new_job(folder: str, chat_id: str, schedule: str = "23:00") -> dict:
    name = Path(folder).name or folder
    return {
        "id": uuid.uuid4().hex[:12],
        "name": name,
        "folder": folder,
        "chat_id": chat_id,
        "destination": "topic",
        "main_topic_id": None,
        "history_topic_id": None,
        "main_topic_name": name,
        "history_topic_name": f"{name} History",
        "schedule": schedule,
        "enabled": True,
    }


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
            modified = path.stat().st_mtime_ns
        except OSError:
            continue
        if record.get("modified") != modified or record.get("deleted"):
            result.append(path)
    return result
