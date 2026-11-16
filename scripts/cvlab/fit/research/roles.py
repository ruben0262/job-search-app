"""Candidate 5: what a person's role is, and what a posting's title names, read by the occupation words of
nine languages rather than by where a line sits.

**Occupation heads** (`HEADS`): the nouns that name a job in the languages the corpus is written in, from
general knowledge of each language's job titles, not from the evaluation set. A word is a head when it is one,
or when it ends in one of five letters or more ("servicetechniker", "magazijnmedewerker", "targoncavezeto"):
the compounds German, Dutch and Hungarian write titles as.

**A posting's core** (`core_words`): its title cut at the separators titles use, keeping every part that holds
a head ("POP MART - Full Time Retail Assistant" is a retail assistant; "Servicetechniker / Elektroniker" is
both), the first part when none does. **A compound's head** is its own word too, on both sides
(`split_heads`).

**A person's roles** (`role_phrases`): the short lines of a CV (twelve words or fewer, not a sentence) with a
head in them, cut to the part that holds it; and the opening words of a sentence that starts by naming their
job ("Registered General Nurse with six years ..."). Never a line of contact details, their name, a place
alone, or an employer's name with no head in it."""

from __future__ import annotations

import re

from libs.documents.read import fold, stem
from libs.fit.text import IDENTITY, NOISE, words

_HEADS = """
engineer developer programmer architect analyst scientist designer manager director lead leader chief officer
executive representative associate assistant consultant specialist coordinator administrator advisor adviser
technician nurse midwife doctor physician surgeon therapist physiotherapist pharmacist paramedic carer
caregiver teacher lecturer tutor professor instructor trainer facilitator educator accountant auditor
bookkeeper clerk cashier receptionist secretary driver chef cook waiter waitress server barista bartender
cleaner housekeeper operator operative worker labourer laborer electrician plumber mechanic welder fitter
installer joiner carpenter bricklayer plasterer painter decorator roofer glazier tiler intern trainee
apprentice owner founder partner buyer planner scheduler dispatcher controller recruiter editor writer
journalist translator interpreter researcher supervisor attendant guard porter steward librarian
photographer artist marketer strategist producer tester pilot dentist hygienist veterinarian salesperson
merchandiser underwriter actuary solicitor lawyer barrister paralegal conveyancer handler picker packer
loader courier colleague crew host hostess runner baker butcher florist hairdresser barber stylist
beautician aide orderly inspector surveyor estimator foreman machinist assembler storeman storekeeper
warehouseman gardener landscaper caretaker janitor concierge counsellor counselor psychologist clinician
practitioner sonographer radiographer optometrist optician technologist chemist biologist economist
statistician coach lifeguard chauffeur rider negotiator agent broker cashier
elektriker elektroniker mechaniker mechatroniker techniker monteur installateur schlosser schweisser tischler
schreiner maler lackierer fahrer kraftfahrer lagerist kommissionierer staplerfahrer fachkraft helfer
mitarbeiter mitarbeiterin sachbearbeiter sachbearbeiterin kaufmann kauffrau buchhalter buchhalterin berater
beraterin verkaufer verkauferin kassierer kassiererin koch kochin kellner servicekraft pfleger pflegerin
krankenschwester erzieher erzieherin lehrer lehrerin ingenieur ingenieurin entwickler entwicklerin
informatiker leiter leiterin meister meisterin assistent assistentin referent referentin disponent
disponentin planer planerin spediteur programmierer architekt architektin bauleiter polier maurer zimmerer
dachdecker klempner gartner reinigungskraft hausmeister zahnarzt arzt arztin apotheker therapeut betreuer
betreuerin rezeptionist rezeptionistin hotelfachmann hotelfachfrau backer fleischer metzger friseur friseurin
werkstudent praktikant auszubildender azubi
specjalista specjalistka kierownik kierowniczka magazynier magazynierka kierowca ksiegowa ksiegowy rekruter
rekruterka asystent asystentka konsultant konsultantka technik inzynier programista programistka kucharz
kucharka sprzedawca sprzedawczyni kasjer kasjerka spedytor spedytorka logistyk operatorka pracownik
pracowniczka doradca doradczyni przedstawiciel przedstawicielka koordynator koordynatorka analityk
analityczka menedzer dyrektor opiekun opiekunka pielegniarka pielegniarz nauczyciel nauczycielka elektryk
mechanik monter spawacz slusarz tokarz frezer kelner kelnerka barman barmanka recepcjonista recepcjonistka
sekretarka urzednik ekspert lider projektant praktykant stazysta kontroler audytor handlowiec kurier
ochroniarz sprzataczka fizjoterapeuta farmaceuta lekarz ratownik
konyvelo berszamfejto gepkezelo karbantarto muszeresz technikus vezeto munkatars ugyintezo mernok szakacs
elado sofor raktaros asszisztens tanacsado elemzo fejleszto programozo villanyszerelo lakatos hegeszto
forgacsolo apolo tanar recepcios pincer takarito szerelo gepesz fodrasz ellenor menedzser igazgato titkarno
penztaros futar gyakornok segedmunkas
medewerker medewerkster chauffeur adviseur verpleegkundige verzorgende kok verkoper verkoopster beheerder
magazijnmedewerker heftruckchauffeur elektricien boekhouder administrateur leidinggevende teamleider
teamleidster ontwikkelaar programmeur technicus begeleider leerkracht docent leraar schoonmaker beveiliger
receptioniste vrachtwagenchauffeur timmerman loodgieter schilder lasser stagiair werkvoorbereider calculator
inkoper fysiotherapeut tandarts
cuoco cuoca addetto addetta impiegato impiegata responsabile tecnico cameriere cameriera commesso commessa
magazziniere magazziniera autista ingegnere contabile operaio operaia manutentore elettricista infermiere
infermiera insegnante segretario segretaria consulente specialista direttore direttrice assistente
sviluppatore programmatore progettista operatore operatrice pizzaiolo pasticcere lavapiatti governante
facchino portiere educatore educatrice fisioterapista farmacista medico idraulico saldatore meccanico
carrellista mulettista stagista tirocinante venditore venditrice coordinatore coordinatrice analista
administrativo administrativa auxiliar ingeniero ingeniera desarrollador desarrolladora programador
programadora dependiente dependienta camarero camarera cocinero cocinera conductor conductora mozo operario
operaria enfermero enfermera profesor profesora comercial gerente jefe jefa asistente consultor consultora
coordinador coordinadora recepcionista limpiador limpiadora vigilante fontanero soldador mecanico
carretillero repartidor teleoperador teleoperadora gestor gestora becario becaria vendedor vendedora cajero
cajera farmaceutico
technicien technicienne ingenieur developpeur developpeuse vendeur vendeuse serveur serveuse cuisinier
cuisiniere magasinier magasiniere cariste operatrice infirmier infirmiere enseignant enseignante assistante
conseiller conseillere charge chargee directrice specialiste analyste coordinateur coordinatrice
receptionniste gestionnaire employe employee electricien plombier soudeur mecanicien preparateur livreur
soignant soignante educateur stagiaire alternant
"""
HEADS = frozenset(fold(word) for word in _HEADS.split())
# A compound counts when it ends in a head of at least this many letters, after at least `_PREFIX` of its own.
_LONG_HEAD, _PREFIX = 5, 3
_LONG = sorted((head for head in HEADS if len(head) >= _LONG_HEAD), key=len, reverse=True)
_TOKEN = re.compile(r"[^\W\d_][\w'+#-]*")


