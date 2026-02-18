"""Core configuration — daemon-level settings independent of adapters."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class CoreConfig:
    data_dir: Path = field(default_factory=lambda: Path(__file__).parent / "data")
    dashboard_port: int = 7777
