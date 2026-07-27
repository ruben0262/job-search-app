"""VC portfolio job boards — thousands of startups aggregated per fund, with direct apply links.

Two platforms run most of these boards (both verified 2026-07-08, no auth, no blocking):
  - Consider ("api-boards"): jobs.a16z.com (~15k jobs), jobs.sequoiacap.com (~9k). POST
    /api-boards/search-jobs on the board's own host with {board:{id}, query:{searchText,
    remoteOnly}, meta:{size}}. Results are newest-first with ISO timeStamps; the server-side
    datePosted param is unreliable, so recency is enforced client-side.
  - Getro: jobs.accel.com and many others. POST api.getro.com/api/v2/collections/<network_id>/
    search/jobs (needs Accept: application/json or it 406s). 20 hits/page, newest-first,
    epoch created_at. The network_id is in the board page's __NEXT_DATA__ (props.pageProps.
    network.id) — recorded per board in config.

Both return DIRECT company/ATS apply URLs, so every result also feeds the ATS token harvester
(gather_all scans all result URLs) — these boards grow the direct-ATS company list for free.
Board lists, queries and filters live in config.yaml (consider / getro blocks); filters reuse
the same keyword/exclude/location anchors as the direct-ATS sources via ats.JobFilter.

--- ported from the reference instance, 2026-07-27 ---

Lifted from `job-search/pipeline/sources/vcboards.py` by
`services/harvester/sources/port_from_reference.py`. Parsing, pagination and per-source quirks are
unchanged on purpose — they are the tuned part, and a faithful copy stays diffable against the
original.

The one systematic change: this module no longer constructs `httpx.Client` itself. Every client
comes from `services.harvester.fetch`, which is what keeps "whose credential paid for this fetch"
answerable, and what stops an ambient `HTTP_PROXY` silently routing the shared harvest through one
user's proxy.
"""
from __future__ import annotations

from typing import Any

from libs.corpus.filters import epoch_to_iso
from . import ats
from services.harvester.sources.base import JobLink


def _dedup_add(out: list[JobLink], seen: set[str], jl: JobLink, jf: ats.JobFilter) -> None:
    if jl.url and jl.url not in seen and jf.ok(jl):
        seen.add(jl.url)
        out.append(jl)


# ── Consider (jobs.a16z.com, jobs.sequoiacap.com, …) ─────────────────────

def gather_consider(cfg: dict[str, Any], logger) -> list[JobLink]:
    jf = ats.JobFilter(cfg)
    size = int(cfg.get("page_size", 100))
    remote_only = bool(cfg.get("remote_only", False))
    out: list[JobLink] = []
    seen: set[str] = set()
    for board in cfg.get("boards", []):
        host, bid = board.get("host"), board.get("board")
        if not host or not bid:
            continue
        for q in (cfg.get("queries") or [""]):
            try:
                r = ats._http().post(
                    f"https://{host}/api-boards/search-jobs",
                    json={"meta": {"size": size}, "board": {"id": bid, "isParent": True},
                          "query": {"promoteFeatured": False, "searchText": q,
                                    "remoteOnly": remote_only}})
                r.raise_for_status()
                jobs = r.json().get("jobs") or []
            except Exception as exc:
                logger.warning("consider %s %r failed: %s", host, q, exc)
                continue
            for j in jobs:
                url = (j.get("applyUrl") or j.get("url") or "").strip()
                locs = [str(x) for x in (j.get("locations") or []) if x]
                skills = [str(s) for s in (j.get("skills") or [])[:15]]
                _dedup_add(out, seen, JobLink(
                    url=url, title=(j.get("title") or "").strip(),
                    company=(j.get("companyName") or "").strip(),
                    location=", ".join(locs), source="consider",
                    date_posted=j.get("timeStamp"),
                    snippet="\n".join(p for p in [j.get("title") or "",
                                                  "Skills: " + ", ".join(skills) if skills else ""] if p),
                    is_remote=(True if j.get("remote") else None)), jf)
    logger.info("consider: %d boards -> %d relevant recent jobs", len(cfg.get("boards", [])), len(out))
    return out


# ── Getro (jobs.accel.com, …) ────────────────────────────────────────────

def gather_getro(cfg: dict[str, Any], logger) -> list[JobLink]:
    jf = ats.JobFilter(cfg)
    pages = int(cfg.get("pages", 2))          # 20/page, newest-first
    out: list[JobLink] = []
    seen: set[str] = set()
    for board in cfg.get("boards", []):
        nid = board.get("network_id")
        if not nid:
            continue
        for q in (cfg.get("queries") or [""]):
            for page in range(pages):
                try:
                    r = ats._http().post(
                        f"https://api.getro.com/api/v2/collections/{nid}/search/jobs",
                        json={"hitsPerPage": 20, "page": page, "query": q, "filters": {}},
                        headers={"Accept": "application/json"})
                    r.raise_for_status()
                    jobs = (r.json().get("results") or {}).get("jobs") or []
                except Exception as exc:
                    logger.warning("getro %s %r failed: %s", board.get("host") or nid, q, exc)
                    break
                for j in jobs:
                    org = j.get("organization") or {}
                    locs = [str(x) for x in (j.get("locations") or []) if x]
                    skills = [str(s) for s in (j.get("skills") or [])[:15]]
                    # work_mode is exact: remote / hybrid / on_site — use all three states.
                    wm = (j.get("work_mode") or "").lower()
                    loc = ", ".join(locs)
                    if wm == "hybrid":
                        loc = f"{loc} (Hybrid)".strip()
                    _dedup_add(out, seen, JobLink(
                        url=(j.get("url") or "").strip(), title=(j.get("title") or "").strip(),
                        company=(org.get("name") or "").strip(),
                        location=loc, source="getro",
                        date_posted=epoch_to_iso(j.get("created_at")),
                        snippet="\n".join(p for p in [j.get("title") or "",
                                                      "Skills: " + ", ".join(skills) if skills else ""] if p),
                        is_remote=(True if wm == "remote" else (False if wm == "on_site" else None))), jf)
                if len(jobs) < 20:
                    break
    logger.info("getro: %d boards -> %d relevant recent jobs", len(cfg.get("boards", [])), len(out))
    return out
