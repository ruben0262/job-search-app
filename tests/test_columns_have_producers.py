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
    # A column default on the model (`default="free"`), applied by SQLAlchemy on insert. The plan
    # is a label shown on the account and admin pages; the limits that used to hang off it are now
    # `libs.limits` (ROADMAP 6as).
    "plan_id": "model column default, applied on insert",
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
    # `quota_overrides` and `profile_context` were here and are now dropped — the first with the
    # whole second limits system it belonged to, the second because `context.md` in
    # `user_profile_files` is the live version of the same idea (ROADMAP 6as).
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


# ── one name, one definition (ROADMAP 6at) ────────────────────────────────────────────────────

# Names that legitimately appear in more than one module because they mean different things there.
# **Each entry is a claim that the collision is intentional**, and the value differing between them
# is the evidence — a per-call token ceiling for a CV is not the one for a setup chat.
KNOWN_COLLISIONS = {
    "MAX_TOKENS": "per-call model ceilings: agents 32k, capture 16k, docgen 32k",
    "THINKING_TOKENS": "per-call, same reason as MAX_TOKENS",
    "MIN_DESCRIPTION": "corpus re-fetch threshold (200) vs the scorer's usable-text floor (100)",
    "TIMEOUT": "per-adapter HTTP timeouts, tuned to each board",
    "_TIMEOUT": "per-adapter HTTP timeouts, tuned to each board",
    "DEFAULT_TIMEOUT": "per-adapter HTTP timeouts, tuned to each board",
    "BATCH_SIZE": "per-lane batch sizes",
    "MAX_RETRIES": "per-client retry budgets",
    "MAX_PER_RUN": "per-lane batch ceilings: labeller, shadow lane, score lane",
    "SYSTEM_PROMPT": "each agent's own prompt",
    "_HERE": "the `Path(__file__).parent` idiom; per-module by definition",
    "URL": "per-test-file fixtures in the embedder suite",
    "_JD": "per-test-file fixtures in the embedder suite",
    "_FORMATS": "date formats, per parser",

    # **These four are real duplicates and are listed rather than fixed**, because each is a small
    # consolidation with its own blast radius and doing them in one sweep would make a single
    # revert impossible. Recorded so they are chosen rather than forgotten (ROADMAP 6at):
    #
    #   _UA               one user-agent string copied into four adapters
    #   CLAUDE_KINDS      the claude credential kinds, in three modules
    #   CLAUDE_OAUTH_BETA the beta header, in two
    #   TOKEN_PURPOSES    auth token purposes, in two
    #   WRITEABLE         the writeable-file list, in two
    #   HU_SOURCES        the Hungarian source list, in two
    #   _ONSITE_RE/_REMOTE_RE  workplace regexes in workfilter and features
    "_UA": "TODO: one user agent, copied into four adapters",
    "CLAUDE_KINDS": "TODO: the claude credential kinds, in three modules",
    "CLAUDE_OAUTH_BETA": "TODO: the beta header, in two",
    "TOKEN_PURPOSES": "TODO: auth token purposes, in two",
    "WRITEABLE": "TODO: the writeable-file list, in two",
    "HU_SOURCES": "TODO: the Hungarian source list, in two",
    "_ONSITE_RE": "TODO: workplace regexes duplicated in workfilter and features",
    "_REMOTE_RE": "TODO: workplace regexes duplicated in workfilter and features",
}


def test_a_constant_is_defined_in_one_place():
    """**The defect this project has shipped most often, caught by name.**

    Six of one week's sixteen defects were one value defined twice: a daily cap in four places, a
    retrain gate in two, a tick in three. The failure is invisible — both copies are plausible, one
    is enforced, and nothing errors. Where two modules need the same number, it goes in `libs` and
    both import it (CLAUDE.md §5).

    A genuine collision — the same word meaning different things — goes in `KNOWN_COLLISIONS` with
    what each one means, which forces the question to be answered rather than assumed.
    """
    import ast
    from collections import defaultdict

    homes: dict[str, list[str]] = defaultdict(list)
    for path in source_files():
        if "migrations" in str(path):
            continue
        for node in ast.parse(path.read_text(encoding="utf-8")).body:
            if (isinstance(node, ast.Assign) and len(node.targets) == 1
                    and isinstance(node.targets[0], ast.Name)
                    and node.targets[0].id.isupper()):
                homes[node.targets[0].id].append(str(path))

    duplicated = {name: where for name, where in homes.items()
                  if len(where) > 1 and name not in KNOWN_COLLISIONS}

    assert not duplicated, (
        "these constants are defined in more than one module:\n  "
        + "\n  ".join(f"{name}: {', '.join(where)}" for name, where in sorted(duplicated.items()))
        + "\n\nPut the value in `libs` and import it from both, or add it to KNOWN_COLLISIONS "
          "saying what each one means.")
