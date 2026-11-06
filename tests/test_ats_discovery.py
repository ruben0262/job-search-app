"""Learning which company boards to poll from postings the corpus already holds.

**The list was a closed loop.** Tokens came only from hiring.cafe's `board_token` field, and
hiring.cafe is queried with our users' keywords — so the boards polled were the boards our existing
users had already surfaced. Sampled 2026-08-09: all 336 live tokens were software startups, on a
platform where nine of seventeen enabled sources are ATS boards polling that list. A mechanical
engineer had 2.9% of the corpus to look at, and no amount of tuning could change it, because the
tuning ran against a company list that could only describe the users it already had.

Every other source carries the same signal in the posting URL. Measured 2026-08-10: 592 corpus
companies with an ATS-shaped URL, 171 of them boards nobody polled.
"""
from __future__ import annotations

import uuid
from datetime import timedelta

import pytest

from libs.corpus.ats import AtsRepository
from libs.corpus.ats_urls import token_from_url, tokens_from_urls
from libs.db import insert, now
from libs.db.models import CorpusJob
from services.harvester.cycle import _discover_ats_tokens

# ── reading a board out of a link ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize(("url", "expected"), [
    ("https://boards.greenhouse.io/monzo/jobs/4012345", ("greenhouse", "monzo")),
    ("https://job-boards.greenhouse.io/ricardo/jobs/99", ("greenhouse", "ricardo")),
    ("https://jobs.lever.co/cosworth/8f2c-11", ("lever", "cosworth")),
    ("https://jobs.ashbyhq.com/mclaren/1a2b", ("ashby", "mclaren")),
    ("https://jobs.ashbyhq.com/cytora.com/1a2b", ("ashby", "cytora.com")),
    ("https://careers.smartrecruiters.com/BoschGroup/743999", ("smartrecruiters", "boschgroup")),
    ("https://williamsracing.recruitee.com/o/design-engineer", ("recruitee", "williamsracing")),
    ("https://acme.breezy.hr/p/1234", ("breezy", "acme")),
    ("https://apply.workable.com/jaguarlandrover/j/ABC123", ("workable", "jaguarlandrover")),
    ("https://zf.wd3.myworkdayjobs.com/en-US/ZF_Careers/job/Engineer", ("workday", "zf")),
    ("https://acme.jobs.personio.de/job/123", ("personio", "acme")),
])
def test_a_posting_url_names_its_company_board(url, expected):
    assert token_from_url(url) == expected


@pytest.mark.parametrize("url", [
    "https://www.linkedin.com/jobs/view/12345",
    "https://example.com/careers/engineer",
    "https://boards.greenhouse.io/",
    "https://jobs.lever.co/",
    None,
    "",
])
def test_a_url_that_names_no_board_is_skipped(url):
    """A wrong token is a board that 404s every cycle, so anything unclear is skipped."""
    assert token_from_url(url) is None


def test_the_vendors_own_pages_are_not_mistaken_for_companies():
    """`boards.greenhouse.io/embed/...` and friends are the ATS, not somebody's board."""
    for url in ("https://boards.greenhouse.io/search?q=x",
                "https://jobs.ashbyhq.com/api/non-user-graphql",
                "https://careers.smartrecruiters.com/company/about"):
        pair = token_from_url(url)
        assert pair is None or pair[1] not in {"search", "api", "company"}


def test_the_same_board_seen_twice_is_one_token():
    urls = ["https://boards.greenhouse.io/monzo/jobs/1",
            "https://boards.greenhouse.io/monzo/jobs/2",
            "https://jobs.lever.co/cosworth/9"]

    assert tokens_from_urls(urls) == [("greenhouse", "monzo"), ("lever", "cosworth")]


# ── the loop it breaks, end to end ────────────────────────────────────────────────────────────

def _posting(db, url: str, *, source: str = "adzuna", age_hours: int = 1, apply: str | None = None) -> None:
    insert(db, CorpusJob(url=url, title="Design Engineer", source=source, application_url=apply,
                         first_seen_at=now() - timedelta(hours=age_hours),
                         posted_at=now() - timedelta(hours=age_hours)))


