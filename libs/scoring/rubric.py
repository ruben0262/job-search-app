"""Rendering a user's rubric from their confirmed profile."""
from __future__ import annotations

import hashlib
import json

from libs.contracts.profile import Constraints, ProfileSchema, Targets
from libs.prompts import LABEL_TEMPLATE, load

# Which parts of the profile actually change the rubric.
RUBRIC_RELEVANT_FIELDS = ("identity.name", "skills", "targets", "constraints", "not_list",
                          "experience")


def render_rubric(profile: ProfileSchema) -> str:
    """The user's full labelling rubric. Same profile in, same bytes out, always."""
    return (load(LABEL_TEMPLATE)
            .replace("<<NAME>>", profile.identity.name)
            .replace("<<WHO>>", _who_block(profile))
            .replace("<<HARD_GATES>>", _hard_gates(profile.constraints)))


def profile_version(rubric_md: str) -> str:
    """The label scope. Short on purpose — it appears on every label row and in every log line."""
    return hashlib.sha256(rubric_md.encode("utf-8")).hexdigest()[:8]


def rubric_relevant_digest(profile: ProfileSchema) -> str:
    """A hash of only the fields the rubric reads."""
    dumped = profile.model_dump()
    subset = {path: _resolve(dumped, path) for path in RUBRIC_RELEVANT_FIELDS}
    return hashlib.sha256(
        json.dumps(subset, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]


def _resolve(dumped: dict, path: str):
    """`"identity.name"` → the value at that path."""
    value = dumped
    for key in path.split("."):
        value = value[key]
    return value


# ── the WHO block ─────────────────────────────────────────────────────────────────────────────

def _who_block(profile: ProfileSchema) -> str:
    lines: list[str] = []

    seniority = profile.targets.seniority or "unspecified"
    education = profile.education[0] if profile.education else None
    if education is not None:
        lines.append(f"- {education.degree} ({education.institution}), {seniority} level. "
                     f"Judge seniority from the REQUIREMENTS, not the title.")
    else:
        lines.append(f"- {seniority.capitalize()} level. Judge seniority from the REQUIREMENTS, "
                     f"not the title.")

    strengths = _strengths(profile)
    if strengths:
        lines.append("- Strengths, each with shipped work:")
        lines.extend(f"  - {line}" for line in strengths)

    if profile.not_list:
        lines.append("- Does NOT have professional experience in: "
                     + ", ".join(profile.not_list)
                     + ". Roles whose CORE requirement is one of these are off-stack (some overlap "
                       "via general engineering skill, but not a primary match).")

    lines.append(_targets_line(profile.targets))
    lines.extend(_workplace_lines(profile.constraints))

    if profile.constraints.languages:
        langs = ", ".join(profile.constraints.languages)
        lines.append(f"- Languages: {langs}. A JD written in any other language means the employer "
                     f"expects that language — hard gate.")
    return "\n".join(lines)


def _strengths(profile: ProfileSchema) -> list[str]:
    """One line per skill category, plus the stacks actually shipped in roles and projects."""
    out = []
    for label, values in (("Languages", profile.skills.languages),
                          ("Frameworks and tools", profile.skills.frameworks + profile.skills.tools),
                          ("Domains", profile.skills.domains)):
        if values:
            out.append(f"{label}: {', '.join(_dedupe(values))}.")

    shipped = _dedupe([tech for entry in profile.experience for tech in entry.stack]
                      + [tech for entry in profile.projects for tech in entry.stack])
    if shipped:
        out.append(f"Shipped in production or research: {', '.join(shipped)}.")
    return out


def _targets_line(targets: Targets) -> str:
    if not targets.role_types:
        return "- Targets: any engineering role matching the strengths above."
    ordered = "; ".join(f"({chr(97 + i)}) {role}" for i, role in enumerate(targets.role_types))
    size = f" {targets.company_size} preferred." if targets.company_size else ""
    return f"- Targets, priority order: {ordered}.{size}"


def _workplace_lines(constraints: Constraints) -> list[str]:
    policy = constraints.remote_policy or "remote_or_hybrid"
    onsite = ", ".join(constraints.onsite_locations) if constraints.onsite_locations else None

    if policy == "any":
        # Workplace mode and acceptable region are independent; conflating them loses a real constraint.
        line = "- Workplace policy: remote, hybrid, and on-site are all equally acceptable"
        line += (f". On-site work is acceptable in {onsite}, and nowhere else without relocation "
                 f"or visa support." if onsite else ".")
    else:
        line = ("- Workplace policy: FULLY REMOTE only. Hybrid and on-site are dealbreakers"
                if policy == "remote_only"
                else "- Workplace policy: FULLY REMOTE is the goal. Hybrid is acceptable")
        line += (f". On-site is acceptable ONLY in {onsite}." if onsite
                 else ". Mandatory relocation is a dealbreaker.")

    lines = [line, "- Unpaid or equity-only is a dealbreaker. Contract, freelance, and part-time "
                   "remote roles count the same as full-time — do not penalise them."]
    if constraints.min_salary:
        lines.append(f"- Below {constraints.min_salary:,} is a dealbreaker where salary is stated; "
                     f"an unstated salary is not a gate.")
    return lines


def _hard_gates(constraints: Constraints) -> str:
    gates = ["   - non-technical role (no engineering or quantitative component)",
             "   - seniority scope far above the band stated above",
             "   - unpaid / equity-only"]

    policy = constraints.remote_policy or "remote_or_hybrid"
    onsite = ", ".join(constraints.onsite_locations)
    if onsite:
        # Named regions mean anywhere else is gated, whatever the workplace mode.
        gates.append(f"   - on-site-only outside {onsite}, with no relocation or visa support")
    elif policy != "any":
        gates.append("   - on-site-only, or mandatory relocation")
    if policy == "remote_only":
        gates.append("   - hybrid or any required office attendance")
    gates.append("   - remote but restricted to a region they cannot legally work in "
                 "(e.g. \"US only\", \"must be authorized to work in the US\")")
    if constraints.languages:
        gates.append(f"   - JD language is not one of: {', '.join(constraints.languages)}")
    return "\n".join(gates)


def _dedupe(values: list[str]) -> list[str]:
    seen, out = set(), []
    for value in values:
        key = value.casefold()
        if value and key not in seen:
            seen.add(key)
            out.append(value)
    return out
