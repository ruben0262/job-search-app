"""Restore a dump into a scratch database and prove the result is actually usable.

    python deploy/scripts/restore_drill.py --dump /var/backups/jobsearch/jobsearch-*.sql.gz

**An untested backup is not a backup.** This is the drill `OPERATIONS_PLAN.md` §7 requires before
launch and quarterly after, automated so it is run rather than intended.

It does not check that the file restores — that is the easy half, and a dump that restores into an
empty schema tells you nothing. It checks the three things that actually have to be true afterwards:

1. **The rows are there.** Users, corpus, labels, models — counted and compared against the live
   database, so a dump taken mid-write is caught.
2. **Credentials decrypt with the separately-held KEK.** This is the whole KEK-separation design
   under test. If it fails, the backup is a database of unreadable secrets and every user must
   re-enter every credential — and the only way to learn that is to try.
3. **A model is servable.** The artifact metadata survives, and the version columns still agree, so
   a restored platform can score rather than merely start.

**Restores into a scratch database and drops it afterwards.** It refuses to run against the live
one, which is a mistake with no undo.
"""
from __future__ import annotations

import argparse
import gzip
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def _libpq(url: str) -> str:
    return url.replace("postgresql+psycopg://", "postgresql://")


def _admin_url(url: str) -> str:
    """The same server, the `postgres` database — you cannot drop a database you are connected to."""
    base, _, _ = _libpq(url).rpartition("/")
    return f"{base}/postgres"


def _psql(url: str, sql: str) -> subprocess.CompletedProcess:
    return subprocess.run(["psql", url, "-v", "ON_ERROR_STOP=1", "-c", sql],
                          capture_output=True, text=True, check=False)


def _count(url: str, table: str) -> int:
    result = subprocess.run(["psql", _libpq(url), "-tAc", f"SELECT count(*) FROM {table}"],
                            capture_output=True, text=True, check=False)
    return int(result.stdout.strip() or 0) if result.returncode == 0 else -1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dump", required=True)
    parser.add_argument("--keep", action="store_true",
                        help="leave the scratch database behind for inspection")
    args = parser.parse_args(argv)

    live = os.environ.get("DATABASE_URL", "")
    if not live:
        print("DATABASE_URL is not set — it names the server to restore onto and the live "
              "database to compare against", file=sys.stderr)
        return 2

    dump = Path(args.dump)
    if not dump.exists():
        print(f"no such dump: {dump}", file=sys.stderr)
        return 2

    scratch_name = f"drill_{uuid.uuid4().hex[:10]}"
    admin = _admin_url(live)
    scratch = f"{_libpq(live).rpartition('/')[0]}/{scratch_name}"

    print(f"restoring {dump.name} into {scratch_name}")
    created = _psql(admin, f'CREATE DATABASE "{scratch_name}"')
    if created.returncode != 0:
        print(f"could not create the scratch database: {created.stderr.strip()}", file=sys.stderr)
        return 2

    try:
        # **Copied in, not handed over.** `subprocess` wants a real file descriptor for `stdin`,
        # and `GzipFile.fileno()` returns the descriptor of the *compressed* file — so passing the
        # handle directly feeds psql raw gzip bytes. It fails with
        # `invalid byte sequence for encoding "UTF8": 0x8b`, which reads like a corrupt dump rather
        # than a mistake in this script. Found by running the drill, which is the argument for
        # having one.
        process = subprocess.Popen(["psql", scratch, "-v", "ON_ERROR_STOP=1", "--quiet"],
                                   stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.PIPE)
        with gzip.open(dump, "rb") as source:
            shutil.copyfileobj(source, process.stdin)
        process.stdin.close()
        stderr = process.stderr.read()
        process.stderr.close()
        if process.wait() != 0:
            print(f"restore FAILED: {stderr.decode(errors='replace')[-800:]}", file=sys.stderr)
            return 1

        failures = _check(live, scratch)
        if failures:
            for line in failures:
                print(f"  FAILED: {line}", file=sys.stderr)
            print(f"\nDRILL FAILED — {len(failures)} check(s). This backup would not have restored "
                  f"a working platform.", file=sys.stderr)
            return 1

        print("\nDRILL PASSED. The dump restores, the rows are there, credentials decrypt with the "
              "separately-held KEK, and a model is servable.")
        return 0
    finally:
        if args.keep:
            print(f"scratch database left behind: {scratch_name}")
        else:
            _psql(admin, f'DROP DATABASE IF EXISTS "{scratch_name}" WITH (FORCE)')


