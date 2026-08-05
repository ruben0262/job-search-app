"""The description pass: which rows it picks up, what it writes, and what it refuses to erase.

This is the stage that decides what the labeller reads. Before it existed the platform scored jobs
from whatever teaser a listing API returned, so the tests that matter here are about *selection*
(a thin row is picked up, a trimmed one is not) and about *not losing information* (a fetch that
saw nothing must not clear what an earlier fetch knew).

Needs a live Postgres; skips without `DATABASE_URL`.
"""
from __future__ import annotations

import os
import uuid

import pytest

pytest.importorskip("sqlalchemy")
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from libs.corpus import postings
from libs.corpus.repository import CorpusRepository
from libs.db.models import CorpusJob
from services.harvester.describe import describe_pending

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="DATABASE_URL not set (no live Postgres)")

JD = ('<div class="show-more-less-html__markup">' + "A real description. " * 30 + "</div>")


@pytest.fixture(autouse=True)
def _no_backoff(monkeypatch):
    """LinkedIn's retry loop sleeps two seconds six times. Real seconds, in every test that hits a
    LinkedIn row."""
    monkeypatch.setattr(postings.time, "sleep", lambda _seconds: None)


@pytest.fixture()
def repo():
    engine = create_engine(os.environ["DATABASE_URL"])
    conn = engine.connect()
    trans = conn.begin()
    session = Session(bind=conn, join_transaction_mode="create_savepoint")
    try:
        yield CorpusRepository(session), session
    finally:
        session.close()
        trans.rollback()
        conn.close()


class FakeResponse:
    def __init__(self, text: str = "", status_code: int = 200):
        self.text = text
        self.status_code = status_code
        self.url = "https://example.com"


class FakeClient:
    def __init__(self, text: str = JD, status_code: int = 200):
        self.text, self.status_code = text, status_code
        self.requested: list[str] = []

    def get(self, url: str, **_: object) -> FakeResponse:
        self.requested.append(url)
        return FakeResponse(self.text, self.status_code)


def _linkedin(repo: CorpusRepository, **kwargs) -> int:
    return repo.add(url=f"https://www.linkedin.com/jobs/view/{uuid.uuid4().int % 10**9}",
                    source="linkedin", title="Engineer", company="Acme", **kwargs)


def _board(repo: CorpusRepository, **kwargs) -> int:
    return repo.add(url=f"https://boards.greenhouse.io/acme/jobs/{uuid.uuid4().hex}",
                    source="greenhouse", title="Engineer", company="Acme", **kwargs)


# ── which rows are picked up ──────────────────────────────────────────────────────────────────

def test_a_row_with_no_description_is_awaiting_one(repo):
    corpus, session = repo
    job_id = _board(corpus)
    session.flush()
    assert job_id in [job.id for job in corpus.awaiting_description()]


def test_a_row_whose_snippet_is_a_teaser_is_picked_up_again(repo):
    """**The case the platform was silently getting wrong.** The row has text, so nothing looked
    broken — it was 180 characters of advert, and every label came from it."""
    corpus, session = repo
    job_id = _board(corpus)
    corpus.set_description(job_id, "Join our fast-growing team! " * 5)
    session.flush()
    assert job_id in [job.id for job in corpus.awaiting_description()]


def test_a_row_with_a_real_description_is_left_alone(repo):
    corpus, session = repo
    job_id = _board(corpus)
    corpus.set_description(job_id, "A genuine job description. " * 40)
    session.flush()
    assert job_id not in [job.id for job in corpus.awaiting_description()]


def test_a_trimmed_row_is_never_re_fetched(repo):
    """Retention dropped that text on purpose. Re-fetching it would undo the sweep every cycle and
    the corpus would never shrink."""
    corpus, session = repo
    job_id = _board(corpus)
    session.get(CorpusJob, job_id).trimmed_at = session.scalar(select(func.now()))
    session.flush()
    assert job_id not in [job.id for job in corpus.awaiting_description()]


def test_the_two_passes_do_not_take_each_other_s_work(repo):
    """LinkedIn rows can only be filled by a caller holding a proxy lease. The split is what stops
    the free shared pass burning its retries on pages that will always 403 it."""
    corpus, session = repo
    linkedin_id, board_id = _linkedin(corpus), _board(corpus)
    session.flush()

    free = [job.id for job in corpus.awaiting_description(linkedin=False)]
    proxied = [job.id for job in corpus.awaiting_description(linkedin=True)]

    assert board_id in free and linkedin_id not in free
    assert linkedin_id in proxied and board_id not in proxied


