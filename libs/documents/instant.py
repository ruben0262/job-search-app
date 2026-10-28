"""The free CV and cover letter, built by code from the person's own documents. No AI.

Every sentence about the person is a line of their own documents, picked and ordered by how much it
shares with the posting. The only words of ours are the frame: section headings, a greeting, a close.
The look is the scaffold CV's, whose `<style>` block is read at runtime so the two cannot drift; the
scaffold itself is a prompt file and is never written to (CLAUDE.md §3a).
"""
from __future__ import annotations

import re
from html import escape

from libs.documents.read import Entry, Facts, fold, read, words
from libs.templates import SCAFFOLD_CV, load

# How much the CV keeps: the most relevant jobs, the most relevant bullets of each.
ENTRIES_SHOWN, BULLETS_SHOWN = 5, 4
# How many of their achievements the cover letter quotes.
ACHIEVEMENTS = 3

_LETTER_STYLE = ("<style>body { font-size: 10.5pt; line-height: 1.45; } "
                 ".letter p, .letter ul { margin: 0 0 9pt 0; } .letter ul { padding-left: 14pt; }</style>")


def render(kind: str, *, documents: dict[str, str], identity: dict, job) -> str:
    """A complete HTML document for one job: `kind` is "cv" or "cover".

    `documents` maps a document kind to its text, `identity` carries `full_name` and `links`, and
    `job` has `title`, `company`, `description`, `location` and `url`.
    """
    facts = read(documents)
    asked = words(f"{getattr(job, 'title', '') or ''} {getattr(job, 'description', '') or ''}")
    if kind == "cv":
        return _cv(facts, identity, job, asked, mine=words("\n".join(documents.values())))
    if kind == "cover":
        return _cover(facts, identity, job, asked)
    raise ValueError(f"unknown document kind {kind!r}: expected 'cv' or 'cover'")


def _cv(facts: Facts, identity: dict, job, asked: set[str], *, mine: set[str]) -> str:
    name, header = _header(facts, identity)
    title = _bare(getattr(job, "title", "") or "")
    # Their title mirrors the posting's only when every word of it is already in their documents.
    headline = title if title and words(title) <= mine else (facts.titles[0] if facts.titles else "")
    summary = " ".join(line for section in facts.cv.of("profile") for line in section.lines)
    parts = [header]
    parts += [f'<p class="profile"><strong>{escape(text)}</strong></p>' if bold
              else f'<p class="profile">{escape(text)}</p>'
              for text, bold in ((headline, True), (summary, False)) if text]

    jobs = sorted(enumerate(_once(facts.jobs)), key=lambda pair: (
        -_shared(" ".join((pair[1].heading, *pair[1].bullets)), asked), pair[0]))
    parts.append(_section("Experience", [_entry(entry, _relevant(entry.bullets, asked)[:BULLETS_SHOWN])
                                         for _, entry in jobs[:ENTRIES_SHOWN]]
                          or _plain(facts, "experience")))
    parts.append(_section("Education", [_entry(entry, entry.bullets[:2]) for entry in _once(
        entry for section in facts.cv.of("education") for entry in section.entries)]
                          or _plain(facts, "education")))

    wanted = [skill for skill in facts.skills if words(skill) and words(skill) <= asked]
    skills = [f"<strong>{escape(skill)}</strong>" for skill in wanted] + [
        escape(skill) for skill in facts.skills if skill not in wanted]
    parts.append(_section("Skills", [f'<p class="entry-desc">{" · ".join(skills)}</p>'] if skills else []))
    parts.append(_section("Languages", _plain(facts, "languages")))
    parts += [_section(section.heading.title() if section.heading.isupper() else section.heading,
                       [_list(section.lines[:6])]) for section in facts.cv.of("other")]
    return _page(f"{name}, CV", "\n".join(part for part in parts if part))


def _cover(facts: Facts, identity: dict, job, asked: set[str]) -> str:
    name, header = _header(facts, identity)
    role = _bare(getattr(job, "title", "") or "")
    company = (getattr(job, "company", "") or "").strip()
    bullets = [bullet for entry in facts.jobs for bullet in entry.bullets]
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
    return _page(f"{name}, cover letter",
                 header + '\n<div class="letter">\n' + "\n".join(lines) + "\n</div>", _LETTER_STYLE)


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


def _page(title: str, body: str, style: str = "") -> str:
    # Comments out first: the scaffold's own says "Keep the <style> block intact", and a search that
    # starts there carries its `<title>[YOUR NAME] – CV</title>` into every document.
    scaffold = re.search(r"(?is)<style\b[^>]*>.*?</style>", re.sub(r"(?s)<!--.*?-->", "", load(SCAFFOLD_CV)))
    return ('<!DOCTYPE html>\n<html lang="en">\n<head>\n<meta charset="UTF-8">\n'
            f"<title>{escape(title)}</title>\n{scaffold.group(0) if scaffold else ''}\n{style}\n"
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
    lines = [line for section in facts.cv.of(kind) for line in section.lines]
    return [_list(lines)] if lines else []


def _relevant(bullets: tuple[str, ...], asked: set[str]) -> list[str]:
    return [bullet for _, bullet in sorted(enumerate(bullets),
                                           key=lambda pair: (-_shared(pair[1], asked), pair[0]))]


def _shared(text: str, asked: set[str]) -> int:
    """How many words a line of theirs has in common with the posting."""
    return len(words(text) & asked)


def _once(entries) -> list[Entry]:
    """Each entry once: two uploads of one CV list every job twice."""
    seen: dict[tuple[str, str], Entry] = {}
    for entry in entries:
        seen.setdefault((fold(entry.heading), entry.dates), entry)
    return list(seen.values())


def _bare(title: str) -> str:
    """A posting's title without its trimmings: "Backend Engineer (m/w/d) - Remote" is the role."""
    return re.split(r"\s+[-–—|/]\s+|\s*[(,]", title)[0].strip()  # noqa: RUF001
