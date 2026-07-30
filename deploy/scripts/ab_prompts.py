"""A/B one CV prompt against another on real jobs, graded by the LLM judge.

    python deploy/scripts/ab_prompts.py --email you@example.com --jobs 12 \
        --variant mine=stored --variant default=scaffold:tailor_cv.md
    python deploy/scripts/ab_prompts.py --email you@example.com --jobs 12 \
        --variant mine=stored --variant candidate=file:/tmp/new_tailor_cv.md

**Prompts are SACRED and this is what makes an edit reviewable.** The rule carried from the
reference instance is that no prompt changes without an A/B and a changelog entry, and until now
there was no A/B to run — so "is this version better" was answered by reading two PDFs and having
a feeling about them. This turns it into a number per variant with the evidence attached.

## What it holds constant

Everything except the prompt. Same account, same profile files, same grounding, same base CV, same
jobs, same model, same job context. The only difference between two runs is the bytes of
`tailor_cv.md`, which is the whole point — a comparison where anything else moved would measure
that instead.

## How the jobs are chosen

Spread across the score range rather than the top of it. A prompt can look excellent on the five
jobs that were an obvious fit and fall apart on the ones needing a real angle, and the second group
is where a CV earns its keep. Selection is deterministic given the same corpus, so a re-run
compares like with like.

## What the judge is

`libs/prompts/eval_cv.md`, ported from the reference — seven dimensions, each needing a quoted line
from the CV as evidence, plus a holistic 0–100. Evidence is what stops the judge rewarding a CV it
has not read. It runs on a **stronger model than the writer**, as the reference does: a judge no
better than the author cannot see the author's mistakes.

Costs real money on the account's own credential — roughly `jobs × variants` CV builds plus the
same number of judgements. Twelve jobs across two variants is a few dollars.
"""
from __future__ import annotations

import argparse
import json
import logging
import statistics
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sqlalchemy import func, select

from libs.contracts.profile import ProfileSchema
from libs.db.models import CorpusJob, User, UserJobState, UserLabel
from libs.db.session import session_scope
from libs.llm import CLAUDE_KINDS, client_for
from libs.profiles import scaffold
from libs.profiles.repository import ProfileFiles
from libs.prompts import EVAL_CV_TEMPLATE, load
from libs.scoring.cveval import deterministic
from libs.secrets import SecretStore, install, redacting
from services.docgen import build

logger = logging.getLogger("ab")

# The judge is deliberately not the writer's model. The reference makes the same choice and for the
# same reason: a judge no better than the author cannot see the author's mistakes.
#
# Sonnet 5 rather than Opus by default, and that is a limits decision rather than a quality one: the
# writer is Haiku, so Sonnet is comfortably the stronger model, and a personal plan rate-limits Opus
# hard enough that a two-variant run stalls partway through. `--judge-model claude-opus-5` when the
# account can take it.
JUDGE_MODEL = "claude-sonnet-5"

# What a judgement must come back as. Same constraints every structured-output call here obeys:
# `additionalProperties: false` on every object, and no `minimum`/`maximum`.
DIMENSIONS = ("six_second_pull", "role_angle_fit", "relevance_coverage", "achievement_quality",
              "human_voice", "defensibility", "keyword_integration")

VERDICT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "overall_100": {"type": "integer"},
        "verdict": {"type": "string", "enum": ["strong", "good", "borderline", "weak"]},
        "one_line": {"type": "string"},
        "dimensions": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                name: {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {"score": {"type": "integer"}, "evidence": {"type": "string"}},
                    "required": ["score", "evidence"],
                } for name in DIMENSIONS
            },
            "required": list(DIMENSIONS),
        },
        "top_fixes": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["overall_100", "verdict", "one_line", "dimensions", "top_fixes"],
}


