"""What the user typed themselves, rendered for a setup conversation.

**Hard data**: their name, their links, their search settings. Typed on a form or in the chat, stored
in plain columns, and true because they said so. The rest of a profile is soft — the agents write
`context.md`, `01-candidate-profile.md` and the writing prompts by reading documents, and those are
inferences that go stale.

Injected into the setup agent's system prompt rather than offered as a tool, and the difference
matters. A tool the model *may* call is one it may also skip — and then the hard data exists while
the agent never looks, which is how one account's two new links never reached the prompt that writes
its contact line. Nothing here needs judgement, so nothing here needs a decision: it is always
present.

Read from the columns each save path already writes. Nothing is stored to answer this.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from libs.contracts.config import migrate_config
from libs.db.models import User, UserConfig


def _links(user: User) -> list[str]:
    out = []
    for entry in user.links or []:
        if not isinstance(entry, dict):
            continue
        url = (entry.get("url") or "").strip()
        if url:
            label = (entry.get("platform") or "").strip()
            out.append(f"{label}: {url}" if label else url)
    return out


def _search(config: dict | None) -> list[str]:
    """Their search settings, in the shape the current schema reads whatever version was stored."""
    if not config:
        return ["Search settings: not set yet."]

    config = migrate_config(config)
    lines: list[str] = []
    for index, section in enumerate(config.get("sections") or [], 1):
        parts = [f"keywords: {', '.join(section.get('keywords') or []) or 'none'}"]
        if section.get("exclude_keywords"):
            parts.append(f"never show: {', '.join(section['exclude_keywords'])}")
        if section.get("locations"):
            parts.append(f"places: {', '.join(section['locations'])}")
        if section.get("remote_policy"):
            parts.append(f"remote policy: {section['remote_policy']}")
        lines.append(f"- Search {index} — " + "; ".join(parts))

    for label, key in (("Seniority", "seniority"), ("Languages they can apply in", "languages")):
        if config.get(key):
            lines.append(f"- {label}: {', '.join(config[key])}")
    if config.get("min_salary"):
        lines.append(f"- Minimum salary: {config['min_salary']} {config.get('salary_currency', '')}"
                     .strip())
    return lines


def render(session: Session, user_id) -> str:
    """The typed half of this person's profile, as plain text. Empty string if there is none."""
    user = session.get(User, user_id)
    if user is None:
        return ""

    config = session.scalar(select(UserConfig.config).where(UserConfig.user_id == user_id))
    lines = [f"- Name: {user.full_name}" if user.full_name else "- Name: not given yet",
             f"- Account email: {user.email}"]
    links = _links(user)
    lines.append(f"- Links: {'; '.join(links)}" if links else "- Links: none given")
    if user.timezone:
        lines.append(f"- Timezone: {user.timezone}")
    lines += _search(config)

    return (
        "\n\n---\n\n"
        "# What this person typed themselves\n\n"
        "These are their own answers, from the signup form and their settings page — not read out of "
        "a document. Where a document disagrees with this, **this is the one that is right**, and it "
        "is what their name, contact line and search should be written from.\n\n"
        + "\n".join(lines) + "\n")
