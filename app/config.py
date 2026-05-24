"""Application configuration loaded from environment / .env file."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration for the assistant.

    Values are read from environment variables and an optional `.env`
    file in the project root. None of the secrets ever leave the host.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="MLA_",
        extra="ignore",
    )

    mistral_api_key: str = Field(default="", description="Mistral API key.")
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


settings = Settings()
