"""Nightly backup: the database, the model artifacts, and nothing else.

    python deploy/scripts/backup.py --out /var/backups/jobsearch
    python deploy/scripts/backup.py --out /var/backups/jobsearch --verify-only /path/to/dump.sql.gz

**The KEK is deliberately not in here, and that is the single most important line in this file.**
Two ways to get this catastrophically wrong, both silent until the day they matter
(`OPERATIONS_PLAN.md` §7):

1. **KEK backed up *with* the database** — the backup file becomes a complete credential breach.
   Anyone who gets the dump gets every user's Claude key, proxy password, and bot token.
2. **KEK not backed up *at all*** — a restore yields a database of undecryptable credentials, and
   every user has to re-enter every secret.

So this refuses to run if `SECRETS_KEK` would land in the output, and prints a reminder that the key
is backed up **separately, to a different place, under different credentials**. That separation is
the entire reason a stolen dump is useless, and it is the thing most often implemented and then
quietly defeated by writing the env file next to the database.

**Every dump is verified after it is written.** An unverified backup is a hope: `gzip -t` plus a
check that the dump contains the tables that matter costs a second and catches the truncated write,
the full disk, and the permission error that produced a 0-byte file.
"""
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

# Tables whose absence means the dump is not usable, whatever its size. Not the full list — this is
# a smoke test on the output, not a schema assertion.
REQUIRED_TABLES = ("users", "corpus", "user_labels", "user_secrets", "models")

# 30 days, per `OPERATIONS_PLAN.md` §7. Kept as a constant because a retention that lives only in a
# cron line is one nobody can find when they need to know how far back they can go.
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
            # Plain SQL rather than the custom format: it is greppable, diffable, and restorable
            # with `psql` alone. A custom-format dump needs a matching `pg_restore` version, which
            # is one more thing to be wrong at four in the morning.
            "--format=plain", "--no-owner", "--no-privileges"]


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
    parser.add_argument("--keep-days", type=int, default=KEEP_DAYS)
    parser.add_argument("--verify-only", help="verify an existing dump and exit")
    args = parser.parse_args(argv)

    if args.verify_only:
        return 0 if verify(Path(args.verify_only)) else 1

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    # **The check that makes the KEK separation real rather than intended.** Writing the dump next
    # to the key turns an encrypted-at-rest database into a plaintext one for anybody holding the
    # backup, and it is the single easiest mistake to make here.
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
        # Removed rather than left: a partial dump that looks like a backup is worse than no
        # backup, because somebody will find it and believe it.
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

    removed = prune(out, args.keep_days)
    if removed:
        print(f"  pruned {removed} dump(s) older than {args.keep_days} days")

    print("\nREMINDER: SECRETS_KEK is NOT in this backup, on purpose. Back it up separately, to a "
          "different place, under different credentials — a restore without it yields a database "
          "of credentials nobody can decrypt.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
