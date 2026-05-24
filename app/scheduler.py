"""In-process recurring scheduler with cron-like syntax.

Jobs are persisted to ``MLA_SCHEDULER_FILE`` (default
``~/.mistral_assistant_jobs.json``) and reloaded on server start.

The scheduler runs as a background asyncio task. Each job is a shell
command (or a chat prompt to the assistant) executed on its schedule.

Supported when-spec syntax (we keep it small + obvious):

* ``every 30s`` / ``every 5m`` / ``every 2h``
* ``daily 09:30``  → every day at 09:30 local time
* ``hourly :15``   → every hour at minute 15
* ``weekly mon 18:00`` → every Monday at 18:00 (mon|tue|wed|thu|fri|sat|sun)
* ``cron <min> <hour> <day> <month> <weekday>`` → classic 5-field cron

Each job entry::

    {
      "id":         "uuid",
      "name":       "human label",
      "when":       "daily 09:30",
      "kind":       "shell"   |  "chat",
      "command":    "echo hi"      # for shell
      "prompt":     "summarize…",  # for chat
      "enabled":    true,
      "created_at": 1716394800.0,
      "last_run":   1716481200.0,
      "next_run":   1716567600.0,
      "history":    [{"ts":..., "ok":..., "output":"…"}],
    }
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import subprocess
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .config import settings

log = logging.getLogger(__name__)


_WEEKDAYS = {
    "mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6,
}


# ── when-spec parsing ───────────────────────────────────────────────────


def _parse_hhmm(text: str) -> tuple[int, int]:
    m = re.match(r"^(\d{1,2}):(\d{2})$", text.strip())
    if not m:
        raise ValueError(f"expected HH:MM, got {text!r}")
    h, mi = int(m.group(1)), int(m.group(2))
    if not (0 <= h <= 23 and 0 <= mi <= 59):
        raise ValueError(f"out of range: {text!r}")
    return h, mi


def _next_after(now: datetime, when: str) -> datetime:
    """Return the next datetime > now matching the when-spec."""
    text = when.strip().lower()

    # every <n><unit>
    m = re.match(r"^every\s+(\d+)\s*(s|m|h|sec|min|hour|seconds?|minutes?|hours?)$", text)
    if m:
        n = int(m.group(1))
        unit = m.group(2)[0]
        seconds = {"s": n, "m": n * 60, "h": n * 3600}[unit]
        return now + timedelta(seconds=seconds)

    # hourly :MM
    m = re.match(r"^hourly\s+:(\d{1,2})$", text)
    if m:
        target_min = int(m.group(1))
        nxt = now.replace(minute=target_min, second=0, microsecond=0)
        if nxt <= now:
            nxt += timedelta(hours=1)
        return nxt

    # daily HH:MM
    m = re.match(r"^daily\s+(\d{1,2}:\d{2})$", text)
    if m:
        h, mi = _parse_hhmm(m.group(1))
        nxt = now.replace(hour=h, minute=mi, second=0, microsecond=0)
        if nxt <= now:
            nxt += timedelta(days=1)
        return nxt

    # weekly <day> HH:MM
    m = re.match(r"^weekly\s+(mon|tue|wed|thu|fri|sat|sun)\s+(\d{1,2}:\d{2})$", text)
    if m:
        target_wd = _WEEKDAYS[m.group(1)]
        h, mi = _parse_hhmm(m.group(2))
        nxt = now.replace(hour=h, minute=mi, second=0, microsecond=0)
        days_ahead = (target_wd - now.weekday()) % 7
        if days_ahead == 0 and nxt <= now:
            days_ahead = 7
        return nxt + timedelta(days=days_ahead)

    # cron min hour day month weekday
    m = re.match(r"^cron\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)$", text)
    if m:
        return _next_cron(now, *m.groups())

    raise ValueError(f"unrecognised schedule: {when!r}")


def _cron_match(field: str, value: int, vmin: int, vmax: int) -> bool:
    if field == "*":
        return True
    # */N
    if field.startswith("*/"):
        step = int(field[2:])
        return (value - vmin) % step == 0
    # comma-separated values / ranges
    for chunk in field.split(","):
        if "-" in chunk:
            a, b = chunk.split("-")
            if int(a) <= value <= int(b):
                return True
        elif chunk.isdigit() and int(chunk) == value:
            return True
    return False


def _next_cron(now: datetime, mn: str, hr: str, dom: str, mo: str, dow: str) -> datetime:
    """Brute-force next match within the coming 366 days. Good enough."""
    candidate = now.replace(second=0, microsecond=0) + timedelta(minutes=1)
    for _ in range(366 * 24 * 60):
        if (
            _cron_match(mn, candidate.minute, 0, 59)
            and _cron_match(hr, candidate.hour, 0, 23)
            and _cron_match(dom, candidate.day, 1, 31)
            and _cron_match(mo, candidate.month, 1, 12)
            and _cron_match(dow, candidate.weekday(), 0, 6)
        ):
            return candidate
        candidate += timedelta(minutes=1)
    raise ValueError("no cron match in the next year")


# ── store ───────────────────────────────────────────────────────────────


class Scheduler:
    """Async loop that fires due jobs."""

    def __init__(self) -> None:
        self.jobs: dict[str, dict[str, Any]] = {}
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._chat_executor = None  # set by main.py after agent is wired

    # storage ----------------------------------------------------------

    @property
    def path(self) -> Path:
        return settings.scheduler_file

    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.jobs = {j["id"]: j for j in data.get("jobs", [])}
            log.info("loaded %d scheduled job(s)", len(self.jobs))
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("failed to load scheduler state: %s", exc)

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps({"jobs": list(self.jobs.values())}, indent=2, default=str),
                encoding="utf-8",
            )
        except OSError as exc:
            log.warning("failed to save scheduler state: %s", exc)

    # CRUD --------------------------------------------------------------

    def add(
        self,
        name: str,
        when: str,
        kind: str = "shell",
        command: str = "",
        prompt: str = "",
    ) -> dict[str, Any]:
        if kind not in {"shell", "chat"}:
            raise ValueError("kind must be 'shell' or 'chat'")
        if kind == "shell" and not command:
            raise ValueError("shell jobs require a command")
        if kind == "chat" and not prompt:
            raise ValueError("chat jobs require a prompt")
        nxt = _next_after(datetime.now(), when)
        job = {
            "id": str(uuid.uuid4())[:8],
            "name": name or when,
            "when": when,
            "kind": kind,
            "command": command,
            "prompt": prompt,
            "enabled": True,
            "created_at": time.time(),
            "last_run": None,
            "next_run": nxt.timestamp(),
            "history": [],
        }
        self.jobs[job["id"]] = job
        self.save()
        return job

    def remove(self, job_id: str) -> bool:
        if job_id in self.jobs:
            del self.jobs[job_id]
            self.save()
            return True
        return False

    def toggle(self, job_id: str, enabled: bool) -> bool:
        if job_id in self.jobs:
            self.jobs[job_id]["enabled"] = enabled
            if enabled:
                self.jobs[job_id]["next_run"] = (
                    _next_after(datetime.now(), self.jobs[job_id]["when"]).timestamp()
                )
            self.save()
            return True
        return False

    def list(self) -> list[dict[str, Any]]:
        return sorted(self.jobs.values(), key=lambda j: j["next_run"] or 0)

    # runtime -----------------------------------------------------------

    def set_chat_executor(self, fn) -> None:
        """Provide a callable ``async fn(prompt:str) -> str`` for chat jobs."""
        self._chat_executor = fn

    async def _run_one(self, job: dict[str, Any]) -> None:
        log.info("scheduler firing job %s (%s)", job["id"], job["name"])
        result: dict[str, Any] = {"ts": time.time(), "ok": False, "output": ""}
        try:
            if job["kind"] == "shell":
                proc = await asyncio.to_thread(
                    subprocess.run,
                    job["command"],
                    shell=True,
                    capture_output=True,
                    text=True,
                    timeout=settings.shell_timeout_seconds,
                )
                result["ok"] = proc.returncode == 0
                out = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
                result["output"] = out[-2000:]
                result["returncode"] = proc.returncode
            elif job["kind"] == "chat" and self._chat_executor:
                text = await self._chat_executor(job["prompt"])
                result["ok"] = True
                result["output"] = (text or "")[-2000:]
            else:
                result["output"] = "no executor available"
        except Exception as exc:  # noqa: BLE001
            result["output"] = f"{type(exc).__name__}: {exc}"

        job["last_run"] = result["ts"]
        job["history"] = (job.get("history") or [])[-9:] + [result]
        try:
            job["next_run"] = _next_after(datetime.now(), job["when"]).timestamp()
        except ValueError:
            job["enabled"] = False
        self.save()

    async def _loop(self) -> None:
        log.info("scheduler started (interval=15s)")
        while not self._stop.is_set():
            now_ts = time.time()
            due = [
                j for j in list(self.jobs.values())
                if j.get("enabled") and (j.get("next_run") or 0) <= now_ts
            ]
            for job in due:
                # advance next_run first so we don't double-fire
                try:
                    job["next_run"] = _next_after(
                        datetime.now() + timedelta(seconds=1), job["when"]
                    ).timestamp()
                except ValueError:
                    job["enabled"] = False
                self.save()
                asyncio.create_task(self._run_one(job))
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=15)
            except asyncio.TimeoutError:
                pass

    def start(self) -> None:
        if self._task and not self._task.done():
            return
        self.load()
        self._stop.clear()
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=2)
            except asyncio.TimeoutError:
                self._task.cancel()


SCHEDULER = Scheduler()
