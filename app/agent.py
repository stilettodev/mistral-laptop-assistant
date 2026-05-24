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

from . import storage
from .config import settings
from .memory import context_block
from .mistral_client import build_tool_schemas, get_client
from .router import route
from .safety import audit, evaluate
from .schemas import ChatRequest, PERSONAS
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
log of every tool call is written to {audit}.

{persona}
"""

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_system_prompt(persona: str = "jarvis") -> str:
    import os

    persona_block = PERSONAS.get(persona, PERSONAS["jarvis"])
    base = SYSTEM_PROMPT.format(
        system=platform.platform(),
        user=os.environ.get("USER") or os.environ.get("USERNAME") or "you",
        home=str(settings.workspace_dir),
        audit=str(settings.audit_log),
        persona=persona_block,
    )
    mem = context_block()
    if mem:
        base += "\n\n" + mem
    return base


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
        elif msg["role"] == "user" and msg.get("images"):
            # Multimodal user message: list of content parts.
            parts: list[dict[str, Any]] = [
                {"type": "text", "text": msg.get("content") or ""}
            ]
            for img_url in msg["images"]:
                parts.append({"type": "image_url", "image_url": img_url})
            out.append({"role": "user", "content": parts})
        else:
            out.append({"role": msg["role"], "content": msg.get("content") or ""})
    return out


# Brief nudge appended after tool-result batches so the model gives a
# concise final answer instead of calling more tools.
_FINISH_GUIDANCE = (
    " — Tool results are above. Provide a short, direct final answer now. "
    "Do NOT call any more tools."
)


def _synthesise_results(
    model: str,
    tools_schema: list[dict[str, Any]],
    history: list[dict[str, Any]],
    mc: Any,
) -> tuple[str | None, list[dict[str, Any]]]:
    """Append a guidance message and call chat.complete once for the final answer.

    Returns (final_text, new_tool_calls). The caller yields the text and
    handles any remaining tool calls in the next loop iteration.
    This does NOT count as a new step.
    """
    history.append(
        {"role": "user", "content": _FINISH_GUIDANCE}
    )
    try:
        response = mc.client().chat.complete(
            model=model,
            messages=_serialize_messages(history),
            tools=tools_schema,
            tool_choice="auto",
            temperature=0.2,
        )
    except Exception as exc:
        log.warning("synthesis call failed: %s", exc)
        history.pop()
        return None, []

    history.pop()

    choice = response.choices[0].message
    final_text = choice.content or ""
    raw_tc = getattr(choice, "tool_calls", None) or []
    tool_calls_out = [
        {"id": tc.id, "name": tc.function.name, "arguments": json.loads(tc.function.arguments)}
        for tc in raw_tc
    ]

    if not tool_calls_out:
        history.append({"role": "assistant", "content": final_text})
        return final_text, []

    # Model still wants tools — leave history as-is (the pending branch will
    # re-add the assistant message naturally) and return the calls so the
    # caller can set pending.
    return "", tool_calls_out


# ---------------------------------------------------------------------------
# Conversation store
# ---------------------------------------------------------------------------


class Conversation:
    """In-memory chat history (one per server process).

    A simple dict keyed by ``conversation_id`` keeps things stateless
    from the frontend's perspective: send the same id to continue.
    Conversations are also persisted to disk so they survive restarts.
    """

    def __init__(self) -> None:
        self._store: dict[str, list[dict[str, Any]]] = {}
        self._pending_calls: dict[str, list[dict[str, Any]]] = {}

    def get(self, cid: str) -> list[dict[str, Any]]:
        if cid not in self._store:
            # Try loading from disk first.
            persisted = storage.load(cid)
            self._store[cid] = persisted if persisted is not None else []
        return self._store[cid]

    def reset(self, cid: str) -> None:
        self._store.pop(cid, None)
        self._pending_calls.pop(cid, None)
        storage.delete(cid)

    def pending(self, cid: str) -> list[dict[str, Any]]:
        return self._pending_calls.get(cid, [])

    def set_pending(self, cid: str, calls: list[dict[str, Any]]) -> None:
        if calls:
            self._pending_calls[cid] = calls
        else:
            self._pending_calls.pop(cid, None)

    def persist(self, cid: str) -> None:
        if cid in self._store:
            storage.save(cid, self._store[cid])


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

    persona = request.persona or settings.default_persona
    if not history:
        history.append({"role": "system", "content": _build_system_prompt(persona)})

    # Replay any pending tool calls awaiting confirmation.
    pending = CONVERSATIONS.pending(conversation_id)

    if pending:
        # We are resuming: do NOT append the new user message; just run
        # the pending tool calls now that we have confirmations.
        yield {"type": "status", "data": "Resuming after confirmation…"}
    else:
        user_msg: dict[str, Any] = {"role": "user", "content": request.message}
        if request.images:
            user_msg["images"] = request.images
        history.append(user_msg)
        model, reason, via = _select_model(request)
        yield {
            "type": "model",
            "data": {"model": model, "reason": reason, "via": via},
        }
        audit("chat_start", {"cid": conversation_id, "model": model, "msg": request.message[:300]})

    # When resuming, recover the model from the audit log of the request.
    if pending:
        model = request.model if request.model != "auto" else "mistral-medium-latest"

    from .mistral_client import MultiKeyClient
    mc = MultiKeyClient()
    tools_schema = build_tool_schemas()
    step = 0

    while step < settings.max_agent_steps:
        # Execute all pending tool calls as one batch (they are a consequence
        # of the single model call that produced them — don't charge a step).
        if pending:
            tool_calls = pending
            pending = []  # consumed
            CONVERSATIONS.set_pending(conversation_id, [])
            assistant_text = ""
            # NO step += 1 here — this is a continuation, not a new model step.
        else:
            step += 1
            yield {"type": "status", "data": f"Thinking with {model} (step {step})…"}
            try:
                response = await asyncio.to_thread(
                    mc.client().chat.complete,
                    model=model,
                    messages=_serialize_messages(history),
                    tools=tools_schema,
                    tool_choice="auto",
                    temperature=0.2,
                )
            except Exception as exc:
                err_str = str(exc).lower()
                if any(x in err_str for x in ["401", "unauthorized", "403", "forbidden"])                        or any(x in err_str for x in ["429", "rate limit", "quota"]):
                    next_key = mc.rotate()
                    if next_key:
                        audit("key_rotate", {"cid": conversation_id, "from": mc.current_key[:6]})
                        continue
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
                CONVERSATIONS.persist(conversation_id)
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
            CONVERSATIONS.persist(conversation_id)
            return

        # Execute approved tool calls in order, then hand all results to the model
        # in a single follow-up call (saves steps and avoids the model re-calling
        # the same tools when it sees partial results).
        if approved:
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
                    except Exception as exc:  # noqa: BLE001
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
                tool_result_entry = {
                    "role": "tool",
                    "name": tc["name"],
                    "tool_call_id": tc["id"],
                    "content": json.dumps(result, default=str),
                }
                history.append(tool_result_entry)
                yield {
                    "type": "tool_result",
                    "data": {"id": tc["id"], "name": tc["name"], "result": result},
                }

            # After batch execution, synthesise all results for the model in one
            # go so it can form a final answer rather than issuing more tool calls.
            # This does NOT charge a step.
            if not needs_confirm:
                final_text, new_tc = _synthesise_results(model, tools_schema, history, mc)
                if final_text:
                    yield {"type": "final", "data": final_text}
                    audit("chat_end", {"cid": conversation_id, "steps": step})
                    CONVERSATIONS.persist(conversation_id)
                    return
                if new_tc:
                    # Model still wants tools — they'll be handled on the next loop
                    # iteration (which charges a step since pending=[] there).
                    pending = new_tc
                    continue

    yield {
        "type": "error",
        "data": f"Hit max_agent_steps={settings.max_agent_steps} without finishing.",
    }
    CONVERSATIONS.persist(conversation_id)


async def chat_oneshot(prompt: str) -> str:
    """Run the assistant for a single prompt and return the final text.

    Used by the scheduler for ``chat`` jobs. Always runs in ``yolo``
    safety mode so background jobs don't block on confirmations.
    """
    import uuid as _uuid

    cid = "scheduler-" + _uuid.uuid4().hex[:8]
    request = ChatRequest(message=prompt, model="auto", safety_mode="yolo")
    parts: list[str] = []
    async for event in run_agent(cid, request):
        if event["type"] in {"message", "final"} and event["data"]:
            parts.append(str(event["data"]))
    CONVERSATIONS.reset(cid)  # don't pollute disk with scheduler runs
    return parts[-1] if parts else ""
