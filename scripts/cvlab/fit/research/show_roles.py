"""The role phrases `roles.role_phrases` reads from each seen person's CVs, and a few posting titles' cores.

PYTHONPATH=. python -m scripts.cvlab.fit.research.show_roles"""

import json

from libs.fit.text import NOISE, words
from scripts.cvlab.fit.build import PANEL, documents
from scripts.cvlab.fit.research import harness as h
from scripts.cvlab.fit.research.roles import core_words, role_phrases

for name in h.SEEN:
    candidate = json.loads((PANEL / name / "candidate.json").read_text(encoding="utf-8"))
    phrases = [
        p
        for filename, text in documents(name)
        if filename.startswith(("cv", "linkedin"))
        for p in role_phrases(text, candidate.get("name"))
    ]
    roles = list(dict.fromkeys(w for p in phrases for w in words(p) if w not in NOISE))
    print(f"{name:17s} {phrases[:8]}\n{'':17s} -> {roles[:20]}")
for title in (
    "POP MART - Full Time Retail Assistant",
    "Tesco Shift Leader - Days - Nottingham Castle",
    "Servicetechniker / Elektroniker (m/w/d) - IoT & Gebäudetechnik",
    "PM259339 - Healthcare Support Worker",
    "One Stop - Customer Service Assistant",
    "Retail - MCS - Warehouse/Yard Supervisor",
    "Junior Transport Planner & Operations Support",
    "Associate / Senior Associate Mechanical Engineer (Data)",
    "Recruitment Day - Transport & Warehouse - Sat Nov 7th",
    "Baliemedewerker / Magazijnmedewerker | DYKA",
    "Gyógyszercsomagoló gépkezelő (Evergreen)",
    "Data Warehouse Engineer",
):
    print(f"{title[:60]:60s} -> {core_words(title)} | compounds {core_words(title, compounds=True)}")
