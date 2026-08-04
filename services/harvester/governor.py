"""Global controls for the per-user LinkedIn tier. Policy and scheduling arithmetic, no I/O.

**Why a per-user tier still needs a global governor** (`HARVESTER_PLAN.md` §5.2b). When LinkedIn
moved to per-user loops under decision D3, it left the shared fan-out budget behind — that budget
governs the *shared* query set and has no view of 50 users running independent schedules.

Per-user proxies give IP diversity, which handles **ban risk**. They do nothing about the platform
issuing hundreds of concurrent LinkedIn requests, and nothing about a thundering herd when many
users happen to share a notification interval. Those are different problems and need these three
controls.

Nothing here executes anything. The per-user LinkedIn run is an ordinary queue job
(`SCHEDULER_PLAN.md`), so the concurrency cap is simply that lane's worker-pool size — no new
mechanism, and the wiring lands with `libs/queue` in Phase 2.
"""
from __future__ import annotations

import os
import random
from datetime import datetime, timedelta

from libs.settings import DELIVERY_INTERVAL_MIN

# Global ceiling on simultaneous per-user LinkedIn loops. Becomes the `linkedin` lane's worker count.
LINKEDIN_MAX_CONCURRENT_USERS = 4

# The floor under whatever an operator sets in the admin panel.
#
# **Was 60, lowered to the delivery cadence on 2026-08-04**, because at 60 the control was a lie:
# `linkedin_interval_min` is a limit in the admin panel with a default of 15, and a floor above it
# silently rewrote every value below an hour. Wiring the control without moving the floor would have
# fixed the plumbing and left the behaviour exactly as it was.
#
# The floor existed because this tier spends a user's own bandwidth and carries ban risk. The first
# argument is measurably weak — the cap is 200 MB a day and observed usage is 0.0 — and the second
# is a **deliberate trade**: LinkedIn produced 337 of the founder's 348 jobs above the delivery
# cutoff, and an hourly floor meant a posting there could be an hour old before the pipeline had
# seen it at all, which is most of the lead time this product exists to sell.
#
# What still protects the tier: jitter below, the per-user proxy cap, the concurrency ceiling, and
# quarantine on consecutive failures. What is gone is a blanket floor that also applied to accounts
# using almost none of their allowance.
LINKEDIN_MIN_INTERVAL_MIN = DELIVERY_INTERVAL_MIN

# ±10% of the interval. Without it, every user created with the default interval fires at the same
# instant forever — the thundering herd that per-user scheduling otherwise creates for free.
LINKEDIN_JITTER_FRACTION = 0.10


def linkedin_enabled() -> bool:
    """The global kill switch.

    Exists because the alternative during an incident is editing 50 users' configs one at a time,
    while LinkedIn is actively blocking. One environment variable and a restart of the lane's
    workers stops the entire tier.

    Default on; set `HARVEST_LINKEDIN_DISABLED=1` to stop it.
    """
    return os.environ.get("HARVEST_LINKEDIN_DISABLED", "").strip().lower() not in {"1", "true", "yes"}


def resolve_linkedin_interval(notify_interval_min: int) -> int:
    """Default the LinkedIn interval from the user's notification interval, floored at the minimum.

    Defaulting rather than asking: the user already told us how often they want to hear from us, and
    fetching a paid tier more often than they will ever read it is pure cost.
    """
    return max(int(notify_interval_min), LINKEDIN_MIN_INTERVAL_MIN)


def next_linkedin_run(
    now: datetime,
    interval_min: int,
    *,
    rng: random.Random | None = None,
) -> datetime:
    """When this user's next LinkedIn run is due, with jitter applied.

    Jitter is applied per scheduling decision rather than once at signup, so users do not
    re-converge after a restart re-arms everyone at the same moment — which is exactly when a herd
    would otherwise form.

    `rng` is injectable so scheduling is testable without the result depending on chance. A
    scheduler whose behaviour cannot be reproduced is a scheduler whose bugs cannot be either.
    """
    rng = rng or random
    interval = max(int(interval_min), LINKEDIN_MIN_INTERVAL_MIN)
    spread = interval * LINKEDIN_JITTER_FRACTION
    offset = rng.uniform(-spread, spread)
    return now + timedelta(minutes=interval + offset)
