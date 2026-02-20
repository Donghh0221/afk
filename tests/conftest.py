from __future__ import annotations

import pytest
from pathlib import Path


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    """Provide an isolated data directory for stores."""
    d = tmp_path / "data"
    d.mkdir()
    return d
