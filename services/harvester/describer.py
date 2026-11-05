"""LinkedIn's job texts, read all the time, beside the 15-minute run that lists the jobs (owner, 2026-11-05).

The run (`services.harvester.linkedin`) lists every picked country and stores what is new. This reads each
waiting job's text from then on, newest first, a round at a time, as many pages at once as the proxy has
addresses (`proxy_exits`). Read inside the run before, it had 11 minutes of every 15, after the lists, and
the first connection the proxy refused stopped it for the run: about four texts a run while 13,800 waited.

- Between rounds it looks again: for newer jobs, which go first, and for a sweep running (the `linkedin`
  lease). A sweep goes first, so the two never spend LinkedIn's allowance at once.
- After a round that read nothing it rests `REST_SECONDS`: nothing waiting, LinkedIn refusing every try,
  or the proxy down. The last two are said.
- One line every `PLATFORM_TICK_MIN`, what was read and what was refused, rather than one a round.

    python -m services.harvester.describer
"""
from __future__ import annotations

import logging
import time
from collections import Counter
from dataclasses import dataclass, field
from time import monotonic

from libs.corpus.repository import CorpusRepository
from libs.db import database, held
from libs.obs import configure
from libs.proxy import ENV as PROXY_ENV
from libs.proxy import site_proxy
from libs.secrets import install, redacting
from libs.settings import PLATFORM_TICK_MIN, proxy_exits
from libs.settings import get as settings_get
from services.harvester.describe import DescribeReport, describe_pending
from services.harvester.fetch import make_site_client
from services.harvester.governor import linkedin_enabled

logger = logging.getLogger(__name__)

# Jobs a round reads before looking again for newer ones and for a sweep: about half a minute at the pace
# six addresses allow.
ROUND = 120
# The rest after a round that read nothing. LinkedIn answered again a minute after refusing everything
# (2026-11-04), and a sweep takes one to three minutes: half a minute is late for neither by much.
REST_SECONDS = 30


@dataclass
class Tally:
    """What the rounds since the last line did."""

    rounds: int = 0
    attempted: int = 0
    described: int = 0
    outcomes: Counter = field(default_factory=Counter)
    since: float = field(default_factory=monotonic)

    def add(self, report: DescribeReport) -> None:
        self.rounds += 1
        self.attempted += report.attempted
        self.described += report.described
        self.outcomes.update(report.outcomes)


def run_round(db) -> DescribeReport | None:
    """Up to `ROUND` of the newest LinkedIn jobs waiting for their text, or `None` when none can be read
    now: LinkedIn switched off, no proxy, or a sweep running."""
    if not linkedin_enabled() or (proxy := site_proxy()) is None or held(db, "linkedin"):
        return None
    with redacting(proxy), make_site_client(proxy) as client:
        report = describe_pending(CorpusRepository(db), client, linkedin=True, limit=ROUND,
                                  max_age_hours=int(settings_get(db, "job_max_age_hours")),
                                  workers=proxy_exits(db), quiet=True)
    if report.proxy_error:
        logger.error("describer: the site proxy refused every try, so the round stopped: %s",
                     report.proxy_error[:200])
    return report


def run(db, *, rounds: int | None = None, sleep=time.sleep) -> None:
    """Round after round, resting after one that read nothing. `rounds` ends it, for a test."""
    tally = Tally()
    while rounds is None or rounds > 0:
        try:
            report = run_round(db)
        except Exception:
            logger.exception("describer: a round failed; resting, then going on")
            report = None
        if report is not None:
            tally.add(report)
        if report is None or not report.described:
            sleep(REST_SECONDS)
        if monotonic() - tally.since >= PLATFORM_TICK_MIN * 60:
            _say(db, tally)
            tally = Tally()
        rounds = None if rounds is None else rounds - 1


def _say(db, tally: Tally) -> None:
    """The line a tick: what was read, why the rest was not, and how many still wait."""
    waiting = CorpusRepository(db).count_awaiting_description(
        linkedin=True, max_age_hours=int(settings_get(db, "job_max_age_hours")))
    logger.info("describer: %d text(s) read of %d tried in %d round(s), %d waiting — %s", tally.described,
                tally.attempted, tally.rounds, waiting, dict(tally.outcomes))


def main() -> int:
    configure("describer")
    # The site proxy's address carries a password, and a traceback would carry it.
    install()
    db = database()
    if not linkedin_enabled():
        logger.warning("describer: LinkedIn is switched off (HARVEST_LINKEDIN_DISABLED), so no text is read")
    elif site_proxy() is None:
        logger.warning("describer: %s is not set, so no LinkedIn text is read", PROXY_ENV)
    run(db)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
