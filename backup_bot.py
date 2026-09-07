"""Telegram backup backend used by the PySide6 product UI."""

from __future__ import annotations

import json
import os
import time
import threading
import traceback
from datetime import datetime
from pathlib import Path

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
STATE_FILES = {
    LOG_FILE,
    HISTORY_FILE,
    JOBS_FILE,
    MANIFEST_FILE,
    FOLDERS_FILE,
    PROJECT_FILE,
    ENV_FILE,
    BASE_DIR / "backup_restore_index.json",
}
TELEGRAM_TIMEOUT = 30
TELEGRAM_RETRIES = 3
BACKUP_LOCK = threading.Lock()


def load_env() -> None:
    """Load simple KEY=VALUE entries from the local .env file."""
    if not ENV_FILE.exists():
        return
    for raw_line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"\''))


def _atomic_write(path: Path, text: str) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def save_backup_time() -> None:
    _atomic_write(
        LOG_FILE,
        json.dumps({"last_backup": datetime.now().isoformat()}),
    )


def load_history() -> list[dict]:
    if not HISTORY_FILE.exists():
        return []
    try:
        value = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return []
    return value if isinstance(value, list) else []


def save_history(history: list[dict]) -> None:
    _atomic_write(
        HISTORY_FILE,
        json.dumps(history[-5000:], ensure_ascii=False, indent=2),
    )


def _is_state_file(path: Path) -> bool:
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
    if not history:
        return current_files(str(folder), STATE_FILES)
    sent_versions = {
        (item.get("path"), item.get("modified")) for item in history
    }
    result = []
    for path in current_files(str(folder), STATE_FILES):
        try:
            modified = path.stat().st_mtime_ns
        except OSError:
            continue
        if (str(path), modified) not in sent_versions:
            result.append(path)
    return result


def _safe_response_text(response: requests.Response) -> str:
    try:
        payload = response.json()
        return json.dumps(payload, ensure_ascii=False, default=str)
    except (ValueError, TypeError):
        return str(response.text)[:4000]


def _debug_telegram_failure(method: str, response: requests.Response) -> None:
    print("\n" + "=" * 72, flush=True)
    print("TELEGRAM API ERROR", flush=True)
    print(f"method: {method}", flush=True)
    print(f"HTTP status: {response.status_code}", flush=True)
    print(f"response: {_safe_response_text(response)}", flush=True)
    print("=" * 72 + "\n", flush=True)


def telegram_request(token, method, **kwargs):
    """Call Telegram with retries and print full API failures to the terminal."""
    url = f"https://api.telegram.org/bot{token}/{method}"
    last_error = None

    for attempt in range(1, TELEGRAM_RETRIES + 1):
        try:
            response = requests.post(url, timeout=TELEGRAM_TIMEOUT, **kwargs)

            if not response.ok:
                _debug_telegram_failure(method, response)

            if response.status_code == 429:
                retry_after = 1
                try:
                    retry_after = int(
                        response.json()
                        .get("parameters", {})
                        .get("retry_after", 1)
                    )
                except (TypeError, ValueError, json.JSONDecodeError):
                    pass
                if attempt < TELEGRAM_RETRIES:
                    time.sleep(max(1, min(retry_after, 60)))
                    continue

            if 500 <= response.status_code < 600 and attempt < TELEGRAM_RETRIES:
                time.sleep(2 ** (attempt - 1))
                continue

            try:
                response.raise_for_status()
            except requests.HTTPError as error:
                raise RuntimeError(
                    f"Telegram {method} failed ({response.status_code}): "
                    f"{_safe_response_text(response)}"
                ) from error

            payload = response.json()
            if not payload.get("ok"):
                print(
                    f"Telegram API returned ok=false for {method}: "
                    f"{json.dumps(payload, ensure_ascii=False, default=str)}",
                    flush=True,
                )
                raise RuntimeError(
                    payload.get("description", "Telegram API error")
                )
            return payload["result"]

        except RuntimeError:
            raise
        except requests.RequestException as error:
            last_error = error
            print(
                f"Telegram request exception | method={method} | "
                f"attempt={attempt}/{TELEGRAM_RETRIES} | {error}",
                flush=True,
            )
            if attempt < TELEGRAM_RETRIES:
                time.sleep(2 ** (attempt - 1))
                continue
            print(traceback.format_exc(), flush=True)
            raise

    raise last_error or RuntimeError("Telegram request failed")


def _ensure_project_history_topic(forum, token, chat_id):
    project = load_project()
    if (
        project.get("history_chat_id") == str(chat_id)
        and project.get("history_topic_id")
    ):
        return int(project["history_topic_id"])

    chat = forum.get_chat(token, chat_id)
    if not chat.get("is_forum"):
        raise RuntimeError(
            "چت مقصد Forum نیست. برای استفاده از Topic باید Topics گروه فعال باشد."
        )

    topic_id = forum.create_topic(
        token,
        chat_id,
        project.get("history_topic_name") or "Backup History",
    )
    project.update(
        {
            "history_chat_id": str(chat_id),
            "history_topic_id": topic_id,
            "history_topic_name": project.get("history_topic_name")
            or "Backup History",
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }
    )
    save_project(project)
    return topic_id


