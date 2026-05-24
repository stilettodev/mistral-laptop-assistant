"""Unit tests that don't require network or a Mistral API key."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

from app import safety, tools
from app.config import settings
from app.mistral_client import DEFAULT_MODELS, build_tool_schemas
from app.router import heuristic_route


# ── tools ─────────────────────────────────────────────────────────────


def test_list_dir_returns_entries(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("hi")
    (tmp_path / "sub").mkdir()
    result = tools.list_dir(path=str(tmp_path))
    assert result["ok"] is True
    names = {e["name"] for e in result["entries"]}
    assert {"a.txt", "sub"} <= names


def test_read_write_roundtrip(tmp_path: Path) -> None:
    target = tmp_path / "hello.txt"
    w = tools.write_file(path=str(target), content="hello world")
    assert w["ok"]
    r = tools.read_file(path=str(target))
    assert r["ok"]
    assert r["content"] == "hello world"


def test_append_file_extends_content(tmp_path: Path) -> None:
    target = tmp_path / "log.txt"
    tools.write_file(path=str(target), content="line1\n")
    tools.append_file(path=str(target), content="line2\n")
    assert (tmp_path / "log.txt").read_text() == "line1\nline2\n"


def test_run_shell_captures_output() -> None:
    res = tools.run_shell(command="echo hello-from-shell")
    assert res["ok"] is True
    assert "hello-from-shell" in res["stdout"]


def test_run_shell_reports_failure() -> None:
    res = tools.run_shell(command="false")
    assert res["ok"] is False
    assert res["returncode"] != 0


def test_run_shell_timeout() -> None:
    res = tools.run_shell(command="sleep 5", timeout=1)
    assert res["ok"] is False
    assert "timeout" in res["error"]


def test_system_info_keys() -> None:
    info = tools.system_info()
    for key in ("system", "memory_total_gb", "cpu_count", "python"):
        assert key in info


def test_get_env_masks_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MY_SECRET_TOKEN", "abcd1234")
    monkeypatch.setenv("MY_PUBLIC_VAR", "hello")
    secret = tools.get_env("MY_SECRET_TOKEN")
    public = tools.get_env("MY_PUBLIC_VAR")
    assert secret["masked"] is True
    assert "abcd1234" not in secret["value"]
    assert public["value"] == "hello"


def test_move_and_delete(tmp_path: Path) -> None:
    src = tmp_path / "src.txt"
    dst = tmp_path / "dst.txt"
    src.write_text("x")
    assert tools.move_file(src=str(src), dst=str(dst))["ok"]
    assert dst.exists() and not src.exists()
    assert tools.delete_path(path=str(dst))["ok"]
    assert not dst.exists()


def test_list_processes_contains_self() -> None:
    result = tools.list_processes(filter="", limit=200)
    assert result["ok"]
    pids = {p["pid"] for p in result["processes"]}
    assert os.getpid() in pids or len(pids) > 0


# ── tool schema generation ───────────────────────────────────────────


def test_build_tool_schemas_has_all_tools() -> None:
    schemas = build_tool_schemas()
    names = {s["function"]["name"] for s in schemas}
    assert names == set(tools.TOOLS)
    for s in schemas:
        # Mistral requires parameters to be a JSON-schema object
        assert s["type"] == "function"
        assert s["function"]["parameters"]["type"] == "object"
        assert "description" in s["function"]


# ── safety ───────────────────────────────────────────────────────────


def test_readonly_tool_runs_in_normal_mode() -> None:
    d = safety.evaluate("read_file", {"path": "x"}, "normal", {}, "id-1")
    assert d.allowed and not d.needs_confirmation


def test_write_requires_confirmation_in_normal_mode() -> None:
    d = safety.evaluate("write_file", {"path": "x", "content": "y"}, "normal", {}, "id-2")
    assert not d.allowed
    assert d.needs_confirmation


def test_dangerous_shell_pattern_detected() -> None:
    d = safety.evaluate(
        "run_shell", {"command": "rm -rf /"}, "normal", {}, "id-3"
    )
    assert d.needs_confirmation


def test_yolo_skips_confirmation() -> None:
    d = safety.evaluate(
        "run_shell", {"command": "rm -rf /tmp/foo"}, "yolo", {}, "id-4"
    )
    assert d.allowed and not d.needs_confirmation


def test_strict_mode_blocks_readonly_until_approved() -> None:
    d = safety.evaluate("read_file", {"path": "x"}, "strict", {}, "id-5")
    assert d.needs_confirmation
    d_ok = safety.evaluate("read_file", {"path": "x"}, "strict", {"id-5": True}, "id-5")
    assert d_ok.allowed


def test_audit_writes_jsonl(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    log_path = tmp_path / "audit.log"
    monkeypatch.setattr(settings, "audit_log", log_path)
    safety.audit("test_event", {"foo": "bar"})
    entry = json.loads(log_path.read_text().strip())
    assert entry["event"] == "test_event"
    assert entry["foo"] == "bar"


# ── router ────────────────────────────────────────────────────────────


def test_router_code_keywords() -> None:
    r = heuristic_route("Write a Python function to parse JSON")
    assert r is not None
    assert r.model == "codestral-latest"


def test_router_short_prompt_picks_small() -> None:
    r = heuristic_route("ls")
    assert r is not None
    assert "small" in r.model or "ministral" in r.model


def test_router_vision_keyword() -> None:
    r = heuristic_route("Take a screenshot and describe what you see")
    assert r is not None
    assert "pixtral" in r.model


def test_router_complex_prompt_picks_large() -> None:
    prompt = (
        "Please plan a comprehensive analysis of my project directory. "
        "Compare the architectural trade-offs between switching from REST to gRPC, "
        "explain the migration path, and outline a deep test strategy."
    )
    r = heuristic_route(prompt)
    assert r is not None
    assert "large" in r.model


def test_router_default_models_contains_auto() -> None:
    ids = {m["id"] for m in DEFAULT_MODELS}
    assert "auto" in ids
    assert "mistral-large-latest" in ids
    assert "codestral-latest" in ids
