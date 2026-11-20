"""The schema in `libs.db`: what `ensure_schema` makes true, and what deleting an account removes.

There is no migration history to drift from any more (ROADMAP Phase 9). What can still go wrong is
between the declarations and the database — a collection nobody registered, a rule nothing enforces,
an index two versions fight over — and between an account and what it leaves behind.
"""
from __future__ import annotations

import importlib
import inspect
import pkgutil
import threading
import uuid

import pytest

from tests.conftest import needs_db


def test_every_collection_declared_is_registered():
    """`MODELS` is the one list `ensure_schema`, `delete_account` and the backup read. A document class
    left out of it gets no indexes, survives its account's deletion, and is missing from every backup."""
    import libs.db.models
    from libs.db import Document
    from libs.db.models import MODELS

    declared = set()
    for module in pkgutil.iter_modules(libs.db.models.__path__):
        for _, cls in inspect.getmembers(importlib.import_module(f"libs.db.models.{module.name}"),
                                         inspect.isclass):
            if issubclass(cls, Document) and cls is not Document and cls.__module__.startswith("libs.db"):
                declared.add(cls)
    assert declared == set(MODELS), f"not in MODELS: {sorted(c.__name__ for c in declared - set(MODELS))}"


def test_a_document_with_a_field_nobody_declared_is_refused_on_read():
    """Loud rather than dropped: a misspelt field must not vanish between a write and the next read."""
    from libs.db.models import User

    with pytest.raises(TypeError):
        User.from_doc({"_id": uuid.uuid4(), "email": "a@example.com", "password_hash": "x", "emial": "typo"})


@needs_db
def test_the_schema_is_applied_twice_without_complaint_and_enforces_its_rules(db):
    from pymongo.errors import DuplicateKeyError, WriteError

    from libs.db import ensure_schema, insert
    from libs.db.models import MODELS, QueueJob, User

    ensure_schema(db)
    for model in MODELS:
        names = {index["name"] for index in db[model.COLLECTION].list_indexes()}
        assert {index.document["name"] for index in model.INDEXES} <= names, model.COLLECTION

    user = insert(db, User(email="a@example.com", password_hash="x"))
    with pytest.raises(DuplicateKeyError):
        insert(db, User(email="a@example.com", password_hash="y"))
    with pytest.raises(WriteError):
        db[User.COLLECTION].insert_one(User(email="b@example.com", password_hash="x", status="gone").to_doc())

    # Five enqueues of the same pending work are one job; finishing it frees the slot.
    first = insert(db, QueueJob(lane="docgen", user_id=user.id, dedup_key="k"))
    with pytest.raises(DuplicateKeyError):
        insert(db, QueueJob(lane="docgen", user_id=user.id, dedup_key="k"))
    db[QueueJob.COLLECTION].update_one({"_id": first.id}, {"$set": {"state": "done"}})
    assert insert(db, QueueJob(lane="docgen", user_id=user.id, dedup_key="k")).id > first.id
    # Jobs with no key never collide with each other.
    insert(db, QueueJob(lane="docgen", user_id=user.id))
    insert(db, QueueJob(lane="docgen", user_id=user.id))


@needs_db
def test_an_index_nobody_declares_is_left_by_a_service_and_dropped_by_the_deploy(db, caplog):
    """A service starting on older code must not drop what newer code added; the deploy's prune does."""
    from libs.db import ensure_schema
    from libs.db.models import CorpusJob

    db[CorpusJob.COLLECTION].create_index("title", name="from_newer_code")

    ensure_schema(db)
    assert "from_newer_code" in {index["name"] for index in db[CorpusJob.COLLECTION].list_indexes()}
    assert any("from_newer_code" in record.getMessage() for record in caplog.records)

    ensure_schema(db, prune=True)
    assert "from_newer_code" not in {index["name"] for index in db[CorpusJob.COLLECTION].list_indexes()}


@needs_db
def test_an_index_whose_options_changed_is_left_by_a_service_and_changed_by_the_deploy(db, caplog):
    """Contact messages began to expire after 3 years (2026-11-18). A database whose `expires` index an
    earlier draft had made at 5 years crashed every service at its start, since the database refuses an index
    by a name it holds with other options (`scripts/dev.sh`, 2026-11-18). Any changed age or option would do
    the same. A service starting leaves it and says so, as it leaves an index nobody declares; the deploy's
    prune makes it what the code declares: an age in place, anything else by building it again."""
    from libs.db import ensure_schema
    from libs.db.models import ContactMessage, User
    from libs.db.models.contact import CONTACT_KEEP_YEARS

    ensure_schema(db)
    messages, users = db[ContactMessage.COLLECTION], db[User.COLLECTION]
    messages.drop_index("expires")
    messages.create_index("created_at", name="expires", expireAfterSeconds=5 * 366 * 86400)
    users.drop_index("email_unique")
    users.create_index("email", name="email_unique")

    def index(collection, name: str) -> dict:
        return next(found for found in collection.list_indexes() if found["name"] == name)

    ensure_schema(db)
    assert index(messages, "expires")["expireAfterSeconds"] == 5 * 366 * 86400
    assert not index(users, "email_unique").get("unique")
    said = [record.getMessage() for record in caplog.records if record.levelname == "WARNING"]
    assert any("contact_messages.expires" in line for line in said), said
    assert any("users.email_unique" in line for line in said), said

    ensure_schema(db, prune=True)
    assert index(messages, "expires")["expireAfterSeconds"] == CONTACT_KEEP_YEARS * 366 * 86400
    assert index(users, "email_unique")["unique"] is True


