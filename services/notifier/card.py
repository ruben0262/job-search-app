"""One job as a Telegram card. **A port of `watcher/feed.py`, line for line.**

The platform shipped a three-line message — score, title, company, URL — while the reference
instance had spent months arriving at a card that answers, in one glance, every question somebody
asks before opening a posting: how good is it, how fresh, how contested, where, what does it pay,
and what would I be asking for. Losing that was not a simplification; it removed the information the
feed exists to deliver.

Every element below is the reference's, in its order, with its emoji and its separator:

    🟢 <b>85</b> · Staff Engineer (Backend, Infrastructure & AI) ⚡
    Just Move In · linkedin · 👥25 🟡 · <1h
    📍 Greater London, England, UK · 🌍 Remote
    💰 not listed · 🎯 ask 48k–65k GBP/yr
    https://www.linkedin.com/jobs/view/4444687442

- **🟢/🟡** is the fit band, not the score — a high match and a borderline one read differently at a
  glance even when the numbers are close.
- **⚡** marks easy-apply and **♻️** a repost. Both change whether it is worth the next ten minutes.
- **👥** carries its own colour: the reference buckets applicant counts, because "25 applicants" and
  "over 200" are different jobs, and the dot is what makes that legible before the number is read.
- **💰 and 🎯 are two different things on purpose.** What the posting lists, and what this user
  should be asking for. A gap between them is information — the reference calls it a do-not-undersell
  policy — and `⚠️ below target` is the flag for when the posting is clearly under.

**HTML parse mode, and every field escaped.** A job title containing `&` or `<` — `M&S`, `<1h` in
the age label — silently fails Telegram's parser and the whole card arrives as raw markup or not at
all.
"""
from __future__ import annotations

import html
from dataclasses import dataclass

from libs.corpus import salary as salarylib
from libs.corpus.filters import age_label
from libs.corpus.workfilter import classify

# The reference's own thresholds for the applicant dot: how contested a posting is, in four steps.
# 200 is where LinkedIn stops counting and says "Over 200", which is also its strongest stale tell.
APPLICANT_BUCKETS = (10, 50, 200)
APPLICANT_DOTS = ("🟢", "🟡", "🟠", "🔴")

# The score at or above which a match is a 🟢 rather than a 🟡.
#
# **60, which is the delivery cutoff, and that is the whole point.** The reference calls a job
# "high" at `min_score_for_cv` — the same number it will auto-build a CV at — so the dot means "this
# one clears the bar", not "this one is exceptional".
#
# It was 80 here, under a comment claiming it matched the feed query. It did not: `deliver` sends at
# 60, so everything between 60 and 80 arrived wearing the colour that means *borderline*. A 68 is a
# job this platform decided was worth interrupting somebody for, and it was telling them not to get
# excited about it.
HIGH_FIT = 60.0

WORKPLACE_LABELS = {"remote": "🌍 Remote", "hybrid": "🏠 Hybrid", "onsite": "🏢 On-site"}


@dataclass(frozen=True)
class FeedItem:
    """One scored posting, with everything the card renders.

    Every field after `score` is optional because the corpus fills them opportunistically: a source
    that does not report applicant counts is not a reason to withhold a match. A missing field
    removes its element from the card rather than rendering an empty one.
    """

    job_id: int
    url: str
    title: str
    company: str | None
    score: float
    source: str | None = None
    location: str | None = None
    is_remote: bool | None = None
    description: str | None = None
    posted_at: object | None = None
    applicants: int | None = None
    # LinkedIn's own words. "Be among the first 25" and "Over 200" are different claims that parse
    # to the same kind of integer, and only the caption tells them apart.
    applicants_caption: str | None = None
    easy_apply: bool | None = None
    reposted: bool | None = None
    workplace: str | None = None
    # Set when a pre-send refresh found the posting closed. Not rendered — a closed job is dropped
    # before the card is built — but carried so the filter and the fetch do not need a second query
    # to agree with each other.
    closed: bool = False


