"""Disk-backed conversation store.

Each conversation is one JSON file under
``MLA_CONVERSATIONS_DIR`` (default ``~/.mistral_assistant_chats``)::

    {
      "id": "uuid",
      "title": "first user message excerpt",
      "created_at": 1716394800.0,
      "updated_at": 1716394820.0,
      "messages": [{"role":"user","content":"…"}, …]
    }
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .config import settings


def _dir() -> Path:
    settings.conversations_dir.mkdir(parents=True, exist_ok=True)
    return settings.conversations_dir


def _path(cid: str) -> Path:
    return _dir() / f"{cid}.json"


def save(cid: str, messages: list[dict[str, Any]]) -> None:
    """Persist a conversation. Title = first user message excerpt."""
    if not messages:
        return
    title = ""
    for m in messages:
        if m.get("role") == "user" and m.get("content"):
            title = str(m["content"])[:80]
            break
    title = title or "Untitled"
    path = _path(cid)
    created = (
        json.loads(path.read_text(encoding="utf-8")).get("created_at", time.time())
        if path.exists()
        else time.time()
    )
    payload = {
        "id": cid,
        "title": title,
        "created_at": created,
        "updated_at": time.time(),
        "messages": messages,
    }
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


def load(cid: str) -> list[dict[str, Any]] | None:
    path = _path(cid)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("messages", [])
    except (OSError, json.JSONDecodeError):
        return None


def list_conversations() -> list[dict[str, Any]]:
    out = []
    for path in _dir().glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            out.append(
                {
                    "id": data["id"],
                    "title": data.get("title", "Untitled"),
                    "created_at": data.get("created_at", 0),
                    "updated_at": data.get("updated_at", 0),
                    "messages": len(data.get("messages", [])),
                }
            )
        except (OSError, json.JSONDecodeError, KeyError):
            continue
    out.sort(key=lambda x: x["updated_at"], reverse=True)
    return out


def delete(cid: str) -> bool:
    path = _path(cid)
    if path.exists():
        path.unlink()
        return True
    return False
