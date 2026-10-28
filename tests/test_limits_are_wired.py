"""Every limit an operator can set must reach something that reads it."""
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
        # A `resolve(...)` call naming this key.
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
    """Wiring a control is not enough if a constant outranks it."""
    from libs.limits import BY_KEY
    from services.harvester.governor import LINKEDIN_MIN_INTERVAL_MIN, resolve_linkedin_interval

    documented = BY_KEY["linkedin_interval_min"].default
    assert documented is not None
    assert documented >= LINKEDIN_MIN_INTERVAL_MIN, (
        "the floor is above the catalogue's own default, so the panel's default is unreachable")
    assert resolve_linkedin_interval(documented) == documented


def test_no_limit_is_read_with_an_or_fallback():
    """`None` means Unlimited, and `resolve(...) or DEFAULT` turns that choice back into a cap."""
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
            # Two lines, because a wrapped call hid one of these.
            window = " ".join(
                part.split("#")[0] for part in lines[number - 1:number + 1])
            if " or " in window:
                offenders.append(f"{path.relative_to(ROOT)}:{number}: {stripped}")

    assert not offenders, (
        "a limit read with an `or` fallback cannot express Unlimited:\n  " + "\n  ".join(offenders))


def test_a_limit_that_cannot_be_unlimited_says_so():
    """A dropdown offering Unlimited for a value that has no unlimited meaning invites somebody to switch."""
    from libs.limits import BY_KEY

    assert BY_KEY["linkedin_interval_min"].allow_unlimited is False
    assert BY_KEY["send_gap_seconds"].allow_unlimited is False
    # And the ones where unlimited is a real, useful answer keep it.
    assert BY_KEY["notifications_per_day"].allow_unlimited is True
    assert BY_KEY["items_per_run"].allow_unlimited is True


def test_unlimited_is_honoured_wherever_it_is_offered():
    """A dropdown option that silently means something else is worse than no option."""
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
                # The consumer has to look at `None` within a few lines of reading it.
                window = "\n".join(lines[number:number + 6])
                if "None" not in window and "is not None" not in window:
                    unhonoured.append(f"{limit.key}: {line.strip()}")

    assert not unhonoured, (
        "these limits offer Unlimited and their consumer never checks for it:\n  "
        + "\n  ".join(unhonoured))


def test_only_one_place_answers_how_many_jobs_a_day():
    """Four answers, and the page showed a fifth of the truth."""
    from pathlib import Path

    dashboard = Path("services/api/dashboard_routes.py").read_text(encoding="utf-8")
    status = Path("services/api/status_routes.py").read_text(encoding="utf-8")

    # Definitions and reads, not prose — the docstrings below name the old mechanisms deliberately.
    assert "DEFAULT_NOTIFICATIONS_PER_DAY = " not in dashboard + status, (
        "the mirrored constant is back; libs.limits is in libs so both services can read it")
    assert 'get("max_notifications_per_day"' not in dashboard + status, (
        "the user config field is not what the notifier enforces, so it must not be reported as "
        "the delivery cap")
    assert 'resolve(user, "notifications_per_day")' in status


def test_unlimited_delivery_is_reported_as_unlimited_not_as_a_number():
    """`None` means no cap."""
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


def test_the_applicant_ceiling_is_the_operators_and_unlimited_shows_everything():
    """The single biggest filter on a feed, and it was a constant nobody could see."""
    from libs.tenant.filters import UserFilters

    # The operator's number is honoured, not the config's.
    assert UserFilters.from_config({}, max_age_hours=24, applicant_cap=50).applicant_cap == 50
    # A config that carries the old field cannot override the operator.
    assert UserFilters.from_config({"applicant_cap": 999}, max_age_hours=24,
                                   applicant_cap=50).applicant_cap == 50
    # Unlimited means show everything: `applicant_glut` reads 0 as "do not drop".
    assert UserFilters.from_config({}, max_age_hours=24, applicant_cap=None).applicant_cap == 0


