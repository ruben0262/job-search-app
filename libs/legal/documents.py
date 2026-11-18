"""The four legal pages — `terms.md`, `privacy.md`, `cookies.md` and `data.md` beside this file — with every
name, period and limit filled in from where it is decided: the operator's details and the site's settings in
Admin, and the constants the code keeps. So no page can state a period the code does not keep, or a name
nobody set.

The files are Markdown with `{name}` gaps (`facts`); a gap this module does not fill fails `render`, and
`tests/test_legal.py` renders every page. A lawyer edits the files; the numbers stay the code's."""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from pymongo.database import Database

from libs import settings
from libs.auth.sessions import ACCESS_TTL, REFRESH_TTL
from libs.auth.tokens import RESET_TTL, VERIFICATION_TTL
from libs.db import BACKUP_KEEP_DAYS
from libs.db.models.contact import CONTACT_KEEP_YEARS
from libs.legal import LEGAL_VERSION
from libs.legal.disclosure import RECIPIENTS, STORED, span
from libs.notify import site_url

HERE = Path(__file__).parent

# Each page's address on the site (`#/terms` …) and its title.
DOCUMENTS = {
    "terms": "Terms of Service",
    "privacy": "Privacy Policy",
    "cookies": "Cookie Policy",
    "data": "Data Use Policy",
}

# How long the database server's own encrypted copies reach back: its restic policy keeps one a month for
# twelve months (the server_db project's `bin/sdb`, `--keep-monthly 12`). In that project, not this one, so
# said here and changed with it: a deleted account lingers in a copy this long.
DATABASE_COPY_MONTHS = 12

# What a detail the operator has not filled in yet reads as, where the law wants it said.
NOT_SET = "[not filled in yet]"


def render(db: Database, name: str) -> dict:
    """One page, filled in: `name` is a key of `DOCUMENTS`."""
    if name not in DOCUMENTS:
        raise KeyError(f"no legal page {name!r}; known: {sorted(DOCUMENTS)}")
    text = (HERE / f"{name}.md").read_text(encoding="utf-8")
    return {"name": name, "title": DOCUMENTS[name], "version": LEGAL_VERSION,
            "markdown": text.format_map(facts(db))}


def facts(db: Database) -> dict[str, str]:
    """Every gap the pages may hold, by name."""
    stored = settings.snapshot(db)
    operator = {**settings.DEFAULT_OPERATOR, **(settings.get(stored, "operator") or {})}
    updated = date.fromisoformat(LEGAL_VERSION)

    def hours(key: str) -> str:
        return span(timedelta(hours=int(settings.get(stored, key))))

    return {
        **{f"operator_{field}": str(operator.get(field) or "").strip() or NOT_SET
           for field in settings.OPERATOR_FIELDS},
        "email": str(settings.get(stored, "support_email")),
        "site": site_url(),
        "version": LEGAL_VERSION,
        "updated": f"{updated.day} {updated:%B %Y}",
        "signin_span": span(ACCESS_TTL),
        "session_span": span(REFRESH_TTL),
        "verify_span": span(VERIFICATION_TTL),
        "reset_span": span(RESET_TTL),
        "backup_days": str(BACKUP_KEEP_DAYS),
        "copy_months": str(DATABASE_COPY_MONTHS),
        "contact_years": str(CONTACT_KEEP_YEARS),
        "document_keep": hours("document_keep_hours"),
        "shelf": hours("job_max_age_hours"),
        "free_wait": hours("free_delay_hours"),
        "starter_wait": hours("starter_delay_hours"),
        "trial_days": str(settings.get(stored, "trial_days")),
        "recipients": _table(("Who", "What for", "What they receive", "Where", "On whose terms"),
                             [(r.name, r.purpose, r.data, r.location, r.terms) for r in RECIPIENTS]),
        "stored": _table(("Name", "Kind", "Set by", "What it is for", "How long"),
                         [(s.name, s.kind, s.by, s.purpose, s.kept) for s in STORED]),
    }


def _table(head: tuple[str, ...], rows: list[tuple[str, ...]]) -> str:
    """A Markdown table."""
    lines = [head, tuple("---" for _ in head), *rows]
    return "\n".join("| " + " | ".join(cell.replace("|", "/") for cell in line) + " |" for line in lines)