def pick_jobs(session, user_id, count: int, *, min_score: float = 60.0,
              prefer: str = "best") -> list[CorpusJob]:
    """Real jobs this account was scored on, spread across the score range above `min_score`.

    **Above the threshold, because that is the only population a CV is ever built for.** The first
    run of this graded a Senior Product Designer posting the model had scored 12; both variants
    produced a bad CV, both judgements said "this is an engineer applying for a design role", and
    the comparison measured nothing about either prompt. A CV is built when the user decides to
    apply, so the prompt has to be judged on the jobs they would actually apply to.

    Spread across the bands above it rather than top-N, because a prompt can look excellent on the
    obvious fits and fall apart on the ones needing a real angle — and that second group is where a
    CV earns its keep. Ordered by id within each band, so the same corpus yields the same sample
    twice and two runs are comparable.
    """
    rows = session.execute(
        select(CorpusJob, UserJobState.score)
        .join(UserJobState, UserJobState.job_id == CorpusJob.id)
        .where(UserJobState.user_id == user_id,
               UserJobState.score >= min_score,
               CorpusJob.trimmed_at.is_(None),
               func.length(func.coalesce(CorpusJob.description, "")) >= 1_500)
        .order_by(CorpusJob.id)).all()
    if not rows:
        return []

    # Five bands across whatever range survived the floor, so a run of three does not take three
    # near-identical jobs from the top of it.
    lowest = min(score for _job, score in rows)
    highest = max(score for _job, score in rows)
    width = max((highest - lowest) / 5, 1e-6)

    # **One posting per role.** The corpus carries the same job from several boards and the same
    # employer re-listing it, and the first run of this graded "Software Engineer (AI Training)"
    # twice and "Back End Developer (AI Infrastructure)" twice — halving an eight-job sample
    # without saying so. Dedup is not the corpus's job here: those are genuinely distinct rows
    # with distinct URLs, and it is only *this* comparison that wants them collapsed.
    bands: dict[int, list] = defaultdict(list)
    seen: set[tuple[str, str]] = set()
    for job, score in rows:
        role = ((job.title or "").strip().lower(), (job.company or "").strip().lower())
        if role in seen:
            continue
        seen.add(role)
        bands[min(int((score - lowest) / width), 4)].append(job)

    # Best band first: a short run should sample the jobs most likely to be applied to. `prefer`
    # flips it for the one caller that wants the opposite — the calibration's wrong-job control,
    # which needs the most distant posting this account has rather than the most relevant.
    picked, band_keys = [], sorted(bands, reverse=prefer == "best")
    while len(picked) < count and any(bands[key] for key in band_keys):
        for key in band_keys:
            if bands[key] and len(picked) < count:
                picked.append(bands[key].pop(0))
    return picked


def variant_prompt(spec: str, files: ProfileFiles) -> str:
    """Resolve `stored`, `scaffold:<name>` or `file:<path>` to prompt text."""
    if spec == "stored":
        return files.read("tailor_cv.md") or ""
    kind, _, rest = spec.partition(":")
    if kind == "scaffold":
        return scaffold(rest)
    if kind == "file":
        return Path(rest).read_text(encoding="utf-8").replace("\r\n", "\n")
    raise SystemExit(f"unknown variant source {spec!r} — use stored, scaffold:NAME or file:PATH")


def role_angles_for(prompt: str, files: ProfileFiles) -> str | None:
    """This account's own angles, but only for a prompt that has somewhere to put them.

    A stored prompt from before the split has its lenses written inline and no `<<ROLE_ANGLES>>`,
    so it needs nothing. Returning None makes that explicit rather than incidental, which matters
    here: the whole comparison is inline-lenses against injected-lenses, and a variant that
    accidentally got both would measure neither.
    """
    return files.read("08-role-angles.md") if "<<ROLE_ANGLES>>" in prompt else None