def test_unlimited_applicants_actually_lets_a_busy_posting_through():
    """The limit has to reach the decision, not just the dataclass."""
    from libs.tenant.filters import UserFilters, applicant_glut

    capped = UserFilters.from_config({}, max_age_hours=24, applicant_cap=200)
    unlimited = UserFilters.from_config({}, max_age_hours=24, applicant_cap=None)

    assert applicant_glut(source="linkedin", applicants=200, cap=capped.applicant_cap) is True
    assert applicant_glut(source="linkedin", applicants=200, cap=unlimited.applicant_cap) is False


def test_the_applicant_cap_default_is_not_written_twice():
    """A constant beside a catalogue entry naming the same thing is the defect this project has shipped."""
    from libs.limits import BY_KEY
    from libs.tenant.filters import CATALOGUE_APPLICANT_CAP as DEFAULT_APPLICANT_CAP

    assert BY_KEY["applicant_cap"].default == DEFAULT_APPLICANT_CAP


def test_the_label_pool_default_is_read_from_the_catalogue_and_not_restated():
    """The cold-start target an operator sets in the panel is the one every caller falls back to."""
    from libs.labels import pool_target
    from libs.limits import BY_KEY

    assert pool_target() == BY_KEY["label_pool"].default


def test_every_limit_in_the_catalogue_has_a_control_an_operator_can_reach():
    """A limit with no choices renders an empty dropdown, and nothing errors.

    This used to compare the catalogue against a `CHOICES` map in `AdminUserPage.tsx` — two lists
    that had to agree, with a test to make them agree, which is the arrangement CLAUDE.md §5 names
    as the defect rather than the fix. The values now live in the catalogue and reach the panel
    through `/admin/limits`, so what needs asserting is that the endpoint actually carries them.
    """
    from libs.limits import LIMITS, describe

    served = {row["key"]: row.get("choices") for row in describe()}

    assert set(served) == {limit.key for limit in LIMITS}
    empty = [key for key, choices in served.items() if not choices]
    assert not empty, (
        f"these limits reach the panel with nothing to pick: {empty}. The panel renders exactly "
        "what this endpoint serves, so an empty list is a control that cannot be operated.")


def test_every_limit_offers_values_an_operator_can_pick():
    """A ceiling with no choices renders a dropdown that cannot be opened.

    The options used to live in a `CHOICES` map inside `AdminUserPage.tsx`. Nothing connected the
    two, so a limit added to the catalogue got a row in the panel, a label, a detail line, a
    working PUT route — and an empty select. Wired end to end and unusable, with no error at any
    layer to say so.
    """
    missing = [limit.key for limit in LIMITS if not limit.choices]

    assert not missing, (
        f"these limits offer nothing to pick: {missing}. Add `choices=` in `libs/limits`, which is "
        "the only place the panel reads them from.")


def test_a_limits_default_is_one_of_the_values_offered():
    """Otherwise the panel opens showing a value the dropdown cannot represent, and the first save
    silently changes a setting the operator was only looking at."""
    wrong = [(limit.key, limit.default, limit.choices) for limit in LIMITS
             if limit.default is not None and limit.default not in limit.choices]

    assert not wrong, f"a default that is not offered: {wrong}"


def test_the_panel_names_no_limit_of_its_own():
    """A panel that renders the catalogue needs to know none of its keys.

    The defect this guards is a second source of truth, and the only readable symptom from Python
    is a limit's *name* appearing in the frontend — whatever the variable around it is called. An
    earlier version of this test looked for `const CHOICES` by name, which would have passed
    happily after a rename and failed on one that changed nothing.
    """
    panel = (ROOT / "web" / "src" / "pages" / "AdminUserPage.tsx").read_text(encoding="utf-8")

    hardcoded = [limit.key for limit in LIMITS if limit.key in panel]

    assert not hardcoded, (
        f"AdminUserPage.tsx names these limits directly: {hardcoded}. It should render whatever "
        "`/admin/limits` serves and know none of them by name, or the two lists drift and the "
        "panel silently offers the wrong options.")
