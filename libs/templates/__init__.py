"""The looks a CV and a cover letter can have, and the one each person picked.

**A template is a stylesheet over one markup.** Every CV the platform makes carries the same class names
(`cv-header`, `section-title`, `entry`, `entry-date`…), and every letter the same frame (`letter-meta`
holding `letter-to`, then `letter-subject` and `letter`; never a date of its own). So a template is one
HTML file in `cv/` or `cover/` whose `<head>` holds its look and nothing else: the writer
fills the body when the document is made, and the template lays it out. Each person has one of each,
`User.cv_template` and `User.cover_template` (owner, 2026-11-07); none picked is `DEFAULT`.

Meridian, the CV everyone had before templates, is the scaffold itself (`SCAFFOLD_CV`): read here, never
written (CLAUDE.md §3a). Why every template is a single column, and what each sector expects, with the
sources: README.md.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

_HERE = Path(__file__).resolve().parent

# The one base CV a new account is seeded with, and the one every user's own copy descends from.
SCAFFOLD_CV = _HERE.parents[0] / "scaffolds" / "base_cv.html"


@lru_cache(maxsize=64)
def load(path: Path | str) -> str:
    """Read a template file, with newlines normalised."""
    return Path(path).read_text(encoding="utf-8").replace("\r\n", "\n")


STYLE = re.compile(r"(?is)<style\b.*?</style>")
_PAGE_MARGIN = re.compile(r"(?is)@page\s*{[^}]*?\bmargin\s*:\s*([^;}]+)")


def split_style(html: str) -> tuple[str, str]:
    """The document without its comments and its first `<style>` block, and that block ("" if none).

    Comments go first. The scaffold's own says "Keep the <style> block intact", and a search starting
    there took the comment's end, `<head>` and the placeholder `<title>` along with the CSS — the model
    never saw `<body>`, and every AI CV came back carrying `[YOUR NAME]`. One definition, where two parts
    of the platform had each found the block their own way.
    """
    html = re.sub(r"(?s)<!--.*?-->", "", html)
    match = STYLE.search(html)
    return (html[:match.start()] + html[match.end():], match.group(0)) if match else (html, "")


# What every look keeps, so an applicant tracking system reads each word whole (README, "Adding a look").
# Here once rather than in each look, so that a new look cannot leave it out, and Meridian, which is the
# scaffold's own stylesheet and never edited (CLAUDE.md §3a), has it too. Measured 2026-11-08, five PDF
# readers over every look:
# - No ligatures. Lato and Carlito draw "fi", "fl" and "ffi" as one glyph, which pypdf and pdfminer read
#   as one character: "certiﬁed", "workﬂow" and "oﬃce", which no search for the word finds.
# - Each part of the contact line moves to the next line whole, and breaks inside only when it is wider
#   than the line (a very long link). Meridian's broke anywhere: a phone came out "91234-" over "5678",
#   which all five read wrongly and poppler as "912345678".
EVERY_LOOK = ("\n/* Every look (libs/templates EVERY_LOOK): what an ATS needs to read each word whole. */\n"
              "body { font-variant-ligatures: none; }\n"
              ".cv-contact { flex-wrap: wrap; }\n"
              ".cv-contact span { display: inline-block; overflow-wrap: anywhere; }\n")

KINDS = ("cv", "cover")
# Where each kind's pick is kept on the account.
FIELD = {"cv": "cv_template", "cover": "cover_template"}

# The sectors the picker groups templates under, in the order it shows them.
SECTORS = (
    "IT and software", "Engineering", "Finance and accounting", "Business and management",
    "Sales and customer service", "Marketing, media and design", "Healthcare and care",
    "Teaching and education", "Science and research", "Law", "Government and charities",
    "Hospitality and retail", "Building, trades and logistics", "Office and admin",
)
POPULAR_TITLE = "Most popular"


@dataclass(frozen=True)
class Template:
    kind: str
    id: str
    name: str
    sectors: tuple[str, ...]
    path: Path

    @property
    def style(self) -> str:
        """Its `<style>` block, ending in what every look keeps for an ATS (`EVERY_LOOK`): what an AI CV
        is given back after the model wrote its text. One block, as every step after expects one."""
        block = split_style(load(self.path))[1]
        css, end, _ = block.rpartition("</style>")
        return f"{css}{EVERY_LOOK}{end}" if end else block

    @property
    def page_margin(self) -> str:
        """The margin its `@page` rule gives the printed page. A screen has no page, so a picture pads
        the body by this instead: without it, the shapes run to the very edge of the picture."""
        match = _PAGE_MARGIN.search(self.style)
        if match is None:
            logger.warning("template %s/%s has no @page margin; its picture is drawn with 18mm",
                           self.kind, self.id)
        return match.group(1).strip() if match else "18mm"


def _cv(id_: str, name: str, *sectors: str) -> Template:
    path = SCAFFOLD_CV if id_ == "meridian" else _HERE / "cv" / f"{id_}.html"
    return Template("cv", id_, name, sectors, path)


def _cover(id_: str, name: str, *sectors: str) -> Template:
    return Template("cover", id_, name, sectors, _HERE / "cover" / f"{id_}.html")


# Every look, in the order research ranks them (README.md); each sector lists its own in this order.
CATALOGUE: tuple[Template, ...] = (
    _cv("meridian", "Meridian",
        "IT and software", "Engineering", "Business and management", "Sales and customer service",
        "Science and research", "Office and admin"),
    _cv("kestrel", "Kestrel",
        "IT and software", "Engineering", "Business and management", "Marketing, media and design",
        "Science and research"),
    _cv("ledger", "Ledger",
        "Finance and accounting", "Business and management", "Law", "Government and charities"),
    _cv("prism", "Prism",
        "Marketing, media and design", "Sales and customer service", "Hospitality and retail"),
    _cv("tabula", "Tabula",
        "Engineering", "Finance and accounting", "Government and charities",
        "Building, trades and logistics", "Office and admin"),
    _cv("halcyon", "Halcyon",
        "Sales and customer service", "Healthcare and care", "Teaching and education",
        "Government and charities", "Hospitality and retail"),
    _cv("foundry", "Foundry",
        "Hospitality and retail", "Building, trades and logistics"),
    _cv("quill", "Quill",
        "Teaching and education", "Science and research", "Law"),

    _cover("meridian", "Meridian",
           "IT and software", "Engineering", "Business and management", "Sales and customer service",
           "Teaching and education", "Science and research", "Office and admin"),
    _cover("kestrel", "Kestrel",
           "IT and software", "Engineering", "Business and management", "Marketing, media and design",
           "Science and research"),
    _cover("ledger", "Ledger",
           "Finance and accounting", "Business and management", "Law", "Government and charities"),
    _cover("tabula", "Tabula",
           "Engineering", "Finance and accounting", "Healthcare and care", "Government and charities",
           "Building, trades and logistics", "Office and admin"),
    _cover("halcyon", "Halcyon",
           "Sales and customer service", "Healthcare and care", "Teaching and education",
           "Government and charities", "Hospitality and retail"),
    _cover("prism", "Prism",
           "Marketing, media and design", "Sales and customer service", "Hospitality and retail"),
)

# What research shows is used most (owner, 2026-11-07: fixed, not counted from picks). README.md has why.
POPULAR = {"cv": ("meridian", "kestrel", "ledger", "prism"),
           "cover": ("meridian", "kestrel", "ledger", "tabula")}
# Somebody who never picked has what everyone had before templates.
DEFAULT = "meridian"


def find(kind: str, template_id: str | None) -> Template | None:
    return next((each for each in CATALOGUE if each.kind == kind and each.id == template_id), None)


def of_kind(kind: str) -> list[Template]:
    return [each for each in CATALOGUE if each.kind == kind]


def chosen(user, kind: str) -> Template:
    """The look this person's documents of `kind` are made in."""
    picked = getattr(user, FIELD[kind])
    template = find(kind, picked or DEFAULT)
    if template is None:
        # A template taken out of the catalogue after somebody picked it.
        logger.warning("templates: %s picked %s template %r, which is gone; making it in %s",
                       getattr(user, "id", "?"), kind, picked, DEFAULT)
        template = find(kind, DEFAULT)
    return template


