"""Import a reference-instance checkout as one user's profile files.

    python deploy/scripts/import_profile_files.py <email> <path-to-checkout>

The founder's profile already exists — `CLAUDE.md` and seven skill files written by hand over
months, plus a base CV that has earned real interviews. Running the setup agent over it would ask
questions whose answers are already in the files and produce a worse copy. This imports them
verbatim instead.

**Verbatim is the point.** Nothing is rewritten, reformatted, or "generalised" on the way in: these
are the most heavily tuned files in either repository, and an import that edits them is an import
nobody can diff against the source.

Everybody else gets the scaffolds and the setup conversation (`REBUILD_PLAN.md`).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sqlalchemy import select

from libs.db.models import User
from libs.db.session import session_scope
from libs.profiles.repository import ProfileFiles

# Where each file lives in a reference checkout, and which kind it becomes. `CLAUDE.md` is the
# document the platform calls `context.md` — same content, a name that says what it is rather than
# which tool used to load it.
SOURCES: tuple[tuple[str, str, str], ...] = (
    ("CLAUDE.md", "context.md", "context"),
    (".claude/skills/job-application-assistant/01-candidate-profile.md",
     "01-candidate-profile.md", "skill"),
    (".claude/skills/job-application-assistant/02-behavioral-profile.md",
     "02-behavioral-profile.md", "skill"),
    (".claude/skills/job-application-assistant/04-job-evaluation.md",
     "04-job-evaluation.md", "skill"),
    (".claude/skills/job-application-assistant/05-cv-templates.md",
     "05-cv-templates.md", "skill"),
    (".claude/skills/job-application-assistant/07-interview-prep.md",
     "07-interview-prep.md", "skill"),
    ("scorer/prompts/label.md", "label.md", "prompt"),
    # The task prompts are per user (`libs/profiles`). Somebody who has tuned theirs over months
    # keeps them; the platform's field-agnostic version is only the starting point.
    ("pipeline/prompts/tailor_cv.md", "tailor_cv.md", "prompt"),
    ("pipeline/prompts/tailor_cover.md", "tailor_cover.md", "prompt"),
    ("pipeline/prompts/score.md", "score.md", "prompt"),
    ("pipeline/prompts/interview.md", "interview.md", "prompt"),
    ("pipeline/prompts/outreach.md", "outreach.md", "prompt"),
    ("pipeline/prompts/build_cvs.md", "build_cvs.md", "prompt"),
    # The reference kept `dev` and `quant`; the platform has one base CV that tailoring adapts.
    # `base_cv.html` first for a cleaned checkout, then the dev template for the original.
    ("cv/base_cv.html", "base_cv.html", "template"),
    ("cv/base_cv_dev.html", "base_cv.html", "template"),
)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2

    email, checkout = argv[0], Path(argv[1])
    if not checkout.is_dir():
        print(f"not a directory: {checkout}")
        return 1

    with session_scope() as session:
        user = session.scalar(select(User).where(User.email == email))
        if user is None:
            print(f"no such user: {email}")
            return 1

        files = ProfileFiles(session, user.id)
        # Seed first so anything the checkout does not carry still exists as a scaffold for the
        # agent to fill later — a half-imported profile should be completable, not broken.
        seeded = files.seed()

        imported, scoring_relevant = [], []
        for relative, name, kind in SOURCES:
            source = checkout / relative
            if not source.is_file() or name in imported:
                continue
            content = source.read_text(encoding="utf-8").replace("\r\n", "\n")
            if files.write(name, content, kind=kind):
                scoring_relevant.append(name)
            imported.append(name)
            print(f"  {relative} -> {name} ({len(content):,} chars)")

        session.commit()

    print(f"\nseeded {seeded} scaffold(s), imported {len(imported)} file(s) for {email}")
    if scoring_relevant:
        # Said rather than acted on: whether to spend a relabel is the user's decision
        # (`REBUILD_PLAN.md`, "Retraining is a notification, never a side effect").
        print(f"scoring-relevant: {', '.join(scoring_relevant)} — their model is now judging "
              f"against a rubric it was not trained on, and a retrain would fix that")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
