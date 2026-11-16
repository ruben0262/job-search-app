"""Where the current score's order is wrong, on the 27 seen: of each person's postings two or more points
apart in their labels, the ones the score puts the wrong way round, and what readers said of the posting it
put too high (the "over" one) and the one it put too low (the "under" one), sorted into reasons by their
words.

    PYTHONPATH=. python -m scripts.cvlab.fit.research.discordant
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict

from scripts.cvlab.fit.evaluate import GAP
from scripts.cvlab.fit.research import harness as h

REASONS = (
    (
        "requirement: licence/registration/qualification",
        r"licen[cs]|registra|registered|qualifi|certif|degree|"
        r"cscs|sia|nmc|acca|cima|solicitor|meister|diploma|ticket",
    ),
    (
        "requirement: years",
        r"\byears?\b.*(?:requir|ask|need|want|short)|(?:requir|ask|need|want)\w*.*\byears?\b",
    ),
    (
        "level: above them",
        r"step up|steps? above|senior|more senior|level above|manag\w* role|lead\w* role|head of|"
        r"director|above (?:his|her|their) level|two steps|several steps",
    ),
    (
        "level: below them",
        r"below (?:his|her|their)|beneath|overqualif|junior for|entry.level for|too junior",
    ),
    ("language", r"language|fluent|german|dutch|polish|hungarian|italian|spanish|french|english"),
    (
        "shared word, other work",
        r"shares? (?:the|a|its|his|her)? ?(?:word|title)|nothing to do|different (?:job|work|"
        r"trade|role)|not (?:his|her|their) (?:work|trade)|little in common|unrelated",
    ),
)


def reason(text: str) -> str:
    text = text.lower()
    return next((name for name, pattern in REASONS if re.search(pattern, text)), "other")


def main() -> None:
    data = h.load()
    judged = h.baseline(data)
    over, under, by_person = Counter(), Counter(), defaultdict(int)
    examples = defaultdict(list)
    total = 0
    for name in h.SEEN:
        keys = [k for k in judged if k[0] == name]
        for i, a in enumerate(keys):
            for b in keys[i + 1 :]:
                if abs(data.truth[a] - data.truth[b]) < GAP:
                    continue
                total += 1
                high, low = (a, b) if data.truth[a] > data.truth[b] else (b, a)
                if judged[low] >= judged[high]:
                    by_person[name] += 1
                    r_over = reason(data.rows[low]["why_a"] + " " + (data.rows[low]["blocker_a"] or ""))
                    r_under = reason(data.rows[high]["why_a"] + " " + (data.rows[high]["blocker_a"] or ""))
                    over[r_over] += 1
                    under[r_under] += 1
                    if len(examples[r_over]) < 4:
                        examples[r_over].append(
                            (
                                name,
                                data.postings[low[1]]["title"][:50],
                                data.truth[low],
                                data.postings[high[1]]["title"][:50],
                                data.truth[high],
                            )
                        )
    wrong = sum(by_person.values())
    print(f"{wrong} of {total} clear pairs the wrong way round ({wrong / total:.1%})")
    print("\nwhat readers said of the posting put too HIGH:")
    for name, n in over.most_common():
        print(f"  {name:50s} {n:4d}  {n / wrong:.0%}")
    print("\nwhat readers said of the posting put too LOW:")
    for name, n in under.most_common():
        print(f"  {name:50s} {n:4d}  {n / wrong:.0%}")
    print("\nby person:", dict(sorted(by_person.items(), key=lambda item: -item[1])))
    for name, rows in examples.items():
        print(f"\n{name}")
        for row in rows:
            print(f"   {row[0]:17s} over: {row[1]} ({row[2]})  under: {row[3]} ({row[4]})")


if __name__ == "__main__":
    main()
