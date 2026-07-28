"""Sending one message. One implementation for production, one for everything else."""
from __future__ import annotations

import logging
import os
import smtplib
import ssl
from email.message import EmailMessage
from typing import Protocol

logger = logging.getLogger(__name__)

SMTP_TIMEOUT = 20.0


class MailError(RuntimeError):
    """Delivery failed. Never fatal to the flow that triggered it — see the callers."""


class EmailSender(Protocol):
    def send(self, to: str, subject: str, body: str) -> None: ...


class SmtpSender:
    """SMTP over STARTTLS, authenticated with a username and password.

    **Provider-agnostic on purpose** — host, port, user and sender all come from the environment.
    The platform sends as `no-reply@flow-solutions.xyz` through Zoho, matching the other
    FlowSolutions services; nothing here knows or cares about that, which is what made switching
    from the originally-planned Gmail a configuration change and not a code change.

    The password comes from the environment and is never logged: `libs.secrets`' redaction filter
    covers the process, and `smtplib` puts credentials in its debug output, so debug is left off.
    """

    def __init__(self, host: str, port: int, user: str, password: str, sender: str):
        self._host, self._port = host, port
        self._user, self._password = user, password
        self._from = sender

    def send(self, to: str, subject: str, body: str) -> None:
        message = EmailMessage()
        message["From"] = self._from
        message["To"] = to
        message["Subject"] = subject
        message.set_content(body)
        try:
            with smtplib.SMTP(self._host, self._port, timeout=SMTP_TIMEOUT) as smtp:
                smtp.starttls(context=ssl.create_default_context())
                smtp.login(self._user, self._password)
                smtp.send_message(message)
        except Exception as exc:
            # The address is logged; the subject and body are not. A verification body contains a
            # token that grants access to the account.
            raise MailError(f"could not send to {to}: {type(exc).__name__}") from exc


class ConsoleSender:
    """Prints instead of sending. For development and tests — never wired in production.

    Deliberately keeps the message: a test that asserts a link was generated needs to read it, and
    a developer running the signup flow locally needs to click it.
    """

    def __init__(self) -> None:
        self.sent: list[tuple[str, str, str]] = []

    def send(self, to: str, subject: str, body: str) -> None:
        self.sent.append((to, subject, body))
        logger.info("email (not sent) to %s: %s", to, subject)


def sender_from_env(env: dict[str, str] | None = None) -> EmailSender:
    """`SmtpSender` when SMTP is configured, `ConsoleSender` otherwise.

    Falling back rather than raising is deliberate: a developer with no mail credentials should be
    able to run the whole signup flow, and the console sender makes the link visible. Production
    sets the variables, and `OPERATIONS_PLAN.md` §4's checks are what catch a box that does not.
    """
    env = env if env is not None else dict(os.environ)
    host, user, password = env.get("SMTP_HOST"), env.get("SMTP_USER"), env.get("SMTP_PASSWORD")
    if not (host and user and password):
        logger.warning("SMTP is not configured — email will be printed, not sent")
        return ConsoleSender()
    return SmtpSender(host=host, port=int(env.get("SMTP_PORT", "587")), user=user,
                      password=password, sender=env.get("MAIL_FROM", user))
