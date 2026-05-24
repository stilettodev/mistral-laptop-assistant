"""Optional desktop integrations: native window (pywebview) and system tray (pystray).

Both modules are loaded lazily so installing them stays optional —
the headless ``mla serve`` workflow continues to work without them.
"""

from __future__ import annotations

import logging
import threading
import time
import webbrowser
from typing import Callable

log = logging.getLogger(__name__)


# ── system tray ───────────────────────────────────────────────────────


def run_tray(url: str, on_quit: Callable[[], None] | None = None) -> None:
    """Start a system tray icon. Blocks the calling thread."""
    try:
        import pystray
        from PIL import Image, ImageDraw
    except ImportError as exc:
        raise RuntimeError(
            "tray support requires `pystray` and `pillow`. "
            "Install with: uv add pystray pillow"
        ) from exc

    # 64x64 rounded square in Mistral orange with a serif "m"
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((2, 2, 62, 62), radius=14, fill=(255, 122, 60, 255))
    try:
        from PIL import ImageFont

        font = ImageFont.truetype("DejaVuSans-Bold.ttf", 38)
    except (OSError, ImportError):
        font = None
    d.text((20, 6), "m", font=font, fill=(26, 18, 12, 255))

    def _open(_=None):
        webbrowser.open(url)

    def _quit(icon, _=None):
        if on_quit:
            try:
                on_quit()
            except Exception:  # noqa: BLE001
                pass
        icon.stop()

    menu = pystray.Menu(
        pystray.MenuItem("Open chat", _open, default=True),
        pystray.MenuItem(f"URL: {url}", None, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit", _quit),
    )
    icon = pystray.Icon("mla", img, "Mistral Laptop Assistant", menu)
    log.info("tray icon ready")
    icon.run()


# ── pywebview native window ──────────────────────────────────────────


def run_window(url: str, title: str = "Mistral Laptop Assistant") -> None:
    """Open the chat in a real native window. Blocks until closed."""
    try:
        import webview  # pywebview
    except ImportError as exc:
        raise RuntimeError(
            "desktop window support requires `pywebview`. "
            "Install with: uv add pywebview"
        ) from exc
    webview.create_window(title, url, width=1100, height=780, min_size=(720, 540))
    webview.start()


# ── helpers ──────────────────────────────────────────────────────────


def wait_for_server(url: str, timeout: float = 10.0) -> bool:
    """Poll a URL until it responds 2xx (used before opening the window/tray)."""
    import urllib.request

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1) as r:
                if 200 <= r.status < 300:
                    return True
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.2)
    return False


def in_background(target: Callable[..., None], *args, **kwargs) -> threading.Thread:
    t = threading.Thread(target=target, args=args, kwargs=kwargs, daemon=True)
    t.start()
    return t
