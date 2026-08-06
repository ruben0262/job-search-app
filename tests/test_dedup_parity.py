"""T2 — the dedup regression suite."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from libs.corpus.keys import canonical_url, content_key

GOLDEN = Path(__file__).parent / "golden" / "dedup_cases.json"
_cases = json.loads(GOLDEN.read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", _cases["canonical_url"], ids=lambda c: c["url"] or "<empty>")
def test_canonical_url_matches_reference(case):
    assert canonical_url(case["url"]) == case["expected"]


@pytest.mark.parametrize(
    "case", _cases["content_key"],
    ids=lambda c: f"{c['title'][:20]}|{c['company']}|{c['location']}")
def test_content_key_matches_reference(case):
    assert content_key(case["title"], case["company"], case["location"]) == case["expected"]


# ── The rules above, restated as behaviour rather than as table rows ──────────────────────────
# The parametrised cases prove equality with the reference.


def test_the_same_job_on_both_greenhouse_hosts_is_one_job():
    """Two hostnames, one board. Missing this puts every Greenhouse posting in the feed twice."""
    assert (canonical_url("https://job-boards.greenhouse.io/acme/jobs/123")
            == canonical_url("https://boards.greenhouse.io/acme/jobs/123"))


def test_tracking_parameters_do_not_make_a_new_job():
    """`?gh_src=` and `utm_*` are added by whoever linked to the posting, not by the employer."""
    base = canonical_url("https://boards.greenhouse.io/acme/jobs/123")
    for noisy in ("https://boards.greenhouse.io/acme/jobs/123?gh_src=abcd",
                  "https://boards.greenhouse.io/acme/jobs/123?utm_source=x&utm_campaign=y",
                  "https://boards.greenhouse.io/acme/jobs/123#apply",
                  "https://boards.greenhouse.io/acme/jobs/123/",
                  "https://BOARDS.greenhouse.io/ACME/Jobs/123",
                  "http://boards.greenhouse.io/acme/jobs/123"):
        assert canonical_url(noisy) == base, noisy


def test_unusable_input_yields_no_opinion_rather_than_a_shared_key():
    """`None` must mean "no opinion", and callers must fail open."""
    for junk in ("", "   ", "not-a-url", "/relative/path"):
        assert canonical_url(junk) is None, junk
    assert content_key("", "Acme", "London") is None
    assert content_key("Engineer", "", "London") is None
    assert content_key("(Remote)", "Acme", "") is None      # parens stripped -> nothing left
    assert content_key("Remote", "Acme", "") is None        # pure noise -> nothing left


def test_the_same_role_in_two_cities_is_never_collapsed():
    """The single most consequential rule here. Location is part of the identity, on purpose."""
    assert (content_key("Backend Engineer", "Acme", "London")
            != content_key("Backend Engineer", "Acme", "Berlin"))


def test_arrangement_words_are_not_identity():
    """"Remote Senior Engineer" and "Senior Engineer" at one company are one posting."""
    plain = content_key("Senior Backend Engineer", "Acme", "")
    for variant in ("Remote Senior Backend Engineer",
                    "Fully Remote Senior Backend Engineer",
                    "Senior Backend Engineer (Remote)",
                    "Senior Backend Engineer (m/f/d)",
                    "  Senior   Backend  Engineer  ",
                    "Senior/Backend Engineer",
                    "SENIOR BACKEND ENGINEER"):
        assert content_key(variant, "Acme", "") == plain, variant


def test_legal_suffixes_are_stripped_but_never_the_whole_name():
    """"Acme" and "Acme Ltd" are one employer. A company actually named "Co" still exists."""
    plain = content_key("Engineer", "Acme", "")
    for suffixed in ("Acme Inc", "Acme Inc.", "Acme Ltd", "Acme GmbH", "Acme Kft."):
        assert content_key("Engineer", suffixed, "") == plain, suffixed

    # The guard is `while len(c) > 1` — a single-token company survives even if it is a suffix word.
    assert content_key("Engineer", "Co", "") == "co|engineer"
    assert content_key("Engineer", "AB", "") == "ab|engineer"


def test_remote_and_remote_emea_do_not_collapse():
    """The reference's own comment claims these are equal."""
    assert content_key("Backend Engineer", "Acme", "Remote") == "acme|backend engineer"
    assert content_key("Backend Engineer", "Acme", "Remote - EMEA") == "acme|backend engineer|emea"
    assert (content_key("Backend Engineer", "Acme", "Remote")
            != content_key("Backend Engineer", "Acme", "Remote - EMEA"))


def test_hybrid_location_keeps_the_city():
    """"Hybrid - Budapest" is a Budapest job."""
    assert content_key("Backend Engineer", "Acme", "Hybrid - Budapest") == "acme|backend engineer|budapest"
    assert content_key("Backend Engineer", "Acme", "Budapest (Hybrid)") == "acme|backend engineer|budapest"


# ── Differential fuzz against the live reference ──────────────────────────────────────────────
# The golden cases prove the inputs someone thought to write down.

REFERENCE = Path(r"d:\Projects\job-search")
_have_reference = (REFERENCE / "pipeline" / "lib" / "store.py").exists()


@pytest.mark.skipif(not _have_reference, reason=f"reference instance not present at {REFERENCE}")
def test_no_divergence_from_the_reference_on_random_input():
    """40,000 random strings through both implementations."""
    import random
    import string
    import sys

    if str(REFERENCE) not in sys.path:
        sys.path.insert(0, str(REFERENCE))
    from pipeline.lib.store import canonical_url as ref_url
    from pipeline.lib.store import content_key as ref_key

    rng = random.Random(1234)
    alphabet = string.ascii_letters + string.digits + " -_/().,|&+#?=%:éüа日"

    def rnd(n: int) -> str:
        return "".join(rng.choice(alphabet) for _ in range(rng.randint(0, n)))

    prefixes = ["", "https://", "http://", "https://www.", "https://job-boards.greenhouse.io/"]
    for _ in range(20_000):
        u = rng.choice(prefixes) + rnd(30)
        assert canonical_url(u) == ref_url(u), f"canonical_url diverged on {u!r}"

    for _ in range(20_000):
        t, c, loc = rnd(20), rnd(14), rnd(16)
        assert content_key(t, c, loc) == ref_key(t, c, loc), \
            f"content_key diverged on {(t, c, loc)!r}"
