"""Filling the base CV from a confirmed profile. Deterministic, no LLM.

The third renderer of the same shape as `libs.scoring.rubric` and `libs.scoring.facts`: an agent
captures, deterministic code renders. Here the stakes are the most direct of the three — the rubric
decides what a user is shown and the facts block grounds a generation, but this ends up as a PDF
attached to a real application under a real person's name. A wrong field is a document that looks
entirely correct and is about somebody else.

**What this produces is the input to tailoring, not the output.** `tailor_cv.md` receives the
rendered document as its BASE TEMPLATE and returns a tailored copy: it cuts entries, reorders
bullets, and rewrites the profile paragraph for one job. So this renderer's job is completeness and
truth, not selection — every entry the profile holds goes in, and the prompt cuts to the 4–6 that
earn their place. Nothing here decides what is relevant.

**Everything from the profile is escaped.** A profile is captured from documents the user uploaded,
which are untrusted text (`libs/contracts/profile.py`), and this output is HTML that a headless
Chrome then renders. An unescaped `<` is a broken layout at best and injected markup at worst; the
escaping is a boundary, not tidiness.

Sections with no data are omitted whole rather than left as an empty heading — a CV with a
"Projects" title and nothing under it reads as a bug in the product, which is exactly what it
would be.
"""
from __future__ import annotations

import html

from libs.contracts.profile import Education, Experience, ProfileSchema, Project
from libs.templates import BASE_CV_TEMPLATE, load

# Every marker the template carries. A marker the renderer forgets would reach a PDF as a literal
# HTML comment — invisible in the rendered page and permanently missing content, which is the
# quietest possible failure. `tests/test_cv_render.py` asserts none survives a render.
MARKERS: tuple[str, ...] = (
    "<!--NAME-->",
    "<!--CONTACT-->",
    "<!--PROFILE-->",
    "<!--SKILLS-->",
    "<!--EXPERIENCE-->",
    "<!--PROJECTS-->",
    "<!--EDUCATION-->",
    "<!--LANGUAGES-->",
    "<!--CERTIFICATIONS-->",
)

# Competency labels. "Programming languages" rather than "Languages" because the CV already has a
# Languages section further down and that one is about the languages the person *speaks* — the
# tailoring prompt reads both and must never merge them.
_SKILL_GROUPS = (
    ("Programming languages", "languages"),
    ("Frameworks &amp; libraries", "frameworks"),
    ("Domains", "domains"),
    ("Tools &amp; platforms", "tools"),
)


def render_cv(profile: ProfileSchema, *, summary: str | None = None) -> str:
    """The candidate's full CV as HTML: correct layout, all of their real experience.

    `summary` overrides the profile paragraph, for the caller that already has one. Left unset it
    is composed from the profile — see `_summary`.
    """
    filled = {
        "<!--NAME-->": _text(profile.identity.name),
        "<!--CONTACT-->": _contact(profile),
        "<!--PROFILE-->": _profile_paragraph(profile, summary),
        "<!--SKILLS-->": _skills(profile),
        "<!--EXPERIENCE-->": _experience(profile),
        "<!--PROJECTS-->": _projects(profile),
        "<!--EDUCATION-->": _education(profile),
        "<!--LANGUAGES-->": _languages(profile),
        "<!--CERTIFICATIONS-->": _certifications(profile),
    }
    document = load(BASE_CV_TEMPLATE)
    for marker, value in filled.items():
        document = document.replace(marker, value)
    return _collapse_blank_runs(document)


# ── header ────────────────────────────────────────────────────────────────────────────────────

def _contact(profile: ProfileSchema) -> str:
    """Location and links, each in its own `<span>` — the stylesheet spaces them by that.

    Deliberately no fallback to the account email, for the reason `libs.scoring.docprompt` gives:
    the address somebody signs in with is not necessarily the one they want a recruiter to have,
    and putting it on a document sent to an employer is not a decision this code gets to make.
    """
    parts = []
    if profile.identity.location:
        parts.append(_text(profile.identity.location))
    parts.extend(_contact_link(value) for value in profile.identity.links.values() if value.strip())
    return "".join(f"<span>{part}</span>" for part in parts)


