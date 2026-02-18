from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


class ProjectStore:
    """Manages project registration data in a JSON file."""

    def __init__(self, data_dir: Path) -> None:
        self._path = data_dir / "projects.json"
        self._projects: dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        if self._path.exists():
            self._projects = json.loads(self._path.read_text())
            logger.info("Loaded %d projects", len(self._projects))

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(self._projects, indent=2, ensure_ascii=False)
        )

    def add(self, name: str, path: str) -> bool:
        """Register a project. Returns False if already exists."""
        if name in self._projects:
            return False
        resolved = str(Path(path).expanduser().resolve())
        if not Path(resolved).is_dir():
            raise ValueError(f"Directory not found: {resolved}")
        self._projects[name] = {
            "path": resolved,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        self._save()
        return True

    def remove(self, name: str) -> bool:
        """Unregister a project."""
        if name not in self._projects:
            return False
        del self._projects[name]
        self._save()
        return True

    def get(self, name: str) -> dict | None:
        """Look up a project."""
        return self._projects.get(name)

    def list_all(self) -> dict[str, dict]:
        """List all projects."""
        return dict(self._projects)
