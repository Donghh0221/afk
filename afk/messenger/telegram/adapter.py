from __future__ import annotations

import logging
from typing import Callable, Awaitable

from telegram import (
    Bot,
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
from telegram.constants import ParseMode

from afk.config import Config

logger = logging.getLogger(__name__)

# Telegram max message length
MAX_MESSAGE_LENGTH = 4096


def _split_message(text: str) -> list[str]:
    """Split messages exceeding 4096 characters."""
    if len(text) <= MAX_MESSAGE_LENGTH:
        return [text]
    chunks: list[str] = []
    while text:
        if len(text) <= MAX_MESSAGE_LENGTH:
            chunks.append(text)
            break
        # Split at newline boundary
        split_at = text.rfind("\n", 0, MAX_MESSAGE_LENGTH)
        if split_at == -1:
            split_at = MAX_MESSAGE_LENGTH
        chunks.append(text[:split_at])
        text = text[split_at:].lstrip("\n")
    return chunks


class TelegramAdapter:
    """MessengerPort implementation based on Telegram forum topics."""

    def __init__(self, config: Config) -> None:
        self._config = config
        self._app: Application | None = None
        self._group_id = config.telegram_group_id

        # Callback storage
        self._on_text: Callable[[str, str], Awaitable[None]] | None = None
        self._on_command: dict[str, Callable[..., Awaitable[None]]] = {}
        self._on_permission_response: Callable[
            [str, str, str], Awaitable[None]
        ] | None = None  # (channel_id, request_id, choice)

    def set_on_text(
        self, callback: Callable[[str, str], Awaitable[None]]
    ) -> None:
        """Text message callback: (channel_id, text) -> None"""
        self._on_text = callback

    def set_on_command(
        self, command: str, callback: Callable[..., Awaitable[None]]
    ) -> None:
        """Register command callback."""
        self._on_command[command] = callback

    def set_on_permission_response(
        self,
        callback: Callable[[str, str, str], Awaitable[None]],
    ) -> None:
        """Permission response callback: (channel_id, request_id, choice) -> None"""
        self._on_permission_response = callback

    def _get_channel_id(self, update: Update) -> str:
        """Extract channel_id from Update. Returns thread_id for forum topics, 'general' otherwise."""
        thread_id = update.effective_message.message_thread_id
        if thread_id:
            return str(thread_id)
        return "general"

    async def send_message(
        self, channel_id: str, text: str, *, silent: bool = False
    ) -> str:
        """Send a message."""
        bot = self._app.bot
        thread_id = int(channel_id) if channel_id != "general" else None

        chunks = _split_message(text)
        last_msg = None
        for chunk in chunks:
            last_msg = await bot.send_message(
                chat_id=self._group_id,
                message_thread_id=thread_id,
                text=chunk,
                disable_notification=silent,
            )
        return str(last_msg.message_id) if last_msg else ""

    async def edit_message(
        self, channel_id: str, message_id: str, text: str
    ) -> None:
        """Edit an existing message."""
        bot = self._app.bot
        try:
            await bot.edit_message_text(
                chat_id=self._group_id,
                message_id=int(message_id),
                text=text[:MAX_MESSAGE_LENGTH],
            )
        except Exception as e:
            logger.warning("Failed to edit message %s: %s", message_id, e)

    async def send_permission_request(
        self,
        channel_id: str,
        tool_name: str,
        tool_args: str,
        request_id: str,
    ) -> None:
        """Display permission approval request with inline buttons."""
        bot = self._app.bot
        thread_id = int(channel_id) if channel_id != "general" else None

        # Summarize tool args (truncate if too long)
        args_summary = tool_args[:500] + "..." if len(tool_args) > 500 else tool_args
        text = f"⚠️ Tool execution request\n🔧 {tool_name}: {args_summary}"

        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "✅ Allow",
                        callback_data=f"perm:{request_id}:allow",
                    ),
                    InlineKeyboardButton(
                        "❌ Deny",
                        callback_data=f"perm:{request_id}:deny",
                    ),
                ],
            ]
        )

        await bot.send_message(
            chat_id=self._group_id,
            message_thread_id=thread_id,
            text=text,
            reply_markup=keyboard,
            disable_notification=False,
        )

    async def create_session_channel(self, name: str) -> str:
        """Create forum topic. Returns: thread_id string."""
        bot = self._app.bot
        topic = await bot.create_forum_topic(
            chat_id=self._group_id,
            name=name,
        )
        return str(topic.message_thread_id)

    async def _handle_text(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Text message handler."""
        if not update.effective_message or not update.effective_message.text:
            return
        # Ignore messages from the bot itself
        if update.effective_message.from_user and update.effective_message.from_user.is_bot:
            return
        channel_id = self._get_channel_id(update)
        text = update.effective_message.text
        if self._on_text:
            await self._on_text(channel_id, text)

    async def _handle_callback(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Inline button callback handler."""
        query = update.callback_query
        if not query or not query.data:
            return
        await query.answer()

        data = query.data
        if data.startswith("perm:"):
            parts = data.split(":", 2)
            if len(parts) == 3:
                _, request_id, choice = parts
                channel_id = self._get_channel_id(update)
                # Update button text
                choice_text = "✅ Allowed" if choice == "allow" else "❌ Denied"
                await query.edit_message_text(
                    text=query.message.text + f"\n\n→ {choice_text}"
                )
                if self._on_permission_response:
                    await self._on_permission_response(
                        channel_id, request_id, choice
                    )

    async def _make_command_handler(
        self, cmd: str
    ) -> Callable:
        """Command handler factory."""
        callback = self._on_command[cmd]

        async def handler(
            update: Update, context: ContextTypes.DEFAULT_TYPE
        ) -> None:
            if not update.effective_message:
                return
            channel_id = self._get_channel_id(update)
            args = context.args or []
            await callback(channel_id, args)

        return handler

    async def start(self) -> None:
        """Start Telegram bot (polling)."""
        builder = Application.builder().token(self._config.telegram_bot_token)
        self._app = builder.build()

        # Register command handlers
        for cmd in self._on_command:
            handler = await self._make_command_handler(cmd)
            self._app.add_handler(CommandHandler(cmd, handler))

        # Text handler (non-command messages)
        self._app.add_handler(
            MessageHandler(
                filters.TEXT & ~filters.COMMAND,
                self._handle_text,
            )
        )

        # Inline button callback handler
        self._app.add_handler(CallbackQueryHandler(self._handle_callback))

        logger.info("Starting Telegram polling...")
        await self._app.initialize()
        await self._app.start()
        await self._app.updater.start_polling(drop_pending_updates=True)

    async def stop(self) -> None:
        """Stop Telegram bot."""
        if self._app:
            await self._app.updater.stop()
            await self._app.stop()
            await self._app.shutdown()
