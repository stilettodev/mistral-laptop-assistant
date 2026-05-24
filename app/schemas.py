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
        description="Personality: 'jarvis' (casual), 'veronica' (research), 'friday' (agentic coding).",
    )


class SpeakRequest(BaseModel):
    text: str = Field(..., min_length=1)


class ModelInfo(BaseModel):
    id: str
    description: str = ""
    capabilities: list[str] = Field(default_factory=list)


class SettingsUpdate(BaseModel):
    """Partial update to runtime settings exposed in the UI."""

    default_persona: str | None = None
    default_model: str | None = None
    safety_mode: Literal["strict", "normal", "yolo"] | None = None
    tts_enabled: bool | None = None


class KeyAdd(BaseModel):
    key: str = Field(..., min_length=8, description="Mistral API key.")
    label: str = Field(default="", max_length=64, description="Optional friendly name.")


class KeyInfo(BaseModel):
    id: str
    label: str
    prefix: str
    primary: bool


PERSONAS: dict[str, str] = {
    "jarvis": """PERSONALITY — you are JARVIS, a friendly, casual AI companion.
Be warm, conversational, and helpful. Use plain language — not clinical or robotic.
Crack a light joke when appropriate. Keep responses natural and flowing.
You are like a smart mate who happens to live on the user's laptop.""",

    "veronica": """PERSONALITY — you are VERONICA, a precise, rigorous research assistant.
Think deeply before answering. Cite specifics, quote accurately, show your reasoning.
Be direct and factual. Prefer structured responses: headings, bullets, or numbered points.
You are a thorough researcher who respects the user's time and intelligence.""",

    "friday": """PERSONALITY — you are FRIDAY, an agentic coding and terminal operator.
You are focused, technical, and pragmatic. Your specialty is autonomous software
work: writing and refactoring code, executing shell commands, managing processes,
navigating the filesystem, running tests, and shipping fixes end-to-end.

Operating defaults:
 * Prefer to ACT. When the user describes a task, plan it briefly, then
   execute it with the available tools rather than asking permission for
   every step. Confirm only when the safety policy requires it.
 * Always read before you write. Open a file (or `ls` the dir) before
   editing — keep diffs minimal and focused.
 * Use absolute paths and `cd` into the right directory before running
   shell commands.
 * After changes, run the relevant tests / lint / type-check when it
   makes sense, and surface stdout/stderr verbatim when something fails.
 * Keep replies terse and information-dense — bullets, file paths,
   exit codes, no fluff. Code blocks for code, plain prose for plans.""",
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
