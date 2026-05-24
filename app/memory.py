"""Persistent long-term memory: a tiny key-value store on disk.

The assistant uses :mod:`tools.remember` / :mod:`tools.recall` /
:mod:`tools.forget` to keep facts across sessions ("my work folder is
~/projects/acme", "I prefer espresso at 14:00", etc).

We deliberately keep the format trivial – a single JSON file the user
can inspect or edit by hand at any time.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .config import settings


def _load() -> dict[str, dict[str, Any]]:
    path: Path = settings.memory_file
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save(data: dict[str, dict[str, Any]]) -> None:
    path: Path = settings.memory_file
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")


def remember(key: str, value: str) -> dict[str, Any]:
    data = _load()
    data[key] = {"value": value, "saved_at": time.time()}
    _save(data)
    return {"ok": True, "key": key, "count": len(data)}


def recall(key: str = "") -> dict[str, Any]:
    data = _load()
    if not key:
        return {"ok": True, "all": data, "count": len(data)}
    if key not in data:
        return {"ok": False, "error": f"no memory under key {key!r}"}
    return {"ok": True, "key": key, **data[key]}


def forget(key: str) -> dict[str, Any]:
    data = _load()
    if key not in data:
        return {"ok": False, "error": f"no memory under key {key!r}"}
    del data[key]
    _save(data)
    return {"ok": True, "key": key, "remaining": len(data)}


def context_block() -> str:
    """Pretty multi-line block injected into the system prompt."""
    data = _load()
    if not data:
        return ""
    lines = ["Long-term memory (use freely):"]
    for k, v in sorted(data.items()):
        lines.append(f"  - {k}: {v['value']}")
    return "\n".join(lines)
