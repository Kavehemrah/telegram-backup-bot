"""Small Telegram Bot API helpers for forum-aware backups."""

from __future__ import annotations

from pathlib import Path


class TelegramForum:
    """Forum operations built on top of the existing telegram_request helper."""

    def __init__(self, request):
        self.request = request

    def get_chat(self, token: str, chat_id: str):
        return self.request(token, "getChat", data={"chat_id": chat_id})

    def create_topic(self, token: str, chat_id: str, name: str) -> int:
        result = self.request(token, "createForumTopic", data={"chat_id": chat_id, "name": name[:128]})
        return int(result["message_thread_id"])

    def send_document(self, token: str, chat_id: str, path: Path, thread_id: int | None = None, caption: str | None = None) -> dict:
        data = {"chat_id": chat_id}
        if thread_id is not None:
            data["message_thread_id"] = thread_id
        if caption:
            data["caption"] = caption[:1024]
        with path.open("rb") as document:
            return self.request(token, "sendDocument", files={"document": document}, data=data)

    def send_document_by_file_id(self, token: str, chat_id: str, file_id: str, thread_id: int | None = None, caption: str | None = None) -> dict:
        data = {"chat_id": chat_id, "document": file_id}
        if thread_id is not None:
            data["message_thread_id"] = thread_id
        if caption:
            data["caption"] = caption[:1024]
        return self.request(token, "sendDocument", data=data)

    def copy_message(self, token: str, chat_id: str, message_id: int, destination_thread_id: int) -> int:
        result = self.request(token, "copyMessage", data={
            "chat_id": chat_id,
            "from_chat_id": chat_id,
            "message_id": message_id,
            "message_thread_id": destination_thread_id,
        })
        return int(result["message_id"])

    def send_text(self, token: str, chat_id: str, text: str, thread_id: int | None = None) -> int:
        data = {"chat_id": chat_id, "text": text}
        if thread_id is not None:
            data["message_thread_id"] = thread_id
        result = self.request(token, "sendMessage", data=data)
        return int(result["message_id"])

    def delete_message(self, token: str, chat_id: str, message_id: int) -> bool:
        return bool(self.request(token, "deleteMessage", data={"chat_id": chat_id, "message_id": message_id}))

    def prepare_topic(self, token: str, chat_id: str, name: str, existing_id: int | None = None) -> int:
        chat = self.get_chat(token, chat_id)
        if not chat.get("is_forum"):
            raise RuntimeError("چت مقصد Forum نیست. برای استفاده از Topic باید Topics گروه فعال باشد.")
        return int(existing_id) if existing_id else self.create_topic(token, chat_id, name)