def test_a_board_named_by_a_posting_becomes_a_board_we_poll(db):
    """The whole point: a company reaches the ATS tier without anybody adding it.

    The posting arrives on LinkedIn or Adzuna because a user's own keywords found it. Its URL
    names a Greenhouse board. From the next cycle that board is polled directly, which is how a
    new user from a new field widens the corpus for themselves.
    """
    token = f"ricardo{uuid.uuid4().hex[:8]}"
    _posting(db, f"https://boards.greenhouse.io/{token}/jobs/4012345")
    ats = AtsRepository(db)
    assert token not in ats.live_tokens("greenhouse")

    found = _discover_ats_tokens(db, ats)

    assert found == 1
    assert token in ats.live_tokens("greenhouse")


def test_a_board_already_polled_is_not_enrolled_twice(db):
    """It runs every cycle over overlapping rows, so it has to be idempotent."""
    token = f"cosworth{uuid.uuid4().hex[:8]}"
    _posting(db, f"https://jobs.lever.co/{token}/8f2c")
    ats = AtsRepository(db)

    first = _discover_ats_tokens(db, ats)
    second = _discover_ats_tokens(db, ats)

    assert first == 1
    assert second == 0, "the second pass re-enrolled boards it had already added"
    assert ats.live_tokens("lever").count(token) == 1


def test_discovery_reads_every_source_not_only_the_one_that_used_to_feed_it(db):
    """`harvest_from_hits` read hiring.cafe alone, which is what made the list a closed loop. LinkedIn
    names a board by where it is applied to, its own link being LinkedIn's."""
    from_linkedin = f"mclaren{uuid.uuid4().hex[:8]}"
    from_adzuna = f"jlr{uuid.uuid4().hex[:8]}"
    _posting(db, "https://www.linkedin.com/jobs/view/4012345", source="linkedin",
             apply=f"https://jobs.ashbyhq.com/{from_linkedin}/1a2b")
    _posting(db, f"https://{from_adzuna}.recruitee.com/o/design-engineer", source="adzuna")

    _discover_ats_tokens(db, AtsRepository(db))

    ats = AtsRepository(db)
    assert from_linkedin in ats.live_tokens("ashby")
    assert from_adzuna in ats.live_tokens("recruitee")


def test_a_board_a_posting_is_applied_to_is_one_we_poll(db):
    """A board's posting often sends its applicants to another company's board: 29 boards on the laptop's
    corpus were named only that way, a tenth more than the 291 polled (2026-11-06)."""
    token = f"wd{uuid.uuid4().hex[:8]}"
    _posting(db, "https://justjoin.it/job-offer/acme-design-engineer", source="justjoinit",
             apply=f"https://{token}.wd3.myworkdayjobs.com/en-US/careers/job/Berlin/Design_123")

    _discover_ats_tokens(db, AtsRepository(db))

    assert token in AtsRepository(db).live_tokens("workday")


def test_linkedin_postings_with_no_apply_link_do_not_crowd_out_the_ones_that_name_a_board(db, monkeypatch):
    """~40,000 LinkedIn postings a day, each linking to LinkedIn: read first, they filled the row cap and no
    board's posting was read at all."""
    monkeypatch.setattr("services.harvester.cycle.ATS_DISCOVERY_MAX_ROWS", 3)
    for n in range(5):
        _posting(db, f"https://www.linkedin.com/jobs/view/{4_000_000 + n}", source="linkedin")
    # Last whichever order the rows are read in: by source, by when found, or as stored.
    token = f"brembo{uuid.uuid4().hex[:8]}"
    _posting(db, f"https://apply.workable.com/{token}/j/ABC123", source="workable", age_hours=0)

    _discover_ats_tokens(db, AtsRepository(db))

    assert token in AtsRepository(db).live_tokens("workable")


# ── the companies postings name, looked for by name (owner, 2026-11-06) ──────────────────────────

def _named(db, company: str, title: str, *, source: str = "linkedin") -> None:
    insert(db, CorpusJob(url=f"https://www.linkedin.com/jobs/view/{uuid.uuid4().int % 10**12}", title=title,
                         company=company, source=source, first_seen_at=now()))


