"""Small Telegram Bot API helpers for forum-aware backups."""

from __future__ import annotations

from pathlib import Path

from backup_jobs import load_folders
from restore_catalog import record_uploaded_file


class TelegramAPIError(RuntimeError):
    """Human-readable Telegram Bot API error with method and status details."""

    def __init__(self, method: str, status_code: int | None, description: str):
        self.method = method
        self.status_code = status_code
        self.description = description
        prefix = f"Telegram {method} failed"
        if status_code is not None:
            prefix += f" ({status_code})"
        super().__init__(f"{prefix}: {description}")


class TelegramForum:
    """Forum operations built on top of the existing telegram_request helper."""

    def __init__(self, request):
        self.request = request

    def get_chat(self, token: str, chat_id: str):
        return self.request(token, "getChat", data={"chat_id": chat_id})

    def create_topic(self, token: str, chat_id: str, name: str) -> int:
        result = self.request(
            token,
            "createForumTopic",
            data={"chat_id": chat_id, "name": name[:128]},
        )
        return int(result["message_thread_id"])

    def send_document(
        self,
        token: str,
        chat_id: str,
        path: Path,
        thread_id: int | None = None,
        caption: str | None = None,
        *,
        relative_path: str | None = None,
        topic_name: str | None = None,
    ) -> dict:
        data = {"chat_id": chat_id}
        if thread_id is not None:
            data["message_thread_id"] = thread_id
        if caption:
            data["caption"] = caption[:1024]
        try:
            with path.open("rb") as document:
                result = self.request(
                    token,
                    "sendDocument",
                    files={"document": document},
                    data=data,
                )
        except Exception as exc:
            response = getattr(exc, "response", None)
            if response is not None:
                description = None
                try:
                    payload = response.json()
                    description = payload.get("description")
                except Exception:
                    description = None
                if description:
                    raise TelegramAPIError(
                        "sendDocument", response.status_code, description
                    ) from exc
            raise

        document_info = result.get("document") or {}
        file_id = document_info.get("file_id")
        if file_id and result.get("message_id") is not None:
            derived_relative = relative_path
            derived_topic_name = topic_name
            if derived_relative is None or derived_topic_name is None:
                try:
                    for folder in load_folders():
                        folder_path = folder.get("path")
                        if not folder_path:
                            continue
                        folder_root = Path(folder_path).resolve()
                        try:
                            derived_relative = str(path.resolve().relative_to(folder_root))
                            if derived_topic_name is None:
                                derived_topic_name = folder.get("topic_name")
                            break
                        except ValueError:
                            continue
                except OSError:
                    pass
            record_kwargs = {
                "chat_id": chat_id,
                "message_id": int(result["message_id"]),
                "file_id": str(file_id),
                "path": str(path),
                "relative_path": derived_relative or path.name,
                "size": path.stat().st_size,
                "thread_id": thread_id,
            }
            if derived_topic_name:
                record_kwargs["topic_name"] = str(derived_topic_name)
            record_uploaded_file(**record_kwargs)
        return result

    def send_document_by_file_id(
        self,
        token: str,
        chat_id: str,
        file_id: str,
        thread_id: int | None = None,
        caption: str | None = None,
    ) -> dict:
        data = {"chat_id": chat_id, "document": file_id}
        if thread_id is not None:
            data["message_thread_id"] = thread_id
        if caption:
            data["caption"] = caption[:1024]
        return self.request(token, "sendDocument", data=data)

    def copy_message(
        self,
        token: str,
        chat_id: str,
        message_id: int,
        destination_thread_id: int,
    ) -> int:
        result = self.request(
            token,
            "copyMessage",
            data={
                "chat_id": chat_id,
                "from_chat_id": chat_id,
                "message_id": message_id,
                "message_thread_id": destination_thread_id,
            },
        )
        return int(result["message_id"])

    def send_text(
        self,
        token: str,
        chat_id: str,
        text: str,
        thread_id: int | None = None,
    ) -> int:
        data = {"chat_id": chat_id, "text": text}
        if thread_id is not None:
            data["message_thread_id"] = thread_id
        result = self.request(token, "sendMessage", data=data)
        return int(result["message_id"])

    def delete_message(self, token: str, chat_id: str, message_id: int) -> bool:
        return bool(
            self.request(
                token,
                "deleteMessage",
                data={"chat_id": chat_id, "message_id": message_id},
            )
        )

    def prepare_topic(
        self,
        token: str,
        chat_id: str,
        name: str,
        existing_id: int | None = None,
    ) -> int:
        chat = self.get_chat(token, chat_id)
        if not chat.get("is_forum"):
            raise RuntimeError("Telegram chat is not a forum.")
        if existing_id:
            try:
                self.send_text(token, chat_id, "ping", existing_id)
                return int(existing_id)
            except RuntimeError:
                pass
        return self.create_topic(token, chat_id, name)
