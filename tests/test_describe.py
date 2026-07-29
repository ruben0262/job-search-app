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
