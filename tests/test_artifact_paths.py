"""Host tools and container paths: the mismatch that nearly deleted every model."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "deploy" / "scripts"))


class _Session:
    """Just enough session to hand `orphans` a list of stored paths."""

    def __init__(self, paths):
        self._paths = paths

    def scalars(self, _statement):
        return self._paths


def test_an_artifact_the_database_claims_is_never_an_orphan(tmp_path):
    """**This is the near-miss.** `models.path` is the path the trainer's *container* saw.

    The pruner runs on the host, where the same files live under `MODEL_DIR`. Comparing resolved
    absolute paths meant nothing ever matched: a first run against production reported all eight
    artifacts as orphans, **including both active models**, and `--delete` would have removed the
    files the scorer serves from for every account. Only the dry-run default stopped it.

    The filename is the durable identity — `_write_artifact` builds it from the user id and version,
    and `resolve_artifact` already falls back to `MODEL_DIR` plus the basename.
    """
    from prune_orphaned_artifacts import orphans

    live = tmp_path / "7533bb9c.v4.joblib"
    live.write_bytes(b"a model")
    stale = tmp_path / "d620f6f3.v2.joblib"
    stale.write_bytes(b"belongs to a deleted account")

    # What the database holds: container paths, which do not exist on this host at all.
    session = _Session(["/var/lib/jobsearch/models/7533bb9c.v4.joblib"])

    found = orphans(session, tmp_path)

    assert live not in found, (
        "an artifact the database claims was reported as an orphan — with --delete this deletes the "
        "model the scorer is serving")
    assert found == [stale]


def test_nothing_is_deleted_without_being_asked():
    """The default that prevented the above from being an outage rather than a finding."""
    import prune_orphaned_artifacts as pruner

    source = Path(pruner.__file__).read_text(encoding="utf-8")
    assert '"--delete", action="store_true"' in source, (
        "deleting must stay opt-in; this script's first correct-looking run was wrong")


@pytest.mark.parametrize("unit", ["backup.service"])
def test_the_backup_reads_the_host_artifact_directory_from_one_place(unit):
    """`--models /var/lib/jobsearch/models` was a container path passed to a host process.

    `backup.py` archives the directory only `if models.is_dir()`, so it silently archived nothing —
    every dump for two weeks carried the database and none of the models. One env var, `MODEL_DIR`,
    is now the single answer for the backup, the pruner and the restore drill.
    """
    text = (ROOT / "deploy" / unit).read_text(encoding="utf-8")
    assert "--models /var/lib/jobsearch/models" not in text, (
        "the unit passes a container path to a host process; the directory will not exist")
    assert "MODEL_DIR" in text
