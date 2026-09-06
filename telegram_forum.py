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

    def send_document(self, token: str, chat_id: str, path: Path, thread_id: int | None = None) -> dict:
        data = {"chat_id": chat_id}
        if thread_id is not None:
            data["message_thread_id"] = thread_id
        with path.open("rb") as document:
            return self.request(token, "sendDocument", files={"document": document}, data=data)

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

    def prepare_topics(self, token: str, chat_id: str, main_name: str, history_name: str, existing_main_id: int | None = None, existing_history_id: int | None = None) -> tuple[int, int]:
        chat = self.get_chat(token, chat_id)
        if not chat.get("is_forum"):
            raise RuntimeError("چت مقصد Forum نیست. برای استفاده از Topic باید Topics گروه فعال باشد.")
        main_id = existing_main_id or self.create_topic(token, chat_id, main_name)
        history_id = existing_history_id or self.create_topic(token, chat_id, history_name)
        return main_id, history_id
