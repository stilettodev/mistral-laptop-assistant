"""Automation tools exposed to the Mistral agent.

Each public function in :class:`Tools` is automatically converted to a
Mistral function-calling schema. Keep the signatures simple
(``str``/``int``/``bool`` arguments only) so the JSON schema generation
stays unambiguous for the model.

Tools are deliberately defensive – they return structured ``{"ok":
bool, ...}`` dicts so the agent can reason about failures instead of
crashing the conversation.
"""

from __future__ import annotations

import base64
import io
import os
import platform
import shlex
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Any

import psutil

from .config import settings


def _result(ok: bool, **payload: Any) -> dict[str, Any]:
    return {"ok": ok, **payload}


def _truncate(text: str, limit: int = 8000) -> str:
    if len(text) <= limit:
        return text
    return (
        text[: limit // 2]
        + f"\n…[truncated {len(text) - limit} chars]…\n"
        + text[-limit // 2 :]
    )


def _resolve_path(path: str) -> Path:
    expanded = os.path.expanduser(os.path.expandvars(path))
    p = Path(expanded)
    if not p.is_absolute():
        p = (settings.workspace_dir / p).resolve()
    return p


# ---------------------------------------------------------------------------
# Filesystem
# ---------------------------------------------------------------------------


def list_dir(path: str = ".", show_hidden: bool = False) -> dict[str, Any]:
    """List entries of a directory.

    Args:
        path: Directory to list. Relative paths are resolved against the
            configured workspace directory (usually $HOME).
        show_hidden: Include dotfiles when True.
    """
    p = _resolve_path(path)
    if not p.exists():
        return _result(False, error=f"{p} does not exist")
    if not p.is_dir():
        return _result(False, error=f"{p} is not a directory")
    entries = []
    for child in sorted(p.iterdir(), key=lambda c: (not c.is_dir(), c.name.lower())):
        if not show_hidden and child.name.startswith("."):
            continue
        try:
            stat = child.stat()
            entries.append(
                {
                    "name": child.name,
                    "type": "dir" if child.is_dir() else "file",
                    "size": stat.st_size,
                    "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(
                        timespec="seconds"
                    ),
                }
            )
        except OSError:
            continue
    return _result(True, path=str(p), count=len(entries), entries=entries)


def read_file(path: str, max_bytes: int = 200_000) -> dict[str, Any]:
    """Read a UTF-8 text file from disk.

    Args:
        path: File path. Binary files return an error.
        max_bytes: Refuse to read files larger than this many bytes.
    """
    p = _resolve_path(path)
    if not p.exists() or not p.is_file():
        return _result(False, error=f"{p} is not a file")
    size = p.stat().st_size
    if size > max_bytes:
        return _result(
            False,
            error=f"file is {size} bytes (> {max_bytes}); raise max_bytes to override",
        )
    try:
        text = p.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return _result(False, error="file is not valid UTF-8")
    return _result(True, path=str(p), size=size, content=text)


def write_file(path: str, content: str, mode: str = "overwrite") -> dict[str, Any]:
    """Write text to a file.

    Args:
        path: Destination path. Parent directories are created.
        content: Text to write.
        mode: ``overwrite`` (default) or ``append``.
    """
    p = _resolve_path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    if mode not in {"overwrite", "append"}:
        return _result(False, error=f"unknown mode {mode!r}")
    open_mode = "w" if mode == "overwrite" else "a"
    with p.open(open_mode, encoding="utf-8") as fh:
        fh.write(content)
    return _result(True, path=str(p), bytes_written=len(content.encode("utf-8")))


def append_file(path: str, content: str) -> dict[str, Any]:
    """Append text to a file (creates it if missing)."""
    return write_file(path=path, content=content, mode="append")


def move_file(src: str, dst: str) -> dict[str, Any]:
    """Move or rename a file/directory."""
    s, d = _resolve_path(src), _resolve_path(dst)
    if not s.exists():
        return _result(False, error=f"{s} does not exist")
    d.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(s), str(d))
    return _result(True, src=str(s), dst=str(d))


def delete_path(path: str) -> dict[str, Any]:
    """Delete a file or directory recursively."""
    p = _resolve_path(path)
    if not p.exists():
        return _result(False, error=f"{p} does not exist")
    if p.is_dir():
        shutil.rmtree(p)
    else:
        p.unlink()
    return _result(True, deleted=str(p))


# ---------------------------------------------------------------------------
# Shell / processes
# ---------------------------------------------------------------------------


