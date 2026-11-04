"""MongoDB: one client per process, and the schema every collection follows — in one place.

Each collection is a dataclass in `libs.db.models`: its fields and their defaults, its indexes, and
the values a field may hold. `ensure_schema` makes the database match them, and nothing else creates
an index or a rule, so the declaration and the database cannot drift apart unnoticed.
"""
from __future__ import annotations

import logging
import os
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, fields
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar, Self

from pymongo import IndexModel, MongoClient, ReturnDocument
from pymongo.database import Database
from pymongo.errors import CollectionInvalid, DuplicateKeyError, OperationFailure

logger = logging.getLogger(__name__)

_client: MongoClient | None = None


def database() -> Database:
    """This process's database. `MONGODB_URI` is required: a plausible default would hide a typo."""
    global _client  # noqa: PLW0603 - one client per process, built on first use
    if _client is None:
        uri = os.environ.get("MONGODB_URI")
        if not uri:
            raise RuntimeError("MONGODB_URI is not set. Expected e.g. mongodb+srv://<user>:<password>@"
                               "<cluster>.mongodb.net, as in certificates/mongodb.env")
        _client = MongoClient(uri, uuidRepresentation="standard", tz_aware=True, appname="mahoraga",
                              serverSelectionTimeoutMS=10_000)
    return _client[os.environ.get("MONGODB_DB") or "mahoraga"]


