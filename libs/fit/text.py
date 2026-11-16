"""The rules a fit score reads text by, for a posting and for a person alike. No AI (CLAUDE.md §0).

Each rule is here because the evaluation set measured it (`scripts/cvlab/fit`, CHANGELOG 2026-11-16):
words are the CV reader's (`libs.documents.read.stem`, which beat a Snowball stemmer 0.644 to 0.617),
function words of the corpus's languages are dropped, so are places and postcodes, and a posting's sections
about the company, its package and the law, whose words are the rarest a posting has and say nothing about the
job.
"""
from __future__ import annotations

import re

from libs.documents.read import PLACES, fold, stem

WORD = re.compile(r"[^\W\d_][\w+#]*")

# Function words of the languages the corpus is written in, folded as text is.
STOP = frozenset("""
a an and the of to in for on with at by from as is are was were be been being our we you your will this that
it its or not have has had can all who what their they them about into over more most also other such per any
each than then there these those which while within without across up out new using use used via etc i me my
he she his her us do
der die das und mit fur den dem des ein eine einen einem einer ist sind wir sie ihr ihre zu im am von bei auf
als oder auch nicht wie wird werden sowie unser unsere unseren
az es egy hogy nem is van vagy de ha mint meg el ki be fel le al pedig itt ott ez azt amely aki valamint
illetve
w z na do sie nie jest oraz dla od po przez jak lub ze o ten ta to za przy jestes twoj nasz naszych
het een en van op te met voor dat die zijn wordt ook bij aan of niet je jouw wij ons onze naar
il lo gli le un una e di da con su per tra fra che non piu come anche nel nella dei delle del alla sono
el los las y o en por para que se su sus es mas pero muy
les une et ou du dans par sur avec qui ne pas plus sont vous nous votre vos notre nos
""".split())  # noqa: SIM905 - a word list reads best as words

# Words that say how senior and nothing else leave a title; "manager", "supervisor" and "head" say what the
# job is, and taking them out read "Site Manager" as "site" (2026-11-16).
MODIFIERS = frozenset(stem(word) for word in """
    senior sr junior jr mid entry experienced starszy mlodszy erfahrener""".split())  # noqa: SIM905
NOISE = MODIFIERS | frozenset(stem(word) for word in """
    m f d w x k mwd fmd mfd wmd remote hybrid onsite full part time contract permanent temporary temp
    freelance fixed term hourly shift shifts job jobs role position vacancy opportunity hiring urgent
    immediate start new team all genders gender gn ftc months hrs nights days zero hour hours based office
    home uk eu europe internal only""".split())  # noqa: SIM905

# Every UK-style postcode in a text, wherever it is (`read._POSTCODE` is the one an address line ends on).
_POSTCODE_ANYWHERE = re.compile(
    r"(?i)\b[a-z]{1,2}\d[a-z\d]?(?:\s*\d[a-z]{2})?\b|\b\d{2}-?\d{3}\b|\b\d{4,5}\b")


def words(text: str | None) -> list[str]:
    """The stems of the words that carry meaning, in order, with places and postcodes taken out: where a job
    is says nothing about what it is, and the site filters places on their own."""
    plain = PLACES.sub(" ", _POSTCODE_ANYWHERE.sub(" ", fold(text or "")))
    return [stem(token) for token in WORD.findall(plain) if len(token) >= 2 and token not in STOP]


# ── a posting ──────────────────────────────────────────────────────────────────────────────────

_OFFER = re.compile(
    r"(?i)^\W*(about us|about the company|who we are|company description|our company|why join|why us|"
    r"why work|what we offer|we offer|our offer|benefits|perks|what'?s in it for you|what you get|in return|"
    r"rewards|our benefits|package|equal opportunit|diversity|eeo|privacy|data protection|how to apply|"
    r"application process|oferujemy|co oferujemy|o nas|benefity|wir bieten|was wir bieten|uber uns|"
    r"deine vorteile|ihre vorteile|amit kinalunk|amit nyujtunk|rolunk|juttatas|offriamo|cosa offriamo|"
    r"chi siamo|wat bieden wij|wij bieden|over ons|wat wij bieden|ofrecemos|que ofrecemos|quienes somos|"
    r"nous offrons|ce que nous offrons|qui sommes)")
_WORK = re.compile(
    r"(?i)^\W*(responsibilit|requirement|qualification|what you('ll| will) do|what you('ll| will) bring|"
    r"about you|about the role|the role|your role|key duties|duties|main tasks|skills|experience|essential|"
    r"desirable|who you are|you have|you are|your profile|profile|obowiazki|wymagania|zakres|twoje zadania|"
    r"aufgaben|ihre aufgaben|deine aufgaben|anforderungen|profil|dein profil|ihr profil|feladat|elvaras|"
    r"amit kerunk|mansioni|requisiti|responsabilita|taken|functie|vereisten|wat ga je doen|wat vragen wij|"
    r"funciones|requisitos|missions|profil recherche|vos missions)")
# A heading is a short line.
_HEADING_CHARS = 60


def working_text(text: str | None) -> str:
    """A posting less what it says about the company, the package and the law, kept whole when that would
    leave less than a quarter of it: a heading read wrongly must not leave a job with no text."""
    keep, skipping = [], False
    for line in (text or "").splitlines():
        head = fold(line.strip())
        if len(head) <= _HEADING_CHARS and _OFFER.match(head):
            skipping = True
            continue
        if len(head) <= _HEADING_CHARS and _WORK.match(head):
            skipping = False
        if not skipping:
            keep.append(line)
    kept = "\n".join(keep)
    return kept if len(kept) >= len(text or "") / 4 else text or ""


