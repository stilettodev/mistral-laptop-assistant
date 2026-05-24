"""Application configuration loaded from environment / .env file."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration for the assistant.

    Values are read from environment variables and an optional `.env`
    file in the project root or `~/.mistral_assistant.env`.
    None of the secrets ever leave the host.
    """

    model_config = SettingsConfigDict(
        env_file=[
            str(Path.home() / ".mistral_assistant.env"),
            ".env",
        ],
        env_file_encoding="utf-8",
        env_prefix="MLA_",
        extra="ignore",
    )

    # ── API Keys ─────────────────────────────────────────────────────────────
    mistral_api_key: str = Field(default="", description="Primary Mistral API key.")
    mistral_api_keys: str = Field(
        default="",
        description=(
            "Additional fallback API keys, comma-separated. "
            "Used when the primary key hits a 429/401 error. "
            "Format: key1,key2,key3 (primary key is always tried first)"
        ),
    )

    default_model: str = Field(
        default="auto",
        description="Default model id or 'auto' for automatic routing.",
    )
    router_model: str = Field(
        default="ministral-3b-latest",
        description="Small/cheap model used to pick a model in 'auto' mode.",
    )

    host: str = Field(default="127.0.0.1")
    port: int = Field(default=8000)

    # ── Personas ──────────────────────────────────────────────────────────────
    default_persona: Literal["jarvis", "veronica"] = Field(
        default="jarvis",
        description="Default personality: 'jarvis' (casual) or 'veronica' (research)",
    )

    # Safety
    safety_mode: Literal["strict", "normal", "yolo"] = Field(default="normal")
    workspace_dir: Path = Field(default_factory=lambda: Path.home())
    audit_log: Path = Field(
        default_factory=lambda: Path.home() / ".mistral_assistant_audit.log"
    )

    # Behaviour
    max_agent_steps: int = Field(default=20)
    shell_timeout_seconds: int = Field(default=120)
    history_path: Path = Field(
        default_factory=lambda: Path.home() / ".mistral_assistant_history.json"
    )

    # Persistent state
    scheduler_file: Path = Field(
        default_factory=lambda: Path.home() / ".mistral_assistant_jobs.json"
    )
    memory_file: Path = Field(
        default_factory=lambda: Path.home() / ".mistral_assistant_memory.json"
    )
    conversations_dir: Path = Field(
        default_factory=lambda: Path.home() / ".mistral_assistant_chats"
    )
    uploads_dir: Path = Field(
        default_factory=lambda: Path.home() / ".mistral_assistant_uploads"
    )

    # Voice
    stt_model: str = Field(default="voxtral-mini-latest")
    tts_model: str = Field(default="")  # blank = use server default
    tts_voice: str = Field(default="")  # blank = use server default
    tts_enabled: bool = Field(default=False)

    # Per-tool gating (comma-separated names; empty = no restriction)
    allow_tools: str = Field(default="")
    deny_tools: str = Field(default="")

    # ── Helpers ───────────────────────────────────────────────────────────────
    @property
    def all_api_keys(self) -> list[str]:
        """Return all API keys in priority order (primary first, then fallbacks)."""
        keys = []
        if self.mistral_api_key:
            keys.append(self.mistral_api_key)
        if self.mistral_api_keys:
            for k in self.mistral_api_keys.split(","):
                k = k.strip()
                if k and k not in keys:
                    keys.append(k)
        return keys


settings = Settings()