def deterministic_summary(html: str, text: str, *, job) -> str:
    """The objective half, computed rather than asked for.

    The judge is told these checks are already done and **not** to re-grade them. Handing it a
    character count and nothing else was worse than handing it nothing: a model told a measurement
    exists and then not shown it does not abstain — it estimates, from the same text it is meant to
    be judging subjectively, which is exactly the contamination the split exists to prevent.

    Format follows the reference's `_load_judge_prompt`, so the judge reads what it was tuned on.
    """
    det = deterministic(job.description or "", job.title or "", html, text,
                        {"pages": None}, job.company or "")
    coverage = det["keyword_coverage"]
    hidden = det["issues"]
    return (f"keyword coverage: {coverage['all_pct']}% overall, "
            f"{coverage['critical_pct']}% of critical keywords; "
            f"missing critical: {', '.join(coverage['missing_critical']) or 'none'}. "
            f"bullets: {det['bullets']['compliant']}/{det['bullets']['total']} follow the formula. "
            f"extracted characters: {len(text):,}."
            + (f" objective issues: {'; '.join(hidden[:3])}." if hidden else ""))


def judge(client, *, model: str, prompt: str, description: str, cv_text: str,
          summary: str) -> dict:
    """One judgement. Returns the parsed verdict."""
    filled = (prompt.replace("<DETERMINISTIC_SUMMARY>", summary)
                    .replace("<JOB_DESCRIPTION>", description[:6_000])
                    .replace("<CV_TEXT>", cv_text[:12_000]))
    if "<CV_TEXT>" not in prompt:
        # The reference's template ends with the CV under a header rather than a placeholder.
        filled = f"{filled}\n\n{cv_text[:12_000]}"
    return client.complete_json(
        model=model, system="You are a strict technical recruiter.",
        messages=[{"role": "user", "content": filled}],
        schema=VERDICT_SCHEMA, max_tokens=4_000)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", required=True)
    parser.add_argument("--jobs", type=int, default=12)
    parser.add_argument("--min-score", type=float, default=60.0,
                        help="only grade jobs at or above this score (default: the delivery "
                             "threshold, which is the population a CV is ever built for)")
    parser.add_argument("--variant", action="append", required=True,
                        metavar="NAME=SOURCE",
                        help="stored | scaffold:tailor_cv.md | file:/path/to/prompt.md")
    parser.add_argument("--judge-model", default=JUDGE_MODEL,
                        help=f"the grading model (default {JUDGE_MODEL})")
    parser.add_argument("--out", default="ab_results.json")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    install()
    variants = dict(pair.split("=", 1) for pair in args.variant)
    results: list[dict] = []
    failures: list[dict] = []

    with session_scope() as session:
        user = session.scalar(select(User).where(User.email == args.email.lower()))
        if user is None:
            raise SystemExit(f"no such account: {args.email}")

        files = ProfileFiles(session, user.id)
        grounding = files.grounding()
        base_cv = files.read("base_cv.html")
        profile = ProfileSchema.model_validate(user.profile or {})
        prompts = {name: variant_prompt(spec, files) for name, spec in variants.items()}
        angles = {name: role_angles_for(prompt, files) for name, prompt in prompts.items()}
        for name, prompt in prompts.items():
            print(f"  variant {name:10} {len(prompt):>6,} chars"
                  f"{'  + role angles' if angles[name] else '  (angles inline)'}")
        jobs = pick_jobs(session, user.id, args.jobs, min_score=args.min_score)
        if not jobs:
            raise SystemExit(f"no jobs scored >= {args.min_score:.0f} with a real description — "
                             "lower --min-score or run a harvest first")

        print(f"{len(jobs)} job(s) x {len(prompts)} variant(s) "
              f"= {len(jobs) * len(prompts)} build(s) and as many judgements\n")

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

                        for name, prompt in prompts.items():
                            # **One bad call must not destroy a paid run.** A model that returns
                            # no HTML is a real and occasional outcome, and losing fifteen
                            # completed judgements to the sixteenth is losing the money they cost.
                            # The failure is recorded rather than swallowed: a variant that fails
                            # to produce a document more often than the other is *worse*, and a
                            # run that quietly dropped those rows would score it as equal.
                            try:
                                html = build.tailor(client, profile, base_cv, context,
                                                    facts_md=grounding, template=prompt,
                                                    role_angles=angles[name])
                            except Exception as exc:
                                failures.append({"job_id": job.id, "title": job.title,
                                                 "variant": name, "error": type(exc).__name__})
                                print(f"  {job.title[:44]:44} {name:10} "
                                      f"BUILD FAILED  {type(exc).__name__}")
                                _save(args.out, results, failures)
                                continue

                            text = build.html_to_text(html)
                            verdict = judge(
                                client, model=args.judge_model, prompt=judge_prompt,
                                description=job.description or "", cv_text=text,
                                summary=deterministic_summary(html, text, job=job))
                            results.append({"job_id": job.id, "title": job.title,
                                            "variant": name, "chars": len(text), **verdict})
                            print(f"  {job.title[:44]:44} {name:10} "
                                  f"{verdict['overall_100']:>3}/100  {verdict['verdict']}")
                            # Written after every judgement, not at the end. The run is expensive
                            # and long enough that a crash at minute nine should cost the last
                            # call, not all of them.
                            _save(args.out, results, failures)
                break
        else:
            raise SystemExit("no Claude credential stored for this account")

    _save(args.out, results, failures)
    report(results, failures)
    print(f"\nwrote {args.out}")
    return 0