_TITLE_CUT = re.compile(r"\s[-–—|/]\s|\s\||\|\s|,|\(|\[")  # noqa: RUF001 - the dashes titles are written with


def core_title(title: str) -> str:
    """The part of a title before a place, a shift or a sector: "Warehouse Operative - Erith" is a warehouse
    operative. The rest of a title is read with the text."""
    return _TITLE_CUT.split(title)[0] or title


_LEVELS = {
    0: "intern internship interns apprentice apprenticeship trainee student werkstudent praktikant praktikum "
       "stagista stage stagiaire gyakornok stazysta stazystka praktykant becario becaria practicas",
    1: "junior graduate jr entry mlodszy absolvent beginner",
    3: "senior sr experienced starszy erfahrener supervisor manager kierownik",
    4: "lead principal head chief director leiter vezeto responsabile",
}
_LEVEL = {stem(word): level for level, names in _LEVELS.items() for word in names.split()}
# The level of a title that names none, and of a person whose years are unknown.
MIDDLE = 2


def title_level(title: str) -> int | None:
    """How senior a title says the job is, 0 (a trainee) to 4 (a head), or None when it does not say."""
    found = [_LEVEL[stem(token)] for token in WORD.findall(fold(title)) if stem(token) in _LEVEL]
    if not found:
        return None
    return min(found) if 0 in found else max(found)


# ── languages ─────────────────────────────────────────────────────────────────────────────────

_LANGUAGE_NAMES = {
    "en": "english englisch angielski angol inglese ingles anglais engels",
    "de": "german deutsch deutschkenntnisse niemiecki nemet tedesco aleman allemand duits",
    "fr": "french franzosisch francuski francia francese frances francais frans",
    "es": "spanish spanisch hiszpanski spanyol spagnolo espanol espagnol spaans",
    "it": "italian italienisch wloski olasz italiano italien italiaans",
    "nl": "dutch niederlandisch holenderski holland olandese neerlandes neerlandais nederlands",
    "pl": "polish polnisch polski lengyel polacco polaco polonais pools",
    "hu": "hungarian ungarisch wegierski magyar ungherese hungaro hongrois hongaars",
    "pt": "portuguese portugiesisch portugalski portugal portoghese portugues portugais portugees",
    "sv": "swedish schwedisch szwedzki sved svedese sueco suedois zweeds",
    "cs": "czech tschechisch czeski cseh ceco checo tcheque tsjechisch",
    "ro": "romanian rumanisch rumunski roman rumeno rumano roumain roemeens",
    "da": "danish danisch dunski dan danese danes danois deens",
}
_LANGUAGE = {word: code for code, names in _LANGUAGE_NAMES.items() for word in names.split()}
_LANGUAGES = re.compile(r"(?<!\w)(" + "|".join(sorted(_LANGUAGE, key=len, reverse=True)) + r")(?!\w)")
_NEEDED = re.compile(
    r"(fluen|native|proficien|\bc1\b|\bc2\b|\bb2\b|business level|advanced|excellent|speak|required|must|"
    r"essential|written and spoken|spoken and written|verbal and written|kenntnisse|fliessend|"
    r"verhandlungssicher|biegl|znajomosc|nyelvtudas|nyelvismeret|ottim|buona conoscenza|vloeiend|beheers|"
    r"dominio|nivel alto|courant|maitrise|perfect)")
_OPTIONAL = re.compile(
    r"(nice to have|a plus|advantage|preferred|desirable|bonus|beneficial|mile widzian|elony|von vorteil|"
    r"wunschenswert|een pre|pluspunt|un plus|valorable|gradit|apprezzat|plus\b)")
# What a language needs around it to be asked for, either side.
_AROUND = 70
NEGATION = re.compile(r"\b(?:not|never|no|don't|dont|haven't|havent|isn't|wasn't|nem|nincs|soha|nie|nigdy|"
                      r"nicht|kein|keine|niet|geen|nooit|non|mai|nunca|ni)\b")
_BEGINNER = re.compile(r"\b(?:a1|a2|basic|beginner|elementary|limited|alapfok|alapszint|podstawow|"
                       r"grundkenntnisse|schulkenntnisse|basis|base|basico|scolaire|school)\b")


def languages_required(text: str | None) -> list[str]:
    """The languages a posting says it needs, never one it calls a plus: "fluent German", "biegła znajomość
    języka niemieckiego", "Deutschkenntnisse verhandlungssicher"."""
    folded = fold(text or "")
    needed = set()
    for found in _LANGUAGES.finditer(folded):
        around = folded[max(0, found.start() - _AROUND): found.end() + _AROUND]
        if _NEEDED.search(around) and not _OPTIONAL.search(around):
            needed.add(_LANGUAGE[found.group(1)])
    return sorted(needed)


def languages_spoken(texts: list[str]) -> set[str]:
    """The languages a person's documents name in any of the languages above, except one at a beginner's
    level or in a sentence that says they do not speak it. The CV reader knows English headings only, and read
    a Polish recruiter's "angielski — B2" as no English, which blocked every English posting for her
    (2026-11-16)."""
    spoken = set()
    for text in texts:
        for line in fold(text).splitlines():
            for part in re.split(r"[,;|·•/]", line):
                if not (NEGATION.search(part) or _BEGINNER.search(part)):
                    spoken.update(_LANGUAGE[found.group(1)] for found in _LANGUAGES.finditer(part))
    return spoken


# ── a person ──────────────────────────────────────────────────────────────────────────────────

# A line that is their contact details or a document's label, not their work.
IDENTITY = re.compile(r"@|https?://|www\.|linkedin|github|\+?\d[\d ()./-]{6,}\d|curriculum vitae|lebenslauf|"
                      r"oneletrajz|\bcv\b|\bfoto\b")
