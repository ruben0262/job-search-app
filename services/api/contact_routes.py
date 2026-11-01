"""The Help page's contact form: a message from a signed-in person to the people who run the site.

Kept as a row first and emailed second, so a message survives an email that did not go: the admin
panel lists every one, and says which were emailed. The reply goes to the account's own address.
"""
from __future__ import annotations

import logging
import uuid as _uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from pymongo.database import Database

from libs import settings
from libs.db import find, get, insert, now
from libs.db.models import ContactMessage, User
from libs.mail import EmailSender, MailError, send_within_cap
from libs.obs import audit
from services.api.admin_routes import require_admin
from services.api.deps import current_user, get_db, get_mailer, rate_limit

logger = logging.getLogger(__name__)
router = APIRouter(tags=["contact"])


class ContactRequest(BaseModel):
    # One line: it becomes an email subject.
    subject: str = Field(min_length=3, max_length=120, pattern=r"^[^\r\n]+$")
    message: str = Field(min_length=10, max_length=4000)


class ContactReceipt(BaseModel):
    # What to quote when writing about it again.
    reference: str
    # Where the reply will go.
    email: str


class ContactAddress(BaseModel):
    email: str


@router.get("/contact")
def contact_address(db: Database = Depends(get_db),
                    _user_id: _uuid.UUID = Depends(current_user)) -> ContactAddress:
    """Where the form's messages go, for anyone who would rather use their own email app."""
    return ContactAddress(email=settings.get(db, "support_email"))


@router.post("/contact", status_code=202)
def contact(body: ContactRequest, request: Request, db: Database = Depends(get_db),
            mailer: EmailSender = Depends(get_mailer),
            user_id: _uuid.UUID = Depends(current_user)) -> ContactReceipt:
    """Send a message to support."""
    rate_limit(request, f"contact:{user_id}", limit=5, per_seconds=3600)
    user = get(db, User, user_id)
    message = insert(db, ContactMessage(user_id=user_id, email=user.email, subject=body.subject.strip(),
                                        message=body.message.strip()))
    reference = f"M-{message.id}"
    try:
        send_within_cap(db, mailer, settings.get(db, "support_email"), f"[Mahoraga {reference}] {message.subject}",
                        f"From: {user.email}\nReference: {reference}\n\n{message.message}",
                        purpose="contact", reply_to=user.email)
        db[ContactMessage.COLLECTION].update_one({"_id": message.id}, {"$set": {"emailed_at": now()}})
    except MailError:
        logger.warning("contact: message %s from %s was kept but not emailed; it is in the admin panel",
                       message.id, user_id, exc_info=True)
    audit(logger, "contact.sent", user=user_id, message=message.id)
    return ContactReceipt(reference=reference, email=user.email)


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
