"""Every clock in the platform, checked against every other one.

**This is the test for the failure class that has cost the most.** Four of the defects found on
2026-08-05 were the same shape: a step allowed to run longer than a window somebody else set, in a
different file, often in a different language. The suite could not catch any of them, because each
component was individually correct — `send()` works, and the notify claim really is two minutes, and
200 × 3 seconds is still greater than 120.

So the thing under test here is not a component. It is the **relationships between them**, which is
where this project's bugs actually live:

- a systemd one-shot must finish inside its own timer, or the tick after it never starts;
- a queue handler must finish inside its lane's claim, or the sweeper hands its work to a second
  worker while the first is still going;
- and a schedule written in a unit file must agree with the same schedule written in Python.

`deploy/*.timer` and `deploy/*.service` are parsed as data. They are configuration, and
configuration that nothing validates is configuration that drifts — `linkedin.service` carried a
30-minute timeout against a 15-minute timer for weeks, and the symptom was five hours with no
LinkedIn jobs and a timer reporting no next run (ROADMAP 6ap).

No database, no network. This runs in CI on every commit.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

UNITS = Path("deploy")

# `2min`, `15min`, `60`, `1h 30min` — systemd's time spans, in the forms this tree actually uses.
_SPAN = re.compile(r"(\d+)\s*(h|min|s)?")


def seconds(value: str) -> int:
    """A systemd time span in seconds. A bare number is seconds, which is systemd's own default."""
    total = 0
    for amount, unit in _SPAN.findall(value.strip()):
        total += int(amount) * {"h": 3600, "min": 60, "s": 1, "": 1}[unit or ""]
    return total


def directive(path: Path, name: str) -> str | None:
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith(f"{name}="):
            return line.split("=", 1)[1]
    return None


def timers() -> list[Path]:
    found = sorted(UNITS.glob("*.timer"))
    assert found, "no timer units found — this test is looking in the wrong place"
    return found


@pytest.mark.parametrize("timer", timers(), ids=lambda p: p.stem)
def test_a_one_shot_finishes_inside_its_own_tick(timer: Path):
    """**The invariant that was broken and nothing checked.**

    `Type=oneshot` means systemd will not start a run while one is active, so a timeout longer than
    the interval does not delay the next tick — it cancels it. `linkedin.service` had
    `TimeoutStartSec=30min` against `OnUnitActiveSec=15min`, so a stuck pass held the tier for two
    whole ticks before systemd would even kill it.
    """
    interval = directive(timer, "OnUnitActiveSec")
    if interval is None:
        return                                  # a calendar timer; the daily backup is not a tick

    service = timer.with_suffix(".service")
    assert service.exists(), f"{timer.name} has no matching service unit"
    timeout = directive(service, "TimeoutStartSec")
    assert timeout is not None, (
        f"{service.name} has no TimeoutStartSec — a one-shot with no timeout can hang forever and "
        "hold every tick after it")

    assert seconds(timeout) < seconds(interval), (
        f"{service.name} may run for {timeout} but {timer.name} fires every {interval}. A run that "
        "outlives its interval stops the next one starting, which is not a delay — it is an outage "
        "that ends when somebody notices.")


def test_the_python_tick_matches_the_unit_files():
    """systemd cannot read Python, so the number exists twice. This is what stops them drifting —
    they were identical by hand, and one of them was still wrong."""
    from libs.settings import PLATFORM_TICK_MIN

    ticking = {timer.stem: directive(timer, "OnUnitActiveSec") for timer in timers()}
    on_the_tick = {name: value for name, value in ticking.items()
                   if value and seconds(value) == PLATFORM_TICK_MIN * 60}

    assert on_the_tick, (
        f"no timer fires at PLATFORM_TICK_MIN ({PLATFORM_TICK_MIN}min), so either the setting is "
        f"wrong or every unit has drifted from it: {ticking}")
    # The harvest and the LinkedIn tier are the two that are meant to share it.
    assert {"harvester", "linkedin"} <= set(on_the_tick), (
        f"these should tick with the platform: {sorted(on_the_tick)}")


def test_the_linkedin_describe_budget_fits_inside_the_pass():
    """Derived from the tick rather than written beside it — this was a local `= 15` hand-copied
    from the timer, which is the same defect the derivation exists to prevent."""
    from libs.settings import PLATFORM_TICK_MIN
    from services.harvester.linkedin import DESCRIBE_BUDGET_SECONDS

    tick = PLATFORM_TICK_MIN * 60
    assert tick > DESCRIBE_BUDGET_SECONDS, "a pass must not outlive the tick that started it"

    timeout = seconds(directive(UNITS / "linkedin.service", "TimeoutStartSec"))
    assert timeout > DESCRIBE_BUDGET_SECONDS, (
        "the pass must stop itself before systemd kills it, or the run ends with no summary and "
        "nothing recorded about what it did")


def test_a_notify_run_fits_inside_the_notify_claim():
    """The other half of the same rule, on the queue rather than on systemd. The refresh and the
    send both happen inside one claim, and 200 messages at a 3-second gap does not."""
    from libs.db.models.queue import CLAIM_TIMEOUT_MIN
    from services.notifier.deliver import SEND_BUDGET_SECONDS
    from services.notifier.freshness import REFRESH_BUDGET_SECONDS

    claim = CLAIM_TIMEOUT_MIN["notify"] * 60
    assert claim > REFRESH_BUDGET_SECONDS + SEND_BUDGET_SECONDS, (
        f"the refresh ({REFRESH_BUDGET_SECONDS}s) and the send ({SEND_BUDGET_SECONDS}s) together "
        f"must finish inside the {claim}s claim, or the job is swept back to ready while its "
        "worker is still sending and a second worker starts the same digest from the top")


def test_every_timer_has_a_service_and_every_service_a_timer():
    """A unit with no counterpart is either dead or never runs, and both are silent. Two dead
    sweeps and a labeller that never started have already been found here by accident."""
    stems = {p.stem for p in timers()}
    services = {p.stem for p in UNITS.glob("*.service")}

    orphan_timers = stems - services
    assert not orphan_timers, f"timers with no service: {sorted(orphan_timers)}"

    # A service without a timer is legitimate only if something else starts it; none such exist
    # today, so any new one is a deliberate decision that should update this test.
    orphan_services = services - stems
    assert not orphan_services, (
        f"services no timer starts: {sorted(orphan_services)} — if that is intended, say so here")
