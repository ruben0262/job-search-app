"""Indeed and Google Jobs via the python-jobspy library. LinkedIn is read without it
(`services.harvester.linkedin`).

jobspy + pandas are imported lazily inside the functions so this module still
imports on a Python where jobspy isn't installed (e.g. local 3.13 HTTP-only
testing); if it's missing at call time the adapter logs and returns [].

Confirmed against jobspy 1.1.82 (2026-06):
  • scrape_jobs returns a pandas DataFrame; the canonical link is `job_url`.
  • `date_posted` is a datetime.date (or NaT); `is_remote` is numpy bool_;
    missing scalars come back as pandas NaN.
  • Indeed    — no proxy; requires country_indeed; hours_old and is_remote are
                split across separate config queries (can't combine) — dedup
                handles the overlap downstream.
  • Google    — google_search_term free-text only.
A short delay between queries keeps us polite / under detection thresholds.

--- ported from the reference instance, 2026-07-27 ---

Lifted from `job-search/pipeline/sources/jobspy_source.py` by
`retired/services/harvester/sources/port_from_reference.py`. Parsing, pagination and per-source quirks are
unchanged on purpose — they are the tuned part, and a faithful copy stays diffable against the
original.

The one systematic change: this module no longer constructs `httpx.Client` itself. Every client
comes from `services.harvester.fetch`, which is what keeps "whose credential paid for this fetch"
answerable, and what stops an ambient `HTTP_PROXY` silently routing the shared harvest through one
user's proxy.
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from services.harvester.sources.base import JobLink

_DELAY_S = 2       # politeness pause between successive queries (sequential mode only)


def _run_queries(site: str, kwargs_list: list[dict], logger, workers: int) -> list:
    """Run a site's queries. workers>1 runs them concurrently (no delay — the rotating proxy
    gives each its own IP); workers==1 keeps the old polite sequential pace."""
    out: list = []
    if workers <= 1:
        for i, kw in enumerate(kwargs_list):
            if i:
                time.sleep(_DELAY_S)
            out.extend(_run_one(site, kw, logger))
        return out
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for found in pool.map(lambda kw: _run_one(site, kw, logger), kwargs_list):
            out.extend(found)
    return out


def _safe_str(val: Any) -> str:
    import pandas as pd
    if val is None or (not isinstance(val, str) and pd.isna(val)):
        return ""
    return str(val)


def _iso_date(val: Any) -> str | None:
    import pandas as pd
    if val is None or (not isinstance(val, str) and pd.isna(val)):
        return None
    return val.isoformat() if hasattr(val, "isoformat") else str(val)


def _rows_to_joblinks(df, source: str) -> list[JobLink]:
    import pandas as pd
    out: list[JobLink] = []
    if df is None or len(df) == 0:
        return out
    for _, r in df.iterrows():
        url = _safe_str(r.get("job_url"))
        if not url:
            continue
        # jobspy's is_remote=False just means "not marked remote" — could be hybrid, so
        # unknown (False would make the workplace filter treat it as explicit on-site).
        rem = r.get("is_remote")
        # LinkedIn Easy Apply heuristic: external jobs carry a direct company URL,
        # Easy Apply ones don't (you apply on LinkedIn). Only meaningful for LinkedIn.
        # easy_apply from the scrape is unreliable — job_url_direct is often EMPTY even for
        # non-easy jobs in a fast scrape, so `not job_url_direct` = True for everything (⚡ on
        # all). Leave it unknown; fetch_job sets it accurately from the LinkedIn guest page.
        easy = None
        out.append(JobLink(
            url=url,
            title=_safe_str(r.get("title")),
            company=_safe_str(r.get("company")),
            location=_safe_str(r.get("location")),
            source=source,
            date_posted=_iso_date(r.get("date_posted")),
            snippet=_safe_str(r.get("description")),
            is_remote=True if (rem is not None and not pd.isna(rem) and bool(rem)) else None,
            easy_apply=easy,
        ))
    return out


def _run_one(site: str, kwargs: dict[str, Any], logger) -> list[JobLink]:
    try:
        from jobspy import scrape_jobs
    except ImportError as exc:
        logger.warning("jobspy not installed (%s) - skipping %s", exc, site)
        return []
    label = kwargs.get("search_term") or kwargs.get("google_search_term") or ""
    try:
        df = scrape_jobs(**kwargs)
    except Exception as exc:
        logger.warning("%s scrape failed for %r: %s", site, label, exc)
        return []
    links = _rows_to_joblinks(df, site)
    # Tagged here because this is the only place both the query and its results are in scope.
    for link in links:
        link.found_via = label
    return links


def gather_indeed(cfg: dict[str, Any], logger) -> list[JobLink]:
    results_wanted = cfg.get("results_wanted", 30)
    hours_old = cfg.get("hours_old")

    # Build the work list: countries x keywords cross-product, then explicit queries.
    combos: list[dict[str, Any]] = [
        {"search_term": kw, "country_indeed": c, "hours_old": hours_old}
        for c in cfg.get("countries", [])
        for kw in cfg.get("keywords", [])
    ]
    combos.extend(cfg.get("queries", []))

    out: list[JobLink] = []
    for i, q in enumerate(combos):
        if i:
            time.sleep(_DELAY_S)
        kwargs: dict[str, Any] = dict(
            site_name=["indeed"], search_term=q["search_term"],
            location=q.get("location"), results_wanted=results_wanted,
            country_indeed=q.get("country_indeed", "UK"),
        )
        ho = q.get("hours_old", hours_old)
        if ho:
            kwargs["hours_old"] = ho
        if q.get("is_remote"):
            kwargs["is_remote"] = True
        out.extend(_run_one("indeed", kwargs, logger))
    return out


def gather_google(cfg: dict[str, Any], logger) -> list[JobLink]:
    results_wanted = cfg.get("results_wanted", 30)
    out: list[JobLink] = []
    for i, q in enumerate(cfg.get("queries", [])):
        if i:
            time.sleep(_DELAY_S)
        kwargs = dict(site_name=["google"], google_search_term=q["google_search_term"],
                      results_wanted=results_wanted)
        out.extend(_run_one("google", kwargs, logger))
    return out
