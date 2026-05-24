"""pytest fixtures shared across all test modules."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def stop_scheduler():
    """Prevent the background scheduler from interfering with async tests."""
    import app.scheduler

    orig_start = app.scheduler.SCHEDULER.start
    orig_stop = app.scheduler.SCHEDULER.stop

    app.scheduler.SCHEDULER.start = lambda: None
    app.scheduler.SCHEDULER.stop = lambda *a, **k: None

    async def noop(*a, **k):
        return None

    app.scheduler.SCHEDULER.stop = noop  # type: ignore[assignment]

    yield

    app.scheduler.SCHEDULER.start = orig_start
    app.scheduler.SCHEDULER.stop = orig_stop


@pytest.fixture
def client():
    from app.main import app

    with TestClient(app, raise_server_exceptions=True) as tc:
        yield tc