@needs_db
def test_a_collection_nothing_reads_any_more_is_left_by_a_service_and_dropped_by_the_deploy(db):
    """The operator's search terms fed a query set that was removed on 2026-11-06. A service starting leaves
    them, as it leaves an index; the deploy's prune drops them, and nothing else."""
    from libs.db import ensure_schema

    db["operator_keywords"].insert_one({"_id": 3, "keyword": "nurse"})
    db["kept_by_someone_else"].insert_one({"_id": 1})

    ensure_schema(db)
    assert "operator_keywords" in db.list_collection_names()

    ensure_schema(db, prune=True)
    assert {"operator_keywords", "kept_by_someone_else"} & set(db.list_collection_names()) == {
        "kept_by_someone_else"}


@needs_db
def test_services_starting_together_on_an_empty_database_all_come_up(db):
    from libs.db import ensure_schema
    from libs.db.models import MODELS

    fresh = db.client[f"{db.name}_fresh"]
    failures: list[BaseException] = []

    def start() -> None:
        try:
            ensure_schema(fresh)
        except BaseException as exc:  # noqa: BLE001 - collected and asserted on below
            failures.append(exc)

    try:
        threads = [threading.Thread(target=start) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert not failures, failures
        assert {model.COLLECTION for model in MODELS} <= set(fresh.list_collection_names())
    finally:
        db.client.drop_database(fresh.name)


@needs_db
def test_deleting_an_account_removes_everything_it_owns_and_nothing_shared(db):
    from libs.db import delete_account, insert, now
    from libs.db.models import (
        MODELS,
        BuiltDocument,
        CorpusJob,
        QueueJob,
        User,
        UserConfig,
        UserDocument,
        UserJobState,
    )

    gone, kept = (insert(db, User(email=f"{name}@example.com", password_hash="x"))
                  for name in ("gone", "kept"))
    job = insert(db, CorpusJob(url="https://jobs.example.com/1", source="test", title="Backend Engineer"))
    for person in (gone, kept):
        insert(db, UserConfig(user_id=person.id, config={"sections": []}))
        insert(db, UserDocument(user_id=person.id, kind="cv", filename="cv.pdf", text=person.email,
                                text_sha256=person.email, chars=1))
        insert(db, UserJobState(user_id=person.id, job_id=job.id, applied_at=now()))
        build = insert(db, QueueJob(lane="docgen", user_id=person.id))
        insert(db, BuiltDocument(id=build.id, user_id=person.id, job_id=job.id, kind="cv", mode="instant",
                                 filename="cv.pdf", content=b"%PDF", size=4, saved=True))

    delete_account(db, gone.id)

    for model in MODELS:
        if model.OWNER:
            assert not db[model.COLLECTION].count_documents({model.OWNER: gone.id}), model.COLLECTION
    assert db[User.COLLECTION].count_documents({}) == 1
    assert db[BuiltDocument.COLLECTION].count_documents({}) == 1, "the build's PDF goes with its account"
    assert db[CorpusJob.COLLECTION].count_documents({}) == 1, "a public posting outlives its readers"
    assert db[UserJobState.COLLECTION].count_documents({"user_id": kept.id}) == 1


@needs_db
def test_an_unknown_setting_key_is_refused_rather_than_stored(db):
    """A typo that persists is a setting that silently never applies."""
    from libs import settings

    with pytest.raises(KeyError):
        settings.put(db, "no_such_setting", 1)
    assert db.platform_settings.count_documents({}) == 0


@needs_db
def test_freshness_is_per_source_and_falls_back(db):
    """Sites differ in how fast they list and how far back their filters reach."""
    from libs import settings

    default = settings.freshness_hours(db, "a-source-nobody-configured")
    settings.put(db, "source_freshness_hours", {"adzuna": 48})
    assert settings.freshness_hours(db, "adzuna") == 48
    assert settings.freshness_hours(db, "a-source-nobody-configured") == default


@needs_db
@pytest.mark.parametrize("collection, field", [
    ("user_sessions", "expires_at"), ("auth_tokens", "expires_at"), ("mail_sends", "sent_at"),
    ("ai_requests", "created_at"), ("jobs", "finished_at")])
def test_what_is_read_only_for_a_while_is_dropped_by_the_database_after_it(db, collection, field):
    """A session at every refresh, a token for every link, a row for every email sent, AI request made and
    job done: each is read for a day or a month and none ever left (385 sessions for the dev copy's two
    accounts, 2026-11-20). The database drops each by its own date."""
    ages = [index.get("expireAfterSeconds") for index in db[collection].list_indexes()
            if list(index["key"]) == [field]]
    assert ages and ages[0] is not None, f"{collection}.{field} is kept for good"
