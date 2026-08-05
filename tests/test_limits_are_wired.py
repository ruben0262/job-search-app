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


def test_no_limit_is_read_with_an_or_fallback():
    """**`None` means Unlimited, and `resolve(...) or DEFAULT` turns that choice back into a cap.**

    Reported from the panel: an account set to *Unlimited* jobs per delivery run was still receiving
    ten. `resolve` already returns the catalogue default for a limit nobody has set, so a `None` it
    returns is only ever the operator having explicitly chosen no ceiling — and `or` cannot tell
    that apart from zero or from unset.

    The one exception is `send_gap_seconds`, which forbids unlimited because the rule is Telegram's
    rather than ours, so its `or` can never fire.
    """
    offenders = []
    for path, body in ((p, p.read_text(encoding="utf-8")) for p in _sources()):
        lines = body.splitlines()
        for number, line in enumerate(lines, start=1):
            stripped = line.strip()
            # Comments explaining the rule are not violations of it.
            if stripped.startswith("#"):
                continue
            if "resolve(" not in line or stripped.startswith("def "):
                continue
            if "send_gap_seconds" in line:
                continue
            # **Two lines, because a wrapped call hid one of these.** `resolve(...)` on one line and
            # `or DEFAULT` on the next is the same defect and reads as innocent.
            window = " ".join(
                part.split("#")[0] for part in lines[number - 1:number + 1])
            if " or " in window:
                offenders.append(f"{path.relative_to(ROOT)}:{number}: {stripped}")

    assert not offenders, (
        "a limit read with an `or` fallback cannot express Unlimited:\n  " + "\n  ".join(offenders))


def test_a_limit_that_cannot_be_unlimited_says_so():
    """A dropdown offering Unlimited for a value that has no unlimited meaning invites somebody to
    switch off their own feed. `linkedin_interval_min` is minutes between runs; unlimited is not a
    slower cadence, it is never."""
    from libs.limits import BY_KEY

    assert BY_KEY["linkedin_interval_min"].allow_unlimited is False
    assert BY_KEY["send_gap_seconds"].allow_unlimited is False
    # And the ones where unlimited is a real, useful answer keep it.
    assert BY_KEY["notifications_per_day"].allow_unlimited is True
    assert BY_KEY["items_per_run"].allow_unlimited is True


def test_unlimited_is_honoured_wherever_it_is_offered():
    """**A dropdown option that silently means something else is worse than no option.**

    Reported from the panel: an account set to Unlimited jobs per delivery run was still receiving
    ten. The rule this pins is the general one — a limit whose catalogue says unlimited is available
    must have a consumer that acts on `None`, and a limit whose consumer cannot express unlimited
    must not offer it.
    """
    from libs.limits import LIMITS

    bodies = {path: path.read_text(encoding="utf-8") for path in _sources()}

    unhonoured = []
    for limit in LIMITS:
        if not limit.allow_unlimited:
            continue
        quoted = (f'"{limit.key}"', f"'{limit.key}'")
        for body in bodies.values():
            lines = body.splitlines()
            for number, line in enumerate(lines):
                if "resolve(" not in line or not any(q in line for q in quoted):
                    continue
                # The consumer has to look at `None` within a few lines of reading it — either on
                # the same line or in the branch under it.
                window = "\n".join(lines[number:number + 6])
                if "None" not in window and "is not None" not in window:
                    unhonoured.append(f"{limit.key}: {line.strip()}")

    assert not unhonoured, (
        "these limits offer Unlimited and their consumer never checks for it:\n  "
        + "\n  ".join(unhonoured))


def test_only_one_place_answers_how_many_jobs_a_day():
    """**Four answers, and the page showed a fifth of the truth.**

    `notifications_per_day` had definitions in four places: the user config schema
    (`max_notifications_per_day`, default 20), a mirrored constant in `status_routes` (100), the
    plan quota in `libs.tenant.quotas` (100), and `libs.limits` (None — unlimited). The notifier
    enforces the last. So the dashboard told an account "at most 20 messages a day" on a day it had
    delivered 908, and the status page was ready to claim the allowance was spent at 100
    (ROADMAP 6af).

    This pins the fix rather than the wording: the two pages that report the cap read it from the
    same place the notifier does, and the mirrored constant is gone.
    """
    from pathlib import Path

    dashboard = Path("services/api/dashboard_routes.py").read_text(encoding="utf-8")
    status = Path("services/api/status_routes.py").read_text(encoding="utf-8")

    # Definitions and reads, not prose — the docstrings below name the old mechanisms deliberately,
    # so the assertion has to look for the code rather than the words.
    assert "DEFAULT_NOTIFICATIONS_PER_DAY = " not in dashboard + status, (
        "the mirrored constant is back; libs.limits is in libs so both services can read it")
    assert 'get("max_notifications_per_day"' not in dashboard + status, (
        "the user config field is not what the notifier enforces, so it must not be reported as "
        "the delivery cap")
    assert 'resolve(user, "notifications_per_day")' in status


def test_unlimited_delivery_is_reported_as_unlimited_not_as_a_number():
    """`None` means no cap. Every place that has quietly substituted a number for it has understated
    what the platform does, and one of them said so on the user's own home page."""
    from libs import limits
    from libs.db.models import User
    from services.api.status_routes import daily_cap

    user = User(email="x@example.com", password_hash="x", limits={})
    assert daily_cap(user) is None, "the catalogue default is unlimited"

    user.limits = {"notifications_per_day": 40}
    assert daily_cap(user) == 40

    # And the catalogue itself still offers unlimited, which is what makes `None` reachable.
    assert next(item for item in limits.LIMITS
                if item.key == "notifications_per_day").allow_unlimited
