"""FastAPI entry point.

Exposes a small REST + SSE API consumed by the bundled web UI:

* ``GET  /api/status``  – host info, API-key state, current safety mode.
* ``GET  /api/models``  – available Mistral models (live + curated).
* ``POST /api/chat``    – send a user message; streams agent events as SSE.
* ``GET  /api/conversations``         – list saved chats.
* ``POST /api/conversations/{cid}/reset`` – delete a chat.
* ``POST /api/upload``  – upload a file (image) for the next chat turn.
* ``POST /api/voice/transcribe`` – upload mic audio → text (Mistral STT).
* ``POST /api/voice/speak``      – text → audio bytes (Mistral TTS).
* ``GET  /api/jobs``    – list recurring jobs (the scheduler).
* ``GET  /``            – the single-page web app.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import logging
import mimetypes
import platform
import uuid
from pathlib import Path

from fastapi import (
    FastAPI,
    File,
    HTTPException,
    Request,
    UploadFile,
)
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .agent import CONVERSATIONS, chat_oneshot, run_agent
from .config import settings
from .mistral_client import list_models
from .scheduler import SCHEDULER
from .schemas import (
    ChatRequest,
    KeyAdd,
    KeyInfo,
    PERSONAS,
    SettingsUpdate,
    SpeakRequest,
    StatusResponse,
)
from . import keystore, storage, voice

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("mla")

ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = ROOT / "web"


@contextlib.asynccontextmanager
async def lifespan(_: FastAPI):
    from .tools import TOOLS

    SCHEDULER.set_chat_executor(chat_oneshot)
    SCHEDULER.start()
    log.info(
        "MLA ready: %d tool(s) loaded · safety=%s · workspace=%s",
        len(TOOLS),
        settings.safety_mode,
        settings.workspace_dir,
    )
    try:
        yield
    finally:
        await SCHEDULER.stop()


app = FastAPI(title="Mistral Laptop Assistant", version="0.2.0", lifespan=lifespan)


# ── status / models ──────────────────────────────────────────────────────


@app.get("/api/status", response_model=StatusResponse)
def status() -> StatusResponse:
    return StatusResponse(
        ok=True,
        api_key_configured=bool(settings.all_api_keys),
        safety_mode=settings.safety_mode,
        default_model=settings.default_model,
        default_persona=settings.default_persona,
        workspace_dir=str(settings.workspace_dir),
        audit_log=str(settings.audit_log),
        platform=platform.platform(),
    )


@app.get("/api/capabilities")
def capabilities() -> dict[str, object]:
    return {
        "voice": voice.available(),
        "allow_tools": settings.allow_tools,
        "deny_tools": settings.deny_tools,
        "default_model": settings.default_model,
        "default_safety": settings.safety_mode,
        "tts_enabled": settings.tts_enabled,
    }


# ── settings (live, UI-managed) ─────────────────────────────────────────


_PERSONA_META = {
    "jarvis":   {"label": "Jarvis",   "icon": "☕", "sub": "Companion"},
    "veronica": {"label": "Veronica", "icon": "🔬", "sub": "Research"},
    "friday":   {"label": "Friday",   "icon": "🛠️", "sub": "Coding"},
}


@app.get("/api/settings")
def get_settings() -> dict[str, object]:
    return {
        "default_persona": settings.default_persona,
        "default_model": settings.default_model,
        "safety_mode": settings.safety_mode,
        "tts_enabled": settings.tts_enabled,
        "personas": [
            {
                "id": pid,
                "label": meta["label"],
                "icon": meta["icon"],
                "sub": meta["sub"],
                "description": PERSONAS.get(pid, "").split("\n", 1)[0],
            }
            for pid, meta in _PERSONA_META.items()
            if pid in PERSONAS
        ],
        "key_count": len(settings.all_api_keys),
    }


@app.put("/api/settings")
def put_settings(update: SettingsUpdate) -> dict[str, object]:
    if update.default_persona is not None:
        if update.default_persona not in PERSONAS:
            raise HTTPException(400, f"unknown persona: {update.default_persona!r}")
        settings.default_persona = update.default_persona  # type: ignore[assignment]
    if update.default_model is not None:
        settings.default_model = update.default_model
    if update.safety_mode is not None:
        settings.safety_mode = update.safety_mode
    if update.tts_enabled is not None:
        settings.tts_enabled = update.tts_enabled
    return get_settings()


# ── API keys (multi-key fallback pool) ──────────────────────────────────


@app.get("/api/keys")
def list_keys() -> dict[str, object]:
    """Return masked metadata for every stored key (UI + env)."""
    items: list[dict[str, object]] = [k.model_dump() for k in keystore.list_keys()]
    env_keys = []
    if settings.mistral_api_key:
        env_keys.append(settings.mistral_api_key)
    if settings.mistral_api_keys:
        env_keys.extend(k.strip() for k in settings.mistral_api_keys.split(",") if k.strip())
    for i, k in enumerate(env_keys):
        masked = (k[:4] + "…" + k[-4:]) if len(k) > 8 else "•" * len(k)
        items.append({
            "id": f"env-{i}",
            "label": "env" if i == 0 else f"env fallback {i}",
            "prefix": masked,
            "primary": not items and i == 0,
            "source": "env",
        })
    return {"keys": items, "total": len(items)}


@app.post("/api/keys", response_model=KeyInfo)
def add_key(payload: KeyAdd) -> KeyInfo:
    try:
        return keystore.add_key(payload.key, payload.label)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.delete("/api/keys/{key_id}")
def delete_key(key_id: str) -> dict[str, bool]:
    if key_id.startswith("env-"):
        raise HTTPException(400, "env-provided keys cannot be removed from the UI")
    if not keystore.remove_key(key_id):
        raise HTTPException(404, "key not found")
    return {"ok": True}


@app.get("/api/models")
def models() -> dict[str, object]:
    return {"models": list_models()}


# ── conversations ────────────────────────────────────────────────────────


@app.get("/api/conversations")
def list_conversations() -> dict[str, object]:
    return {"conversations": storage.list_conversations()}


@app.get("/api/conversations/{cid}")
def get_conversation(cid: str) -> dict[str, object]:
    messages = storage.load(cid)
    if messages is None:
        raise HTTPException(404, "conversation not found")
    # Strip system messages from the UI payload.
    visible = [m for m in messages if m.get("role") != "system"]
    return {"id": cid, "messages": visible}


@app.post("/api/conversations/{cid}/reset")
def reset_conversation(cid: str) -> dict[str, bool]:
    CONVERSATIONS.reset(cid)
    return {"ok": True}


# ── chat / SSE ──────────────────────────────────────────────────────────


@app.post("/api/chat")
async def chat(payload: ChatRequest, request: Request) -> StreamingResponse:
    if not settings.all_api_keys:
        raise HTTPException(
            status_code=400,
            detail="No Mistral API key configured. Add one in the Settings tab "
                   "or set MLA_MISTRAL_API_KEY in your .env.",
        )

    cid = request.headers.get("x-conversation-id") or str(uuid.uuid4())

    async def event_stream():
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


# ── uploads (drag-and-drop image attachments) ───────────────────────────


@app.post("/api/upload")
async def upload(file: UploadFile = File(...)) -> dict[str, object]:
    """Save a file and return a data: URL the chat can attach.

    For images we return a data URL inline so the Mistral vision model
    can read it directly. For other files we save them under the
    uploads directory and return an absolute path the agent can use.
    """
    data = await file.read()
    if not data:
        raise HTTPException(400, "empty upload")
    mime, _ = mimetypes.guess_type(file.filename or "")
    mime = mime or "application/octet-stream"
    if mime.startswith("image/") and len(data) <= 8_000_000:
        b64 = base64.b64encode(data).decode("ascii")
        return {
            "ok": True,
            "kind": "image",
            "name": file.filename,
            "mime": mime,
            "size": len(data),
            "data_url": f"data:{mime};base64,{b64}",
        }
    # large or non-image: save to disk
    settings.uploads_dir.mkdir(parents=True, exist_ok=True)
    safe_name = (file.filename or f"upload_{uuid.uuid4().hex}").replace("/", "_")
    out = settings.uploads_dir / safe_name
    out.write_bytes(data)
    return {
        "ok": True,
        "kind": "file",
        "name": file.filename,
        "mime": mime,
        "size": len(data),
        "path": str(out),
    }


# ── voice ───────────────────────────────────────────────────────────────


@app.post("/api/voice/transcribe")
async def voice_transcribe(file: UploadFile = File(...)) -> dict[str, object]:
    data = await file.read()
    if not data:
        raise HTTPException(400, "empty audio")
    result = voice.transcribe(data, filename=file.filename or "audio.webm")
    if not result.get("ok"):
        raise HTTPException(502, result.get("error", "transcription failed"))
    return result


@app.post("/api/voice/speak")
def voice_speak(payload: SpeakRequest) -> Response:
    audio, err = voice.synthesize(payload.text)
    if err or audio is None:
        raise HTTPException(502, err or "no audio returned")
    return Response(content=audio, media_type="audio/mpeg")


# ── recurring jobs ───────────────────────────────────────────────────────


@app.get("/api/jobs")
def list_jobs() -> dict[str, object]:
    return {"jobs": SCHEDULER.list()}


@app.delete("/api/jobs/{job_id}")
def delete_job(job_id: str) -> dict[str, bool]:
    ok = SCHEDULER.remove(job_id)
    if not ok:
        raise HTTPException(404, "job not found")
    return {"ok": True}


@app.post("/api/jobs/{job_id}/toggle")
def toggle_job(job_id: str, enabled: bool) -> dict[str, bool]:
    ok = SCHEDULER.toggle(job_id, enabled)
    if not ok:
        raise HTTPException(404, "job not found")
    return {"ok": True}


# ── memory (long-term key/value) ───────────────────────────────────────


@app.get("/api/memory")
def get_memory() -> dict[str, object]:
    from . import memory

    res = memory.recall("")
    return {"entries": res.get("all", {})}


@app.delete("/api/memory/{key}")
def delete_memory(key: str) -> dict[str, bool]:
    from . import memory

    res = memory.forget(key)
    if not res.get("ok"):
        raise HTTPException(404, res.get("error", "key not found"))
    return {"ok": True}


# ── helpers ─────────────────────────────────────────────────────────────


def _sse(event: str, data: object) -> str:
    payload = data if isinstance(data, str) else json.dumps(data, default=str)
    return f"event: {event}\ndata: {payload}\n\n"


# ── static UI ───────────────────────────────────────────────────────────


app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(str(WEB_DIR / "index.html"))
