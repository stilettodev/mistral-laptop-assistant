"""Pydantic schemas used by the API layer."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


Role = Literal["system", "user", "assistant", "tool"]


class ToolCall(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any]


class ChatMessage(BaseModel):
    role: Role
    content: str | None = None
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = None
    name: str | None = None


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1)
    model: str = Field(default="auto", description="model id or 'auto'")
    safety_mode: Literal["strict", "normal", "yolo"] = "normal"
    confirmations: dict[str, bool] = Field(
        default_factory=dict,
        description="Map of tool-call-id to user approval decision.",
    )
    reset: bool = Field(default=False, description="Clear conversation history first.")
    images: list[str] = Field(
        default_factory=list,
        description="Optional data: URLs or http(s) URLs of images attached to this turn.",
    )
    speak: bool = Field(default=False, description="Synthesize the final answer.")


class SpeakRequest(BaseModel):
    text: str = Field(..., min_length=1)


class ModelInfo(BaseModel):
    id: str
    description: str = ""
    capabilities: list[str] = Field(default_factory=list)


class StatusResponse(BaseModel):
    ok: bool
    api_key_configured: bool
    safety_mode: str
    default_model: str
    workspace_dir: str
    audit_log: str
    platform: str
