"""The logging configuration itself."""
from __future__ import annotations

import logging
import logging.handlers
import os
from pathlib import Path

from libs.secrets import install_on_handler

# `logs/` beside the repo, overridable so a container can mount a volume and a test can use tmp.
DEFAULT_LOG_DIR = Path(os.environ.get("LOG_DIR") or "logs")

# Service and logger name are both present on purpose.
LOG_FORMAT = "%(asctime)s %(levelname)-7s [%(service)s] %(name)s: %(message)s"

# 10 MB x 5 keeps roughly a week of a busy harvester without anybody thinking about it.
MAX_BYTES = 10 * 1024 * 1024
BACKUP_COUNT = 5


class _ServiceFilter(logging.Filter):
    """Stamps every record with the service name, so one log line identifies its own process."""

    def __init__(self, service: str):
        super().__init__()
        self.service = service

    def filter(self, record: logging.LogRecord) -> bool:
        # `setdefault`: a record that already names a service keeps its own.
        if not hasattr(record, "service"):
            record.service = self.service
        return True


def log_path(service: str, log_dir: Path | str | None = None) -> Path:
    return Path(log_dir or DEFAULT_LOG_DIR) / f"{service}.log"


# Libraries that log one INFO line per network call, raised so they report trouble only.
NOISY_LIBRARIES = ("httpx", "httpx2", "httpcore", "urllib3", "asyncio", "pymongo", "stripe")


def _quieten_libraries() -> None:
    """Raise third-party loggers to WARNING, unless the operator asked for DEBUG."""
    debugging = logging.getLogger().level <= logging.DEBUG
    for name in NOISY_LIBRARIES:
        logging.getLogger(name).setLevel(logging.NOTSET if debugging else logging.WARNING)


def configure(service: str, *, level: str | None = None,
              log_dir: Path | str | None = None,
              console: bool = True) -> Path | None:
    """Configure logging for this process."""
    root = logging.getLogger()
    root.setLevel(level or os.environ.get("LOG_LEVEL", "INFO"))
    _quieten_libraries()

    existing = {getattr(handler, "_jobsearch_service", None) for handler in root.handlers}
    if service in existing:
        return log_path(service, log_dir)

    formatter = logging.Formatter(LOG_FORMAT)
    stamp = _ServiceFilter(service)

    def _attach(handler: logging.Handler) -> None:
        handler.setFormatter(formatter)
        handler.addFilter(stamp)
        # The redaction filter goes on the handler: a record reaches the root's handlers, not its filters.
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
    """Record something that must never be silent, as one greppable line."""
    rendered = " ".join(f"{key}={value}" for key, value in fields.items())
    logger.info("%s %s", event, rendered) if rendered else logger.info("%s", event)
