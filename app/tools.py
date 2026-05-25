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
import json
import os
import platform
import shlex
import shutil
import socket
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
from .skills import install_skill, list_available_skills, list_skills, uninstall_skill


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
    timeout = int(timeout) or settings.shell_timeout_seconds
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
    limit = int(limit)
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


def open_url(url: str, extract_content: bool = True) -> dict[str, Any]:
    """Open a URL in the browser and optionally retrieve its text content.

    If extract_content is True (default), also fetches and returns the
    page text so the agent can read what was retrieved. Uses Tavily's
    extract API if TAVILY_API_KEY is set, otherwise falls back to
    read_url.
    """
    if not url.startswith(("http://", "https://", "file://")):
        url = "https://" + url

    # Open in browser regardless — user should see it.
    webbrowser.open(url, new=2)

    if not extract_content:
        return _result(True, url=url, opened=True, content=None)

    # Try to retrieve content so the model sees what was fetched.
    content = None

    # Try Tavily first (better extraction).
    tavily_key = os.environ.get("TAVILY_API_KEY")
    if tavily_key:
        try:
            import urllib.request

            req = urllib.request.Request(
                "https://api.tavily.com/extract",
                data=json.dumps({"urls": [url], "max_results": 1}).encode(),
                headers={"Content-Type": "application/json", "Authorization": f"Bearer {tavily_key}"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read())
            results = data.get("results", [])
            if results and results[0].get("raw_content"):
                content = results[0]["raw_content"]
        except Exception:
            pass  # Fall through to read_url fallback.

    # Fallback: basic HTTP fetch via read_url logic.
    if content is None:
        try:
            import httpx

            with httpx.Client(timeout=10.0, follow_redirects=True) as client:
                resp = client.get(url)
            if resp.status_code == 200 and "text/html" in resp.headers.get("content-type", ""):
                # Strip HTML tags for plain text.
                from bs4 import BeautifulSoup

                soup = BeautifulSoup(resp.text, "html.parser")
                # Remove script and style elements.
                for tag in soup(["script", "style"]):
                    tag.decompose()
                content = soup.get_text(separator="\n", strip=True)
                # Clean up excessive newlines.
                import re

                content = re.sub(r"\n{3,}", "\n\n", content)
        except Exception:
            pass  # Could not extract content.

    # Truncate long content so the message doesn't explode.
    if content:
        content = content[: 15_000] if len(content) > 15_000 else content

    return _result(True, url=url, opened=True, content=content)


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
# HTTP / networking
# ---------------------------------------------------------------------------


def read_url(url: str, max_bytes: int = 50_000) -> dict[str, Any]:
    """Fetch the text content of any URL (GET request)."""
    try:
        import httpx

        max_bytes = int(max_bytes)
        with httpx.Client(timeout=10.0, follow_redirects=True) as client:
            resp = client.get(url)
        text = resp.text[:max_bytes]
        truncated = len(resp.text) > max_bytes
        return _result(
            True,
            url=url,
            status_code=resp.status_code,
            content_type=resp.headers.get("content-type", ""),
            text=text,
            truncated=truncated,
            total_bytes=len(resp.text),
        )
    except Exception as exc:
        return _result(False, error=f"read_url failed: {exc}")


def get_public_ip() -> dict[str, Any]:
    """Return the machine's external/public IP address."""
    try:
        import httpx

        with httpx.Client(timeout=8.0) as client:
            ip = client.get("https://api.ipify.org", params={"format": "text"}).text.strip()
        return _result(True, ip=ip)
    except Exception as exc:
        return _result(False, error=str(exc))


def ping_host(hostname: str, count: int = 4) -> dict[str, Any]:
    """Ping a hostname or IP and return packet stats.

    Works cross-platform using system ping command.
    """
    try:
        count = int(count)  # ensure int even if LLM sends string
        args = ["-c", str(count)] if sys.platform != "win32" else ["-n", str(count)]
        cmd = ["ping"] + args + [hostname]
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=count * 5 + 5
        )
        return _result(
            proc.returncode == 0,
            hostname=hostname,
            returncode=proc.returncode,
            output=_truncate(proc.stdout + proc.stderr, 2000),
        )
    except subprocess.TimeoutExpired:
        return _result(False, error=f"ping timed out after {int(count) * 5}s")
    except Exception as exc:
        return _result(False, error=str(exc))


