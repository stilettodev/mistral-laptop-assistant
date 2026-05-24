"""Tiny command-line launcher: ``mla`` or ``python -m app``.

Usage::

    mla                          # start server on 127.0.0.1:8000
    mla --open                   # also open in your default browser
    mla --window                 # open a real native window (pywebview)
    mla --tray                   # add a system-tray icon (pystray)
    mla --port 8080 --open       # different port
    mla audit --tail             # tail the audit log
    mla jobs                     # show recurring jobs
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import threading
import time
import webbrowser
from pathlib import Path

import uvicorn

from .config import settings


def _print_banner(url: str) -> None:
    print(f"\n🤖  Mistral Laptop Assistant → {url}")
    print(f"    workspace: {settings.workspace_dir}")
    print(f"    audit log: {settings.audit_log}")
    print(f"    safety:    {settings.safety_mode}")
    if settings.allow_tools:
        print(f"    allow:     {settings.allow_tools}")
    if settings.deny_tools:
        print(f"    deny:      {settings.deny_tools}")
    if not settings.mistral_api_key:
        print("    ⚠️  no API key – set MLA_MISTRAL_API_KEY in .env\n")
    else:
        print("    ✅ API key loaded\n")


def cmd_serve(args: argparse.Namespace) -> int:
    host, port = args.host, args.port
    url = f"http://{host if host != '0.0.0.0' else '127.0.0.1'}:{port}/"

    # --key flag overrides everything else
    if args.api_key:
        import os
        os.environ["MLA_MISTRAL_API_KEY"] = args.api_key
        Path.home().joinpath(".mistral_assistant.env").write_text(
            f"MLA_MISTRAL_API_KEY={args.api_key}\n"
        )
        # Re-exec so pydantic-settings picks up the new key
        import os as _os, sys as _sys
        _os.execv(_sys.executable, [_sys.executable, *_sys.argv])

    _print_banner(url)

    if not settings.mistral_api_key:
        key = _prompt_api_key()
        if not key:
            return 1

    # Build the post-startup background tasks.
    def post_start() -> None:
        from .desktop import wait_for_server

        if not wait_for_server(url, timeout=10):
            print("⚠️  server did not respond in time; skipping browser/window/tray")
            return
        if args.open:
            webbrowser.open(url)
        if args.window:
            from .desktop import in_background, run_window

            in_background(run_window, url)
        if args.tray:
            from .desktop import in_background, run_tray

            in_background(run_tray, url)

    if args.open or args.window or args.tray:
        threading.Thread(target=post_start, daemon=True).start()

    uvicorn.run(
        "app.main:app",
        host=host,
        port=port,
        log_level="info" if not args.quiet else "warning",
        reload=args.reload,
    )
    return 0


def _prompt_api_key() -> str | None:
    """Interactively ask for an API key and persist it to ~/.mistral_assistant.env.

    After saving, re-execs the current process so settings pick it up.
    """
    import getpass, os, sys

    print()
    print("  🤖  MLA needs your Mistral API key")
    print("  Get one free at: https://console.mistral.ai/")
    print()
    try:
        key = getpass.getpass("  Paste your key (hidden): ").strip()
    except EOFError:
        try:
            key = input("  Paste your key: ").strip()
        except (EOFError, KeyboardInterrupt):
            key = ""
    if not key:
        print("  No key entered. Set MLA_MISTRAL_API_KEY in .env and restart.")
        return None
    env_path = Path.home() / ".mistral_assistant.env"
    env_path.write_text(f"MLA_MISTRAL_API_KEY={key}\n")
    print(f"  ✓ saved to {env_path}")
    # Re-exec so pydantic-settings picks up the new env var.
    print("  Starting server…\n")
    os.execv(sys.executable, [sys.executable, *sys.argv])


def cmd_audit(args: argparse.Namespace) -> int:
    path: Path = settings.audit_log
    if not path.exists():
        print(f"no audit log at {path}")
        return 0
    if args.tail:
        print(f"📜  tailing {path} (Ctrl-C to stop)\n")
        with path.open("r", encoding="utf-8") as fh:
            fh.seek(0, os.SEEK_END)
            try:
                while True:
                    line = fh.readline()
                    if not line:
                        time.sleep(0.5)
                        continue
                    sys.stdout.write(line)
                    sys.stdout.flush()
            except KeyboardInterrupt:
                pass
    else:
        print(path.read_text(encoding="utf-8"))
    return 0


def cmd_jobs(_: argparse.Namespace) -> int:
    path: Path = settings.scheduler_file
    if not path.exists():
        print("no scheduled jobs")
        return 0
    data = json.loads(path.read_text(encoding="utf-8"))
    jobs = data.get("jobs", [])
    if not jobs:
        print("no scheduled jobs")
        return 0
    print(f"📅  {len(jobs)} scheduled job(s):\n")
    for j in jobs:
        on = "✅" if j.get("enabled") else "🚫"
        kind = j.get("kind", "shell")
        target = j.get("command") if kind == "shell" else j.get("prompt")
        print(f"  {on}  {j['id']}  [{kind:5}]  every {j['when']:<24}  {j['name']}")
        if target:
            print(f"       └── {target[:70]}")
    return 0


def cmd_memory(_: argparse.Namespace) -> int:
    path: Path = settings.memory_file
    if not path.exists():
        print("no memory entries")
        return 0
    data = json.loads(path.read_text(encoding="utf-8"))
    if not data:
        print("no memory entries")
        return 0
    print(f"🧠  {len(data)} memory entrie(s):\n")
    for k, v in sorted(data.items()):
        print(f"  - {k}: {v['value']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mla", description="Mistral Laptop Assistant")
    sub = parser.add_subparsers(dest="cmd")

    serve = sub.add_parser("serve", help="run the web UI + API (default)")
    serve.add_argument("--host", default=settings.host)
    serve.add_argument("--port", type=int, default=settings.port)
    serve.add_argument("--open", action="store_true", help="open the browser")
    serve.add_argument("--window", action="store_true", help="open a native desktop window")
    serve.add_argument("--tray", action="store_true", help="add a system tray icon")
    serve.add_argument("--reload", action="store_true", help="auto-reload on code changes")
    serve.add_argument("--quiet", action="store_true", help="reduce uvicorn log noise")
    serve.add_argument("-k", "--key", dest="api_key", default="",
                      help="Mistral API key (or set MLA_MISTRAL_API_KEY env var / .env)")
    serve.set_defaults(func=cmd_serve)

    audit = sub.add_parser("audit", help="show the tool-call audit log")
    audit.add_argument("--tail", action="store_true", help="follow new entries")
    audit.set_defaults(func=cmd_audit)

    jobs = sub.add_parser("jobs", help="show recurring jobs")
    jobs.set_defaults(func=cmd_jobs)

    memory = sub.add_parser("memory", help="show long-term memory entries")
    memory.set_defaults(func=cmd_memory)

    args = parser.parse_args(argv)
    if not args.cmd:
        return cmd_serve(parser.parse_args(["serve"]))
    return args.func(args)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
