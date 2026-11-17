"""What a posting requires that a person may not have, read by rules in the corpus's languages: a
credential (a licence, registration, card or professional qualification), being on its staff, or being a
student. A score is capped when one is missing (`libs.fit.CEILING`): the rubric's own rule for a blocker, and
28% of the clear pairs the score had in the wrong order put a posting too high that named one (CHANGELOG
2026-11-16, night).

**Required or a plus.** A posting's lines are read in order. A heading opens a section that requires
("Requirements", "Essential", "Wymagania", "Anforderungen", "Elvárások", "Vereisten", "Requisiti",
"Requisitos"...), that would like ("Desirable", "Nice to have", "Mile widziane", "Von Vorteil",
"Előny"...), or that is about something else ("Responsibilities", "Benefits"...). A line's own words win over
its section's: "ideally", "a plus", "preferred" make it a plus; "must", "essential", "required", "mandatory"
make it required. A line that offers the thing ("we will train you", "or willing to obtain", "we fund your
licence") never requires it. The title requires what it names.

**Credentials** (`CREDENTIALS`): from general knowledge of the European job market, written once for a posting
and a person alike, before any held-out person was read. A person holds one when their documents name it
outside a sentence that says they do not; "studying for", "part-qualified", "working towards" is half held,
and counts as held: the rubric does not cap someone on the way to it. A credential not in the list never caps.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

from libs.documents.read import fold
from libs.fit.roles import role_phrases

# ── credentials ───────────────────────────────────────────────────────────────────────────────────

CREDENTIALS: dict[str, str] = {
    # driving and transport
    "driving licence": r"driving licen[cs]e|driver'?s licen[cs]e|fuhrerschein|prawo jazdy|jogositvany|"
    r"rijbewijs|patente (?:di guida|b\b)|carnet de conducir|permiso de conducir|permis (?:de conduire|b\b)",
    "licence c/c1": r"\bc1? licen[cs]e|class 2\b|cat(?:egory)?\.? c1?\b(?!\s*\+)|klasse c1?\b(?!e)|"
    r"kat(?:egorii|\.)? c1?\b(?!\s*\+)|\bc kategori",
    "licence c+e": r"c\s*\+\s*e\b|\bc/e\b|class 1\b|\bce licen|cat(?:egory)?\.? ce\b|klasse ce\b|\bartic\b|"
    r"articulated|\bhgv 1\b|\blgv 1\b",
    "hgv/lgv": r"(?:\bhgv\b|\blgv\b|\bvrachtwagen|\blkw\b|ciezarow|kamion)[^.\n]{0,25}(?:licen|class|cat|"
    r"driv|fuhrerschein|rijbewijs|prawo jazdy)|(?:licen[cs]e|driver|driving)[^.\n]{0,15}(?:\bhgv\b|"
    r"\blgv\b)",
    "bus licence d": r"\bpcv\b|\bd licen[cs]e|cat(?:egory)?\.? d\b|class d\b|klasse d\b|"
    r"kat(?:egorii|\.)? d\b",
    "driver cpc": r"driver cpc|\bdcpc\b|\bcpc\b|kod 95|code 95|kwalifikacj\w* wstepn|\bgki\b|"
    r"berufskraftfahrer\w*qualifikation|\bbkrfqg\b",
    "adr": r"\badr\b",
    "digital tachograph card": r"digi(?:tal)? ?tacho|tachograph card|driver card|fahrerkarte|karta kierowcy",
    "forklift": r"fork ?lift|\bflt\b|counterbalance|reach truck|\brtitb\b|stapler|gabelstapler|"
    r"flurforderzeug|wozk\w* widlow|\budt\b|targonca|heftruck|reachtruck|carrellist|muletto|carretiller|"
    r"carretilla elevadora|chariot elevateur|\bcaces\b",
    "plant / access": r"\bipaf\b|\bpasma\b|\bcpcs\b|\bnpors\b|crane (?:operator )?licen|kranschein|suwnic|"
    r"darukezel|futodaru",
    # health and care
    "nursing registration": r"\bnmc\b|nursing and midwifery council|\bnmbi\b|nursing and midwifery board|"
    r"registered (?:general |adult |mental health |children'?s |learning disabilit\w* )?"
    r"nurse|\brgn\b|\brmn\b|\brnld\b|\brsc?n\b|nurse registration|pflegefachkraft|"
    r"examiniert\w* (?:pflege|kranken)|prawo wykonywania zawodu pielegn|big-?registrat",
    "hcpc": r"\bhcpc\b|health and care professions council",
    "gmc": r"\bgmc\b|general medical council",
    "gdc": r"\bgdc\b|general dental council",
    # A pharmacist and a pharmacy technician are both on the GPhC's register: a line says which, or else the
    # job's title or the person's own roles do (`posting`, `person`); "gphc" is the register named alone. As
    # one, a technician read as holding what an "Advanced Pharmacist" post asked (2026-11-17).
    "pharmacist registration": r"(?:\bgphc\b|general pharmaceutical council)[^\n]{0,80}\bpharmacist|"
    r"\bpharmacist\b[^\n]{0,80}(?:\bgphc\b|general pharmaceutical council)|registered pharmacist|"
    r"albo (?:professionale )?dei farmacisti|abilitazione (?:alla professione )?(?:di )?farmacista",
    "pharmacy technician registration": r"(?:\bgphc\b|general pharmaceutical council)[^\n]{0,80}technician|"
    r"\btechnician\b[^\n]{0,80}(?:\bgphc\b|general pharmaceutical council)|registered pharmacy technician",
    "gphc": r"\bgphc\b|general pharmaceutical council",
    # Checking a pharmacy's work before it leaves, two qualifications: the dispensary's final accuracy check
    # (an ACPT), and the pre- and in-process check of aseptic and technical services, which the first is not.
    "accredited checking technician": r"(?:accredited|accuracy) checking (?:pharmacy )?technician|\bacpt\b",
    "in-process checker": r"in-?process (?:accuracy )?checker|pre[- ]and in-?process check",
    # "MRCVS" after a vet's name is the membership itself.
    "rcvs": r"\bm?rcvs\b|royal college of veterinary",
    "social work registration": r"social work england|registered social worker|\bcoru\b|\bsssc\b|\bniscc\b|"
    r"social care wales",
    "care qualification": r"(?:level|nvq|qcf|rqf) ?[2-5] (?:diploma |certificate )?in (?:adult )?health and "
    r"social care|diploma in (?:adult )?health and social care|\bqqi\b level [56]",
    "prescribing": r"prescrib\w* qualif|non-medical prescrib|independent prescriber|\bnmp\b|\bv300\b",
    # money and law
    "acca": r"\bacca\b",
    "cima": r"\bcima\b|\bcgma\b|\bacma\b",
    "aca/icaew": r"\baca\b|\bicaew\b|chartered accountant",
    "aat": r"\baat\b",
    "accountancy qualification": r"qualified accountant|merlegkepes|bilanzbuchhalter|steuerberater|"
    r"wirtschaftsprufer|bieg\w* rewident|registered auditor",
    "actuary": r"qualified actuary|\bifoa\b|institute and faculty of actuaries",
    "solicitor": r"qualified solicitor|solicitor of england|\bsra\b|admitted (?:as a )?solicitor|\bpqe\b|"
    r"\bnq\b|qualified lawyer|admission to practi[cs]e|\bsqe ?[12]?\b|\blpc\b|rechtsanwalt|adwokat|"
    r"radca prawn|ugyved\b|avvocato|abogad[oa] colegiad|avocat au barreau|advocaat",
    "cilex": r"\bcilex\b|chartered legal executive",
    "insurance qualification": r"\bcii\b|\bacii\b|\bdipcii\b|cert ?cii",
    "cipd": r"\bcipd\b",
    "cfa": r"\bcfa\b",
    # teaching
    "teaching qualification": r"\bqts\b|qualified teacher status|\bpgce\b|\bqtls\b|teaching qualification|"
    r"lehramt|referendariat|uprawnienia pedagog|pedagogus vegzettseg|abilitazione "
    r"all'insegnamento",
    # building, trades, engineering
    "cscs": r"\bcscs\b|\bccdo\b|\becs card\b",
    "smsts/sssts": r"\bsmsts\b|\bsssts\b",
    "gas safe": r"gas safe|\bacs\b|\bccn1\b|\bcodnco1\b|\bcocn1\b",
    "electrical 18th edition": r"18th edition|bs ?7671|17th edition|\bam2\b|approved electrician|\bjib\b",
    # The one who signs off an electrical contractor's work for NICEIC or NAPIT. Not the inspection and
    # testing qualification (C&G 2391) alone: readers did not hold it against a final-year apprentice whose
    # Level 3 covers inspection and testing (2026-11-17).
    "electrical qualified supervisor": r"(?:act|acting|serve|work) as (?:the |our |a )?(?:nominated )?"
    r"qualified supervisor|qualified supervisor[^\n]{0,40}(?:niceic|napit|electrical)|"
    r"(?:niceic|napit)[^\n]{0,40}qualified supervisor",
    "electrical qualification (de/pl/hu)": r"elektrofachkraft|uprawnienia (?:sep|elektr)|\bsep [ed]\b|"
    r"villanyszerelo (?:vegzettseg|bizonyitvany|szakkepesites)|erosaram",
    "f-gas": r"f-?gas|kaltemittel|\bktm\b",
    "nebosh/iosh": r"\bnebosh\b|\biosh\b",
    "meister/techniker": r"\bmeister|staatlich gepruft|techniker(?:schule|abschluss)",
    "trade certificate (de)": r"gesell\w*|abgeschlossene\w* (?:berufs)?ausbildung|ausbildung (?:als|zum|zur)|"
    r"facharbeiter",
    "trade certificate (hu)": r"\bokj\b|szakkepesites|szakmunkas bizonyitvany|szakmunkas vegzettseg",
    "welding certificate": r"\b9606\b|\ben ?287\b|coded welder|welding (?:cert|ticket)|spawacz\w* (?:uprawn|"
    r"certyf)|hegeszto (?:vizsga|minosites)",
    "vehicle technician qualification": r"\bmot tester|\bimi\b|vehicle technician (?:nvq|level 3)|\bnvq\b "
    r"(?:level )?[23]\b[^.\n]{0,30}(?:vehicle|motor|light)|kfz-mechatroniker",
    "aircraft licence": r"part[- ]?66|\bb1\b licen|\bb2\b licen|aircraft maintenance licen",
    "chartered engineer": r"\bceng\b|\bieng\b|\beit\b|chartered engineer|incorporated engineer",
    # security
    # Not "sia" alone: an Italian verb.
    "sia licence": r"\bsia (?:licen|badge|card|door|security|front|cctv|registered|approved)|"
    r"security industry authority|door supervisor licen|close protection licen|\b34a\b|"
    r"sachkundeprufung|licencj\w* pracownika ochrony|vagyonor",
    # Held now, not "eligible for", which is a rule about who may be hired, like the right to work.
    "security clearance": r"(?:active|current|existing|live|valid|hold|holds|held)\b[^.\n]{0,20}"
    r"(?:\bsc\b|\bdv\b|\bctc\b|security) clear",
    # food
    "food hygiene": r"food (?:safety|hygiene) (?:level|cert)|\bhaccp\b",
    # the Netherlands
    "vca": r"\bvca\b",
    "bhv": r"\bbhv\b",
}
_CREDENTIALS = {name: re.compile(pattern) for name, pattern in CREDENTIALS.items()}
# The ones an employer arranges or a short course gives, never a reason not to hire.
OBTAINABLE = frozenset({"food hygiene", "bhv", "digital tachograph card", "nebosh/iosh", "cscs"})
# What holding one means holding too.
IMPLIES = {
    "licence c+e": {"licence c/c1", "hgv/lgv", "driving licence"},
    "licence c/c1": {"hgv/lgv", "driving licence"},
    "hgv/lgv": {"driving licence"},
    "bus licence d": {"driving licence"},
    "aca/icaew": {"accountancy qualification"},
    "acca": {"accountancy qualification"},
    "cima": {"accountancy qualification"},
    "pharmacist registration": {"gphc"},
    "pharmacy technician registration": {"gphc"},
}
# A register named with which of its trades: that trade's, not the whole register's.
_NARROWER = {"gphc": ("pharmacist registration", "pharmacy technician registration")}
# A job whose title is a regulated one requires its registration wherever the posting names it, not only
# under "Requirements": "As a band 5 registered nurse, you will ..." in a "Neonatal Staff Nurse" post,
# "Membership of the Royal College of Veterinary Surgeons" in a list under no heading the reader knows
# (2026-11-17).
_TITLED = {
    "nursing registration": re.compile(
        r"\b(?:staff|registered|charge|community|district|theatre|ward|neonatal|paediatric|pediatric|"
        r"mental health|practice|school|scrub|recovery|specialist|clinical) nurse\b|\bnurse (?:practitioner|"
        r"specialist|consultant)|\bward sister\b|\bmidwi"),
    "rcvs": re.compile(r"veterinary surgeon|\bvet surgeon|veterinarian"),
    "pharmacist registration": re.compile(r"\bpharmacist|\bfarmacist"),
}
# What doing the work needs, required wherever the posting names it: acting as an electrical contractor's
# qualified supervisor.
_DUTIES = frozenset({"electrical qualified supervisor"})


def implied(names: set[str]) -> set[str]:
    out = set(names)
    for name in names:
        out |= IMPLIES.get(name, set())
    return out


# A car licence is asked of field roles of every kind and readers never held it against anyone; it counts for
# a job of driving only.
_DRIVING_JOB = re.compile(
    r"driv|courier|deliver|chauffeur|fahrer|kierowc|sofor|autista|conductor|repartidor|"
    r"livreur|\bvan\b|\bhgv\b|\blgv\b|\bbus\b"
)


def credentials_in(folded: str) -> set[str]:
    found = {name for name, pattern in _CREDENTIALS.items() if pattern.search(folded)}
    return {name for name in found if not any(narrower in found for narrower in _NARROWER.get(name, ()))}


# ── who may apply ─────────────────────────────────────────────────────────────────────────────────

_INTERNAL = re.compile(
    r"\binternal (?:applicants |candidates )?only\b|^\W*internal\W*$|\binternal\W*$|"
    r"\bonly (?:open|available) to (?:current|existing) \w*\s?(?:staff|employees|colleagues)|"
    r"\bopen to (?:current|existing) (?:staff|employees) only|tylko dla (?:obecnych )?pracownik|"
    r"nur fur interne|csak belso"
)
_STUDENT = re.compile(
    r"currently enrolled|must be (?:a )?(?:current |registered )?student|"
    r"immatrikuliert|eingeschrieben\w* student|werkstudent|status studenta|aktiv hallgatoi|"
    r"hallgatoi jogviszony|studente iscritt|estudiante matriculad|convenio de practicas|"
    r"stage curricolare|ingeschreven student"
)


# ── a posting's lines: required, a plus, or neither ───────────────────────────────────────────────

_REQ_HEAD = re.compile(
    r"^\W*(requirements?|essential\w*|essential criteria|what you('ll| will) need|"
    r"what we('re| are) looking for|you (will )?(need|have|bring)|must haves?|qualifications?|"
    r"skills and experience|experience required|"
    r"your profile|profile|about you|who you are|what you('ll| will)? bring|the ideal candidate|person "
    r"specification|criteria|wymagania|nasze wymagania|oczekujemy|czego oczekujemy|wymagamy|anforderungen|"
    r"dein profil|ihr profil|was du mitbringst|was sie mitbringen|das bringst du mit|voraussetzungen|"
    r"elvarasok?|elvarasaink|amit elvarunk|requisiti|requisiti richiesti|cosa cerchiamo|il candidato ideale|"
    r"profilo|vereisten|functie-?eisen|wat vragen wij|wat wij vragen|jouw profiel|wie ben jij|requisitos|"
    r"requisitos minimos|lo que buscamos|perfil|profil recherche|votre profil|competences requises)\b"
)
_PREF_HEAD = re.compile(
    r"^\W*(desirable|nice[- ]to[- ]haves?|preferred|bonus|advantageous|it would be great|beneficial|"
    r"mile widziane|dodatkowym atutem|atutem bedzie|von vorteil|wunschenswert|elony|elonyt jelent|pluspunten|"
    r"een pre|gradit\w*|titolo preferenziale|valorable|se valorara|souhaite|un plus|atout)\b"
)
_OTHER_HEAD = re.compile(
    r"^\W*(responsibilit|duties|key duties|main duties|what you('ll| will) do|the role|your role|"
    r"about (us|the)|benefits|perks|what we offer|we offer|our offer|why join|how to apply|zakres obowiazkow|"
    r"obowiazki|twoje zadania|oferujemy|aufgaben|deine aufgaben|ihre aufgaben|wir bieten|feladatok|feladat|"
    r"amit kinalunk|mansioni|offriamo|taken|wat bieden|wat ga je doen|funciones|ofrecemos|missions|"
    r"vos missions)"
)
_MUST = re.compile(
    r"\b(must|essential|required|requirement|mandatory|necessary|need to (?:have|hold)|you will need|"
    r"you('ll| will) (?:need|hold|have)|minimum|valid|full|hold(?:s|ing)? (?:a|an|the|current|valid)|"
    r"wymagan|warunek|niezbedn|koniecz|erforderlich|voraussetzung|zwingend|notwendig|zwingende|"
    r"elengedhetetlen|feltetel|szukseges|elvaras|vereist|noodzakelijk|verplicht|in bezit van|richiest|"
    r"indispensabil|obbligatori|imprescindible|requerid|necesari|obligatoire|indispensable|exige)\b"
)
_PLUS = re.compile(
    r"\b(ideally|desirable|preferred|preferably|advantage\w*|a plus|nice to have|bonus|beneficial|an asset|"
    r"would be great|not essential|not required|mile widzian|atut|von vorteil|wunschenswert|idealerweise|"
    r"elony|pluspunt|een pre|gradit|preferibil|preferenzial|valorable|se valorara|souhaite|un plus|apprezzat)"
)
_OFFERED = re.compile(
    r"(we will|we'll|we can|we) (?:train|provide|fund|pay for|support you|put you through|help you)|"
    r"training (?:will be |is )?(?:provided|given)|"
    r"willing(?:ness)? to (?:obtain|gain|work towards|undertake)|or (?:be )?working towards|or willing|"
    r"opportunity to (?:gain|obtain|achieve)|full training|"
    r"(?:we|wir) (?:offer|bieten|finanzieren)|zapewniamy szkolen|oplacimy|biztositjuk|betalen|financiamos"
)
_HEAD_CHARS = 60


def lines_by_kind(text: str | None) -> list[tuple[str, str]]:
    """Each line of a posting, folded, with "required", "plus" or "other"."""
    out, section = [], "none"
    for raw in (text or "").splitlines():
        line = fold(raw.strip())
        if not line:
            continue
        if len(line) <= _HEAD_CHARS:
            if _PREF_HEAD.match(line):
                section = "plus"
                continue
            if _REQ_HEAD.match(line):
                section = "required"
                continue
            if _OTHER_HEAD.match(line):
                section = "other"
                continue
        if _OFFERED.search(line):
            kind = "offered"
        elif _PLUS.search(line):
            kind = "plus"
        elif _MUST.search(line) or section == "required":
            kind = "required"
        else:
            kind = "plus" if section == "plus" else "other"
        out.append((line, kind))
    return out


def posting(title: str, text: str | None) -> dict:
    """What a posting requires, stored with its features: `req`, the credentials it requires as groups of
    alternatives ("ACA, ACCA or CIMA qualified" is one group, met by any of the three); `staff`, open only to
    its own staff; `student`, only to students."""
    folded_title = fold(title or "")
    driving = bool(_DRIVING_JOB.search(folded_title))

    def kept(found: set[str]) -> set[str]:
        found = found - OBTAINABLE
        return found if driving else found - {"driving licence"}

    titled = {name for name, pattern in _TITLED.items() if pattern.search(folded_title)}
    # The GPhC named alone, in a pharmacy job: the register of the trade the title names.
    trade = ("pharmacy technician registration" if "technician" in folded_title
             else "pharmacist registration" if "pharmacist registration" in titled else None)

    def asked(found: set[str]) -> set[str]:
        return (found - {"gphc"}) | {trade} if trade and "gphc" in found else found

    groups: list[set[str]] = [{name} for name in asked(kept(credentials_in(folded_title)))]
    for line, kind in lines_by_kind(text):
        found = kept(credentials_in(line))
        if kind == "other":
            # Where the posting says what the work is: only its title's own registration, or what doing the
            # work needs.
            found = {name for name in found if name in titled or name in _DUTIES}
        elif kind != "required":
            continue
        if found:
            groups.append(asked(found))
    # A group within another is the same requirement asked twice.
    unique = [group for group in groups if not any(other < group for other in groups)]
    whole = fold(text or "")
    return {"req": [list(group) for group in sorted({tuple(sorted(group)) for group in unique})],
            "staff": bool(_INTERNAL.search(folded_title) or _INTERNAL.search(whole[:3000])),
            "student": bool(_STUDENT.search(folded_title) or _STUDENT.search(whole))}


# ── a person ──────────────────────────────────────────────────────────────────────────────────────

_LACK = re.compile(
    r"\b(?:no|not|never|don'?t|do not|haven'?t|have not|without|lack|nie|nigdy|kein\w*|nicht|"
    r"nincs|soha|geen|niet|nooit|non|nessun\w*|sin|sans)\b"
)
_PARTIAL = re.compile(
    r"studying|working towards|part[- ]qualified|in progress|currently (?:sitting|taking|"
    r"completing|doing|attending)|trainee|expected|exams? (?:left|to go|remaining)|w trakcie|"
    r"in ausbildung|kepzes|tanfolyam|folyamatban|in corso|en curso|en cours|bezig met|"
    r"in opleiding|\bcourse\b|\bkurs\b"
)
_SENTENCE_OR_LINE = re.compile(r"(?<=[.!?;])\s+|\n")


def _pharmacy_trade(documents: Mapping[str, Sequence[str]]) -> str | None:
    """Which of the GPhC's trades their CVs name as a role of theirs: a technician's first, since a pharmacist
    seldom writes "technician" in a role line and a technician often works beside a pharmacist."""
    phrases = [fold(phrase) for kind in ("cv", "linkedin") for text in documents.get(kind) or ()
               for phrase in role_phrases(text)]
    if any("technician" in phrase and "pharmac" in phrase for phrase in phrases):
        return "pharmacy technician registration"
    return "pharmacist registration" if any("pharmacist" in phrase for phrase in phrases) else None


def person(documents: Mapping[str, Sequence[str]]) -> dict:
    """What a person holds, from every document they gave: `has`, the credentials held whole or half (what
    holding one means holding too included), and `student`, whether they say they are one."""
    held, half = set(), set()
    student = False
    for texts in documents.values():
        for text in texts or ():
            for sentence in _SENTENCE_OR_LINE.split(fold(text)):
                found = credentials_in(sentence)
                if found:
                    if _LACK.search(sentence):
                        continue
                    (half if _PARTIAL.search(sentence) else held).update(found)
                if re.search(r"\b(?:current(?:ly)? (?:a )?student|i am (?:a )?student|studentka|student\w* "
                             r"(?:at|of|der|na)|hallgato)\b", sentence):
                    student = True
    # The GPhC named alone ("GPhC 2087654"): the register of the trade their own roles are.
    for named in (held, half):
        if "gphc" in named and not named & set(_NARROWER["gphc"]) and (trade := _pharmacy_trade(documents)):
            named.add(trade)
    # Named once as in progress, it is in progress, whatever else is said of it.
    half_ = implied(half)
    held_ = implied(held - half) - half_
    return {"has": sorted(held_ | half_), "student": student}
