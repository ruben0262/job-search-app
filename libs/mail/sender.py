"""Sending one message. One implementation for production, one for everything else."""
from __future__ import annotations

import logging
import os
import secrets
import smtplib
import ssl
import string
from datetime import UTC, datetime
from email.message import EmailMessage
from email.utils import format_datetime, parseaddr
from typing import Protocol
from urllib.parse import urlparse

from libs.notify import site_url

logger = logging.getLogger(__name__)

SMTP_TIMEOUT = 20.0

# What an email's own id is made of before its "@": letters and digits, 24 of them. iCloud is reported to drop
# mail it has accepted, without a word and not into Junk, over an id it dislikes: 40 characters or more before
# the "@", or hex digits alone (2026-11-22, when people on iPhones found no confirmation email).
_ID_LETTERS = string.ascii_letters + string.digits
_ID_LENGTH = 24


class MailError(RuntimeError):
    """Delivery failed. Never fatal to the flow that triggered it — see the callers."""


class EmailSender(Protocol):
    def send(self, to: str, subject: str, body: str, *, reply_to: str | None = None,
             html: str | None = None) -> None: ...


class SmtpSender:
    """SMTP over STARTTLS, authenticated with a username and password."""

    def __init__(self, host: str, port: int, user: str, password: str, sender: str):
        self._host, self._port = host, port
        self._user, self._password = user, password
        self._from = sender

    def send(self, to: str, subject: str, body: str, *, reply_to: str | None = None,
             html: str | None = None) -> None:
        message = EmailMessage()
        message["From"] = self._from
        message["To"] = to
        message["Subject"] = subject
        # Neither was set, and nothing here can tell whether the mail server adds them: the standard asks for
        # a date (RFC 5322 §3.6), and the id is on the domain the email is from.
        message["Date"] = format_datetime(datetime.now(UTC))
        domain = parseaddr(self._from)[1].rpartition("@")[2] or self._host
        own = "".join(secrets.choice(_ID_LETTERS) for _ in range(_ID_LENGTH))
        message["Message-ID"] = f"<{own}@{domain}>"
        if reply_to:
            message["Reply-To"] = reply_to
        message.set_content(body)
        if html:
            # The text first and the HTML as its alternative: a client shows the last one it can.
            message.add_alternative(html, subtype="html")
        try:
            with smtplib.SMTP(self._host, self._port, timeout=SMTP_TIMEOUT) as smtp:
                smtp.starttls(context=ssl.create_default_context())
                smtp.login(self._user, self._password)
                smtp.send_message(message)
        except Exception as exc:
            # The address is logged; the subject and body are not.
            raise MailError(f"could not send to {to}: {type(exc).__name__}") from exc


# Where the site is somebody's own computer, and an email printed in its terminal is how they read it.
LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")


class ConsoleSender:
    """Prints instead of sending, on somebody's own computer. Anywhere else it says the email was not sent
    and prints none of it: a link that signs somebody in, or sets their password, was written to the
    server's log whenever SMTP was left out of its settings (2026-11-20)."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str, str]] = []

    def send(self, to: str, subject: str, body: str, *, reply_to: str | None = None,
             html: str | None = None) -> None:
        self.sent.append((to, subject, body))
        if urlparse(site_url()).hostname not in LOCAL_HOSTS:
            logger.error("email to %s was not sent, and SMTP is not configured: %s", to, subject)
            return
        logger.info("email (not sent) to %s: %s", to, subject)
        print(f"--- email to {to}: {subject} ---\n{body}\n---", flush=True)


def sender_from_env(env: dict[str, str] | None = None) -> EmailSender:
    """`SmtpSender` when SMTP is configured, `ConsoleSender` otherwise."""
    env = env if env is not None else dict(os.environ)
    host, user, password = env.get("SMTP_HOST"), env.get("SMTP_USER"), env.get("SMTP_PASSWORD")
    if not (host and user and password):
        logger.warning("SMTP is not configured — email will be printed, not sent")
        return ConsoleSender()
    return SmtpSender(host=host, port=int(env.get("SMTP_PORT", "587")), user=user,
                      password=password, sender=env.get("MAIL_FROM", user))
