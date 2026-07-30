"""Logging for every process on the platform. One configuration, one format, one place.

`OPERATIONS_PLAN.md` §4 exists because of a specific failure class: **the platform stopping quietly**.
A crashed worker, a source returning nothing, a delivery that never happened — none of those raise,
and none of them are visible unless something writes them down. So the rule here is that anything
which could be mistaken for "nothing to do" gets a log line saying which it was.

Three properties this module guarantees, because each has already gone wrong once:

1. **Secrets never reach a log file.** `configure()` attaches the redaction filter to every handler
   it creates, not to the root logger — a logger's filters do not run for records propagated from a
   child logger, so a filter installed on the root alone protects almost nothing.
2. **Logs survive the process.** A rotating file per service, so a crash at 3 a.m. is still readable
   at 9. Console alone is lost the moment the container is replaced.
3. **Configuring twice does not double every line.** Entry points, tests, and scripts all call it.
"""
from libs.obs.logs import (
    DEFAULT_LOG_DIR,
    LOG_FORMAT,
    audit,
    configure,
    log_path,
)
from libs.obs.timing import Timings, timed
from libs.obs.timing import report as report_timings

__all__ = [
    "DEFAULT_LOG_DIR",
    "LOG_FORMAT",
    "Timings",
    "audit",
    "configure",
    "log_path",
    "report_timings",
    "timed",
]
