"""Restore backed-up files from Telegram using locally persisted file metadata."""

from __future__ import annotations

import os
from pathlib import Path

import requests

from backup_bot import telegram_request
from restore_catalog import load_restore_index, list_restore_candidates

MAX_DOWNLOAD_BYTES = 20 * 1024 * 1024


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
                raise ValueError(
                    "Telegram file is larger than the 20 MB Bot API download limit."
                )
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
