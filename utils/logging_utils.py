"""
Logging setup for the NBA Prop AI platform.

Call `setup_logging()` once at application start.  All other modules
obtain loggers via the standard `logging.getLogger(__name__)` pattern.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path


def setup_logging(
    level: str = "INFO",
    log_to_file: bool = False,
    log_file: str = "nba_prop_ai.log",
) -> None:
    """
    Configure root logger with console (and optionally file) handlers.

    Args:
        level: Logging level string ('DEBUG', 'INFO', 'WARNING', 'ERROR').
        log_to_file: If True, also write logs to *log_file*.
        log_file: Path to the log file.
    """
    numeric_level = getattr(logging, level.upper(), logging.INFO)

    fmt = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"
    formatter = logging.Formatter(fmt, datefmt=datefmt)

    root = logging.getLogger()
    root.setLevel(numeric_level)

    # Remove existing handlers to avoid duplicates on repeated calls
    root.handlers.clear()

    # Console handler
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(numeric_level)
    console.setFormatter(formatter)
    root.addHandler(console)

    # Optional file handler
    if log_to_file:
        fh = logging.FileHandler(log_file, mode="a", encoding="utf-8")
        fh.setLevel(numeric_level)
        fh.setFormatter(formatter)
        root.addHandler(fh)

    # Silence noisy third-party libraries at WARNING unless DEBUG requested
    if numeric_level > logging.DEBUG:
        for lib in ("urllib3", "requests", "httpcore", "httpx"):
            logging.getLogger(lib).setLevel(logging.WARNING)
