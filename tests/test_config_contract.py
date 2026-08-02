

def test_the_form_and_the_schema_agree_on_how_many_places_may_be_picked():
    """**They disagreed, and the form was the one that let you through.**

    The picker offers about twenty options across remote scopes, regions and cities, and the schema
    accepted five. Any honest answer to "tick everything that applies" was rejected after the tick,
    by a message naming a count rather than a choice.

    Pinned because the two live in different languages and nothing else would notice them drifting.
    """
    import pathlib
    import re

    from libs.contracts.config import MAX_LOCATIONS_PER_USER

    source = pathlib.Path("web/src/pages/onboarding/ConfigStep.tsx").read_text(encoding="utf-8")
    found = re.search(r"const MAX_LOCATIONS = (\d+);", source)
    assert found, "the form must declare the bound it enforces"
    assert int(found.group(1)) == MAX_LOCATIONS_PER_USER
