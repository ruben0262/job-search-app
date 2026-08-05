"""Telegram's bad minutes, and which of them are worth spending a job's attempts on.

**Two of an account's digests were lost permanently to a "Bad Gateway"** on 2026-08-04. Only 429
was retried, so a 502 raised on the first response; the queue then spent all five of its attempts
inside five and a half minutes and dead-lettered the job while Telegram was still unwell. Every
piece was working as written — the retries, the attempt counter, the dead-letter — they were just
sampling an outage at a resolution far finer than the outage (ROADMAP 6ad).

The distinction each test defends is the same one: **is this our request being wrong, or Telegram
being briefly broken?** A 4xx is ours and never improves by asking again. A 5xx is theirs, says
nothing about the payload, and asking again is the only useful response.

No database here on purpose. These are decisions `send` makes from a status code, and a test that
skips itself when Postgres is absent is a test that does not run in CI.
"""
from __future__ import annotations

import pytest

pytest.importorskip("httpx")


def _stub_telegram(monkeypatch, responses: list[int]) -> list[str]:
    """Answer each `send` call with the next status code, and record how many were made.

    The last entry repeats, so `[502]` is an outage that never ends and `[502, 502, 200]` is one
    that recovers on the third try.
    """
    import httpx

    from services.notifier import __main__ as notifier

    calls: list[str] = []

    def post(url, **_kwargs):
        status = responses[min(len(calls), len(responses) - 1)]
        calls.append(url)
        body = {"description": "Bad Gateway"} if status >= 400 else {"ok": True}
        return httpx.Response(status, json=body)

    monkeypatch.setattr(notifier.httpx, "post", post)
    # Real waits would make this suite take minutes to prove something about control flow.
    monkeypatch.setattr(notifier.time, "sleep", lambda _seconds: None)
    return calls


def test_a_telegram_502_is_retried_rather_than_failing_the_job(monkeypatch):
    """The regression itself: a gateway error that clears must not cost the user their digest."""
    from services.notifier.__main__ import send

    calls = _stub_telegram(monkeypatch, [502, 502, 200])
    send("token", "chat", "hello")
    assert len(calls) == 3


def test_a_telegram_outage_that_does_not_end_says_it_was_an_outage(monkeypatch):
    """The message is what an operator reads at 3am, and it has to name the right suspect.

    "Telegram kept rate limiting this bot" is what the failure said during an upstream outage,
    which sends somebody looking at bot limits for a problem that was never theirs.
    """
    from services.notifier.__main__ import SERVER_ERROR_RETRIES, send

    calls = _stub_telegram(monkeypatch, [502])
    with pytest.raises(RuntimeError, match="server error"):
        send("token", "chat", "hello")
    assert len(calls) == SERVER_ERROR_RETRIES


def test_a_bad_request_is_not_retried(monkeypatch):
    """400 is our payload being wrong and 403 is a revoked bot. Neither improves by asking again,
    and retrying them would spend the budget a real 5xx needs."""
    from services.notifier.__main__ import send

    calls = _stub_telegram(monkeypatch, [400])
    with pytest.raises(RuntimeError):
        send("token", "chat", "hello")
    assert len(calls) == 1


def test_rate_limits_and_outages_do_not_share_one_budget(monkeypatch):
    """Two conditions with two right answers, so two counters.

    A shared one lets a burst of flood-waits exhaust the allowance an outage needs — the sequence
    below uses more total retries than either budget permits alone, and is correct precisely
    because neither budget was exceeded.
    """
    from services.notifier.__main__ import RATE_LIMIT_RETRIES, SERVER_ERROR_RETRIES, send

    assert RATE_LIMIT_RETRIES >= 2 and SERVER_ERROR_RETRIES >= 2
    calls = _stub_telegram(monkeypatch, [429, 429, 502, 502, 200])
    send("token", "chat", "hello")
    assert len(calls) == 5


def test_a_flood_wait_still_obeys_telegrams_own_number(monkeypatch):
    """Unchanged behaviour, guarded because the retry loop was restructured around it: the wait
    comes from `parameters.retry_after`, not from a constant somebody guessed."""
    import httpx

    from services.notifier import __main__ as notifier

    waits: list[float] = []
    calls: list[str] = []

    def post(url, **_kwargs):
        calls.append(url)
        if len(calls) == 1:
            return httpx.Response(429, json={"parameters": {"retry_after": 7}})
        return httpx.Response(200, json={"ok": True})

    monkeypatch.setattr(notifier.httpx, "post", post)
    monkeypatch.setattr(notifier.time, "sleep", waits.append)

    notifier.send("token", "chat", "hello")
    assert waits == [7.0]
