"""Does the judge actually discriminate? Three pairs where the answer is known in advance.

    python deploy/scripts/calibrate_judge.py --email you@example.com --jobs 3

**An A/B is only as good as its instrument, and nothing had checked the instrument.** The first
real run came back with every CV between 31 and 47 and every verdict "weak" — which is either a
true finding about the prompt or a broken judge, and the two are indistinguishable from a single
column of numbers.

This does not need a ground-truth CV, which is what makes it runnable at all: there is no archive
of "CVs that got interviews" to grade against, and the one posting that did produce an interview
has since been taken down. Instead it constructs pairs whose ordering is not in doubt and checks
the judge reproduces it.

## The three probes

1. **Tailored beats untailored.** The same job, the base CV against the tailored one. If tailoring
   for a specific posting does not beat not tailoring at all, the judge cannot see the thing the
   whole product does.
2. **A CV collapses on the wrong job.** The CV tailored for job A, graded against a posting from
   a **different field entirely**. This should fall hardest on `role_angle_fit` and
   `keyword_integration` — the two dimensions that are *about* the posting. A judge that scores it
   the same is reading the CV and ignoring the JD.

   The distant job is chosen by *lowest* score, deliberately, and the first version of this probe
   got it wrong: it paired jobs drawn from the same band above the delivery threshold, which for
   this account meant "Software Engineer (AI Training)" against "Back End Developer (AI
   Infrastructure)". Those are the same role. The probe returned +1.7 and looked like a broken
   judge; it was a broken experiment, because the wrong job was not wrong.
3. **Real beats degraded.** The tailored CV against a mechanically damaged copy of itself: metrics
   stripped, strong verbs replaced with "Responsible for", every bullet flattened to one shape.
   This is aimed at `achievement_quality` and `human_voice`, and it is the probe that catches a
   judge rewarding fluency over substance.

## What counts as a pass

Ordering, not magnitude. The judge may be harsh — the account's owner independently scored one of
his own real CVs at "40, maybe 50" and the judge agreed, so harshness is not the concern. What
matters for an A/B is that better scores above worse, **on every job, not on average**: a mean that
comes out right while two of three jobs are inverted is a coin flip with a good day.

`MIN_GAP` is the margin below which a difference is not evidence. Two CVs of genuinely equal
quality land within a few points of each other, and a judge that separated the probes by two
points would be agreeing with us by accident.
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ab_prompts import (
    JUDGE_MODEL,
    deterministic_summary,
    judge,
    pick_jobs,
    role_angles_for,
)
from sqlalchemy import select

from libs.contracts.profile import ProfileSchema
from libs.db.models import User, UserJobState, UserLabel
from libs.db.session import session_scope
from libs.llm import CLAUDE_KINDS, client_for
from libs.profiles.repository import ProfileFiles
from libs.prompts import EVAL_CV_TEMPLATE, load
from libs.secrets import SecretStore, install, redacting
from services.docgen import build

logger = logging.getLogger("calibrate")

# Below this, a difference is not evidence. Two CVs of genuinely equal quality land within a few
# points of each other, so a judge separating the probes by two would be agreeing by accident.
MIN_GAP = 8

# What the degradation does, in the order a bad writer would do it. Each one attacks a dimension
# the judge claims to score, so a judge that shrugs at all four is not reading for substance.
_BANNED_OPENER = "Responsible for "
# Strong verb to flat gerund. A map rather than a rule, because English is not regular and the
# point is a bullet that reads as a *task list* — "Responsible for building X" — rather than one
# that reads as damaged. A judge marking down broken grammar is marking down the wrong thing.
_FLATTEN = {
    "Built": "building", "Shipped": "shipping", "Designed": "designing",
    "Engineered": "working on", "Architected": "designing", "Led": "leading",
    "Reduced": "reducing", "Trained": "training", "Deployed": "deploying",
    "Automated": "automating", "Integrated": "integrating", "Delivered": "delivering",
    "Developed": "developing", "Implemented": "implementing", "Created": "creating",
    "Launched": "launching", "Improved": "improving", "Optimised": "optimising",
    "Optimized": "optimizing", "Achieved": "achieving", "Owned": "owning", "Ran": "running",
}
_STRONG_VERBS = re.compile(r"\b(" + "|".join(_FLATTEN) + r")\b")
_PERCENT = re.compile(r"\b\d[\d,.]*\s*%")
_NUMBER = re.compile(r"\b\d[\d,.]*\b")


def degrade(html: str) -> str:
    """Damage a CV in the ways the prompt spends most of its length forbidding.

    Deliberately mechanical, not model-written: a degraded copy produced by asking a model to
    "write this worse" is a different CV, and the comparison would measure the second model's taste
    rather than the judge's discrimination. This changes only the bullet text, and only in ways
    every version of the prompt explicitly calls a failure.
    """
    def spoil(match: re.Match) -> str:
        # Flattened to a gerund rather than deleted: "Responsible for building a Python API" is
        # what a genuinely weak bullet looks like, while "Responsible for a Python API" reads as
        # damaged — and a judge marking down damage is marking down the wrong thing.
        bullet = _STRONG_VERBS.sub(lambda verb: _FLATTEN[verb.group(1)],
                                   match.group(1), count=1).strip()
        bullet = _PERCENT.sub("a lot", bullet)
        bullet = _NUMBER.sub("many", bullet)
        return f"<li>{_BANNED_OPENER}{bullet}</li>"

    return re.sub(r"<li>(.*?)</li>", spoil, html, flags=re.S)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", required=True)
    parser.add_argument("--jobs", type=int, default=3)
    parser.add_argument("--judge-model", default=JUDGE_MODEL)
    parser.add_argument("--out", default="calibration.json")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    install()
    rows: list[dict] = []

    with session_scope() as session:
        user = session.scalar(select(User).where(User.email == args.email.lower()))
        if user is None:
            raise SystemExit(f"no such account: {args.email}")

        files = ProfileFiles(session, user.id)
        grounding = files.grounding()
        base_cv = files.read("base_cv.html")
        profile = ProfileSchema.model_validate(user.profile or {})
        template = files.read("tailor_cv.md")
        angles = role_angles_for(template or "", files)

        jobs = pick_jobs(session, user.id, args.jobs)
        if not jobs:
            raise SystemExit("no scored jobs with a real description — run a harvest first")

        # The most distant posting this account has, not a sibling of the ones being tested. Taken
        # from the bottom of the score range and capped there, because a low score for this user is
        # precisely "a job in a different field".
        distant = pick_jobs(session, user.id, 1, min_score=0.0, prefer="worst")
        if not distant:
            raise SystemExit("no low-scored job to use as the wrong-job control")
        wrong = distant[0]
        print(f"wrong-job control: {wrong.title!r} at {wrong.company or 'unknown'}")

        print(f"{len(jobs)} job(s), 4 judgements each "
              f"= {len(jobs) * 4} judgement(s) and {len(jobs)} build(s)\n")

        store = SecretStore(session)
        for credential in CLAUDE_KINDS:
            with store.use(user.id, credential) as secret:
                if secret is None:
                    continue
                value = secret.reveal()
                with redacting(value):
                    client = client_for(value, credential)
                    judge_prompt = load(EVAL_CV_TEMPLATE)

                    for job in jobs:
                        score = session.scalar(
                            select(UserJobState.score).where(
                                UserJobState.user_id == user.id,
                                UserJobState.job_id == job.id))
                        notes = session.scalar(
                            select(UserLabel.rationale).where(
                                UserLabel.user_id == user.id, UserLabel.job_id == job.id))
                        context = build.job_context(job, score=score, fit_notes=notes)

                        tailored = build.tailor(client, profile, base_cv, context,
                                                facts_md=grounding, template=template,
                                                role_angles=angles)

                        # Four judgements, one build. The wrong-job and degraded probes reuse the
                        # document rather than building again — the probe is about the judge, and a
                        # second build would introduce the model's own variance into the answer.
                        probes = {
                            "tailored": (tailored, job),
                            "untailored": (base_cv, job),
                            "wrong_job": (tailored, wrong),
                            "degraded": (degrade(tailored), job),
                        }
                        for label, (document, against) in probes.items():
                            text = build.html_to_text(document)
                            verdict = judge(
                                client, model=args.judge_model, prompt=judge_prompt,
                                description=against.description or "", cv_text=text,
                                summary=deterministic_summary(document, text, job=against))
                            rows.append({"job_id": job.id, "title": job.title, "probe": label,
                                         "graded_against": against.title, **verdict})
                            print(f"  {job.title[:38]:38} {label:11} "
                                  f"{verdict['overall_100']:>3}/100  {verdict['verdict']}")
                            Path(args.out).write_text(json.dumps(rows, indent=2), encoding="utf-8")
                break
        else:
            raise SystemExit("no Claude credential stored for this account")

    report(rows)
    print(f"\nwrote {args.out}")
    return 0


def report(rows: list[dict]) -> None:
    """Per-probe means, then the three verdicts, then the dimensions each probe was aimed at."""
    by_job: dict[int, dict[str, dict]] = {}
    for row in rows:
        by_job.setdefault(row["job_id"], {})[row["probe"]] = row

    print("\n" + "=" * 72)
    for probe in ("tailored", "untailored", "wrong_job", "degraded"):
        scores = [row["overall_100"] for row in rows if row["probe"] == probe]
        if scores:
            print(f"{probe:12} mean {statistics.mean(scores):5.1f}  n={len(scores)}")

    checks = [
        ("tailoring beats not tailoring", "tailored", "untailored",
         ("role_angle_fit", "keyword_integration")),
        ("the right job beats the wrong one", "tailored", "wrong_job",
         ("role_angle_fit", "keyword_integration")),
        ("real beats degraded", "tailored", "degraded",
         ("achievement_quality", "human_voice")),
    ]

    print()
    verdicts = []
    for name, better, worse, dimensions in checks:
        pairs = [(job[better], job[worse]) for job in by_job.values()
                 if better in job and worse in job]
        if not pairs:
            continue
        gaps = [good["overall_100"] - bad["overall_100"] for good, bad in pairs]
        # Every job, not the average. A mean that comes out right while two of three jobs are
        # inverted is a coin flip having a good day.
        passed = all(gap >= MIN_GAP for gap in gaps)
        verdicts.append(passed)
        print(f"  {'PASS' if passed else 'FAIL'}  {name}: "
              f"mean {statistics.mean(gaps):+.1f}, per job {[f'{gap:+d}' for gap in gaps]}")
        for dimension in dimensions:
            deltas = [good["dimensions"][dimension]["score"] - bad["dimensions"][dimension]["score"]
                      for good, bad in pairs]
            print(f"          {dimension:22} {statistics.mean(deltas):+.2f}")

    print()
    if all(verdicts) and verdicts:
        print("The judge discriminates. Its absolute scores may still be harsh, but an A/B run on")
        print("it is measuring something real.")
    else:
        print("The judge failed at least one probe. Until that is fixed, A/B numbers taken from it")
        print("are not evidence about a prompt — they are noise with a decimal point.")


if __name__ == "__main__":
    raise SystemExit(main())
