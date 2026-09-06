"""Restore backed-up files from Telegram using locally persisted file metadata."""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

import requests

from backup_bot import telegram_request

BASE_DIR = Path(__file__).resolve().parent
RESTORE_INDEX_FILE = BASE_DIR / "backup_restore_index.json"
MAX_DOWNLOAD_BYTES = 20 * 1024 * 1024


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


def _atomic_save(entries: list[dict]) -> None:
    tmp = RESTORE_INDEX_FILE.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(entries[-10000:], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tmp.replace(RESTORE_INDEX_FILE)


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
    entries.append(
        {
            "chat_id": str(chat_id),
            "message_id": int(message_id),
            "file_id": str(file_id),
            "path": str(path),
            "relative_path": str(relative_path),
            "size": int(size),
            "thread_id": thread_id,
            "uploaded_at": datetime.now().isoformat(timespec="seconds"),
        }
    )
    _atomic_save(entries)


def list_restore_candidates() -> list[dict]:
    """Return newest indexed uploads first."""
    entries = load_restore_index()
    return list(reversed(entries))


def _safe_relative_path(relative_path: str) -> Path:
    candidate = Path(relative_path)
    if not relative_path or candidate.is_absolute():
        raise ValueError("Restore path is invalid.")
    if any(part in ("", ".", "..") for part in candidate.parts):
        raise ValueError("Restore path escapes the destination folder.")
    return candidate


def restore_file(
    token: str,
    entry: dict,
    destination_folder: str | Path,
    *,
    overwrite: bool = False,
    timeout: int = 30,
) -> Path:
    """Download one indexed Telegram file into its original relative path."""
    file_id = str(entry.get("file_id", "")).strip()
    if not file_id:
        raise ValueError("This backup entry has no downloadable file_id.")

    destination = Path(destination_folder).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    relative = _safe_relative_path(str(entry.get("relative_path", "")))
    output = (destination / relative).resolve()
    try:
        output.relative_to(destination)
    except ValueError as exc:
        raise ValueError("Restore path escapes the destination folder.") from exc

    if output.exists() and not overwrite:
        raise FileExistsError(f"File already exists: {output}")

    file_info = telegram_request(token, "getFile", data={"file_id": file_id})
    file_path = str(file_info.get("file_path", "")).strip()
    remote_size = file_info.get("file_size")
    if not file_path:
        raise RuntimeError("Telegram did not return a file_path.")
    if remote_size is not None and int(remote_size) > MAX_DOWNLOAD_BYTES:
        raise ValueError(
            f"Telegram Bot API download limit exceeded: {int(remote_size) / 1024 / 1024:.1f} MB."
        )

    url = f"https://api.telegram.org/file/bot{token}/{file_path}"
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".restore.tmp")
    try:
        with requests.get(url, stream=True, timeout=timeout) as response:
            response.raise_for_status()
            content_length = response.headers.get("Content-Length")
            if content_length and int(content_length) > MAX_DOWNLOAD_BYTES:
                raise ValueError("Telegram file is larger than the 20 MB Bot API download limit.")
            with temporary.open("wb") as target:
                downloaded = 0
                for chunk in response.iter_content(chunk_size=1024 * 256):
                    if not chunk:
                        continue
                    downloaded += len(chunk)
                    if downloaded > MAX_DOWNLOAD_BYTES:
                        raise ValueError("Downloaded file exceeds the 20 MB safety limit.")
                    target.write(chunk)
        os.replace(temporary, output)
    except Exception:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return output
