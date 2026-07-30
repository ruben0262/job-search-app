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
from datetime import UTC, datetime

from libs.corpus import langfilter, workfilter
from libs.corpus.ranking import applicant_glut, opportunity_score, priority_key

logger = logging.getLogger(__name__)

# How many scored rows to consider per digest slot. Five is enough that a heavily filtered feed
# still fills, and small enough that the language detector is not run over a thousand descriptions.
CANDIDATE_MULTIPLIER = 5

# LinkedIn's display cap. A listing pinned at "over 200 applicants" is nearly always stale or
# reposted; 0 disables the drop for a user who would rather see everything.
DEFAULT_APPLICANT_CAP = 200

# The window a user gets if they never said. Matches `SearchSection.posted_within_hours`.
DEFAULT_MAX_AGE_HOURS = 24


@dataclass(frozen=True)
class Eligibility:
    """One user's filters, read from their config once per delivery rather than per job."""

    languages: set[str]
    onsite_locations: list[str]
    # Terms the user typed specifically in order not to see them. **A hard no at delivery, not a
    # scoring input.** They were honoured only by the cold-start keyword scorer, which means they
    # quietly stopped applying the moment an account got a trained model — and "I told it never to
    # show me contract roles and it started again" is indistinguishable from the product ignoring
    # them, because that is what it was doing.
    exclude_keywords: tuple[str, ...] = ()
    applicant_cap: int = DEFAULT_APPLICANT_CAP
    # How old a posting may be and still be worth sending. **The user's own answer, applied here.**
    # The harvester fetches at the *widest* window any active account asked for, because the corpus
    # is shared and one fetch serves everyone — which means a user who asked for six hours was
    # being sent whatever the most patient account on the platform wanted. This is where their
    # answer is honoured.
    max_age_hours: int = DEFAULT_MAX_AGE_HOURS

    @classmethod
    def from_config(cls, config: dict | None) -> Eligibility:
        """Build from a stored `user_config.config`. A missing key means "no opinion", not "none".

        That reading matters for `languages` especially: an account that never answered the
        question must not have its entire feed dropped for being in the wrong language.
        """
        config = config or {}
        # The **widest** of their sections, not the narrowest. A user with a six-hour window on one
        # search and a week on another asked for both, and a job cannot be attributed back to the
        # section that found it — so the generous reading is the only one that cannot silently
        # delete something they asked for.
        windows = [int(section.get("posted_within_hours") or 0)
                   for section in config.get("sections") or []]
        return cls(
            languages={str(code).lower() for code in config.get("languages") or []},
            onsite_locations=workfilter.onsite_locations(config.get("onsite_locations")),
            applicant_cap=int(config.get("applicant_cap", DEFAULT_APPLICANT_CAP)),
            max_age_hours=max([window for window in windows if window > 0],
                              default=DEFAULT_MAX_AGE_HOURS),
            exclude_keywords=tuple(
                term.strip().lower()
                for section in config.get("sections") or []
                for term in section.get("exclude_keywords") or []
                if term and term.strip()),
        )


def too_old(item, rules: Eligibility, *, first_seen=None, now=None) -> bool:
    """Whether this posting is past the window the user asked for.

    Falls back to `first_seen_at` when the source gave no posted date, which most do not — when we
    found it is a weaker answer than when it was posted, but it is a real one, and it is the same
    fallback the recency buckets use.

    **Unknown means keep.** With neither date there is no evidence of age, and dropping on an
    absent field would silently empty the feed of every source that does not report one. The
    reference's `is_within_hours` makes the same call for the same reason.
    """
    when = item.posted_at or first_seen
    if when is None:
        return False
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    hours = ((now or datetime.now(UTC)) - when).total_seconds() / 3600
    return hours > rules.max_age_hours


def is_eligible(item, rules: Eligibility, *, first_seen=None, now=None) -> tuple[bool, str | None]:
    """Whether to send this job, and the reason if not.

    The reason is returned rather than logged here so the caller can count them: "delivered 2 of
    40" is alarming and unactionable, while "dropped 38 for language" is a config problem somebody
    can fix in a minute.

    **Two kinds of check, in order.** The first two are facts about the posting — it is closed, or
    it is older than the user asked for — and they are not judgements about anybody's preferences.
    The rest are preferences, and every one of them fails open.
    """
    fact = _unusable(item, rules, first_seen=first_seen, now=now)
    if fact is not None:
        return False, fact
    return _preferences(item, rules)


def _unusable(item, rules: Eligibility, *, first_seen, now) -> str | None:
    """Facts about the posting that make it not worth sending, whoever the user is."""
    if getattr(item, "closed", False):
        # Learned during the pre-send refresh. There is nothing to apply for.
        return "closed"
    if too_old(item, rules, first_seen=first_seen, now=now):
        return "too_old"
    return None


def _preferences(item, rules: Eligibility) -> tuple[bool, str | None]:
    """What this particular user does and does not want to see. Every check fails open."""
    if rules.exclude_keywords:
        # Title and company only, never the description. A posting that merely *mentions* a term
        # in a benefits paragraph is not the job the user was excluding, and matching the body
        # turned "no recruitment agencies" into a filter that removed most of a feed.
        haystack = f"{item.title or ''} {item.company or ''}".lower()
        for term in rules.exclude_keywords:
            if term in haystack:
                return False, "excluded_keyword"

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


def rank(items: list, *, first_seen: dict[int, object] | None = None, now=None) -> list:
    """Sort by opportunity, best first. Fit is the tiebreaker only.

    `first_seen` supplies `first_seen_at` per job id for the postings whose source gave no posted
    date — which LinkedIn frequently does not. When we found it is a worse answer than when it was
    posted, but it is a real one, and the alternative is treating every such job as old.
    """
    first_seen = first_seen or {}
    return sorted(items, key=lambda item: priority_key(
        posted_at=item.posted_at, first_seen_at=first_seen.get(item.job_id),
        applicants=item.applicants, reposted=item.reposted,
        workplace=item.workplace, score=int(item.score or 0), now=now))


def select(items: list, rules: Eligibility, *, limit: int,
           first_seen: dict[int, object] | None = None,
           now=None) -> tuple[list, dict[str, int]]:
    """Filter, rank, and take the top `limit`. Returns the items and why the rest were dropped.

    `now` is threaded through rather than left to each check's own default. Two calls that read the
    clock a millisecond apart are harmless in production and are the difference between a passing
    and a failing test suite at midnight — one decision about what time it is, made once.
    """
    kept, dropped = [], {}
    for item in items:
        eligible, reason = is_eligible(item, rules, now=now,
                                       first_seen=first_seen.get(item.job_id)
                                       if first_seen else None)
        if eligible:
            kept.append(item)
        else:
            dropped[reason] = dropped.get(reason, 0) + 1
    return rank(kept, first_seen=first_seen, now=now)[:limit], dropped


def worth_applying_now(item, *, first_seen: object | None = None,
                       accepting: bool | None = None) -> int:
    """The 0–100 opportunity score for one item, for display and for `/rate` ordering."""
    return opportunity_score(posted_at=item.posted_at, first_seen_at=first_seen,
                             applicants=item.applicants, reposted=item.reposted,
                             accepting=accepting)
