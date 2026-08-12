"""drop targets.company_size from stored profiles

Revision ID: b2c4e8f60a19
Revises: e91c47d3b285
Create Date: 2026-08-12 09:00:00.000000

`Targets.company_size` had **no consumer anywhere** — not the CV prompt, not the cover letter, not
the scoring features, not the panel. It was captured by an LLM call, validated, stored, and read by
nothing. It is also one of the two fields whose length cap dead-lettered a real account's onboarding
on 2026-08-11.

**The data has to move with the schema, and this is the whole reason this migration exists.**
`ProfileSchema` is `extra="forbid"`: a key left behind after its field is deleted does not degrade
gracefully, it makes `model_validate` raise, and `docgen` then cannot build a CV for that account at
all. Both live accounts carried the key (one with a value, one null), so removing the field without
this would have broken every document build on the platform.

Stripped from `profile` and `profile_draft` alike — the draft is the same shape awaiting confirmation.

`PROFILE_SCHEMA_VERSION` goes to 3 with it. Nothing reads that column today, which is worth knowing
rather than relying on: the safety here is this migration, not the version number.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = 'b2c4e8f60a19'
down_revision: str | None = 'e91c47d3b285'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# One statement per column, guarded so a row without `targets` is untouched rather than rewritten.
_STRIP = """
UPDATE users
   SET {column} = jsonb_set({column}, '{{targets}}', ({column} -> 'targets') - 'company_size')
 WHERE {column} ? 'targets'
   AND ({column} -> 'targets') ? 'company_size'
"""


def upgrade() -> None:
    for column in ("profile", "profile_draft"):
        op.execute(_STRIP.format(column=column))


def downgrade() -> None:
    # Deliberately empty. The values are gone and nothing read them, so inventing one back would be
    # fabricating a preference on the candidate's behalf — worse than the absence.
    pass