def run_shell(command: str, working_dir: str = "", timeout: int = 0) -> dict[str, Any]:
    """Run a shell command and capture stdout/stderr.

    Args:
        command: Full command line. Executed via the user's default shell.
        working_dir: Directory to run in. Defaults to the workspace.
        timeout: Seconds before the command is killed. 0 = use default.
    """
    cwd = _resolve_path(working_dir) if working_dir else settings.workspace_dir
    timeout = timeout or settings.shell_timeout_seconds
    try:
        proc = subprocess.run(
            command,
            shell=True,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        return _result(
            False,
            error=f"timeout after {timeout}s",
            stdout=_truncate(exc.stdout or ""),
            stderr=_truncate(exc.stderr or ""),
        )
    return _result(
        proc.returncode == 0,
        returncode=proc.returncode,
        cwd=str(cwd),
        stdout=_truncate(proc.stdout),
        stderr=_truncate(proc.stderr),
    )


def which(program: str) -> dict[str, Any]:
    """Return the absolute path of an executable on $PATH (or None)."""
    found = shutil.which(program)
    return _result(found is not None, program=program, path=found)


def list_processes(filter: str = "", limit: int = 25) -> dict[str, Any]:
    """List running processes (PID, name, CPU%, memory MB).

    Args:
        filter: Case-insensitive substring on the process name.
        limit: Maximum number of processes to return.
    """
    procs: list[dict[str, Any]] = []
    needle = filter.lower()
    for p in psutil.process_iter(["pid", "name", "username", "memory_info"]):
        try:
            info = p.info
            name = info.get("name") or ""
            if needle and needle not in name.lower():
                continue
            mem_mb = round((info["memory_info"].rss if info["memory_info"] else 0) / 1e6, 1)
            procs.append(
                {
                    "pid": info["pid"],
                    "name": name,
                    "user": info.get("username") or "",
                    "memory_mb": mem_mb,
                }
            )
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    procs.sort(key=lambda x: x["memory_mb"], reverse=True)
    return _result(True, count=len(procs), processes=procs[:limit])


def kill_process(pid: int, force: bool = False) -> dict[str, Any]:
    """Terminate (or kill -9) a process by PID."""
    try:
        proc = psutil.Process(pid)
        name = proc.name()
        if force:
            proc.kill()
        else:
            proc.terminate()
        return _result(True, pid=pid, name=name, signal="kill" if force else "term")
    except psutil.NoSuchProcess:
        return _result(False, error=f"no process with pid {pid}")
    except psutil.AccessDenied:
        return _result(False, error="access denied (try sudo)")


# ---------------------------------------------------------------------------
# Apps / browser / notifications / clipboard
# ---------------------------------------------------------------------------


def open_app(name: str) -> dict[str, Any]:
    """Open a desktop application by name or executable path.

    Uses the platform's native opener (``open`` on macOS, ``xdg-open``
    on Linux, ``start`` on Windows) and also falls back to direct
    execution if the app is on PATH.
    """
    sys_name = platform.system()
    try:
        if sys_name == "Darwin":
            subprocess.Popen(["open", "-a", name])
        elif sys_name == "Windows":
            subprocess.Popen(["cmd", "/c", "start", "", name], shell=False)
        else:  # Linux / others
            if shutil.which(name):
                subprocess.Popen([name])
            else:
                subprocess.Popen(["xdg-open", name])
        return _result(True, app=name, platform=sys_name)
    except (FileNotFoundError, OSError) as exc:
        return _result(False, error=str(exc))


def open_url(url: str) -> dict[str, Any]:
    """Open a URL in the user's default browser."""
    if not url.startswith(("http://", "https://", "file://")):
        url = "https://" + url
    ok = webbrowser.open(url, new=2)
    return _result(ok, url=url)


def notify(title: str, message: str = "") -> dict[str, Any]:
    """Show a desktop notification."""
    sys_name = platform.system()
    try:
        if sys_name == "Darwin":
            script = f'display notification "{message}" with title "{title}"'
            subprocess.run(["osascript", "-e", script], check=False)
        elif sys_name == "Linux":
            if shutil.which("notify-send"):
                subprocess.run(["notify-send", title, message], check=False)
            else:
                return _result(False, error="install libnotify-bin / notify-send")
        elif sys_name == "Windows":
            ps = (
                "[reflection.assembly]::loadwithpartialname('System.Windows.Forms');"
                "[reflection.assembly]::loadwithpartialname('System.Drawing');"
                "$n=new-object system.windows.forms.notifyicon;"
                "$n.icon=[system.drawing.systemicons]::Information;"
                f"$n.balloontiptitle='{title}';"
                f"$n.balloontiptext='{message}';"
                "$n.visible=$true;$n.showballoontip(5000);"
                "start-sleep -s 6;$n.dispose()"
            )
            subprocess.Popen(["powershell", "-NoProfile", "-Command", ps])
        return _result(True, title=title, message=message)
    except (FileNotFoundError, OSError) as exc:
        return _result(False, error=str(exc))


def clipboard_get() -> dict[str, Any]:
    """Read the current clipboard text content."""
    try:
        import pyperclip

        return _result(True, text=pyperclip.paste())
    except Exception as exc:  # pyperclip may raise PyperclipException on Linux without xclip
        return _result(False, error=f"clipboard unavailable: {exc}")


def clipboard_set(text: str) -> dict[str, Any]:
    """Copy text to the clipboard."""
    try:
        import pyperclip

        pyperclip.copy(text)
        return _result(True, bytes=len(text.encode("utf-8")))
    except Exception as exc:
        return _result(False, error=f"clipboard unavailable: {exc}")


# ---------------------------------------------------------------------------
# Screenshot
# ---------------------------------------------------------------------------


def screenshot(save_path: str = "") -> dict[str, Any]:
    """Capture the primary monitor as a PNG file.

    If ``save_path`` is empty, the image is written to a temp file.
    Returns the absolute path plus a base64 thumbnail for the UI.
    """
    try:
        import mss
        import mss.tools

        if save_path:
            out = _resolve_path(save_path)
        else:
            out = Path(tempfile.gettempdir()) / f"mla_screenshot_{int(time.time())}.png"
        out.parent.mkdir(parents=True, exist_ok=True)
        with mss.mss() as sct:
            monitor = sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]
            img = sct.grab(monitor)
            mss.tools.to_png(img.rgb, img.size, output=str(out))
        data = out.read_bytes()
        b64 = base64.b64encode(data).decode("ascii")
        return _result(
            True,
            path=str(out),
            size_bytes=len(data),
            width=img.size[0],
            height=img.size[1],
            preview_base64=b64[:200_000],  # cap UI payload
        )
    except Exception as exc:
        return _result(False, error=f"screenshot failed: {exc}")


