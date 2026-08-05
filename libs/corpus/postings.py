"""What a job posting's own page says, past what the source's listing API told us.

**Port of `scorer/fetch_desc.py`.** The reference fetches this before every scoring run; the
platform, until now, did not fetch it at all — it stored whatever `snippet` the source adapter
happened to return and scored on that. Measured on the corpus, a snippet is a few hundred
characters of boilerplate where the posting itself runs to several thousand, so the label was being
formed from the job's advertisement rather than the job.

Six things come off the page, and each of them is a decision the platform was previously making
blind:

- **description** — what the labeller and the embedder actually read.
- **applicants** — LinkedIn's own count. A glut means the listing is stale.
- **reposted** — an auto-resurfaced stale listing, which the card marks and ranking demotes.
- **workplace** — the authoritative `jobLocationType` / pill, so `workfilter` can drop an explicit
  on-site posting instead of passing it through as unknown.
- **posted date** — the real "2 days ago", against a scrape that only ever knows the day.
- **apply URL** — the external ATS target, which is both the link the user needs and the strongest
  cross-source dedup key there is.

## Why this is in `libs` rather than in the harvester

The corpus is shared, so a description belongs to the job and not to the user who happened to
trigger the fetch. Both the harvester (filling in new rows) and a per-user path (`/cv <link>` on an
arbitrary URL) need this, and `services` may not import `services` — so it lives here, and takes
the caller's client rather than building one.

**That client is the tenancy seam.** The reference reads `PROXY_URL` from the environment because
it serves one person. Here the caller passes the `TenantClient` holding that user's proxy lease, so
bandwidth is metered to whoever caused it, and no code path can quietly fall back to the shared IP
that LinkedIn blocks.

## Conservative on purpose

Every parser returns `None` rather than a guess. `workplace` in particular: only an explicit tag
yields `onsite`, because the filter downstream *drops* on that value and a passing mention of
"occasional onsite visits" in a remote posting's body would otherwise bin a good job.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import re
import time
from typing import Any
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import httpx

logger = logging.getLogger(__name__)


class ProxyUnusable(RuntimeError):
    """The proxy itself refused, so no retry and no other job will fare any better.

    Distinct from a per-job failure on purpose. "This posting would not load" is worth six retries
    on a rotating IP; "your proxy account is out of traffic" is worth none, and retrying it turns
    one dead credential into six requests per row for as long as the pass runs. The reference never
    had to distinguish them because it had exactly one proxy and a human watching it.
    """

try:
    from bs4 import BeautifulSoup
except ImportError:  # pragma: no cover - only the harvester image installs it
    BeautifulSoup = None  # type: ignore[assignment]

# LinkedIn rate-limits bulk guest fetches. Each retry goes out on a fresh residential IP because
# the proxy rotates per connection, so a retry is a genuinely different attempt rather than a wait.
LI_RETRIES = 6
LI_BACKOFF = 2.0

# Answers a fresh IP cannot change. **A retry is only worth its bandwidth when the next attempt
# could plausibly differ**, and a deleted posting will not come back: 404 and 410 were being asked
# six times each, on the user's own paid residential proxy, for the same answer (ROADMAP 6am).
#
# 429 and 5xx are deliberately absent — those are exactly the transient cases the rotation exists
# for, and they keep every one of their attempts.
TERMINAL_STATUSES = frozenset({400, 404, 410})

# Trust a source's own snippet at this length rather than spending a fetch. Tier-2 APIs return the
# full text here; LinkedIn returns a teaser, which is why it is never trusted below.
MIN_SNIPPET = 200

# Cap on the stored description: enough to score and embed, and a bound on the token bill for a
# posting that inlines an entire employee handbook.
MAX_DESC = 4_000

_LINKEDIN_ID = re.compile(r"/jobs/view/(\d+)")
_LINKEDIN_HOSTS = {"linkedin.com", "www.linkedin.com"}

_APPLICANTS = re.compile(r"num-applicants__caption[^>]*>\s*([^<]+)<")
_POSTED = re.compile(r"posted-time-ago__text[^>]*>\s*([^<]+)<")
_CLOSED = re.compile(r"no longer accepting applications", re.I)

_JLT_RE = re.compile(r'"jobLocationType"\s*:\s*"([^"]+)"', re.I)
# The workplace pill as LinkedIn renders it: bracketed in the title, or middot-separated in the
# top-card subtitle ("London, UK · Hybrid"). Deliberately narrow — these shapes cannot be produced
# by prose, which is what stops a JD mentioning "onsite" from being classified as on-site.
_WP_REMOTE_RE = re.compile(r"\((?:fully\s+)?remote\)|[·|•]\s*remote\b|\bfully\s+remote\b", re.I)
_WP_HYBRID_RE = re.compile(r"\(hybrid\)|[·|•]\s*hybrid\b", re.I)
_WP_ONSITE_RE = re.compile(r"\(on-?site\)|[·|•]\s*on-?site\b", re.I)

# Known ATS job-page shapes. A board's Apply button almost always points at the company's own ATS,
# which makes this the cross-source dedup key: two boards listing one job resolve to one URL.
_ATS_APPLY_RE = re.compile(
    r"https?://(?:job-boards|boards)\.greenhouse\.io/[A-Za-z0-9_-]+/jobs/\d+"
    r"|https?://jobs\.(?:eu\.)?lever\.co/[A-Za-z0-9_-]+/[A-Fa-f0-9-]{16,}"
    r"|https?://jobs\.ashbyhq\.com/[A-Za-z0-9_.-]+/[A-Fa-f0-9-]{16,}"
    r"|https?://[A-Za-z0-9_-]+\.wd\d+\.myworkdayjobs\.com/[^\s\"'<>]*?/job/[^\s\"'<>]+"
    r"|https?://jobs\.smartrecruiters\.com/[A-Za-z0-9_-]+/\d{9,}"
    r"|https?://[A-Za-z0-9-]+\.recruitee\.com/o/[A-Za-z0-9-]+"
    r"|https?://[A-Za-z0-9-]+\.breezy\.hr/p/[A-Za-z0-9-]+"
    r"|https?://[A-Za-z0-9-]+\.jobs\.personio\.(?:de|com)/job/\d+"
    r"|https?://apply\.workable\.com/[A-Za-z0-9-]+/j/[A-Za-z0-9]+")



# ── page → text ───────────────────────────────────────────────────────────────────────────────

def strip_html(text: str, selector_class: str | None = None) -> str:
    """Visible text, optionally from one element. Falls back to a tag strip without bs4."""
    if not BeautifulSoup:
        return re.sub(r"<[^>]+>", " ", text)
    soup = BeautifulSoup(text, "html.parser")
    node = soup.find(class_=re.compile(selector_class)) if selector_class else None
    return (node or soup).get_text(" ", strip=True)


def _jsonld_objects(html_text: str) -> list[dict]:
    """Every schema.org JobPosting object embedded in a page."""
    if not BeautifulSoup:
        return []
    found = []
    soup = BeautifulSoup(html_text, "html.parser")
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "")
        except Exception:
            continue
        for obj in (data if isinstance(data, list) else [data]):
            if isinstance(obj, dict) and "JobPosting" in str(obj.get("@type", "")):
                found.append(obj)
    return found


def jsonld_description(html_text: str) -> str | None:
    """The JD out of the page's JSON-LD, which most ATS and boards embed.

    Preferred over stripping the whole page because it is the description and nothing else — no
    nav, no cookie banner, no "similar jobs" list competing for the model's attention.
    """
    for obj in _jsonld_objects(html_text):
        clean = strip_html(obj.get("description") or "")
        if len(clean) >= 100:
            return clean
    return None


def jsonld_posting(html_text: str) -> dict | None:
    """Title, company, location and description from a page's JSON-LD, or None if absent."""
    for obj in _jsonld_objects(html_text):
        org = obj.get("hiringOrganization") or {}
        company = org.get("name") if isinstance(org, dict) else (org if isinstance(org, str) else "")
        location = obj.get("jobLocation")
        if isinstance(location, list):
            location = location[0] if location else {}
        place = ""
        if isinstance(location, dict):
            address = location.get("address") or {}
            if isinstance(address, dict):
                place = (address.get("addressLocality") or address.get("addressRegion")
                         or address.get("addressCountry") or "")
                if isinstance(place, dict):
                    place = place.get("name", "")
        return {"title": (obj.get("title") or "").strip(),
                "company": (company or "").strip(),
                "location": (place or "").strip(),
                "description": strip_html(obj.get("description") or "")}
    return None


