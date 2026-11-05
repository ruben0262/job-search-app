"""What a job posting's own page says, past what the source's listing API told us."""
from __future__ import annotations

import datetime as dt
import html
import json
import logging
import re
import time
from typing import Any
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import httpx

from libs.corpus.repository import MIN_DESCRIPTION

logger = logging.getLogger(__name__)


class ProxyUnusable(RuntimeError):
    """The proxy itself refused, so no retry and no other job will fare any better."""


def _proxy_refused(exc: Exception, refused: int) -> None:
    """A connection the site proxy would not open, the `refused`th try in a row for this page. A 407 is the
    proxy refusing us at every address alike, its password or its traffic: no try will fare better.
    Anything else is the one address this connection left from (2026-11-05: two of six answered 503 to
    every connection while four worked, and stopping at the first stopped every text for the run), and
    the next try leaves from another. Every try refused is the proxy itself."""
    if str(exc).startswith("407") or refused >= LI_RETRIES:
        raise ProxyUnusable(str(exc)) from exc

try:
    from bs4 import BeautifulSoup
except ImportError:  # pragma: no cover - only the harvester image installs it
    BeautifulSoup = None  # type: ignore[assignment]

# What a page read sends: the harvest's and a pasted link's, one definition for both services.
PAGE_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# LinkedIn rate-limits bulk guest fetches.
LI_RETRIES = 6
LI_BACKOFF = 2.0

# Answers a fresh IP cannot change.
TERMINAL_STATUSES = frozenset({400, 404, 410})

# Trust a source's own snippet at this length rather than spending a fetch.
MIN_SNIPPET = 200

_LINKEDIN_ID = re.compile(r"/jobs/view/(\d+)")
# A No Fluff Jobs posting's slug, which its API answers for.
_NOFLUFF_SLUG = re.compile(r"nofluffjobs\.com/(?:[a-z]{2}/)?job/([^/?#]+)")
_LINKEDIN_HOSTS = {"linkedin.com", "www.linkedin.com"}

_APPLICANTS = re.compile(r"num-applicants__caption[^>]*>\s*([^<]+)<")
_POSTED = re.compile(r"posted-time-ago__text[^>]*>\s*([^<]+)<")
_CLOSED = re.compile(r"no longer accepting applications", re.I)

_JLT_RE = re.compile(r'"jobLocationType"\s*:\s*"([^"]+)"', re.I)
# The workplace pill as LinkedIn renders it: bracketed in the title, or middot-separated below it.
_WP_REMOTE_RE = re.compile(r"\((?:fully\s+)?remote\)|[·|•]\s*remote\b|\bfully\s+remote\b", re.I)
_WP_HYBRID_RE = re.compile(r"\(hybrid\)|[·|•]\s*hybrid\b", re.I)
_WP_ONSITE_RE = re.compile(r"\(on-?site\)|[·|•]\s*on-?site\b", re.I)

# Known ATS job-page shapes.
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

# Where the page breaks a line. Not whitespace, so folding the page's own spaces keeps it.
_BREAK = "\0"
# Elements set apart from what surrounds them. A list item is a line of its own, marked "•".
_BLOCKS = ("p", "div", "section", "article", "header", "footer", "ul", "ol", "table", "tr",
           "blockquote", "pre", "h1", "h2", "h3", "h4", "h5", "h6")


