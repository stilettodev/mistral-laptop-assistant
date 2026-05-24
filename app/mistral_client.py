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
    workload is mostly I/O bound.
    """
    key = api_key or settings.mistral_api_key
    if not key:
        raise RuntimeError(
            "No Mistral API key configured. Set MLA_MISTRAL_API_KEY in your .env file."
        )
    return Mistral(api_key=key)


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
    """Generate Mistral function-calling JSON schemas from TOOLS."""
    schemas: list[dict[str, Any]] = []
    for name, fn in TOOLS.items():
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
    unreachable or the key is missing.
    """
    if not settings.mistral_api_key:
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