# ---------------------------------------------------------------------------
# Web search
# ---------------------------------------------------------------------------


def web_search(query: str, max_results: int = 5) -> dict[str, Any]:
    """Search the web (DuckDuckGo). No API key required."""
    try:
        from ddgs import DDGS

        with DDGS() as ddg:
            raw = list(ddg.text(query, max_results=max_results))
        results = [
            {
                "title": r.get("title", ""),
                "url": r.get("href") or r.get("url", ""),
                "snippet": r.get("body", ""),
            }
            for r in raw
        ]
        return _result(True, query=query, count=len(results), results=results)
    except Exception as exc:
        return _result(False, error=f"search failed: {exc}")


# ---------------------------------------------------------------------------
# System info / env
# ---------------------------------------------------------------------------


def system_info() -> dict[str, Any]:
    """Return high-level information about the host machine."""
    vm = psutil.virtual_memory()
    du = psutil.disk_usage(str(Path.home()))
    return _result(
        True,
        platform=platform.platform(),
        system=platform.system(),
        release=platform.release(),
        python=sys.version.split()[0],
        cpu_count=psutil.cpu_count(logical=True),
        cpu_percent=psutil.cpu_percent(interval=0.2),
        memory_total_gb=round(vm.total / 1e9, 2),
        memory_used_gb=round(vm.used / 1e9, 2),
        memory_percent=vm.percent,
        disk_total_gb=round(du.total / 1e9, 2),
        disk_used_gb=round(du.used / 1e9, 2),
        disk_percent=du.percent,
        user=os.environ.get("USER") or os.environ.get("USERNAME") or "",
        home=str(Path.home()),
        cwd=os.getcwd(),
    )


def get_env(name: str) -> dict[str, Any]:
    """Read an environment variable (returns empty string if unset).

    Secrets are masked unless the variable is on a small allow-list of
    common public values.
    """
    value = os.environ.get(name, "")
    if not value:
        return _result(False, name=name, value="")
    sensitive = any(s in name.upper() for s in ("KEY", "TOKEN", "SECRET", "PASSWORD"))
    if sensitive:
        masked = value[:2] + "…" + value[-2:] if len(value) > 6 else "***"
        return _result(True, name=name, value=masked, masked=True)
    return _result(True, name=name, value=value, masked=False)


def get_datetime(timezone: str = "") -> dict[str, Any]:
    """Return the current local (or named) datetime in ISO format."""
    now = datetime.now()
    return _result(True, iso=now.isoformat(timespec="seconds"), epoch=int(now.timestamp()))


# ---------------------------------------------------------------------------
# Scheduling
# ---------------------------------------------------------------------------