def _ensure_folder_topic(forum, token, job):
    folder = get_or_create_folder(job["folder"])
    topic_id = folder.get("topic_id")
    if topic_id:
        return int(topic_id), folder

    topic_id = forum.prepare_topic(
        token,
        job["chat_id"],
        job.get("main_topic_name")
        or folder.get("topic_name")
        or job["name"],
    )
    folder["topic_id"] = topic_id
    folder["topic_name"] = (
        job.get("main_topic_name")
        or folder.get("topic_name")
        or job["name"]
    )
    update_folder(folder)
    return topic_id, folder


def _records_for_folder(manifest, folder_record, legacy_job_id=None):
    folder_id = folder_record["id"]
    records = manifest.setdefault("folders", {}).setdefault(folder_id, {})
    if (
        not records
        and legacy_job_id
        and isinstance(manifest.get(legacy_job_id), dict)
    ):
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
        "📦 نسخه قبلی\n"
        f"فولدر: {job.get('name', '')}\n"
        f"فایل: {relative}\n"
        f"نسخه: {version}\n"
        f"زمان: {record.get('sent_at', '')}",
        int(history_id),
    )
    forum.copy_message(
        token,
        job["chat_id"],
        int(message_id),
        int(history_id),
    )
    log(f"نسخه قبلی به History منتقل شد: {relative} | v{version}")


def run_job_backup(
    token,
    job,
    log,
    progress=None,
    cancel_event=None,
    selected_files=None,
):
    if not BACKUP_LOCK.acquire(blocking=False):
        raise RuntimeError("یک عملیات پشتیبان‌گیری دیگر در حال اجراست.")

    try:
        folder = Path(job["folder"])
        if not folder.is_dir():
            raise ValueError(f"فولدر معتبر نیست: {folder}")

        forum = TelegramForum(telegram_request)
        manifest = load_manifest()
        folder_record = get_or_create_folder(str(folder))
        records = _records_for_folder(
            manifest,
            folder_record,
            job.get("id"),
        )

        destination_thread = None
        history_id = None
        if job.get("destination", "topic") == "topic":
            destination_thread, folder_record = _ensure_folder_topic(
                forum,
                token,
                job,
            )
            if job.get("history_enabled", True):
                history_id = _ensure_project_history_topic(
                    forum,
                    token,
                    job["chat_id"],
                )
            job["main_topic_id"] = destination_thread
            job["history_topic_id"] = history_id
            job["folder_id"] = folder_record["id"]
        else:
            job["main_topic_id"] = None
            job["history_topic_id"] = None

        excluded = STATE_FILES
        all_current = current_files(str(folder), excluded)

        if selected_files is None:
            pending = pending_files(str(folder), records, excluded)
            if job.get("backup_mode") == "SELECTED":
                selected_set = {
                    str(Path(path).resolve())
                    for path in job.get("selected_files", [])
                }
                paths = [
                    path
                    for path in pending
                    if str(path.resolve()) in selected_set
                ]
            else:
                paths = pending
        else:
            paths = [Path(path) for path in selected_files]

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
                    _archive_current_version(
                        forum,
                        token,
                        job,
                        old,
                        history_id,
                        log,
                    )
                if job.get("replace_files", True):
                    try:
                        forum.delete_message(
                            token,
                            job["chat_id"],
                            int(old["message_id"]),
                        )
                    except Exception as error:
                        log(f"حذف نسخه قبلی ممکن نشد: {error}")

            log(
                f"ارسال فایل: {path} | chat={job['chat_id']} | "
                f"thread={destination_thread}"
            )
            message = forum.send_document(
                token,
                job["chat_id"],
                path,
                destination_thread,
            )

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
            log(
                f"ارسال شد: {path} | نسخه {records[key]['version']}"
            )
            if progress:
                progress(index, total)

        existing_keys = {file_key(path) for path in all_current}
        for key, record in list(records.items()):
            if (
                record.get("deleted")
                or key in existing_keys
                or not record.get("message_id")
            ):
                continue

            if job.get("history_enabled", True) and history_id:
                _archive_current_version(
                    forum,
                    token,
                    job,
                    record,
                    history_id,
                    log,
                )
                forum.send_text(
                    token,
                    job["chat_id"],
                    "🗑 فایل حذف شد\n"
                    f"فایل: {record.get('relative_path', record.get('path', ''))}\n"
                    f"نسخه {record.get('version', 1)} محفوظ است.",
                    int(history_id),
                )

            if job.get("replace_files", True):
                try:
                    forum.delete_message(
                        token,
                        job["chat_id"],
                        int(record["message_id"]),
                    )
                except Exception as error:
                    log(f"پیام فایل حذف‌شده پاک نشد: {error}")

            record["deleted"] = True
            record["deleted_at"] = datetime.now().isoformat(timespec="seconds")

        save_manifest(manifest)
        save_backup_time()
        return uploaded, True
    finally:
        BACKUP_LOCK.release()


def backup_changed_files(
    token,
    chat_id,
    folder,
    log,
    progress=None,
    cancel_event=None,
    selected_files=None,
):
    job = new_job(folder, chat_id)
    return run_job_backup(
        token,
        job,
        log,
        progress,
        cancel_event,
        selected_files,
    )
