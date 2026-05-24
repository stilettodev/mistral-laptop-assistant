"""Mistral API helpers: client construction, model discovery, schemas."""

from __future__ import annotations

import inspect
from functools import lru_cache
from typing import Any

try:
    from mistralai import Mistral  # mistralai 1.x
except ImportError:  # pragma: no cover - mistralai 2.x moved it
    from mistralai.client import Mistral  # type: ignore[no-redef]

from .config import settings
from .tools import TOOLS, short_description


def get_client(api_key: str | None = None) -> Mistral:
    """Return a Mistral SDK client.

    A per-process client is fine because the SDK is thread-safe and our
    workload is mostly I/O bound. When ``api_key`` is omitted we fall
    back to the highest-priority key from :pyattr:`Settings.all_api_keys`
    (UI keystore first, then env).
    """
    key = api_key
    if not key:
        keys = settings.all_api_keys
        key = keys[0] if keys else ""
    if not key:
        raise RuntimeError(
            "No Mistral API key configured. Add one via the Settings tab or "
            "set MLA_MISTRAL_API_KEY in your .env file."
        )
    return Mistral(api_key=key)


class MultiKeyClient:
    """Wrapper that cycles through multiple API keys on 401/429 errors."""

    def __init__(self, keys: list[str] | None = None):
        self._keys = keys or settings.all_api_keys
        self._index = 0

    @property
    def current_key(self) -> str:
        idx = self._index % len(self._keys)
        return self._keys[idx]

    def client(self) -> Mistral:
        return get_client(self.current_key)

    def rotate(self) -> str | None:
        """Advance to the next key. Returns the new key or None if no more."""
        if len(self._keys) <= 1:
            return None
        self._index += 1
        return self.current_key

    @property
    def has_fallback(self) -> bool:
        return len(self._keys) > 1

    def exhausted(self) -> bool:
        return self._index >= len(self._keys)


# ---------------------------------------------------------------------------
# Tool schema generation
# ---------------------------------------------------------------------------


_PY_TO_JSON = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    list: "array",
    dict: "object",
}


def _python_to_jsonschema(annotation: Any) -> dict[str, Any]:
    if annotation is inspect.Parameter.empty:
        return {"type": "string"}
    return {"type": _PY_TO_JSON.get(annotation, "string")}


def build_tool_schemas() -> list[dict[str, Any]]:
    """Generate Mistral function-calling JSON schemas from TOOLS.

    Tools blocked by the ``MLA_ALLOW_TOOLS`` / ``MLA_DENY_TOOLS`` policy
    are filtered out, so the model never sees them.
    """
    from .safety import tool_is_allowed_by_policy

    schemas: list[dict[str, Any]] = []
    for name, fn in TOOLS.items():
        allowed, _ = tool_is_allowed_by_policy(name)
        if not allowed:
            continue
        sig = inspect.signature(fn)
        properties: dict[str, Any] = {}
        required: list[str] = []
        for param_name, param in sig.parameters.items():
            properties[param_name] = _python_to_jsonschema(param.annotation)
            if param.default is inspect.Parameter.empty:
                required.append(param_name)
        schemas.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": short_description(fn),
                    "parameters": {
                        "type": "object",
                        "properties": properties,
                        "required": required,
                    },
                },
            }
        )
    return schemas


# ---------------------------------------------------------------------------
# Model catalogue
# ---------------------------------------------------------------------------


# A curated default list. Used when the live /v1/models call fails or
# the API key isn't set yet. Order = recommended in the UI dropdown.
DEFAULT_MODELS: list[dict[str, Any]] = [
    {
        "id": "auto",
        "description": "Auto-select the best Mistral model for each request.",
        "capabilities": ["routing"],
    },
    {
        "id": "mistral-large-latest",
        "description": "Flagship reasoning model. Best for hard, multi-step tasks.",
        "capabilities": ["reasoning", "tool-use", "long-context"],
    },
    {
        "id": "mistral-medium-latest",
        "description": "Balanced quality/cost for general use.",
        "capabilities": ["reasoning", "tool-use"],
    },
    {
        "id": "mistral-small-latest",
        "description": "Fast, cheap workhorse for everyday tasks.",
        "capabilities": ["chat", "tool-use"],
    },
    {
        "id": "magistral-medium-latest",
        "description": "Chain-of-thought reasoning model.",
        "capabilities": ["reasoning"],
    },
    {
        "id": "magistral-small-latest",
        "description": "Lightweight reasoning model.",
        "capabilities": ["reasoning"],
    },
    {
        "id": "codestral-latest",
        "description": "Specialized for code generation, editing, and shell commands.",
        "capabilities": ["code", "tool-use"],
    },
    {
        "id": "ministral-8b-latest",
        "description": "Edge model, very fast for simple instructions.",
        "capabilities": ["chat"],
    },
    {
        "id": "ministral-3b-latest",
        "description": "Tiniest, cheapest model. Used for routing.",
        "capabilities": ["chat", "routing"],
    },
    {
        "id": "pixtral-large-latest",
        "description": "Multimodal model that can analyse images.",
        "capabilities": ["vision", "reasoning"],
    },
]


@lru_cache(maxsize=1)
def list_models() -> list[dict[str, Any]]:
    """Return available Mistral models.

    Falls back to the curated default catalogue when the API is
    unreachable or no key is available.
    """
    if not settings.all_api_keys:
        return DEFAULT_MODELS
    try:
        client = get_client()
        listing = client.models.list()
        live_ids: list[str] = []
        for item in (getattr(listing, "data", None) or []):
            mid = getattr(item, "id", None)
            if mid:
                live_ids.append(mid)
        if not live_ids:
            return DEFAULT_MODELS
        # Keep the curated descriptions when we recognise the id, append
        # unknown ones at the end.
        known = {m["id"]: m for m in DEFAULT_MODELS}
        merged: list[dict[str, Any]] = [DEFAULT_MODELS[0]]  # keep "auto"
        for mid in sorted(set(live_ids)):
            if mid in known:
                merged.append(known[mid])
            else:
                merged.append({"id": mid, "description": "", "capabilities": []})
        return merged
    except Exception:
        return DEFAULT_MODELS
