"""How a person's CVs and cover letters look: the templates, grouped as the picker shows them, the one
of each kind they picked, the colour both are made in, and a picture of each in it (`libs.templates`)."""
from __future__ import annotations

import logging
import uuid as _uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from pymongo.database import Database

from libs import templates
from libs.db import get
from libs.db.models import User
from libs.obs import audit
from services.api.deps import current_user, get_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/templates", tags=["templates"])

Kind = Literal["cv", "cover"]


class TemplateCard(BaseModel):
    id: str
    name: str
    # The look in abstract shapes (`templates.picture`), drawn in the page from this answer: one request
    # for every picture, kept with the rest, so the sheet opens with them already there.
    picture: str


class TemplateGroup(BaseModel):
    # "Most popular", or a sector.
    title: str
    # Ids from `templates`, in the order shown. One template can be in several groups.
    templates: list[str]


class TemplateShelf(BaseModel):
    # The one their documents of this kind are made in.
    chosen: str
    templates: list[TemplateCard]
    groups: list[TemplateGroup]


class TemplateColour(BaseModel):
    id: str
    name: str
    # "#1e3a8a": what its swatch is drawn in.
    hex: str


class Templates(BaseModel):
    cv: TemplateShelf
    cover: TemplateShelf
    # The colour every CV and cover letter is made in, from `colours`; None, each look's own.
    colour: str | None
    colours: list[TemplateColour]


class TemplateChoice(BaseModel):
    id: str


class ColourChoice(BaseModel):
    # None: each look's own colours.
    id: str | None


def _templates(user: User) -> Templates:
    def shelf(kind: str) -> TemplateShelf:
        return TemplateShelf(
            chosen=templates.chosen(user, kind).id,
            # Each in their colour, so the pictures show what they will get.
            templates=[TemplateCard(id=each.id, name=each.name,
                                    picture=templates.picture(templates.coloured(each, user)))
                       for each in templates.of_kind(kind)],
            groups=[TemplateGroup(title=title, templates=ids) for title, ids in templates.groups(kind)])

    return Templates(cv=shelf("cv"), cover=shelf("cover"), colour=templates.colour_of(user),
                     colours=[TemplateColour(id=key, name=name, hex=value)
                              for key, (name, value) in templates.COLOURS.items()])


@router.get("")
def list_templates(db: Database = Depends(get_db),
                   user_id: _uuid.UUID = Depends(current_user)) -> Templates:
    """Every look, the most popular first and then by sector, and the one of each kind they use."""
    return _templates(get(db, User, user_id))


# Before `/{kind}`, which would take "colour" for a kind and refuse it.
@router.put("/colour")
def choose_colour(body: ColourChoice, db: Database = Depends(get_db),
                  user_id: _uuid.UUID = Depends(current_user)) -> Templates:
    """Every CV and cover letter made from now on is made in this colour; None, in each look's own."""
    if body.id is not None and body.id not in templates.COLOURS:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"there is no colour called {body.id!r}")
    db[User.COLLECTION].update_one({"_id": user_id}, {"$set": {"look_colour": body.id}})
    audit(logger, "templates.colour", user=user_id, colour=body.id)
    return _templates(get(db, User, user_id))


@router.put("/{kind}")
def choose_template(kind: Kind, body: TemplateChoice, db: Database = Depends(get_db),
                    user_id: _uuid.UUID = Depends(current_user)) -> Templates:
    """Every document of this kind made from now on is made in this look."""
    if templates.find(kind, body.id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND,
                            detail=f"there is no {kind} look called {body.id!r}")
    db[User.COLLECTION].update_one({"_id": user_id}, {"$set": {templates.FIELD[kind]: body.id}})
    audit(logger, "templates.chosen", user=user_id, kind=kind, template=body.id)
    return _templates(get(db, User, user_id))