def _boards(monkeypatch, listed: dict[tuple[str, str], list[str]], refused=None) -> list[tuple[str, str]]:
    """Every ATS answers from `listed`, `{(ats, board): [titles]}`, an empty board otherwise; a board in
    `refused`, `{(ats, board): "HTTP 429"}`, answers that once. Returns every board asked, in order."""
    from services.harvester import cycle
    from services.harvester.sources.base import JobLink

    asked, refused = [], dict(refused or {})

    def answering(ats: str):
        def fetch(token: str, _etag=None):
            asked.append((ats, token))
            if (ats, token) in refused:
                raise RuntimeError(refused.pop((ats, token)))
            return [JobLink(url=f"https://{ats}.example/{token}/{n}", title=title, source=ats)
                    for n, title in enumerate(listed.get((ats, token), []))], None
        return fetch

    monkeypatch.setattr(cycle, "FETCHERS", {ats: answering(ats) for ats in cycle.FETCHERS})
    return asked


def _run(db, *boards: str):
    from services.harvester.cycle import run_cycle

    return run_cycle(db, source_configs={board: {"enabled": True} for board in boards},
                     run_source=lambda *_: [], describe=False)


def test_a_company_a_posting_names_has_its_board_found_and_polled(db, monkeypatch):
    """Links named one new board in three days, while the companies postings name had hundreds (2026-11-06).
    A board found by the company's name counts when it lists a job that company's posting here is, and the
    run polls it from then on; one of the same name listing none is another company's."""
    _named(db, "Acme  Robotics", "Backend Engineer (m/w/d)")
    asked = _boards(monkeypatch, {("greenhouse", "acmerobotics"): ["Backend Engineer", "Designer"],
                                  ("lever", "acme-robotics"): ["Store Manager"]})

    report = _run(db, "greenhouse", "lever", "ashby", "himalayas")

    ats = AtsRepository(db)
    assert report.ats_boards_by_name == 1
    assert ats.live_tokens("greenhouse") == ["acmerobotics"], "what the run polls (`gather_ats`)"
    assert ats.names("greenhouse") == {"acmerobotics": "Acme  Robotics"}, "named as the posting names it"
    assert not ats.live_tokens("lever"), "a board of that name with none of its jobs is somebody else's"
    assert {board for board, _ in asked} == {"greenhouse", "lever", "ashby"}, "on the boards this run reads"


def test_a_company_is_looked_for_once_and_again_only_when_the_look_up_failed(db, monkeypatch):
    """A guess a run costs requests, so a company is asked once, found or not. Too many requests is no
    answer, so asked again; a board refusing the name is one. A company with a board already, or named only
    by a board's own posting, is not asked at all."""
    _named(db, "Acme", "Welder")
    _named(db, "Globex", "Welder")
    _named(db, "Hooli", "Welder")
    _named(db, "Initech", "Welder", source="greenhouse")
    AtsRepository(db).add_tokens([("lever", "umbrella")])
    _named(db, "Umbrella", "Welder")
    asked = _boards(monkeypatch, {}, refused={("greenhouse", "globex"): "HTTP 429",
                                              ("greenhouse", "hooli"): "HTTP 403"})

    _run(db, "greenhouse")
    assert sorted(asked) == [("greenhouse", "acme"), ("greenhouse", "globex"), ("greenhouse", "hooli")]
    asked.clear()
    _run(db, "greenhouse")
    assert asked == [("greenhouse", "globex")], "too many requests, so asked again; Acme and Hooli answered"
    asked.clear()
    _run(db, "greenhouse")
    assert asked == []


def test_a_run_looks_for_as_many_as_its_time_allows_and_the_next_run_goes_on(db, monkeypatch):
    """Last in the run, in its own share of it (`BOARD_LOOKUP_BUDGET_SECONDS`): the rest waits, the run
    does not."""
    _named(db, "Acme", "Welder")
    asked = _boards(monkeypatch, {("greenhouse", "acme"): ["Welder"]})
    monkeypatch.setattr("services.harvester.cycle.BOARD_LOOKUP_BUDGET_SECONDS", 0)

    assert _run(db, "greenhouse").ats_boards_by_name == 0
    assert asked == []

    monkeypatch.setattr("services.harvester.cycle.BOARD_LOOKUP_BUDGET_SECONDS", 60)
    assert _run(db, "greenhouse").ats_boards_by_name == 1