def _contact_link(value: str) -> str:
    """A link if it is one, plain text if it is not.

    A profile's `links` map is where a portfolio and a LinkedIn go, and also where somebody puts a
    phone number. Guessing `https://` in front of a phone number produces a header that looks fine
    and a PDF whose contact detail is a dead link.
    """
    value = value.strip()
    if "://" in value:
        return _anchor(value, value.split("://", 1)[1].rstrip("/"))
    if "@" in value and " " not in value:
        return _anchor(f"mailto:{value}", value)
    if "." in value and " " not in value:
        return _anchor(f"https://{value}", value.rstrip("/"))
    return _text(value)


def _anchor(href: str, label: str) -> str:
    return f'<a href="{html.escape(href, quote=True)}">{_text(label)}</a>'


def _profile_paragraph(profile: ProfileSchema, summary: str | None) -> str:
    text = summary if summary is not None else _summary(profile)
    return f'<p class="profile">{_text(text)}</p>' if text else ""


def _summary(profile: ProfileSchema) -> str:
    """A factual opening line, composed from the profile and nothing else.

    The tailoring prompt rewrites this paragraph for every job — it is the section it is most
    explicit about (the "6-second test"). So the bar here is not persuasion, it is that an
    untailored CV still opens with something true. Every clause comes from a confirmed field:
    no adjectives, no achievements, nothing the facts block would not support.
    """
    role = _lead_role(profile)
    skills = [*profile.skills.languages[:4], *profile.skills.frameworks[:3]]

    if role and skills:
        return f"{role} with experience across {_join(skills)}."
    if role:
        return f"{role}."
    if skills:
        return f"Experience across {_join(skills)}."
    return ""


def _lead_role(profile: ProfileSchema) -> str:
    """What to call the candidate: what they are looking for, or failing that what they have done.

    Their target beats their history on purpose — a CV argues for the next role, and the profile's
    `targets` are the user's own answer to what that is.
    """
    role = profile.targets.role_types[0] if profile.targets.role_types else ""
    if not role and profile.experience:
        role = profile.experience[0].role
    if not role:
        return ""
    lead = f"{profile.targets.seniority} {role}" if profile.targets.seniority else role
    return lead[0].upper() + lead[1:]


# ── sections ──────────────────────────────────────────────────────────────────────────────────

def _skills(profile: ProfileSchema) -> str:
    items = [f"<strong>{label}:</strong> {_join_text(getattr(profile.skills, field))}"
             for label, field in _SKILL_GROUPS if getattr(profile.skills, field)]
    if not items:
        return ""
    return _section("Core Competencies", _list(items), extra_class=" competencies")


def _experience(profile: ProfileSchema) -> str:
    if not profile.experience:
        return ""
    return _section("Experience", "\n".join(_experience_entry(e) for e in profile.experience))


def _experience_entry(entry: Experience) -> str:
    title = f"{_text(entry.role)} &mdash; {_text(entry.org)}"
    if entry.stack:
        # `&middot;` then the stack, the reference instance's convention: a recruiter scanning
        # titles sees the technology without reading a bullet.
        title += f" &middot; {_join_text(entry.stack)}"
    dates = " &ndash; ".join(_text(part) for part in (entry.start, entry.end) if part)
    return _entry(title, dates=dates, bullets=[*entry.bullets, *entry.metrics])


def _projects(profile: ProfileSchema) -> str:
    """Projects as their own section, not folded into Experience.

    The distinction is the user's: they answered `experience` and `projects` separately, and a
    project presented as employment is the kind of overstatement that ends an interview. The
    tailoring prompt decides which of them earn space for a given job.
    """
    if not profile.projects:
        return ""
    return _section("Projects", "\n".join(_project_entry(p) for p in profile.projects))


def _project_entry(entry: Project) -> str:
    title = _text(entry.name)
    if entry.stack:
        title += f" &middot; {_join_text(entry.stack)}"
    return _entry(title, description=entry.what_built, bullets=list(entry.metrics))


def _education(profile: ProfileSchema) -> str:
    if not profile.education:
        return ""
    return _section("Education", "\n".join(_education_entry(e) for e in profile.education))


