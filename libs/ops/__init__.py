"""Operations: seeing the platform, and being told when it needs a person.

**Every failure here is silent** (``OPERATIONS_PLAN.md`` §1). A stalled queue, a harvester finding
nothing, a retention trim that quietly stopped — none of them raise, and each is otherwise
discovered weeks later by a user asking why their feed went quiet. ``health`` makes them numbers;
``alerts`` decides which numbers need waking somebody up.

In ``libs`` because the admin endpoint and the alert timer must give identical answers, and two
implementations would disagree exactly when it mattered.
"""
from libs.ops.alerts import Alert, evaluate, render
from libs.ops.health import (
    LaneHealth,
    PlatformHealth,
    UserHealth,
    lane_health,
    platform_health,
    user_health,
)

__all__ = ["Alert", "LaneHealth", "PlatformHealth", "UserHealth", "evaluate", "lane_health",
           "platform_health", "render", "user_health"]
