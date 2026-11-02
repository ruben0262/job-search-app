"""The only place the harvester builds an HTTP client."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

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


@dataclass(frozen=True)
class ProxyLease:
    """One account's LinkedIn run through the site proxy: whose words it searches, and the proxy."""

    user_id: UUID
    proxy_url: str

    def __post_init__(self) -> None:
        if not self.proxy_url:
            raise ValueError("a ProxyLease without a proxy_url cannot authorise anything")


class TenantClient(httpx.Client):
    """An `httpx.Client` that knows whose credential it carries, and how much it has spent."""

    def __init__(self, *, lease: ProxyLease | None, **kwargs: Any):
        super().__init__(**kwargs)
        self.lease = lease
        self.bytes_downloaded = 0

    @property
    def owner(self) -> UUID | None:
        """The user this client may fetch for. `None` means the shared, unproxied loop."""
        return self.lease.user_id if self.lease else None

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


def make_client(lease: ProxyLease | None = None, **kwargs: Any) -> TenantClient:
    """The only HTTP client factory in the harvester."""
    opts: dict[str, Any] = {
        "headers": _UA, "timeout": DEFAULT_TIMEOUT, "follow_redirects": True,
        "trust_env": False, **kwargs}
    if lease is None:
        return TenantClient(lease=None, **opts)
    direct = opts.pop("transport", None)
    return TenantClient(lease=lease, mounts=_mounts(lease.proxy_url, direct), **opts)


def make_site_client(proxy_url: str, **kwargs: Any) -> TenantClient:
    """A client with the site proxy for `PROXIED_HOSTS`, for work no one account owns: describing the
    LinkedIn postings everyone's searches stored."""
    opts: dict[str, Any] = {
        "headers": _UA, "timeout": DEFAULT_TIMEOUT, "follow_redirects": True,
        "trust_env": False, **kwargs}
    direct = opts.pop("transport", None)
    return TenantClient(lease=None, mounts=_mounts(proxy_url, direct), **opts)


def make_shared_client(**kwargs: Any) -> TenantClient:
    """The shared harvest loop's client."""
    return make_client(lease=None, **kwargs)


def is_proxied(url: str) -> bool:
    """Whether this URL would be routed through the proxy. Mirrors `_mounts`' host matching."""
    host = (httpx.URL(url).host or "").lower().removeprefix("www.")
    return any(host == h or host.endswith(f".{h}") for h in PROXIED_HOSTS)