# ── what the pass writes ──────────────────────────────────────────────────────────────────────

def test_a_fetched_description_replaces_the_teaser(repo):
    corpus, session = repo
    job_id = _board(corpus)
    corpus.set_description(job_id, "teaser")
    session.flush()

    report = describe_pending(corpus, FakeClient(), linkedin=False)
    session.flush()

    assert report.described == 1
    assert "A real description." in session.get(CorpusJob, job_id).description


def test_the_replaced_description_invalidates_the_vector(repo):
    """A vector computed from the teaser does not match the text it now sits beside, and nothing
    downstream would raise — it would just score wrong."""
    corpus, session = repo
    job_id = _board(corpus)
    corpus.set_description(job_id, "teaser")
    corpus.set_embedding(job_id, vector=b"\x00" * 8,
                         text_sha256=session.get(CorpusJob, job_id).text_sha256,
                         model="m", dim=2, version="v1")
    session.flush()

    describe_pending(corpus, FakeClient(), linkedin=False)
    session.flush()
    assert session.get(CorpusJob, job_id).embedding is None


def test_a_fetch_that_returns_nothing_leaves_the_row_for_the_next_pass(repo):
    corpus, session = repo
    job_id = _board(corpus)
    corpus.set_description(job_id, "teaser")
    session.flush()

    report = describe_pending(corpus, FakeClient("", 429), linkedin=False)
    session.flush()

    assert report.described == 0 and report.still_thin == 1
    assert session.get(CorpusJob, job_id).description == "teaser"
    assert job_id in [job.id for job in corpus.awaiting_description(linkedin=False)]


def test_the_outcome_tally_says_why_a_pass_found_nothing(repo):
    """A run that fell back to snippets for everything looks identical from outside to one that
    worked. The tally is the difference."""
    corpus, session = repo
    _board(corpus)
    session.flush()

    report = describe_pending(corpus, FakeClient("", 403), linkedin=False)
    assert report.outcomes.get("generic_http_403") == 1


# ── the rule about not erasing ────────────────────────────────────────────────────────────────

def test_a_none_never_overwrites_a_known_value(repo):
    """Pages answer inconsistently — the guest fragment has the applicant count, the full page has
    the apply button. A fetch that did not see a field must not erase what an earlier one did.
    Clearing `workplace` this way would silently un-filter a job the user had excluded."""
    corpus, session = repo
    job_id = _board(corpus, workplace="onsite", applicants=42)
    session.flush()

    corpus.set_posting_metadata(job_id, workplace=None, applicants=None, reposted=True)
    session.flush()

    job = session.get(CorpusJob, job_id)
    assert (job.workplace, job.applicants, job.reposted) == ("onsite", 42, True)


def test_metadata_is_kept_even_when_the_description_came_back_thin(repo):
    """An applicant count and a workplace tag are what the card and the filter read, and they
    arrive on responses that carried no usable JD."""
    corpus, session = repo
    job_id = _linkedin(corpus)
    session.flush()

    fragment = ('<span class="num-applicants__caption"> 12 applicants </span>'
                '<h1>Engineer (Hybrid)</h1>')
    describe_pending(corpus, FakeClient(fragment), linkedin=True)
    session.flush()

    job = session.get(CorpusJob, job_id)
    assert (job.applicants, job.workplace) == (12, "hybrid")


def _posting(repo):
    """One LinkedIn row to refresh, flushed so the repository can find it."""
    corpus, session = repo
    job_id = _linkedin(corpus)
    session.flush()
    return session, session.get(CorpusJob, job_id)



# ── the freshness check ───────────────────────────────────────────────────────────────────────

class FakeMetaResponse:
    def __init__(self, text: str = "", status_code: int = 200):
        self.text, self.status_code = text, status_code
        self.url = "https://www.linkedin.com/jobs/view/1"


class FakeMetaClient:
    """Answers every request with the same page, and records that it was asked."""

    def __init__(self, text: str, status_code: int = 200):
        self.text, self.status_code = text, status_code
        self.requested: list[str] = []

    def get(self, url, **_):
        self.requested.append(url)
        return FakeMetaResponse(self.text, self.status_code)

    def stream(self, _method, url, **_):
        from contextlib import nullcontext
        self.requested.append(url)

        class Streamed(FakeMetaResponse):
            def iter_bytes(inner):
                yield inner.text.encode()

        return nullcontext(Streamed(self.text, self.status_code))


