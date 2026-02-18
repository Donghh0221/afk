"""Whisper STT adapter configuration."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class WhisperConfig:
    api_key: str
    model: str = "whisper-1"