# ── the LinkedIn signals ──────────────────────────────────────────────────────────────────────

def parse_applicants(html_text: str) -> int | None:
    """The number out of LinkedIn's caption: "68 applicants", "Over 200 applicants"."""
    caption = _APPLICANTS.search(html_text)
    if not caption:
        return None
    number = re.search(r"(\d+)", caption.group(1))
    return int(number.group(1)) if number else None


def applicants_caption(html_text: str) -> str | None:
    """The caption verbatim, for a card that would rather say "Over 200" than "200"."""
    caption = _APPLICANTS.search(html_text)
    return caption.group(1).strip() if caption else None


_UNIT_DELTA = {"minute": "minutes", "hour": "hours", "day": "days", "week": "weeks",
               "month": "days"}


def parse_posted_at(html_text: str, *, now: dt.datetime | None = None) -> dt.datetime | None:
    """LinkedIn's relative "2 days ago" as an absolute timestamp.

    Worth the parse because the scrape only ever knows the date, and recency ordering on a feed
    checked every fifteen minutes cares about the hours.
    """
    caption = _POSTED.search(html_text)
    if not caption:
        return None
    match = re.search(r"(\d+)\s*(minute|hour|day|week|month)", caption.group(1).strip().lower())
    if not match:
        return None
    count, unit = int(match.group(1)), match.group(2)
    delta = {_UNIT_DELTA[unit]: count * 30 if unit == "month" else count}
    return (now or dt.datetime.now(dt.UTC)) - dt.timedelta(**delta)


