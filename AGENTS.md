# AGENTS.md — Mistral Laptop Assistant

Persistent context for future OpenHands sessions on this repo.

## Purpose
Local web app that uses the Mistral AI API as the brain of a personal
assistant that can run arbitrary actions on the user's laptop (shell,
files, processes, apps, browser, clipboard, screenshots, web search,
scheduling, notifications).

## Stack
- Python 3.11+ managed with **uv** (`uv sync`, `uv run …`)
- FastAPI + uvicorn (single-process; streams Server-Sent Events)
- `mistralai` ≥ 2.x — import path is `from mistralai.client import Mistral`
  (we keep a 1.x fallback for older installs)
- `psutil`, `mss`, `pyperclip`, `ddgs` for system access
- Vanilla HTML/JS/CSS frontend in `web/` (no build step)

## Layout
```
app/
  config.py         pydantic-settings, prefix MLA_
  schemas.py        ChatRequest, StatusResponse, ToolCall
  tools.py          all agent tools — add new ones here
  mistral_client.py SDK wrapper + tool-schema generator + model catalogue
  router.py         heuristic + LLM auto model picker
  safety.py         confirmation policy + JSONL audit log
  agent.py          async generator that drives chat.complete with tools
  main.py           FastAPI app (/api/status /api/models /api/chat SSE)
  cli.py            `mla` entry point  (subcommands: serve, audit)
  __main__.py       allows `python -m app`
web/
  index.html  styles.css  app.js
tests/
  test_app.py       22 unit tests, all offline
```

## Conventions
- Tool functions return `{"ok": bool, ...}` dicts. Never raise — wrap in
  `try/except` and surface the message via `error` key.
- Tool argument types must be plain `str | int | bool` so the JSON-schema
  generator in `mistral_client.build_tool_schemas` produces clean output.
- When adding a tool: add it to the `TOOLS` registry at the bottom of
  `app/tools.py` and decide whether it belongs in
  `safety.READONLY_TOOLS` or the `write_like` set.
- Settings come from env vars prefixed `MLA_` (or `.env`).
- Agent state is a singleton `CONVERSATIONS` dict keyed by
  `x-conversation-id` header.

## Running locally
```bash
cp .env.example .env       # then add MLA_MISTRAL_API_KEY
uv run mla --open          # or: uv run python -m app
```

## Testing
`uv run pytest -v`  — runs 22 unit tests fully offline.

## Notes / gotchas
- `mistralai` 2.x changed the import path. The fallback in
  `app/mistral_client.py` keeps both versions working.
- SSE responses include `X-Accel-Buffering: no` to avoid nginx buffering.
- The web UI is a single file each (`index.html` / `app.js` /
  `styles.css`) — no bundler. Fonts: Instrument Serif, IBM Plex Sans,
  JetBrains Mono.
- Default bind is `127.0.0.1`. Don't bind on 0.0.0.0 unless asked.
- The auto router's LLM fallback uses `MLA_ROUTER_MODEL`
  (default `ministral-3b-latest`) for cost.
