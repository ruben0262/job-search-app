"""Stage 0: scoring a job from the config alone. No model, no LLM, no embedding.

**What this is for.** A new account has no labels, no model, and no rubric until processing has run
and a first train has finished — ten to thirty minutes at best (`COLD_START_PLAN.md` §2). Without
this, their first impression of the product is an empty page, and the most common thing a person
does with an empty page is close it. This turns a 30-minute cold start into a 30-second first
impression, for free.

**It is deliberately crude and the API says so.** It reads the config the user just filled in — the
keywords they chose, the places they will work, whether remote is required — and nothing else. It
cannot tell a good backend role from a bad one, because nothing has told it what good means yet.
Its whole job is to be *non-empty and not obviously wrong* until the personal model exists.

**It also stratifies the cold-start pool** (§4). Labelling 300 random jobs is mostly wasted money:
most are obvious rejects and teach a classifier nothing. Sorting by this score and drawing from the
top, middle and bottom bands puts the money where the boundary is — and the boundary is where a
classifier actually learns.

Deterministic and dependency-free on purpose: it runs inside a request to show a match count while
somebody is still typing.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# The bands the cold-start pool is drawn from, and roughly what each teaches (`COLD_START_PLAN.md`
# §4): likely positives teach what "good" looks like, the middle is where the boundary is, and clear
# negatives are the calibration floor.
POOL_BANDS = ((0.40, "high"), (0.40, "mid"), (0.20, "low"))

# A title match is worth far more than a description match. "Backend Engineer" in the title is what
# the job *is*; the same words in the description are frequently "you will work with our backend
# engineers", which is a different job entirely.
TITLE_WEIGHT = 60
DESCRIPTION_WEIGHT = 15
LOCATION_WEIGHT = 25

# Only the first stretch of a description is read, for the same reason the labeller caps it:
# requirements and stack sit at the top and the tail is benefits boilerplate.
DESC_CHARS = 2_500

# Location values that mean "delivery region", not a place on a map. A posting marked remote
# satisfies these; a city name has to actually appear.
_REMOTE_LOCATIONS = {"remote_worldwide", "remote_eu", "remote_uk", "remote_us"}

# Region values that no posting's `location` string will ever spell the same way. Matching them
# textually would reject almost everything, so a posting is given the benefit of the doubt.
_REGION_LOCATIONS = {"european_union", "united_kingdom", "europe", "ireland"}


@dataclass(frozen=True)
class RuleScore:
    """A score, or an exclusion with a reason.

    The reason exists because "why is this not in my feed?" is otherwise unanswerable, and an
    unanswerable feed is one people stop trusting.
    """

    score: int | None
    reason: str = ""

    @property
    def excluded(self) -> bool:
        return self.score is None


def _compile(term: str) -> re.Pattern:
    """Word-boundary match, tolerant of the space/hyphen split.

    `full-stack` must match "Full Stack Developer" and vice versa — the reference instance shipped
    for months with a feature that silently read 0 for exactly this reason
    (`libs/scoring/features.py` carries the same rule).
    """
    parts = [re.escape(part) for part in re.split(r"[\s-]+", term.strip()) if part]
    return re.compile(r"\b" + r"[\s-]+".join(parts) + r"\b", re.IGNORECASE)


def score_job(config: dict, *, title: str, description: str | None = None,
              location: str | None = None, is_remote: bool | None = None) -> RuleScore:
    """Score one posting against one config. 0-100, or excluded.

    Reads every section: a job matching *any* section is in, because sections exist to express
    "different terms on LinkedIn than on the boards" rather than to narrow.
    """
    title = title or ""
    body = (description or "")[:DESC_CHARS]
    haystack = f"{title}\n{body}"

    best: RuleScore | None = None
    for section in config.get("sections") or []:
        result = _score_section(section, title=title, body=body, haystack=haystack,
                                location=location, is_remote=is_remote)
        if result.excluded:
            # An exclusion in one section does not exclude the job outright — another section may
            # want it. Only remembered if nothing better turns up.
            best = best or result
            continue
        if best is None or best.excluded or result.score > best.score:
            best = result
    return best or RuleScore(None, "no search sections configured")


def _score_section(section: dict, *, title: str, body: str, haystack: str,
                   location: str | None, is_remote: bool | None) -> RuleScore:
    for term in section.get("exclude_keywords") or []:
        if _compile(term).search(haystack):
            # A hard no. The user typed this term specifically to not see it, so a high keyword
            # score elsewhere must not outvote it.
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
    score += _location_points(section.get("locations") or [], location, is_remote)
    return RuleScore(min(score, 100))


def _location_points(wanted: list[str], location: str | None, is_remote: bool | None) -> int:
    """Points for the job being somewhere the user will work.

    **Absence of evidence is not a mismatch.** Plenty of postings have no usable location string,
    and scoring those to zero would bury them under worse jobs that happened to name a city.
    """
    if not wanted:
        return LOCATION_WEIGHT
    if is_remote and any(value in _REMOTE_LOCATIONS for value in wanted):
        return LOCATION_WEIGHT
    if any(value in _REGION_LOCATIONS for value in wanted):
        # No posting spells "european_union" in its location field. Matching it textually would
        # reject nearly everything, so the region is treated as satisfied and the model sorts it
        # out later.
        return LOCATION_WEIGHT
    if not location:
        return LOCATION_WEIGHT // 2
    haystack = location.casefold()
    if any(value.replace("_", " ") in haystack for value in wanted):
        return LOCATION_WEIGHT
    return 0