def strip_html(text: str, selector_class: str | None = None) -> str:
    """Visible text, optionally from one element, laid out as the page shows it: paragraphs apart,
    each list item a "•" line. It used to be one run-on line, which is how every card read
    (owner, 2026-11-02). Falls back to a tag strip without bs4."""
    if "<" not in text and "&lt;" in text:
        # Escaped markup, as some boards put it in their JSON-LD.
        text = html.unescape(text)
    if "<" not in text:
        # Plain text already, whose line breaks are its layout.
        return _layout(text.split("\n"))
    if not BeautifulSoup:
        return _layout([re.sub(r"<[^>]+>", " ", text)])
    soup = BeautifulSoup(text, "html.parser")
    node = (soup.find(class_=re.compile(selector_class)) if selector_class else None) or soup
    for br in node.find_all("br"):
        br.replace_with(_BREAK)
    for item in node.find_all("li"):
        item.insert_before(_BREAK + "• ")
    for block in node.find_all(_BLOCKS):
        # Inside a list item it is part of that item's line.
        if block.find_parent("li") is None:
            block.insert_before(_BREAK * 2)
            block.insert_after(_BREAK * 2)
    return _layout(node.get_text().split(_BREAK))


def _layout(lines: list[str]) -> str:
    """Each line's spaces folded, a bullet with nothing after it dropped, one blank line at most."""
    folded = (" ".join(line.split()) for line in lines)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(line for line in folded if line != "•")).strip()


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
    """The JD out of the page's JSON-LD, which most ATS and boards embed."""
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
    """LinkedIn's relative "2 days ago" as an absolute timestamp."""
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
    """Whether LinkedIn is showing "Reposted" rather than "Posted"."""
    caption = _POSTED.search(html_text)
    if not caption or "repost" not in caption.group(1).lower():
        return None
    return True


def parse_workplace(html_text: str) -> str | None:
    """`remote` | `hybrid` | `onsite` | None, in that order of evidence."""
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
    """False once the posting says "No longer accepting applications"."""
    return not bool(_CLOSED.search(html_text))


def parse_easy_apply(html_text: str) -> bool | None:
    """Whether the application stays on LinkedIn."""
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
    """LinkedIn's main apply control as `(is_easy_apply, candidate_url)`."""
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
    """Follow an apply link and keep it only if it lands somewhere external and alive."""
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


# ── a link somebody pasted ────────────────────────────────────────────────────────────────────

# Wherever LinkedIn puts a job's number in an address a person copies: `/jobs/view/<number>`,
# `/jobs/view/<title>-at-<company>-<number>`, or `?currentJobId=<number>` from a search or the feed.
_LINKEDIN_PASTED = re.compile(r"/jobs/view/(?:[^/?#]*-)?(\d+)|[?&]currentJobId=(\d+)")

# A person waits on these, so a LinkedIn page refused is asked again at once, each time from the
# proxy's next address, rather than after the harvest's backoff.
LINK_TRIES = 3


def job_link(url: str) -> str | None:
    """A pasted address the way the corpus keeps it, or None if it is not a web address. A LinkedIn job
    is kept as `/jobs/view/<number>`, as the harvest stores it, however it was copied."""
    url = (url or "").strip()
    if url and "://" not in url:
        url = f"https://{url}"
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in ("http", "https") or "." not in host:
        return None
    pasted = _LINKEDIN_PASTED.search(url)
    if pasted and (host == "linkedin.com" or host.endswith(".linkedin.com")):
        return f"https://www.linkedin.com/jobs/view/{pasted.group(1) or pasted.group(2)}"
    return url