def now() -> datetime:
    """The time as it is stored: UTC, to the millisecond — all MongoDB keeps — so a value written and
    read back compares equal to the one still in hand."""
    moment = datetime.now(UTC)
    return moment.replace(microsecond=moment.microsecond // 1000 * 1000)


def next_ids(db: Database, sequence: str, count: int = 1) -> range:
    """`count` integers for `sequence`, never handed out twice. For the ids the site shows or links to."""
    issued = db.counters.find_one_and_update({"_id": sequence}, {"$inc": {"value": count}},
                                             upsert=True, return_document=ReturnDocument.AFTER)
    return range(issued["value"] - count + 1, issued["value"] + 1)


@contextmanager
def lease(db: Database, name: str, seconds: float) -> Iterator[bool]:
    """Hold the named lease, or yield False while a live holder has it. Let go on exit; a holder that
    dies lets go when its time runs out. Only the holder can end it, so a run that outlived its lease
    cannot end its successor's."""
    holder = uuid.uuid4().hex
    try:
        # At or before now: a lease let go ends at the moment it was let go, and dates are kept to the
        # millisecond, so a run starting in that millisecond found it still held (a flaky test, 2026-11-03).
        db.locks.find_one_and_update(
            {"_id": name, "until": {"$lte": now()}},
            {"$set": {"until": now() + timedelta(seconds=seconds), "holder": holder}}, upsert=True)
        acquired = True
    except DuplicateKeyError:  # the lease exists and is still running
        acquired = False
    try:
        yield acquired
    finally:
        if acquired:
            db.locks.update_one({"_id": name, "holder": holder}, {"$set": {"until": now()}})


@dataclass(kw_only=True)
class Document:
    """A collection's shape. Subclasses are dataclasses whose `id` field, if any, is stored as `_id`."""

    COLLECTION: ClassVar[str]
    INDEXES: ClassVar[tuple[IndexModel, ...]] = ()
    # The values a field may hold, enforced by the database on every write — the old CHECK constraints.
    CHOICES: ClassVar[dict[str, tuple[Any, ...]]] = {}
    # The field naming the account a document belongs to: deleting an account deletes these.
    OWNER: ClassVar[str | None] = "user_id"
    # Integer ids issued by `next_ids` when an insert leaves `id` empty.
    SEQUENCE: ClassVar[bool] = False
    # Whether the nightly backup keeps it. False for what rebuilds itself or expires: postings are
    # collected again within hours, and a backup is not where a session or a built PDF should live.
    BACKED_UP: ClassVar[bool] = True
    # Fields this class no longer declares and stored documents may still carry: read past, and removed
    # from the stored documents by the deploy's prune.
    RETIRED: ClassVar[tuple[str, ...]] = ()

    @classmethod
    def from_doc(cls, doc: dict | None) -> Self | None:
        """The document as this class, or None. A field the class does not declare is an error."""
        if doc is None:
            return None
        data = {name: value for name, value in doc.items() if name not in cls.RETIRED}
        key = data.pop("_id", None)
        if "id" in cls.__dataclass_fields__:
            data["id"] = key
        return cls(**data)

    def to_doc(self) -> dict:
        data = {field.name: getattr(self, field.name) for field in fields(self)}
        if "id" in data:
            data["_id"] = data.pop("id")
        return data


def insert[D: Document](db: Database, document: D) -> D:
    """Store a new document, issuing its integer id first where its collection uses one."""
    if document.SEQUENCE and getattr(document, "id", None) is None:
        document.id = next_ids(db, document.COLLECTION)[0]
    db[document.COLLECTION].insert_one(document.to_doc())
    return document


def get[D: Document](db: Database, model: type[D], key: Any) -> D | None:
    return model.from_doc(db[model.COLLECTION].find_one({"_id": key}))


def find_one[D: Document](db: Database, model: type[D], where: dict, **options: Any) -> D | None:
    return model.from_doc(db[model.COLLECTION].find_one(where, **options))


def find[D: Document](db: Database, model: type[D], where: dict, **options: Any) -> list[D]:
    return [model.from_doc(doc) for doc in db[model.COLLECTION].find(where, **options)]


def ensure_schema(db: Database | None = None, *, prune: bool = False) -> None:
    """Make every collection's rules and indexes what `libs.db.models` declares. Safe to repeat: it runs
    at every service start.

    An index nobody declares any more is dropped only with `prune`, which the deploy's schema step
    passes (`python -m libs.db --prune`) from an image of the commit being deployed. A service starting
    on older code must not drop what newer code added, or two versions would undo each other.
    """
    from libs.db.models import MODELS  # noqa: PLC0415 - the models import this module

    db = database() if db is None else db
    existing = set(db.list_collection_names())
    for model in MODELS:
        rules = {"$jsonSchema": {"bsonType": "object", "properties": {
            name: {"enum": list(values)} for name, values in model.CHOICES.items()}}}
        if model.COLLECTION not in existing:
            _create(db, model.COLLECTION, rules)
        db.command("collMod", model.COLLECTION, validator=rules, validationLevel="strict")
        collection = db[model.COLLECTION]
        declared = set(collection.create_indexes(list(model.INDEXES))) if model.INDEXES else set()
        for index in collection.list_indexes():
            if index["name"] in declared | {"_id_"}:
                continue
            if prune:
                collection.drop_index(index["name"])
                logger.info("schema: dropped %s.%s, which nothing declares any more", model.COLLECTION,
                            index["name"])
            else:
                logger.warning("schema: %s.%s is not declared by this code; left for the deploy's prune",
                               model.COLLECTION, index["name"])
        if prune and model.RETIRED:
            removed = collection.update_many({"$or": [{name: {"$exists": True}} for name in model.RETIRED]},
                                             {"$unset": dict.fromkeys(model.RETIRED, "")}).modified_count
            if removed:
                logger.info("schema: removed %s from %d %s", model.RETIRED, removed, model.COLLECTION)


def _create(db: Database, name: str, rules: dict) -> None:
    """Create a collection, unless another service starting at the same moment made it first: its rules
    are set by the `collMod` that follows either way."""
    try:
        db.create_collection(name, validator=rules)
    except CollectionInvalid:
        pass
    except OperationFailure as exc:
        if exc.code != NAMESPACE_EXISTS:
            raise


# What the server answers when the collection appeared between our look and our create.
NAMESPACE_EXISTS = 48


def delete_account(db: Database, user_id: Any) -> dict[str, int]:
    """Everything stored about one account, then the account: what a cascade did in Postgres."""
    from libs.db.models import MODELS, User  # noqa: PLC0415 - the models import this module

    removed = {model.COLLECTION: db[model.COLLECTION].delete_many({model.OWNER: user_id}).deleted_count
               for model in MODELS if model.OWNER}
    removed[User.COLLECTION] = db[User.COLLECTION].delete_one({"_id": user_id}).deleted_count
    return removed