def parse_reposted(html_text: str) -> bool | None:
    """Whether LinkedIn is showing "Reposted" rather than "Posted".

    **`None` unless the page actually says so, which no page we can reach does.** A repost is a
    stale listing resurfaced, and it is worth ranking down whatever its timestamp claims — but the
    marker appears only to a logged-in session. Confirmed twice: the word "repost" is absent from
    the guest fragment and from the 250 KB full page on every sample, while the same posting shows
    "Reposted 5 hours ago" to a signed-in browser.

    So a caption without the word is **no evidence either way**, and returning False from it told
    2,584 rows they were not reposts on the strength of a page that never mentions reposts. Unknown
    is the honest answer, and it is what the card already renders as nothing.
    """
    caption = _POSTED.search(html_text)
    if not caption or "repost" not in caption.group(1).lower():
        return None
    return True


def posted_caption(html_text: str) -> str | None:
    """The raw "Posted 3 days ago" / "Reposted 2 weeks ago" text, or `None` where there is none.

    **Kept so that "no repost has ever been detected" can be checked rather than assumed.** Across
    2,584 LinkedIn rows the caption was found every time and `parse_reposted` said False every time,
    which is either a true answer or a caption that never carries the word — and nothing stored
    could tell those apart.
    """
    caption = _POSTED.search(html_text)
    return caption.group(1).strip() if caption else None


def parse_workplace(html_text: str) -> str | None:
    """`remote` | `hybrid` | `onsite` | None, in that order of evidence.

    schema.org `jobLocationType` first: TELECOMMUTE is the W3C/Google standard and the most
    reliable signal on the page. Then LinkedIn's pill, scanned only in the first 6KB where the
    top-card lives — restricting the window is what keeps JD prose out of the classification.

    On the lightweight guest fragment this legitimately returns None: the pill is on the full
    `/jobs/view` page. None is the safe answer, because the filter downstream fails open.
    """
    if not html_text:
        return None
    kind = _JLT_RE.search(html_text)
    if kind and "telecommute" in kind.group(1).lower():
        return "remote"
    head = html_text[:6000]
    if _WP_ONSITE_RE.search(head):
        return "onsite"
    if _WP_HYBRID_RE.search(head):
        return "hybrid"
    if _WP_REMOTE_RE.search(head):
        return "remote"
    return None


def parse_accepting(html_text: str) -> bool:
    """False once the posting says "No longer accepting applications".

    What lets the refresher retire a dead listing rather than keep it in a feed as if it were live.
    """
    return not bool(_CLOSED.search(html_text))


def parse_easy_apply(html_text: str) -> bool | None:
    """Whether the application stays on LinkedIn.

    Keyed off *where* the application happens rather than the button's words, because the
    logged-out page rarely shows the literal "Easy Apply" label — that is a signed-in string, and
    keying off it made almost everything read as external:

        apply-link-onsite   → stays on LinkedIn  → easy apply
        apply-link-offsite  → company website    → not easy
    """
    low = html_text.lower()
    if "apply-link-offsite" in low or "apply on company website" in low:
        return False
    if "apply-link-onsite" in low or "easy apply" in low or "easyapply" in low:
        return True
    return None


# ── the apply target ──────────────────────────────────────────────────────────────────────────