def read_link(url: str, client: httpx.Client) -> dict[str, Any]:
    """What a job's own page says, for somebody who pasted its link (`job_link`): its title, company,
    place and text, whether it has closed, and whether the page said all of that in a form code can
    rely on (`complete`): JSON-LD, LinkedIn's job card, or No Fluff Jobs' API. Anything else is a
    guess from the page's title and main text, for the person to look over before it is kept."""
    found: dict[str, Any] = {"title": "", "company": "", "location": "", "description": "",
                             "workplace": None, "posted_at": None, "closed": False, "complete": False}
    job_id = linkedin_id(url)
    nofluff = _NOFLUFF_SLUG.search(url)
    host = urlparse(url).hostname
    response = None
    for _ in range(LINK_TRIES if job_id else 1):
        try:
            response = client.get(_guest_url(job_id) if job_id
                                  else f"https://nofluffjobs.com/api/posting/{nofluff.group(1)}" if nofluff
                                  else url)
        except httpx.HTTPError as exc:
            logger.warning("link: %s could not be read, the person fills it in: %s", host, exc)
            return found
        if response.status_code != 429:
            break
    if response.status_code in (404, 410):
        found["closed"] = True
        return found
    if response.status_code != 200:
        logger.warning("link: %s answered %d, the person fills it in", host, response.status_code)
        return found

    structured = True
    if nofluff:
        found.update(_nofluff_card(response.json()))
    elif job_id:
        found.update(_linkedin_card(response.text), posted_at=parse_posted_at(response.text),
                     description=strip_html(response.text, "show-more-less-html__markup"))
    else:
        posting = jsonld_posting(response.text)
        structured = posting is not None
        found.update(posting or _page_guess(response.text))
    if not nofluff:
        found.update(closed=not parse_accepting(response.text), workplace=parse_workplace(response.text))
    found["complete"] = bool(structured and found["title"] and found["company"]
                             and len(found["description"]) >= MIN_DESCRIPTION)
    return found


def _linkedin_card(page: str) -> dict[str, str]:
    """The title, company and place off LinkedIn's guest job page, which carries no JSON-LD."""
    if not BeautifulSoup:
        return {}
    soup = BeautifulSoup(page, "html.parser")

    def text(name: str) -> str:
        tag = soup.find(class_=name)
        return tag.get_text(" ", strip=True) if tag else ""

    return {"title": text("topcard__title"), "company": text("topcard__org-name-link"),
            "location": text("topcard__flavor--bullet")}


def _nofluff_card(posting: dict) -> dict[str, Any]:
    """A No Fluff Jobs posting from its API: its page is script and menus (`_fetch_nofluff`)."""
    places = (posting.get("location") or {}).get("places") or []
    place = next((each for each in places if each.get("city")), {})
    country = place.get("country")
    return {"title": posting.get("title") or "", "company": (posting.get("company") or {}).get("name") or "",
            "location": ", ".join(part for part in (
                place.get("city"), country.get("name") if isinstance(country, dict) else country) if part),
            "description": _nofluff_text(posting), "closed": posting.get("status") not in (None, "PUBLISHED")}


def _page_guess(page: str) -> dict[str, str]:
    """A page with no JobPosting in it: its title less the site's name after a "|", the site's own name
    for the company, and the text of its main part rather than its menus."""
    if not BeautifulSoup:
        return {"description": strip_html(page)}
    soup = BeautifulSoup(page, "html.parser")

    def meta(name: str) -> str:
        tag = soup.find("meta", attrs={"property": name})
        return (tag.get("content") or "").strip() if tag else ""

    title = meta("og:title") or (soup.title.get_text() if soup.title else "")
    main = soup.find("main") or soup.find("article")
    return {"title": title.split(" | ")[0].strip(), "company": meta("og:site_name"),
            "description": strip_html(str(main) if main else page)}


# A sentence's end, then the next one's start.
_SENTENCE_END = re.compile(r"(?<=[a-z0-9)][.!?])\s+(?=[A-Z\"“(•])")
# A paragraph ends with the sentence that takes it past this: two or three sentences of a job's
# prose, and a long heading run into its first sentence on its own.
PARAGRAPH_CHARS = 300


def paragraphs(text: str) -> str:
    """A description that is one line, in paragraphs. Every one stored before 2026-11-02 was read that
    way (The Muse's among them), and so is the odd page whose JSON-LD has no markup. A text with any
    line break in it is laid out already, and comes back as it is."""
    if "\n" in text:
        return text
    laid_out = [""]
    for sentence in _SENTENCE_END.split(text.strip()):
        laid_out[-1] = f"{laid_out[-1]} {sentence}".strip()
        if len(laid_out[-1]) >= PARAGRAPH_CHARS:
            laid_out.append("")
    return "\n\n".join(part for part in laid_out if part)


