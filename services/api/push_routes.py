"""Browser notifications (Web Push): the key a browser subscribes with, and the subscription itself."""
from __future__ import annotations

import logging
import os
import uuid as _uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from pymongo.database import Database

from libs.db import now
from libs.db.models import PushSubscription
from libs.obs import audit
from services.api.deps import current_user, get_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/push", tags=["push"])


class PushKey(BaseModel):
    key: str


class PushKeys(BaseModel):
    p256dh: str = Field(min_length=1, max_length=256)
    auth: str = Field(min_length=1, max_length=64)


class PushSubscriptionBody(BaseModel):
    """What the browser's `PushSubscription.toJSON()` returns, posted as it is."""

    # The notifier posts to this address, so only an https one is taken.
    endpoint: str = Field(min_length=12, max_length=2048, pattern=r"^https://")
    keys: PushKeys


class PushEndpoint(BaseModel):
    endpoint: str = Field(min_length=12, max_length=2048)


class PushStatus(BaseModel):
    status: str


@router.get("/key")
def key() -> PushKey:
    """The public half of the platform's VAPID key pair: what a browser subscribes with."""
    public = os.environ.get("VAPID_PUBLIC_KEY", "")
    if not public:
        logger.warning("push: VAPID_PUBLIC_KEY is not set, so no browser can subscribe")
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="Notifications are not set up on this site yet.")
    return PushKey(key=public)


@router.post("/subscribe")
def subscribe(body: PushSubscriptionBody, db: Database = Depends(get_db),
              user_id: _uuid.UUID = Depends(current_user)) -> PushStatus:
    """Remember this browser. Subscribing it again replaces its row, whichever account it was on."""
    db[PushSubscription.COLLECTION].update_one(
        {"endpoint": body.endpoint},
        {"$set": {"user_id": user_id, "p256dh": body.keys.p256dh, "auth": body.keys.auth},
         "$setOnInsert": {"created_at": now()}}, upsert=True)
    audit(logger, "push.subscribed", user=user_id)
    return PushStatus(status="subscribed")


@router.delete("/subscribe")
def unsubscribe(body: PushEndpoint, db: Database = Depends(get_db),
                user_id: _uuid.UUID = Depends(current_user)) -> PushStatus:
    """Forget this browser."""
    db[PushSubscription.COLLECTION].delete_one({"endpoint": body.endpoint, "user_id": user_id})
    return PushStatus(status="unsubscribed")
