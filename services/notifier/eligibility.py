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

from libs.corpus.ranking import priority_key
from libs.limits import limit_default

logger = logging.getLogger(__name__)

# How many scored rows to consider per digest slot. Five is enough that a heavily filtered feed
# still fills, and small enough that the language detector is not run over a thousand descriptions.
CANDIDATE_MULTIPLIER = 5

# LinkedIn's display cap, and **the operator's per-user limit is the live one** — this is only the
# fallback for a caller that has no user to resolve against (a test, a direct construction).
#
# It used to be the only value, read via `config.get("applicant_cap", …)` from a config field
# nothing writes, so it was a constant with a lookup in front of it. Measured 2026-08-05: it was
# dropping 87 of one account's 90 fresh matches, and nobody could see it or change it
# (ROADMAP 6ag). `applicant_glut` treats 0 as off, which is what unlimited resolves to.
DEFAULT_APPLICANT_CAP = limit_default("applicant_cap")



def is_eligible(item) -> tuple[bool, str | None]:
    """Whether to send this job, and the reason if not.

    **One check left, and it is the only one that could not have been made earlier.** Everything
    this used to decide — excluded words, the applicant ceiling, workplace mode, language, the
    freshness window — now runs in `libs.tenant.filters` *before the posting is scored*, so a row
    that reaches here has already been judged deliverable by the account's own filters.

    That move is what fixed the disagreement: those checks running at send time meant a posting the
    account could never receive was still embedded, scored, and counted as "waiting" by the health
    page and by the radar, which then reported a backlog the notifier would never send. One account
    held a permanent "1 job waiting, last delivery 2h ago" alert on exactly that (ROADMAP 6bb).

    What remains is `closed`, and it cannot move: it is learned from the posting's own page during
    the pre-send refresh, seconds before the card goes out, and a job that closed since we scored it
    is the one thing no earlier filter could have known.
    """
    return (False, "closed") if getattr(item, "closed", False) else (True, None)


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


def select(items: list, *, limit: int,
           first_seen: dict[int, object] | None = None,
           now=None) -> tuple[list, dict[str, int]]:
    """Rank, drop anything that closed since it was scored, and take the top `limit`.

    **This used to be where the feed was filtered, and it is not any more.** Excluded words, the
    applicant ceiling, workplace, language and the age window all run in `libs.tenant.filters`
    before a posting is scored, so what arrives here has already been judged deliverable by the
    account's own rules. That move is what stopped the health page counting a backlog the notifier
    would never send (ROADMAP 6bb).

    `now` is threaded through rather than left to each check's own default. Two calls that read the
    clock a millisecond apart are harmless in production and are the difference between a passing
    and a failing suite at midnight — one decision about what time it is, made once.
    """
    kept, dropped = [], {}
    for item in items:
        eligible, reason = is_eligible(item)
        if eligible:
            kept.append(item)
        else:
            dropped[reason] = dropped.get(reason, 0) + 1
    return rank(kept, first_seen=first_seen, now=now)[:limit], dropped
