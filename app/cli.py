"""Tiny command-line launcher: ``mla`` or ``python -m app``.

Usage::

    mla                        # start server on 127.0.0.1:8000
    mla --port 8080 --open     # listen on 8080 and open the browser
    mla audit --tail           # tail the audit log
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import webbrowser
from pathlib import Path

import uvicorn

from .config import settings


def cmd_serve(args: argparse.Namespace) -> int:
    host, port = args.host, args.port
    url = f"http://{host}:{port}/"
    print(f"\n🤖  Mistral Laptop Assistant → {url}")
    print(f"    workspace: {settings.workspace_dir}")
    print(f"    audit log: {settings.audit_log}")
    print(f"    safety:    {settings.safety_mode}")
    if not settings.mistral_api_key:
        print("    ⚠️  no API key – set MLA_MISTRAL_API_KEY in .env\n")
    else:
        print("    ✅ API key loaded\n")
    if args.open:
        # Give uvicorn a moment to bind before launching the browser.
        import threading

        def _open() -> None:
            time.sleep(1.0)
            webbrowser.open(url)

        threading.Thread(target=_open, daemon=True).start()

    uvicorn.run(
        "app.main:app",
        host=host,
        port=port,
        log_level="info",
        reload=args.reload,
    )
    return 0


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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mla", description="Mistral Laptop Assistant")
    sub = parser.add_subparsers(dest="cmd")

    serve = sub.add_parser("serve", help="run the web UI + API (default)")
    serve.add_argument("--host", default=settings.host)
    serve.add_argument("--port", type=int, default=settings.port)
    serve.add_argument("--open", action="store_true", help="open the browser")
    serve.add_argument("--reload", action="store_true", help="auto-reload on code changes")
    serve.set_defaults(func=cmd_serve)

    audit = sub.add_parser("audit", help="show the tool-call audit log")
    audit.add_argument("--tail", action="store_true", help="follow new entries")
    audit.set_defaults(func=cmd_audit)

    args = parser.parse_args(argv)
    if not args.cmd:
        # default = serve
        return cmd_serve(parser.parse_args(["serve"]))
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
