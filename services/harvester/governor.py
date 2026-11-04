"""Global controls for the LinkedIn tier."""
from __future__ import annotations

import os


def linkedin_enabled() -> bool:
    """The global kill switch."""
    return os.environ.get("HARVEST_LINKEDIN_DISABLED", "").strip().lower() not in {"1", "true", "yes"}
