"""The only place the harvester builds an HTTP client: a plain one for the boards, and one with the
site's proxy for LinkedIn (`libs.proxy`).

There is no per-person client any more. Each account once paid for its own proxy, and a `ProxyLease`
said whose a client carried; since the site has one proxy for everyone (owner, 2026-11-01) and the
LinkedIn search is pooled across people (2026-11-03), nothing is fetched on one person's behalf.
"""
from __future__ import annotations

from typing import Any

import httpx

# Residential bandwidth is metered per GB, so only hosts that refuse datacenter IPs go through it.
PROXIED_HOSTS = ("linkedin.com",)

_UA = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}
DEFAULT_TIMEOUT = 25


class MeteredClient(httpx.Client):
    """An `httpx.Client` that counts what it has downloaded, for the log."""

    def __init__(self, **kwargs: Any):
        super().__init__(**kwargs)
        self.bytes_downloaded = 0

    @property
    def megabytes_downloaded(self) -> float:
        return self.bytes_downloaded / (1024 * 1024)

    def send(self, request: httpx.Request, **kwargs: Any) -> httpx.Response:
        """Meter every response, because the proxy bills on the wire rather than on success."""
        response = super().send(request, **kwargs)
        self.bytes_downloaded += _response_bytes(response)
        return response


def _response_bytes(response: httpx.Response) -> int:
    """Bytes a response actually cost, headers included."""
    body = getattr(response, "num_bytes_downloaded", 0) or len(response.content or b"")
    headers = sum(len(k) + len(v) + 4 for k, v in response.headers.raw)
    return int(body) + headers


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
    # `trust_env=False`: a stray HTTP_PROXY on the box must not route the boards through anything.
    return {"headers": _UA, "timeout": DEFAULT_TIMEOUT, "follow_redirects": True, "trust_env": False,
            **kwargs}


def make_shared_client(**kwargs: Any) -> MeteredClient:
    """The boards' client: every request direct."""
    return MeteredClient(**_options(kwargs))


def make_site_client(proxy_url: str, **kwargs: Any) -> MeteredClient:
    """A client with the site proxy for `PROXIED_HOSTS` and every other host direct: describing the
    LinkedIn postings the searches stored."""
    opts = _options(kwargs)
    direct = opts.pop("transport", None)
    return MeteredClient(mounts=_mounts(proxy_url, direct), **opts)


def is_proxied(url: str) -> bool:
    """Whether this URL would be routed through the proxy. Mirrors `_mounts`' host matching."""
    host = (httpx.URL(url).host or "").lower().removeprefix("www.")
    return any(host == h or host.endswith(f".{h}") for h in PROXIED_HOSTS)
