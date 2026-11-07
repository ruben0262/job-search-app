"""The free CV and cover letter, built by code from the person's own documents. No AI.

Every sentence about the person is a line of their own documents, picked and ordered by how much it
shares with the posting. The only words of ours are the frame: section headings, a greeting, a close,
and on a letter the date, the company and the subject, each from the job as it is made. The look is
the template they picked (`libs.templates`), whose `<style>` block is read at runtime; Meridian's is
the scaffold's, a prompt file never written to (CLAUDE.md §3a).
"""
from __future__ import annotations

import re
from dataclasses import replace
from datetime import UTC, datetime
from html import escape

from libs import templates
from libs.documents.read import Entry, Facts, fold, read, texts, words
from libs.templates import Template

# How much the CV keeps: the most relevant jobs, the most relevant bullets of each.
ENTRIES_SHOWN, BULLETS_SHOWN = 5, 4
# How many of their achievements the cover letter quotes.
ACHIEVEMENTS = 3


def render(kind: str, *, documents: dict[str, list[str]], identity: dict, job,
           template: Template | None = None) -> str:
    """A complete HTML document for one job: `kind` is "cv" or "cover", laid out by `template`, the
    default look when none is given.

    `documents` maps a document kind to its texts, every CV switched on among them; `identity`
    carries `full_name` and `links`, and `job` has `title`, `company`, `description`, `location` and `url`.
    """
    template = template or templates.find(kind, templates.DEFAULT)
    if template is None or template.kind != kind:
        raise ValueError(f"unknown document kind {kind!r}, or a look of another kind: expected 'cv' or "
                         "'cover', and a template of that kind")
    facts = read(documents)
    asked = words(f"{getattr(job, 'title', '') or ''} {getattr(job, 'description', '') or ''}")
    if kind == "cv":
        return _cv(facts, identity, job, asked, template,
                   mine=words("\n".join(texts(documents, *documents))))
    return _cover(facts, identity, job, asked, template)


def _cv(facts: Facts, identity: dict, job, asked: set[str], template: Template, *,
        mine: set[str]) -> str:
    name, header = _header(facts, identity)
    title = _bare(getattr(job, "title", "") or "")
    # Their title mirrors the posting's only when every word of it is already in their documents.
    headline = title if title and words(title) <= mine else (facts.titles[0] if facts.titles else "")
    # One CV's summary, the one closest to the posting: two CVs' summaries run together say it twice.
    summary = max(facts.profiles, key=lambda text: _shared(text, asked), default="")
    parts = [header]
    parts += [f'<p class="profile"><strong>{escape(text)}</strong></p>' if bold
              else f'<p class="profile">{escape(text)}</p>'
              for text, bold in ((headline, True), (summary, False)) if text]

    # Relevance decides which jobs make the cut; the ones kept stay newest first, as a recruiter and
    # an ATS read a CV. Inside each job its most relevant bullets lead.
    jobs = sorted(sorted(enumerate(_once(facts.jobs, asked)), key=lambda pair: (
        -_shared(" ".join((pair[1].heading, *pair[1].bullets)), asked), pair[0]))[:ENTRIES_SHOWN])
    parts.append(_section("Experience", [_entry(entry, _relevant(entry.bullets, asked)[:BULLETS_SHOWN])
                                         for _, entry in jobs]
                          or _plain(facts, "experience")))
    parts.append(_section("Education", [_entry(entry, entry.bullets[:2]) for entry in _once(
        (entry for section in facts.cv.of("education") for entry in section.entries), asked)]
                          or _plain(facts, "education")))

    wanted = [skill for skill in facts.skills if words(skill) and words(skill) <= asked]
    skills = [f"<strong>{escape(skill)}</strong>" for skill in wanted] + [
        escape(skill) for skill in facts.skills if skill not in wanted]
    parts.append(_section("Skills", [f'<p class="entry-desc">{" · ".join(skills)}</p>'] if skills else []))
    parts.append(_section("Languages", _plain(facts, "languages")))
    # A section two CVs both have ("Certifications") once, with the lines of both.
    others: dict[str, tuple[str, list[str]]] = {}
    for section in facts.cv.of("other"):
        others.setdefault(fold(section.heading), (section.heading, []))[1].extend(section.lines)
    parts += [_section(heading.title() if heading.isupper() else heading,
                       [_list(list(dict.fromkeys(lines))[:6])]) for heading, lines in others.values()]
    return _page(f"{name}, CV", "\n".join(part for part in parts if part), template)


