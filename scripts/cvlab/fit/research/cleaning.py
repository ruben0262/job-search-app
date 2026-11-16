"""Candidate 8, cleaner text on both sides: the function words of the corpus's languages that `text.STOP`
lacks (German "ich", "mich", "du"; Polish, Hungarian, Dutch, Italian, Spanish and French pronouns and
auxiliaries), a company's legal form ("GmbH", "Kft", "Sp. z o.o."), the lines of a CV that are personal data
or a consent to processing it ("Geburtsdatum", "Familienstand", "Data urodzenia", "Születési idő", "Data di
nascita", "D.Lgs. 196/2003", "RODO"), and more of the headings a posting's package is under ("Das bieten wir",
"Co oferujemy").

`patched(...)` switches them on inside a `with`, for the readers in `libs.fit` and in `variants`, so the rest
of the harness is unchanged; outside it, nothing differs."""

from __future__ import annotations

import contextlib
import re

import libs.fit as fit_module
import libs.fit.text as text_module
from libs.documents.read import fold

MORE_STOP = frozenset(fold(word) for word in """
ich mich mir du dich dir dein deine deinen deinem deiner deines er es ihm ihn ihnen euch uns unserem unserer
bist hast hat haben habe hab kann kannst konnen musst mussen muss soll sollst sollte wirst wurde sein seine
seinen ihrem ihren ihres zum zur vom beim ins aus nach uber unter vor durch ohne um bis man noch nur sehr
schon gerne so wenn dann damit dass ob was wer wo welche welcher welches dieser diese dieses jeder jede jedes
alle allen aller mehr mochtest mochte freuen
ale jak sa byc bedzie moze takze tez czy jego jej ktory ktora ktore ktorych ktorym pod nad bez we tak tylko
juz jeszcze bardzo twoje twoja twoich nasza nasze naszym naszej
vagyunk vagyok lesz lehet kell kerunk keresunk szamara reszere altal mellett utan elott kozott alatt szerint
jij hun hij zij ze uw kan kunnen moet moeten wil willen heeft hebben hebt er als maar om dan zo nog wel al
lo la gli ed nei negli dello alle al agli ai ha hanno essere avere si ci vi ti mi tu noi voi loro suo sua
suoi sue nostro nostra tuo tua questo questa quello quella
unos unas del al con como sin sobre entre tu tus nuestro nuestra nuestros ser estar tiene tienen hay
des au aux ce cette ces est etre avoir il elle ils elles on
gmbh kg co ltd limited kft zrt nyrt bt srl spa bv nv llc inc plc ag sp zoo
""".split())  # noqa: SIM905 - a word list reads best as words

PERSONAL = re.compile(
    r"geburtsdatum|geburtsort|geboren am|familienstand|staatsangehorigkeit|anschrift|wohnort|"
    r"pers[oö]nliche daten|data urodzenia|miejsce urodzenia|stan cywilny|obywatelstwo|adres zamieszkania|"
    r"szuletesi|szuletett|csaladi allapot|allampolgarsag|lakcim|anyja neve|data di nascita|luogo di nascita|"
    r"nazionalita|stato civile|residenza|codice fiscale|fecha de nacimiento|lugar de nacimiento|nacionalidad|"
    r"estado civil|geboortedatum|geboorteplaats|nationaliteit|burgerlijke staat|woonplaats|date of birth|"
    r"\bd\.?o\.?b\b|place of birth|nationality|marital status|date de naissance|situation familiale|"
    r"trattamento dei (?:miei )?dati|d\.? ?lgs|196/2003|\bgdpr\b|\brodo\b|wyrazam zgode|przetwarzanie moich|"
    r"hozzajarul|adatkezel|datenschutz|privacy"
)
MORE_OFFER = (
    r"|das bieten wir|wir bieten dir|deine benefits|unsere benefits|unser angebot|was dich erwartet|"
    r"darauf kannst du dich freuen|was wir dir bieten|wat wij jou bieden|arbeidsvoorwaarden|"
    r"co zyskujesz|ti offriamo|te ofrecemos|avantages|your benefits|rewards and benefits|"
    r"what's in it for you"
)
BOILERPLATE = re.compile(r"browser does not support|audio element|enable javascript|cookie")


@contextlib.contextmanager
def patched(stop: bool = True, personal: bool = True, offer: bool = True):
    """The readers with the cleaning switched on, restored after: in every module that holds them by name."""
    from scripts.cvlab.fit.research import roles as roles_module  # noqa: PLC0415
    from scripts.cvlab.fit.research import variants as variants_module  # noqa: PLC0415

    holders_identity = (text_module, fit_module, roles_module)
    holders_working = (text_module, fit_module, variants_module)
    saved_stop, saved_offer = text_module.STOP, text_module._OFFER
    saved_identity = [module.IDENTITY for module in holders_identity]
    saved_working = [module.working_text for module in holders_working]
    try:
        if stop:
            text_module.STOP = text_module.STOP | MORE_STOP
        if personal:
            identity = re.compile(text_module.IDENTITY.pattern + "|" + PERSONAL.pattern)
            for module in holders_identity:
                module.IDENTITY = identity
        if offer:
            text_module._OFFER = re.compile(text_module._OFFER.pattern[:-1] + MORE_OFFER + ")")
            original = saved_working[0]

            def working_text(text):
                kept = "\n".join(
                    line for line in (text or "").splitlines() if not BOILERPLATE.search(fold(line))
                )
                return original(kept)

            for module in holders_working:
                module.working_text = working_text
        yield
    finally:
        text_module.STOP, text_module._OFFER = saved_stop, saved_offer
        for module, value in zip(holders_identity, saved_identity, strict=True):
            module.IDENTITY = value
        for module, value in zip(holders_working, saved_working, strict=True):
            module.working_text = value
