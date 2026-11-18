"""The contact form: a message to the people who run the site, from the Help page signed in or from the
landing page signed out (owner, 2026-11-17: "to the same place where we have inside the app").

Kept as a row first and emailed second, so a message survives an email that did not go: the admin
panel lists every one, and says which were emailed. The reply goes to the account's own address, or to
the one a visitor gave.
"""
from __future__ import annotations

import logging
import uuid as _uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field
from pymongo.database import Database

from libs import settings
from libs.db import find, get, insert, now
from libs.db.models import ContactMessage, User
from libs.mail import EmailSender, MailError, send_within_cap
from libs.membership import has
from libs.obs import audit
from services.api.admin_routes import require_admin
from services.api.deps import current_user, get_db, get_mailer, rate_limit

logger = logging.getLogger(__name__)
router = APIRouter(tags=["contact"])


class ContactRequest(BaseModel):
    # One line: it becomes an email subject. The landing page's form asks for none, and the message's first
    # words are the subject instead.
    subject: str | None = Field(default=None, min_length=3, max_length=120, pattern=r"^[^\r\n]+$")
    message: str = Field(min_length=10, max_length=4000)
    # Where to reply to somebody not signed in. A signed-in person's reply goes to their account's address.
    email: EmailStr | None = None


class ContactReceipt(BaseModel):
    # What to quote when writing about it again.
    reference: str
    # Where the reply will go.
    email: str


class ContactAddress(BaseModel):
    email: str
    # The members' chat with the people who build the site (`members_channel_url`), for an account whose plan
    # has it (`team_access`); None for the rest, and for everybody while it has no link.
    channel: str | None = None


class SocialLink(BaseModel):
    # One of `libs.settings.SOCIAL_NETWORKS`, whose name and picture the site draws.
    network: str
    url: str


@router.get("/socials")
def socials(db: Database = Depends(get_db)) -> list[SocialLink]:
    """The site's places on social networks that have a link, for anybody, signed in or not (`socials`)."""
    links = settings.get(db, "socials") or {}
    return [SocialLink(network=network, url=links[network]) for network in settings.SOCIAL_NETWORKS
            if links.get(network)]


@router.get("/contact")
def contact_address(db: Database = Depends(get_db),
                    user_id: _uuid.UUID = Depends(current_user)) -> ContactAddress:
    """Where the form's messages go, for anyone who would rather use their own email app, and the members'
    chat, for whoever has it."""
    channel = settings.get(db, "members_channel_url")
    return ContactAddress(email=settings.get(db, "support_email"),
                          channel=channel if channel and has(db, user_id, "team_access") else None)


@router.post("/contact", status_code=202)
def contact(body: ContactRequest, request: Request, db: Database = Depends(get_db),
            mailer: EmailSender = Depends(get_mailer)) -> ContactReceipt:
    """Send a message to support, signed in or with an address to reply to."""
    try:
        user_id: _uuid.UUID | None = current_user(request)
    except HTTPException:
        # Not signed in: a visitor on the landing page, who says where the reply goes. Without that, the 401.
        if body.email is None:
            raise
        user_id = None
    # An account is counted by itself; a visitor by their address (`rate_limit`), whatever email they type.
    rate_limit(request, f"contact:{user_id or 'visitor'}", limit=5, per_seconds=3600)
    email = get(db, User, user_id).email if user_id else body.email
    message = insert(db, ContactMessage(user_id=user_id, email=email,
                                        subject=(body.subject or " ".join(body.message.split())[:80]).strip(),
                                        message=body.message.strip()))
    reference = f"M-{message.id}"
    # Anybody can type any address, so the inbox is told this one was not checked by a sign-in.
    sender = email if user_id else f"{email} (not signed in, so not checked)"
    try:
        send_within_cap(db, mailer, settings.get(db, "support_email"),
                        f"[Mahoraga {reference}] {message.subject}",
                        f"From: {sender}\nReference: {reference}\n\n{message.message}",
                        purpose="contact", reply_to=email)
        db[ContactMessage.COLLECTION].update_one({"_id": message.id}, {"$set": {"emailed_at": now()}})
    except MailError:
        logger.warning("contact: message %s from %s was kept but not emailed; it is in the admin panel",
                       message.id, user_id or "a visitor", exc_info=True)
    # The person's receipt, by email and at once: Hungary's consumer protection act has a complaint made
    # through a website's form acknowledged so (Fgytv. 17/A. § (6a), since 2025-07-23), and any message may be
    # one. It repeats none of their words: anybody can type anybody's address on the landing page, and a
    # receipt that carried them would let the form send strangers whatever somebody typed.
    try:
        send_within_cap(db, mailer, email, f"We got your message ({reference})",
                        f"Thank you for writing to Mahoraga. We got your message, reference {reference}, "
                        "and a person will answer it within 30 days, usually much sooner.\n\n"
                        "If you didn't write to us, you can ignore this email.",
                        purpose="contact")
    except MailError:
        logger.warning("contact: the receipt for message %s did not go to its sender", message.id,
                       exc_info=True)
    audit(logger, "contact.sent", user=user_id, message=message.id)
    return ContactReceipt(reference=reference, email=email)


class ContactRow(BaseModel):
    reference: str
    email: str
    subject: str
    message: str
    created_at: datetime
    emailed: bool


@router.get("/admin/contact")
def contact_messages(db: Database = Depends(get_db),
                     admin: User = Depends(require_admin)) -> list[ContactRow]:
    """Every contact message, newest first."""
    audit(logger, "admin.contact_read", user=admin.id)
    return [ContactRow(reference=f"M-{row.id}", email=row.email, subject=row.subject, message=row.message,
                       created_at=row.created_at, emailed=row.emailed_at is not None)
            for row in find(db, ContactMessage, {}, sort=[("_id", -1)], limit=200)]
