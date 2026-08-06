"""Message bodies."""
from __future__ import annotations

VERIFY_SUBJECT = "Confirm your email"
RESET_SUBJECT = "Reset your password"

# Both templates show the URL in full rather than hiding it behind a label.
_VERIFY = """\
Hi,

Confirm this address to finish setting up your job-search account:

  {url}

The link works once and expires in 24 hours.

If you did not sign up, ignore this — nothing was created that you need to undo.
"""

_RESET = """\
Hi,

Someone asked to reset the password for this account:

  {url}

The link works once and expires in one hour.

If that was not you, ignore this. Your password has not changed, and nobody can change it without
this link.
"""


def verification_email(url: str) -> tuple[str, str]:
    return VERIFY_SUBJECT, _VERIFY.format(url=url)


def reset_email(url: str) -> tuple[str, str]:
    return RESET_SUBJECT, _RESET.format(url=url)
