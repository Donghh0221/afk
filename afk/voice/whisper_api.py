from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from openai import OpenAI

logger = logging.getLogger(__name__)


class WhisperAPISTT:
    """STTPort implementation using OpenAI Whisper API."""

    def __init__(self, api_key: str, model: str = "whisper-1") -> None:
        self._client = OpenAI(api_key=api_key)
        self._model = model

    async def transcribe(self, audio_path: str) -> str:
        """Transcribe audio file via OpenAI Whisper API.

        Telegram voice messages are in ogg/opus format, which Whisper API
        supports directly — no ffmpeg conversion needed.
        """

        def _sync_transcribe() -> str:
            with open(audio_path, "rb") as audio_file:
                response = self._client.audio.transcriptions.create(
                    model=self._model,
                    file=audio_file,
                )
            return response.text

        loop = asyncio.get_running_loop()
        text = await loop.run_in_executor(None, _sync_transcribe)
        logger.info("Transcribed audio (%s): %s", Path(audio_path).name, text[:100])
        return text
