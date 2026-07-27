"""Transactional email. Gmail SMTP, one sending account.

Load-bearing rather than optional (`OPERATIONS_PLAN.md` §6, G3): a verification link that never
arrives is an account that can never be used, and the user has no way to tell whether the fault is
theirs.

**Gmail with an app password, not OAuth.** One sending account needs no per-user consent, and the
OAuth refresh dance buys nothing at this scale. The tradeoff is recorded rather than hidden: Gmail
caps a normal account at roughly 500 recipients a day, which is ample for an invite-only cohort and
must be revisited before self-serve signup — the failure mode is silent rejection, not an error at
send time.
"""
from libs.mail.sender import (
    ConsoleSender,
    EmailSender,
    MailError,
    SmtpSender,
    sender_from_env,
)
from libs.mail.templates import reset_email, verification_email

__all__ = [
    "ConsoleSender",
    "EmailSender",
    "MailError",
    "SmtpSender",
    "reset_email",
    "sender_from_env",
    "verification_email",
]