def head_of(token: str) -> str | None:
    """The head a folded token is or ends in, or None."""
    if token in HEADS:
        return token
    return next((head for head in _LONG if token.endswith(head) and len(token) - len(head) >= _PREFIX), None)


def has_head(text: str) -> bool:
    return any(head_of(token) for token in _TOKEN.findall(fold(text)))


def split_heads(stems: list[str], text: str) -> list[str]:
    """`stems` with the stem of every compound's head added after it: "Servicetechniker" is a "techniker"."""
    out = list(stems)
    for token in _TOKEN.findall(fold(text)):
        head = head_of(token)
        if head and head != token:
            for part in (head, token[: len(token) - len(head)]):
                found = stem(part)
                if found not in out and found not in NOISE:
                    out.append(found)
    return out


# ── a posting's title ─────────────────────────────────────────────────────────────────────────────

_PARTS = re.compile(r"\s[-–—|/]\s|\s\||\|\s|\s/|/\s|,|\(|\)|\[|\]|\s[-–—]|[-–—]\s|:")  # noqa: RUF001


def core_parts(title: str) -> list[str]:
    """The parts of a title that name the job: those with a head in them, or the first part."""
    parts = [part.strip() for part in _PARTS.split(title or "") if part and part.strip()]
    named = [part for part in parts if has_head(part)]
    return named or parts[:1] or [title or ""]


def core_words(title: str, *, compounds: bool = False) -> list[str]:
    """The words of a title's core, once each, less the words of seniority and contract."""
    text = " ".join(core_parts(title))
    found = [word for word in dict.fromkeys(words(text)) if word not in NOISE]
    return split_heads(found, text) if compounds else found


