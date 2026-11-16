"""Freeze the fit evaluation set: for each made-up person in `scripts/cvlab/panel`, real postings drawn from
the list their search would show on the site, and the packets two raters judge them from.

    python -m scripts.cvlab.fit.build freeze      # reads the local corpus (MONGODB_URI, MONGODB_DB)
    python -m scripts.cvlab.fit.build packets     # writes scripts/cvlab/out/fit/packets/<rater>/<person>.md

**Their list, as the site makes it.** A person's search (`people.json`) becomes a config, and the postings are
those `libs.tenant.filters.candidate_conditions` and `libs.matching.has_any` admit: open, whole text read, in
their places and languages, with one of their words in the title or the text. That is what a score will be
asked to put in order, so that is what it is judged on.

**Drawn, not picked.** Up to `TITLED` postings with one of their words in the title and the rest of
`PER_PERSON` with it only in the text, at random from a seed named for the person, so a rerun on the same
corpus draws the same ones and nobody chose them by looking at a score. The same role listed in several cities
is one posting (company and title, without the place). A panel person's two bench postings
(`scripts/cvlab/jobs`) are added.

The frozen files are the record: `postings.jsonl` (each posting's text as stored) and `pairs.jsonl` (who is
judged against what, and how it was drawn). Nothing here runs on the site.
"""
from __future__ import annotations

import json
import pathlib
import random
import sys

FIT = pathlib.Path(__file__).resolve().parent
PANEL = FIT.parent / "panel"
BENCH_JOBS = FIT.parent / "jobs"
PACKETS = FIT.parent / "out" / "fit" / "packets"
PER_PERSON, TITLED = 18, 10
# What a rater reads of a posting: longer ones are cut there (the median is about 3,700 characters).
READ_CHARS = 6_000
FIELDS = ("title", "company", "location", "country", "language", "workplace", "source", "url", "description",
          "first_seen_at", "posted_at")


def people() -> dict[str, dict]:
    return {name: spec for name, spec in json.loads((FIT / "people.json").read_text(encoding="utf-8")).items()
            if not name.startswith("_")}


def config(spec: dict) -> dict:
    """Their search as My search stores it."""
    return {"sections": [{"keywords": spec["words"], "exclude_keywords": spec.get("red", []),
                          "locations": spec["places"]}], "languages": spec["languages"]}


def group(row: dict) -> str:
    from libs.documents.read import fold  # noqa: PLC0415
    return f"{fold(row.get('company') or '')}|{fold(row['title'])}"


def freeze() -> None:
    from libs.corpus.freshness import platform_windows  # noqa: PLC0415
    from libs.corpus.words import words_in  # noqa: PLC0415
    from libs.db import database  # noqa: PLC0415
    from libs.matching import green_words, has_any  # noqa: PLC0415
    from libs.tenant.filters import UserFilters, candidate_conditions  # noqa: PLC0415

    db = database()
    windows = platform_windows(db)
    # Incremental: a person already drawn keeps their postings, whatever the corpus holds now. Drawn again
    # from today's corpus, the labels already read would be of postings no longer in the set.
    postings: dict[int, dict] = {row["id"]: row for row in _existing("postings.jsonl")}
    pairs = _existing("pairs.jsonl")
    drawn_before = {pair["person"] for pair in pairs}
    for name, spec in people().items():
        if name in drawn_before:
            continue
        cfg = config(spec)
        words = green_words(cfg)
        filters = UserFilters.from_config(cfg, max_age_hours=windows.max_age_hours,
                                          source_min_ages=windows.source_min_ages)
        rows = list(db.corpus.find({"$and": [*candidate_conditions(filters), has_any(words)]},
                                   dict.fromkeys(FIELDS, 1), sort=[("_id", 1)]))
        strata: dict[str, list[dict]] = {"title": [], "text": []}
        seen = set()
        for row in rows:
            if (key := group(row)) not in seen:
                seen.add(key)
                strata["title" if words_in(row["title"], words) else "text"].append(row)
        draw = random.Random(f"fit-{name}")  # noqa: S311 - a seeded draw, not a secret
        titled = draw.sample(strata["title"], min(TITLED, len(strata["title"])))
        text = draw.sample(strata["text"], min(PER_PERSON - len(titled), len(strata["text"])))
        if len(titled) + len(text) < PER_PERSON:  # too few in the text: more titles instead
            rest = [row for row in strata["title"] if row not in titled]
            titled += draw.sample(rest, min(PER_PERSON - len(titled) - len(text), len(rest)))
        chosen = [("title", row) for row in titled] + [("text", row) for row in text]
        for path in sorted(BENCH_JOBS.glob("*.json")):
            bench = json.loads(path.read_text(encoding="utf-8"))
            if bench.get("candidate") == name and bench["id"] not in {row["_id"] for _, row in chosen}:
                chosen.append(("bench", {"_id": bench["id"], **{field: bench.get(field)
                                                                for field in FIELDS}}))
        for stratum, row in chosen:
            postings[row["_id"]] = {"id": row["_id"], "group": group(row),
                                    **{field: _plain(row.get(field)) for field in FIELDS}}
            pairs.append({"person": name, "job": row["_id"], "drawn": stratum})
        print(f"{name:18s} list {len(rows):5d} ({len(strata['title'])} titled, "
              f"{len(strata['text'])} in the text,"
              f" once each)  drawn {len(chosen)}")
    _write(FIT / "postings.jsonl", [postings[key] for key in sorted(postings)])
    _write(FIT / "pairs.jsonl", pairs)
    print(f"{len(pairs)} pairs, {len(postings)} postings")


