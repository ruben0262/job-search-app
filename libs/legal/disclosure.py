"""Who else sees a user's data, in the words the user is shown."""
from __future__ import annotations

from dataclasses import dataclass

# Date-stamped rather than sequential, so a stored consent says *when* it was given to what.
# 2026-10-28: Google and OpenAI added; Anthropic's purpose is now documents only — profiles are read
# by rules and nothing scores (ROADMAP Phase 8); and a small allowance runs on the platform's own
# key. A new processor asks everyone again.
DISCLOSURE_VERSION = "2026-10-28"


@dataclass(frozen=True)
class Processor:
    name: str
    purpose: str
    data: str
    # Whose credential pays for it.
    on_your_credential: bool
    location: str


# What reaches whichever of the three providers writes a document. Instant documents reach none.
# `on_your_credential` is False for all three: an account with no key of its own writes on the
# platform's, within its daily allowance, so "paid for by your own credential" is not always true.
_SENT_FOR_A_DOCUMENT = ("Your profile and base CV as this site holds them, and the title, company, "
                        "location and description of the job posting it is written for.")

PROCESSORS = (
    Processor(
        name="Anthropic",
        purpose="Writing a CV or cover letter when you ask for an AI-written one: on your own "
                "Claude key or subscription, or on the platform's key within your daily "
                "allowance if the platform's key is Anthropic's.",
        data=_SENT_FOR_A_DOCUMENT,
        on_your_credential=False,
        location="United States",
    ),
    Processor(
        name="Google (Gemini API)",
        purpose="The same, on your own Gemini key or on the platform's if it is Google's. Only a "
                "key from a project with billing turned on: Google's free tier may not be used "
                "for personal data.",
        data=_SENT_FOR_A_DOCUMENT,
        on_your_credential=False,
        location="Outside the EU",
    ),
    Processor(
        name="OpenAI",
        purpose="The same, on your own OpenAI key or on the platform's if it is OpenAI's.",
        data=_SENT_FOR_A_DOCUMENT,
        on_your_credential=False,
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

# What the consent screen says above the list.
CONSENT_HEADLINE = "An AI-written CV or cover letter sends your profile to the AI that writes it."

CONSENT_BODY = (
    "Instant CVs and cover letters are built on this server, by code, and nothing leaves it.\n\n"
    "When you ask for an AI-written one, we send your profile and the job posting to the provider "
    "that writes it — Anthropic, Google or OpenAI — and only when you press the button. It runs on "
    "your own key if you saved one, and otherwise on the platform's paid key, within a small daily "
    "allowance. The files you uploaded never leave this server.\n\n"
    "You can withdraw this at any time by deleting your account, which removes everything we hold. "
    "We cannot recall what a provider has already processed; their retention is governed by their "
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
