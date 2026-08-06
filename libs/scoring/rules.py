"""Stage 0: scoring a job from the config alone."""
from __future__ import annotations

import re
from dataclasses import dataclass

# The bands the cold-start pool is drawn from (`COLD_START_PLAN.md` §4).
POOL_BANDS = ((0.40, "high"), (0.40, "mid"), (0.20, "low"))

# A title match is worth far more than a description match.
TITLE_WEIGHT = 75
DESCRIPTION_WEIGHT = 25

# Only the first stretch of a description is read: requirements sit at the top, boilerplate below.
DESC_CHARS = 2_500


@dataclass(frozen=True)
class RuleScore:
    """A score, or an exclusion with a reason."""

    score: int | None
    reason: str = ""

    @property
    def excluded(self) -> bool:
        return self.score is None


def _compile(term: str) -> re.Pattern:
    """Word-boundary match, tolerant of the space/hyphen split."""
    parts = [re.escape(part) for part in re.split(r"[\s-]+", term.strip()) if part]
    return re.compile(r"\b" + r"[\s-]+".join(parts) + r"\b", re.IGNORECASE)


def score_job(config: dict, *, title: str, description: str | None = None,
              is_remote: bool | None = None) -> RuleScore:
    """Score one posting against one config."""
    title = title or ""
    body = (description or "")[:DESC_CHARS]
    haystack = f"{title}\n{body}"

    best: RuleScore | None = None
    for section in config.get("sections") or []:
        result = _score_section(section, title=title, body=body, haystack=haystack,
                                is_remote=is_remote)
        if result.excluded:
            # An exclusion in one section does not exclude the job outright — another section may want it.
            best = best or result
            continue
        if best is None or best.excluded or result.score > best.score:
            best = result
    return best or RuleScore(None, "no search sections configured")


def _score_section(section: dict, *, title: str, body: str, haystack: str,
                   is_remote: bool | None) -> RuleScore:
    for term in section.get("exclude_keywords") or []:
        if _compile(term).search(haystack):
            # A hard no.
            return RuleScore(None, f"excluded by your keyword {term!r}")

    policy = section.get("remote_policy") or "remote_or_hybrid"
    if policy == "remote_only" and is_remote is False:
        return RuleScore(None, "you asked for remote only")

    keywords = section.get("keywords") or []
    in_title = any(_compile(term).search(title) for term in keywords)
    in_body = any(_compile(term).search(body) for term in keywords)
    if not (in_title or in_body):
        return RuleScore(None, "none of your keywords appear in it")

    score = TITLE_WEIGHT if in_title else 0
    score += DESCRIPTION_WEIGHT if in_body else 0
    return RuleScore(min(score, 100))
