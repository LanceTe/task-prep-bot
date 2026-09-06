"""Root logging setup: writes to LOG_DIR/leaf-valley.log and stderr."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

LOG_FILENAME = "leaf-valley.log"
_LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"
_NOISY_LOGGERS = ("discord", "discord.http", "discord.gateway")


def configure_logging(log_dir: Path, level: str = "INFO") -> None:
    """Wire the root logger to LOG_DIR/leaf-valley.log + stderr and quiet discord.py.

    Idempotent: replaces existing root handlers so re-invocation (tests, reload)
    doesn't duplicate output.
    """
    log_dir.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter(_LOG_FORMAT)

    file_handler = logging.FileHandler(log_dir / LOG_FILENAME, encoding="utf-8")
    file_handler.setFormatter(formatter)

    stream_handler = logging.StreamHandler(sys.stderr)
    stream_handler.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(level.upper())
    root.handlers[:] = [file_handler, stream_handler]

    for noisy in _NOISY_LOGGERS:
        logging.getLogger(noisy).setLevel(logging.WARNING)
