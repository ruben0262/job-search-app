"""Message bodies: each email as plain text, and as HTML in the site's look (owner, 2026-11-12:
"improve on email designs"). The text is what a client without HTML shows, and what a reader who
prefers it gets.

The HTML is laid out the way email clients read it: tables, every style inline, no pictures to be
blocked and no script. Every link is shown in full under its button, as the text always did, so it
can still be copied, and a reader can see where it goes before following it.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from html import escape

VERIFY_SUBJECT = "Confirm your email"
RESET_SUBJECT = "Reset your password"

BRAND = "Mahoraga"
# The site's purple (`web/src/styles.css`, `--accent`), white on it 5:1.
ACCENT = "#8b3dff"
INK = "#0f1013"
MUTED = "#5b5d66"
LINE = "#e7e7ec"
EMAIL_FONT = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif"
_FOOTER = f"{BRAND} · The job search that runs itself"


def _lasts(ttl: timedelta) -> str:
    """"24 hours", "an hour": how long a link works, from the token's own lifetime (`libs.auth.tokens`), which
    the caller passes: imported here, `libs.auth` brought its password hashing into the notifier's image,
    which sends the daily email and does not install it."""
    hours = round(ttl.total_seconds() / 3600)
    return "an hour" if hours == 1 else f"{hours} hours"


# ── the layout ──────────────────────────────────────────────────────────────────────────────────

def _button(label: str, url: str) -> str:
    return (f'<table role="presentation" cellpadding="0" cellspacing="0" style="margin:28px 0 8px">'
            f'<tr><td style="border-radius:999px;background:{ACCENT}">'
            f'<a href="{escape(url)}" style="display:inline-block;padding:14px 28px;border-radius:999px;'
            f'color:#ffffff;font:600 16px/1 {EMAIL_FONT};text-decoration:none">{escape(label)}</a>'
            f"</td></tr></table>")


def _link_in_full(url: str) -> str:
    return (f'<p style="margin:16px 0 0;color:{MUTED};font:13px/1.5 {EMAIL_FONT}">Or open this link:<br>'
            f'<a href="{escape(url)}" style="color:{ACCENT};word-break:break-all">{escape(url)}</a></p>')


def _para(text: str) -> str:
    return f'<p style="margin:0 0 12px;color:{INK};font:16px/1.6 {EMAIL_FONT}">{text}</p>'


def _small(text: str) -> str:
    return (f'<p style="margin:20px 0 0;padding-top:18px;border-top:1px solid {LINE};color:{MUTED};'
            f'font:13px/1.6 {EMAIL_FONT}">{text}</p>')


def _page(*, preview: str, heading: str, body: str, footer: str) -> str:
    """One email: the name at the top, a white card with the heading and what it says, a small footer."""
    dot = (f'<span style="display:inline-block;width:12px;height:12px;margin-right:8px;border-radius:50%;'
           f'background:{ACCENT}"></span>')
    title = (f'<h1 style="margin:0 0 14px;color:{INK};font:700 26px/1.2 {EMAIL_FONT};letter-spacing:-0.02em">'
             f"{escape(heading)}</h1>")
    foot = f'style="padding:20px 8px 0;color:{MUTED};font:12px/1.6 {EMAIL_FONT};text-align:center"'
    return f"""\
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light">
<title>{escape(heading)}</title>
</head>
<body style="margin:0;padding:0;background:#f4f4f7">
<div style="display:none;max-height:0;overflow:hidden;opacity:0">{escape(preview)}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f4f4f7">
<tr><td align="center" style="padding:32px 16px">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:560px">
<tr><td style="padding:0 4px 18px;font:700 18px/1 {EMAIL_FONT};color:{INK}">{dot}{BRAND}</td></tr>
<tr><td style="padding:36px 32px;border:1px solid {LINE};border-radius:20px;background:#ffffff">
{title}
{body}
</td></tr>
<tr><td {foot}>{footer}</td></tr>
</table>
</td></tr>
</table>
</body>
</html>
"""


# ── the emails ──────────────────────────────────────────────────────────────────────────────────

def verification_email(url: str, ttl: timedelta) -> tuple[str, str, str]:
    """Subject, text and HTML of the email that confirms a new account's address, its link working `ttl`."""
    lasts = _lasts(ttl)
    text = f"""\
Hi,

Confirm this address to finish setting up your {BRAND} account:

  {url}

The link works once and expires in {lasts}.

If you did not sign up, ignore this — nothing was created that you need to undo.
"""
    html = _page(
        preview=f"One tap to finish setting up your {BRAND} account.",
        heading="Confirm your email",
        body=(_para(f"Tap the button to confirm this address and finish setting up your {BRAND} account.")
              + _button("Confirm my email", url) + _link_in_full(url)
              + _small(f"The link works once and expires in {lasts}. If you didn't sign up, ignore this "
                       "email: nothing was created that you need to undo.")),
        footer=_FOOTER)
    return VERIFY_SUBJECT, text, html