OPEN_POSTING = ('<span class="num-applicants__caption"> 12 applicants </span>'
                '<span class="posted-time-ago__text"> 3 hours ago </span>' + "x" * 300)
CLOSED_POSTING = "<p>No longer accepting applications</p>" + "x" * 300


def test_a_closed_posting_stops_the_build_before_anything_is_spent(repo):
    """**The most annoying possible failure is the one where everything succeeds.** The document
    arrives, it is good, and the apply link says the job closed three days ago."""
    from services.docgen.refresh import PostingClosed, refresh_before_build

    session, job = _posting(repo)
    with pytest.raises(PostingClosed):
        refresh_before_build(session, job, FakeMetaClient(CLOSED_POSTING))


def test_a_live_posting_lets_the_build_proceed(repo):
    from services.docgen.refresh import refresh_before_build

    session, job = _posting(repo)
    meta = refresh_before_build(session, job, FakeMetaClient(OPEN_POSTING))
    assert meta["accepting"] is True


def test_a_failed_fetch_never_reads_as_a_closed_job(repo):
    """A proxy hiccup must not present to the user as "that job is gone". Only a page that says so
    stops a build."""
    from services.docgen.refresh import refresh_before_build

    session, job = _posting(repo)
    assert refresh_before_build(session, job, FakeMetaClient("", 429))["accepting"] is None


def test_the_refreshed_metadata_goes_back_to_the_shared_corpus(repo):
    """The user paid for the request. Everyone gets the applicant count."""
    from services.docgen.refresh import refresh_before_build

    session, job = _posting(repo)
    refresh_before_build(session, job, FakeMetaClient(OPEN_POSTING))
    session.flush()
    assert job.applicants == 12


# ── metadata on rows that already have a description ──────────────────────────────────────────

GUEST = ('<span class="num-applicants__caption"> 42 applicants </span>'
         '<span class="posted-time-ago__text"> 3 hours ago </span>'
         '<h1>Engineer (Hybrid)</h1>' + "x" * 300)


def test_a_linkedin_row_with_a_description_but_no_signals_is_picked_up(repo):
    """**A different question from "is it thin", and the corpus was full of the difference.**
    JobSpy returns a usable description for most LinkedIn postings, so those rows never looked
    thin — and all 3,494 of them still had a null applicant count, posted time, repost flag and
    workplace tag, because nothing had ever opened the guest page."""
    corpus, session = repo
    job_id = _linkedin(corpus)
    corpus.set_description(job_id, "A genuine job description. " * 40)
    session.flush()

    assert job_id not in [job.id for job in corpus.awaiting_description(linkedin=True)]
    assert job_id in [job.id for job in corpus.awaiting_metadata()]


def test_the_refresh_writes_every_signal_the_page_carries(repo):
    from services.harvester.describe import refresh_metadata

    corpus, session = repo
    job_id = _linkedin(corpus)
    corpus.set_description(job_id, "A genuine job description. " * 40)
    session.flush()

    report = refresh_metadata(corpus, FakeClient(GUEST), limit=10)
    session.flush()

    job = session.get(CorpusJob, job_id)
    assert report.metadata_written >= 1
    assert (job.applicants, job.workplace) == (42, "hybrid")
    assert job.posted_at is not None


def test_a_row_that_was_refreshed_is_not_asked_again(repo):
    """The "never looked" test is the absence of both signals, not a timestamp — otherwise every
    row would be re-fetched every hour forever."""
    from services.harvester.describe import refresh_metadata

    corpus, session = repo
    job_id = _linkedin(corpus)
    corpus.set_description(job_id, "A genuine job description. " * 40)
    session.flush()

    refresh_metadata(corpus, FakeClient(GUEST), limit=10)
    session.flush()
    assert job_id not in [job.id for job in corpus.awaiting_metadata()]


def test_a_board_row_is_never_in_the_metadata_queue(repo):
    """Only LinkedIn exposes any of these. A Greenhouse page has no applicant count to read, so
    fetching it would be a proxied request that cannot succeed."""
    corpus, session = repo
    job_id = _board(corpus)
    corpus.set_description(job_id, "A genuine job description. " * 40)
    session.flush()
    assert job_id not in [job.id for job in corpus.awaiting_metadata()]


