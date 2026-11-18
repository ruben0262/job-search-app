"""Whether a person has agreed to the legal pages as they read now: the Terms of Service, and the Privacy,
Cookie and Data Use policies.

One agreement, given at sign-up with the box or on the one screen that asks an account made before the current
version (`services/api/dashboard_routes.py`), and nothing of the site past that screen until it is given.
Asked again wherever a person's text could leave the server (`services.docgen`), which cannot import the
API — so it lives in `libs`, and in a module that reaches nothing heavier than the database: the docgen image
installs no `argon2`, which `libs.auth` needs, so the recipients and the pages (`disclosure`, `documents`) are
apart."""
from __future__ import annotations

import uuid as _uuid

from pymongo.database import Database

from libs.db import get
from libs.db.models import User

# The version of the legal pages — the Terms of Service and the Privacy, Cookie and Data Use policies — that a
# person agrees to: at sign-up, with the box (`services/api/auth_routes.py`), or, for an account made before
# it, on the one screen that asks (`services/api/dashboard_routes.py`, stage "agree"). Date-stamped, so a
# stored agreement says when it was given and to what. A new one asks everybody again, and the Terms promise
# 30 days' notice by email first (section 19), so it changes with a change people must agree to — the Terms'
# substance, a new recipient of their data — and not for a typo.
# - 2026-10-28: Google and OpenAI added; Anthropic's purpose documents only.
# - 2026-11-01: the site's one proxy. 2026-11-11: Stripe.
# - 2026-11-12: the cookies, agreed to at sign-up with the rest (owner: "a box to confirm the privacy policy
#   and cookies of the site before signing up").
# - 2026-11-18: the four pages written as a set, and the box agrees to the Terms. Cloudflare named, which
#   carries every visit. The proxy dropped: since 2026-11-04 it reads LinkedIn by country, with nothing of
#   anybody's search. CVs and letters only on the person's own AI account (2026-11-09).
LEGAL_VERSION = "2026-11-18"


def agreed(user: User | None) -> bool:
    """Agreed to the version that reads now. A new version asks everybody again."""
    return bool(user and user.processing_consent_at is not None
                and user.processing_consent_version == LEGAL_VERSION)


def has_consented(db: Database, user_id: _uuid.UUID) -> bool:
    """`agreed`, for a caller holding only the account's id."""
    return agreed(get(db, User, user_id))
