"""the per-user grounding document

Revision ID: d5f2c8a41b93
Revises: c4d1a7b93e58
Create Date: 2026-07-29 10:00:00.000000

**The reference instance's CLAUDE.md, per user.**

Its ~434-line profile document is auto-loaded by the Claude CLI on every call, and the 2026-07-15
A/B measured what it is worth: stripped of it, the model invented an employer and fabricated
metrics. That document — not the prompt, not the template — is why the reference's CVs read like a
person wrote them.

The platform had no equivalent. `facts_md` is rendered from `ProfileSchema`, which means it can only
ever contain what a schema has fields for: no methodology sections, no writing rules, no worked
judgement about what to lead with for a given kind of role. The result was a CV built from a list of
skills.

So a user gets a **profile context document** of their own. It is prose, it is theirs, and it is
injected as the system prompt for every generation call. `facts_md` stays as the deterministic
backstop for an account that has not got one yet.

Nullable: an account without one falls back to the rendered facts block, which is what every
existing account has today.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d5f2c8a41b93"
down_revision: str | None = "c4d1a7b93e58"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("profile_context", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "profile_context")
