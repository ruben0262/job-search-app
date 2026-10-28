"""One job as a Telegram card, and as a browser notification."""
from __future__ import annotations

import html

from libs.corpus import salary as salarylib
from libs.corpus.filters import age_label
from libs.corpus.workfilter import classify
from libs.db.models import CorpusJob
from libs.notify import site_url

# The reference's own thresholds for the applicant dot: how contested a posting is, in four steps.
APPLICANT_BUCKETS = (10, 50, 200)
APPLICANT_DOTS = ("🟢", "🟡", "🟠", "🔴")

WORKPLACE_LABELS = {"remote": "🌍 Remote", "hybrid": "🏠 Hybrid", "onsite": "🏢 On-site"}


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


def job_page(job: CorpusJob) -> str:
    """The job on our site, where the CV for it is made."""
    return site_url(f"/#/feed?job={job.id}")


def render(job: CorpusJob, words: tuple[str, ...]) -> str:
    """The card, in Telegram HTML: the job, where it is, what it pays, and which of your words it has."""
    esc = html.escape

    applicants = applicant_label(job.applicants, job.applicants_caption)
    applicants = f" · 👥{esc(applicants)}" if applicants else ""
    # `age_label` can return "<1h", and a raw `<` breaks Telegram's HTML parse.
    age = age_label(_iso(job.posted_at))
    age = f" · {esc(age)}" if age else ""

    easy = " ⚡" if job.easy_apply is True else ""
    repost = " ♻️" if job.reposted is True else ""

    workplace = WORKPLACE_LABELS.get(
        job.workplace or classify(job.title or "", job.location or "", job.description,
                                  job.is_remote) or "")
    location = esc((job.location or "").strip()[:70])
    place = " · ".join(part for part in [f"📍 {location}" if location else "", workplace] if part)
    place = f"{place}\n" if place else ""

    return (f"<b>{esc((job.title or '')[:120])}</b>{easy}{repost}\n"
            f"{esc((job.company or '')[:60])} · {esc(job.source or '')}{applicants}{age}\n"
            f"{place}{salary_line(job)}\n"
            f"Matches: {esc(', '.join(words))}")


def keyboard(job: CorpusJob) -> dict:
    """Two links and no callbacks: the posting itself, and the job on our site to make a CV for it."""
    buttons = [{"text": "Make my CV", "url": job_page(job)}]
    if job.url:
        buttons.insert(0, {"text": "Open job", "url": job.url})
    return {"inline_keyboard": [buttons]}


def notification(job: CorpusJob) -> dict:
    """The job as a browser notification: what the service worker shows, and where a tap goes."""
    return {"title": job.title or "A new job for you",
            "body": " · ".join(part for part in (job.company, job.location) if part),
            "url": job_page(job)}


def salary_line(job: CorpusJob) -> str:
    """`'💰 55k–75k EUR/yr · 🎯 ask 60k–80k EUR/yr'`, with `⚠️ below target` when it applies."""
    fields = {"title": job.title or "", "location": job.location or "",
              "description": job.description or "", "source": job.source or ""}
    posted = salarylib.extract(job.description or "")
    ask = salarylib.suggest(fields, posted, None)

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
