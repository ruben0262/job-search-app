"""The phrases one model writes into different people's letters: its formula, which a recruiter who reads many
letters learns to see as the model's rather than the person's. A reading aid beside the AI-language list in
`services.docgen.checks`, not a check: a formula worth sending back is added there.

    python scripts/cvlab/formulas.py <folder> [<folder> ...]   # scripts/cvlab/out/cvs/<model>/<version>

Run from the repository root. English letters only; four words or more, said by at least a fifth of the people
and by four of them; a phrase inside a longer one said as often is shown as the longer one.
"""
from __future__ import annotations

import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
from scripts.cvlab.run import letter_text
from services.docgen import checks

# What every letter has by design: the greeting and sign-off the prompt asks for.
EXPECTED = re.compile(r"^(?:dear hiring manager|yours sincerely|kind regards|yours faithfully)")


def formulas(letters: dict[str, str], *, shortest: int = 4, longest: int = 7) -> list[tuple[int, str]]:
    """`letters` by the person they are for (one person's two letters count once): each phrase and how many
    people's letters it is in, most first."""
    people: dict[str, set[str]] = {}
    for person, text in letters.items():
        words = re.findall(r"[a-z']+", text.lower().replace("\u2019", "'"))
        for size in range(shortest, longest + 1):
            for at in range(len(words) - size + 1):
                people.setdefault(" ".join(words[at:at + size]), set()).add(person)
    floor = max(4, -(-len({p.split("/")[0] for p in letters}) // 5))
    common = sorted(((len({p.split("/")[0] for p in who}), phrase) for phrase, who in people.items()),
                    reverse=True)
    kept: list[tuple[int, str]] = []
    for count, phrase in common:
        if count < floor or EXPECTED.match(phrase):
            continue
        if not any(phrase in longer and count == said for said, longer in kept):
            kept = [(said, other) for said, other in kept if not (other in phrase and said == count)]
            kept.append((count, phrase))
    return sorted(kept, reverse=True)


def english_letters(folder: pathlib.Path) -> dict[str, str]:
    """A bench folder's English letters by `<person>/<posting>`."""
    found = {}
    for path in sorted(folder.glob("*__*__*__*.cover.html")):
        text = letter_text(path.read_text(encoding="utf-8"))
        if checks.is_english(text):
            person, _, posting = path.name.split("__")[:3]
            found[f"{person}/{posting}"] = text
    return found


if __name__ == "__main__":
    for each in sys.argv[1:]:
        letters = english_letters(pathlib.Path(each))
        print(f"\n{each}: {len(letters)} English letters, {len({k.split('/')[0] for k in letters})} people")
        for count, phrase in formulas(letters)[:25]:
            print(f"  {count:3d}  {phrase}")