def fetch_job(url: str, *, client: httpx.Client, snippet: str = "", title: str = "",
              stats: dict | None = None) -> Posting:
    """The description and every posting signal the page will give up."""
    def tally(key: str) -> None:
        if stats is not None:
            stats[key] = stats.get(key, 0) + 1

    snippet = (snippet or "").strip()
    job_id = linkedin_id(url)
    out = Posting(description="", applicants=None, reposted=None, workplace=None,
                  posted_at=None, application_url=None, easy_apply=None, accepting=None)

    if (nofluff := _NOFLUFF_SLUG.search(url or "")) is not None:
        return _fetch_nofluff(nofluff.group(1), client=client, snippet=snippet, title=title, out=out,
                              tally=tally)
    if not job_id:
        return _fetch_board(url, client=client, snippet=snippet, title=title, out=out, tally=tally)

    return _fetch_linkedin(job_id, client=client, snippet=snippet, title=title, out=out,
                           tally=tally)


def _fetch_board(url: str, *, client: httpx.Client, snippet: str, title: str,
                 out: Posting, tally) -> Posting:
    """Anything that is not LinkedIn: one request, JSON-LD preferred, page strip as the fallback."""
    if len(snippet) >= MIN_SNIPPET:
        tally("snippet")
        out["description"] = snippet
        return out
    try:
        if url:
            response = client.get(url)
            # Gone, said by the server. 404 and 410 only: a 403 or a 429 is the board refusing us,
            # not the posting ending, and treating those as closed would hide live jobs.
            if response.status_code in (404, 410):
                out["accepting"] = False
                tally(f"generic_http_{response.status_code}")
                tally("closed")
                out["description"] = snippet or title
                return out
            if response.status_code == 200:
                # Free while the page is open: the ATS link, and the JSON-LD workplace type.
                out["accepting"] = parse_accepting(response.text)
                out["application_url"] = extract_ats_url(response.text)
                out["workplace"] = parse_workplace(response.text)
                body = jsonld_description(response.text)
                text = body or strip_html(response.text)
                if len(text) > len(snippet):
                    tally("jsonld_ok" if body else "generic_ok")
                    out["description"] = text
                    return out
            tally(f"generic_http_{response.status_code}")
    except Exception:
        tally("generic_error")
    tally("thin")
    out["description"] = snippet or title
    return out


def _fetch_nofluff(slug: str, *, client: httpx.Client, snippet: str, title: str,
                   out: Posting, tally) -> Posting:
    """No Fluff Jobs builds its pages with a script, so a page holds its menus and nothing of the job
    ("JavaScript is required for the page…", stored as the text until 2026-11-02). Its own API holds
    the posting: what it is, what it asks for, the daily tasks and the pay."""
    try:
        response = client.get(f"https://nofluffjobs.com/api/posting/{slug}")
        if response.status_code in (404, 410):
            out["accepting"] = False
            tally(f"nofluff_http_{response.status_code}")
        elif response.status_code == 200:
            text = _nofluff_text(json.loads(response.text))
            if len(text) > len(snippet):
                tally("nofluff_ok")
                out["description"] = text
                return out
            tally("nofluff_empty")
        else:
            tally(f"nofluff_http_{response.status_code}")
    except Exception:
        tally("nofluff_error")
    out["description"] = snippet or title
    return out