def dns_lookup(hostname: str) -> dict[str, Any]:
    """Resolve a hostname to its IP address(es)."""
    try:
        addrs = sorted(set(r[-1][0] for r in socket.getaddrinfo(hostname, None)))
        return _result(True, hostname=hostname, addresses=addrs, count=len(addrs))
    except Exception as exc:
        return _result(False, error=str(exc))


# ---------------------------------------------------------------------------
# File system helpers
# ---------------------------------------------------------------------------


def find_files(directory: str, pattern: str = "*", max_results: int = 20) -> dict[str, Any]:
    """Recursively search for files matching ``pattern`` (glob) under ``directory``."""
    try:
        root = _resolve_path(directory)
        max_results = int(max_results)
        matches = list(root.rglob(pattern))
        dirs, files = [], []
        for p in matches:
            if p.is_dir():
                dirs.append(str(p))
            else:
                files.append(str(p))
            if len(files) + len(dirs) >= max_results:
                break
        return _result(
            True,
            directory=str(root),
            pattern=pattern,
            files=files,
            directories=dirs,
            count=len(files) + len(dirs),
            truncated=len(matches) > max_results,
        )
    except Exception as exc:
        return _result(False, error=str(exc))


def file_size(path: str) -> dict[str, Any]:
    """Return size, creation time, and modification time for a path."""
    try:
        p = _resolve_path(path)
        stat = p.stat()
        return _result(
            True,
            path=str(p),
            size_bytes=stat.st_size,
            created=int(stat.st_ctime),
            modified=int(stat.st_mtime),
            is_file=p.is_file(),
            is_dir=p.is_dir(),
        )
    except Exception as exc:
        return _result(False, error=str(exc))


def count_tokens(text: str) -> dict[str, Any]:
    """Estimate the number of tokens in a text string (chars / 4 approx)."""
    try:
        n = len(text) // 4
        return _result(True, chars=len(text), estimated_tokens=n)
    except Exception as exc:
        return _result(False, error=str(exc))


