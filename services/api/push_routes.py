"""Browser notifications (Web Push): the key a browser subscribes with, its subscription, and a test."""
from __future__ import annotations

import logging
import uuid as _uuid

import requests
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from pymongo.database import Database
from pywebpush import WebPushException

from libs.db import find_one, now
from libs.db.models import PushSubscription
from libs.notify.push import private_key, public_key, send
from libs.obs import audit
from libs.secrets import redacting
from services.api.deps import get_db, with_alerts

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
def key(db: Database = Depends(get_db)) -> PushKey:
    """The public half of the platform's key pair: what a browser subscribes with. Made on first asking."""
    return PushKey(key=public_key(db))


@router.post("/subscribe")
def subscribe(body: PushSubscriptionBody, db: Database = Depends(get_db),
              user_id: _uuid.UUID = Depends(with_alerts)) -> PushStatus:
    """Remember this browser. Subscribing it again replaces its row, whichever account it was on."""
    db[PushSubscription.COLLECTION].update_one(
        {"endpoint": body.endpoint},
        {"$set": {"user_id": user_id, "p256dh": body.keys.p256dh, "auth": body.keys.auth},
         "$setOnInsert": {"created_at": now()}}, upsert=True)
    audit(logger, "push.subscribed", user=user_id)
    return PushStatus(status="subscribed")


@router.post("/test")
def test(body: PushEndpoint, db: Database = Depends(get_db),
         user_id: _uuid.UUID = Depends(with_alerts)) -> PushStatus:
    """One notification to this browser, as an alert's goes, to see that it arrives. Says what its push
    service said when it would not take it."""
    browser = find_one(db, PushSubscription, {"endpoint": body.endpoint, "user_id": user_id})
    if browser is None:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            detail="This phone is not signed up yet. Turn notifications off and on again.")
    key = private_key(db)
    try:
        with redacting(key):
            delivered = send(browser, {"title": "Notifications are on",
                                       "body": "New jobs from your alerts will arrive like this.",
                                       "url": "/#/alerts"}, key)
    except (WebPushException, requests.RequestException) as exc:
        code = getattr(exc, "status_code", None)
        logger.warning("push: a test to a browser of %s was refused (%s): %s", user_id, code, exc)
        said = f"This phone's notification service said no ({code or 'no answer'}). Try again in a minute."
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=said) from exc
    if not delivered:
        db[PushSubscription.COLLECTION].delete_one({"endpoint": body.endpoint})
        raise HTTPException(status.HTTP_410_GONE,
                            detail="This phone stopped taking notifications. Turn them off and on again.")
    audit(logger, "push.tested", user=user_id)
    return PushStatus(status="sent")
