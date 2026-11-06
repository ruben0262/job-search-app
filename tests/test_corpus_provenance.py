"""How a posting got into the corpus — recorded, so it never has to be inferred again.

On 2026-08-08 an account with no proxy was receiving LinkedIn mechanical-engineering postings.
Answering "how did those get here" took an afternoon and produced an inference rather than a fact:
the harvest logged `ran 66 quer(ies)` for the other account, that account's config held 22 keywords
across 3 locations, 22×3 is 66, and the proxy-less account's line every pass was `has no proxy
credential`. Correct, probably — and unreadable.

These tests are about the columns that make it readable.
"""
from __future__ import annotations

import uuid

from libs.corpus.repository import CorpusRepository
from libs.db import get, insert
from libs.db.models import CorpusJob, User


def _ids(db, conditions: list[dict]) -> list[int]:
    return db[CorpusJob.COLLECTION].distinct("_id", {"$and": conditions})


def test_a_row_records_the_search_term_that_found_it(db):
    """LinkedIn's matching is looser than its query, and that gap is the thing nobody could see."""
    job_id = CorpusRepository(db).add(
        url=f"https://www.linkedin.com/jobs/view/{uuid.uuid4()}", source="linkedin",
        title="Graduate Mechanical Design Engineer",
        discovered_via="graduate software engineer")

    row = get(db, CorpusJob, job_id)
    assert row.discovered_via == "graduate software engineer"
    assert row.title != row.discovered_via, "the whole point is that these differ"


def test_deleting_an_account_does_not_delete_the_corpus(db):
    """Other accounts are matched against these rows, and a posting outlives whoever found it."""
    from libs.db import delete_account

    user = insert(db, User(email=f"{uuid.uuid4()}@example.com", password_hash="x", status="active"))
    job_id = CorpusRepository(db).add(
        url=f"https://www.linkedin.com/jobs/view/{uuid.uuid4()}", source="linkedin",
        title="Engineer")

    delete_account(db, user.id)

    assert get(db, User, user.id) is None
    assert get(db, CorpusJob, job_id) is not None, "the posting survives"


def test_the_adapter_tags_every_link_with_its_query():
    """Tagged where the query and its results are both in scope, or it cannot be tagged at all."""
    from services.harvester.sources.base import JobLink

    link = JobLink(url="https://www.linkedin.com/jobs/view/3", title="Stress Engineer",
                   source="linkedin", found_via="graduate software engineer")

    assert link.as_corpus_row()["discovered_via"] == "graduate software engineer"


def test_a_feed_with_no_query_stores_null_rather_than_an_empty_string():
    """A feed source has no search term. NULL says that; "" reads as a term somebody typed."""
    from services.harvester.sources.base import JobLink

    link = JobLink(url="https://example.com/feed/1", title="Engineer", source="remoteok")

    assert link.as_corpus_row()["discovered_via"] is None


def test_the_linkedin_ingest_records_what_returned_each_posting(db):
    """A pass that does not carry the term through records nothing.

    Asserted by running the ingest and reading the row, not by grepping the module for a call.
    The first version of this test matched a literal line of source, which would have passed while
    the value was wrong and failed on a rename that changed nothing.
    """
    from services.harvester.linkedin import _ingest
    from services.harvester.sources.base import JobLink

    url = f"https://www.linkedin.com/jobs/view/{uuid.uuid4()}"
    new = _ingest(CorpusRepository(db),
                  [JobLink(url=url, title="Mechanical Design Engineer", source="linkedin",
                           found_via="graduate software engineer")])

    assert new == 1
    row = db[CorpusJob.COLLECTION].find_one({"url": url})
    assert row["discovered_via"] == "graduate software engineer", "what returned it"


# ── abandoning a posting we cannot read ───────────────────────────────────────────────────────

def test_a_posting_is_abandoned_after_two_failed_reads(db):
    """Two, and it is never fetched or matched again.

    Before this the describer had no memory: a page that returned no usable text came back every
    fifteen minutes forever. Measured 2026-08-08, the same ~56 LinkedIn ids for a week, each
    attempt on a user's own metered residential proxy.
    """
    from libs.corpus.repository import DESCRIBE_MAX_FAILURES, awaiting_description_conditions

    corpus = CorpusRepository(db)
    job_id = corpus.add(url=f"https://www.linkedin.com/jobs/view/{uuid.uuid4()}",
                        source="linkedin", title="Unreadable Posting")
    db[CorpusJob.COLLECTION].update_one({"_id": job_id}, {"$set": {"description": "a snippet only"}})

    assert corpus.record_describe_failure(job_id) == 1
    assert job_id in _ids(db, awaiting_description_conditions()), \
        "one failure is a blip and deserves another IP"

    assert corpus.record_describe_failure(job_id) == DESCRIBE_MAX_FAILURES

    assert job_id not in _ids(db, awaiting_description_conditions())


def test_a_successful_read_clears_the_count(db):
    """Consecutive means consecutive. A page that answers has not failed twice in a row."""
    corpus = CorpusRepository(db)
    job_id = corpus.add(url=f"https://www.linkedin.com/jobs/view/{uuid.uuid4()}",
                        source="linkedin", title="Intermittent Posting")

    corpus.record_describe_failure(job_id)
    assert get(db, CorpusJob, job_id).describe_failures == 1
    corpus.clear_describe_failures(job_id)

    assert get(db, CorpusJob, job_id).describe_failures == 0


def test_a_posting_is_anybody_s_candidate_only_once_its_whole_text_is_read(db):
    """Matched on its title, a job reached Jobs pages and alerts, then left them when its text came in:
    another language, a way of working not wanted, a salary under the minimum (owner, 2026-11-06). So
    one still waiting, one with a teaser and one given up on are nobody's; the one read is."""
    from libs.corpus.repository import DESCRIBE_MAX_FAILURES, MIN_DESCRIPTION
    from libs.tenant.filters import UserFilters, candidate_conditions

    corpus = CorpusRepository(db)
    _waiting, teaser, read, given_up = (
        corpus.add(url=f"https://www.linkedin.com/jobs/view/{uuid.uuid4()}", source="linkedin", title=title)
        for title in ("Waiting", "Teaser", "Read", "Given up on"))
    corpus.set_description(teaser, "x" * (MIN_DESCRIPTION - 1))
    corpus.set_description(read, "x" * MIN_DESCRIPTION)
    for _ in range(DESCRIBE_MAX_FAILURES):
        corpus.record_describe_failure(given_up)

    assert _ids(db, candidate_conditions(UserFilters.from_config(None, max_age_hours=24))) == [read]