def hash_file(path: str, algorithm: str = "sha256") -> dict[str, Any]:
    """Compute a cryptographic hash of a file."""
    import hashlib

    try:
        p = _resolve_path(path)
        h = hashlib.new(algorithm)
        with open(p, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return _result(True, path=str(p), algorithm=algorithm, hash=h.hexdigest())
    except Exception as exc:
        return _result(False, error=str(exc))


# ---------------------------------------------------------------------------
# Text / encoding utilities
# ---------------------------------------------------------------------------


def hash_text(text: str, algorithm: str = "sha256") -> dict[str, Any]:
    """Compute a hash of a text string."""
    try:
        import hashlib
        h = hashlib.new(algorithm)
        h.update(text.encode())
        return _result(True, algorithm=algorithm, hash=h.hexdigest(), chars=len(text))
    except Exception as exc:
        return _result(False, error=str(exc))


def base64_encode(text: str) -> dict[str, Any]:
    """Encode text as base64."""
    try:
        b = base64.b64encode(text.encode()).decode()
        return _result(True, encoded=b)
    except Exception as exc:
        return _result(False, error=str(exc))


def base64_decode(data: str) -> dict[str, Any]:
    """Decode a base64 string back to text."""
    try:
        return _result(True, decoded=base64.b64decode(data.encode()).decode())
    except Exception as exc:
        return _result(False, error=str(exc))


def json_validate(text: str) -> dict[str, Any]:
    """Validate a JSON string and return pretty-printed version."""
    try:
        parsed = json.loads(text)
        return _result(True, valid=True, pretty=json.dumps(parsed, indent=2))
    except json.JSONDecodeError as exc:
        return _result(
            False, valid=False,
            error=f"line {exc.lineno}, col {exc.colno}: {exc.msg}",
        )


def grep_file(path: str, pattern: str, case_sensitive: bool = False) -> dict[str, Any]:
    """Search for ``pattern`` in a text file, return matching lines."""
    try:
        import re
        p = _resolve_path(path)
        flags = 0 if case_sensitive else re.IGNORECASE
        compiled = re.compile(pattern, flags)
        matches, line_no = [], 0
        for line_no, line in enumerate(p.read_text().splitlines(), 1):
            if compiled.search(line):
                matches.append({"line": line_no, "text": line.rstrip()})
                if len(matches) >= 100:
                    break
        return _result(
            True, path=str(p), pattern=pattern,
            matches=matches, count=len(matches), total_lines=line_no,
        )
    except Exception as exc:
        return _result(False, error=str(exc))


def line_count(path: str) -> dict[str, Any]:
    """Count lines, words, and characters in a text file."""
    try:
        p = _resolve_path(path)
        content = p.read_text()
        lines = content.splitlines()
        words = content.split()
        return _result(
            True, path=str(p),
            lines=len(lines), words=len(words), chars=len(content),
        )
    except Exception as exc:
        return _result(False, error=str(exc))


def extract_lines(path: str, start: int = 1, end: int = -1) -> dict[str, Any]:
    """Extract a range of lines from a text file (1-indexed, inclusive)."""
    try:
        p = _resolve_path(path)
        start = int(start)
        end = int(end)
        lines = p.read_text().splitlines()
        s = max(1, min(start, len(lines)))
        e = len(lines) if end == -1 else max(s, min(end, len(lines)))
        excerpt = "\n".join(lines[s - 1 : e])
        return _result(
            True, path=str(p), start_line=s, end_line=e, excerpt=excerpt,
        )
    except Exception as exc:
        return _result(False, error=str(exc))


def url_encode(text: str, safe: str = "") -> dict[str, Any]:
    """URL-encode a text string."""
    try:
        import urllib.parse
        return _result(True, encoded=urllib.parse.quote(text, safe=safe))
    except Exception as exc:
        return _result(False, error=str(exc))


def url_decode(text: str) -> dict[str, Any]:
    """URL-decode a percent-encoded string."""
    try:
        import urllib.parse
        return _result(True, decoded=urllib.parse.unquote(text))
    except Exception as exc:
        return _result(False, error=str(exc))


def compare_files(path1: str, path2: str) -> dict[str, Any]:
    """Compare two files byte-for-byte or line-by-line."""
    try:
        p1 = _resolve_path(path1)
        p2 = _resolve_path(path2)
        s1, s2 = p1.stat().st_size, p2.stat().st_size
        if s1 != s2:
            return _result(True, identical=False, reason="different sizes", size1=s1, size2=s2)
        # quick byte compare
        with open(p1, "rb") as a, open(p2, "rb") as b:
            chunk = 65536
            same = True
            while True:
                da, db = a.read(chunk), b.read(chunk)
                if da != db:
                    same = False
                    break
                if not da:
                    break
        return _result(True, identical=same, size_bytes=s1)
    except Exception as exc:
        return _result(False, error=str(exc))


def make_directory(path: str) -> dict[str, Any]:
    """Create a directory and any missing parents."""
    try:
        p = _resolve_path(path)
        p.mkdir(parents=True, exist_ok=True)
        return _result(True, path=str(p))
    except Exception as exc:
        return _result(False, error=str(exc))


def copy_file(src: str, dst: str) -> dict[str, Any]:
    """Copy a file to a destination (file or directory)."""
    try:
        s = _resolve_path(src)
        d = _resolve_path(dst)
        if d.is_dir():
            d = d / s.name
        shutil.copy2(s, d)
        return _result(True, src=str(s), dst=str(d), size_bytes=d.stat().st_size)
    except Exception as exc:
        return _result(False, error=str(exc))


# ---------------------------------------------------------------------------
# System monitoring
# ---------------------------------------------------------------------------


def get_cpu_usage() -> dict[str, Any]:
    """Return per-CPU and overall CPU utilisation (percent)."""
    try:
        per_cpu = psutil.cpu_percent(interval=0.5, percpu=True)
        return _result(
            True,
            overall=round(sum(per_cpu) / len(per_cpu), 1),
            per_cpu=[round(c, 1) for c in per_cpu],
            count=psutil.cpu_count(logical=False),
            logical_count=psutil.cpu_count(logical=True),
        )
    except Exception as exc:
        return _result(False, error=str(exc))


def get_memory_usage() -> dict[str, Any]:
    """Return RAM utilisation (total / free / used / percent)."""
    try:
        vm = psutil.virtual_memory()
        return _result(
            True,
            total_gb=round(vm.total / (1024**3), 2),
            available_gb=round(vm.available / (1024**3), 2),
            used_gb=round(vm.used / (1024**3), 2),
            percent=round(vm.percent, 1),
        )
    except Exception as exc:
        return _result(False, error=str(exc))


def get_disk_usage(path: str = "") -> dict[str, Any]:
    """Return disk space for a path (defaults to filesystem root)."""
    try:
        target = _resolve_path(path) if path else Path("/")
        du = psutil.disk_usage(str(target))
        return _result(
            True,
            path=str(target),
            total_gb=round(du.total / (1024**3), 2),
            used_gb=round(du.used / (1024**3), 2),
            free_gb=round(du.free / (1024**3), 2),
            percent=round(du.percent, 1),
        )
    except Exception as exc:
        return _result(False, error=str(exc))


def get_network_interfaces() -> dict[str, Any]:
    """List all network interfaces and their IPv4/IPv6 addresses."""
    try:
        interfaces = {}
        for name, addrs in psutil.net_if_addrs().items():
            infos = []
            for addr in addrs:
                infos.append({"family": str(addr.family).split(".")[-1],
                              "address": addr.address,
                              "netmask": addr.netmask})
            interfaces[name] = infos
        return _result(True, interfaces=interfaces, count=len(interfaces))
    except Exception as exc:
        return _result(False, error=str(exc))


def get_battery_status() -> dict[str, Any]:
    """Return battery charge percentage and power plugged-in state."""
    try:
        b = psutil.sensors_battery()
        if b is None:
            return _result(False, error="No battery detected on this machine")
        return _result(
            True,
            percent=round(b.percent, 1),
            plugged_in=b.power_plugged,
            time_left=int(b.secsleft) if b.secsleft >= 0 else -1,
        )
    except Exception as exc:
        return _result(False, error=str(exc))


def get_boot_time() -> dict[str, Any]:
    """Return system boot timestamp and uptime string."""
    try:
        boot = psutil.boot_time()
        uptime_s = time.time() - boot
        h, rem = divmod(int(uptime_s), 3600)
        m, s = divmod(rem, 60)
        return _result(
            True,
            boot_timestamp=int(boot),
            uptime_formatted=f"{h}h {m}m {s}s",
            uptime_seconds=int(uptime_s),
        )
    except Exception as exc:
        return _result(False, error=str(exc))


# ---------------------------------------------------------------------------
# Bookmarks
# ---------------------------------------------------------------------------

_BOOKMARK_FILE = Path(settings.workspace_dir) / ".bookmarks.json"


def _load_bookmarks() -> dict[str, str]:
    try:
        return json.loads(_BOOKMARK_FILE.read_text())
    except Exception:
        return {}


def _save_bookmarks(bm: dict[str, str]) -> None:
    _BOOKMARK_FILE.write_text(json.dumps(bm, indent=2))


def save_bookmark(url: str, label: str) -> dict[str, Any]:
    """Save a URL with a short label for quick access."""
    try:
        bm = _load_bookmarks()
        bm[label.lower().strip()] = url
        _save_bookmarks(bm)
        return _result(True, label=label.lower().strip(), url=url, count=len(bm))
    except Exception as exc:
        return _result(False, error=str(exc))


def list_bookmarks() -> dict[str, Any]:
    """List all saved bookmarks (label → URL)."""
    try:
        bm = _load_bookmarks()
        return _result(True, count=len(bm), bookmarks=bm)
    except Exception as exc:
        return _result(False, error=str(exc))


def open_bookmark(label: str) -> dict[str, Any]:
    """Open a bookmark by its label."""
    try:
        bm = _load_bookmarks()
        url = bm.get(label.lower().strip())
        if not url:
            return _result(False, error=f"No bookmark found for label '{label}'")
        webbrowser.open(url)
        return _result(True, label=label.lower().strip(), url=url)
    except Exception as exc:
        return _result(False, error=str(exc))


def delete_bookmark(label: str) -> dict[str, Any]:
    """Delete a saved bookmark by label."""
    try:
        bm = _load_bookmarks()
        if label.lower().strip() not in bm:
            return _result(False, error=f"No bookmark found for '{label}'")
        del bm[label.lower().strip()]
        _save_bookmarks(bm)
        return _result(True, label=label.lower().strip(), count=len(bm))
    except Exception as exc:
        return _result(False, error=str(exc))


# ---------------------------------------------------------------------------
# Web search
# ---------------------------------------------------------------------------


def web_search(query: str, max_results: int = 5) -> dict[str, Any]:
    """Search the web (DuckDuckGo). No API key required."""
    try:
        from ddgs import DDGS

        max_results = int(max_results)
        started = time.time()
        with DDGS() as ddg:
            raw = list(ddg.text(query, max_results=max_results))
        elapsed_ms = int((time.time() - started) * 1000)
        results = [
            {
                "title": r.get("title", ""),
                "url": r.get("href") or r.get("url", ""),
                "snippet": _truncate(r.get("body", ""), 300),
            }
            for r in raw
        ]
        return _result(
            True,
            query=query,
            count=len(results),
            results=results,
            duration_ms=elapsed_ms,
        )
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

# Define git functions first so they can be referenced in the TOOLS dict below.
def _git_status(directory: str = ".") -> dict[str, Any]:
    """Return the output of ``git status`` in the given directory."""
    try:
        cwd = _resolve_path(directory)
        out = subprocess.run(
            ["git", "status"], cwd=str(cwd), capture_output=True, text=True
        )
        return _result(out.returncode == 0, directory=str(cwd), output=out.stdout + out.stderr)
    except Exception as exc:
        return _result(False, error=str(exc))


def _git_log(directory: str = ".", limit: int = 10) -> dict[str, Any]:
    """Return the last ``limit`` commits as a list of (hash, message, author)."""
    try:
        cwd = _resolve_path(directory)
        proc = subprocess.run(
            ["git", "log", f"--format=%H|%s|%an", f"-{limit}"],
            cwd=str(cwd), capture_output=True, text=True,
        )
        commits = []
        for line in proc.stdout.strip().splitlines():
            parts = line.split("|", 2)
            if len(parts) == 3:
                commits.append({"hash": parts[0][:7], "message": parts[1], "author": parts[2]})
        return _result(True, directory=str(cwd), commits=commits, count=len(commits))
    except Exception as exc:
        return _result(False, error=str(exc))


def _git_branch(directory: str = ".") -> dict[str, Any]:
    """List all local and remote branches."""
    try:
        cwd = _resolve_path(directory)
        proc = subprocess.run(
            ["git", "branch", "-a"], cwd=str(cwd), capture_output=True, text=True
        )
        branches = [b.strip() for b in proc.stdout.strip().splitlines() if b.strip()]
        current = next((b.lstrip("* ").strip() for b in branches if b.startswith("* ")), "")
        return _result(True, directory=str(cwd), branches=branches, current=current, count=len(branches))
    except Exception as exc:
        return _result(False, error=str(exc))


def _git_diff(directory: str = ".", target: str = "") -> dict[str, Any]:
    """Show unstaged changes (``git diff``). Pass a commit hash to compare against it."""
    try:
        cwd = _resolve_path(directory)
        args = ["git", "diff"] + ([target] if target else [])
        proc = subprocess.run(args, cwd=str(cwd), capture_output=True, text=True)
        return _result(
            proc.returncode in (0, 1),
            directory=str(cwd),
            output=_truncate(proc.stdout + proc.stderr, 5000),
            has_changes=bool(proc.stdout.strip()),
        )
    except Exception as exc:
        return _result(False, error=str(exc))


# Public aliases with underscore prefix to avoid collision with git commands.
def git_status(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return _git_status(*args, **kwargs)


def git_log(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return _git_log(*args, **kwargs)


def git_branch(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return _git_branch(*args, **kwargs)


def git_diff(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return _git_diff(*args, **kwargs)


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
        # Skills management
        list_skills,
        list_available_skills,
        install_skill,
        uninstall_skill,
        # HTTP / networking
        read_url,
        get_public_ip,
        ping_host,
        dns_lookup,
        # File system helpers
        find_files,
        file_size,
        count_tokens,
        hash_file,
        hash_text,
        base64_encode,
        base64_decode,
        json_validate,
        grep_file,
        line_count,
        extract_lines,
        url_encode,
        url_decode,
        compare_files,
        make_directory,
        copy_file,
        # System monitoring
        get_cpu_usage,
        get_memory_usage,
        get_disk_usage,
        get_network_interfaces,
        get_battery_status,
        get_boot_time,
        # Bookmarks
        save_bookmark,
        list_bookmarks,
        open_bookmark,
        delete_bookmark,
        # Git utilities
        git_status,
        git_log,
        git_branch,
        git_diff,
    ]
}


def short_description(fn: Any) -> str:
    """First non-empty line of the docstring, lightly cleaned."""
    doc = textwrap.dedent(fn.__doc__ or "").strip()
    return doc.split("\n\n", 1)[0].replace("\n", " ")
