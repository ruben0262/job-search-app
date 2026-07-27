"""Pure predicate and normalisation functions shared across the platform.

Lifted from `pipeline/lib/` (`CODE_STRUCTURE.md` §3). They live in `libs` rather than under the
harvester because more than one service needs them — the harvester filters on recency during
ingest, and the scorer and notifier both render "how long ago posted" from the same timestamps. A
second copy would drift, and a job that looked 3 hours old in the feed and 3 days old in a digest is
the kind of inconsistency nobody reports as a bug.

They are pure: no I/O, no database, no clock injection beyond `datetime.now`. That is what makes
them testable without standing anything up, and what makes them safe to call per job in a loop.
"""
from libs.corpus.filters.timefilter import (
    age_label,
    epoch_to_iso,
    is_within_hours,
    parse_date,
)

__all__ = ["age_label", "epoch_to_iso", "is_within_hours", "parse_date"]