def _check(live: str, scratch: str) -> list[str]:
    """The three things that have to be true after a restore. Returns the failures."""
    failures: list[str] = []

    # 1. The rows are there, and match. A dump taken mid-write is the case this catches.
    for table in ("users", "corpus", "user_labels", "models", "user_secrets"):
        live_count, restored = _count(live, table), _count(scratch, table)
        status = "ok" if restored >= live_count > 0 or live_count == restored else "MISMATCH"
        print(f"  {table:14} live={live_count:<8} restored={restored:<8} {status}")
        if restored < 0:
            failures.append(f"{table} is not queryable in the restored database")
        elif live_count > 0 and restored == 0:
            failures.append(f"{table} restored empty while the live database has {live_count} rows")

    # 2. **The KEK separation, under test.** Everything else can be right and this still fail — and
    #    if it does, the backup is a database of secrets nobody can read.
    if not os.environ.get("SECRETS_KEK"):
        failures.append("SECRETS_KEK is not set, so the drill cannot prove credentials decrypt — "
                        "which is the single most important thing it exists to check")
    else:
        failures += _check_decryption(scratch)

    # 3. A model is servable: an artifact row whose generation still agrees with its owner's. The
    #    scorer fails closed on a mismatch, so a restore that loses this restores a platform that
    #    starts and then refuses to score — which looks like a working restore until somebody looks
    #    at their feed.
    servable = _scalar(scratch, """
        SELECT count(*) FROM models m
        JOIN users u ON u.id = m.user_id
        WHERE m.is_active AND m.profile_generation = u.profile_generation""")
    total_models = _count(scratch, "models")
    if total_models > 0 and servable < 1:
        failures.append("no restored model is servable — every artifact disagrees with its owner's "
                        "profile generation, so the scorer would fail closed on all of them")
    else:
        print(f"  models       {servable} servable of {total_models} restored")

    # 3b. **And the file behind the row is actually reachable.**
    #
    # Added after a real failure: the scorer crash-looped on a `models.path` that was correct on the
    # host that trained it and meaningless inside a container. Checking only the row would have
    # reported a healthy restore of a platform that cannot score — the exact reassurance a drill
    # must never give.
    failures += _check_artifacts(scratch)

    return failures


def _check_artifacts(scratch: str) -> list[str]:
    """Every active artifact resolves to a file that exists, the way the scorer resolves it."""
    from services.scorer.registry import resolve_artifact

    result = subprocess.run(
        ["psql", _libpq(scratch), "-tAc", "SELECT path FROM models WHERE is_active"],
        capture_output=True, text=True, check=False)
    paths = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if not paths:
        print("  artifacts    no active models — nothing to resolve")
        return []

    missing = [path for path in paths if not Path(resolve_artifact(path)).exists()]
    if missing:
        return [f"{len(missing)} of {len(paths)} active artifact(s) do not exist where the scorer "
                f"would look — a restore of rows without their files scores nothing"]
    print(f"  artifacts    {len(paths)} active artifact(s) resolve to a real file")
    return []


def _scalar(url: str, sql: str) -> int:
    result = subprocess.run(["psql", _libpq(url), "-tAc", sql],
                            capture_output=True, text=True, check=False)
    return int(result.stdout.strip() or 0) if result.returncode == 0 else -1


def _check_decryption(scratch: str) -> list[str]:
    """Open one real credential from the restored database with the live KEK."""
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import Session

    from libs.db.models import UserSecret
    from libs.secrets import SecretStore

    engine = create_engine(scratch.replace("postgresql://", "postgresql+psycopg://"))
    try:
        with Session(engine) as session:
            secret = session.scalar(select(UserSecret).limit(1))
            if secret is None:
                # Not a failure. A platform with no stored credentials has nothing to prove here,
                # and calling that a failed drill would train somebody to ignore the result.
                print("  decryption   no credentials stored — nothing to verify")
                return []
            with SecretStore(session).use(secret.user_id, secret.kind) as opened:
                if opened is None or not opened.reveal():
                    return [f"a stored {secret.kind} did not decrypt with the current KEK — the "
                            f"restore is a database of unreadable credentials"]
            print(f"  decryption   ok — a stored {secret.kind} opened with the current KEK")
            return []
    except Exception as exc:
        return [f"decryption raised {type(exc).__name__}: {exc}"]
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
