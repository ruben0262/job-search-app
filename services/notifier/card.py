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
from libs.corpus.timefilter import age_label
from libs.corpus.workfilter import classify

# The reference's own thresholds for the applicant dot: how contested a posting is, in four steps.
# 200 is where LinkedIn stops counting and says "Over 200", which is also its strongest stale tell.
APPLICANT_BUCKETS = (10, 50, 200)
APPLICANT_DOTS = ("🟢", "🟡", "🟠", "🔴")

# The score at or above which a match is a 🟢 rather than a 🟡. The same cutoff the feed query uses
# for "high", so the dot and the ranking cannot disagree.
HIGH_FIT = 80.0

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
    easy_apply: bool | None = None
    reposted: bool | None = None
    workplace: str | None = None


def applicant_bucket(count: int) -> int:
    for index, edge in enumerate(APPLICANT_BUCKETS):
        if count < edge:
            return index
    return len(APPLICANT_BUCKETS)


def applicant_label(count: int | None) -> str:
    """`'25 🟡'`, `'200+ 🔴'`, or empty. The reference's `store.applicant_label`, unchanged."""
    if not isinstance(count, int):
        return ""
    return f"{count}{'+' if count >= 200 else ''} {APPLICANT_DOTS[applicant_bucket(count)]}"


def render(item: FeedItem, *, salary_bands: dict | None = None) -> str:
    """The card, in Telegram HTML."""
    esc = html.escape
    icon = "🟢" if item.score >= HIGH_FIT else "🟡"

    applicants = applicant_label(item.applicants)
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
