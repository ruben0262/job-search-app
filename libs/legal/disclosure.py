"""Who else sees a user's data, in the words the user is shown."""
from __future__ import annotations

from dataclasses import dataclass

# Date-stamped rather than sequential, so a stored consent says *when* it was given to what.
# 2026-10-28: Google and OpenAI added; Anthropic's purpose is now documents only — profiles are read
# by rules and nothing scores (ROADMAP Phase 8); and a small allowance runs on the platform's own
# key. A new processor asks everyone again.
# 2026-11-01: the proxy is the site's one provider now, not a person's own, and LinkedIn is searched
# for everyone's words through it — so it reaches people it did not reach before.
# 2026-11-11: Stripe, which takes the membership's payments (`services/api/billing_routes.py`).
# 2026-11-12: the cookies, now agreed to with the rest at sign-up (owner: "a box to confirm the privacy policy
# and cookies of the site before signing up").
DISCLOSURE_VERSION = "2026-11-12"


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
        name="Stripe",
        purpose="Taking the payments for your membership, and the page where you change or cancel it.",
        data="Your email address, and the card or other payment details you type into Stripe's own form, "
             "which never reach this site. Nothing else about you: not your CV, your searches or your "
             "documents.",
        on_your_credential=False,
        location="Ireland and the United States",
    ),
    Processor(
        name="Telegram",
        purpose="Delivering your job matches.",
        data="The job titles, companies and links we send you. Your bot token and chat id.",
        on_your_credential=True,
        location="Outside the EU",
    ),
    Processor(
        name="The site's proxy provider",
        purpose="Searching LinkedIn for the jobs your words describe, and reading those postings. "
                "LinkedIn refuses a server's own address, so this site reaches it through one proxy.",
        data="The words, places and kind of work your account searches for. Nothing else about you: "
             "not your name, your email or your CV.",
        on_your_credential=False,
        location="The provider's network of residential addresses",
    ),
)

@dataclass(frozen=True)
class Cookie:
    name: str
    purpose: str
    # How long it stays.
    kept: str


# What the site keeps in a browser, every one of them needed for it to work: nothing measures or advertises.
# Its cookies are the two `services/api/auth_routes.py` sets; the rest is the browser's own storage, and
# Stripe's script, which only the payment form loads (`web/src/membership.tsx`).
COOKIES = (
    Cookie(name="access_token", purpose="Keeps you signed in while you use the site.", kept="15 minutes"),
    Cookie(name="refresh_token", purpose="Signs you in again when the one above runs out. Only ever sent to "
                                         "the address that renews it.", kept="30 days"),
    Cookie(name="theme, in your browser's storage", purpose="Light or dark, as you chose it.",
           kept="Until you clear it"),
    Cookie(name="job-marks, in your browser's storage", purpose="A Skip or Mark as applied pressed while the "
                                                                "connection was lost, until it is sent.",
           kept="Until it is sent"),
    Cookie(name="Stripe's", purpose="Set by Stripe on the payment form alone, to prevent fraud.",
           kept="As Stripe's cookie policy says"),
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
        "cookies": [{"name": c.name, "purpose": c.purpose, "kept": c.kept} for c in COOKIES],
    }
