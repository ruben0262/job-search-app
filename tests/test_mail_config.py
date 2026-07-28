"""Which sender a given environment produces.

**This is the gap that cost a real outage-shaped hour.** With no `SMTP_*` set, the API resolved to
`ConsoleSender` and printed emails instead of sending them — signup returned success, the
confirmation never arrived, and nothing about it looked broken. The fallback is deliberate, so a
developer can run the flow locally; the failure is that nothing asserted which one you get.
"""
from __future__ import annotations

import pytest

pytest.importorskip("pydantic")

from libs.mail import ConsoleSender, sender_from_env
from libs.mail.sender import SmtpSender

CONFIGURED = {
    "SMTP_HOST": "smtp.zoho.eu",
    "SMTP_USER": "no-reply@flow-solutions.xyz",
    "SMTP_PASSWORD": "not-a-real-password",
}


def test_a_configured_environment_really_sends():
    sender = sender_from_env(CONFIGURED)
    assert isinstance(sender, SmtpSender)


@pytest.mark.parametrize("missing", sorted(CONFIGURED))
def test_any_missing_piece_falls_back_rather_than_half_configuring(missing):
    """Half a configuration is worse than none: it would fail per-send, at three in the morning,
    inside a worker, rather than at startup."""
    env = {key: value for key, value in CONFIGURED.items() if key != missing}
    assert isinstance(sender_from_env(env), ConsoleSender)


def test_an_empty_password_is_not_a_configuration():
    """`.env` files carry `SMTP_PASSWORD=` with nothing after it far more often than they carry no
    line at all — and an empty string is not a credential."""
    assert isinstance(sender_from_env({**CONFIGURED, "SMTP_PASSWORD": ""}), ConsoleSender)


def test_the_fallback_says_so_out_loud(caplog):
    """The whole failure was that it was silent. A warning is what `check_health.py` and a human
    reading the log have to go on."""
    import logging

    with caplog.at_level(logging.WARNING):
        sender_from_env({})
    assert any("SMTP is not configured" in record.message for record in caplog.records)


def test_the_sender_address_defaults_to_the_login_but_can_differ():
    """They are the same for this platform and are not always: a mailbox may send as an alias, and
    silently rewriting the From address would break DKIM alignment."""
    assert sender_from_env(CONFIGURED)._from == CONFIGURED["SMTP_USER"]
    assert sender_from_env({**CONFIGURED, "MAIL_FROM": "alias@flow-solutions.xyz"})._from == (
        "alias@flow-solutions.xyz")
