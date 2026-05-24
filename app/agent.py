"""Agent loop: drives the Mistral chat-completion API with tool calls.

The loop is structured as an **async generator** that yields structured
events (``status``, ``message``, ``tool_call``, ``tool_result``,
``confirmation_needed``, ``final``, ``error``). The web layer turns
those into Server-Sent Events for the browser.
"""

from __future__ import annotations

import asyncio
import json
import logging
import platform
import time
from collections.abc import AsyncGenerator
from typing import Any

from .config import settings
from .mistral_client import build_tool_schemas, get_client
from .router import route
from .safety import audit, evaluate
from .schemas import ChatRequest
from .tools import TOOLS

log = logging.getLogger(__name__)


SYSTEM_PROMPT = """You are MLA – the Mistral Laptop Assistant.

You run inside a process on the user's own computer ({system}, user
{user}). You can use the provided tools to automate ANY task on the
laptop: read/write files, run shell commands, list processes, open
apps, browse URLs, take screenshots, search the web, schedule tasks,
manage the clipboard, send notifications.

Operating rules:
 * Think first. Decide which tools (if any) are needed before answering.
 * Prefer the least invasive tool that gets the job done.
 * Chain multiple tool calls when needed – iterate until the user's
   goal is fully achieved.
 * For destructive operations the user may be prompted to confirm.
   If a confirmation is denied, stop and explain.
 * Always summarise what you did in plain language at the end.
 * NEVER fabricate results. If a tool fails, surface the real error.

The current working directory the user lives in is {home}. The audit
log of every tool call is written to {audit}."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_system_prompt() -> str:
    import os

    return SYSTEM_PROMPT.format(
        system=platform.platform(),
        user=os.environ.get("USER") or os.environ.get("USERNAME") or "you",
        home=str(settings.workspace_dir),
        audit=str(settings.audit_log),
    )


def _serialize_messages(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Strip internal fields and produce Mistral-compatible payloads."""
    out: list[dict[str, Any]] = []
    for msg in history:
        if msg["role"] == "tool":
            out.append(
                {
                    "role": "tool",
                    "name": msg.get("name", ""),
                    "tool_call_id": msg["tool_call_id"],
                    "content": msg["content"],
                }
            )
        elif msg["role"] == "assistant" and msg.get("tool_calls"):
            out.append(
                {
                    "role": "assistant",
                    "content": msg.get("content") or "",
                    "tool_calls": [
                        {
                            "id": tc["id"],
                            "type": "function",
                            "function": {
                                "name": tc["name"],
                                "arguments": json.dumps(tc["arguments"]),
                            },
                        }
                        for tc in msg["tool_calls"]
                    ],
                }
            )
        else:
            out.append({"role": msg["role"], "content": msg.get("content") or ""})
    return out


# ---------------------------------------------------------------------------
# Conversation store
# ---------------------------------------------------------------------------


class Conversation:
    """In-memory chat history (one per server process).

    A simple dict keyed by ``conversation_id`` keeps things stateless
    from the frontend's perspective: send the same id to continue.
    """

    def __init__(self) -> None:
        self._store: dict[str, list[dict[str, Any]]] = {}
        self._pending_calls: dict[str, list[dict[str, Any]]] = {}

    def get(self, cid: str) -> list[dict[str, Any]]:
        return self._store.setdefault(cid, [])

    def reset(self, cid: str) -> None:
        self._store.pop(cid, None)
        self._pending_calls.pop(cid, None)

    def pending(self, cid: str) -> list[dict[str, Any]]:
        return self._pending_calls.get(cid, [])

    def set_pending(self, cid: str, calls: list[dict[str, Any]]) -> None:
        if calls:
            self._pending_calls[cid] = calls
        else:
            self._pending_calls.pop(cid, None)


CONVERSATIONS = Conversation()


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------


def _select_model(req: ChatRequest) -> tuple[str, str, str]:
    """Resolve the user/auto model selection. Returns (model, reason, via)."""
    chosen = req.model or settings.default_model or "auto"
    if chosen == "auto":
        decision = route(req.message)
        return decision.model, decision.reason, decision.via
    return chosen, "user selected", "user"


