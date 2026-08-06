"""The user's profile as files, and what a generation call is grounded on."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

_SCAFFOLDS = Path(__file__).resolve().parents[1] / "scaffolds"
_PROMPTS = Path(__file__).resolve().parents[1] / "prompts"

# Read on every generation call, same for every user.
PLATFORM_FILES: tuple[str, ...] = (
    "SKILL.md",
    "03-writing-style.md",
    "06-cover-letter-templates.md",
)

# What a new account is seeded with, in the order they are concatenated into a call.
SEED_FILES: tuple[tuple[str, str], ...] = (
    ("context.md", "context"),
    ("01-candidate-profile.md", "skill"),
    ("02-behavioral-profile.md", "skill"),
    ("04-job-evaluation.md", "skill"),
    ("05-cv-templates.md", "skill"),
    ("07-interview-prep.md", "skill"),
    # The part of CV tailoring that cannot be shared.
    ("08-role-angles.md", "skill"),
    ("label.md", "prompt"),
    # The task prompts are per user too.
    ("tailor_cv.md", "prompt"),
    ("tailor_cover.md", "prompt"),
    # Staged rather than wired: nothing calls these yet (`REBUILD_PLAN.md` phases 3–4).
    ("score.md", "prompt"),
    ("interview.md", "prompt"),
    ("outreach.md", "prompt"),
    ("build_cvs.md", "prompt"),
    ("base_cv.html", "template"),
)

# The files the setup agent and the setup UI may write.
WRITEABLE: frozenset[str] = frozenset(name for name, _kind in SEED_FILES)

# The files a CV or cover-letter call is grounded on.
GENERATION_FILES: tuple[str, ...] = (
    "context.md",
    "01-candidate-profile.md",
    "02-behavioral-profile.md",
    "05-cv-templates.md",
)

# What an unfilled scaffold looks like.
PLACEHOLDER_MARKERS = ("[FILLED BY", "[YOUR NAME]", "[BRACKETED]", "[CITY, COUNTRY]")

# Files that ship usable rather than as placeholders: framework rules and the task prompts.
SHIPS_USABLE = ("05-cv-templates.md", "tailor_cv.md", "tailor_cover.md", "score.md",
                "interview.md", "outreach.md", "build_cvs.md")


class ProfileIncomplete(RuntimeError):
    """The account has no usable profile yet. Terminal — running setup is the fix, not a retry."""


@lru_cache(maxsize=32)
def scaffold(name: str) -> str:
    """A shipped scaffold, newline-normalised."""
    return (_SCAFFOLDS / name).read_text(encoding="utf-8").replace("\r\n", "\n")


@lru_cache(maxsize=16)
def platform_file(name: str) -> str:
    """A platform-owned prompt file — the same bytes for every user."""
    return (_PROMPTS / name).read_text(encoding="utf-8").replace("\r\n", "\n")


def setup_files() -> tuple[str, ...]:
    """The files the setup agent is responsible for producing, and a user must have before training."""
    return tuple(name for name, _kind in SEED_FILES if not ships_usable(name))


def missing_setup(files: dict[str, str]) -> list[str]:
    """Which setup files are absent or still a scaffold."""
    return [name for name in setup_files()
            if name not in files or not is_filled(files[name])]


def is_filled(content: str) -> bool:
    """Whether a file has been populated, by the same test `/setup` applies to itself."""
    return bool(content.strip()) and not any(m in content for m in PLACEHOLDER_MARKERS)


def grounding(files: dict[str, str]) -> str:
    """The system prompt for a generation call: platform files, then this user's own."""
    usable = {name: text for name, text in files.items()
              if name in GENERATION_FILES and is_filled(text)}
    if "context.md" not in usable:
        raise ProfileIncomplete(
            "this account has no completed profile — finish setup before generating documents")

    sections = [platform_file(name) for name in PLATFORM_FILES]
    sections += [usable[name] for name in GENERATION_FILES if name in usable]
    return "\n\n---\n\n".join(sections)


def seed_files() -> list[tuple[str, str, str]]:
    """`(name, kind, content)` for a new account — every scaffold, unfilled."""
    return [(name, kind, scaffold(name)) for name, kind in SEED_FILES]


def ships_usable(name: str) -> bool:
    """Whether this file is usable as shipped rather than a placeholder to fill."""
    return name in SHIPS_USABLE