def groups(kind: str) -> list[tuple[str, list[str]]]:
    """What the picker shows, in order: the most popular, then each sector with its templates."""
    shelf = [(POPULAR_TITLE, list(POPULAR[kind]))]
    for sector in SECTORS:
        ids = [each.id for each in of_kind(kind) if sector in each.sectors]
        if ids:
            shelf.append((sector, ids))
    return shelf


# ── the picture of a look ─────────────────────────────────────────────────────────────────────

# Every shape in a picture: a rounded block in its element's own colour, sized by its own type. Lines of
# writing are faint; names, headings, titles and dates are solid. A contact line's separators are drawn as
# gaps, never as characters.
_PICTURE_STYLE = ("<style>@media screen { html { background: #fff; } body { padding: %s; } "
                  "i { display: inline-block; vertical-align: middle; height: 0.75em; "
                  "border-radius: 0.375em; background: currentColor; } i.t { opacity: 0.22; } "
                  '.cv-contact span::before { content: "" !important; } }</style>')


def _shape(width: str, line: bool = False) -> str:
    return f'<i{' class="t"' if line else ""} style="width: {width}"></i>'


def _lines(*widths: str) -> str:
    return "<br>".join(_shape(width, line=True) for width in widths)


def _items(*widths: str) -> str:
    return "".join(f"<li>{_shape(width, line=True)}</li>" for width in widths)


