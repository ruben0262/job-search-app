"""Transactional email."""
from libs.mail.quota import (
    DEFAULT_DAILY_CAP,
    MailCapReached,
    daily_cap,
    send_within_cap,
    sent_last_24h,
)
from libs.mail.sender import (
    ConsoleSender,
    EmailSender,
    MailError,
    SmtpSender,
    sender_from_env,
)
from libs.mail.templates import DigestJob, digest_email, reset_email, verification_email

__all__ = [
    "DEFAULT_DAILY_CAP",
    "ConsoleSender",
    "DigestJob",
    "EmailSender",
    "MailCapReached",
    "MailError",
    "SmtpSender",
    "daily_cap",
    "digest_email",
    "reset_email",
    "send_within_cap",
    "sender_from_env",
    "sent_last_24h",
    "verification_email",
]
