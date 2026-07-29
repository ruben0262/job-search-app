"""The source registry, and the structural rules the adapter port had to preserve.

These are cheap tests guarding an expensive mistake. The adapters are ~3,500 lines of parsing that
no test here can meaningfully check without a network — but the *rules* around them are checkable,
and they are the ones whose violation is silent:

- an adapter building its own HTTP client would bypass the one factory D3 depends on, and would
  restore `trust_env=True` so an ambient `HTTP_PROXY` could route the shared harvest through one
  user's credential;
- a proxied source mis-tagged as shared would spend that user's bandwidth for everybody.

Neither raises. Both are one grep and one assertion away from being impossible.
"""
from __future__ import annotations

import ast
import logging
import re
from pathlib import Path

import pytest

from services.harvester.sources import (
    OUTER_CONCURRENCY,
    PER_USER_SOURCES,
    REGISTRY,
    SHARED_SOURCES,
    Source,
    Tier,
    jobspy_source,
    sources_for,
)
from services.harvester.sources.base import JobLink

HARVESTER = Path(__file__).resolve().parents[1] / "services" / "harvester"
ADAPTERS = sorted(p for p in (HARVESTER / "sources").glob("*.py")
                  if p.name != "port_from_reference.py")


# ── the one-factory rule ──────────────────────────────────────────────────────────────────────

def test_no_adapter_builds_its_own_http_client():
    """**The rule the whole fetch layer rests on.**

    The reference builds `httpx.Client` in 17 places, which is fine with one tenant and no proxy on
    those boards. Here it would mean clients that no lease governs and that default to
    `trust_env=True` — so a stray `HTTP_PROXY` would silently route the shared harvest through one
    user's proxy, and "whose credential paid for this" would stop having one answer.
    """
    offenders = [
        f"{path.relative_to(HARVESTER)}:{i}"
        for path in HARVESTER.rglob("*.py") if path.name != "port_from_reference.py"
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if re.search(r"\bhttpx\.Client\(", line)
    ]
    assert offenders == [], (
        "these construct an HTTP client directly instead of using services.harvester.fetch: "
        + ", ".join(offenders))


def _env_reads(path: Path) -> list[tuple[int, str]]:
    """Every environment-variable name a module actually reads, with its line number.

    Parsed from the AST rather than grepped, because the modules *discuss* `HTTP_PROXY` in their
    docstrings — the first version of this check flagged its own explanatory prose, which is a test
    that fails for a reason unrelated to the thing it protects.
    """
    found: list[tuple[int, str]] = []
    tree = ast.parse(path.read_text(encoding="utf-8"))

    def _is_environ(node: ast.expr) -> bool:
        """`os.environ` or a bare `environ`. Not any object that happens to have `.get`."""
        return (isinstance(node, ast.Attribute) and node.attr == "environ") or \
            (isinstance(node, ast.Name) and node.id == "environ")

    for node in ast.walk(tree):
        # os.environ.get("X") / os.getenv("X") — but NOT some_dict.get("proxy"), which is an
        # ordinary dictionary read. The first version of this check did not distinguish them and
        # flagged `cfg.get("proxy")`, which is a configuration value, not an environment one.
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.args:
            is_env_get = node.func.attr == "get" and _is_environ(node.func.value)
            is_getenv = node.func.attr == "getenv"
            first = node.args[0]
            if (is_env_get or is_getenv) and isinstance(first, ast.Constant) \
                    and isinstance(first.value, str):
                found.append((node.lineno, first.value))
        # os.environ["X"]
        elif isinstance(node, ast.Subscript) and isinstance(node.value, ast.Attribute) \
                and node.value.attr == "environ" and isinstance(node.slice, ast.Constant) \
                and isinstance(node.slice.value, str):
            found.append((node.lineno, node.slice.value))
    return found


def test_the_fetch_layer_is_the_only_place_that_reads_a_proxy_setting():
    """A proxy read anywhere else is a proxy no lease governs.

    The reference resolved its proxy from `PROXY_URL` in whichever module needed one. Under D3 the
    credential belongs to a user, not to the process, so an environment read is not a configuration
    style — it is a credential with no owner.
    """
    offenders = [
        f"{path.relative_to(HARVESTER)}:{line} reads {name}"
        for path in HARVESTER.rglob("*.py") if path.name != "fetch.py"
        for line, name in _env_reads(path)
        if re.search(r"PROXY", name, re.IGNORECASE)
    ]
    assert offenders == [], "proxy configuration read outside fetch.py: " + ", ".join(offenders)


def test_the_check_above_would_actually_catch_one():
    """A guard that cannot fail is worse than none, so prove this one detects the thing it forbids.

    Learned the expensive way: the CI drift check shipped inert because nobody made it fail once.
    """
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        offender = Path(tmp) / "sneaky.py"
        offender.write_text(
            'import os\n'
            '"""A docstring mentioning HTTP_PROXY must NOT trigger this."""\n'
            'proxy = os.environ.get("PROXY_URL")\n', encoding="utf-8")
        names = [name for _, name in _env_reads(offender)]
        assert "PROXY_URL" in names, "the check would miss a real environment read"

        innocent = Path(tmp) / "prose.py"
        innocent.write_text('"""Mentions HTTP_PROXY and ALL_PROXY in prose only."""\n',
                            encoding="utf-8")
        assert _env_reads(innocent) == [], "the check flags documentation as a violation"


# ── the registry ──────────────────────────────────────────────────────────────────────────────

