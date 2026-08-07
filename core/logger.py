"""Logging setup for The Value Council.

Wraps :mod:`loguru` with a console sink (colored, level from settings)
and a rotating file sink at ``logs/app.log``. Call :func:`setup_logging`
once at process start; everywhere else, ``from core.logger import get_logger``.
"""

from __future__ import annotations

import sys
from pathlib import Path

from loguru import logger as _logger

_CONFIGURED: bool = False
_FORMAT: str = (
    "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
    "<level>{level: <8}</level> | "
    "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
    "<level>{message}</level>"
)
_FILE_FORMAT: str = (
    "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | "
    "{name}:{function}:{line} | {message}"
)


def setup_logging(level: str = "INFO", log_dir: Path | None = None) -> None:
    """Configure the global loguru logger.

    Idempotent — safe to call multiple times. The first call wins; later
    calls only update the level.

    Args:
        level: One of ``DEBUG``, ``INFO``, ``WARNING``, ``ERROR``, ``CRITICAL``.
        log_dir: Directory for the rotating file sink. Defaults to
            ``<project_root>/logs``.
    """
    global _CONFIGURED

    if log_dir is None:
        log_dir = Path(__file__).resolve().parent.parent / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    if _CONFIGURED:
        # Already set up — just update the level on existing handlers.
        # Loguru does not expose per-handler level updates, so re-init.
        _logger.remove()

    _logger.add(
        sys.stderr,
        level=level,
        format=_FORMAT,
        colorize=True,
        backtrace=True,
        diagnose=False,
    )
    _logger.add(
        log_dir / "app.log",
        level=level,
        format=_FILE_FORMAT,
        rotation="10 MB",
        retention="7 days",
        compression="zip",
        enqueue=True,
        backtrace=True,
        diagnose=False,
    )
    _CONFIGURED = True


def get_logger(name: str) -> _logger.__class__:  # type: ignore[name-defined]
    """Return a logger bound to ``name``.

    Auto-configures on first use with sensible defaults so callers do
    not have to remember to call :func:`setup_logging`.
    """
    if not _CONFIGURED:
        setup_logging()
    return _logger.bind(module=name)


__all__ = ["get_logger", "setup_logging"]
