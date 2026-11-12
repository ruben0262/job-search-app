"""Logging: the properties that make a failure visible rather than silent."""
from __future__ import annotations

import logging

import pytest

pytest.importorskip("cryptography")

from libs.obs import audit, configure
from libs.obs.logs import LOG_FORMAT
from libs.secrets import redacting

TOKEN = "sk-ant-api03-" + "Zx9" * 20


@pytest.fixture(autouse=True)
def _clean_root():
    """Each test starts from a bare root logger and leaves it that way."""
    root = logging.getLogger()
    saved, level = list(root.handlers), root.level
    root.handlers.clear()
    try:
        yield
    finally:
        root.handlers.clear()
        root.handlers.extend(saved)
        root.setLevel(level)


def test_a_secret_never_reaches_the_log_file(tmp_path):
    """The console is transient; the file is what is kept, shipped, and read during an incident."""
    path = configure("harvester", log_dir=tmp_path, console=False)
    with redacting(TOKEN):
        logging.getLogger("services.harvester.fetch").warning("dialling with %s", TOKEN)

    written = path.read_text(encoding="utf-8")
    assert TOKEN not in written
    assert "[REDACTED]" in written


def test_a_child_loggers_record_is_written(tmp_path):
    """Every module logs through `logging.getLogger(__name__)`."""
    path = configure("api", log_dir=tmp_path, console=False)
    logging.getLogger("services.api.auth_routes").info("signup accepted")

    written = path.read_text(encoding="utf-8")
    assert "signup accepted" in written
    assert "services.api.auth_routes" in written


def test_the_line_says_which_process_wrote_it(tmp_path):
    """Four workers share one box. Without the service name, a log line is half an answer."""
    path = configure("notifier", log_dir=tmp_path, console=False)
    logging.getLogger("services.notifier.deliver").info("digest sent")
    assert "[notifier]" in path.read_text(encoding="utf-8")


def test_configuring_twice_does_not_duplicate_every_line(tmp_path):
    """Entry points, operational scripts, and test fixtures all call it."""
    configure("trainer", log_dir=tmp_path, console=False)
    configure("trainer", log_dir=tmp_path, console=False)

    logging.getLogger("services.trainer.train").info("fitted")
    assert path_lines(tmp_path / "trainer.log", "fitted") == 1


def test_an_unwritable_log_directory_warns_and_keeps_going(tmp_path):
    """A read-only volume must not stop a harvest."""
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("", encoding="utf-8")

    assert configure("harvester", log_dir=blocker / "logs", console=False) is None
    logging.getLogger("services.harvester.cycle").info("still running")  # must not raise


def test_audit_writes_one_greppable_line(tmp_path):
    """For events where "it did not happen" and "it happened" look identical unless written down."""
    path = configure("trainer", log_dir=tmp_path, console=False)
    audit(logging.getLogger("services.trainer"), "model.published", user="abc123", version=2)

    line = path.read_text(encoding="utf-8").strip()
    assert "model.published user=abc123 version=2" in line


def test_the_format_carries_level_service_and_module():
    """Asserted on the constant rather than on output."""
    for field in ("%(levelname)", "%(service)s", "%(name)s", "%(asctime)s", "%(message)s"):
        assert field in LOG_FORMAT


def path_lines(path, needle: str) -> int:
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if needle in line)


@pytest.mark.parametrize("library", ["httpx", "httpx2", "stripe"])
def test_a_library_narrating_every_request_does_not_drown_our_own_lines(tmp_path, library):
    """One line in ten was ours. `httpx2` is the client the Anthropic SDK moved to at 1.0; Stripe's SDK says
    two lines a call, the request and its answer (2026-11-11)."""
    path = configure("notifier", log_dir=tmp_path, console=False)

    logging.getLogger(library).info('HTTP Request: GET https://example.com "HTTP/1.1 200 OK"')
    logging.getLogger(library).warning("could not reach the host")
    logging.getLogger("services.notifier.deliver").info("notified abc123 with 3 item(s)")

    assert path_lines(path, "200 OK") == 0
    assert path_lines(path, "could not reach the host") == 1
    assert path_lines(path, "notified abc123") == 1


def test_debug_keeps_the_request_trace(tmp_path):
    """`LOG_LEVEL=DEBUG` means somebody is looking at a specific problem and the per-request trace is the."""
    path = configure("harvester", level="DEBUG", log_dir=tmp_path, console=False)

    logging.getLogger("httpx").info('HTTP Request: GET https://example.com "HTTP/1.1 200 OK"')

    assert path_lines(path, "200 OK") == 1
