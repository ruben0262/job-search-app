"""Global controls for the per-user LinkedIn tier."""
from __future__ import annotations

import os
import random
from datetime import datetime, timedelta

# Global ceiling on simultaneous per-user LinkedIn loops. Becomes the `linkedin` lane's worker count.
LINKEDIN_MAX_CONCURRENT_USERS = 4

# ±10% of the interval.
LINKEDIN_JITTER_FRACTION = 0.10


def linkedin_enabled() -> bool:
    """The global kill switch."""
    return os.environ.get("HARVEST_LINKEDIN_DISABLED", "").strip().lower() not in {"1", "true", "yes"}


def next_linkedin_run(
    now: datetime,
    interval_min: int,
    *,
    rng: random.Random | None = None,
) -> datetime:
    """When this user's next LinkedIn run is due, with jitter applied. The interval is the account's
    `linkedin_interval_min` limit, whose catalogue entry holds the floor."""
    rng = rng or random
    interval = int(interval_min)
    spread = interval * LINKEDIN_JITTER_FRACTION
    offset = rng.uniform(-spread, spread)
    return now + timedelta(minutes=interval + offset)
