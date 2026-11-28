"""A look: the documents one wording wrote for a few people, printed whole for a reader, with what code can
count of each. How a wording is judged since 2026-11-27 (owner: "evaluation falls on you only with a couple of
profile examples"): the session reads every document against the targets written down for it, and the counts
say which to read first. `CLAUDE.md` §3c; the method is the `prompt-lab` skill.

    scripts/cvlab/look.sh cover live haiku                    # write the look with one writer, then read it
    python scripts/cvlab/look.py jobs cover <folder>          # the look's postings, as the bench's --jobs-dir
    python scripts/cvlab/look.py read cover <results.json> <variant> --out <the bench's --out> [--quiet]

A look is `looks/<name>.json`: its pairs of a panel person and a posting fixture (`jobs/`, or
`looks/postings/` for one that is in no other set), and its counts: a pattern, where it is looked for (the
first paragraph, the last two, all of it) and whether a good document has some, none or any. A count is a
reading aid, never a mark.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parents[1]))
from scripts.cvlab import run  # noqa: E402 - after the path

LOOKS = ROOT / "looks"
PANEL = ROOT / "panel"
# The owner's anti-AI-language skill, where this machine has it. Not in the repository: without it the column
# says "not run", which is not "pass" (`CLAUDE.md` §3b).
CHECKER = pathlib.Path.home() / ".claude/skills/no-ai-language/scripts/check.py"
_SUFFIX = {"cover": ".cover.html", "cv": ".html"}


def look(name: str) -> dict:
    path = LOOKS / f"{name}.json"
    if not path.exists():
        raise SystemExit(f"no look {name!r} at {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def fixture(slug: str) -> dict:
    for root in (ROOT / "jobs", LOOKS / "postings"):
        if (path := root / f"{slug}.json").exists():
            return json.loads(path.read_text(encoding="utf-8"))
    raise SystemExit(f"no posting fixture {slug!r} under jobs/ or looks/postings/")


def jobs(name: str, folder: pathlib.Path) -> list[pathlib.Path]:
    """The look's postings written as a `--jobs-dir`: each for the person the look pairs it with, under a slug
    of its own where that is not the person its fixture was made for, so one posting can be read for two."""
    folder.mkdir(parents=True, exist_ok=True)
    written = []
    for person, slug in look(name)["pairs"]:
        posting = fixture(slug)
        if posting.get("candidate") != person:
            posting |= {"candidate": person, "slug": f"{slug}--{person}", "group": f"{slug}--{person}"}
        path = folder / f"{posting['slug']}.json"
        path.write_text(json.dumps(posting, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        written.append(path)
    return written


def paragraphs(page: str) -> list[str]:
    """A written letter's paragraphs between the greeting and the sign-off, a bold run as `**…**`."""
    marked = re.sub(r"(?s)<strong>(.*?)</strong>", r"**\1**", page)
    return run.letter_text(marked).split("\n\n")[1:-1]


def counted(parts: list[str], counts: list[dict]) -> dict[str, tuple[int, bool]]:
    """Each count of the look over one document: how many, and whether that is what a good one has."""
    where = {"first": parts[:1], "last": parts[-2:], "all": parts}
    found = {}
    for count in counts:
        hits = len(re.findall(count["pattern"], "\n".join(where[count["in"]])))
        found[count["name"]] = (hits, {"some": hits > 0, "none": hits == 0, "any": True}[count["good"]])
    return found


def reads_as_ai(text: str, own_words: str) -> str:
    """The skill's checker on one document: "pass", its hard hits, or "not run". Single words of the posting
    and of the person's own record are theirs to use ("unmatched items" is a payments developer's work)."""
    if not CHECKER.exists():
        return "not run"
    allow = ",".join(sorted(set(re.findall(r"[A-Za-z][\w'-]{3,}", own_words))))
    argv = [sys.executable, str(CHECKER), "--json", "--allow", allow, "-"]
    done = subprocess.run(argv, input=text, capture_output=True, text=True,  # noqa: S603 - the owner's script
                          check=False)
    try:
        found = next(iter(json.loads(done.stdout).values()))
    except (ValueError, StopIteration):
        return "not run"
    hard = [hit["text"] for hit in found["hits"] if hit["severity"] == "HARD"]
    return "pass" if found["passes"] else f"FAIL {hard}"


def read(name: str, results: pathlib.Path, variant: str, out: pathlib.Path, *, quiet: bool = False) -> int:
    """Print the look: a line a document, then each document whole. Returns how many it found."""
    spec = look(name)
    rows = [row for row in json.loads(results.read_text(encoding="utf-8")) if row["variant"] == variant]
    rows.sort(key=lambda row: (row["candidate"], row["job"]))
    documents = []
    for row in rows:
        kept = f"{row['candidate']}__{variant}__{row['job']}__{row['sample']}{_SUFFIX[spec['kind']]}"
        pages = list(out.glob(f"{row['model']}*/{row['prompt_version']}/{kept}"))
        if len(pages) != 1:
            raise SystemExit(f"{len(pages)} kept documents named {kept} by {row['model']} under {out}")
        page = pages[0].read_text(encoding="utf-8")
        parts = paragraphs(page) if spec["kind"] == "cover" else run.build.checks.text(page).split("\n")
        slug = row["job"].removesuffix(f"--{row['candidate']}")
        own = " ".join([json.dumps(fixture(slug)), *(path.read_text(encoding="utf-8")
                                                     for path in (PANEL / row["candidate"]).glob("*.txt"))])
        documents.append((row, parts, counted(parts, spec["counts"]), reads_as_ai("\n\n".join(parts), own)))
    names = [count["name"] for count in spec["counts"]]
    print(" | ".join(["person", "posting", "words", *names, "sent back", "AI list", "pages", "reads as AI",
                      "tokens out", "seconds"]))
    for row, parts, found, checker in documents:
        faults = row.get("fault_list") or []
        print(" | ".join([row["candidate"], row["job"][:28], str(len(" ".join(parts).split())),
                          *(f"{hits}{'' if good else ' !'}" for hits, good in (found[n] for n in names)),
                          f"{len(faults)} {'; '.join(fault[:60] for fault in faults)}".strip(),
                          str(row.get("ai_language")), str(row.get("pages")), checker,
                          str(row.get("tokens_out")), str(row.get("seconds"))]))
    for row, parts, _, _ in () if quiet else documents:
        print(f"\n===== {row['candidate']} | {row['job']}")
        for part in parts:
            print("  ¶", part)
    return len(documents)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("action", choices=("jobs", "people", "read"))
    parser.add_argument("look")
    parser.add_argument("rest", nargs="*", help="jobs: <folder>; read: <results.json> <variant>")
    parser.add_argument("--out", default=str(ROOT / "out" / "look"), help="the bench's --out")
    parser.add_argument("--quiet", action="store_true", help="the lines only, no documents")
    args = parser.parse_args()
    if args.action == "people":
        print(",".join(dict.fromkeys(person for person, _ in look(args.look)["pairs"])))
        return
    if args.action == "jobs":
        print(f"{len(jobs(args.look, pathlib.Path(args.rest[0])))} posting(s) in {args.rest[0]}")
        return
    results, variant = args.rest
    if not read(args.look, pathlib.Path(results), variant, pathlib.Path(args.out), quiet=args.quiet):
        raise SystemExit(f"no document of {variant} in {results}")


if __name__ == "__main__":
    main()