def test_linkedin_is_the_only_per_user_source():
    """It is the only source that needs a residential proxy, and after the 2026-07-26 routing
    change the only thing proxied at all. Anything else tagged per-user would be paying residential
    rates for pages that serve fine direct."""
    assert PER_USER_SOURCES == ("jobspy_linkedin",)


def test_the_shared_tier_contains_no_proxied_source():
    """A proxied source mis-tagged shared would spend one user's metered bandwidth for everyone —
    the free-riding D3 removed, reintroduced by a typo in a table."""
    assert "jobspy_linkedin" not in SHARED_SOURCES
    assert set(SHARED_SOURCES) | set(PER_USER_SOURCES) == set(REGISTRY)


def test_sources_for_cannot_hand_the_shared_loop_a_proxied_source():
    shared = sources_for(Tier.SHARED)
    assert shared and all(s.tier is Tier.SHARED for s in shared)
    assert all(s.key != "jobspy_linkedin" for s in shared)


def test_only_the_ats_family_needs_the_token_and_etag_repository():
    """Everything else is stateless. A source that claimed to need it would be handed a database
    session it has no business holding."""
    needing = {s.key for s in REGISTRY.values() if s.needs_ats_repo}
    assert needing == {"greenhouse", "ashby", "lever", "smartrecruiters", "recruitee",
                       "breezy", "workday", "personio", "workable"}


def test_every_registry_entry_is_callable_and_well_formed():
    for key, source in REGISTRY.items():
        assert isinstance(source, Source)
        assert source.key == key, "the registry key and the Source key must agree"
        assert callable(source.adapter)
        assert isinstance(source.tier, Tier)


def test_the_outer_concurrency_cap_is_not_raised():
    """From the 2026-07-24 incident: JobSpy sources open their own nested pools, so an unbounded
    outer fan-out spawned ~180 threads per scrape. The comment says do not raise it; this makes the
    comment enforceable."""
    assert OUTER_CONCURRENCY == 8


# ── the JobLink contract ──────────────────────────────────────────────────────────────────────

def test_a_joblink_maps_onto_a_corpus_row_and_carries_no_user():
    """A source adapter has no access to a user and must not acquire one — the corpus takes what
    identifies a public posting and nothing about who asked for it."""
    row = JobLink(url="https://boards.greenhouse.io/acme/jobs/1", title="Backend Engineer",
                  company="Acme", location="London", source="greenhouse",
                  is_remote=True).as_corpus_row()

    assert row["url"] == "https://boards.greenhouse.io/acme/jobs/1"
    assert row["source"] == "greenhouse" and row["title"] == "Backend Engineer"
    assert row["company"] == "Acme" and row["location"] == "London" and row["is_remote"] is True
    assert not any("user" in key for key in row)


def test_a_joblink_carries_the_posting_metadata_the_feed_card_reads():
    """**Both of these were collected and then dropped.** `date_posted` never reached the corpus, so
    every card was rendered with no age on it; `easy_apply` is the ⚡ and the signal that makes the
    apply-URL dedup layer resolvable."""
    row = JobLink(url="https://www.linkedin.com/jobs/view/1", title="Backend Engineer (Remote)",
                  company="Acme", location="London", source="linkedin",
                  date_posted="2026-07-29T08:00:00+00:00", easy_apply=True,
                  is_remote=True).as_corpus_row()

    assert row["posted_at"] is not None and row["posted_at"].year == 2026
    assert row["easy_apply"] is True
    # Classified from what the source gave us; LinkedIn's own pill overwrites it at fetch time.
    assert row["workplace"] == "remote"


def test_joblink_defaults_are_safe_for_an_unknown_field():
    """Sources disagree about what they report. Unknown must be `None`, not a guess — `is_remote`
    defaulting to False would quietly exclude remote jobs from a remote-only user's feed."""
    link = JobLink(url="https://example.test/1")
    assert link.is_remote is None
    assert link.date_posted is None
    assert link.application_url is None
    assert link.found_at


@pytest.mark.parametrize("path", ADAPTERS, ids=lambda p: p.name)
def test_every_adapter_module_imports(path):
    """A port that leaves a module unimportable is a source that silently never runs."""
    import importlib
    importlib.import_module(f"services.harvester.sources.{path.stem}")


def test_linkedin_cannot_be_gathered_without_a_lease():
    """The reference read `cfg["proxy"]`. A credential arriving through a config dict has no
    verified owner, so nothing distinguishes "this user's proxy" from "whichever proxy was in the
    config" — which is the pooling D3 forbids, arriving as a plain dictionary key.

    jobspy opens its own connections and cannot be routed through `make_client`, so this is the one
    place a proxy URL reaches a third-party library. Requiring the lease type is what keeps the
    owner attached to the value.
    """
    import inspect

    sig = inspect.signature(jobspy_source.gather_linkedin)
    lease = sig.parameters.get("lease")
    assert lease is not None, "gather_linkedin must take a lease"
    assert lease.kind is inspect.Parameter.KEYWORD_ONLY, \
        "keyword-only, so it cannot be positionally confused with cfg"
    assert lease.default is inspect.Parameter.empty, "mandatory, so it cannot be omitted"

    with pytest.raises(TypeError):
        jobspy_source.gather_linkedin({}, logging.getLogger("test"))


def test_the_shared_jobspy_sources_take_no_lease():
    """Indeed and Google need no proxy. Accepting one would let them spend a user's bandwidth."""
    import inspect

    for adapter in (jobspy_source.gather_indeed, jobspy_source.gather_google):
        assert "lease" not in inspect.signature(adapter).parameters, adapter.__name__
