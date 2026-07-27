"""Regenerate the dedup golden file by executing the REFERENCE implementation.

    python tests/golden/generate_dedup_golden.py [--reference d:/Projects/job-search]

This is the provenance of `dedup_cases.json`, and the reason T2 means anything: the expected values
are not what someone believed the rules were, they are what
`job-search/pipeline/lib/store.py` actually returns today. Reading the rules and writing the
expectations by hand would encode the reading, and a subtle misreading would then be "confirmed" by
a test that agrees with it.

**Run this only to add cases or to deliberately re-baseline.** Regenerating it to make a failing
test pass would defeat the whole point — if the port changes an output, that is the finding, not a
stale golden.

The reference is a separate repository and is not a dependency of this one. It is not present in
CI, which is why the golden file is committed rather than generated during the test run.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Cases chosen to pin the semantics that are load-bearing and easy to break silently, not to
# maximise coverage. Each group targets a rule that a reimplementation would plausibly get wrong.
CANONICAL_URL_CASES = [
    # host folding: the two Greenhouse hosts are the same board
    "https://job-boards.greenhouse.io/acme/jobs/123",
    "https://boards.greenhouse.io/acme/jobs/123",
    "https://JOB-BOARDS.greenhouse.io/Acme/jobs/123",
    # www stripping
    "https://www.lever.co/acme/abc-123",
    "https://lever.co/acme/abc-123",
    # tracking noise: query and fragment dropped
    "https://boards.greenhouse.io/acme/jobs/123?gh_src=abcd",
    "https://boards.greenhouse.io/acme/jobs/123?utm_source=x&utm_campaign=y",
    "https://boards.greenhouse.io/acme/jobs/123#apply",
    # trailing slash
    "https://boards.greenhouse.io/acme/jobs/123/",
    "https://boards.greenhouse.io/acme/jobs/123//",
    # case: boards are case-insensitive on tokens
    "https://BOARDS.greenhouse.io/ACME/Jobs/123",
    # scheme variants
    "http://boards.greenhouse.io/acme/jobs/123",
    # real-world shapes from the other tiers
    "https://www.linkedin.com/jobs/view/4012345678/?trackingId=abc%3D",
    "https://jobs.ashbyhq.com/acme/9f8e7d6c-1234-4321-abcd-000000000000",
    "https://acme.workday.com/en-US/careers/job/Remote/Engineer_R-123",
    # degenerate input — must not raise
    "",
    "   ",
    "not-a-url",
    "/relative/path",
    "https://",
    "ftp://boards.greenhouse.io/acme/jobs/123",
]

# (title, company, location)
CONTENT_KEY_CASES = [
    # baseline
    ("Senior Backend Engineer", "Acme", ""),
    # parenthesised groups are stripped wholesale
    ("Senior Backend Engineer (Remote)", "Acme", ""),
    ("Senior Backend Engineer (m/f/d)", "Acme", ""),
    ("Senior Backend Engineer (EU)", "Acme", ""),
    # title noise words dropped
    ("Remote Senior Backend Engineer", "Acme", ""),
    ("Fully Remote Senior Backend Engineer", "Acme", ""),
    ("Senior Backend Engineer - Hybrid", "Acme", ""),
    # company legal suffixes stripped, but never down to nothing
    ("Engineer", "Acme Inc", ""),
    ("Engineer", "Acme Inc.", ""),
    ("Engineer", "Acme Ltd", ""),
    ("Engineer", "Acme GmbH", ""),
    ("Engineer", "Acme Kft.", ""),
    ("Engineer", "Acme Holdings Limited", ""),
    # a single-token company that IS a suffix word must survive (the loop guards on len > 1)
    ("Engineer", "Co", ""),
    ("Engineer", "AB", ""),
    # LOCATION IS PART OF THE KEY — same role in two cities is two jobs, never collapsed
    ("Backend Engineer", "Acme", "London"),
    ("Backend Engineer", "Acme", "Berlin"),
    ("Backend Engineer", "Acme", "London, UK"),
    # remote/hybrid words are dropped from location, so these collapse together
    ("Backend Engineer", "Acme", "Remote"),
    ("Backend Engineer", "Acme", "Remote - EMEA"),
    ("Backend Engineer", "Acme", "Fully Remote"),
    ("Backend Engineer", "Acme", "Hybrid - Budapest"),
    ("Backend Engineer", "Acme", "On-site"),
    ("Backend Engineer", "Acme", "Budapest (Hybrid)"),
    # fail open: unusable title or company yields no key at all
    ("", "Acme", "London"),
    ("Engineer", "", "London"),
    ("", "", ""),
    ("(Remote)", "Acme", ""),
    ("!!!", "Acme", ""),
    ("Remote", "Acme", ""),
    # whitespace and punctuation normalisation
    ("  Senior   Backend  Engineer  ", "  Acme  ", "  London  "),
    ("Senior/Backend Engineer", "Acme", ""),
    ("Senior-Backend-Engineer", "Acme", ""),
    ("SENIOR BACKEND ENGINEER", "ACME", "LONDON"),
    # unicode and accents
    ("Ingénieur Backend", "Acmé", "Zürich"),
    ("バックエンドエンジニア", "Acme", ""),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reference", default=r"d:\Projects\job-search",
                    help="path to the single-tenant reference instance")
    ap.add_argument("--out", default=str(Path(__file__).with_name("dedup_cases.json")))
    args = ap.parse_args()

    ref = Path(args.reference)
    if not (ref / "pipeline" / "lib" / "store.py").exists():
        print(f"reference not found at {ref} — pass --reference", file=sys.stderr)
        return 1

    sys.path.insert(0, str(ref))
    from pipeline.lib.store import canonical_url, content_key

    golden = {
        "_source": "job-search/pipeline/lib/store.py",
        "_generated_by": "tests/golden/generate_dedup_golden.py",
        "_note": ("Expected values are the reference implementation's ACTUAL output, not a "
                  "transcription of its rules. Do not hand-edit; do not regenerate to make a "
                  "failing test pass."),
        "canonical_url": [{"url": u, "expected": canonical_url(u)} for u in CANONICAL_URL_CASES],
        "content_key": [
            {"title": t, "company": c, "location": loc, "expected": content_key(t, c, loc)}
            for t, c, loc in CONTENT_KEY_CASES
        ],
    }

    Path(args.out).write_text(json.dumps(golden, indent=2, ensure_ascii=False) + "\n",
                              encoding="utf-8")
    print(f"wrote {args.out}: {len(golden['canonical_url'])} canonical_url, "
          f"{len(golden['content_key'])} content_key cases")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
