"""Every column the product reads is written by something.

**The second-most expensive failure class here, and it is invisible by construction.** A nullable
column that nothing ever assigns reads as NULL forever, and NULL is a legal value — so no query
fails, no exception is raised, and the feature that depends on it is simply absent. Twice in one
week:

- `users.telegram_webhook_secret` was declared, migrated and read by the webhook route, and written
  by **nothing**. Every "Build my CV" tap on the account that lacked it arrived, matched no user and
  404'd in silence for weeks (ROADMAP 6af).
- `proxy_credentials` rows were created as placeholders with `status = healthy` and no secret behind
  them, so an account whose LinkedIn tier had never run once reported a healthy credential
  (ROADMAP 6ao).

Both were found by a user noticing a missing feature, which is the most expensive way to find
anything. This test asks the question directly: **for every column something reads, is there a line
somewhere that writes it?**

Deliberately crude — a grep for an assignment, not a dataflow analysis. The failure it catches is
"nowhere at all", which is what both defects were, and a cleverer check would be one nobody trusts.

No database. Runs in CI on every commit.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

CODE = [Path("libs"), Path("services")]

# Columns that are genuinely written by the database or by Alembic rather than by application code,
# with the reason each one is exempt. **Adding a name here is a decision**: it says "nothing in
# Python writes this and that is correct", and it is the only way to silence this test.
WRITTEN_ELSEWHERE = {
    "id": "primary keys are the database's",
    "created_at": "server_default",
    "first_seen_at": "server_default on insert",
    "updated_at": "server_default / onupdate",
    "sent_at": "server_default now() on the mail_sends insert",
}

# **Columns this test found on its first run, each a real answer to "what is silently absent".**
#
# Listed rather than deleted because removing a column is the owner's call, and listed rather than
# ignored because a permanently red test is one nobody reads. A *new* unwritten column still fails
# the build; these are the known ones, with what each actually costs (ROADMAP 6aq).
KNOWN_UNWRITTEN = {
    # Deliberate, and documented at the reader. `_cap` prefers `libs.limits.proxy_mb_per_day` and
    # falls back to this column, which nothing but the server_default of 200 ever sets. Harmless as
    # long as it stays a fallback — it is on this list so it cannot quietly become a second answer.
    "daily_cap_mb": "intentional fallback; libs.limits.proxy_mb_per_day is the live value",
    # **A second source of truth that disagrees with the first.** The column defaults to 240 and the
    # `libs.limits` entry of the same name defaults to 15; the governor reads the limit. The column
    # is the loser of a migration nobody finished.
    "linkedin_interval_min": "superseded by libs.limits; column default 240 vs limit default 15",
    # **A feature that cannot be used.** `quotas_for` merges this over the plan's limits, so the
    # per-user quota override is implemented, read, and impossible to set: nothing writes it.
    "quota_overrides": "read by libs.tenant.quotas and written by nothing — overrides are unusable",
    # **No reader either.** Declared 2026-07-29 and referenced by exactly one line, its own
    # declaration. Dead in both directions.
    "profile_context": "no reader and no writer; dead in both directions",
}


def source_files() -> list[Path]:
    return [path for root in CODE for path in root.rglob("*.py")
            if "__pycache__" not in str(path)]


def declared_columns() -> dict[str, list[str]]:
    """Every mapped column, by the model file that declares it."""
    declaration = re.compile(r"^\s{4}(\w+):\s*Mapped\[")
    out: dict[str, list[str]] = {}
    for path in Path("libs/db/models").rglob("*.py"):
        names = [match.group(1) for match in
                 (declaration.match(line) for line in path.read_text(encoding="utf-8").splitlines())
                 if match]
        if names:
            out[path.name] = names
    return out


@pytest.fixture(scope="module")
def bodies() -> str:
    return "\n".join(path.read_text(encoding="utf-8") for path in source_files())


def test_there_are_columns_to_check(bodies):
    """The guard on the guard. A regex that silently matches nothing would make this file a
    reassuring no-op, which is worse than not having it — that exact mistake shipped twice in the
    CI drift check."""
    columns = declared_columns()
    assert len(columns) >= 5, f"only found columns in {list(columns)} — the parser has drifted"
    assert sum(len(names) for names in columns.values()) > 60


def test_every_column_is_written_by_something(bodies):
    """**The check itself.** A column with no producer is a feature that is silently absent."""
    unwritten: list[str] = []

    for model_file, names in declared_columns().items():
        for name in names:
            if name in WRITTEN_ELSEWHERE or name in KNOWN_UNWRITTEN:
                continue
            # An assignment (`x.name =`, `name=`), a bulk update (`"name":`), or an insert value.
            written = re.search(
                rf"(\.{name}\s*=[^=]|\b{name}\s*=[^=]|[\"']{name}[\"']\s*:)", bodies)
            if not written:
                unwritten.append(f"{model_file}:{name}")

    assert not unwritten, (
        "these columns are declared and read but nothing ever writes them, which reads as NULL "
        "forever and raises nothing:\n  " + "\n  ".join(sorted(unwritten))
        + "\n\nEither write them, delete them, or add them to WRITTEN_ELSEWHERE with the reason.")


def test_the_known_unwritten_columns_still_exist(bodies):
    """**An exemption list that outlives what it exempts is a lie.** If one of these is finally
    written, or the column is dropped, the entry has to go — otherwise the list slowly becomes a
    place where real findings are hidden."""
    declared = {name for names in declared_columns().values() for name in names}

    stale = [name for name in KNOWN_UNWRITTEN if name not in declared]
    assert not stale, f"these are exempted but no longer declared — remove them: {sorted(stale)}"

    now_written = [name for name in KNOWN_UNWRITTEN
                   if re.search(rf"(\.{name}\s*=[^=]|\b{name}\s*=[^=])", bodies)]
    assert not now_written, (
        f"these now have a producer, so the exemption is out of date: {sorted(now_written)}")


def test_the_check_would_have_caught_the_webhook_secret(bodies):
    """**Proof the assertion above can fail**, pinned to the defect that motivated it.

    `telegram_webhook_secret` now has exactly one producer — the connect route. If that assignment
    is ever removed, the column goes back to being a promise nothing keeps, and the general test
    above is what should fail. This asserts the mechanism it relies on actually finds a producer.
    """
    assert re.search(r"\.telegram_webhook_secret\s*=[^=]", bodies), (
        "the column that started this has lost its producer again")

    # And the negative: a column name that exists nowhere must not appear written.
    assert not re.search(r"(\.nonexistent_column_xyz\s*=[^=]|\bnonexistent_column_xyz\s*=[^=])",
                         bodies)
