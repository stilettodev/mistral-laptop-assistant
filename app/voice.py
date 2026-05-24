"""Voice I/O – Mistral STT (voxtral) and TTS.

Used by ``/api/voice/transcribe`` (browser uploads mic audio) and
``/api/voice/speak`` (server returns synthesized speech).

Both endpoints degrade gracefully when the relevant Mistral model is
unavailable in the account – the UI hides the buttons in that case.
"""

from __future__ import annotations

import logging
from typing import Any

from .config import settings
from .mistral_client import get_client

log = logging.getLogger(__name__)


def transcribe(audio_bytes: bytes, filename: str = "input.webm") -> dict[str, Any]:
    """Transcribe spoken audio to text using a Mistral voxtral model."""
    client = get_client()
    try:
        resp = client.audio.transcriptions.complete(
            model=settings.stt_model,
            file={"file_name": filename, "content": audio_bytes},
        )
        text = getattr(resp, "text", None) or ""
        return {"ok": True, "text": text}
    except Exception as exc:  # noqa: BLE001 – surface raw API errors
        log.warning("transcription failed: %s", exc)
        return {"ok": False, "error": str(exc)}


def synthesize(text: str) -> tuple[bytes | None, str | None]:
    """Synthesize speech audio. Returns (audio_bytes_or_None, error)."""
    client = get_client()
    kwargs: dict[str, Any] = {
        "input": text[:4000],
        "response_format": "mp3",
    }
    if settings.tts_model:
        kwargs["model"] = settings.tts_model
    if settings.tts_voice:
        kwargs["voice_id"] = settings.tts_voice
    try:
        resp = client.audio.speech.complete(**kwargs)
        # The SDK returns a streamable HTTP response; the bytes live on
        # different attributes depending on version. Try the common ones.
        for attr in ("audio", "content", "data", "body"):
            data = getattr(resp, attr, None)
            if isinstance(data, (bytes, bytearray)):
                return bytes(data), None
        # mistralai 2.x uses an httpx Response-like object
        raw = getattr(resp, "raw_response", None)
        if raw is not None and hasattr(raw, "content"):
            return raw.content, None
        return None, "TTS response did not contain audio bytes"
    except Exception as exc:  # noqa: BLE001
        log.warning("synthesize failed: %s", exc)
        return None, str(exc)


def available() -> dict[str, bool]:
    """Best-effort capability probe – just checks the API key is set."""
    return {
        "stt": bool(settings.mistral_api_key),
        "tts": bool(settings.mistral_api_key) and settings.tts_enabled,
    }
