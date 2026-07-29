"""Which of a user's scored jobs are worth sending, and in what order.

**The score says whether a job fits. This says whether it is worth acting on.** The reference keeps
those apart deliberately and the platform had collapsed them: `pending_feed` sorted by score and
sent the top ten, so a perfect-fit posting three weeks old with two hundred applicants outranked a
good one posted an hour ago. The first is almost certainly filled; the second is winnable.

Three things happen here, in the order the reference does them:

1. **Drop what the user cannot act on** — a language they do not write, a workplace mode they have
   excluded, a listing already drowning in applicants.
2. **Rank what is left by opportunity** — youth and scarcity, not fit (`libs/corpus/ranking.py`).
3. **Take the top of that ranking**, not the top of the score ordering.

**Every filter fails open.** An undetectable language, an unclassifiable workplace, an unknown
applicant count: all keep the job. The asymmetry is the point — a false drop loses a real job
silently and the user never learns it existed, while a false keep costs one card they scroll past.

The candidate pool is deliberately wider than the digest. Filtering after selection would mean a
digest of three when seven were eligible, because four dropped out of a ten-row window.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from libs.corpus import langfilter, workfilter
from libs.corpus.ranking import applicant_glut, opportunity_score, priority_key

logger = logging.getLogger(__name__)

# How many scored rows to consider per digest slot. Five is enough that a heavily filtered feed
# still fills, and small enough that the language detector is not run over a thousand descriptions.
CANDIDATE_MULTIPLIER = 5

# LinkedIn's display cap. A listing pinned at "over 200 applicants" is nearly always stale or
# reposted; 0 disables the drop for a user who would rather see everything.
DEFAULT_APPLICANT_CAP = 200


@dataclass(frozen=True)
class Eligibility:
    """One user's filters, read from their config once per delivery rather than per job."""

    languages: set[str]
    onsite_locations: list[str]
    applicant_cap: int = DEFAULT_APPLICANT_CAP

    @classmethod
    def from_config(cls, config: dict | None) -> Eligibility:
        """Build from a stored `user_config.config`. A missing key means "no opinion", not "none".

        That reading matters for `languages` especially: an account that never answered the
        question must not have its entire feed dropped for being in the wrong language.
        """
        config = config or {}
        return cls(
            languages={str(code).lower() for code in config.get("languages") or []},
            onsite_locations=workfilter.onsite_locations(config.get("onsite_locations")),
            applicant_cap=int(config.get("applicant_cap", DEFAULT_APPLICANT_CAP)),
        )


def is_eligible(item, rules: Eligibility) -> tuple[bool, str | None]:
    """Whether to send this job, and the reason if not.

    The reason is returned rather than logged here so the caller can count them: "delivered 2 of
    40" is alarming and unactionable, while "dropped 38 for language" is a config problem somebody
    can fix in a minute.
    """
    if applicant_glut(source=item.source, applicants=item.applicants,
                      cap=rules.applicant_cap):
        return False, "applicant_glut"

    # `explicit=` is the tag parsed off the posting's own page, and it wins over every heuristic:
    # it is what the employer wrote, not what the title implies.
    mode = workfilter.classify(item.title or "", item.location or "", item.description,
                               item.is_remote, explicit=item.workplace)
    if not workfilter.is_allowed(mode, item.location or "", rules.onsite_locations):
        return False, "workplace"

    # Language last: it is the only check that runs a detector, and the cheap checks have already
    # removed most of what it would have been run on.
    if not langfilter.is_allowed(item.description or "", rules.languages, source=item.source):
        return False, "language"

    return True, None


def rank(items: list, *, first_seen: dict[int, object] | None = None) -> list:
    """Sort by opportunity, best first. Fit is the tiebreaker only.

    `first_seen` supplies `first_seen_at` per job id for the postings whose source gave no posted
    date — which LinkedIn frequently does not. When we found it is a worse answer than when it was
    posted, but it is a real one, and the alternative is treating every such job as old.
    """
    first_seen = first_seen or {}
    return sorted(items, key=lambda item: priority_key(
        posted_at=item.posted_at, first_seen_at=first_seen.get(item.job_id),
        applicants=item.applicants, reposted=item.reposted,
        workplace=item.workplace, score=int(item.score or 0)))


def select(items: list, rules: Eligibility, *, limit: int,
           first_seen: dict[int, object] | None = None) -> tuple[list, dict[str, int]]:
    """Filter, rank, and take the top `limit`. Returns the items and why the rest were dropped."""
    kept, dropped = [], {}
    for item in items:
        eligible, reason = is_eligible(item, rules)
        if eligible:
            kept.append(item)
        else:
            dropped[reason] = dropped.get(reason, 0) + 1
    return rank(kept, first_seen=first_seen)[:limit], dropped


def worth_applying_now(item, *, first_seen: object | None = None,
                       accepting: bool | None = None) -> int:
    """The 0–100 opportunity score for one item, for display and for `/rate` ordering."""
    return opportunity_score(posted_at=item.posted_at, first_seen_at=first_seen,
                             applicants=item.applicants, reposted=item.reposted,
                             accepting=accepting)
