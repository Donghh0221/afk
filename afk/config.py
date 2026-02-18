from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv


@dataclass
class Config:
    telegram_bot_token: str
    telegram_group_id: int
    data_dir: Path = field(default_factory=lambda: Path(__file__).parent / "data")
    dashboard_port: int = 7777
    openai_api_key: str = ""

    @classmethod
    def from_env(cls) -> Config:
        load_dotenv()
        token = os.environ.get("AFK_TELEGRAM_BOT_TOKEN", "")
        group_id = os.environ.get("AFK_TELEGRAM_GROUP_ID", "")
        if not token:
            raise ValueError("AFK_TELEGRAM_BOT_TOKEN is required")
        if not group_id:
            raise ValueError("AFK_TELEGRAM_GROUP_ID is required")
        return cls(
            telegram_bot_token=token,
            telegram_group_id=int(group_id),
            dashboard_port=int(os.environ.get("AFK_DASHBOARD_PORT", "7777")),
            openai_api_key=os.environ.get("AFK_OPENAI_API_KEY", "")
            or os.environ.get("OPENAI_API_KEY", ""),
        )