def _cover(facts: Facts, identity: dict, job, asked: set[str], template: Template) -> str:
    name, header = _header(facts, identity)
    role = _bare(getattr(job, "title", "") or "")
    company = (getattr(job, "company", "") or "").strip()
    bullets = list(dict.fromkeys(bullet for entry in facts.jobs for bullet in entry.bullets))
    # The most relevant first, and between equals the one with a number in it: an achievement.
    best = sorted(enumerate(bullets), key=lambda pair: (
        -_shared(pair[1], asked), not re.search(r"\d", pair[1]), pair[0]))[:ACHIEVEMENTS]
    applying = f"the {escape(role)} role" if role else "this role"
    lines = ["<p>Dear Hiring Manager,</p>",
             f"<p>I am applying for {applying}{f' at {escape(company)}' if company else ''}.</p>"]
    if best:
        lines += ["<p>Some of what I have done that fits it:</p>",
                  _list([bullet for _, bullet in best])]
    contact = " · ".join(escape(each) for each in (facts.email, facts.phone) if each)
    lines += ["<p>I would welcome the chance to talk about it.</p>",
              f"<p>Kind regards,<br>{escape(name)}{f'<br>{contact}' if contact else ''}</p>"]
    # Who it is to, when, and what about: from the job, on the day it is made. Each template places them.
    today = datetime.now(UTC)
    to = "<br>".join(escape(each) for each in (company, (getattr(job, "location", "") or "").strip()) if each)
    meta = ('<div class="letter-meta">\n' + (f'<p class="letter-to">{to}</p>\n' if to else "")
            + f'<p class="letter-date">{today.day} {today:%B %Y}</p>\n</div>\n'
            + f'<p class="letter-subject">{escape(f"Application for {role}" if role else "Job application")}'
              '</p>')
    return _page(f"{name}, cover letter",
                 f'{header}\n{meta}\n<div class="letter">\n' + "\n".join(lines) + "\n</div>", template)


def _header(facts: Facts, identity: dict) -> tuple[str, str]:
    """Their name and contact line: what they typed, else what their CV says."""
    name = (identity.get("full_name") or facts.name or "").strip()
    links = [link.get("url") if isinstance(link, dict) else str(link)
             for link in identity.get("links") or []] or list(facts.links)
    contact = [escape(each) for each in (facts.location, facts.phone) if each]
    if facts.email:
        contact.append(f'<a href="mailto:{escape(facts.email)}">{escape(facts.email)}</a>')
    contact += [f'<a href="{escape(url if "://" in url else f"https://{url}")}">'
                f'{escape(re.sub(r"^(?:https?://)?(?:www[.])?|/$", "", url))}</a>' for url in links if url]
    spans = "".join(f"<span>{each}</span>" for each in contact)
    return name, (f'<div class="cv-header">\n<div class="cv-name">{escape(name)}</div>\n'
                  f'<div class="cv-contact">{spans}</div>\n</div>\n<hr class="header-rule">')


def _page(title: str, body: str, template: Template) -> str:
    return ('<!DOCTYPE html>\n<html lang="en">\n<head>\n<meta charset="UTF-8">\n'
            f"<title>{escape(title)}</title>\n{template.style}\n"
            f"</head>\n<body>\n{body}\n</body>\n</html>\n")


def _section(title: str, items: list[str]) -> str:
    if not items:
        return ""
    return (f'<div class="section">\n<div class="section-title">{escape(title)}</div>\n'
            + "\n".join(items) + "\n</div>")


def _entry(entry: Entry, bullets) -> str:
    dates = f'<span class="entry-date">{escape(entry.dates)}</span>' if entry.dates else ""
    return (f'<div class="entry">\n<div class="entry-head"><span class="entry-title">'
            f"{escape(entry.heading)}</span>{dates}</div>\n{_list(bullets, plain=False)}</div>")


def _list(lines, *, plain: bool = True) -> str:
    items = "".join(f"<li>{escape(line)}</li>" for line in lines)
    kind = ' class="plain-list"' if plain else ""
    return f"<ul{kind}>{items}</ul>\n" if items else ""


def _plain(facts: Facts, kind: str) -> list[str]:
    """A section with no dated entries, as the lines they wrote."""
    lines = list(dict.fromkeys(line for section in facts.cv.of(kind) for line in section.lines))
    return [_list(lines)] if lines else []


def _relevant(bullets: tuple[str, ...], asked: set[str]) -> list[str]:
    return [bullet for _, bullet in sorted(enumerate(bullets),
                                           key=lambda pair: (-_shared(pair[1], asked), pair[0]))]


def _shared(text: str, asked: set[str]) -> int:
    """How many words a line of theirs has in common with the posting."""
    return len(words(text) & asked)


def _once(entries, asked: set[str]) -> list[Entry]:
    """Each job once, where it first comes. Two CVs often tell one job for two kinds of role, under a
    title each: it is shown under the title closer to the posting, with every bullet either has.

    One job is one organisation over the same months; without both, the heading and dates as written."""
    same: dict[tuple, list[Entry]] = {}
    for entry in entries:
        key = ((fold(entry.organisation), entry.start, entry.end) if entry.organisation and entry.start
               else (fold(entry.heading), entry.dates))
        same.setdefault(key, []).append(entry)
    shown = []
    for copies in same.values():
        best = max(copies, key=lambda each: _shared(each.heading, asked))
        bullets = dict.fromkeys(bullet for each in (best, *copies) for bullet in each.bullets)
        shown.append(replace(best, bullets=tuple(bullets)))
    return shown


def _bare(title: str) -> str:
    """A posting's title without its trimmings: "Backend Engineer (m/w/d) - Remote" is the role."""
    return re.split(r"\s+[-–—|/]\s+|\s*[(,]", title)[0].strip()  # noqa: RUF001
