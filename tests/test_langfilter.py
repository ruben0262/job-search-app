"""Reading a text's language: the same answer however many threads ask at once."""
from __future__ import annotations

import threading

import pytest

ENGLISH = ("Built the billing service in Python and Postgres, cut checkout latency to forty milliseconds "
           "for twelve hundred shops, and ran the on-call rota for the payments team.")


def test_a_language_read_on_several_threads_at_once_is_read_the_same_as_on_one():
    """langdetect fills one shared factory on first use, and threads that started together read it
    half-filled: English came back as None or as Norwegian (2026-11-11). The harvester describes on two
    threads, so a posting could be stored in the wrong language and hidden by someone's language filter."""
    pytest.importorskip("langdetect")
    from langdetect import detector_factory

    from libs.corpus import langfilter

    detector_factory._factory = None  # first use, as in a fresh process
    found: list[str | None] = []
    threads = [threading.Thread(target=lambda: found.append(langfilter.detect_language(ENGLISH)))
               for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert found == ["en"] * 8
