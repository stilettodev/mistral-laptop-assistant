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
    persona: str = Field(
        default="",
        description="Personality: 'jarvis' (casual) or 'veronica' (research).",
    )


class SpeakRequest(BaseModel):
    text: str = Field(..., min_length=1)


class ModelInfo(BaseModel):
    id: str
    description: str = ""
    capabilities: list[str] = Field(default_factory=list)


PERSONAS: dict[str, str] = {
    "jarvis": """PERSONALITY — you are JARVIS, a friendly, casual AI companion.
Be warm, conversational, and helpful. Use plain language — not clinical or robotic.
Crack a light joke when appropriate. Keep responses natural and flowing.
You are like a smart mate who happens to live on the user's laptop.""",

    "veronica": """PERSONALITY — you are VERONICA, a precise, rigorous research assistant.
Think deeply before answering. Cite specifics, quote accurately, show your reasoning.
Be direct and factual. Prefer structured responses: headings, bullets, or numbered points.
You are a thorough researcher who respects the user's time and intelligence.""",
}


class StatusResponse(BaseModel):
    ok: bool
    api_key_configured: bool
    safety_mode: str
    default_model: str
    default_persona: str
    workspace_dir: str
    audit_log: str
    platform: str
