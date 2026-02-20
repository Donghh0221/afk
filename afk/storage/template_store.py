from __future__ import annotations

import json
import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class TemplateConfig:
    name: str
    description: str
    template_dir: Path
    agent: str | None = None
    capabilities: list[str] = field(default_factory=list)
    completion_criteria: dict | None = None


class TemplateStore:
    """Discovers and serves workspace templates from a directory.

    Each subdirectory containing a ``template.json`` is treated as a template.
    All files *except* ``template.json`` are scaffold files that get copied
    into new worktrees.
    """

    def __init__(self, templates_dir: Path) -> None:
        self._dir = templates_dir
        self._templates: dict[str, TemplateConfig] = {}
        self._load()

    def _load(self) -> None:
        if not self._dir.is_dir():
            logger.info("Templates directory not found: %s", self._dir)
            return

        for child in sorted(self._dir.iterdir()):
            meta_path = child / "template.json"
            if not child.is_dir() or not meta_path.exists():
                continue
            try:
                data = json.loads(meta_path.read_text())
                self._templates[data["name"]] = TemplateConfig(
                    name=data["name"],
                    description=data.get("description", ""),
                    template_dir=child,
                    agent=data.get("agent"),
                    capabilities=data.get("capabilities", []),
                    completion_criteria=data.get("completion_criteria"),
                )
            except (json.JSONDecodeError, KeyError) as e:
                logger.warning("Skipping invalid template %s: %s", child.name, e)

        logger.info("Loaded %d templates", len(self._templates))

    def get(self, name: str) -> TemplateConfig | None:
        """Look up a template by name (case-insensitive)."""
        if name in self._templates:
            return self._templates[name]
        name_lower = name.lower()
        for key, cfg in self._templates.items():
            if key.lower() == name_lower:
                return cfg
        return None

    def list_all(self) -> dict[str, TemplateConfig]:
        """Return all loaded templates."""
        return dict(self._templates)

    @staticmethod
    def apply(template: TemplateConfig, dest: str) -> None:
        """Copy scaffold files from *template* into *dest* directory.

        Every file/directory in the template directory **except**
        ``template.json`` is copied.
        """
        dest_path = Path(dest)
        for item in template.template_dir.iterdir():
            if item.name == "template.json":
                continue
            target = dest_path / item.name
            if item.is_dir():
                shutil.copytree(item, target, dirs_exist_ok=True)
            else:
                shutil.copy2(item, target)
        logger.info(
            "Applied template '%s' to %s", template.name, dest,
        )
