"""The facts block — grounding for every generation call."""
from __future__ import annotations

from libs.contracts.profile import ProfileSchema


def render_facts(profile: ProfileSchema) -> str:
    """Everything a generation call may state as fact about this person."""
    sections = [
        f"# Facts about {profile.identity.name}",
        "",
        "Everything below is confirmed by the candidate. **Nothing outside this file may be stated "
        "as fact about them** — not an employer, not a date, not a metric, not a technology. If a "
        "job description asks for something that is not here, say what they do have instead of "
        "inventing what they do not.",
        "",
        _identity(profile),
        _education(profile),
        _experience(profile),
        _projects(profile),
        _certifications(profile),
        _skills(profile),
        _rules(profile),
    ]
    return "\n".join(section for section in sections if section).rstrip() + "\n"


def _identity(profile: ProfileSchema) -> str:
    lines = ["## Identity", "", f"- Name: {profile.identity.name}"]
    if profile.identity.location:
        lines.append(f"- Based in: {profile.identity.location}")
    if profile.identity.work_eligibility:
        lines.append(f"- Work eligibility: {', '.join(profile.identity.work_eligibility)}")
    for label, url in profile.identity.links.items():
        lines.append(f"- {label}: {url}")
    return "\n".join(lines) + "\n"


def _education(profile: ProfileSchema) -> str:
    if not profile.education:
        return ""
    lines = ["## Education", ""]
    for entry in profile.education:
        field = f", {entry.field}" if entry.field else ""
        lines.append(f"- {entry.degree}{field} — {entry.institution}")
        if entry.thesis:
            lines.append(f"  - Thesis: {entry.thesis}")
    return "\n".join(lines) + "\n"


def _experience(profile: ProfileSchema) -> str:
    if not profile.experience:
        return ""
    lines = ["## Experience", "",
             "Bullets and metrics below are the candidate's own. Reuse or rephrase them; do not "
             "add numbers that are not here.", ""]
    for entry in profile.experience:
        dates = " — ".join(p for p in (entry.start, entry.end) if p)
        lines.append(f"### {entry.role}, {entry.org}" + (f" ({dates})" if dates else ""))
        if entry.stack:
            lines.append(f"- Stack: {', '.join(entry.stack)}")
        lines.extend(f"- {bullet}" for bullet in entry.bullets)
        lines.extend(f"- Metric: {metric}" for metric in entry.metrics)
        lines.append("")
    return "\n".join(lines)


def _projects(profile: ProfileSchema) -> str:
    if not profile.projects:
        return ""
    lines = ["## Projects", ""]
    for entry in profile.projects:
        lines.append(f"### {entry.name}")
        if entry.what_built:
            lines.append(f"- {entry.what_built}")
        if entry.stack:
            lines.append(f"- Stack: {', '.join(entry.stack)}")
        lines.extend(f"- Metric: {metric}" for metric in entry.metrics)
        lines.append("")
    return "\n".join(lines)


def _certifications(profile: ProfileSchema) -> str:
    """What they hold, so a generation can name one — and so it names no others."""
    if not profile.certifications:
        return ""
    lines = ["## Certifications", ""]
    for entry in profile.certifications:
        issuer = f" — {entry.issuer}" if entry.issuer else ""
        year = f" ({entry.year})" if entry.year else ""
        lines.append(f"- {entry.name}{issuer}{year}")
    return "\n".join(lines) + "\n"


def _skills(profile: ProfileSchema) -> str:
    groups = [("Languages", profile.skills.languages),
              ("Frameworks", profile.skills.frameworks),
              ("Domains", profile.skills.domains),
              ("Tools", profile.skills.tools)]
    present = [(label, values) for label, values in groups if values]
    if not present:
        return ""
    lines = ["## Skills", ""]
    lines.extend(f"- {label}: {', '.join(values)}" for label, values in present)
    return "\n".join(lines) + "\n"


def _rules(profile: ProfileSchema) -> str:
    """The three derived rules §6.2 names: CV header location, languages, exclusions."""
    lines = ["## Rules for anything written on their behalf", ""]

    # The CV header location rule.
    eligibility = profile.identity.work_eligibility
    if eligibility:
        lines.append(f"- **CV header location**: state eligibility, not just a city — "
                     f"{', '.join(eligibility)}. A bare city on an application to another country "
                     f"reads as a relocation request.")
    elif profile.identity.location:
        lines.append(f"- **CV header location**: {profile.identity.location}.")

    if profile.constraints.languages:
        lines.append(f"- **Languages line**: {', '.join(profile.constraints.languages)}. "
                     f"Do not claim any other language at any level.")

    if profile.exclusions:
        lines.append("- **Never include on a CV or in a cover letter**: "
                     + "; ".join(profile.exclusions) + ".")

    if profile.constraints.remote_policy:
        lines.append(f"- **Workplace**: {profile.constraints.remote_policy.replace('_', ' ')}"
                     + (f"; on-site acceptable in {', '.join(profile.constraints.onsite_locations)}"
                        if profile.constraints.onsite_locations else "") + ".")

    lines.append("- **Never invent**: an employer, a job title, a date, a metric, a certification, "
                 "or a technology. An unsupported claim on a real application is the one failure "
                 "this file exists to prevent.")
    return "\n".join(lines) + "\n"