# ── a person's roles ──────────────────────────────────────────────────────────────────────────────

_MONTHS = (
    r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|"
    r"oct(?:ober)?|nov(?:ember)?|dec(?:ember)?"
)
_DATES = re.compile(
    rf"(?i)\b(?:{_MONTHS})\b\.?|\b\d{{1,2}}\s*[/.]\s*\d{{4}}|\b(?:19|20)?\d\d\b|\b(?:present|current|now|today|"
    r"ongoing|since|until|heute|obecnie|jelenleg|heden|oggi|actualidad|actuel|aktuell|nu)\b"
)
_LINE_PARTS = re.compile(r"\s[-–—|·•]\s|\s*\|\s*|,\s+|\s+(?:at|@|bei|w|u|presso|en|chez|bij|for)\s+|\t+|:\s")  # noqa: RUF001
_SENTENCE = re.compile(r"(?<=[.!?;])\s+")
_BULLET = re.compile(r"^\s*(?:[•●▪◦○■►▸‣⁃∙·*✓✔➢➤]|[-–—]\s)")  # noqa: RUF001
# An employer, not a job: a company's legal form.
_COMPANY = re.compile(
    r"(?i)\b(?:gmbh|ltd|limited|plc|llc|inc|kft|zrt|nyrt|bt|sp\. ?z ?o\.? ?o|s\.?a\.?|b\.?v\.?|"
    r"n\.?v\.?|s\.?r\.?l|s\.?p\.?a|ag|kg|e\.?v|srl|spa|sas|sarl|oy|ab|as)\b\.?"
)
# A duty, not a job: a line that opens with a verb ("Train our apprentice", "Reported changes to the
# coordinator"). English only; the other languages' duty lines are seldom short and named for a job.
_DUTY = frozenset("""write plan train work worked manage managed lead led support supported run ran deliver
built build coordinate coordinated handle handled prepare prepared assist assisted help helped maintain ensure
report reported complete completed own cut answer answered brief act oversee supervise supervised line liaise
mentor mentored teach taught""".split())  # noqa: SIM905 - a word list reads best as words
# Words in -ed that describe a person, not a duty: "Registered General Nurse".
_STATES = frozenset(("registered", "qualified", "certified", "experienced", "skilled", "licensed",
                     "chartered", "accredited", "advanced", "talented", "dedicated", "motivated", "organised",
                     "organized"))
# A role line is a short line, not a sentence.
_ROLE_WORDS = 12
# A role part is at most this many words.
_PART_WORDS = 8
# How far into a sentence its head may be for its opening to be read as a role ("Registered General Nurse"),
# and how near the top of a document a sentence must be: a summary, not a duty.
_OPENING, _TOP_LINES = 6, 8


def _clean(line: str) -> str:
    return re.sub(r"^[\s•●▪◦○■►▸‣⁃∙·*✓✔➢➤\-–—]+", "", line).strip()  # noqa: RUF001


def _duty(tokens: list[str]) -> bool:
    first = fold(tokens[0]) if tokens else ""
    return first in _DUTY or (first.endswith("ed") and len(first) > 4 and first not in _STATES)


def role_phrases(text: str, name: str | None = None, *, sentences: bool = True) -> list[str]:
    """The phrases of a CV that name a role, in the order written (most recent first, in most CVs)."""
    found: list[str] = []
    folded_name = fold(name) if name else None
    at_line = 0
    for raw in text.splitlines():
        line = _clean(raw)
        if not line:
            continue
        at_line += 1
        if _BULLET.match(raw) or IDENTITY.search(fold(line)) or (folded_name and folded_name in fold(line)):
            continue
        plain = _DATES.sub(" ", line)
        tokens = _TOKEN.findall(plain)
        if 0 < len(tokens) <= _ROLE_WORDS and not line.rstrip().endswith("."):
            for part in _LINE_PARTS.split(plain):
                part_tokens = _TOKEN.findall(part or "")
                if (
                    part_tokens
                    and len(part_tokens) <= _PART_WORDS
                    and has_head(part)
                    and not _duty(part_tokens)
                    and not _COMPANY.search(part)
                ):
                    found.append(part.strip(" -–—|·•()"))  # noqa: RUF001
        elif sentences and at_line <= _TOP_LINES:
            for sentence in _SENTENCE.split(line)[:1]:
                opening = _TOKEN.findall(sentence)[:_OPENING]
                at = next((i for i, token in enumerate(opening) if head_of(fold(token))), None)
                if at is not None and not _duty(opening):
                    found.append(" ".join(opening[: at + 1]))
    return found
