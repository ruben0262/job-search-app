"""Which channel a job is delivered to."""
from __future__ import annotations

import pytest

from services.notifier.routing import Routable, chat_for, group_by_chat, is_hungarian, route_key

ROUTES = {"hu": "-100HU", "linkedin": "-100LI", "other": "-100OTHER"}
PERSONAL = "12345"


def job(source: str | None = None, location: str | None = None) -> Routable:
    return Routable(source=source, location=location)


# ── the ordering ──────────────────────────────────────────────────────────────────────────────

def test_a_hungarian_job_wins_even_when_it_came_from_linkedin():
    """The rule most easily lost in a port."""
    assert chat_for(job("linkedin", "Budapest, Hungary"), ROUTES, PERSONAL) == "-100HU"


def test_linkedin_wins_when_the_job_is_not_hungarian():
    assert chat_for(job("linkedin", "Berlin, Germany"), ROUTES, PERSONAL) == "-100LI"


def test_everything_else_lands_in_the_third_channel():
    assert chat_for(job("indeed", "Amsterdam"), ROUTES, PERSONAL) == "-100OTHER"


@pytest.mark.parametrize("source", ["profession", "dreamjobs", "nofluffjobs_hu", "jooble"])
def test_a_hungarian_only_source_needs_no_location(source):
    """These sources carry nothing but Hungarian listings."""
    assert is_hungarian(job(source, location=None))


@pytest.mark.parametrize("location", ["Budapest", "GYŐR", "Gyor", "Pécs", "Debrecen, Magyarország"])
def test_hungarian_places_match_accented_and_not(location):
    """A posting writes its location however it likes."""
    assert route_key(job("indeed", location)) == "hu"


def test_a_city_name_appearing_anywhere_routes_as_hungarian_including_a_false_positive():
    """A known limitation, asserted so it is a decision rather than a surprise."""
    assert route_key(job("indeed", "New Budapest Restaurant Group, Chicago")) == "hu"


# ── the fallback ──────────────────────────────────────────────────────────────────────────────

def test_an_unconfigured_route_falls_back_to_the_personal_chat():
    """A routing table is a convenience."""
    assert chat_for(job("linkedin", "Berlin"), {"hu": "-100HU"}, PERSONAL) == PERSONAL


def test_no_routes_at_all_behaves_exactly_as_before_routing_existed():
    for candidate in (job("linkedin", "Budapest"), job("indeed", "Berlin"), job(None, None)):
        assert chat_for(candidate, None, PERSONAL) == PERSONAL


def test_an_empty_route_value_is_treated_as_unset():
    """A blank in a config file is somebody who has not filled it in yet, not a chat id of ''."""
    assert chat_for(job("indeed", "Berlin"), {"other": ""}, PERSONAL) == PERSONAL


# ── grouping a digest ─────────────────────────────────────────────────────────────────────────

class Item:
    def __init__(self, name: str, source: str, location: str):
        self.name, self.source, self.location = name, source, location


def test_one_delivery_can_span_several_channels():
    """Deciding once for a whole batch would put a Budapest job in the LinkedIn stream whenever it."""
    items = [Item("a", "linkedin", "Budapest"), Item("b", "linkedin", "Berlin"),
             Item("c", "indeed", "Paris")]
    grouped = group_by_chat(items, ROUTES, PERSONAL)

    assert {chat: [item.name for item in batch] for chat, batch in grouped.items()} == {
        "-100HU": ["a"], "-100LI": ["b"], "-100OTHER": ["c"]}


def test_score_order_survives_the_grouping():
    """The caller sorted by score."""
    items = [Item(str(n), "indeed", "Paris") for n in range(5)]
    grouped = group_by_chat(items, ROUTES, PERSONAL)
    assert [item.name for item in grouped["-100OTHER"]] == ["0", "1", "2", "3", "4"]


def test_items_without_a_source_or_location_still_go_somewhere():
    """A corpus row with neither is unusual and not a reason to drop it."""
    grouped = group_by_chat([Item("x", "", "")], ROUTES, PERSONAL)
    assert grouped == {"-100OTHER": [grouped["-100OTHER"][0]]}