def _entry(title: str, date: str, *bullets: str) -> str:
    items = f"<ul>{_items(*bullets)}</ul>" if bullets else ""
    return (f'<div class="entry"><div class="entry-head"><span class="entry-title">{_shape(title)}</span>'
            f'<span class="entry-date">{_shape(date)}</span></div>{items}</div>')


def _section(width: str, *body: str) -> str:
    return f'<div class="section"><div class="section-title">{_shape(width)}</div>{"".join(body)}</div>'


_HEADER = ('<div class="cv-header"><div class="cv-name">' + _shape("58mm") + '</div><div class="cv-contact">'
           + "".join(f"<span>{_shape(width)}</span>" for width in ("16mm", "20mm", "28mm", "24mm"))
           + '</div></div><hr class="header-rule">')

# The same few parts for every look, in the markup every document carries. Each look's own stylesheet
# lays them out, so a picture shows its colours, type sizes, rules and spacing, and nothing else.
_SKETCH = {
    "cv": _HEADER + f'<p class="profile">{_lines("94%", "86%", "48%")}</p>'
          + _section("30mm", _entry("62%", "22mm", "92%", "84%", "88%"),
                     _entry("54%", "22mm", "86%", "74%", "90%"), _entry("46%", "22mm", "80%", "68%"),
                     _entry("52%", "22mm", "88%", "72%"))
          + _section("24mm", _entry("64%", "16mm"), _entry("56%", "16mm"))
          + _section("18mm", f'<p class="entry-desc">{_lines("96%", "62%")}</p>')
          + _section("22mm", f'<ul class="plain-list"><li>{_shape("54%", line=True)}</li></ul>'),
    "cover": _HEADER + f'<div class="letter-meta"><p class="letter-to">{_lines("34mm", "26mm")}</p></div>'
             + f'<p class="letter-subject">{_shape("46%")}</p><div class="letter">'
             + f'<p>{_shape("24%", line=True)}</p><p>{_lines("96%", "90%", "58%")}</p>'
             + f'<p>{_lines("94%", "40%")}</p>'
             + f'<ul>{_items("84%", "76%", "88%")}</ul>'
             + f'<p>{_lines("92%", "66%")}</p><p class="closing">{_shape("18%", line=True)}</p>'
             + f'<p class="signature">{_shape("26%")}</p></div>',
}


def picture(template: Template) -> str:
    """The look drawn, as the picker shows it (owner, 2026-11-07): abstract shapes in the look's own
    colours and sizes, with no word, letter or character in it, not even a title. A screen has no page,
    so the page's margins pad the body.

    A page's `<head>` and `<body>`, which the site parses into an `<html>` of its own in a shadow root
    (`web/src/templates.tsx`): the look's stylesheet applies there exactly as in a page of its own, and
    the site's cannot reach it."""
    return (f"<head>{template.style}{_PICTURE_STYLE % template.page_margin}</head>"
            f"<body>{_SKETCH[template.kind]}</body>")