def extract_ats_url(html_text: str) -> str | None:
    """The first known-ATS job URL in a page. Hrefs are HTML-escaped, hence the unescape."""
    match = _ATS_APPLY_RE.search(html_text or "")
    return match.group(0).replace("&amp;", "&") if match else None


def normalise_apply_url(href: str) -> str | None:
    """A LinkedIn apply redirect resolved to the external URL behind it, or None."""
    href = (href or "").strip()
    if not href:
        return None
    full = urljoin("https://www.linkedin.com", href)
    parsed = urlparse(full)
    if parsed.scheme not in ("http", "https"):
        return None
    if parsed.netloc.lower() not in _LINKEDIN_HOSTS:
        return full
    query = parse_qs(parsed.query)
    for key in ("url", "redirectUrl", "redirect_url"):
        values = query.get(key)
        if not values:
            continue
        target = unquote(values[0]).strip()
        parsed_target = urlparse(target)
        if (parsed_target.scheme in ("http", "https")
                and parsed_target.netloc.lower() not in _LINKEDIN_HOSTS):
            return target
    return None


def parse_apply_signal(html_text: str) -> tuple[bool | None, str | None]:
    """LinkedIn's main apply control as `(is_easy_apply, candidate_url)`.

    Ranked rather than first-match: a page carries several links containing the word "apply", and
    the one that matters is the top-card button. An explicit "Easy Apply" outranks "apply on
    company website", which outranks a bare "apply" anywhere on the page.
    """
    if BeautifulSoup:
        candidates: list[tuple[int, str, str]] = []
        soup = BeautifulSoup(html_text, "html.parser")
        for anchor in soup.find_all("a", href=True):
            href = (anchor.get("href") or "").strip()
            classes = " ".join(anchor.get("class") or [])
            combined = " ".join(
                (" ".join(anchor.stripped_strings), anchor.get("aria-label") or "", classes)
            ).strip().lower()
            if not combined and "jobs-apply" not in href:
                continue
            if "easy apply" in combined or "easyapply" in combined:
                candidates.append((3, combined, href))
            elif ("apply on company website" in combined or "company website" in combined
                  or "jobs-apply" in href or "topcard__button" in classes.lower()):
                candidates.append((2, combined, href))
            elif re.search(r"\bapply\b", combined):
                candidates.append((1, combined, href))
        if candidates:
            candidates.sort(key=lambda item: item[0], reverse=True)
            _, combined, href = candidates[0]
            if "easy apply" in combined or "easyapply" in combined:
                return True, None
            return False, normalise_apply_url(href)

    low = html_text.lower()
    if "apply on company website" in low or "apply-link-offsite" in low:
        return False, None
    if "easy apply" in low or "easyapply" in low:
        return True, None
    return None, None


def resolve_apply_url(url: str, client: httpx.Client) -> str | None:
    """Follow an apply link and keep it only if it lands somewhere external and alive.

    A link that 404s or bounces back to LinkedIn is worse than no link: it is presented to the user
    as the way to apply, and they find out it is not by clicking it.
    """
    parsed = urlparse((url or "").strip())
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return None
    if parsed.netloc.lower() in _LINKEDIN_HOSTS:
        return None
    try:
        response = client.get(url)
    except Exception:
        return None
    if response.status_code >= 400:
        return None
    final = str(response.url)
    if (urlparse(final).netloc or "").lower() in _LINKEDIN_HOSTS:
        return None
    return final


def parse_application_url(html_text: str, client: httpx.Client) -> str | None:
    """The external apply URL, when the posting has one and it resolves."""
    easy_apply, candidate = parse_apply_signal(html_text)
    if easy_apply is not False or not candidate:
        return None
    return resolve_apply_url(candidate, client)


# ── the fetches ───────────────────────────────────────────────────────────────────────────────

def linkedin_id(url: str) -> str | None:
    """The numeric job id in a LinkedIn URL, or None for anything else."""
    match = _LINKEDIN_ID.search(url or "")
    return match.group(1) if match else None


def _guest_url(job_id: str) -> str:
    return f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}"


class Posting(dict):
    """What came off the page. A dict so a caller can pass it straight into the repository."""

    @property
    def description(self) -> str:
        return self.get("description") or ""


