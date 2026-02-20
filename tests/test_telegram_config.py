from __future__ import annotations

import unittest

from adapters.telegram.config import load_telegram_config, parse_allowed_chat_ids


class TelegramConfigTests(unittest.TestCase):
    def test_parse_allowed_chat_ids(self) -> None:
        values = parse_allowed_chat_ids("123, 456,789")
        self.assertEqual(values, {123, 456, 789})

    def test_missing_token_raises(self) -> None:
        with self.assertRaises(ValueError):
            load_telegram_config(None, "123")

    def test_load_config(self) -> None:
        cfg = load_telegram_config("token", "123,456")
        self.assertEqual(cfg.bot_token, "token")
        self.assertEqual(cfg.allowed_chat_ids, {123, 456})


if __name__ == "__main__":
    unittest.main()
