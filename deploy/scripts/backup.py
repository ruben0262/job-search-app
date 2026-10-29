"""Nightly backup: the database, the model artifacts, and nothing else."""
from __future__ import annotations

import argparse
import gzip
import os
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

# Tables whose absence means the dump is not usable, whatever its size.
REQUIRED_TABLES = ("users", "corpus", "user_labels", "user_secrets", "models")

# 30 days, per `OPERATIONS_PLAN.md` §7.
KEEP_DAYS = 30


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        print("DATABASE_URL is not set", file=sys.stderr)
        raise SystemExit(2)
    return url


def _pg_dump_args(url: str) -> list[str]:
    """`pg_dump` speaks libpq URLs, but not SQLAlchemy's `+psycopg` dialect suffix."""
    return ["pg_dump", url.replace("postgresql+psycopg://", "postgresql://"),
            # Plain SQL rather than the custom format: it is greppable, diffable.
            "--format=plain", "--no-owner", "--no-privileges",
            # Unlogged is how a table says its rows are ephemeral: `built_documents` holds a CV
            # for an hour, and a dump must not keep it for thirty days (D9).
            "--no-unlogged-table-data"]


def verify(path: Path) -> bool:
    """Read the dump back. An unverified backup is a hope, not a backup."""
    if not path.exists() or path.stat().st_size == 0:
        print(f"  FAILED: {path} is missing or empty", file=sys.stderr)
        return False

    found: set[str] = set()
    try:
        with gzip.open(path, "rt", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                # `gzip.open` streaming *is* the integrity check: a truncated member raises here.
                for table in REQUIRED_TABLES:
                    if f"CREATE TABLE public.{table} " in line:
                        found.add(table)
    except (OSError, EOFError) as exc:
        print(f"  FAILED: {path} did not decompress cleanly — {exc}", file=sys.stderr)
        return False

    missing = set(REQUIRED_TABLES) - found
    if missing:
        print(f"  FAILED: {path} has no definition for {', '.join(sorted(missing))}",
              file=sys.stderr)
        return False

    print(f"  verified: {path.name}, {path.stat().st_size / 1024**2:.1f} MB, "
          f"{len(found)}/{len(REQUIRED_TABLES)} key tables present")
    return True


def prune(directory: Path, keep_days: int) -> int:
    """Delete dumps older than the retention window. Returns how many went."""
    cutoff = datetime.now(UTC).timestamp() - keep_days * 86400
    removed = 0
    for path in sorted(directory.glob("jobsearch-*.sql.gz")):
        if path.stat().st_mtime < cutoff:
            path.unlink()
            removed += 1
    return removed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, help="directory to write the dump into")
    parser.add_argument("--models", help="model artifact directory to archive alongside")
    parser.add_argument("--archive", help="corpus archive directory to copy alongside")
    parser.add_argument("--keep-days", type=int, default=KEEP_DAYS)
    parser.add_argument("--verify-only", help="verify an existing dump and exit")
    args = parser.parse_args(argv)

    if args.verify_only:
        return 0 if verify(Path(args.verify_only)) else 1

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    # The check that makes the KEK separation real rather than intended.
    kek_file = os.environ.get("SECRETS_KEK_FILE")
    if kek_file and Path(kek_file).resolve().parent == out.resolve():
        print("REFUSING: the KEK lives in the backup directory. A dump beside its key is not an "
              "encrypted backup — it is a credential breach in one file.", file=sys.stderr)
        return 2

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    target = out / f"jobsearch-{stamp}.sql.gz"

    print(f"dumping to {target}")
    try:
        with gzip.open(target, "wb") as sink:
            process = subprocess.Popen(_pg_dump_args(_database_url()), stdout=subprocess.PIPE)
            shutil.copyfileobj(process.stdout, sink)
            process.stdout.close()
            code = process.wait()
    except FileNotFoundError:
        print("pg_dump is not on PATH — install postgresql-client", file=sys.stderr)
        return 2

    if code != 0:
        # Removed rather than left: a partial dump that looks like a backup is worse than no backup.
        target.unlink(missing_ok=True)
        print(f"pg_dump exited {code} — the partial file has been removed", file=sys.stderr)
        return 1

    if not verify(target):
        return 1

    if args.models:
        models = Path(args.models)
        if models.is_dir():
            archive = shutil.make_archive(str(out / f"models-{stamp}"), "gztar", root_dir=models)
            print(f"  models: {Path(archive).name}")

    # **The archive is the only copy of a posting once retention has deleted its row**
    # (`libs.corpus.archive`), so leaving it out of the backup would make the dump a partial record
    # of the platform rather than a full one. Copied whole rather than rotated with the dumps: the
    # archive is append-only by design, and pruning it here would re-create the deletion it exists
    # to prevent.
    if args.archive:
        source = Path(args.archive)
        if source.is_dir() and any(source.iterdir()):
            bundle = shutil.make_archive(str(out / f"corpus-archive-{stamp}"), "gztar",
                                         root_dir=source)
            print(f"  corpus archive: {Path(bundle).name}")
        else:
            # Not an error: nothing has aged out yet. Said out loud so an empty backup is a fact
            # somebody chose to read rather than a silence.
            print(f"  corpus archive: nothing at {source} yet")

    removed = prune(out, args.keep_days)
    if removed:
        print(f"  pruned {removed} dump(s) older than {args.keep_days} days")

    print("\nREMINDER: SECRETS_KEK is NOT in this backup, on purpose. Back it up separately, to a "
          "different place, under different credentials — a restore without it yields a database "
          "of credentials nobody can decrypt.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
