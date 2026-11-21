"""A person's Discord account, linked from the site (owner, 2026-11-19: "link their discord first through the
app, and then they can join the group after, so we can assign proper roles for them").

Discord's own page asks them, for the `identify` scope alone: their account's id and name, nothing of their
servers, friends or messages. The id goes onto their Stripe customer and running subscription
(`billing_routes.tell_discord`), where RoleLogic, the bot in the owner's server, reads it and gives the plan's
role, Starter or Member, when they are in the server or as they join it. Nothing of Discord's loads on the
site: the browser goes to Discord's page and Discord sends it back here, with a `state` only this site signs.
"""
from __future__ import annotations

import logging
import os
import uuid as _uuid
from datetime import timedelta
from urllib.parse import urlencode

import httpx
import stripe
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from pymongo.database import Database

from libs.auth.access import InvalidAccessToken, sign_for, signed_by
from libs.db import find, get
from libs.db.models import User
from libs.notify import site_url
from libs.obs import audit
from services.api.billing_routes import tell_discord
from services.api.deps import current_user, get_db, rate_limit

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/discord", tags=["discord"])

AUTHORIZE = "https://discord.com/oauth2/authorize"
DISCORD_API = "https://discord.com/api/v10"
# The Discord application's id and secret, made by the operator in Discord's developer portal
# (deploy/server.env.example). Unset, the site offers only to join the server, and says why in the log.
CLIENT_ENV = ("DISCORD_CLIENT_ID", "DISCORD_CLIENT_SECRET")
# How long Discord's page may stay open before the return from it is refused.
LINK_TTL = timedelta(minutes=10)
DISCORD_TIMEOUT_SEC = 10
PURPOSE = "discord-link"


def _client() -> httpx.Client:
    return httpx.Client(timeout=DISCORD_TIMEOUT_SEC)


def _app() -> tuple[str, str] | None:
    client_id, secret = (os.environ.get(name, "").strip() for name in CLIENT_ENV)
    return (client_id, secret) if client_id and secret else None


def _callback() -> str:
    """Where Discord sends them back, as registered in the Discord application (deploy/server.env.example)."""
    return site_url("/discord/callback")


class DiscordLink(BaseModel):
    # Whether linking is set up on this site (`CLIENT_ENV`).
    available: bool
    # The Discord account linked, as Discord names it; None while there is none.
    name: str | None


class LinkPage(BaseModel):
    url: str


@router.get("")
def discord(db: Database = Depends(get_db), user_id: _uuid.UUID = Depends(current_user)) -> DiscordLink:
    """Whether they can link a Discord account here, and the one they linked."""
    user = get(db, User, user_id)
    return DiscordLink(available=_app() is not None, name=user.discord_name if user.discord_id else None)


@router.post("/link")
def link(request: Request, user_id: _uuid.UUID = Depends(current_user)) -> LinkPage:
    """Discord's page asking them to link their account, for the browser to open."""
    rate_limit(request, f"discord:{user_id}", limit=20, per_seconds=3600)
    app = _app()
    if app is None:
        logger.error("discord: %s is not set, so nobody can link a Discord account", " or ".join(CLIENT_ENV))
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="Linking Discord isn't set up yet. Try again later.")
    # `prompt=none`: somebody who said yes before is sent straight back.
    return LinkPage(url=f"{AUTHORIZE}?" + urlencode({
        "client_id": app[0], "response_type": "code", "scope": "identify", "prompt": "none",
        "redirect_uri": _callback(), "state": sign_for(user_id, PURPOSE, LINK_TTL)}))


@router.get("/callback", response_class=RedirectResponse, status_code=status.HTTP_303_SEE_OTHER)
def callback(request: Request, code: str | None = None, state: str | None = None, error: str | None = None,
             db: Database = Depends(get_db)) -> RedirectResponse:
    """Discord sending them back: their account linked, and them on Settings, told how it went. The account is
    the one the `state` names, signed here when they asked, and the browser coming back is signed in as it.
    The state alone was enough, so a link one person asked for and sent to another, opened there, moved that
    person's Discord account onto the first one's and took their plan's role with it (2026-11-20)."""
    try:
        user_id = signed_by(state or "", PURPOSE)
    except InvalidAccessToken as exc:
        logger.warning("discord: a return from Discord whose state does not check out (%s)", exc)
        return _back("failed")
    try:
        back_as = current_user(request, db)
    except HTTPException:
        back_as = None
    if back_as != user_id:
        logger.warning("discord: a return for %s in a browser signed in as %s; nothing linked", user_id,
                       back_as)
        return _back("failed")
    if error or not code:
        logger.info("discord: %s did not link an account (%s)", user_id, error or "no code")
        return _back("cancelled")
    app, user = _app(), get(db, User, user_id)
    if app is None or user is None:
        logger.warning("discord: a return for %s, %s", user_id, "with linking not set up" if app is None
                       else "an account that is gone")
        return _back("failed")
    try:
        account = _account(app, code)
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        logger.warning("discord: %s's Discord account could not be read: %s", user_id, exc)
        return _back("failed")
    _keep(db, user, str(account["id"]), account.get("global_name") or account["username"])
    return _back("linked")


@router.delete("")
def unlink(db: Database = Depends(get_db), user_id: _uuid.UUID = Depends(current_user)) -> DiscordLink:
    """Their Discord account let go, here and at Stripe: RoleLogic takes the plan's role from it."""
    _keep(db, get(db, User, user_id), None, None)
    return discord(db, user_id)


def _account(app: tuple[str, str], code: str) -> dict:
    """The Discord account that said yes: a token asked for with the code, then the account read with it."""
    with _client() as http:
        token = http.post(f"{DISCORD_API}/oauth2/token", auth=app, data={
            "grant_type": "authorization_code", "code": code, "redirect_uri": _callback()})
        token.raise_for_status()
        me = http.get(f"{DISCORD_API}/users/@me",
                      headers={"Authorization": f"Bearer {token.json()['access_token']}"})
        me.raise_for_status()
        return me.json()


def _keep(db: Database, user: User, discord_id: str | None, name: str | None) -> None:
    """Their Discord account, or none, on their account and at Stripe. A Discord account is one person's: an
    account that had it lets it go, so a plan's role does not come with it."""
    if discord_id:
        for other in find(db, User, {"discord_id": discord_id, "_id": {"$ne": user.id}}):
            logger.info("discord: %s's Discord account is %s's now", other.id, user.id)
            _keep(db, other, None, None)
    db[User.COLLECTION].update_one({"_id": user.id},
                                   {"$set": {"discord_id": discord_id, "discord_name": name}})
    try:
        tell_discord(get(db, User, user.id))
    except (stripe.StripeError, HTTPException) as exc:
        # Kept here all the same: their subscription is told at Stripe's next word on it (`sync`).
        logger.warning("discord: Stripe was not told %s's Discord account (%s)", user.id, exc)
    audit(logger, "discord.linked" if discord_id else "discord.unlinked", user=user.id)


def _back(said: str) -> RedirectResponse:
    """Back to Settings, with how it went in the address for the page to say (`web/src/discord.tsx`)."""
    return RedirectResponse(site_url(f"/settings?discord={said}"), status_code=status.HTTP_303_SEE_OTHER)
