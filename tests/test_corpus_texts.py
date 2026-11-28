"""A posting's text is kept beside it, not in it (`libs.corpus.texts`): 4,006 of a stored posting's 6,857
bytes were text, which the Jobs list never reads and the database kept in memory with every posting it read
(2026-11-24). Wherever a text is read or written, it is there, and where a posting goes, its text goes."""
from __future__ import annotations

import uuid
from datetime import timedelta

import pytest

from libs.corpus.repository import CorpusRepository
from libs.db import now
from libs.db.models import CorpusJob, CorpusText
from tests.conftest import needs_db

TEXT = "We build backend systems in Go and Postgres. " * 10


@pytest.fixture()
def corpus(db):
    return CorpusRepository(db), db


def _posting(repo: CorpusRepository) -> int:
    return repo.add(url=f"https://boards.greenhouse.io/acme/jobs/{uuid.uuid4().hex}", source="greenhouse",
                    title="Backend Engineer", company=f"Acme{uuid.uuid4().hex[:8]}", location="Berlin")


@needs_db
def test_a_postings_text_is_kept_beside_it_and_the_posting_holds_none_of_it(corpus):
    from libs.corpus.texts import texts

    repo, db = corpus
    job_id = _posting(repo)

    repo.set_description(job_id, TEXT)

    stored = db[CorpusJob.COLLECTION].find_one({"_id": job_id})
    assert "description" not in stored, "the posting the Jobs list reads carries no text"
    assert stored["described_at"] is not None and stored["language"] == "en", (
        "what is read off the text is still on the posting")
    assert texts(db, [job_id]) == {job_id: TEXT}



@needs_db
def test_an_archived_posting_takes_its_text_into_the_archive_and_out_of_the_database(corpus, tmp_path):
    from libs.corpus.archive import archive_expired, read_archive

    repo, db = corpus
    job_id = _posting(repo)
    repo.set_description(job_id, TEXT)
    db[CorpusJob.COLLECTION].update_one({"_id": job_id},
                                        {"$set": {"first_seen_at": now() - timedelta(days=40)}})

    result = archive_expired(db, directory=tmp_path)

    [archived] = [row for row in read_archive(result.path) if row["id"] == job_id]
    assert archived["description"] == TEXT, "the archive keeps the posting whole"
    assert db[CorpusText.COLLECTION].count_documents({"_id": job_id}) == 0, "no text left of a posting gone"
