"""Logging setup: console + optional file handler, log dir created on demand."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

_FMT = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"


def setup_logging(level: str = "INFO", file: Optional[str] = None) -> None:
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    handlers = [logging.StreamHandler()]
    if file:
        p = Path(file)
        p.parent.mkdir(parents=True, exist_ok=True)
        # Must be UTF-8: the default (cp1252 on Windows) crashes on any
        # non-ASCII character in a message (≈, …) — logging then swallows
        # the error and the line silently disappears from the log.
        handlers.append(logging.FileHandler(p, encoding="utf-8"))
    for h in handlers:
        h.setFormatter(logging.Formatter(_FMT))
        # A redirected console on Windows also defaults to cp1252; make an
        # unencodable character degrade to "?" instead of dropping the line.
        stream = getattr(h, "stream", None)
        if stream is not None and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(errors="replace")
            except Exception:  # pragma: cover - exotic stream types
                pass
    root.handlers = handlers

    # Keep third-party chatter below our signal.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


__all__ = ["setup_logging"]