def _save(path: str, results: list[dict], failures: list[dict]) -> None:
    Path(path).write_text(
        json.dumps({"results": results, "failures": failures}, indent=2), encoding="utf-8")


def report(results: list[dict], failures: list[dict] | None = None) -> None:
    """Per-variant means, then per-dimension, then where they most disagreed."""
    by_variant: dict[str, list[dict]] = defaultdict(list)
    for row in results:
        by_variant[row["variant"]].append(row)

    print("\n" + "=" * 72)
    for name, rows in by_variant.items():
        overall = [row["overall_100"] for row in rows]
        print(f"\n{name}: mean {statistics.mean(overall):.1f}/100  "
              f"median {statistics.median(overall):.0f}  "
              f"min {min(overall)}  max {max(overall)}  n={len(rows)}")
        for dimension in DIMENSIONS:
            scores = [row["dimensions"][dimension]["score"] for row in rows]
            print(f"    {dimension:22} {statistics.mean(scores):.2f}")

    # **What to fix, not just how bad it is.** A mean tells you a prompt is losing; the weakest
    # dimensions and the judge's own fixes tell you where. They are printed per variant because a
    # variant can win overall and still be the weaker one on the dimension that decides a callback.
    for name, rows in by_variant.items():
        weakest = sorted(
            ((statistics.mean(row["dimensions"][dimension]["score"] for row in rows), dimension)
             for dimension in DIMENSIONS))[:3]
        print(f"
{name}: weakest dimensions — "
              + ", ".join(f"{dimension} {mean:.1f}/10" for mean, dimension in weakest))

        # Ranked by how often the judge asked for the same thing. One fix on one CV is that CV; the
        # same fix on five is the prompt.
        fixes: dict[str, int] = defaultdict(int)
        for row in rows:
            for fix in row.get("top_fixes") or []:
                fixes[fix.strip()] += 1
        for fix, count in sorted(fixes.items(), key=lambda item: -item[1])[:6]:
            print(f"    x{count}  {fix[:100]}")

    if len(by_variant) == 2:
        first, second = by_variant
        paired = {}
        for row in results:
            paired.setdefault(row["job_id"], {})[row["variant"]] = row
        gaps = [(pair[first]["overall_100"] - pair[second]["overall_100"], job_id, pair)
                for job_id, pair in paired.items() if len(pair) == 2]
        if gaps:
            print(f"\nmean gap ({first} - {second}): "
                  f"{statistics.mean(gap for gap, _, _ in gaps):+.1f} points")
            gaps.sort(key=lambda item: abs(item[0]), reverse=True)
            print("\nwhere they disagreed most:")
            for gap, _job_id, pair in gaps[:3]:
                print(f"  {gap:+3d}  {pair[first]['title'][:50]}")
                print(f"       {first}: {pair[first]['one_line'][:90]}")
                print(f"       {second}: {pair[second]['one_line'][:90]}")


if __name__ == "__main__":
    raise SystemExit(main())
