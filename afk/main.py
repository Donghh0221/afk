from __future__ import annotations

import asyncio
import logging
import os
import signal
from pathlib import Path
from typing import Callable

from dotenv import load_dotenv

from afk.adapters.claude_code.agent import ClaudeCodeAgent
from afk.adapters.claude_code.commit_helper import generate_commit_message
from afk.ports.agent import AgentPort
from afk.adapters.telegram.config import TelegramConfig
from afk.adapters.telegram.renderer import EventRenderer
from afk.adapters.whisper.stt import WhisperAPISTT
from afk.capabilities.tunnel.tunnel import TunnelCapability
from afk.core.commands import Commands
from afk.core.events import EventBus
from afk.storage.message_store import MessageStore
from afk.adapters.web.server import WebControlPlane
from afk.adapters.telegram.adapter import TelegramAdapter
from afk.core.session_manager import SessionManager
from afk.core.orchestrator import Orchestrator
from afk.storage.project_store import ProjectStore
from afk.storage.template_store import TemplateStore

LOG_FILE = "/tmp/afk.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_FILE),
    ],
)
logger = logging.getLogger("afk")


async def main() -> None:
    logger.info("AFK starting...")

    load_dotenv()

    # -- Load configuration per component --
    telegram_token = os.environ.get("AFK_TELEGRAM_BOT_TOKEN", "")
    telegram_group = os.environ.get("AFK_TELEGRAM_GROUP_ID", "")
    if not telegram_token:
        raise ValueError("AFK_TELEGRAM_BOT_TOKEN is required")
    if not telegram_group:
        raise ValueError("AFK_TELEGRAM_GROUP_ID is required")

    telegram_config = TelegramConfig(
        bot_token=telegram_token,
        group_id=int(telegram_group),
    )

    data_dir = Path(__file__).parent / "data"
    web_port = int(os.environ.get("AFK_DASHBOARD_PORT", "7777"))
    openai_api_key = (
        os.environ.get("AFK_OPENAI_API_KEY", "")
        or os.environ.get("OPENAI_API_KEY", "")
    )

    # -- Initialize core infrastructure --
    event_bus = EventBus()
    messenger = TelegramAdapter(telegram_config)
    project_store = ProjectStore(data_dir)
    message_store = MessageStore(data_dir)

    # Select agent runtime via AFK_AGENT env var (default: claude)
    agent_type = os.environ.get("AFK_AGENT", "claude").lower()
    agent_registry: dict[str, Callable[[], AgentPort]] = {"claude": ClaudeCodeAgent}
    if agent_type == "codex":
        from afk.adapters.codex.agent import CodexAgent
        agent_registry["codex"] = CodexAgent
        logger.info("Agent runtime: OpenAI Codex CLI (default)")
    else:
        logger.info("Agent runtime: Claude Code CLI (default)")

    # Deep Research agent (requires OpenAI API key)
    if openai_api_key:
        from afk.adapters.deep_research.agent import DeepResearchAgent
        dr_model = os.environ.get("AFK_DEEP_RESEARCH_MODEL", "o4-mini-deep-research")
        dr_max_str = os.environ.get("AFK_DEEP_RESEARCH_MAX_TOOL_CALLS", "")
        dr_max = int(dr_max_str) if dr_max_str else None
        agent_registry["deep-research"] = lambda: DeepResearchAgent(
            api_key=openai_api_key, model=dr_model, max_tool_calls=dr_max,
        )
        logger.info("Deep Research agent available (model: %s)", dr_model)

    # AFK_BASE_PATH — enables smart /new auto-resolution
    base_path = os.environ.get("AFK_BASE_PATH", "")
    if base_path:
        base_path = str(Path(base_path).expanduser().resolve())
        logger.info("Base path for projects: %s", base_path)

    # Session manager (publishes events via EventBus)
    session_manager = SessionManager(
        messenger, data_dir,
        event_bus=event_bus,
        agent_factory=agent_registry.get(agent_type, ClaudeCodeAgent),
        commit_message_fn=generate_commit_message,
        agent_registry=agent_registry,
        default_agent=agent_type,
    )

    # Initialize STT (optional — voice support requires OpenAI API key)
    stt = None
    if openai_api_key:
        stt = WhisperAPISTT(api_key=openai_api_key)
        logger.info("Voice support enabled (OpenAI Whisper API)")
    else:
        logger.info("Voice support disabled (no OPENAI_API_KEY)")

    # Tunnel capability (cleanup registered with session manager)
    tunnel_capability = TunnelCapability()
    session_manager.add_cleanup_callback(tunnel_capability.cleanup_session)

    # Recover sessions from previous run (must happen before orphan cleanup)
    recovered_sessions = await session_manager.recover_sessions(project_store)

    # Clean up orphan worktrees (skips recovered session worktrees)
    await session_manager.cleanup_orphan_worktrees(project_store)

    # Workspace templates
    templates_dir = Path(__file__).parent / "templates"
    template_store = TemplateStore(templates_dir)

    # Command API — single entry point for all control planes
    commands = Commands(
        session_manager, project_store, message_store,
        stt=stt,
        tunnel=tunnel_capability,
        base_path=base_path or None,
        template_store=template_store,
    )

    # Event renderer — subscribes to EventBus, renders to messenger
    renderer = EventRenderer(event_bus, messenger, message_store)
    renderer.start()

    # Orchestrator wires messenger callbacks to Commands
    _orchestrator = Orchestrator(messenger, commands)

    # Web control plane
    web_cp = WebControlPlane(
        commands, event_bus, message_store, LOG_FILE, port=web_port,
    )

    # Handle shutdown signals
    stop_event = asyncio.Event()

    def handle_signal() -> None:
        logger.info("Shutdown signal received")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, handle_signal)

    # Start Telegram bot + web control plane
    await web_cp.start()
    await messenger.start()
    logger.info("AFK is running. Press Ctrl+C to stop.")

    # Notify Telegram about recovered sessions
    for session in recovered_sessions:
        try:
            await messenger.send_message(
                session.channel_id,
                f"Session recovered: {session.name}\n"
                f"Agent resumed with previous context.",
                silent=True,
            )
        except Exception:
            logger.warning(
                "Failed to send recovery notification for %s", session.name
            )

    # Wait for shutdown
    await stop_event.wait()

    # Cleanup — suspend sessions (preserve worktrees for recovery)
    logger.info("Shutting down...")
    renderer.stop()
    await session_manager.suspend_all_sessions()
    await messenger.stop()
    await web_cp.stop()
    logger.info("AFK stopped.")


def run() -> None:
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    run()