def _plain(value):
    return value.isoformat() if hasattr(value, "isoformat") else value


def _existing(name: str) -> list[dict]:
    return read_jsonl(FIT / name) if (FIT / name).exists() else []


def _write(path: pathlib.Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def read_jsonl(path: pathlib.Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def documents(name: str) -> list[tuple[str, str]]:
    """A person's files as the site would hold them: each `.txt` in their folder, named for its kind."""
    return [(path.name, path.read_text(encoding="utf-8").strip())
            for path in sorted((PANEL / name).glob("*.txt"))]


def packets(names: list[str] | None = None) -> None:
    """One file a person a rater: their documents, then their postings, in an order of the rater's own so that
    neither reads them in the same order as the other. `names`: only these people."""
    postings = {row["id"]: row for row in read_jsonl(FIT / "postings.jsonl")}
    pairs = read_jsonl(FIT / "pairs.jsonl")
    for rater in ("a", "b"):
        folder = PACKETS / rater
        folder.mkdir(parents=True, exist_ok=True)
        for name in names or people():
            jobs = [pair["job"] for pair in pairs if pair["person"] == name]
            random.Random(f"order-{rater}-{name}").shuffle(jobs)  # noqa: S311 - a seeded order, not a secret
            parts = [f"# Person: {name}\n", "Everything they gave the site:\n"]
            parts += [f"## {filename}\n\n{text}\n" for filename, text in documents(name)]
            parts.append(f"# Their {len(jobs)} postings\n")
            for job in jobs:
                row = postings[job]
                text = row.get("description") or ""
                cut = len(text) > READ_CHARS
                parts.append(
                    f"## Posting {job}: {row['title']}\n\n"
                    f"Company: {row.get('company') or '-'} · Location: {row.get('location') or '-'} · "
                    f"Written in: {row.get('language') or 'unknown'}\n\n"
                    f"{text[:READ_CHARS].strip()}{' […the rest is cut]' if cut else ''}\n")
            (folder / f"{name}.md").write_text("\n".join(parts), encoding="utf-8")
    print(f"packets for {len(names or people())} people in {PACKETS}")


RATINGS = FIT.parent / "out" / "fit" / "labels"
# Two readings this far apart are not averaged: a third reading is taken, and the label is the middle one.
DISPUTED = 3


def _readings(rater: str) -> dict[tuple[str, int], dict]:
    return {(row["person"], int(row["job"])): row
            for path in sorted((RATINGS / rater).glob("*.jsonl")) for row in read_jsonl(path)}


def disputes() -> None:
    """The pairs the two raters read three or more apart, each with both reasons and the posting, for a third
    reading written to `out/fit/labels/third/disputes.jsonl` in the rubric's format."""
    a, b = _readings("a"), _readings("b")
    postings = {row["id"]: row for row in read_jsonl(FIT / "postings.jsonl")}
    out = []
    for key in sorted(a.keys() & b.keys()):
        if abs(a[key]["fit"] - b[key]["fit"]) >= DISPUTED:
            row = postings[key[1]]
            out.append(f"## {key[0]} · posting {key[1]}: {row['title']} ({row.get('company')})\n"
                       f"a {a[key]['fit']} ({a[key].get('blocker')}): {a[key]['why']}\n"
                       f"b {b[key]['fit']} ({b[key].get('blocker')}): {b[key]['why']}\n\n"
                       f"{(row.get('description') or '')[:READ_CHARS]}\n")
    path = FIT.parent / "out" / "fit" / "disputes.md"
    path.write_text("\n".join(out), encoding="utf-8")
    print(f"{len(out)} disputed pairs of {len(a.keys() & b.keys())} in {path}")


def merge() -> None:
    """`labels.jsonl`: each pair's two readings, a third where they were `DISPUTED` apart, and the label."""
    a, b = _readings("a"), _readings("b")
    third = _readings("third")
    rows = []
    for pair in read_jsonl(FIT / "pairs.jsonl"):
        key = (pair["person"], pair["job"])
        first, second = a[key], b[key]
        row = {**pair, "a": first["fit"], "b": second["fit"], "third": None,
               "label": (first["fit"] + second["fit"]) / 2,
               "blocker_a": first.get("blocker"), "blocker_b": second.get("blocker"),
               "why_a": first["why"], "why_b": second["why"]}
        if third.get(key, {}).get("override"):
            # A reading against the rubric's own rule (`rubric.md`, the note of 2026-11-16), made by both
            # readers: the third reading is the label, and says why.
            row.update(third=third[key]["fit"], why_third=third[key]["why"], label=float(third[key]["fit"]))
        elif abs(first["fit"] - second["fit"]) >= DISPUTED:
            if key not in third:
                raise SystemExit(f"{key} was read {first['fit']} and {second['fit']}: "
                                 "it needs a third reading")
            row.update(third=third[key]["fit"], why_third=third[key]["why"],
                       label=float(sorted((first["fit"], second["fit"], third[key]["fit"]))[1]))
        rows.append(row)
    _write(FIT / "labels.jsonl", rows)
    print(f"{len(rows)} labels, {sum(row['third'] is not None for row in rows)} decided by a third reading")


if __name__ == "__main__":
    if sys.argv[1] == "packets":
        packets(sys.argv[2].split(",") if len(sys.argv) > 2 else None)
    else:
        {"freeze": freeze, "disputes": disputes, "merge": merge}[sys.argv[1]]()