def _education_entry(entry: Education) -> str:
    """Degree, institution, no dates — the prompt's own rule, so the template must not supply any.

    The thesis line is labelled "Research:" for the same reason: the prompt bans "Dissertation" and
    "Thesis", and a base document that used one would make the model correct the template instead
    of tailoring it.
    """
    title = _text(entry.degree)
    if entry.field:
        title += f", {_text(entry.field)}"
    title += f" &middot; {_text(entry.institution)}"
    description = f"Research: &ldquo;{_text(entry.thesis)}&rdquo;" if entry.thesis else ""
    return _entry(title, description=description, escaped_description=True)


def _certifications(profile: ProfileSchema) -> str:
    """Courses, licences and accreditations, in the reference CV's own shape: name, issuer, year.

    A section rather than a skills bullet because that is where a recruiter looks for it and where
    an ATS matches it — "AWS Solutions Architect" under Certifications is a credential, and the same
    words in a competency list are a claim about familiarity.
    """
    if not profile.certifications:
        return ""
    items = []
    for entry in profile.certifications:
        line = f"<strong>{_text(entry.name)}</strong>"
        if entry.issuer:
            line += f" &mdash; {_text(entry.issuer)}"
        if entry.year:
            line += f" ({_text(entry.year)})"
        items.append(line)
    return _section("Certifications", _list(items, css_class="plain-list"))


def _languages(profile: ProfileSchema) -> str:
    """The spoken-language line, from `constraints.languages`.

    `constraints`, never `skills.languages` — the second is Go and Python, and a CV claiming native
    fluency in Go would be memorable for the wrong reason (the same trap `docprompt` documents).
    The section is omitted rather than defaulted to English: the prompt's rule is that the line
    lists exactly what the profile gives, and inventing a language is inventing a qualification.
    """
    if not profile.constraints.languages:
        return ""
    return _section("Languages", _list([_join_text(profile.constraints.languages)],
                                       css_class="plain-list"))


# ── HTML building blocks ──────────────────────────────────────────────────────────────────────

def _section(title: str, body: str, *, extra_class: str = "") -> str:
    return (f'<div class="section{extra_class}">\n'
            f'  <div class="section-title">{title}</div>\n'
            f"{body}\n"
            f"</div>")


def _entry(title: str, *, dates: str = "", description: str = "", bullets: list[str] | None = None,
           escaped_description: bool = False) -> str:
    """One `.entry` block: a head, an optional description, an optional bullet list.

    `break-inside: avoid` on `.entry` is what keeps an entry off a page boundary, so every repeated
    item is one of these rather than a bare heading plus list.
    """
    lines = ['  <div class="entry">', '    <div class="entry-head">',
             f'      <span class="entry-title">{title}</span>']
    if dates:
        lines.append(f'      <span class="entry-date">{dates}</span>')
    lines.append("    </div>")
    if description:
        body = description if escaped_description else _text(description)
        lines.append(f'    <p class="entry-desc">{body}</p>')
    if bullets:
        lines.append("    <ul>")
        lines.extend(f"      <li>{_text(bullet)}</li>" for bullet in bullets if bullet.strip())
        lines.append("    </ul>")
    lines.append("  </div>")
    return "\n".join(lines)


def _list(items: list[str], *, css_class: str = "") -> str:
    attribute = f' class="{css_class}"' if css_class else ""
    rendered = "\n".join(f"    <li>{item}</li>" for item in items)
    return f"  <ul{attribute}>\n{rendered}\n  </ul>"


def _text(value: str | None) -> str:
    """Every string from the profile goes through here. See the module docstring."""
    return html.escape(value or "", quote=False)


def _join(values: list[str]) -> str:
    """A prose list: "Python, Go and Docker"."""
    clean = [value.strip() for value in values if value.strip()]
    if len(clean) < 2:
        return _text(clean[0]) if clean else ""
    return _text(", ".join(clean[:-1])) + " and " + _text(clean[-1])


def _join_text(values: list[str]) -> str:
    return ", ".join(_text(value) for value in values if value.strip())


def _collapse_blank_runs(document: str) -> str:
    """An omitted section leaves the blank line its marker sat on. Two in a row become one.

    Cosmetic in the PDF and not cosmetic in review: the rendered HTML is what a support request
    arrives with, and a document full of gaps where sections used to be reads like data loss.
    """
    out: list[str] = []
    for line in document.split("\n"):
        if not line.strip() and out and not out[-1].strip():
            continue
        out.append(line)
    return "\n".join(out)
