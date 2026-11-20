"""Who else receives a person's data, and what the site keeps in a browser: the two lists the Privacy Policy
and the Cookie Policy print as tables (`libs.legal.documents`), so the pages and the code name one set."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from libs.auth.sessions import ACCESS_COOKIE, ACCESS_TTL, REFRESH_COOKIE, REFRESH_TTL


@dataclass(frozen=True)
class Recipient:
    name: str
    purpose: str
    data: str
    location: str
    # Whose agreement with them covers it: ours (a processor working for us), or the person's own account.
    terms: str


# What reaches the provider that writes a document (`services.docgen.build.prepare`): the person's own AI
# account, never the platform's.
_SENT_FOR_A_DOCUMENT = ("The text of the documents you uploaded and switched on, such as your CV, cover "
                        "letters and what you wrote about yourself, with whatever they say; your name; the "
                        "languages of the jobs you're looking for; and the job ad. Only when you ask for a "
                        "document.")
_YOUR_AI_ACCOUNT = "Yours: the account or API key you linked, under that company's terms with you"

RECIPIENTS = (
    Recipient(
        name="Cloudflare",
        purpose="Carries every visit to the site: the secure connection, and protection against attacks "
                "and bots. Our server can only be reached through it.",
        data="Everything that passes between your device and the site, including your IP address.",
        location="Worldwide (Cloudflare, Inc., United States)",
        terms="Ours: Cloudflare's data processing agreement",
    ),
    Recipient(
        name="Stripe",
        purpose="Takes the payments for Starter and Member, and runs the page where you change or cancel "
                "them. Stripe also uses payment data to prevent fraud and to meet its own legal duties.",
        data="Your email address, the plan you pick, and the card details you type into Stripe's own form, "
             "which never reach us. If you link your Discord account, its ID, for RoleLogic (below). Nothing "
             "about your searches, your CV or your documents.",
        location="Ireland and the United States",
        terms="Ours for billing; Stripe's own privacy policy for fraud prevention",
    ),
    # The mailbox `SMTP_HOST` names (`libs/mail/sender.py`; `deploy/server.env.example`): change this with it.
    Recipient(
        name="Zoho Mail",
        purpose="Sends our emails: the link to confirm your address or set a new password, the daily email "
                "of new jobs if an alert of yours sends one, and our replies to you.",
        data="Your email address and what the email says.",
        location="European Union",
        terms="Ours: Zoho's data processing agreement",
    ),
    Recipient(
        name="Anthropic (Claude)",
        purpose="Writes a CV or cover letter when you ask for one, if the AI account you linked is Claude.",
        data=_SENT_FOR_A_DOCUMENT,
        location="United States",
        terms=_YOUR_AI_ACCOUNT,
    ),
    Recipient(
        name="OpenAI (ChatGPT)",
        purpose="The same, if the AI account you linked is ChatGPT or an OpenAI key.",
        data=_SENT_FOR_A_DOCUMENT,
        location="United States",
        terms=_YOUR_AI_ACCOUNT,
    ),
    Recipient(
        name="Google (Gemini)",
        purpose="The same, if the AI account you linked is a Gemini key.",
        data=_SENT_FOR_A_DOCUMENT,
        location="United States",
        terms=_YOUR_AI_ACCOUNT,
    ),
    Recipient(
        name="Telegram",
        purpose="Sends your job alerts, if you connect your own Telegram bot.",
        data="Each alert's name and the jobs it found: title, company and link. Your bot's token and your "
             "chat id.",
        location="Outside the EU",
        terms="Yours: your Telegram account",
    ),
    # Linking a Discord account (`services/api/discord_routes.py`; owner, 2026-11-19).
    Recipient(
        name="Discord",
        purpose="Runs our community server, and the page where you link your Discord account to Mahoraga, "
                "if you choose to.",
        data="Nothing from us. When you link your account, Discord tells us its ID and name, after you agree "
             "on Discord's own page.",
        location="United States",
        terms="Yours: your Discord account",
    ),
    # The bot that reads `discord_user_id` from Stripe (`billing_routes.DISCORD_KEY`). Its operator states no
    # company or location (checked 2026-11-19): the owner's to confirm, with its terms (ROADMAP Phase 12).
    Recipient(
        name="RoleLogic",
        purpose="A bot in our Discord server that gives you your plan's role there (Starter or Member), if "
                "you link your Discord account.",
        data="Your Discord account's ID and the state of your plan, which it reads from Stripe.",
        location="Not stated by its operator",
        terms="Ours",
    ),
    Recipient(
        name="Your browser's push service (Google, Apple, Mozilla or Microsoft)",
        purpose="Delivers notifications to your phone or computer, if you turn them on.",
        data="Each notification, encrypted so that only your browser can read it, and the push address your "
             "browser gave us.",
        location="United States",
        terms="Yours: your browser's",
    ),
)


@dataclass(frozen=True)
class Stored:
    name: str
    kind: str
    by: str
    purpose: str
    # How long it stays.
    kept: str


def span(length: timedelta) -> str:
    """'15 minutes', '1 hour', '24 hours', '30 days': how long something lasts, said."""
    minutes = int(length.total_seconds() // 60)
    if minutes >= 2 * 1440 and minutes % 1440 == 0:
        return f"{minutes // 1440} days"
    if minutes % 60 == 0:
        return "1 hour" if minutes == 60 else f"{minutes // 60} hours"
    return f"{minutes} minutes"


# Everything the site keeps on a device, each one needed for something the person asked for: nothing measures
# them or shows them adverts. `tests/test_legal.py` fails when the API sets a cookie, or the site writes a key
# to the browser's storage, that this list does not name.
STORED = (
    Stored(name=ACCESS_COOKIE, kind="Cookie", by="Mahoraga",
           purpose="Keeps you signed in while you use the site.", kept=span(ACCESS_TTL)),
    Stored(name=REFRESH_COOKIE, kind="Cookie", by="Mahoraga",
           purpose="Signs you in again when the one above runs out. Only ever sent to the one address that "
                   "renews it.",
           kept=span(REFRESH_TTL)),
    Stored(name="theme", kind="Browser storage", by="Mahoraga", purpose="Light or dark, as you chose it.",
           kept="Until you clear it"),
    Stored(name="job-marks", kind="Browser storage", by="Mahoraga",
           purpose="A Skip or Mark as applied you pressed while the connection was lost, until it reaches "
                   "us.",
           kept="Until it is sent"),
    Stored(name="signed-in", kind="Browser storage", by="Mahoraga",
           purpose="That this browser is signed in, and since when, so the site opens on the right page at "
                   "once and your other tabs follow when you sign in or out.",
           kept="Until you sign out or clear it"),
    Stored(name="install-later", kind="Browser storage (this tab)", by="Mahoraga",
           purpose="That you tapped Later on Get the app, so this visit does not ask again.",
           kept="Until you close the tab"),
    Stored(name="sw.js", kind="Service worker", by="Mahoraga",
           purpose="Only if you turn on notifications: the small script your browser keeps to show our job "
                   "alerts.",
           kept="Until you turn notifications off or clear the site's data"),
    # Stripe's and Cloudflare's, as their own cookie pages list them (checked 2026-11-18).
    Stored(name="__stripe_mid, __stripe_sid, m", kind="Cookie", by="Stripe",
           purpose="Set by Stripe's payment form, on the page where you pick a plan and pay, to prevent "
                   "fraud.",
           kept="1 year; 30 minutes; 2 years"),
    Stored(name="__cf_bm, cf_clearance, _cfuvid", kind="Cookie", by="Cloudflare",
           purpose="May be set by Cloudflare, which carries the site's traffic, to tell people from bots, to "
                   "remember that a check was passed, and to tell apart people who share an internet "
                   "address.",
           kept="30 minutes; 30 minutes; until you close the browser"),
)
