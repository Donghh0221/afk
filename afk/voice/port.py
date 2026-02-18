from __future__ import annotations

from typing import Protocol


class STTPort(Protocol):
    """Speech-to-text abstract interface."""

    async def transcribe(self, audio_path: str) -> str:
        """Transcribe an audio file to text. Returns the transcription string."""
        ...