def applicant_bucket(count: int) -> int:
    for index, edge in enumerate(APPLICANT_BUCKETS):
        if count < edge:
            return index
    return len(APPLICANT_BUCKETS)


def applicant_label(count: int | None, caption: str | None = None) -> str:
    """`'<25 🟢'`, `'68 🟡'`, `'200+ 🔴'`, or empty.

    **The number on its own says the wrong thing**, which eight live pages made plain: LinkedIn
    writes *"Be among the first 25 applicants"* when **fewer than** 25 have applied, and *"Over 200
    applicants"* when it has stopped counting. Both parse to a bare integer, and a card reading
    `👥25` tells somebody twenty-five people are ahead of them when the page is saying the opposite
    — the difference between a job worth opening now and one worth skipping.

    So the caption decides the qualifier where we have it, and the number is the fallback for a row
    stored before the caption was kept.
    """
    if not isinstance(count, int):
        return ""
    dot = APPLICANT_DOTS[applicant_bucket(count)]
    words = (caption or "").lower()
    if "be among the first" in words:
        # Fewer than `count` have applied — the least contested a posting ever is.
        return f"<{count} {APPLICANT_DOTS[applicant_bucket(0)]}"
    if "over" in words or count >= 200:
        return f"{count}+ {dot}"
    return f"{count} {dot}"


def render(item: FeedItem, *, salary_bands: dict | None = None,
           high_fit: float = HIGH_FIT) -> str:
    """The card, in Telegram HTML.

    `high_fit` is passed by the caller that knows the user's own cutoff, so the dot and the
    decision to send cannot drift apart again. The default is only for a caller that has no user in
    hand — a test, or a preview.
    """
    esc = html.escape
    icon = "🟢" if item.score >= high_fit else "🟡"

    applicants = applicant_label(item.applicants, item.applicants_caption)
    applicants = f" · 👥{esc(applicants)}" if applicants else ""
    # `age_label` can return "<1h", and a raw `<` breaks Telegram's HTML parse — the reference
    # escapes it for exactly this reason.
    age = age_label(_iso(item.posted_at))
    age = f" · {esc(age)}" if age else ""

    easy = " ⚡" if item.easy_apply is True else ""
    repost = " ♻️" if item.reposted is True else ""

    title = esc((item.title or "")[:120])
    company = esc((item.company or "")[:60])
    source = esc(item.source or "")

    workplace = WORKPLACE_LABELS.get(
        item.workplace or classify(item.title or "", item.location or "", item.description,
                                   item.is_remote) or "")
    location = esc((item.location or "").strip()[:70])
    place = " · ".join(part for part in [f"📍 {location}" if location else "", workplace] if part)
    place = f"{place}\n" if place else ""

    return (f"{icon} <b>{int(item.score)}</b> · {title}{easy}{repost}\n"
            f"{company} · {source}{applicants}{age}\n"
            f"{place}{salary_line(item, salary_bands)}\n"
            f"{esc(item.url or '')}")


def salary_line(item: FeedItem, bands: dict | None = None) -> str:
    """`'💰 55k–75k EUR/yr · 🎯 ask 60k–80k EUR/yr'`, with `⚠️ below target` when it applies.

    Extracted from the posting text every time rather than stored: the regex is free, the
    description is already in hand, and a stored figure would be one more thing to backfill when the
    extractor improves.
    """
    job = {"title": item.title or "", "location": item.location or "",
           "description": item.description or "", "source": item.source or ""}
    posted = salarylib.extract(item.description or "")
    ask = salarylib.suggest(job, posted, bands)

    parts = [f"💰 {html.escape(salarylib.fmt(posted))}" if posted else "💰 not listed"]
    if ask:
        parts.append(f"🎯 ask {html.escape(salarylib.fmt(ask))}")
        if ask.get("below_target"):
            parts.append("⚠️ below target")
    return " · ".join(parts)


def _iso(value: object) -> str | None:
    """`posted_at` is a datetime from the database and an ISO string everywhere else."""
    if value is None:
        return None
    return value.isoformat() if hasattr(value, "isoformat") else str(value)