# **`fetch_easy_apply` was removed on 2026-08-05, measured rather than reasoned about.** It streamed
# up to 96 KB of `/jobs/view` per posting on a user's paid residential proxy to read one boolean.
# Probed against four live postings, guest fragment and full page side by side: the full page is
# 250 KB and carries **none** of `apply-link-onsite`, "easy apply", "repost" or "promoted by" —
# every marker appears only to a logged-in session. Six times the bytes for the same nothing.
#
# `parse_easy_apply` stays: it is pure, tested, and correct the day a logged-in page exists.


def fetch_apply_url(url: str, client: httpx.Client) -> str | None:
    """The external ATS link on a job-board page, for cross-source dedup. Fail-soft."""
    try:
        response = client.get(url)
    except Exception:
        return None
    return extract_ats_url(response.text) if response.status_code == 200 else None


def fetch_posting(url: str, client: httpx.Client) -> dict[str, Any]:
    """Everything about an arbitrary job URL, for `/cv <link>` — no corpus row required.

    Prefers JSON-LD, falls back to a page strip for the description and og:title for the title, and
    returns empty strings for whatever the page does not say. It never raises: the user pasted a
    link and deserves an answer about it, not a stack trace.
    """
    job_id = linkedin_id(url)
    try:
        response = client.get(_guest_url(job_id) if job_id else url)
        html_text = response.text if response.status_code == 200 else ""
    except Exception:
        html_text = ""

    posting = (jsonld_posting(html_text) if html_text else None) or {
        "title": "", "company": "", "location": "", "description": ""}
    if len(posting.get("description") or "") < 100 and html_text:
        posting["description"] = strip_html(
            html_text, "show-more-less-html__markup" if job_id else None)
    if not posting.get("title") and html_text and BeautifulSoup:
        soup = BeautifulSoup(html_text, "html.parser")
        meta = soup.find("meta", attrs={"property": "og:title"})
        title = (meta.get("content") if meta else None) or (soup.title.string if soup.title else "")
        posting["title"] = (title or "").strip()

    host = (urlparse(url).netloc or "").lower().replace("www.", "")
    return {"url": url, "source": host or "manual", **posting}


def fetch_job(url: str, *, client: httpx.Client, snippet: str = "", title: str = "",
              stats: dict | None = None) -> Posting:
    """The description and every posting signal the page will give up.

    Returns a `Posting` with `description` always set — falling back to the snippet and then the
    title, because a job with a thin description still has to be scored, and an exception here
    would drop it from the corpus entirely.

    `stats` tallies outcomes by reason (`li_ok`, `li_http_429`, `li_empty`, `generic_ok`, `thin`).
    Without it a bulk run that silently falls back to snippets looks identical to one that worked,
    which is the failure this is designed to make visible.
    """
    def tally(key: str) -> None:
        if stats is not None:
            stats[key] = stats.get(key, 0) + 1

    snippet = (snippet or "").strip()
    job_id = linkedin_id(url)
    out = Posting(description="", applicants=None, reposted=None, workplace=None,
                  posted_at=None, application_url=None, easy_apply=None)

    if not job_id:
        return _fetch_board(url, client=client, snippet=snippet, title=title, out=out, tally=tally)

    return _fetch_linkedin(job_id, client=client, snippet=snippet, title=title, out=out,
                           tally=tally)


def _fetch_board(url: str, *, client: httpx.Client, snippet: str, title: str,
                 out: Posting, tally) -> Posting:
    """Anything that is not LinkedIn: one request, JSON-LD preferred, page strip as the fallback."""
    if len(snippet) >= MIN_SNIPPET:
        tally("snippet")
        out["description"] = snippet[:MAX_DESC]
        return out
    try:
        if url:
            response = client.get(url)
            if response.status_code == 200:
                # Free while the page is open: the board's external ATS link, and the
                # authoritative workplace type most ATS boards emit as JSON-LD. Getting the
                # workplace here is what lets an explicit on-site posting be dropped rather
                # than passed through as unknown.
                out["application_url"] = extract_ats_url(response.text)
                out["workplace"] = parse_workplace(response.text)
                body = jsonld_description(response.text)
                text = body or strip_html(response.text)
                if len(text) > len(snippet):
                    tally("jsonld_ok" if body else "generic_ok")
                    out["description"] = text[:MAX_DESC]
                    return out
            tally(f"generic_http_{response.status_code}")
    except Exception:
        tally("generic_error")
    tally("thin")
    out["description"] = (snippet or title)[:MAX_DESC]
    return out


