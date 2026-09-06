import unittest
from unittest.mock import Mock, patch

import requests

import backup_bot


class TelegramRequestTests(unittest.TestCase):
    def test_network_error_retries(self):
        response = Mock(status_code=200)
        response.json.return_value = {"ok": True, "result": {"id": 1}}

        with patch.object(
            backup_bot.requests,
            "post",
            side_effect=[requests.ConnectionError("network"), response],
        ) as post, patch.object(backup_bot.time, "sleep") as sleep:
            result = backup_bot.telegram_request("token", "getMe")

        self.assertEqual(result, {"id": 1})
        self.assertEqual(post.call_count, 2)
        sleep.assert_called_once_with(1)

    def test_server_error_retries(self):
        first = Mock(status_code=503)
        second = Mock(status_code=200)
        second.json.return_value = {"ok": True, "result": {"id": 2}}

        with patch.object(
            backup_bot.requests,
            "post",
            side_effect=[first, second],
        ) as post, patch.object(backup_bot.time, "sleep") as sleep:
            result = backup_bot.telegram_request("token", "getMe")

        self.assertEqual(result, {"id": 2})
        self.assertEqual(post.call_count, 2)
        sleep.assert_called_once_with(1)

    def test_rate_limit_uses_retry_after(self):
        first = Mock(status_code=429)
        first.json.return_value = {"parameters": {"retry_after": 7}}
        second = Mock(status_code=200)
        second.json.return_value = {"ok": True, "result": {"id": 3}}

        with patch.object(
            backup_bot.requests,
            "post",
            side_effect=[first, second],
        ) as post, patch.object(backup_bot.time, "sleep") as sleep:
            result = backup_bot.telegram_request("token", "getMe")

        self.assertEqual(result, {"id": 3})
        self.assertEqual(post.call_count, 2)
        sleep.assert_called_once_with(7)

    def test_client_error_does_not_retry(self):
        response = Mock(status_code=401)
        response.raise_for_status.side_effect = requests.HTTPError("unauthorized")

        with patch.object(backup_bot.requests, "post", return_value=response) as post, patch.object(
            backup_bot.time, "sleep"
        ) as sleep:
            with self.assertRaisesRegex(RuntimeError, r"Telegram getMe failed \(401\)"):
                backup_bot.telegram_request("token", "getMe")

        post.assert_called_once()
        sleep.assert_not_called()


class BackupConcurrencyTests(unittest.TestCase):
    def test_concurrent_backup_is_rejected(self):
        self.assertTrue(backup_bot.BACKUP_LOCK.acquire(blocking=False))
        try:
            with self.assertRaisesRegex(RuntimeError, "عملیات پشتیبان"):
                backup_bot.backup_changed_files(
                    "token",
                    "chat",
                    ".",
                    lambda message: None,
                )
        finally:
            backup_bot.BACKUP_LOCK.release()


if __name__ == "__main__":
    unittest.main()
