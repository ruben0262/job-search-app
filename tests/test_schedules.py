"""Every clock in the platform, checked against every other one."""
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
    """The invariant that was broken and nothing checked."""
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
    """systemd cannot read Python, so the number exists twice."""
    from libs.settings import PLATFORM_TICK_MIN

    ticking = {timer.stem: directive(timer, "OnUnitActiveSec") for timer in timers()}
    on_the_tick = {name: value for name, value in ticking.items()
                   if value and seconds(value) == PLATFORM_TICK_MIN * 60}

    assert on_the_tick, (
        f"no timer fires at PLATFORM_TICK_MIN ({PLATFORM_TICK_MIN}min), so either the setting is "
        f"wrong or every unit has drifted from it: {ticking}")
    # One fetch tick. LinkedIn used to have its own and now runs on the back of this one.
    assert "harvester" in on_the_tick, (
        f"the fetch tick must be the platform tick: {sorted(on_the_tick)}")


def test_the_linkedin_pass_fits_inside_the_harvest_tick_it_now_rides_on():
    """The LinkedIn pass runs on the back of the harvest tick."""
    from libs.settings import PLATFORM_TICK_MIN
    from services.harvester.linkedin import DESCRIBE_BUDGET_SECONDS

    assert PLATFORM_TICK_MIN * 60 > DESCRIBE_BUDGET_SECONDS, (
        "a pass must not outlive the tick that started it")

    timeout = seconds(directive(UNITS / "harvester.service", "TimeoutStartSec"))
    assert timeout > DESCRIBE_BUDGET_SECONDS, (
        f"the harvest unit allows {timeout}s and the LinkedIn pass inside it may take "
        f"{DESCRIBE_BUDGET_SECONDS}s, so the run would be killed part-way with no summary")


def test_only_the_timers_that_have_to_exist_do():
    """Every clock is a unit to install, a timeout to get wrong."""
    assert {timer.stem for timer in timers()} == {"harvester", "health", "backup"}


def test_a_notify_run_fits_inside_the_notify_claim():
    """The other half of the same rule, on the queue rather than on systemd."""
    from libs.db.models.queue import CLAIM_TIMEOUT_MIN
    from services.notifier.deliver import SEND_BUDGET_SECONDS

    claim = CLAIM_TIMEOUT_MIN["notify"] * 60
    assert claim > SEND_BUDGET_SECONDS, (
        f"the send ({SEND_BUDGET_SECONDS}s) must finish inside the {claim}s claim, or the job is "
        "swept back to ready while its worker is still sending and a second worker starts the "
        "same digest from the top")


def test_every_timer_has_a_service_and_every_service_a_timer():
    """A unit with no counterpart is either dead or never runs, and both are silent."""
    stems = {p.stem for p in timers()}
    services = {p.stem for p in UNITS.glob("*.service")}

    orphan_timers = stems - services
    assert not orphan_timers, f"timers with no service: {sorted(orphan_timers)}"

    # A service without a timer is legitimate only if something else starts it; none such exist today.
    orphan_services = services - stems
    assert not orphan_services, (
        f"services no timer starts: {sorted(orphan_services)} — if that is intended, say so here")


def test_no_unit_expects_systemd_to_expand_a_variable_in_environment():
    """**systemd does not expand `${VAR}` inside `Environment=`.** It passes the literal string.

    This cost the platform its backups and its monitoring for a fortnight, silently. Both units read
    `POSTGRES_PASSWORD` from an `EnvironmentFile=` and built `DATABASE_URL` from it in an
    `Environment=` line; systemd handed the process the literal `${POSTGRES_PASSWORD}`, Postgres
    refused it, and every run died on `password authentication failed`.

    Measured on the box 2026-08-11: **2,318 failed health runs since Jul 29, zero successful**, and
    the only database dumps on disk were from Jul 28 — fourteen days stale, because `backup.py`
    correctly deletes its partial file on failure.

    Expansion works in `ExecStart=`, which is where it belongs.
    """
    offenders = []
    for unit in sorted(UNITS.glob("*.service")):
        for raw in unit.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if line.startswith("Environment=") and "${" in line:
                offenders.append(f"{unit.name}: {line.split('=', 1)[0]}=...${{...}}")

    assert not offenders, (
        "systemd passes these through literally rather than expanding them, so the process gets "
        f"`${{VAR}}` as a value: {offenders}. Build the value in ExecStart, where the shell expands "
        "it.")


def test_the_health_checks_warning_code_is_not_the_code_a_crash_returns():
    """A monitor whose own failure is indistinguishable from a clean run is not a monitor.

    `check_health.py` returned 1 for "warnings" and `health.service` listed `SuccessExitStatus=0 1`
    so a warning would not mark the unit failed. **An uncaught Python exception also exits 1.** So
    when the check began crashing on 2026-07-29 it exited 1, systemd read that as success, and
    `systemctl --failed` stayed clean for thirteen days while nothing was being checked — including
    the backup failure it existed to report.
    """
    from deploy.scripts.check_health import WARN_EXIT

    assert WARN_EXIT != 1, "a crash exits 1; the warning code must not collide with it"

    tolerated = directive(UNITS / "health.service", "SuccessExitStatus").split()
    assert "1" not in tolerated, (
        "health.service tolerates exit 1, which is what an uncaught exception returns — a crashing "
        f"health check would report success. Tolerated: {tolerated}")
    assert str(WARN_EXIT) in tolerated, (
        f"a warning ({WARN_EXIT}) must not mark the unit failed, or a warning starts a restart storm")


def test_an_embed_batch_can_finish_inside_the_client_timeout():
    """A batch that cannot finish is a deadlock, not a slow path."""
    from libs.contracts.embedder import DEFAULT_EMBED_TIMEOUT
    from services.harvester.embedding import EMBED_BATCH_SIZE, EMBED_SECONDS_PER_TEXT

    worst_case = EMBED_BATCH_SIZE * EMBED_SECONDS_PER_TEXT
    assert worst_case < DEFAULT_EMBED_TIMEOUT * 0.6, (
        f"a full batch takes about {worst_case:.0f}s against a {DEFAULT_EMBED_TIMEOUT:.0f}s "
        "timeout, which leaves nothing for a slow day")
