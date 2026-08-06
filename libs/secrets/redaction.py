"""Keeping plaintext credentials out of logs, tracebacks, and error messages."""
from __future__ import annotations

import logging
import re
import threading
import traceback
from collections.abc import Iterator
from contextlib import contextmanager

MASK = "[REDACTED]"

# Below this length a value is too generic to redact without corrupting unrelated log lines.
MIN_REDACTABLE = 8

# Shapes recognisable without knowing the value.
_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}"),            # Claude API key
    re.compile(r"\b\d{8,10}:[A-Za-z0-9_\-]{35}\b"),      # Telegram bot token
    re.compile(r"(?<=://)[^/\s:@]+:[^/\s@]+(?=@)"),      # userinfo in a proxy URL
)


class Secret:
    """A revealed credential."""

    __slots__ = ("_value", "kind")

    def __init__(self, value: str, *, kind: str = "secret"):
        self._value = value
        self.kind = kind

    def reveal(self) -> str:
        """The plaintext. Every call site should be one line long and obviously a use, not a log."""
        return self._value

    @property
    def last_4(self) -> str:
        """What the UI may show. Enough to recognise a key, not enough to be one."""
        return self._value[-4:] if len(self._value) >= 4 else ""

    def __str__(self) -> str:
        return f"<secret:{self.kind} …{self.last_4}>"

    # `repr` matters more than `str`: it is what a traceback, a `%r` and an assertion message use.
    __repr__ = __str__

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Secret) and other._value == self._value

    def __hash__(self) -> int:  # so a Secret can key a dict without exposing the value
        return hash((self.kind, self._value))

    def __bool__(self) -> bool:
        return bool(self._value)


# ── the live-value registry ───────────────────────────────────────────────────────────────────

_lock = threading.RLock()
_live: dict[str, int] = {}   # value -> reference count


def register(value: str) -> None:
    """Mark a plaintext as live, so the filter scrubs it from anything logged while it is in use."""
    if not value or len(value) < MIN_REDACTABLE:
        return
    with _lock:
        _live[value] = _live.get(value, 0) + 1


def unregister(value: str) -> None:
    """Drop one reference. Refcounted because the same credential may be in use twice at once."""
    if not value or len(value) < MIN_REDACTABLE:
        return
    with _lock:
        count = _live.get(value)
        if count is None:
            return
        if count <= 1:
            del _live[value]
        else:
            _live[value] = count - 1


@contextmanager
def redacting(*values: str) -> Iterator[None]:
    """Scrub these values from logs for the duration of the block."""
    for value in values:
        register(value)
    try:
        yield
    finally:
        for value in values:
            unregister(value)


def redact(text: str) -> str:
    """Mask every known live value and every recognised credential shape in `text`."""
    if not text:
        return text
    with _lock:
        live = sorted(_live, key=len, reverse=True)   # longest first, so a prefix cannot shadow
    for value in live:
        if value in text:
            text = text.replace(value, MASK)
    for pattern in _PATTERNS:
        text = pattern.sub(MASK, text)
    return text


class SecretRedactingFilter(logging.Filter):
    """Scrub credentials out of a record before any handler formats it."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact(record.msg)

        if record.args:
            if isinstance(record.args, dict):
                record.args = {k: _redact_arg(v) for k, v in record.args.items()}
            else:
                record.args = tuple(_redact_arg(a) for a in record.args)

        if record.exc_info and not record.exc_text:
            record.exc_text = redact("".join(traceback.format_exception(*record.exc_info)))
        elif record.exc_text:
            record.exc_text = redact(record.exc_text)

        if record.stack_info:
            record.stack_info = redact(record.stack_info)
        return True


def _redact_arg(value):
    """Redact a `%`-style argument without changing its type where that would break formatting."""
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, (int, float, bool, type(None), Secret)):
        return value
    rendered = str(value)
    scrubbed = redact(rendered)
    return scrubbed if scrubbed != rendered else value


def _attach(target: logging.Logger | logging.Handler, installed: SecretRedactingFilter) -> None:
    if not any(isinstance(f, SecretRedactingFilter) for f in target.filters):
        target.addFilter(installed)


def install(logger: logging.Logger | None = None) -> SecretRedactingFilter:
    """Attach the filter to a logger and to each of its handlers."""
    target = logger or logging.getLogger()
    installed = next(
        (f for f in target.filters if isinstance(f, SecretRedactingFilter)),
        SecretRedactingFilter())
    _attach(target, installed)
    for handler in target.handlers:
        _attach(handler, installed)
    return installed


def install_on_handler(handler: logging.Handler) -> SecretRedactingFilter:
    """Cover a handler attached after `install()` ran — a file sink, an error reporter, a test."""
    installed = next(
        (f for f in handler.filters if isinstance(f, SecretRedactingFilter)),
        SecretRedactingFilter())
    _attach(handler, installed)
    return installed
