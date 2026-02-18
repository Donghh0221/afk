from __future__ import annotations

import asyncio
import logging
import signal
import sys

from afk.config import Config
from afk.dashboard.message_store import MessageStore
from afk.dashboard.server import DashboardServer
from afk.messenger.telegram.adapter import TelegramAdapter
from afk.core.session_manager import SessionManager
from afk.core.orchestrator import Orchestrator
from afk.storage.project_store import ProjectStore

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

    # Load configuration
    config = Config.from_env()

    # Initialize components
    messenger = TelegramAdapter(config)
    project_store = ProjectStore(config.data_dir)
    session_manager = SessionManager(messenger, config.data_dir)
    message_store = MessageStore()

    # Clean up any orphan worktrees from previous crash
    await session_manager.cleanup_orphan_worktrees(project_store)

    # Orchestrator wires all callbacks
    _orchestrator = Orchestrator(
        messenger, session_manager, project_store, message_store,
    )

    # Dashboard web server
    dashboard = DashboardServer(
        session_manager, message_store, LOG_FILE, port=config.dashboard_port,
    )

    # Handle shutdown signals
    stop_event = asyncio.Event()

    def handle_signal() -> None:
        logger.info("Shutdown signal received")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, handle_signal)

    # Start Telegram bot + dashboard
    await dashboard.start()
    await messenger.start()
    logger.info("AFK is running. Press Ctrl+C to stop.")

    # Wait for shutdown
    await stop_event.wait()

    # Cleanup
    logger.info("Shutting down...")
    for session in session_manager.list_sessions():
        await session_manager.stop_session(session.channel_id)
    await messenger.stop()
    await dashboard.stop()
    logger.info("AFK stopped.")


def run() -> None:
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    run()
