"""Tunnel config — data structures, persistence, and Claude CLI generation."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shlex
import shutil
import socket
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

CONFIG_FILENAME = ".afk/tunnel.json"


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

def find_free_port() -> int:
    """Ask the OS for an available TCP port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class ServiceConfig:
    """A single service definition from tunnel config."""

    name: str         # "api", "web", "worker"
    command: str      # "uvicorn main:app --port {port}"
    path: str         # "." or "./apps/web" (relative to worktree)
    tunnel: bool      # whether to expose via cloudflared

    def resolve_command(self, port: int) -> list[str]:
        """Substitute ``{port}`` placeholder and split into argv."""
        return shlex.split(self.command.replace("{port}", str(port)))

    def resolve_path(self, worktree_path: str) -> str:
        """Resolve relative path against worktree to an absolute path."""
        return str(Path(worktree_path) / self.path)


@dataclass
class TunnelConfig:
    """Top-level tunnel configuration (maps to ``.afk/tunnel.json``)."""

    services: list[ServiceConfig] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "services": [
                {
                    "name": s.name,
                    "command": s.command,
                    "path": s.path,
                    "tunnel": s.tunnel,
                }
                for s in self.services
            ],
        }

    @classmethod
    def from_dict(cls, data: dict) -> TunnelConfig:
        services = [
            ServiceConfig(
                name=s["name"],
                command=s["command"],
                path=s.get("path", "."),
                tunnel=s.get("tunnel", True),
            )
            for s in data.get("services", [])
        ]
        return cls(services=services)


# ---------------------------------------------------------------------------
# Config I/O
# ---------------------------------------------------------------------------

def load_tunnel_config(worktree_path: str) -> TunnelConfig | None:
    """Load ``.afk/tunnel.json`` from *worktree_path*. Returns None if absent or invalid."""
    config_path = Path(worktree_path) / CONFIG_FILENAME
    if not config_path.exists():
        return None
    try:
        data = json.loads(config_path.read_text())
        config = TunnelConfig.from_dict(data)
        if not config.services:
            return None
        return config
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        logger.warning("Failed to parse %s: %s", config_path, e)
        return None


def save_tunnel_config(worktree_path: str, config: TunnelConfig) -> Path:
    """Save config to ``.afk/tunnel.json``. Creates ``.afk/`` dir if needed."""
    config_path = Path(worktree_path) / CONFIG_FILENAME
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(config.to_dict(), indent=2) + "\n")
    logger.info("Saved tunnel config to %s", config_path)
    return config_path


# ---------------------------------------------------------------------------
# Claude CLI-based config generation
# ---------------------------------------------------------------------------

_GENERATE_PROMPT = """\
Analyze this project and output a JSON tunnel configuration.

Look at the project files (package.json, Procfile, docker-compose.yml, \
pyproject.toml, Makefile, etc.) to determine what services this project runs \
in development mode.

Output ONLY valid JSON in this exact format (no markdown, no explanation):
{
  "services": [
    {
      "name": "short-name",
      "command": "the dev command with {port} placeholder for port",
      "path": ".",
      "tunnel": true
    }
  ]
}

Rules:
- "name": short identifier (e.g. "api", "web", "worker")
- "command": the actual dev/start command. Use {port} where the port should go. \
Include --host 0.0.0.0 or equivalent for servers that need to bind to all interfaces.
- "path": relative path from project root (use "." for root)
- "tunnel": true if the service should be exposed via a public URL, false for \
background workers/queues
- For monorepos, include each service that needs to run during development
- Only include services that make sense for local development
"""


def _extract_json(text: str) -> dict | None:
    """Extract JSON from Claude output (may be wrapped in markdown fences)."""
    # Try stripping markdown code fences first
    fence_match = re.search(r"```(?:json)?\s*\n?(.*?)```", text, re.DOTALL)
    if fence_match:
        text = fence_match.group(1).strip()

    # Try parsing as raw JSON
    try:
        data = json.loads(text)
        if isinstance(data, dict) and "services" in data:
            return data
    except json.JSONDecodeError:
        pass

    # Try finding a JSON object in the text
    brace_match = re.search(r"\{.*\}", text, re.DOTALL)
    if brace_match:
        try:
            data = json.loads(brace_match.group(0))
            if isinstance(data, dict) and "services" in data:
                return data
        except json.JSONDecodeError:
            pass

    return None


async def generate_tunnel_config(worktree_path: str) -> TunnelConfig:
    """Use Claude Code CLI to analyze the project and generate a TunnelConfig.

    Raises RuntimeError on failure.
    """
    claude_path = shutil.which("claude")
    if not claude_path:
        raise RuntimeError(
            "Claude Code CLI not found. Install it to use /tunnel init."
        )

    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}

    try:
        proc = await asyncio.create_subprocess_exec(
            claude_path, "-p", "--no-session-persistence", _GENERATE_PROMPT,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=worktree_path,
            env=env,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
    except asyncio.TimeoutError:
        raise RuntimeError("Claude CLI timed out analyzing the project.")

    if proc.returncode != 0:
        err = stderr.decode().strip() if stderr else "unknown error"
        raise RuntimeError(f"Claude CLI failed: {err}")

    output = stdout.decode().strip()
    if not output:
        raise RuntimeError("Claude CLI returned empty output.")

    data = _extract_json(output)
    if not data:
        raise RuntimeError(
            f"Could not parse JSON from Claude CLI output:\n{output[:500]}"
        )

    config = TunnelConfig.from_dict(data)
    if not config.services:
        raise RuntimeError("Claude CLI generated a config with no services.")

    return config
