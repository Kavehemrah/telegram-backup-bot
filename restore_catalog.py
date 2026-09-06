"""Local metadata catalog used by the Telegram restore feature."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
RESTORE_INDEX_FILE = BASE_DIR / "backup_restore_index.json"


def load_restore_index() -> list[dict]:
    if not RESTORE_INDEX_FILE.exists():
        return []
    try:
        data = json.loads(RESTORE_INDEX_FILE.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return []
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict) and item.get("file_id")]


def record_uploaded_file(
    *,
    chat_id: str,
    message_id: int,
    file_id: str,
    path: str,
    relative_path: str,
    size: int,
    thread_id: int | None = None,
) -> None:
    entries = load_restore_index()
    version = 1 + sum(
        1
        for item in entries
        if str(item.get("chat_id")) == str(chat_id)
        and str(item.get("path")) == str(path)
    )
    entries.append(
        {
            "chat_id": str(chat_id),
            "message_id": int(message_id),
            "file_id": str(file_id),
            "path": str(path),
            "relative_path": str(relative_path),
            "size": int(size),
            "thread_id": thread_id,
            "version": version,
            "uploaded_at": datetime.now().isoformat(timespec="seconds"),
        }
    )
    tmp = RESTORE_INDEX_FILE.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(entries[-10000:], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tmp.replace(RESTORE_INDEX_FILE)


def list_restore_candidates() -> list[dict]:
    return list(reversed(load_restore_index()))
