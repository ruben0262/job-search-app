"""The only place the harvester builds an HTTP client: a plain one for the boards, and one with the
site's proxy for LinkedIn (`libs.proxy`).

There is no per-person client any more. Each account once paid for its own proxy, and a `ProxyLease`
said whose a client carried; since the site has one proxy for everyone (owner, 2026-11-01) and the
LinkedIn search is pooled across people (2026-11-03), nothing is fetched on one person's behalf.
"""
from __future__ import annotations

import base64
import os
from typing import Any

import httpx

from libs.corpus.postings import PAGE_HEADERS

# Residential bandwidth is metered per GB, so only hosts that refuse datacenter IPs go through it.
PROXIED_HOSTS = ("linkedin.com",)

DEFAULT_TIMEOUT = 25

# The boards whose API takes a key of ours: Reed's as the user name of a basic-auth header, NAV's as a bearer
# token, as each one's documentation asks. Added here, the one place a client is built, and sent to that API
# alone: its list (`sources.http_apis`) and a job's details, which the page reader asks for
# (`libs.corpus.postings`) without ever holding a key.
REED_KEY, NAV_KEY = "REED_API_KEY", "NAV_API_TOKEN"


class _BoardKeys(httpx.Auth):
    def __init__(self, headers: dict[tuple[str, str], str]):
        self._headers = headers

    def auth_flow(self, request: httpx.Request):
        for (host, path), value in self._headers.items():
            if request.url.host == host and request.url.path.startswith(path):
                request.headers["Authorization"] = value
        yield request


def _board_keys() -> _BoardKeys | None:
    """The `Authorization` each keyed board's API is sent, by its host and path, for the keys that are set."""
    reed, nav = os.environ.get(REED_KEY, "").strip(), os.environ.get(NAV_KEY, "").strip()
    headers = {**({("www.reed.co.uk", "/api/"): "Basic " + base64.b64encode(f"{reed}:".encode()).decode()}
                  if reed else {}),
               **({("pam-stilling-feed.nav.no", "/api/"): f"Bearer {nav}"} if nav else {})}
    return _BoardKeys(headers) if headers else None


def _mounts(proxy_url: str,
            direct: httpx.BaseTransport | None = None) -> dict[str, httpx.BaseTransport]:
    """Route only `PROXIED_HOSTS` through the proxy; everything else via `direct`.

    A new connection for every proxied request. The site proxy sends each new connection out from its
    next address (measured 2026-11-02: four, in turn), so every page, and every try again after a
    refusal, leaves from another one, and LinkedIn's allowance per address is spent evenly. A
    connection kept open held one address for the whole pass, and a refused page was asked again from
    the address that had just refused it.
    """
    proxied = httpx.HTTPTransport(proxy=proxy_url, limits=httpx.Limits(max_keepalive_connections=0))
    mounts: dict[str, httpx.BaseTransport] = {"all://": direct or httpx.HTTPTransport()}
    for host in PROXIED_HOSTS:
        mounts[f"all://{host}"] = proxied
        mounts[f"all://*.{host}"] = proxied
    return mounts


def _options(kwargs: dict[str, Any]) -> dict[str, Any]:
    # Every request carries `PAGE_HEADERS`, and an adapter passes only what it adds: a JSON `Accept`, a
    # token. `headers=` used to replace them, so four adapters wrote out a User-Agent of their own.
    headers = {**PAGE_HEADERS, **kwargs.pop("headers", {})}
    keys = _board_keys()
    # `trust_env=False`: a stray HTTP_PROXY on the box must not route the boards through anything.
    return {"timeout": DEFAULT_TIMEOUT, "follow_redirects": True, "trust_env": False,
            **({"auth": keys} if keys else {}), **kwargs, "headers": headers}


def make_shared_client(**kwargs: Any) -> httpx.Client:
    """The boards' client: every request direct."""
    return httpx.Client(**_options(kwargs))


def make_site_client(proxy_url: str, **kwargs: Any) -> httpx.Client:
    """A client with the site proxy for `PROXIED_HOSTS` and every other host direct: describing the
    LinkedIn postings the searches stored."""
    opts = _options(kwargs)
    direct = opts.pop("transport", None)
    return httpx.Client(mounts=_mounts(proxy_url, direct), **opts)


def is_proxied(url: str) -> bool:
    """Whether this URL would be routed through the proxy. Mirrors `_mounts`' host matching."""
    host = (httpx.URL(url).host or "").lower().removeprefix("www.")
    return any(host == h or host.endswith(f".{h}") for h in PROXIED_HOSTS)
