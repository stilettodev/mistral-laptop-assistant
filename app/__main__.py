"""Allow ``python -m app`` to launch the server."""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
