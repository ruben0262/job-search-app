"""Import a reference instance's labelled history into the shared corpus and one user's tail."""
from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sqlalchemy import select

from libs.corpus.repository import CorpusRepository
from libs.db.models import User
from libs.db.session import session_scope
from libs.tenant.repository import TenantRepository

# Committed periodically rather than in one transaction.
BATCH = 250


def _rows(path: Path):
    with path.open(encoding="utf-8") as handle:
        for raw in handle:
            line = raw.strip()
            if line:
                yield json.loads(line)


def newest_rubric(rows: list[dict]) -> str | None:
    """Which `profile_version` the reference is currently on, judged by its most recent label."""
    newest: dict[str, str] = {}
    for row in rows:
        version = row.get("profile_version")
        stamp = row.get("relabeled_at") or row.get("labeled_at") or ""
        if version and stamp > newest.get(version, ""):
            newest[version] = stamp
    return max(newest, key=lambda v: newest[v]) if newest else None


def _upsert_job(corpus: CorpusRepository, row: dict) -> int | None:
    """Find or create the corpus row for one reference record. Returns its id."""
    duplicate = corpus.find_duplicate(
        row["url"], row.get("title") or "", row.get("company") or "", row.get("location") or "")
    if duplicate is not None:
        job_id = duplicate.job_id
    else:
        job_id = corpus.add(
            url=row["url"], source=row.get("source") or "import",
            title=row.get("title") or "", company=row.get("company"),
            location=row.get("location"))
        if job_id is None:
            return None
    if row.get("description"):
        corpus.set_description(job_id, row["description"])
    return job_id


def _import_applied(path: Path, user_id) -> int:
    """The applied log."""
    rows = [r for r in _rows(path) if r.get("url")]
    imported = 0
    for start in range(0, len(rows), BATCH):
        with session_scope() as session:
            corpus, tenant = CorpusRepository(session), TenantRepository(session)
            for row in rows[start:start + BATCH]:
                job_id = _upsert_job(corpus, row)
                if job_id is None:
                    continue
                tenant.record_applied(user_id, job_id)
                imported += 1
    print(f"  … {imported} applied records")
    return imported


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", required=True)
    parser.add_argument("--reference", required=True)
    parser.add_argument("--labels", default="scorer/data/labels.jsonl")
    parser.add_argument("--applied", default="pipeline/data/applied.jsonl")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    reference = Path(args.reference)
    labels_path = reference / args.labels
    if not labels_path.exists():
        print(f"no labels at {labels_path}", file=sys.stderr)
        return 2

    rows = list(_rows(labels_path))
    current = newest_rubric(rows)
    usable = [r for r in rows
              if r.get("profile_version") == current and r.get("url") and r.get("score") is not None]
    skipped = collections.Counter(
        r.get("profile_version") or "unversioned" for r in rows
        if r.get("profile_version") != current)

    print(f"{len(rows)} labels in the reference; {len(usable)} at its current rubric {current!r}")
    for version, count in skipped.most_common():
        print(f"  skipping {count} from {version} — a different question, not merely older")

    if args.dry_run:
        print("\n--dry-run: nothing written")
        return 0

    stats: collections.Counter = collections.Counter()
    with session_scope() as session:
        user = session.scalar(select(User).where(User.email == args.email.lower()))
        if user is None:
            print(f"no such account: {args.email}", file=sys.stderr)
            return 2
        if not user.profile or not user.profile_generation:
            # Labels carry a generation stamp.
            print("ERROR: this account has no confirmed profile — run set_profile.py first",
                  file=sys.stderr)
            return 2
        user_id, version, generation = user.id, user.profile_version, user.profile_generation

    for start in range(0, len(usable), BATCH):
        with session_scope() as session:
            corpus, tenant = CorpusRepository(session), TenantRepository(session)
            for row in usable[start:start + BATCH]:
                job_id = _upsert_job(corpus, row)
                if job_id is None:
                    stats["corpus_race"] += 1
                    continue
                tenant.upsert_label(
                    user_id, job_id, label=int(row["score"]), source="llm",
                    rationale=(row.get("fit_notes") or None),
                    profile_version=version, profile_generation=generation)
                stats["labels"] += 1
        print(f"  … {min(start + BATCH, len(usable))}/{len(usable)} labels")

    applied_path = reference / args.applied
    if applied_path.exists():
        stats["applied"] += _import_applied(applied_path, user_id)

    print("\n" + "\n".join(f"  {k}: {v}" for k, v in sorted(stats.items())))
    print("\nnext: embed_backlog.py — the imported rows have no vectors yet")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
