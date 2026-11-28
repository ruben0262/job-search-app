"""What a company says of itself, kept once a company for the writer of a cover letter."""
from __future__ import annotations

import uuid
from datetime import timedelta
from types import SimpleNamespace

from libs.corpus import companies
from libs.corpus.texts import store
from libs.db import insert, now
from libs.db.models import Company, CorpusJob
from services.docgen import build, run

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
    it. It is gathered once and read from its row after, for good (owner, 2026-11-28: "keep company data
    always, even if its 30 days or older"), so no letter pays for it twice. A company whose postings share
    only a paragraph of their own has that."""
    _posting(db, "Acme Ltd", f"About us\n{ABOUT}\n\nThe role\n{ROLE}")
    _posting(db, "Acme Ltd", f"The role\n{ROLE}\n\nRequirements\nA forklift licence.")
    _posting(db, "Northwind", f"About us\nNorthwind sells wind.\n\nThe role\n{ROLE}")

    assert companies.about(db, "Acme Ltd") == ABOUT
    kept = db[Company.COLLECTION].find_one({"_id": "acme ltd"})
    assert (kept["name"], kept["about"], kept["postings"]) == ("Acme Ltd", ABOUT, 2)

    db[CorpusJob.COLLECTION].delete_many({"company": "Acme Ltd"})
    assert companies.about(db, "ACME  ltd") == ABOUT, "read from its row, whatever became of its postings"
    assert companies.about(db, "Acme Ltd", at=now() + timedelta(days=365)) == ABOUT, "a year on, still kept"
    assert companies.about(db, None) == "" and companies.about(db, "Nobody Heard Of") == ""

    shared = "We run forty depots between Leeds and Hull, and every one of our drivers is home each night."
    _posting(db, "Dale Haulage", f"{shared}\n\nYou will drive a rigid on multi-drop rounds in Leeds.")
    _posting(db, "Dale Haulage", f"{shared}\n\nYou will plan the next day's rounds from the Hull office.")
    assert companies.about(db, "Dale Haulage") == shared


HAGER = ("Hager supplies electrical installation products for homes, offices and factories, from consumer "
         "units to cable management, made and shipped from Telford.")


class _Searching:
    """A signed-in model that searches the web for the call that asks: answers with what the test queued."""

    searches = True

    def __init__(self, reply: str):
        self.reply, self.calls = reply, []

    def complete_text(self, **call):
        self.calls.append(call)
        return self.reply


def _job(company: str) -> SimpleNamespace:
    return SimpleNamespace(id=7, title="Warehouse Lead Operator", company=company, location="Telford",
                           description=f"Warehouse Lead Operator at {company}. You lead the morning shift.")


def test_an_employer_its_postings_say_nothing_of_is_looked_up_on_its_own_website_once_and_kept_for_all(db):
    """Owner, 2026-11-28: where nothing is known of a company, the first letter for it reads the company's
    own website, and "if another user then requests it, its an easy lookup in our db". The page is kept only
    from a site named for the company; whatever was found, nobody asks again; an API key, which cannot search
    here, is never asked."""
    _posting(db, "Hager UK", f"The role\n{ROLE}")
    first = _Searching(f'<company_note source="https://hager.co.uk/about-us">{HAGER} Note: I could only see '
                       "this page through search-result excerpts.</company_note>")

    assert run._company(db, _job("Hager UK"), client=first, model="m") == HAGER
    call = first.calls[0]
    assert call["search"] is True and call["system"] == build.LOOK_UP_SYSTEM
    assert "Hager UK" in call["messages"][0]["content"]
    kept = db[Company.COLLECTION].find_one({"_id": "hager uk"})
    assert (kept["about"], kept["source"], kept["searched"]) == (HAGER, "https://hager.co.uk/about-us", True)

    later = _Searching("never asked")
    assert run._company(db, _job("Hager UK"), client=later, model="m") == HAGER and later.calls == []

    _posting(db, "Northwind", f"The role\n{ROLE}")
    elsewhere = _Searching(f'<company_note source="https://uk.linkedin.com/company/northwind">{HAGER}'
                           "</company_note>")
    assert run._company(db, _job("Northwind"), client=elsewhere, model="m") == ""
    assert run._company(db, _job("Northwind"), client=elsewhere, model="m") == ""
    assert len(elsewhere.calls) == 1, "asked once, though nothing was kept"

    _posting(db, "Tokenhouse", f"The role\n{ROLE}")
    leaking = _Searching('<company_note source="https://tokenhouse.example/about">Tokenhouse makes signs for '
                         "shops and pubs across the north of England, by hand. sk-ant-oat01-"
                         "AbCdEfGhIjKlMnOpQrStUvWxYz0123456789</company_note>")
    assert run._company(db, _job("Tokenhouse"), client=leaking, model="m") == "", "nothing shaped like a key"

    _posting(db, "Dale Haulage", f"The role\n{ROLE}")
    api_key = SimpleNamespace(complete_text=lambda **_call: (_ for _ in ()).throw(AssertionError("asked")))
    assert run._company(db, _job("Dale Haulage"), client=api_key, model="m") == ""
    assert companies.to_look_up(db, "Dale Haulage"), "left for a letter whose model can search"


def test_only_a_page_on_a_site_named_for_the_company_is_its_own():
    """A rule, so that what another company of a like name says, or what a board or an encyclopaedia says of
    this one, is never kept as what it says of itself."""
    assert companies.own_site("Merlin Entertainments", "https://www.merlinentertainments.biz/about-us")
    assert companies.own_site("1dea", "https://1dea.pl/")
    assert companies.own_site("Rushcliffe Care Limited", "https://www.rushcliffecare.co.uk/about")
    assert not companies.own_site("Hager UK", "https://uk.linkedin.com/company/hager")
    assert not companies.own_site("Mediq UK", "https://en.wikipedia.org/wiki/Mediq")
    assert not companies.own_site("Acme Ltd", "https://www.northwind.example/about"), "another company's site"
    assert not companies.own_site("Acme Ltd", "http://192.168.1.5/"), "no name in an address"
    assert not companies.own_site("Acme Ltd", ""), "no page"
    assert companies.own_site("Betsi Cadwaladr University Health Board", "https://bcuhb.nhs.wales/about-us/")
    assert companies.own_site("J. RETTENMAIER & SÖHNE GmbH + Co KG", "https://www.jrs.de/en/about")
    assert not companies.own_site("Acme Bicycle Company", "https://www.abc.example/"), "initials of two words"