# ── a closed posting must not reach a feed ────────────────────────────────────────────────────

CLOSED_GUEST = ('<p>No longer accepting applications</p>'
                '<span class="posted-time-ago__text"> 1 month ago </span>' + "x" * 300)


def test_a_closed_posting_is_marked_and_leaves_the_queue(repo):
    """**The failure the user saw.** A month-old, already-closed LinkedIn posting arrived as a live
    card with two build buttons on it. The page said so plainly and nothing read it."""
    from services.harvester.describe import refresh_metadata

    corpus, session = repo
    job_id = _linkedin(corpus)
    corpus.set_description(job_id, "A genuine job description. " * 40)
    session.flush()

    report = refresh_metadata(corpus, FakeClient(CLOSED_GUEST), limit=10)
    session.flush()

    assert report.closed == 1
    assert session.get(CorpusJob, job_id).closed_at is not None
    assert job_id not in [job.id for job in corpus.awaiting_metadata()]


def test_marking_closed_keeps_the_row_and_its_description(repo):
    """Not a deletion and not a trim. A CV already built against it keeps its evidence; what stops
    is delivery."""
    corpus, session = repo
    job_id = _linkedin(corpus)
    corpus.set_description(job_id, "A genuine job description. " * 40)
    session.flush()

    corpus.mark_closed(job_id)
    session.flush()

    job = session.get(CorpusJob, job_id)
    assert job.description and job.trimmed_at is None


def test_closing_twice_does_not_move_the_timestamp(repo):
    """A listing that reopens is rare; a timestamp that drifts is worse than a stale one."""
    corpus, session = repo
    job_id = _linkedin(corpus)
    session.flush()

    corpus.mark_closed(job_id)
    session.flush()
    first = session.get(CorpusJob, job_id).closed_at

    corpus.mark_closed(job_id)
    session.flush()
    session.expire_all()
    assert session.get(CorpusJob, job_id).closed_at == first


def test_the_pass_stops_when_it_runs_out_of_time(repo, monkeypatch):
    """**A run that outlives its timer stops the next one starting.**

    `linkedin.timer` ticks every fifteen minutes and `Type=oneshot` means an active run blocks the
    next. Measured 2026-08-05: a pass still going after twenty minutes, the timer reporting no next
    run, and five hours with no LinkedIn rows ingested while every other source stayed current.

    A count cannot bound this — one row is a proxied fetch whose cost varies by an order of
    magnitude — so the pass bounds itself in seconds. What is left stays thin and is first in line
    next time, which is the same fail-forward the rest of the pipeline uses.

    The clock is driven rather than slept through: this file's autouse fixture no-ops
    `time.sleep` on the shared module, so a test that waited for real time would measure nothing.
    """
    from services.harvester import describe as describe_module

    corpus, session = repo
    for _ in range(8):
        corpus.add(url=f"https://www.linkedin.com/jobs/view/{uuid.uuid4().int % 10**9}",
                   source="linkedin", title="Engineer", company="Acme")
    session.flush()

    # A clock that advances a second per reading: the budget below is spent after two fetches.
    ticks = iter(range(0, 1000))
    monkeypatch.setattr(describe_module, "monotonic", lambda: float(next(ticks)))

    class Fetcher:
        def get(self, url, **_kwargs):
            return FakeResponse("<div>" + "x" * 400 + "</div>")

    report = describe_pending(corpus, Fetcher(), linkedin=True, limit=8, budget_seconds=2.5)

    assert report.attempted < 8, "the pass must stop rather than outlive its timer"
    # And nothing was lost: whatever was not described is still waiting for the next pass.
    assert len(corpus.awaiting_description(limit=20, linkedin=True)) == 8 - report.described


def test_no_budget_means_the_pass_runs_to_completion(repo):
    """The shared cycle has its own scheduling and passes no budget, so the bound must be opt-in
    rather than a default that quietly truncates it."""
    corpus, session = repo
    for _ in range(3):
        corpus.add(url=f"https://www.linkedin.com/jobs/view/{uuid.uuid4().int % 10**9}",
                   source="linkedin", title="Engineer", company="Acme")
    session.flush()

    class Fast:
        def get(self, url, **_kwargs):
            return FakeResponse("<div>" + "x" * 400 + "</div>")

    report = describe_pending(corpus, Fast(), linkedin=True, limit=3)
    assert report.attempted == 3
