"""What a company says of itself, kept once a company for the writer of a cover letter."""
from __future__ import annotations

import uuid
from datetime import timedelta

from libs.corpus import companies
from libs.corpus.texts import store
from libs.db import insert, now
from libs.db.models import Company, CorpusJob

ABOUT = "Acme builds cargo bicycles in Leeds for couriers and city councils. We are forty people."
ROLE = "You will count stock every week and keep the yard safe for everyone on the shift."


def _posting(db, company: str, text: str) -> None:
    job = insert(db, CorpusJob(url=f"https://example.com/{uuid.uuid4()}", source="test", title="Operator",
                               company=company))
    store(db, job.id, text)


def test_a_companys_own_words_are_read_from_under_the_heading_that_names_it():
    """Owner, 2026-11-27: a letter says why this employer, from something true about it. A posting says what
    its company is under "About us", "Who we are" or the company's name, in English or not, and that part
    ends where the next part begins. What a posting says about the role is not about the company."""
    posting = f"Warehouse Operator\n\nAbout us\n{ABOUT}\n\nThe role\n{ROLE}\n\nWhat we offer\nA fair rota."

    assert companies.about_in(posting) == ABOUT
    assert companies.about_in(posting.replace("About us", "About Acme"), "Acme") == ABOUT
    assert companies.about_in(posting.replace("About us", "Über uns")) == ABOUT
    assert companies.about_in(f"About the role\n{ROLE}\n\nRequirements\nA licence.") == ""
    long = companies.about_in("Who we are\n" + "We count stock for shops across the north. " * 40)
    assert 0 < len(long) <= companies.ABOUT_MAX and long.endswith(".")


def test_what_a_company_says_is_gathered_from_its_postings_once_and_kept(db):
    """One posting of a company says what it is and another, shorter, does not: a letter for either is given
    it. It is gathered once and read from its row after, so no letter pays for it twice, and gathered again
    when the row is old. A company whose postings share only a paragraph of their own has that."""
    _posting(db, "Acme Ltd", f"About us\n{ABOUT}\n\nThe role\n{ROLE}")
    _posting(db, "Acme Ltd", f"The role\n{ROLE}\n\nRequirements\nA forklift licence.")
    _posting(db, "Northwind", f"About us\nNorthwind sells wind.\n\nThe role\n{ROLE}")

    assert companies.about(db, "Acme Ltd") == ABOUT
    kept = db[Company.COLLECTION].find_one({"_id": "acme ltd"})
    assert (kept["name"], kept["about"], kept["postings"]) == ("Acme Ltd", ABOUT, 2)

    db[CorpusJob.COLLECTION].delete_many({"company": "Acme Ltd"})
    assert companies.about(db, "ACME  ltd") == ABOUT, "read from its row, whatever became of its postings"
    assert companies.about(db, "Acme Ltd", at=now() + companies.GATHERED_FOR + timedelta(days=1)) == ""
    assert companies.about(db, None) == "" and companies.about(db, "Nobody Heard Of") == ""

    shared = "We run forty depots between Leeds and Hull, and every one of our drivers is home each night."
    _posting(db, "Dale Haulage", f"{shared}\n\nYou will drive a rigid on multi-drop rounds in Leeds.")
    _posting(db, "Dale Haulage", f"{shared}\n\nYou will plan the next day's rounds from the Hull office.")
    assert companies.about(db, "Dale Haulage") == shared