def _fetch_linkedin(job_id: str, *, client: httpx.Client, snippet: str, title: str,
                    out: Posting, tally) -> Posting:
    """The guest endpoint, retried — each attempt goes out on a fresh residential IP."""
    for _ in range(LI_RETRIES):
        try:
            response = client.get(_guest_url(job_id))
        except httpx.ProxyError as exc:
            tally("li_proxy_error")
            raise ProxyUnusable(str(exc)) from exc
        except Exception:
            tally("li_error")
            time.sleep(LI_BACKOFF)
            continue
        if response.status_code == 200:
            # The guest page's values beat the scrape's on every field it carries: a real posted
            # timestamp rather than a date, a genuine workplace tag, the applicant count.
            out["applicants"] = parse_applicants(response.text) or out["applicants"]
            # The caption, because the number in it means different things: "be among the first 25"
            # is fewer than 25, "over 200" is a ceiling.
            out["applicants_caption"] = applicants_caption(response.text)
            out["reposted"] = parse_reposted(response.text)
            out["workplace"] = parse_workplace(response.text)
            out["posted_at"] = parse_posted_at(response.text)
            out["application_url"] = parse_application_url(response.text, client)
            text = strip_html(response.text, "show-more-less-html__markup")
            if len(text) >= 100:
                tally("li_ok")
                out["description"] = text[:MAX_DESC]
                return out
            # **A 200 with no description body is an answer, not a failure.**
            #
            # This used to retry, on the reasoning that a blank body means throttled or expired. The
            # live log disproves it: the same posting returns 200 with the same short body six times
            # in a row, each attempt on a fresh residential IP. It is simply a fragment LinkedIn
            # serves without `show-more-less-html__markup`. Retrying spent six times the user's paid
            # bandwidth to receive the same page again, and everything else on it — the applicant
            # count, the workplace tag, the posted time — was already parsed above and kept
            # (ROADMAP 6am).
            tally("li_empty")
            break
        if response.status_code in TERMINAL_STATUSES:
            # The posting is gone. A fresh IP cannot bring it back, and this was six requests.
            tally(f"li_http_{response.status_code}")
            break
        tally(f"li_http_{response.status_code}")
        time.sleep(LI_BACKOFF)

    tally("li_giveup")
    out["description"] = (snippet or title)[:MAX_DESC]
    return out


def fetch_meta(url: str, *, client: httpx.Client, stats: dict | None = None) -> Posting:
    """Live metadata only — no description. What the freshness refresher runs.

    Cheap enough to run over a whole feed: it answers "is this still worth showing" (accepting,
    applicants, reposted) without re-paying for text already stored. Non-LinkedIn URLs return all
    Nones, because nothing else exposes anything reliable to parse.

    **What it cannot answer, it leaves unknown.** Easy Apply, repost, and an accurate applicant
    count are visible only to a logged-in session — measured on four postings against both the guest
    fragment and the 250 KB full page, and confirmed against the owner's own browser, where the same
    job reads "Reposted 5 hours ago · 98 applicants" and the logged-out page says "5 hours ago · 86
    applicants". The count here is a **floor**, not a total.
    """
    def tally(key: str) -> None:
        if stats is not None:
            stats[key] = stats.get(key, 0) + 1

    out = Posting(applicants=None, applicants_caption=None, posted_at=None, easy_apply=None,
                  accepting=None, reposted=None, application_url=None, workplace=None)
    job_id = linkedin_id(url)
    if not job_id:
        tally("non_li")
        return out

    for _ in range(LI_RETRIES):
        try:
            response = client.get(_guest_url(job_id))
        except httpx.ProxyError as exc:
            tally("li_proxy_error")
            raise ProxyUnusable(str(exc)) from exc
        except Exception:
            tally("li_error")
            time.sleep(LI_BACKOFF)
            continue
        if response.status_code == 200 and len(response.text) > 200:
            out["applicants"] = parse_applicants(response.text)
            out["applicants_caption"] = applicants_caption(response.text)
            out["posted_at"] = parse_posted_at(response.text)
            out["accepting"] = parse_accepting(response.text)
            out["reposted"] = parse_reposted(response.text)
            out["workplace"] = parse_workplace(response.text)
            # Easy Apply is not on any page a logged-out fetch can reach, so it is left unknown
            # rather than guessed at. The apply URL still resolves from the fragment.
            out["application_url"] = parse_application_url(response.text, client)
            tally("li_ok")
            return out
        tally(f"li_http_{response.status_code}" if response.status_code != 200 else "li_empty")
        time.sleep(LI_BACKOFF)

    tally("li_giveup")
    return out
