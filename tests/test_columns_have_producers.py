"""Every column the product reads is written by something."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

CODE = [Path("libs"), Path("services")]

# Columns that are genuinely written by the database or by Alembic rather than by application code.
WRITTEN_ELSEWHERE = {
    "id": "primary keys are the database's",
    "created_at": "server_default",
    "first_seen_at": "server_default on insert",
    "updated_at": "server_default / onupdate",
    "sent_at": "server_default now() on the mail_sends insert",
    # A column default on the model (`default="free"`), applied by SQLAlchemy on insert.
    "plan_id": "model column default, applied on insert",
    # Not a column: the `User.secrets` relationship, which SQLAlchemy fills from `user_secrets` rows.
    # It passed only because the old onboarding gate had a local variable called `secrets`.
    "secrets": "relationship, loaded from user_secrets",
}

# Columns this test found on its first run, each a real answer to "what is silently absent".
KNOWN_UNWRITTEN = {
    # Deliberate, and documented at the reader.
    "daily_cap_mb": "intentional fallback; libs.limits.proxy_mb_per_day is the live value",
    # A second source of truth that disagrees with the first.
    "linkedin_interval_min": "superseded by libs.limits; column default 240 vs limit default 15",
    # The per-user bot's, retired 2026-10-28 for one platform bot; kept with their data, read by nothing.
    "telegram_routes": "retired with the per-user bot's channels; kept rather than dropped",
    "telegram_webhook_secret": "retired with the per-user bot's webhook; kept rather than dropped",
    # The switched-off model's scored-match lifecycle (CLAUDE.md §3 exception): its one writer,
    # `TenantRepository.retire_stale_matches`, was deleted with its last caller on 2026-10-28.
    "retired_at": "switched off with the model; its data and partial indexes are kept",
    # `quota_overrides` and `profile_context` were here and are now dropped.
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
    """The guard on the guard."""
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
    """An exemption list that outlives what it exempts is a lie."""
    declared = {name for names in declared_columns().values() for name in names}

    stale = [name for name in KNOWN_UNWRITTEN if name not in declared]
    assert not stale, f"these are exempted but no longer declared — remove them: {sorted(stale)}"

    now_written = [name for name in KNOWN_UNWRITTEN
                   if re.search(rf"(\.{name}\s*=[^=]|\b{name}\s*=[^=])", bodies)]
    assert not now_written, (
        f"these now have a producer, so the exemption is out of date: {sorted(now_written)}")


def test_the_check_can_tell_a_written_column_from_an_unwritten_one(bodies):
    """Proof the assertion above can fail.

    Pinned to `telegram_webhook_secret`, the defect that motivated it, until that column retired with
    the per-user bot (2026-10-28) — it is in `KNOWN_UNWRITTEN` now, which the test above holds to
    having no producer. The positive half is the chat id the Telegram link writes: lose that and
    nobody can be reached on Telegram.
    """
    assert re.search(r"\.telegram_chat_id\s*=[^=]", bodies), (
        "the Telegram link no longer records the chat it connected")

    # And the negative: a column name that exists nowhere must not appear written.
    assert not re.search(r"(\.nonexistent_column_xyz\s*=[^=]|\bnonexistent_column_xyz\s*=[^=])",
                         bodies)


# ── one name, one definition (ROADMAP 6at) ────────────────────────────────────────────────────

# Names that legitimately appear in more than one module because they mean different things there.
KNOWN_COLLISIONS = {
    # Per-call model ceilings.
    "MAX_TOKENS": "per-call: agents 32k, capture 16k, docgen 32k",
    "THINKING_TOKENS": "per-call, same reason as MAX_TOKENS",
    # Per-adapter HTTP timeouts, tuned to what each board tolerates.
    "TIMEOUT": "per-adapter",
    "_TIMEOUT": "per-adapter",
    "DEFAULT_TIMEOUT": "per-adapter",
    "_UA": "per-adapter: a browser string where a board expects one, a plain one for JSON APIs",
    # Per-lane sizing.
    "MAX_PER_RUN": "per-lane ceilings: labeller, shadow lane, score lane",
    "BATCH_SIZE": "per-lane",
    "MAX_RETRIES": "per-client",
    "MIN_DESCRIPTION": "corpus re-fetch threshold (200) vs the scorer's usable-text floor (100)",
    # Different regexes under one name.
    "_ONSITE_RE": "title/location signals vs description-strength phrases",
    "_REMOTE_RE": "title/location signals vs description-strength phrases",
    # Idioms and fixtures, per-module by definition.
    "_HERE": "the `Path(__file__).parent` idiom",
    "_FORMATS": "date formats, per parser",
    "SYSTEM_PROMPT": "each agent's own prompt",
    "URL": "per-test-file fixtures in the embedder suite",
    "_JD": "per-test-file fixtures in the embedder suite",
}


def test_a_constant_is_defined_in_one_place():
    """The defect this project has shipped most often, caught by name."""
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
