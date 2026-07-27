"""Create a platform account from the reference instance's config. **Read-only on the source.**

    python deploy/scripts/migrate_reference_account.py --email you@example.com --dry-run
    python deploy/scripts/migrate_reference_account.py --email you@example.com

This exists for decision **D6**: the single-tenant stacks keep running, and their owners *also* get
platform accounts, so the two can be compared on the same person. A comparison is only meaningful if
both sides are searching for the same thing — so this copies the config **verbatim** rather than
curating it. Nothing in the reference repository is written, moved, or deleted; it is opened for
reading and that is all.

**What it deliberately does not migrate:**

- **Credentials.** The Claude key, Telegram bot token, and proxy URL live in the reference's `.env`
  and belong in `user_secrets` under envelope encryption (`TENANT_SAFETY.md` §2), which is Phase 2.
  Copying plaintext secrets into a database that has no encryption yet would be the exact mistake
  the KEK design exists to prevent.
- **Scored history, labels, applied records.** Those are the *output* of a rubric that does not
  exist per-user yet (Phase 2). Importing them would attach one person's judgements to a profile
  generation that never produced them.

So this migrates **what the user asked for**, not what the old system concluded. The corpus is
shared and already populated, which is what makes that sufficient: the account gets the whole
back-catalogue immediately rather than starting empty (`COLD_START_PLAN.md`).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Operational scripts are run by path (`python deploy/scripts/...`), not as modules, and that does
# not put the repo root on sys.path. Same trap that made CI fail on `libs` imports: `python -m`
# adds the working directory and a direct invocation does not.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sqlalchemy import select

from libs.contracts.config import CONFIG_SCHEMA_VERSION, Location, UserConfigSchema
from libs.db.models import User
from libs.db.session import session_scope
from libs.tenant.repository import TenantRepository

# The reference searches by region and city name; the platform uses a controlled vocabulary so the
# harvest fold can deduplicate. Mapping is explicit rather than fuzzy — a wrong guess here silently
# changes which jobs a user sees, and "European Union" is not the same target as "remote, EU".
LOCATION_MAP = {
    "european union": Location.EUROPEAN_UNION,
    "europe": Location.EUROPE,
    "united kingdom": Location.UNITED_KINGDOM,
    "budapest": Location.BUDAPEST,
    "budapest, hungary": Location.BUDAPEST,
}


def read_reference_config(reference: Path) -> dict:
    """Extract the owner's search terms and locations. Opens the file; changes nothing."""
    import yaml

    config_path = reference / "pipeline" / "config.yaml"
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    searches = raw.get("searches") or {}

    terms: list[str] = []
    locations: list[str] = []
    for key in ("jobspy_linkedin", "jobspy_indeed", "jobspy_google", "hiringcafe"):
        for query in (searches.get(key) or {}).get("queries") or []:
            term = query.get("search_term") or query.get("search") or query.get("query")
            if term and term.lower() not in {t.lower() for t in terms}:
                terms.append(term)
            location = query.get("location")
            if location and location.lower() not in {loc.lower() for loc in locations}:
                locations.append(location)

    mapped: list[Location] = []
    unmapped: list[str] = []
    for location in locations:
        target = LOCATION_MAP.get(location.strip().lower())
        if target is None:
            unmapped.append(location)
        elif target not in mapped:
            mapped.append(target)

    return {
        "keywords": terms,
        "locations": [loc.value for loc in mapped],
        "unmapped_locations": unmapped,
        "remote_policy": "remote_or_hybrid",
        "posted_within_hours": 72,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", required=True)
    parser.add_argument("--reference", default=r"d:\Projects\job-search")
    parser.add_argument("--timezone", default="Europe/Budapest")
    parser.add_argument("--notify-interval-min", type=int, default=60)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    # The config contains non-ASCII search terms (Hungarian, in the first real migration). A
    # Windows console defaults to cp1252 and this script would die printing them — losing not the
    # data, but the operator's only view of what it is about to write.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    reference = Path(args.reference)
    if not (reference / "pipeline" / "config.yaml").exists():
        print(f"reference config not found under {reference}", file=sys.stderr)
        return 2

    extracted = read_reference_config(reference)
    unmapped = extracted.pop("unmapped_locations")

    if unmapped:
        # Refuse rather than quietly drop. A location that silently vanishes means the user stops
        # seeing a whole category of job and has no way to tell.
        print(f"ERROR: no vocabulary entry for {unmapped!r} — add it to Location before migrating,",
              file=sys.stderr)
        print("       or the user silently stops seeing jobs from there.", file=sys.stderr)
        return 2

    config = UserConfigSchema(**extracted)
    print(f"migrating {len(config.keywords)} search terms and "
          f"{len(config.locations)} locations for {args.email}")
    print(json.dumps(config.model_dump(), indent=2, ensure_ascii=False))

    if args.dry_run:
        print("\n--dry-run: nothing written")
        return 0

    with session_scope() as session:
        existing = session.scalar(select(User).where(User.email == args.email.lower()))
        if existing is not None:
            # Update rather than duplicate: re-running after a config change is the normal case.
            user = existing
            print(f"updating existing account {user.id}")
        else:
            user = User(email=args.email.lower(), password_hash="!migrated-no-login")
            session.add(user)
            session.flush()
            print(f"created account {user.id}")

        user.timezone = args.timezone
        user.notify_interval_min = args.notify_interval_min
        # Active immediately: this account exists for the D6 comparison and its owner is the
        # operator, so there is no email to verify. A self-service signup takes the normal
        # pending_verification path (TENANT_SAFETY.md §5).
        user.status = "active"
        user.active = True

        repo = TenantRepository(session)
        repo.save_config(user.id, config.model_dump(), schema_version=CONFIG_SCHEMA_VERSION)
        repo.touch_activity(user.id)

    print("done — the next harvest cycle will fold these terms into the shared query set")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
