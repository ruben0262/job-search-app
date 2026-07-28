"""The logging configuration itself."""
from __future__ import annotations

import logging
import logging.handlers
import os
from pathlib import Path

from libs.secrets import install_on_handler

# `logs/` beside the repo, overridable so a container can mount a volume and a test can use tmp.
DEFAULT_LOG_DIR = Path(os.environ.get("LOG_DIR") or "logs")

# Service and logger name are both present on purpose. The service says which *process* wrote it —
# with four workers on one box that is the first thing you want — and the logger name says which
# module, which is what you grep for once you know where to look.
LOG_FORMAT = "%(asctime)s %(levelname)-7s [%(service)s] %(name)s: %(message)s"

# 10 MB x 5 keeps roughly a week of a busy harvester without anybody thinking about it. Rotation
# rather than truncation, because the interesting part of an incident is usually just before it.
MAX_BYTES = 10 * 1024 * 1024
BACKUP_COUNT = 5


class _ServiceFilter(logging.Filter):
    """Stamps every record with the service name, so one log line identifies its own process."""

    def __init__(self, service: str):
        super().__init__()
        self.service = service

    def filter(self, record: logging.LogRecord) -> bool:
        # `setdefault` semantics: a record that already names a service — one forwarded from
        # somewhere else — keeps its own.
        if not hasattr(record, "service"):
            record.service = self.service
        return True


def log_path(service: str, log_dir: Path | str | None = None) -> Path:
    return Path(log_dir or DEFAULT_LOG_DIR) / f"{service}.log"


def configure(service: str, *, level: str | None = None,
              log_dir: Path | str | None = None,
              console: bool = True) -> Path | None:
    """Configure logging for this process. Idempotent. Returns the log file path, if any.

    Call once at the top of an entry point. Every module then uses a plain
    `logging.getLogger(__name__)` and inherits this — no module should configure anything itself,
    because the last one to run would win and which one that is depends on import order.

    A file that cannot be opened is a warning, not a failure: a read-only volume or a missing
    directory must not stop a harvest. Console logging continues, and the warning says the file is
    missing rather than leaving someone to wonder why `logs/` is empty.
    """
    root = logging.getLogger()
    root.setLevel(level or os.environ.get("LOG_LEVEL", "INFO"))

    existing = {getattr(handler, "_jobsearch_service", None) for handler in root.handlers}
    if service in existing:
        return log_path(service, log_dir)

    formatter = logging.Formatter(LOG_FORMAT)
    stamp = _ServiceFilter(service)

    def _attach(handler: logging.Handler) -> None:
        handler.setFormatter(formatter)
        handler.addFilter(stamp)
        # The redaction filter goes on the handler, never only on the root logger: a record from
        # `logging.getLogger("services.notifier")` reaches the root's *handlers* without consulting
        # the root's filters, so a filter installed there alone would protect almost nothing.
        install_on_handler(handler)
        handler._jobsearch_service = service
        root.addHandler(handler)

    if console:
        _attach(logging.StreamHandler())

    destination = Path(log_dir or DEFAULT_LOG_DIR)
    try:
        destination.mkdir(parents=True, exist_ok=True)
        _attach(logging.handlers.RotatingFileHandler(
            destination / f"{service}.log", maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT,
            encoding="utf-8"))
    except OSError as exc:
        logging.getLogger(__name__).warning(
            "logging to %s is unavailable (%s) — console only, nothing will be kept",
            destination, exc, extra={"service": service})
        return None

    return destination / f"{service}.log"


def audit(logger: logging.Logger, event: str, **fields) -> None:
    """Record something that must never be silent, as one greppable line.

    For the events where "it just did not happen" is indistinguishable from success unless it was
    written down: a credential saved, a model published, a profile changed, a quota refusal, a
    delivery skipped. Rendered as `event key=value key=value` so it can be grepped and cut without a
    parser.

    Values are rendered through the formatter, so the redaction filter sees them — passing a
    credential here scrubs rather than leaks, though nothing should be passing one.
    """
    rendered = " ".join(f"{key}={value}" for key, value in fields.items())
    logger.info("%s %s", event, rendered) if rendered else logger.info("%s", event)
