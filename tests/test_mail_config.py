"""Which sender a given environment produces."""
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
    """Half a configuration is worse than none: it would fail per-send, at three in the morning."""
    env = {key: value for key, value in CONFIGURED.items() if key != missing}
    assert isinstance(sender_from_env(env), ConsoleSender)


def test_an_empty_password_is_not_a_configuration():
    """`.env` files carry `SMTP_PASSWORD=` with nothing after it far more often than they carry no line."""
    assert isinstance(sender_from_env({**CONFIGURED, "SMTP_PASSWORD": ""}), ConsoleSender)


def test_the_fallback_says_so_out_loud(caplog):
    """The whole failure was that it was silent."""
    import logging

    with caplog.at_level(logging.WARNING):
        sender_from_env({})
    assert any("SMTP is not configured" in record.message for record in caplog.records)


def test_the_sender_address_defaults_to_the_login_but_can_differ():
    """They are the same for this platform and are not always: a mailbox may send as an alias."""
    assert sender_from_env(CONFIGURED)._from == CONFIGURED["SMTP_USER"]
    assert sender_from_env({**CONFIGURED, "MAIL_FROM": "alias@flow-solutions.xyz"})._from == (
        "alias@flow-solutions.xyz")


# ── the links inside those emails ─────────────────────────────────────────────────────────────

def test_the_api_container_is_given_the_public_url():
    """Every emailed link is built from `PUBLIC_URL`."""
    import pathlib
    import re

    compose = (pathlib.Path(__file__).resolve().parents[1]
               / "deploy" / "compose.yml").read_text(encoding="utf-8")
    api_block = compose.split("\n  api:\n", 1)[1].split("\n  cloudflared:", 1)[0]
    assert re.search(r"^\s+PUBLIC_URL:", api_block, re.MULTILINE), (
        "the api service must pass PUBLIC_URL or every emailed link points at localhost")
    # Required, not defaulted: a silently wrong link is worse than a container that will not start.
    assert re.search(r"^\s+PUBLIC_URL:\s+\$\{PUBLIC_URL:\?", api_block, re.MULTILINE)
