"""Who else sees a user's data, in the words the user is shown.

**In `libs` and not in a template, because two things must agree.** The consent screen shows this
list, and the privacy policy publishes it; if a processor were added to one and not the other, the
consent recorded would be to a disclosure that no longer describes what happens. The version below
is what `users.processing_consent_version` stores, so "they consented" has an answer to "to what".

**Bump `DISCLOSURE_VERSION` whenever this list changes.** Adding a processor means every account is
asked again — that is the point, not an inconvenience to route around.
"""
from __future__ import annotations

from dataclasses import dataclass

# Date-stamped rather than sequential, so a stored consent says *when* it was given to what.
DISCLOSURE_VERSION = "2026-07-28"


@dataclass(frozen=True)
class Processor:
    name: str
    purpose: str
    data: str
    # Whose credential pays for it. "Your own key" is materially different from "ours" to a person
    # deciding whether to agree, and it is true of every LLM call this platform makes.
    on_your_credential: bool
    location: str


PROCESSORS = (
    Processor(
        name="Anthropic",
        purpose="Building your profile from your documents, and scoring jobs against it.",
        data="The text of the documents you upload, and the title, company, location and "
             "description of job postings.",
        on_your_credential=True,
        location="United States",
    ),
    Processor(
        name="Telegram",
        purpose="Delivering your job matches.",
        data="The job titles, companies and links we send you. Your bot token and chat id.",
        on_your_credential=True,
        location="Outside the EU",
    ),
    Processor(
        name="Your proxy provider",
        purpose="Fetching LinkedIn postings on your behalf. Never shared with another user "
                "(decision D3).",
        data="The job-search requests made for your account.",
        on_your_credential=True,
        location="Depends on the provider you chose",
    ),
)

# What the consent screen says above the list. Deliberately short and specific: a wall of text is a
# wall people click past, and a consent nobody read is not one.
CONSENT_HEADLINE = "Processing your documents sends their text to Anthropic."

CONSENT_BODY = (
    "To build your profile we send the text of the documents you uploaded to Anthropic, using "
    "**your own Claude credential**. The files themselves never leave this server — only the text "
    "we extracted from them, and only when you press the button.\n\n"
    "You can withdraw this at any time by deleting your account, which removes everything we hold. "
    "We cannot recall what Anthropic has already processed; their retention is governed by their "
    "own terms."
)


def disclosure() -> dict:
    """The machine-readable form, for the consent screen and the policy page alike."""
    return {
        "version": DISCLOSURE_VERSION,
        "headline": CONSENT_HEADLINE,
        "body": CONSENT_BODY,
        "processors": [
            {"name": p.name, "purpose": p.purpose, "data": p.data,
             "on_your_credential": p.on_your_credential, "location": p.location}
            for p in PROCESSORS],
    }
