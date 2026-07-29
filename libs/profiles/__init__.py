"""The user's profile as files, and what a generation call is grounded on.

**This is the port of `cwd=REPO_ROOT`.** The reference instance runs the Claude CLI from its own
checkout, so `CLAUDE.md` and the seven skill files under `.claude/skills/` are loaded automatically
on every call. Its 2026-07-15 A/B measured what that is worth: stripped of them, the model invented
an employer and fabricated metrics. A hosted platform has no per-user checkout, so the same files
live in Postgres and this module assembles them explicitly.

## Three classes of file, and only one of them is per user

- **Platform-owned** (`libs/prompts/`) — `SKILL.md`, `03-writing-style.md`,
  `06-cover-letter-templates.md`. Identical for everyone, because they describe *how to write*, not
  *who this person is*. `setup-prompts.md` states the rule they exist under: "never copy profile
  facts into a prompt — that duplicates the truth in two places and they drift."
- **Per-user** (`user_profile_files`) — `context.md` and the numbered skill files, written by the
  setup agents and editable by the user.
- **Scaffolds** (`libs/scaffolds/`) — the clean, `[PLACEHOLDER]`-filled versions seeded into a new
  account so the agents have something to fill and the user has something to read.

## The one rule that matters

**A scaffold never grounds a generation call.** Its placeholders are literal — `[YOUR NAME]`,
`[CITY, COUNTRY]` — and a document written against one would carry them onto a real application.
`filled` is the gate, and `grounding()` raises rather than quietly producing a worse document.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

_SCAFFOLDS = Path(__file__).resolve().parents[1] / "scaffolds"
_PROMPTS = Path(__file__).resolve().parents[1] / "prompts"

# Read on every generation call, same for every user. Ordered as the reference loads them: the
# workflow first, then how to write, then the letter's shape.
PLATFORM_FILES: tuple[str, ...] = (
    "SKILL.md",
    "03-writing-style.md",
    "06-cover-letter-templates.md",
)

# What a new account is seeded with, in the order they are concatenated into a call. `context.md`
# leads because it is the reference's `CLAUDE.md` — the document everything else defers to.
SEED_FILES: tuple[tuple[str, str], ...] = (
    ("context.md", "context"),
    ("01-candidate-profile.md", "skill"),
    ("02-behavioral-profile.md", "skill"),
    ("04-job-evaluation.md", "skill"),
    ("05-cv-templates.md", "skill"),
    ("07-interview-prep.md", "skill"),
    ("label.md", "prompt"),
    ("base_cv.html", "template"),
)

# The files a CV or cover-letter call is grounded on. `label.md` is deliberately absent: the
# labeller runs it standalone to keep labelling cheap, and it repeats the profile facts for exactly
# that reason (`libs/scaffolds/label.md`). `base_cv.html` is absent because it is not context — it
# is the document being edited, and it is passed as the BASE TEMPLATE instead.
GENERATION_FILES: tuple[str, ...] = (
    "context.md",
    "01-candidate-profile.md",
    "02-behavioral-profile.md",
    "05-cv-templates.md",
)

# What an unfilled scaffold looks like. `/setup` uses the first as its own "is this populated"
# check, so a user is considered set up by the same test the agent applies to itself.
PLACEHOLDER_MARKERS = ("[FILLED BY", "[YOUR NAME]", "[BRACKETED]", "[CITY, COUNTRY]")


class ProfileIncomplete(RuntimeError):
    """The account has no usable profile yet. Terminal — running setup is the fix, not a retry."""


@lru_cache(maxsize=32)
def scaffold(name: str) -> str:
    """A shipped scaffold, newline-normalised.

    Normalised for the reason `libs.prompts.load` gives: a checkout on Windows must not seed
    different bytes from one on Linux, or two boxes would disagree about a document that is
    supposed to be reproducible.
    """
    return (_SCAFFOLDS / name).read_text(encoding="utf-8").replace("\r\n", "\n")


@lru_cache(maxsize=16)
def platform_file(name: str) -> str:
    """A platform-owned prompt file — the same bytes for every user."""
    return (_PROMPTS / name).read_text(encoding="utf-8").replace("\r\n", "\n")


def is_filled(content: str) -> bool:
    """Whether a file has been populated, by the same test `/setup` applies to itself.

    Deliberately a marker check rather than a length or diff check: a user who legitimately writes
    a short profile is filled, and one who edits half a scaffold and leaves `[YOUR NAME]` in the
    header is not.
    """
    return bool(content.strip()) and not any(m in content for m in PLACEHOLDER_MARKERS)


def grounding(files: dict[str, str]) -> str:
    """The system prompt for a generation call: platform files, then this user's own.

    `files` is `{name: content}` for the user, so this stays a pure function — the tests drive it
    without a database, and the caller owns the query.

    Raises `ProfileIncomplete` rather than grounding on a scaffold. A CV carrying `[YOUR NAME]`
    looks like a bug to whoever receives it and like a working product to whoever generated it,
    which is the failure this check exists to prevent.
    """
    usable = {name: text for name, text in files.items()
              if name in GENERATION_FILES and is_filled(text)}
    if "context.md" not in usable:
        raise ProfileIncomplete(
            "this account has no completed profile — finish setup before generating documents")

    sections = [platform_file(name) for name in PLATFORM_FILES]
    sections += [usable[name] for name in GENERATION_FILES if name in usable]
    return "\n\n---\n\n".join(sections)


def seed_files() -> list[tuple[str, str, str]]:
    """`(name, kind, content)` for a new account — every scaffold, unfilled.

    Seeded at signup rather than at first use so the setup agent has files to read and edit rather
    than having to invent the structure, which is what keeps one user's `05-cv-templates.md` the
    same shape as another's.
    """
    return [(name, kind, scaffold(name)) for name, kind in SEED_FILES]
