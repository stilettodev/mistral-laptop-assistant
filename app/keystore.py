"""Disk-backed store for Mistral API keys managed via the UI.

The file lives at ``settings.keys_file`` (default
``~/.mistral_assistant_keys.json``) and has the shape::

    {
      "keys": [
        {"id": "a1b2c3d4", "label": "primary",
         "key": "FULL_KEY", "added_at": 1716394800.0},
        ...
      ]
    }

Public API exposes the keys as :class:`KeyInfo` records (masked
prefix only — never the full key). The agent loop pulls the raw
values through :func:`raw_keys` for fallback rotation.
"""

from __future__ import annotations

import json
import os
import secrets
import threading
import time
from pathlib import Path

from .config import settings
from .schemas import KeyInfo


_LOCK = threading.Lock()


def _path() -> Path:
    return settings.keys_file


def _read() -> dict:
    p = _path()
    if not p.exists():
        return {"keys": []}
    try:
        return json.loads(p.read_text("utf-8")) or {"keys": []}
    except (json.JSONDecodeError, OSError):
        return {"keys": []}


def _write(data: dict) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(p)
    # Keys file holds secrets — restrict perms on POSIX.
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass


def _mask(key: str) -> str:
    if len(key) <= 8:
        return "•" * len(key)
    return key[:4] + "…" + key[-4:]


def _to_info(entry: dict, primary: bool) -> KeyInfo:
    return KeyInfo(
        id=entry["id"],
        label=entry.get("label") or "",
        prefix=_mask(entry["key"]),
        primary=primary,
    )


def list_keys() -> list[KeyInfo]:
    """Return every stored key as a :class:`KeyInfo` (full keys hidden)."""
    with _LOCK:
        data = _read()
    return [_to_info(e, primary=(i == 0)) for i, e in enumerate(data.get("keys", []))]


def raw_keys() -> list[str]:
    """Return the raw key strings in priority order (primary first).

    Called by ``settings.all_api_keys`` and the agent's fallback loop.
    """
    with _LOCK:
        data = _read()
    return [e["key"] for e in data.get("keys", []) if e.get("key")]


def add_key(key: str, label: str = "") -> KeyInfo:
    """Append ``key`` to the store. Duplicates are rejected."""
    key = key.strip()
    if not key:
        raise ValueError("empty key")
    with _LOCK:
        data = _read()
        for existing in data.get("keys", []):
            if existing.get("key") == key:
                raise ValueError("key already stored")
        entry = {
            "id": secrets.token_hex(4),
            "label": (label or "").strip(),
            "key": key,
            "added_at": time.time(),
        }
        data.setdefault("keys", []).append(entry)
        _write(data)
        primary = len(data["keys"]) == 1
    _invalidate_caches()
    return _to_info(entry, primary=primary)


def remove_key(key_id: str) -> bool:
    """Drop the key with ``id`` == ``key_id``. Returns True if removed."""
    with _LOCK:
        data = _read()
        before = len(data.get("keys", []))
        data["keys"] = [e for e in data.get("keys", []) if e.get("id") != key_id]
        if len(data["keys"]) == before:
            return False
        _write(data)
    _invalidate_caches()
    return True


def _invalidate_caches() -> None:
    """Drop downstream caches that depend on the key list."""
    # `list_models` is @lru_cache'd against settings.mistral_api_key — when
    # we change the effective key set we should refresh it on next call.
    try:
        from .mistral_client import list_models

        list_models.cache_clear()
    except Exception:  # noqa: BLE001
        pass
