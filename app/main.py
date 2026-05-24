"""FastAPI entry point.

Exposes a small REST + SSE API consumed by the bundled web UI:

* ``GET  /api/status`` – host info, API-key state, current safety mode.
* ``GET  /api/models`` – available Mistral models (live + curated).
* ``POST /api/chat`` – send a user message; streams agent events as
  Server-Sent Events.
* ``GET  /``           – the single-page web app.
"""

from __future__ import annotations

import asyncio
import json
import logging
import platform
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .agent import CONVERSATIONS, run_agent
from .config import settings
from .mistral_client import list_models
from .schemas import ChatRequest, StatusResponse

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("mla")

ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = ROOT / "web"

app = FastAPI(title="Mistral Laptop Assistant", version="0.1.0")


@app.get("/api/status", response_model=StatusResponse)
def status() -> StatusResponse:
    return StatusResponse(
        ok=True,
        api_key_configured=bool(settings.mistral_api_key),
        safety_mode=settings.safety_mode,
        default_model=settings.default_model,
        workspace_dir=str(settings.workspace_dir),
        audit_log=str(settings.audit_log),
        platform=platform.platform(),
    )


@app.get("/api/models")
def models() -> dict[str, object]:
    return {"models": list_models()}


@app.post("/api/conversations/{cid}/reset")
def reset_conversation(cid: str) -> dict[str, bool]:
    CONVERSATIONS.reset(cid)
    return {"ok": True}


@app.post("/api/chat")
async def chat(payload: ChatRequest, request: Request) -> StreamingResponse:
    if not settings.mistral_api_key:
        raise HTTPException(
            status_code=400,
            detail="Mistral API key not configured. Set MLA_MISTRAL_API_KEY in your .env.",
        )

    cid = request.headers.get("x-conversation-id") or str(uuid.uuid4())

    async def event_stream():
        # Tell the client the conversation id up front.
        yield _sse("conversation", {"id": cid})
        try:
            async for event in run_agent(cid, payload):
                if await request.is_disconnected():
                    log.info("client disconnected, aborting agent loop for %s", cid)
                    break
                yield _sse(event["type"], event["data"])
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.exception("agent crashed")
            yield _sse("error", f"internal error: {exc}")
        finally:
            yield _sse("done", {})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Conversation-Id": cid,
            "X-Accel-Buffering": "no",
        },
    )


def _sse(event: str, data: object) -> str:
    payload = data if isinstance(data, str) else json.dumps(data, default=str)
    return f"event: {event}\ndata: {payload}\n\n"


# Static UI ------------------------------------------------------------------

app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(str(WEB_DIR / "index.html"))
