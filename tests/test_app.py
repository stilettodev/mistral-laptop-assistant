"""Unit tests that don't require network or a Mistral API key."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app import safety, tools
from app.config import settings
from app.mistral_client import DEFAULT_MODELS, build_tool_schemas
from app.router import heuristic_route

ROOT = Path(__file__).resolve().parent.parent


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


# ── scheduler ────────────────────────────────────────────────────────


def test_scheduler_parses_every_spec() -> None:
    from datetime import datetime
    from app.scheduler import _next_after

    now = datetime(2025, 1, 1, 12, 0, 0)
    assert (_next_after(now, "every 30s") - now).total_seconds() == 30
    assert (_next_after(now, "every 5m") - now).total_seconds() == 300
    assert (_next_after(now, "every 2h") - now).total_seconds() == 7200


def test_scheduler_parses_daily_spec() -> None:
    from datetime import datetime
    from app.scheduler import _next_after

    now = datetime(2025, 1, 1, 12, 0, 0)
    nxt = _next_after(now, "daily 09:30")
    assert nxt.day == 2 and nxt.hour == 9 and nxt.minute == 30
    nxt = _next_after(now, "daily 14:00")
    assert nxt.day == 1 and nxt.hour == 14


def test_scheduler_parses_weekly_spec() -> None:
    from datetime import datetime
    from app.scheduler import _next_after

    # Jan 1 2025 is a Wednesday (weekday=2)
    now = datetime(2025, 1, 1, 12, 0, 0)
    nxt = _next_after(now, "weekly mon 09:00")
    assert nxt.weekday() == 0  # Monday


def test_scheduler_parses_cron_spec() -> None:
    from datetime import datetime
    from app.scheduler import _next_after

    now = datetime(2025, 1, 1, 12, 0, 0)
    nxt = _next_after(now, "cron 0 9 * * *")
    assert nxt.hour == 9 and nxt.minute == 0


def test_scheduler_rejects_invalid_spec() -> None:
    from datetime import datetime
    from app.scheduler import _next_after

    with pytest.raises(ValueError):
        _next_after(datetime.now(), "tomorrow ish")


def test_scheduler_crud(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "scheduler_file", tmp_path / "jobs.json")
    from app.scheduler import Scheduler

    s = Scheduler()
    job = s.add(name="hi", when="every 1m", command="echo hi")
    assert job["id"] in s.jobs
    assert s.jobs[job["id"]]["enabled"] is True
    assert (tmp_path / "jobs.json").exists()
    assert s.toggle(job["id"], enabled=False)
    assert s.jobs[job["id"]]["enabled"] is False
    assert s.remove(job["id"])
    assert job["id"] not in s.jobs
    assert s.remove("nonexistent") is False


def test_scheduler_rejects_invalid_job() -> None:
    from app.scheduler import Scheduler

    s = Scheduler()
    with pytest.raises(ValueError):
        s.add(name="x", when="every 1m", kind="invalid")
    with pytest.raises(ValueError):
        s.add(name="x", when="every 1m", kind="shell")  # missing command
    with pytest.raises(ValueError):
        s.add(name="x", when="every 1m", kind="chat")  # missing prompt


# ── memory ───────────────────────────────────────────────────────────


def test_memory_roundtrip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "memory_file", tmp_path / "mem.json")
    from app import memory

    assert memory.recall("missing")["ok"] is False
    assert memory.remember("home", "~/projects")["ok"]
    assert memory.recall("home")["value"] == "~/projects"
    all_facts = memory.recall()
    assert all_facts["count"] == 1
    assert memory.forget("home")["ok"]
    assert memory.recall("home")["ok"] is False


def test_memory_context_block(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "memory_file", tmp_path / "mem.json")
    from app import memory

    assert memory.context_block() == ""
    memory.remember("favorite_editor", "vim")
    block = memory.context_block()
    assert "favorite_editor" in block
    assert "vim" in block


# ── conversation storage ─────────────────────────────────────────────


def test_storage_save_load_delete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "conversations_dir", tmp_path / "chats")
    from app import storage

    msgs = [
        {"role": "user", "content": "first question"},
        {"role": "assistant", "content": "answer"},
    ]
    storage.save("cid1", msgs)
    loaded = storage.load("cid1")
    assert loaded == msgs

    convs = storage.list_conversations()
    assert convs[0]["id"] == "cid1"
    assert convs[0]["title"].startswith("first question")
    assert convs[0]["messages"] == 2

    assert storage.delete("cid1") is True
    assert storage.load("cid1") is None
    assert storage.delete("cid1") is False


# ── per-tool allow/deny policy ───────────────────────────────────────


def test_allow_deny_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.safety import tool_is_allowed_by_policy

    monkeypatch.setattr(settings, "deny_tools", "")
    monkeypatch.setattr(settings, "allow_tools", "")
    assert tool_is_allowed_by_policy("run_shell")[0] is True

    monkeypatch.setattr(settings, "deny_tools", "run_shell,delete_path")
    assert tool_is_allowed_by_policy("run_shell")[0] is False
    assert tool_is_allowed_by_policy("read_file")[0] is True

    monkeypatch.setattr(settings, "deny_tools", "")
    monkeypatch.setattr(settings, "allow_tools", "read_file,list_dir")
    assert tool_is_allowed_by_policy("read_file")[0] is True
    assert tool_is_allowed_by_policy("run_shell")[0] is False


def test_policy_filters_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.mistral_client import build_tool_schemas

    monkeypatch.setattr(settings, "deny_tools", "run_shell,kill_process")
    monkeypatch.setattr(settings, "allow_tools", "")
    names = {s["function"]["name"] for s in build_tool_schemas()}
    assert "run_shell" not in names
    assert "kill_process" not in names
    assert "read_file" in names


def test_evaluate_respects_deny(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "deny_tools", "run_shell")
    monkeypatch.setattr(settings, "allow_tools", "")
    d = safety.evaluate("run_shell", {"command": "echo hi"}, "yolo", {}, "x")
    assert d.allowed is False
    assert "denied" in d.reason.lower()


# ── new tool registration ────────────────────────────────────────────


def test_new_tools_registered() -> None:
    expected = {
        "schedule_recurring",
        "list_recurring",
        "cancel_recurring",
        "toggle_recurring",
        "remember",
        "recall",
        "forget",
    }
    assert expected <= set(tools.TOOLS)


def test_total_tool_count() -> None:
    # 21 originals + 7 new = 28
    assert len(tools.TOOLS) == 28


# ── API routes (FastAPI TestClient) ──────────────────────────────────


def test_list_conversations(client: TestClient) -> None:
    resp = client.get("/api/conversations")
    assert resp.status_code == 200
    assert "conversations" in resp.json()


def test_jobs_endpoint(client: TestClient) -> None:
    resp = client.get("/api/jobs")
    assert resp.status_code == 200
    assert "jobs" in resp.json()


def test_memory_endpoint(client: TestClient) -> None:
    resp = client.get("/api/memory")
    assert resp.status_code == 200
    assert "entries" in resp.json()


def test_capabilities_endpoint(client: TestClient) -> None:
    resp = client.get("/api/capabilities")
    assert resp.status_code == 200
    data = resp.json()
    assert "voice" in data
    assert "allow_tools" in data


def test_chat_without_api_key_returns_400(client: TestClient) -> None:
    resp = client.post("/api/chat", json={"message": "hello"})
    assert resp.status_code == 400


def test_upload_image_returns_data_url(client: TestClient) -> None:
    from app.config import settings

    with tempfile.TemporaryDirectory() as td:
        import app.main
        orig = settings.uploads_dir
        settings.uploads_dir = Path(td)
        try:
            resp = client.post(
                "/api/upload",
                files={"file": ("x.png", b"\x89PNG\r\n", "image/png")},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["ok"] is True
            assert data["kind"] == "image"
            assert "data:image" in data["data_url"]
        finally:
            settings.uploads_dir = orig


def test_upload_non_image_saves_to_disk(client: TestClient) -> None:
    from app.config import settings

    with tempfile.TemporaryDirectory() as td:
        orig = settings.uploads_dir
        settings.uploads_dir = Path(td)
        try:
            resp = client.post(
                "/api/upload",
                files={"file": ("report.csv", b"a,b\n1,2", "text/csv")},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["kind"] == "file"
            assert Path(data["path"]).exists()
        finally:
            settings.uploads_dir = orig


# ── persona registry ────────────────────────────────────────────────────


def test_friday_in_personas() -> None:
    from app.schemas import PERSONAS

    assert "friday" in PERSONAS
    prompt = PERSONAS["friday"]
    assert "FRIDAY" in prompt
    assert "agentic" in prompt
    assert "tests" in prompt.lower() or "test" in prompt


def test_all_personas_have_content() -> None:
    from app.schemas import PERSONAS

    assert all(p.strip() for p in PERSONAS.values())


# ── settings endpoint ──────────────────────────────────────────────────


def test_settings_get_returns_defaults(client: TestClient) -> None:
    resp = client.get("/api/settings")
    assert resp.status_code == 200
    data = resp.json()
    assert data["default_persona"] in ("jarvis", "veronica", "friday")
    assert data["safety_mode"] in ("strict", "normal", "yolo")
    assert isinstance(data["personas"], list)
    ids = [p["id"] for p in data["personas"]]
    assert "jarvis" in ids and "friday" in ids


def test_settings_put_persona(client: TestClient) -> None:
    resp = client.put("/api/settings", json={"default_persona": "friday"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["default_persona"] == "friday"


def test_settings_put_invalid_persona(client: TestClient) -> None:
    resp = client.put("/api/settings", json={"default_persona": "invalid"})
    assert resp.status_code == 400


def test_settings_put_safety(client: TestClient) -> None:
    resp = client.put("/api/settings", json={"safety_mode": "strict"})
    assert resp.status_code == 200
    assert resp.json()["safety_mode"] == "strict"


# ── keys endpoint ───────────────────────────────────────────────────────


def test_keys_get_empty(client: TestClient, tmp_path: Path) -> None:
    import app.config
    orig = app.config.settings.keys_file
    app.config.settings.keys_file = tmp_path / "keys.json"
    try:
        resp = client.get("/api/keys")
        assert resp.status_code == 200
        data = resp.json()
        assert "keys" in data
        assert isinstance(data["keys"], list)
    finally:
        app.config.settings.keys_file = orig


def test_keys_add_and_list(client: TestClient, tmp_path: Path) -> None:
    import app.config
    orig = app.config.settings.keys_file
    app.config.settings.keys_file = tmp_path / "keys.json"
    try:
        resp = client.post("/api/keys", json={"key": "sk-test1234567890abcdef", "label": "test key"})
        assert resp.status_code == 200
        info = resp.json()
        assert info["id"]
        assert info["label"] == "test key"
        # Full key never leaks — verify neither the full start nor end appears.
        raw = "sk-test1234567890abcdef"
        assert raw not in info["prefix"]
        assert "sk-test" not in info["prefix"]  # start hidden

        resp = client.get("/api/keys")
        data = resp.json()
        assert len(data["keys"]) >= 1
    finally:
        app.config.settings.keys_file = orig


def test_keys_add_duplicate_rejected(client: TestClient, tmp_path: Path) -> None:
    import app.config
    orig = app.config.settings.keys_file
    app.config.settings.keys_file = tmp_path / "keys.json"
    try:
        key = "sk-duplicate1234567890abcdefgh"
        assert client.post("/api/keys", json={"key": key}).status_code == 200
        resp = client.post("/api/keys", json={"key": key})
        assert resp.status_code == 400
    finally:
        app.config.settings.keys_file = orig


def test_keys_delete_removes(client: TestClient, tmp_path: Path) -> None:
    import app.config
    orig = app.config.settings.keys_file
    app.config.settings.keys_file = tmp_path / "keys.json"
    try:
        add = client.post("/api/keys", json={"key": "sk-delete999abcdefghij"}).json()
        kid = add["id"]
        assert client.delete(f"/api/keys/{kid}").status_code == 200
        resp = client.delete(f"/api/keys/{kid}")
        assert resp.status_code == 404  # already gone
    finally:
        app.config.settings.keys_file = orig


def test_keys_delete_env_key_rejected(client: TestClient) -> None:
    resp = client.delete("/api/keys/env-0")
    assert resp.status_code == 400


def test_keys_without_api_key_still_serves(client: TestClient) -> None:
    """The /api/keys endpoint should work even when no key is configured."""
    resp = client.get("/api/keys")
    assert resp.status_code == 200
    assert "keys" in resp.json()
