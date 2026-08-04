"""Every limit an operator can set must reach something that reads it.

**This class of defect has now been found six times**, each by accident and each in production: a
delivery slot nothing armed, a rubric written where nothing looked, a label budget rendered as a
control and read by nobody, a per-source freshness window the harvest ignored, and — the day this
file was written — six of the ten entries in the limits catalogue.

The shape is always the same and it is invisible from outside: a value exists, the panel displays
it, an operator changes it, and the thing it names goes on using a constant beside it. Nothing
raises. The setting looks answered, which is worse than it looking absent.

So this asserts the property directly rather than testing any one limit: for every key in the
catalogue, somewhere in `libs/` or `services/` resolves it. A limit added without a consumer fails
here, at the time it is added, rather than in six weeks when somebody notices the slider does
nothing.
"""
from __future__ import annotations

from pathlib import Path

from libs.limits import LIMITS

ROOT = Path(__file__).resolve().parents[1]
# The catalogue itself resolves every key generically in `all_for`; that is not a consumer.
SEARCHED = [ROOT / "libs", ROOT / "services"]
EXCLUDED = {ROOT / "libs" / "limits" / "__init__.py"}


def _sources() -> list[Path]:
    return [path for root in SEARCHED for path in root.rglob("*.py")
            if path not in EXCLUDED and "migrations" not in path.parts]


def test_every_operator_limit_is_read_by_something():
    """A control attached to nothing is a lie the panel tells every time it renders."""
    bodies = {path: path.read_text(encoding="utf-8") for path in _sources()}

    unread = []
    for limit in LIMITS:
        # A `resolve(...)` call naming this key, matched per line rather than with a paren-balanced
        # expression — the first argument is often a call itself, `resolve(db.get(User, id), key)`.
        quoted = (f'"{limit.key}"', f"'{limit.key}'")
        found = any(
            any("resolve(" in line and any(q in line for q in quoted)
                for line in body.splitlines())
            for body in bodies.values())
        if not found:
            unread.append(limit.key)

    assert not unread, (
        f"these limits are rendered in the admin panel and read by nothing: {unread}. "
        "Either give each one a consumer or take it out of the catalogue — a control that "
        "governs nothing is worse than an absent one, because it looks answered.")


def test_the_linkedin_interval_floor_cannot_silently_rewrite_the_operators_value():
    """**Wiring a control is not enough if a constant outranks it.**

    `linkedin_interval_min` defaults to 15 in the catalogue, and the scheduler floored every value at
    60 — so an operator setting the documented default got four times it, silently. Fixing the
    plumbing without moving the floor would have left the behaviour exactly as it was.
    """
    from libs.limits import BY_KEY
    from services.harvester.governor import LINKEDIN_MIN_INTERVAL_MIN, resolve_linkedin_interval

    documented = BY_KEY["linkedin_interval_min"].default
    assert documented is not None
    assert documented >= LINKEDIN_MIN_INTERVAL_MIN, (
        "the floor is above the catalogue's own default, so the panel's default is unreachable")
    assert resolve_linkedin_interval(documented) == documented
