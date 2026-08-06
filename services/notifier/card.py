"""One job as a Telegram card."""
from __future__ import annotations

import html
from dataclasses import dataclass

from libs.corpus import salary as salarylib
from libs.corpus.filters import age_label
from libs.corpus.workfilter import classify

# The reference's own thresholds for the applicant dot: how contested a posting is, in four steps.
APPLICANT_BUCKETS = (10, 50, 200)
APPLICANT_DOTS = ("🟢", "🟡", "🟠", "🔴")

# The score at or above which a match is a 🟢 rather than a 🟡.
HIGH_FIT = 60.0

WORKPLACE_LABELS = {"remote": "🌍 Remote", "hybrid": "🏠 Hybrid", "onsite": "🏢 On-site"}


@dataclass(frozen=True)
class FeedItem:
    """One scored posting, with everything the card renders."""

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
    # LinkedIn's own words.
    applicants_caption: str | None = None
    easy_apply: bool | None = None
    reposted: bool | None = None
    workplace: str | None = None
    # Set when a pre-send refresh found the posting closed.
    closed: bool = False


def applicant_bucket(count: int) -> int:
    for index, edge in enumerate(APPLICANT_BUCKETS):
        if count < edge:
            return index
    return len(APPLICANT_BUCKETS)


def applicant_label(count: int | None, caption: str | None = None) -> str:
    """`'<25 🟢'`, `'68 🟡'`, `'200+ 🔴'`, or empty."""
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
    """The card, in Telegram HTML."""
    esc = html.escape
    icon = "🟢" if item.score >= high_fit else "🟡"

    applicants = applicant_label(item.applicants, item.applicants_caption)
    applicants = f" · 👥{esc(applicants)}" if applicants else ""
    # `age_label` can return "<1h", and a raw `<` breaks Telegram's HTML parse.
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
    """`'💰 55k–75k EUR/yr · 🎯 ask 60k–80k EUR/yr'`, with `⚠️ below target` when it applies."""
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
