"""Keeping plaintext credentials out of logs, tracebacks, and error messages.

`TENANT_SAFETY.md` §2 lists "never logged, never in error messages" as a handling rule with a test
attached. A rule like that cannot be met by remembering, because the leak paths are mostly not code
anyone wrote: an `httpx` connection error quoting the proxy URL it dialled, a `repr()` of a config
dict inside a traceback, a third-party library echoing the header it sent.

Two layers, because neither is sufficient alone:

1. **`Secret` — a value that does not stringify.** `str()`, `repr()`, and f-strings all produce
   `<secret:claude_api …ab12>`. This closes the accidental-interpolation path *at the type level*:
   `logger.info("using %s", key)` cannot leak, because the leak would have to be written as
   `key.reveal()`, which is greppable and reviewable.
2. **`SecretRedactingFilter` — scrubbing at the logging boundary.** Covers everything layer 1
   cannot: values that reached a log through code we do not own, and values that were correctly
   revealed for a call and then appeared in that call's exception. The filter matches registered
   live values, plus patterns for credential shapes that are recognisable on sight.

The filter is the backstop, not the design. Anything relying on it alone would be one library
upgrade away from leaking, which is why `Secret` exists.
"""
from __future__ import annotations

import logging
import re
import threading
import traceback
from collections.abc import Iterator
from contextlib import contextmanager

MASK = "[REDACTED]"

# Below this length a value is too generic to redact globally: scrubbing every occurrence of a
# 4-character string would corrupt unrelated log lines, and no real credential is this short.
MIN_REDACTABLE = 8

# Shapes recognisable without knowing the value. These catch a credential that was logged before it
# was ever registered — the case the registry structurally cannot cover.
#
# Deliberately conservative: a pattern that over-matches silently destroys log lines an operator
# needs during an incident, which is its own outage.
_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}"),            # Claude API key
    re.compile(r"\b\d{8,10}:[A-Za-z0-9_\-]{35}\b"),      # Telegram bot token
    re.compile(r"(?<=://)[^/\s:@]+:[^/\s@]+(?=@)"),      # userinfo in a proxy URL
)


class Secret:
    """A revealed credential. Carries its value; refuses to render it.

    Compared with a bare `str`, the difference that matters is what happens by accident. A `str`
    lands in a log the moment anything formats the object graph it sits in. This does not — the
    value leaves only through `reveal()`.
    """

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

    # `repr` matters more than `str` here: it is what a traceback, a `%r`, a pytest assertion
    # message, and a `repr()`d container all use.
    __repr__ = __str__

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Secret) and other._value == self._value

    def __hash__(self) -> int:  # so a Secret can key a dict without exposing the value
        return hash((self.kind, self._value))

    def __bool__(self) -> bool:
        return bool(self._value)


# ── the live-value registry ───────────────────────────────────────────────────────────────────
#
# Process-global on purpose. The filter runs on whichever thread logged, and a per-thread registry
# would miss exactly the case that matters: a credential revealed on a worker thread and logged by
# a library's own thread.

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
    """Scrub these values from logs for the duration of the block.

    The shape `TENANT_SAFETY.md` §2 asks for — "decrypt only inside the worker that needs the
    credential, in memory, for the duration of the call" — with the log protection scoped to the
    same window rather than to process lifetime.
    """
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
    """Scrub credentials out of a record before any handler formats it.

    A `Filter` rather than a `Formatter` because a formatter is per-handler: adding a second handler
    (a file, a syslog socket, an error reporter) would silently bypass it, and the bypass would look
    exactly like it was working.

    Exceptions are the subtle part. A formatter renders `exc_info` *after* filters run, so scrubbing
    `record.msg` alone leaves the credential in the traceback — which is precisely where a failed
    API call puts it. So the traceback is rendered here, redacted, and cached on `record.exc_text`;
    `logging.Formatter` uses that cache instead of re-rendering.
    """

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
    """Redact a `%`-style argument without changing its type where that would break formatting.

    Non-strings are rendered and inspected rather than passed through: an exception, a dict, or a
    URL object can all carry a credential in their `str()`, and that string is what reaches the log.
    A value only becomes a string if it actually contained something worth masking.
    """
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
    """Attach the filter to a logger **and to each of its handlers**. Idempotent.

    The two halves are not redundant, and getting this wrong is the classic way a redaction filter
    ends up protecting nothing:

    - A **logger's** filters run only for records logged on *that logger*. A record from
      `logging.getLogger("services.notifier")` propagates to the root logger's *handlers* without
      ever consulting the root logger's filters. So a filter on the root alone misses every record
      the application actually emits.
    - A **handler's** filters run whenever a record reaches that handler, propagated or not — which
      is the coverage this needs.

    Call once at process start, **after** logging is configured. A handler added later is not
    covered; pass it to `install_on_handler`.
    """
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
