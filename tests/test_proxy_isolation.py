"""The site proxy carries LinkedIn and nothing else, a new connection for every request; the boards'
client carries no proxy at all, not even one from the box's environment.

There is no per-person client to test any more: the per-person lease went when the LinkedIn search
was pooled (2026-11-03), the site having had one proxy for everyone since 2026-11-01.
"""
from __future__ import annotations

import httpx

from services.harvester.fetch import make_shared_client, make_site_client

SITE_PROXY = "http://site:secret@proxy.example:8080"


def _transport_for(client: httpx.Client, url: str) -> httpx.BaseTransport:
    """The transport httpx would actually use for this URL — the routing decision itself."""
    return client._transport_for_url(httpx.URL(url))


# ── what actually goes through the proxy ──────────────────────────────────────────────────────

def test_only_linkedin_is_proxied():
    """Residential bandwidth is metered per GB."""
    with make_site_client(SITE_PROXY) as client:
        linkedin = _transport_for(client, "https://www.linkedin.com/jobs/view/123")
        for direct_url in ("https://boards.greenhouse.io/acme/jobs/1",
                           "https://jobs.ashbyhq.com/acme/x",
                           "https://api.lever.co/v0/postings/acme",
                           "https://remotive.com/api/remote-jobs"):
            assert _transport_for(client, direct_url) is not linkedin, direct_url


def test_every_proxied_request_is_a_new_connection_so_a_new_address():
    """The site proxy gives each connection its next address. One kept open held one address for a
    whole pass, and asked a refused page again from the address that refused it (2026-11-02)."""
    import socketserver
    import threading

    connections: list[int] = []

    class Proxy(socketserver.BaseRequestHandler):
        def handle(self) -> None:
            connections.append(1)
            reader = self.request.makefile("rb")
            while reader.readline():
                while reader.readline() not in (b"\r\n", b""):
                    pass
                self.request.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok")

    with socketserver.ThreadingTCPServer(("127.0.0.1", 0), Proxy) as server:
        # Its stop is noticed between looks for a connection, half a second apart unless said.
        threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True).start()
        with make_site_client(f"http://127.0.0.1:{server.server_address[1]}") as client:
            for _ in range(3):
                assert client.get("http://www.linkedin.com/jobs/view/1").text == "ok"
        server.shutdown()

    assert len(connections) == 3


def test_linkedin_subdomains_are_proxied_too():
    with make_site_client(SITE_PROXY) as client:
        base = _transport_for(client, "https://linkedin.com/x")
        assert _transport_for(client, "https://www.linkedin.com/x") is base
        assert _transport_for(client, "https://uk.linkedin.com/x") is base


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

    # And the site client's direct route must stay direct too.
    with make_site_client(SITE_PROXY) as client:
        assert not _has_ambient_proxy(client, "https://boards.greenhouse.io/acme/jobs/1")
