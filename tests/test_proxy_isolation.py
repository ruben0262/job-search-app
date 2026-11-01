"""T13 — a proxy credential is used only for its owner's fetches, and only where it is needed."""
from __future__ import annotations

import uuid

import httpx
import pytest

from services.harvester.fetch import (
    PROXIED_HOSTS,
    ProxyLease,
    is_proxied,
    make_client,
    make_shared_client,
)

ALICE_PROXY = "http://alice:secret@proxy.example:8080"
BOB_PROXY = "http://bob:secret@proxy.example:8080"


def _lease(proxy: str = ALICE_PROXY, mb: float = 100.0, user_id=None) -> ProxyLease:
    return ProxyLease(user_id=user_id or uuid.uuid4(), proxy_url=proxy)


def _transport_for(client: httpx.Client, url: str) -> httpx.BaseTransport:
    """The transport httpx would actually use for this URL — the routing decision itself."""
    return client._transport_for_url(httpx.URL(url))


# ── whose credential is this? ─────────────────────────────────────────────────────────────────

def test_a_client_knows_which_user_it_may_fetch_for():
    """The property that turns "we only use a credential for its owner" into an assertion."""
    alice = uuid.uuid4()
    with make_client(_lease(user_id=alice)) as client:
        assert client.owner == alice


def test_the_shared_client_cannot_hold_a_credential_at_all():
    """Structural, not conventional: `make_shared_client` takes no lease parameter."""
    with make_shared_client() as client:
        assert client.owner is None
        assert client.lease is None

    with pytest.raises(TypeError):
        make_shared_client(_lease())            # type: ignore[misc]


def test_two_users_clients_carry_different_credentials():
    """Credentials are never pooled or rotated across users (D3)."""
    alice, bob = uuid.uuid4(), uuid.uuid4()
    with make_client(_lease(ALICE_PROXY, user_id=alice)) as a, \
            make_client(_lease(BOB_PROXY, user_id=bob)) as b:
        assert a.owner == alice
        assert b.owner == bob
        assert a.lease.proxy_url != b.lease.proxy_url
        assert _transport_for(a, "https://www.linkedin.com/jobs/view/1") is not \
            _transport_for(b, "https://www.linkedin.com/jobs/view/1")


# ── what actually goes through the proxy ──────────────────────────────────────────────────────

def test_only_linkedin_is_proxied():
    """Residential bandwidth is metered per GB."""
    with make_client(_lease()) as client:
        linkedin = _transport_for(client, "https://www.linkedin.com/jobs/view/123")
        for direct_url in ("https://boards.greenhouse.io/acme/jobs/1",
                           "https://jobs.ashbyhq.com/acme/x",
                           "https://api.lever.co/v0/postings/acme",
                           "https://remotive.com/api/remote-jobs"):
            assert _transport_for(client, direct_url) is not linkedin, direct_url


def test_linkedin_subdomains_are_proxied_too():
    with make_client(_lease()) as client:
        base = _transport_for(client, "https://linkedin.com/x")
        assert _transport_for(client, "https://www.linkedin.com/x") is base
        assert _transport_for(client, "https://uk.linkedin.com/x") is base


def test_a_redirect_out_of_linkedin_is_not_proxied():
    """Per-host mounts resolve per request URL, so a redirect to an ATS board goes direct."""
    with make_client(_lease()) as client:
        proxied = _transport_for(client, "https://www.linkedin.com/jobs/view/1")
        assert _transport_for(client, "https://boards.greenhouse.io/acme/jobs/1") is not proxied


def test_is_proxied_agrees_with_the_routing_it_describes():
    """A helper that disagreed with the mounts would make cost estimates confidently wrong."""
    with make_client(_lease()) as client:
        for url in ("https://www.linkedin.com/jobs/view/1", "https://linkedin.com/x",
                    "https://boards.greenhouse.io/a", "https://example.test/x"):
            routed_via_proxy = _transport_for(client, url) is _transport_for(
                client, f"https://{PROXIED_HOSTS[0]}/probe")
            assert is_proxied(url) == routed_via_proxy, url


def test_ambient_proxy_environment_variables_are_ignored(monkeypatch):
    """A stray HTTP_PROXY must not silently pool everyone's traffic through one endpoint."""
    monkeypatch.setenv("HTTP_PROXY", "http://ambient.example:3128")
    monkeypatch.setenv("HTTPS_PROXY", "http://ambient.example:3128")
    monkeypatch.setenv("ALL_PROXY", "http://ambient.example:3128")

    def _has_ambient_proxy(client: httpx.Client, url: str) -> bool:
        transport = _transport_for(client, url)
        pool = getattr(transport, "_pool", None)
        return getattr(pool, "_proxy_url", None) is not None

    # The shared loop is the dangerous one: it has no mounts of its own.
    with make_shared_client() as client:
        assert not _has_ambient_proxy(client, "https://boards.greenhouse.io/acme/jobs/1")

    # And a leased client's direct route must stay direct too.
    with make_client(_lease()) as client:
        assert not _has_ambient_proxy(client, "https://boards.greenhouse.io/acme/jobs/1")


# ── the lease ─────────────────────────────────────────────────────────────────────────────────

def test_a_lease_without_a_proxy_url_authorises_nothing():
    with pytest.raises(ValueError, match="proxy_url"):
        ProxyLease(user_id=uuid.uuid4(), proxy_url="")


# ── metering ──────────────────────────────────────────────────────────────────────────────────

# Metering lives in `TenantClient.send` and so applies to every response regardless of route.
DIRECT_URL = "https://boards.greenhouse.io/acme/jobs/1"


def _metered_client(body: bytes, status: int = 200):
    transport = httpx.MockTransport(
        lambda request: httpx.Response(status, content=body, headers={"x-test": "1"}))
    return make_client(_lease(), transport=transport)


def test_bytes_are_counted_on_every_response():
    body = b"x" * 4096
    with _metered_client(body) as client:
        client.get(DIRECT_URL)
        client.get(DIRECT_URL)
    assert client.bytes_downloaded >= len(body) * 2
    assert client.megabytes_downloaded == pytest.approx(client.bytes_downloaded / (1024 * 1024))


def test_failed_responses_are_metered_too():
    """The proxy bills on the wire, not on success."""
    with _metered_client(b"blocked" * 100, status=403) as client:
        client.get(DIRECT_URL)
    assert client.bytes_downloaded > 0, "a 403 still cost real bandwidth"


def test_headers_are_included_in_the_estimate():
    """Small per request, not nothing across thousands."""
    with _metered_client(b"") as client:
        client.get(DIRECT_URL)
    assert client.bytes_downloaded > 0


def test_a_leased_client_still_meters_and_still_knows_its_owner():
    """Metering and attribution are independent of which transport answered."""
    alice = uuid.uuid4()
    transport = httpx.MockTransport(lambda r: httpx.Response(200, content=b"y" * 1024))
    with make_client(_lease(user_id=alice), transport=transport) as client:
        client.get(DIRECT_URL)
        assert client.owner == alice
        assert client.bytes_downloaded >= 1024
