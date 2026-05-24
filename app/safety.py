"""Safety policy: confirmations + audit logging.

The assistant operates with three safety modes:

* ``strict``  – every tool call that is not on the read-only allow-list
  requires an explicit user confirmation in the UI.
* ``normal``  – obviously destructive operations (delete, sudo, kill,
  writing outside the workspace, network exfiltration heuristics)
  require confirmation. Most everyday tasks proceed automatically.
* ``yolo``    – everything is auto-approved. Use at your own risk.

All tool calls – whether approved or denied – are appended to a JSONL
audit log so you can always inspect what the agent did.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from .config import settings


# Tools that only read the system (no state change).
READONLY_TOOLS = {
    "list_dir",
    "read_file",
    "system_info",
    "list_processes",
    "screenshot",
    "web_search",
    "clipboard_get",
    "which",
    "get_env",
    "get_datetime",
}

# Patterns that always require user approval (case-insensitive).
DANGEROUS_PATTERNS = [
    re.compile(r"\brm\s+-[a-z]*r", re.IGNORECASE),
    re.compile(r"\bsudo\b", re.IGNORECASE),
    re.compile(r"\bmkfs\b", re.IGNORECASE),
    re.compile(r"\bdd\s+if=", re.IGNORECASE),
    re.compile(r":(){\s*:\|", re.IGNORECASE),  # fork bomb
    re.compile(r"\bshutdown\b|\breboot\b|\bhalt\b", re.IGNORECASE),
    re.compile(r"\bchmod\s+[0-7]*777", re.IGNORECASE),
    re.compile(r">\s*/dev/sd", re.IGNORECASE),
    re.compile(r"\bcurl\b[^|]*\|\s*(bash|sh|zsh)", re.IGNORECASE),
    re.compile(r"\bwget\b[^|]*\|\s*(bash|sh|zsh)", re.IGNORECASE),
]


SafetyMode = Literal["strict", "normal", "yolo"]


@dataclass
class SafetyDecision:
    allowed: bool
    needs_confirmation: bool
    reason: str = ""


def looks_dangerous(text: str) -> str | None:
    """Return matching dangerous pattern description, or None."""
    for pattern in DANGEROUS_PATTERNS:
        if pattern.search(text):
            return pattern.pattern
    return None


def evaluate(
    tool_name: str,
    arguments: dict[str, Any],
    mode: SafetyMode,
    confirmations: dict[str, bool],
    call_id: str,
) -> SafetyDecision:
    """Decide whether a tool call may execute.

    Returns ``needs_confirmation=True`` when the UI must prompt the
    user. The agent loop pauses until a decision is returned.
    """
    if mode == "yolo":
        return SafetyDecision(allowed=True, needs_confirmation=False)

    if tool_name in READONLY_TOOLS and mode != "strict":
        return SafetyDecision(allowed=True, needs_confirmation=False)

    # Look at args for dangerous shell patterns.
    risky_reason = ""
    for value in arguments.values():
        if isinstance(value, str):
            match = looks_dangerous(value)
            if match:
                risky_reason = f"matches dangerous pattern: {match}"
                break

    # Writes / deletes always need confirmation in normal+strict modes.
    write_like = tool_name in {
        "write_file",
        "append_file",
        "delete_path",
        "move_file",
        "run_shell",
        "kill_process",
        "open_app",
        "open_url",
        "clipboard_set",
        "schedule_task",
        "notify",
    }

    must_confirm = mode == "strict" or write_like or bool(risky_reason)
    if must_confirm:
        decision = confirmations.get(call_id)
        if decision is True:
            return SafetyDecision(allowed=True, needs_confirmation=False)
        if decision is False:
            return SafetyDecision(
                allowed=False,
                needs_confirmation=False,
                reason="User denied execution.",
            )
        return SafetyDecision(
            allowed=False,
            needs_confirmation=True,
            reason=risky_reason or "Requires user confirmation.",
        )

    return SafetyDecision(allowed=True, needs_confirmation=False)


def audit(event: str, data: dict[str, Any]) -> None:
    """Append a structured event to the audit log."""
    path: Path = settings.audit_log
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        entry = {"ts": time.time(), "event": event, **data}
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, default=str) + "\n")
    except OSError:
        # Never let the audit log break the assistant.
        pass
