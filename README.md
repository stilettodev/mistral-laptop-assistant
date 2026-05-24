# 🤖 Mistral Laptop Assistant

> A local personal assistant that can **automate anything on your laptop** using the [Mistral AI](https://mistral.ai) API.
> Pick a model yourself or let the agent choose the best one for each task.

<p align="center">
  <img alt="dark editorial chat UI" src="https://img.shields.io/badge/runs-locally-ff7a3c?style=flat-square" />
  <img alt="mistral" src="https://img.shields.io/badge/powered%20by-Mistral%20AI-ff7a3c?style=flat-square" />
  <img alt="python" src="https://img.shields.io/badge/python-3.11%2B-3b82f6?style=flat-square" />
  <img alt="license" src="https://img.shields.io/badge/license-MIT-22c55e?style=flat-square" />
</p>

---

## What it does

A single-process web app you run on **your own machine**. It opens in your
browser and gives you a chat UI backed by Mistral. The assistant can:

| Capability | Tool(s) |
|---|---|
| Read / write / list / move / delete files | `read_file`, `write_file`, `append_file`, `list_dir`, `move_file`, `delete_path` |
| Run any shell command | `run_shell`, `which` |
| Inspect & manage processes | `list_processes`, `kill_process` |
| Take screenshots | `screenshot` (returns the PNG + preview in chat) |
| Open apps & URLs | `open_app`, `open_url` |
| Read / write the clipboard | `clipboard_get`, `clipboard_set` |
| Show desktop notifications | `notify` |
| Search the web (no API key) | `web_search` |
| System / env / time | `system_info`, `get_env`, `get_datetime` |
| Schedule jobs (`at` / `schtasks`) | `schedule_task` |

Everything runs **on your hardware** with your API key. No telemetry, no
cloud relay – just direct calls from your computer to `api.mistral.ai`.

## Model selection — manual or automatic

Use the **Model** dropdown in the sidebar to:

* **Pin a specific model** — e.g. `codestral-latest`, `mistral-large-latest`,
  `pixtral-large-latest`, etc. The dropdown is populated live from
  `/v1/models` when an API key is set, with a curated fallback otherwise.

* **🤖 auto** *(default)* — a two-stage router decides per request:
  1. **Heuristic** pass on keywords (code → `codestral`, image → `pixtral`,
     long/complex → `mistral-large`, trivial → `mistral-small`, …).
  2. If unsure, a tiny `ministral-3b-latest` classifier picks one of
     `CODE / REASONING / VISION / HEAVY / QUICK / GENERAL`.

  The chosen model and reason are shown above the answer.

## Safety modes

| Mode | Behaviour |
|---|---|
| **strict** | Every non-read tool asks for approval. |
| **normal** *(default)* | Risky / destructive actions ask for approval. |
| **yolo** | Auto-approve everything. Use only when you trust the prompt. |

Dangerous shell patterns (`rm -rf`, `sudo`, `mkfs`, `dd`, `curl … \| sh`,
fork bombs, etc.) are **always** flagged for confirmation in `strict` and
`normal` modes – regardless of how the agent obfuscates them.

Every tool call (allowed, denied, or pending) is appended to
`~/.mistral_assistant_audit.log` as JSONL. Tail it any time:

```bash
mla audit --tail
```

---

## Install & run — one command

```bash
# Install: sets up uv, clones repo, runs uv sync, prompts for API key
curl -LsSf https://yourrepo/install.sh | bash

# Start: opens browser automatically
cd ~/mistral-laptop-assistant && uv run mla --open
```

### Windows

```powershell
# One-line install + launch (from an existing clone)
irm https://raw.githubusercontent.com/stilettodev/mistral-laptop-assistant/main/install.ps1 | iex

# Or manually
uv sync
uv run mla -k YOUR_KEY --open
```

Or from an existing clone:

```bash
uv sync
uv run mla --open -k YOUR_KEY_HERE          # one-line start
uv run mla -k sk-... --open                  # -k / --key is the fastest
```

On first run without a key, it prompts interactively and saves to
`~/.mistral_assistant.env` so you never have to type it again.

### Quick reference

| Command | What it does |
|---|---|
| `uv run mla --open` | Start server + open browser |
| `uv run mla -k KEY --open` | One-shot: set key + start + open |
| `uv run mla --window` | Native OS window (needs `--extra desktop`) |
| `uv run mla --tray` | System tray icon (needs `--extra desktop`) |
| `uv run mla audit --tail` | Live audit log |
| `uv run mla jobs` | Show recurring scheduled jobs |
| `uv run mla memory` | Show long-term memory entries |
| `uv run pytest tests/ -v` | Run 44 offline tests |

## Talk to it — examples

> *Show my 5 biggest processes by memory.*

> *Take a screenshot, save it to ~/Desktop/screen.png and tell me what you see.*

> *Find every `.log` file in ~/Downloads larger than 10 MB and ask before deleting them.*

> *Search the web for "release notes mistral large 2" and summarise the top 3 results.*

> *Write a Python script that lists every TODO in this project and save it as `find_todos.py`.*

> *Open Spotify and start the focus playlist.* *(macOS / Linux apps with native deep-links)*

> *Run `pytest` in `~/projects/myapp` and show me a one-line summary of failures.*

> *At 7pm tonight, remind me to take out the trash.*

---

## Architecture

```
┌────────────────┐  fetch /api/chat (SSE)   ┌───────────────────────┐
│  Web UI        │ ────────────────────────►│  FastAPI · /api/chat  │
│  /web/*        │ ◄─── events: model,      │  app/main.py          │
│  vanilla JS    │     status, tool_call,   └──────────┬────────────┘
└────────────────┘     tool_result,                    │
                       confirmation_needed,            ▼
                       message, final            ┌─────────────┐
                                                 │  Agent loop │
                                                 │ app/agent.py│
                                                 └────┬────────┘
        ┌────────────────────────────────────────────┼─────────────┐
        ▼                  ▼                          ▼             ▼
 ┌────────────┐     ┌───────────┐             ┌──────────────┐  ┌─────────┐
 │ Mistral    │     │ Tools     │             │ Safety       │  │ Router  │
 │ chat API   │◄────│ (21 fns)  │             │ confirmation │  │ auto    │
 │ + function │     │ files,    │             │ + audit log  │  │ model   │
 │   calling  │     │ shell,    │             └──────────────┘  └─────────┘
 └────────────┘     │ apps, …   │
                    └───────────┘
```

* **`app/tools.py`** – every public function is auto-converted into a
  Mistral function-calling JSON schema by `mistral_client.build_tool_schemas`.
* **`app/router.py`** – keyword heuristics first; LLM classifier as fallback.
* **`app/agent.py`** – yields events; the FastAPI layer turns them into
  Server-Sent Events for the browser.
* **`app/safety.py`** – central policy + JSONL audit log.

### Add your own tool

1. Add a function to `app/tools.py` with a clear docstring and plain
   `str` / `int` / `bool` parameters.
2. Add its name to the `TOOLS` list at the bottom.
3. Decide whether it should appear in `READONLY_TOOLS` or the
   `write_like` set in `app/safety.py`.

That's it – it shows up to the model on next request.

---

## Tests

```bash
uv run pytest tests/ -v       # 44 tests, no API key needed

---

## Configuration reference

All `MLA_*` env vars (place in `.env` or export in your shell):

| Variable | Default | Description |
|---|---|---|
| `MLA_MISTRAL_API_KEY` | — | **Required.** Your Mistral API key. |
| `MLA_DEFAULT_MODEL` | `auto` | Default model id (or `auto`). |
| `MLA_ROUTER_MODEL` | `ministral-3b-latest` | Classifier used in auto mode. |
| `MLA_HOST` | `127.0.0.1` | Bind address. |
| `MLA_PORT` | `8000` | Bind port. |
| `MLA_SAFETY_MODE` | `normal` | `strict` / `normal` / `yolo`. |
| `MLA_WORKSPACE_DIR` | `~` | Root the agent operates from. |
| `MLA_AUDIT_LOG` | `~/.mistral_assistant_audit.log` | Append-only JSONL log. |
| `MLA_MAX_AGENT_STEPS` | `20` | Max tool-call iterations per turn. |
| `MLA_SHELL_TIMEOUT_SECONDS` | `120` | Default `run_shell` timeout. |

---

## Security notes

* Your API key never leaves your machine — it goes only to
  `api.mistral.ai` over HTTPS.
* The default bind is `127.0.0.1`. Use `--host 0.0.0.0` only behind a
  firewall / reverse proxy you trust.
* `get_env` masks any variable name containing `KEY`, `TOKEN`, `SECRET`,
  or `PASSWORD` before showing it to the model.
* Files are still resolved with shell-style expansion (`~`, `$VAR`) but
  paths are otherwise free – consider setting `MLA_WORKSPACE_DIR` to
  scope where the agent can write.

---

## License

MIT. Built with ☕ and a love for tiny single-binary tools.
