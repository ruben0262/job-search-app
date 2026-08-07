"""Every platform setting must reach something that reads it, and something an operator can turn."""
from __future__ import annotations

import re
from pathlib import Path

from libs.settings import DEFAULTS

ROOT = Path(__file__).resolve().parents[1]
SEARCHED = [ROOT / "libs", ROOT / "services"]
# The catalogue itself resolves every key generically in `all_settings`; that is not a consumer.
EXCLUDED = {ROOT / "libs" / "settings" / "__init__.py"}


def _sources() -> list[Path]:
    return [path for root in SEARCHED for path in root.rglob("*.py")
            if path not in EXCLUDED and "migrations" not in path.parts]


def test_every_setting_is_read_by_something():
    """A control attached to nothing is a lie the panel tells every time it renders.

    `job_max_age_hours` is the reason this file exists: the window it replaced was read through a
    config key a migration had deleted, so the filter fell back to a constant for months and the
    panel showed a freshness control that governed something else entirely.
    """
    bodies = [path.read_text(encoding="utf-8") for path in _sources()]
    settings_module = (ROOT / "libs" / "settings" / "__init__.py").read_text(encoding="utf-8")

    def reached(key: str) -> bool:
        quoted = (f'"{key}"', f"'{key}'")
        if any(q in body for body in bodies for q in quoted):
            return True
        # Or through a typed helper in `libs.settings` that names it — provided the helper is
        # called from somewhere outside the module that defines it.
        for helper in re.findall(r"^def (\w+)\(.*?(?=^def |\Z)", settings_module, re.M | re.S):
            block = re.search(rf"^def {helper}\(.*?(?=^def |\Z)", settings_module, re.M | re.S)
            if (block and any(q in block.group(0) for q in quoted)
                    and any(f"{helper}(" in body for body in bodies)):
                return True
        return False

    unread = [key for key in DEFAULTS if not reached(key)]

    assert not unread, (
        f"these platform settings are rendered in the admin panel and read by nothing: {unread}. "
        "Either give each one a consumer or take it out of DEFAULTS — a setting that governs "
        "nothing is worse than an absent one, because it looks answered.")


def test_every_setting_has_a_control_in_the_panel():
    """A stored value nobody can change is a constant with extra steps."""
    panel = (ROOT / "web" / "src" / "pages" / "AdminPage.tsx").read_text(encoding="utf-8")

    missing = [key for key in DEFAULTS if key not in panel]
    assert not missing, (
        f"these settings have no control in the operator panel: {missing}")


def test_the_cycle_budget_fits_inside_the_unit_that_kills_it():
    """A cycle killed by systemd skips every stage after the one that overran, and says nothing."""
    from libs.settings import HARVEST_CYCLE_BUDGET_SECONDS

    unit = (ROOT / "deploy" / "harvester.service").read_text(encoding="utf-8")
    found = re.search(r"TimeoutStartSec=(\d+)(min|s)?", unit)
    assert found, "harvester.service has no TimeoutStartSec; the budget below has nothing to fit in"

    seconds = int(found.group(1)) * (60 if found.group(2) == "min" else 1)
    assert seconds > HARVEST_CYCLE_BUDGET_SECONDS, (
        f"the cycle budget ({HARVEST_CYCLE_BUDGET_SECONDS}s) has to leave room inside the unit's "
        f"{seconds}s timeout, or systemd stops the cycle mid-stage rather than the cycle stopping "
        "itself and reporting what is left")


def test_the_lanes_a_person_is_waiting_on_poll_faster_than_the_rest():
    """Five seconds between a tap on Build and the job being claimed is five seconds of nothing."""
    from libs.queue.worker import IDLE_SLEEP_SEC, INTERACTIVE_IDLE_SLEEP_SEC

    assert INTERACTIVE_IDLE_SLEEP_SEC < IDLE_SLEEP_SEC

    for name in ("services/docgen/__main__.py", "services/setupagent/__main__.py"):
        body = (ROOT / name).read_text(encoding="utf-8")
        assert "INTERACTIVE_IDLE_SLEEP_SEC" in body, (
            f"{name} runs a lane somebody is watching, so it must not wait the background poll")