def _nofluff_text(posting: dict) -> str:
    """A No Fluff Jobs posting as one text: the pay first, so the salary is read off it, then the job,
    what it asks for and what the days hold."""
    def section(heading: str, items: list) -> str:
        lines = [f"• {item}" for item in items if item]
        return f"{heading}\n\n" + "\n".join(lines) if lines else ""

    salary = (posting.get("essentials") or {}).get("originalSalary") or {}
    pay = [f"Salary: {pay['range'][0]:g} - {pay['range'][-1]:g} {salary.get('currency', '')} per "
           f"{str(pay.get('period') or 'month').lower()} ({kind.upper() if len(kind) <= 3 else kind})"
           for kind, pay in (salary.get("types") or {}).items() if pay.get("range")]
    needs = posting.get("requirements") or {}
    return "\n\n".join(part for part in (
        "\n".join(pay),
        strip_html(((posting.get("details") or {}).get("description")) or ""),
        section("Requirements:", [each.get("value") for each in needs.get("musts") or []]),
        strip_html(needs.get("description") or ""),
        section("Nice to have:", [each.get("value") for each in needs.get("nices") or []]),
        section("Daily tasks:", (posting.get("specs") or {}).get("dailyTasks") or []),
    ) if part)


def _fetch_linkedin(job_id: str, *, client: httpx.Client, snippet: str, title: str,
                    out: Posting, tally) -> Posting:
    """The guest endpoint, retried — each try on a new connection, so from the site proxy's next
    address (`services.harvester.fetch._mounts`)."""
    refused = 0
    for _ in range(LI_RETRIES):
        try:
            response = client.get(_guest_url(job_id))
        except httpx.ProxyError as exc:
            tally("li_proxy_error")
            refused += 1
            _proxy_refused(exc, refused)
            continue
        except Exception:
            tally("li_error")
            time.sleep(LI_BACKOFF)
            continue
        if response.status_code == 200:
            # The guest page's values beat the scrape's on every field it carries.
            out["accepting"] = parse_accepting(response.text)
            out["applicants"] = parse_applicants(response.text) or out["applicants"]
            # The caption, because "first 25" is fewer than 25 and "over 200" is a ceiling.
            out["applicants_caption"] = applicants_caption(response.text)
            out["reposted"] = parse_reposted(response.text)
            out["workplace"] = parse_workplace(response.text)
            out["posted_at"] = parse_posted_at(response.text)
            out["application_url"] = parse_application_url(response.text, client)
            text = strip_html(response.text, "show-more-less-html__markup")
            if len(text) >= 100:
                tally("li_ok")
                out["description"] = text
                return out
            # A 200 with no description body is an answer, not a failure.
            tally("li_empty")
            break
        if response.status_code in TERMINAL_STATUSES:
            # The posting is gone. A fresh IP cannot bring it back, and this was six requests.
            # 400 is in that set because the guest endpoint answers it for a retired id, so it says
            # the same thing 404 does — the row will never describe, embed or score again.
            out["accepting"] = False
            tally(f"li_http_{response.status_code}")
            break
        tally(f"li_http_{response.status_code}")
        time.sleep(LI_BACKOFF)
    else:
        # Every try throttled or failed in transit: no answer about the posting at all.
        out["throttled"] = True

    tally("li_giveup")
    out["description"] = snippet or title
    return out


def fetch_meta(url: str, *, client: httpx.Client, stats: dict | None = None) -> Posting:
    """Live metadata only — no description."""
    def tally(key: str) -> None:
        if stats is not None:
            stats[key] = stats.get(key, 0) + 1

    out = Posting(applicants=None, applicants_caption=None, posted_at=None, easy_apply=None,
                  accepting=None, reposted=None, application_url=None, workplace=None)
    job_id = linkedin_id(url)
    if not job_id:
        tally("non_li")
        return out

    refused = 0
    for _ in range(LI_RETRIES):
        try:
            response = client.get(_guest_url(job_id))
        except httpx.ProxyError as exc:
            tally("li_proxy_error")
            refused += 1
            _proxy_refused(exc, refused)
            continue
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
            # Easy Apply is on no page a logged-out fetch can reach, so it stays unknown rather than guessed.
            out["application_url"] = parse_application_url(response.text, client)
            tally("li_ok")
            return out
        tally(f"li_http_{response.status_code}" if response.status_code != 200 else "li_empty")
        time.sleep(LI_BACKOFF)

    tally("li_giveup")
    return out