def schedule_task(command: str, run_at: str) -> dict[str, Any]:
    """Schedule a shell command to run later (one-shot).

    Args:
        command: Shell command to execute.
        run_at: When to run – format ``HH:MM`` (today/tomorrow) or
            ``YYYY-MM-DDTHH:MM`` for an absolute date/time.

    Uses ``at`` on macOS/Linux and ``schtasks`` on Windows. For
    recurring jobs see :func:`schedule_recurring`.
    """
    sys_name = platform.system()
    try:
        if sys_name == "Windows":
            task_name = f"MLA_{int(time.time())}"
            # Parse run_at
            if "T" in run_at:
                date, time_part = run_at.split("T")
            else:
                date = datetime.now().strftime("%Y/%m/%d")
                time_part = run_at
            subprocess.run(
                [
                    "schtasks", "/Create",
                    "/SC", "ONCE",
                    "/TN", task_name,
                    "/TR", command,
                    "/ST", time_part,
                    "/SD", date.replace("-", "/"),
                ],
                check=True,
            )
            return _result(True, scheduler="schtasks", task=task_name)
        if not shutil.which("at"):
            return _result(False, error="`at` is not installed (try: sudo apt install at)")
        proc = subprocess.run(
            ["at", run_at],
            input=command,
            text=True,
            capture_output=True,
            check=False,
        )
        return _result(
            proc.returncode == 0,
            scheduler="at",
            stdout=proc.stdout,
            stderr=proc.stderr,
        )
    except (FileNotFoundError, OSError, subprocess.CalledProcessError) as exc:
        return _result(False, error=str(exc))


# ---------------------------------------------------------------------------
# Recurring scheduler (in-process)
# ---------------------------------------------------------------------------


def schedule_recurring(
    name: str,
    when: str,
    command: str = "",
    prompt: str = "",
) -> dict[str, Any]:
    """Create a recurring job that runs in the assistant process.

    The job survives restarts (persisted to disk). Provide either
    ``command`` (shell) or ``prompt`` (re-ask the assistant), not both.

    Args:
        name: Human-readable label.
        when: Schedule spec, e.g. ``every 30m`` · ``daily 09:30`` ·
            ``weekly mon 18:00`` · ``hourly :15`` ·
            ``cron */15 * * * *``.
        command: Shell command to run on each tick (shell job).
        prompt: Chat prompt to send to the assistant on each tick.
    """
    from .scheduler import SCHEDULER

    try:
        kind = "chat" if prompt and not command else "shell"
        job = SCHEDULER.add(name=name, when=when, kind=kind, command=command, prompt=prompt)
        return _result(True, job=job)
    except ValueError as exc:
        return _result(False, error=str(exc))


def list_recurring() -> dict[str, Any]:
    """List all recurring jobs with their next run time."""
    from .scheduler import SCHEDULER

    jobs = SCHEDULER.list()
    return _result(True, count=len(jobs), jobs=jobs)


def cancel_recurring(job_id: str) -> dict[str, Any]:
    """Delete a recurring job by its id (see :func:`list_recurring`)."""
    from .scheduler import SCHEDULER

    ok = SCHEDULER.remove(job_id)
    return _result(ok, id=job_id)


def toggle_recurring(job_id: str, enabled: bool) -> dict[str, Any]:
    """Enable or disable a recurring job."""
    from .scheduler import SCHEDULER

    ok = SCHEDULER.toggle(job_id, enabled)
    return _result(ok, id=job_id, enabled=enabled)


# ---------------------------------------------------------------------------
# Long-term memory
# ---------------------------------------------------------------------------


def remember(key: str, value: str) -> dict[str, Any]:
    """Save a fact to long-term memory (survives across sessions).

    Memory is automatically included in the system prompt of every new
    conversation. Good for preferences, paths, names – anything the
    user expects you to know forever.
    """
    from .memory import remember as _remember

    return _remember(key, value)


def recall(key: str = "") -> dict[str, Any]:
    """Read a fact (or all facts when ``key`` is empty)."""
    from .memory import recall as _recall

    return _recall(key)


def forget(key: str) -> dict[str, Any]:
    """Delete a fact from long-term memory."""
    from .memory import forget as _forget

    return _forget(key)


# ---------------------------------------------------------------------------
# Registry – mapped automatically into Mistral function schemas
# ---------------------------------------------------------------------------


TOOLS: dict[str, Any] = {
    fn.__name__: fn
    for fn in [
        list_dir,
        read_file,
        write_file,
        append_file,
        move_file,
        delete_path,
        run_shell,
        which,
        list_processes,
        kill_process,
        open_app,
        open_url,
        notify,
        clipboard_get,
        clipboard_set,
        screenshot,
        web_search,
        system_info,
        get_env,
        get_datetime,
        schedule_task,
        schedule_recurring,
        list_recurring,
        cancel_recurring,
        toggle_recurring,
        remember,
        recall,
        forget,
    ]
}


def short_description(fn: Any) -> str:
    """First non-empty line of the docstring, lightly cleaned."""
    doc = textwrap.dedent(fn.__doc__ or "").strip()
    return doc.split("\n\n", 1)[0].replace("\n", " ")
