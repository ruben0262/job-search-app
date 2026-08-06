"""Telegram's bad minutes, and which of them are worth spending a job's attempts on."""
from __future__ import annotations

import pytest

pytest.importorskip("httpx")


def _stub_telegram(monkeypatch, responses: list[int]) -> list[str]:
    """Answer each `send` call with the next status code, and record how many were made."""
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
    """The message is what an operator reads at 3am, and it has to name the right suspect."""
    from services.notifier.__main__ import SERVER_ERROR_RETRIES, send

    calls = _stub_telegram(monkeypatch, [502])
    with pytest.raises(RuntimeError, match="server error"):
        send("token", "chat", "hello")
    assert len(calls) == SERVER_ERROR_RETRIES


def test_a_bad_request_is_not_retried(monkeypatch):
    """400 is our payload being wrong and 403 is a revoked bot."""
    from services.notifier.__main__ import send

    calls = _stub_telegram(monkeypatch, [400])
    with pytest.raises(RuntimeError):
        send("token", "chat", "hello")
    assert len(calls) == 1


def test_rate_limits_and_outages_do_not_share_one_budget(monkeypatch):
    """Two conditions with two right answers, so two counters."""
    from services.notifier.__main__ import RATE_LIMIT_RETRIES, SERVER_ERROR_RETRIES, send

    assert RATE_LIMIT_RETRIES >= 2 and SERVER_ERROR_RETRIES >= 2
    calls = _stub_telegram(monkeypatch, [429, 429, 502, 502, 200])
    send("token", "chat", "hello")
    assert len(calls) == 5


def test_a_flood_wait_still_obeys_telegrams_own_number(monkeypatch):
    """Unchanged behaviour, guarded because the retry loop was restructured around it."""
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