def reset_email(url: str, ttl: timedelta) -> tuple[str, str, str]:
    """Subject, text and HTML of the email that resets a password, its link working `ttl`."""
    lasts = _lasts(ttl)
    text = f"""\
Hi,

Someone asked to reset the password for this account:

  {url}

The link works once and expires in {lasts}.

If that was not you, ignore this. Your password has not changed, and nobody can change it without
this link.
"""
    html = _page(
        preview="Choose a new password for your account.",
        heading="Reset your password",
        body=(_para(f"Someone asked to reset the password for your {BRAND} account. If it was you, "
                    "choose a new one:")
              + _button("Choose a new password", url) + _link_in_full(url)
              + _small(f"The link works once and expires in {lasts}. If it wasn't you, ignore this "
                       "email: your password hasn't changed, and nobody can change it without this link.")),
        footer=_FOOTER)
    return RESET_SUBJECT, text, html


@dataclass(frozen=True)
class DigestJob:
    """One job as the daily email lists it."""
    title: str
    # "Acme · Berlin"
    where: str
    # "LinkedIn"
    board: str
    # The words of theirs it has: why it is in the email.
    words: tuple[str, ...]
    # The job on the site.
    url: str


def _digest_row(job: DigestJob) -> str:
    meta = " · ".join(part for part in (job.where, job.board) if part)
    words = "".join(
        f'<span style="display:inline-block;margin:0 6px 6px 0;padding:3px 10px;border-radius:999px;'
        f'background:#eaf8f0;color:#1f6f4a;font:600 12px/1.5 {EMAIL_FONT}">{escape(word)}</span>'
        for word in job.words[:4])
    return (f'<tr><td style="padding:16px 0;border-top:1px solid {LINE}">'
            f'<a href="{escape(job.url)}" '
            f'style="color:{INK};font:600 16px/1.35 {EMAIL_FONT};text-decoration:none">'
            f"{escape(job.title)}</a>"
            f'<div style="margin-top:4px;color:{MUTED};font:14px/1.5 {EMAIL_FONT}">{escape(meta)}</div>'
            + (f'<div style="margin-top:8px">{words}</div>' if words else "")
            + f'<a href="{escape(job.url)}" style="display:inline-block;margin-top:6px;color:{ACCENT};'
            f'font:600 14px/1.5 {EMAIL_FONT};text-decoration:none">See the job &rarr;</a>'
            + "</td></tr>")


def digest_email(jobs: list[DigestJob], *, total: int, site: str, settings: str) -> tuple[str, str, str]:
    """Subject, text and HTML of the daily email: `jobs` listed, best matches first, out of `total` found
    since the last one, with the rest on the site."""
    plural = "s" if total != 1 else ""
    subject = f"{total} new job{plural} for you"
    more = total - len(jobs)

    lines = [f"{total} new job{plural} match your search:", ""]
    for job in jobs:
        lines += [f"• {job.title}" + (f" — {job.where}" if job.where else ""), f"  {job.url}", ""]
    if more > 0:
        lines.append(f"And {more} more on your jobs page: {site}")
    lines += ["", f"Turn this email off in Settings: {settings}"]

    rows = "".join(_digest_row(job) for job in jobs)
    table = ('<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
             f'style="margin-top:8px">{rows}</table>')
    html = _page(
        preview=f"{total} new job{plural} matching your words, best matches first.",
        heading=subject,
        body=(_para("Found since your last email, best matches first. The green words are yours.") + table
              + _button(f"See all {total} on your jobs page" if more > 0 else "Open your jobs", site)),
        footer=(f"You get this email once a day because it is on. "
                f'<a href="{escape(settings)}" style="color:{MUTED}">Turn it off in Settings</a>.'
                f"<br>{_FOOTER}"))
    return subject, "\n".join(lines), html
