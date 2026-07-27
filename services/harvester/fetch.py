"""The only place the harvester builds an HTTP client. Per-user proxy routing lives here.

**This module is what makes decision D3 true rather than aspirational.** D3 says every user's proxy
credential is used strictly for that user's own fetches — never pooled, never rotated across users.
That is a claim about *every* outbound request the harvester makes, so it can only be guaranteed if
there is exactly one way to make one. `make_client()` is that way; constructing an `httpx.Client`
anywhere else in the harvester defeats it (`HARVESTER_PLAN.md` §5, `CODE_STRUCTURE.md` §3).

**Three deliberate departures from the reference implementation**, all forced by multi-tenancy:

1. **The proxy comes from a lease, never from the environment.** The reference reads `PROXY_URL`
   from env, which is single-tenant by construction. Here an ambient env proxy would silently route
   *every* user's traffic through one credential — precisely the pooling D3 forbids, and it would
   look like it was working. There is no env fallback, on purpose.
2. **A client carries the identity of the user it was built for.** `client.lease.user_id` is
   inspectable, so "was this credential used for someone else's fetch" is an assertion rather than a
   code review (T13).
3. **`PROXY_HOSTS=*` is gone.** The reference offers proxy-everything as an escape hatch. Under
   per-user billing that would spend one user's metered bandwidth on shared ATS fetches that work
   fine direct — the user pays for work done on everyone's behalf, which is the free-rider problem
   inverted.

**The shared loop has no way to acquire a proxy at all.** `make_shared_client()` takes no credential
argument, so shared harvesting cannot spend anyone's bandwidth even by mistake. That is a structural
guarantee, not a convention.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

import httpx

# Residential proxy bandwidth is a METERED consumable billed per GB on the wire, so only hosts that
# actually refuse datacenter IPs are routed through it. After the 2026-07-26 routing change that is
# LinkedIn alone; Greenhouse, Lever, Ashby, Workable, Recruitee and the rest serve fine direct and
# are the bulk of description fetches. Proxying them was paying residential rates for pages that
# never needed it (HARVESTER_PLAN.md §5).
PROXIED_HOSTS = ("linkedin.com",)

_UA = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}
DEFAULT_TIMEOUT = 25


class ProxyCapExceeded(RuntimeError):
    """The user's daily proxy budget is spent. Raised at admission, before any bytes move.

    Checked before the fetch rather than during it, because a quota checked halfway through has
    already spent the resource it was protecting (`TENANT_SAFETY.md` §3).
    """


@dataclass(frozen=True)
class ProxyLease:
    """One user's authorisation to spend their own proxy bandwidth, for the duration of one loop.

    Frozen and carrying `user_id` so that a client built from it can be *asked* whose credential it
    holds. That is what turns "we only use a credential for its owner" from a review comment into
    T13, an assertion.

    `remaining_mb` is a snapshot taken at lease time. It bounds the loop; the authoritative counter
    is the database row, updated as bytes are actually spent.
    """

    user_id: UUID
    proxy_url: str
    remaining_mb: float

    def __post_init__(self) -> None:
        if not self.proxy_url:
            raise ValueError("a ProxyLease without a proxy_url cannot authorise anything")
        if self.remaining_mb <= 0:
            raise ProxyCapExceeded(
                f"user {self.user_id} has no proxy budget left ({self.remaining_mb:.1f} MB)")


class TenantClient(httpx.Client):
    """An `httpx.Client` that knows whose credential it carries, and how much it has spent.

    Subclassing rather than wrapping so it is a drop-in for every call site that expects a client —
    a wrapper would tempt someone into reaching for `.inner` and losing the accounting.
    """

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
        """Meter every response, because the proxy bills on the wire rather than on success.

        A 403 or a redirect chain costs real bytes. Counting only successful fetches would
        under-report exactly when a credential is failing and being retried most.
        """
        response = super().send(request, **kwargs)
        self.bytes_downloaded += _response_bytes(response)
        return response


def _response_bytes(response: httpx.Response) -> int:
    """Bytes a response actually cost, headers included.

    `num_bytes_downloaded` covers the body. Headers are small individually and not nothing across
    thousands of requests, so they are estimated rather than ignored — under-counting a metered
    consumable is the error that shows up as an unexpected invoice.
    """
    body = getattr(response, "num_bytes_downloaded", 0) or len(response.content or b"")
    headers = sum(len(k) + len(v) + 4 for k, v in response.headers.raw)
    return int(body) + headers


def _mounts(proxy_url: str,
            direct: httpx.BaseTransport | None = None) -> dict[str, httpx.BaseTransport]:
    """Route only `PROXIED_HOSTS` through the proxy; everything else via `direct`.

    Per-host mounts rather than a client-wide proxy, so httpx resolves routing **per request URL**.
    A redirect out of LinkedIn to an ATS board is then fetched direct rather than inheriting the
    origin's proxied transport — which is both cheaper and what the ATS expects.
    """
    proxied = httpx.HTTPTransport(proxy=proxy_url)
    mounts: dict[str, httpx.BaseTransport] = {"all://": direct or httpx.HTTPTransport()}
    for host in PROXIED_HOSTS:
        mounts[f"all://{host}"] = proxied
        mounts[f"all://*.{host}"] = proxied
    return mounts


def make_client(lease: ProxyLease | None = None, **kwargs: Any) -> TenantClient:
    """**The only HTTP client factory in the harvester.** Never construct `httpx.Client` directly.

    With a lease: LinkedIn is fetched through that user's proxy, everything else direct.
    Without one: everything direct, and no user's bandwidth can be spent.

    **`trust_env=False`, always.** httpx otherwise honours ambient `HTTP_PROXY` / `HTTPS_PROXY` /
    `ALL_PROXY`, which on a multi-tenant box would silently route *every* user's traffic through one
    endpoint — the pooling D3 forbids, arriving via an environment variable nobody set deliberately.
    It would also look like it was working: jobs arrive, feeds populate, and the only symptom is one
    person's proxy bill. Routing here comes from the lease and nowhere else.

    A caller may pass `transport=` to supply the direct (non-proxied) transport; the proxied mounts
    are still derived from the lease, so tests can drive routing without a network while leaving the
    thing under test — which host goes through whose proxy — genuinely exercised.
    """
    opts: dict[str, Any] = {
        "headers": _UA, "timeout": DEFAULT_TIMEOUT, "follow_redirects": True,
        "trust_env": False, **kwargs}
    if lease is None:
        return TenantClient(lease=None, **opts)
    direct = opts.pop("transport", None)
    return TenantClient(lease=lease, mounts=_mounts(lease.proxy_url, direct), **opts)


def make_shared_client(**kwargs: Any) -> TenantClient:
    """The shared harvest loop's client. **Structurally incapable of holding a credential.**

    It takes no lease parameter at all, so the shared tier cannot spend a user's metered bandwidth
    even by mistake. Two thirds of sources need no proxy, and this is what guarantees they never
    accidentally use one.
    """
    return make_client(lease=None, **kwargs)


def is_proxied(url: str) -> bool:
    """Whether this URL would be routed through the proxy. Mirrors `_mounts`' host matching."""
    host = (httpx.URL(url).host or "").lower().removeprefix("www.")
    return any(host == h or host.endswith(f".{h}") for h in PROXIED_HOSTS)
