"""Telegram adapter configuration."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TelegramConfig:
    bot_token: str
    group_id: int