async def run_agent(
    conversation_id: str, request: ChatRequest
) -> AsyncGenerator[dict[str, Any], None]:
    """Drive Mistral chat with tools, yielding SSE-friendly events."""
    history = CONVERSATIONS.get(conversation_id)

    if request.reset:
        CONVERSATIONS.reset(conversation_id)
        history = CONVERSATIONS.get(conversation_id)

    if not history:
        history.append({"role": "system", "content": _build_system_prompt()})

    # Replay any pending tool calls awaiting confirmation.
    pending = CONVERSATIONS.pending(conversation_id)

    if pending:
        # We are resuming: do NOT append the new user message; just run
        # the pending tool calls now that we have confirmations.
        yield {"type": "status", "data": "Resuming after confirmation…"}
    else:
        history.append({"role": "user", "content": request.message})
        model, reason, via = _select_model(request)
        yield {
            "type": "model",
            "data": {"model": model, "reason": reason, "via": via},
        }
        audit("chat_start", {"cid": conversation_id, "model": model, "msg": request.message[:300]})

    # When resuming, recover the model from the audit log of the request.
    if pending:
        model = request.model if request.model != "auto" else "mistral-medium-latest"

    client = get_client()
    tools_schema = build_tool_schemas()
    step = 0

    while step < settings.max_agent_steps:
        step += 1

        # If we have pending tool calls awaiting confirmation, execute now.
        if pending:
            tool_calls = pending
            pending = []  # consumed
            CONVERSATIONS.set_pending(conversation_id, [])
            assistant_text = ""
        else:
            yield {"type": "status", "data": f"Thinking with {model} (step {step})…"}
            try:
                response = await asyncio.to_thread(
                    client.chat.complete,
                    model=model,
                    messages=_serialize_messages(history),
                    tools=tools_schema,
                    tool_choice="auto",
                    temperature=0.2,
                )
            except Exception as exc:
                audit("chat_error", {"cid": conversation_id, "err": str(exc)})
                yield {"type": "error", "data": f"Mistral API error: {exc}"}
                return

            choice = response.choices[0].message
            assistant_text = choice.content or ""
            raw_tool_calls = getattr(choice, "tool_calls", None) or []
            tool_calls = []
            for tc in raw_tool_calls:
                fn = tc.function
                args_raw = fn.arguments
                try:
                    args = json.loads(args_raw) if isinstance(args_raw, str) else (args_raw or {})
                except json.JSONDecodeError:
                    args = {"_raw": args_raw}
                tool_calls.append(
                    {"id": tc.id, "name": fn.name, "arguments": args}
                )

            history.append(
                {
                    "role": "assistant",
                    "content": assistant_text,
                    "tool_calls": tool_calls or None,
                }
            )

            if assistant_text:
                yield {"type": "message", "data": assistant_text}

            if not tool_calls:
                yield {"type": "final", "data": assistant_text}
                audit("chat_end", {"cid": conversation_id, "steps": step})
                return

        # Evaluate safety for every requested call.
        needs_confirm: list[dict[str, Any]] = []
        approved: list[dict[str, Any]] = []
        for tc in tool_calls:
            decision = evaluate(
                tc["name"],
                tc["arguments"],
                request.safety_mode,
                request.confirmations,
                tc["id"],
            )
            if decision.needs_confirmation:
                needs_confirm.append(tc)
            elif decision.allowed:
                approved.append(tc)
            else:
                # Denied – synthesise a tool result reporting the denial.
                history.append(
                    {
                        "role": "tool",
                        "name": tc["name"],
                        "tool_call_id": tc["id"],
                        "content": json.dumps(
                            {"ok": False, "error": decision.reason}
                        ),
                    }
                )
                yield {
                    "type": "tool_result",
                    "data": {
                        "id": tc["id"],
                        "name": tc["name"],
                        "result": {"ok": False, "error": decision.reason},
                        "denied": True,
                    },
                }

        if needs_confirm:
            CONVERSATIONS.set_pending(conversation_id, tool_calls)
            yield {
                "type": "confirmation_needed",
                "data": [
                    {
                        "id": tc["id"],
                        "name": tc["name"],
                        "arguments": tc["arguments"],
                    }
                    for tc in needs_confirm
                ],
            }
            audit(
                "confirmation_needed",
                {"cid": conversation_id, "calls": [c["name"] for c in needs_confirm]},
            )
            return

        # Execute approved tool calls in order.
        for tc in approved:
            yield {
                "type": "tool_call",
                "data": {"id": tc["id"], "name": tc["name"], "arguments": tc["arguments"]},
            }
            fn = TOOLS.get(tc["name"])
            if fn is None:
                result: dict[str, Any] = {"ok": False, "error": f"unknown tool {tc['name']!r}"}
            else:
                try:
                    started = time.time()
                    result = await asyncio.to_thread(fn, **tc["arguments"])
                    result.setdefault("duration_ms", int((time.time() - started) * 1000))
                except TypeError as exc:
                    result = {"ok": False, "error": f"bad arguments: {exc}"}
                except Exception as exc:  # noqa: BLE001 – we want to surface anything
                    result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
            audit(
                "tool_executed",
                {
                    "cid": conversation_id,
                    "tool": tc["name"],
                    "args": tc["arguments"],
                    "ok": bool(result.get("ok")),
                },
            )
            history.append(
                {
                    "role": "tool",
                    "name": tc["name"],
                    "tool_call_id": tc["id"],
                    "content": json.dumps(result, default=str),
                }
            )
            yield {
                "type": "tool_result",
                "data": {"id": tc["id"], "name": tc["name"], "result": result},
            }

    yield {
        "type": "error",
        "data": f"Hit max_agent_steps={settings.max_agent_steps} without finishing.",
    }
