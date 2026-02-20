"""Telegram adapter configuration loading from env values."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TelegramConfig:
    bot_token: str
    allowed_chat_ids: set[int]


def parse_allowed_chat_ids(raw: str | None) -> set[int]:
    if not raw:
        return set()
    values: set[int] = set()
    for part in raw.split(","):
        value = part.strip()
        if not value:
            continue
        values.add(int(value))
    return values


def load_telegram_config(bot_token: str | None, allowed_chat_ids_raw: str | None) -> TelegramConfig:
    if not bot_token:
        raise ValueError("missing TELEGRAM_BOT_TOKEN")

    chat_ids = parse_allowed_chat_ids(allowed_chat_ids_raw)
    return TelegramConfig(bot_token=bot_token, allowed_chat_ids=chat_ids)
