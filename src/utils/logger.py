"""
src/utils/logger.py — Structured logging with Rich console output.
"""
import logging
import sys
from pathlib import Path
import structlog
from rich.console import Console
from rich.logging import RichHandler

_ROOT = Path(__file__).resolve().parents[2]
_LOG_DIR = _ROOT / "logs"
_LOG_DIR.mkdir(exist_ok=True)

_console = Console(stderr=True)


def setup_logging(level: str = "INFO") -> None:
    """Configure structlog + Rich for pretty terminal output."""
    log_level = getattr(logging, level.upper(), logging.INFO)

    # File handler (plain JSON)
    file_handler = logging.FileHandler(_LOG_DIR / "keepalive.log")
    file_handler.setLevel(log_level)

    # Rich terminal handler
    rich_handler = RichHandler(
        console=_console,
        rich_tracebacks=True,
        markup=True,
        show_time=True,
    )
    rich_handler.setLevel(log_level)

    logging.basicConfig(
        level=log_level,
        format="%(message)s",
        handlers=[rich_handler, file_handler],
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.StackInfoRenderer(),
            structlog.dev.set_exc_info,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
    )


def get_logger(name: str = "keepalive"):
    """Return a named structlog logger."""
    return structlog.get_logger(name)
