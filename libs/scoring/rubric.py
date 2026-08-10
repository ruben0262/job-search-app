"""Versioning a user's rubric.

**A rubric is a file, not a rendering.** `label.md` is in `libs.profiles.SEED_FILES`, so every
account is created with one, and the setup agent mirrors it onto `users.rubric_md` as it writes it
(`libs/agents/tools.py::_sync_rubric`). One mechanism, one owner, one place to look.

This module used to render a second one from the confirmed profile schema, which
`TenantRepository.save_profile` then wrote over the top. So storing a profile could replace a
person's own tuned file — thousands of characters, written for them — with a generated summary,
and bump `profile_generation` while doing it, staling every label they had already paid for.
Removed 2026-08-10 by the owner's instruction: nothing rewrites a rubric from a schema.

What is left is the version stamp, which hashes whatever the rubric actually *is*.
"""
from __future__ import annotations

import hashlib


def profile_version(rubric_md: str) -> str:
    """The label scope. Short on purpose — it appears on every label row and in every log line."""
    return hashlib.sha256(rubric_md.encode("utf-8")).hexdigest()[:8]
