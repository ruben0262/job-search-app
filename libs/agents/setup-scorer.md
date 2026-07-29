# /setup-scorer - Personal Fit Model Setup

You are standing up the user's own local scoring model, end-to-end: personalize the labeling
rubric → collect training labels → train → serve → switch the pipeline onto it. When this
command finishes, scoring every scraped job costs the user nothing — Claude is only paid for
the one thing that needs it (writing CVs).

**Prerequisites:**
- Profile populated (`CLAUDE.md` has no `[FILLED BY /setup]` placeholders) — the rubric is
  written from it.
- Some scraped jobs to label: `pipeline/data/new_links.json` / `all_links.json` should have
  at least a few hundred entries. If missing or thin, fill the pool first with
  `docker compose run --rm pipeline` (a full scrape; with `SCORING_ENGINE=claude` it also
  scores the top links via Claude). Do NOT rely on the watcher for this — pre-model, the
  watcher cannot score and idles ("scorer service not ready" in its logs is expected until
  this command finishes).
- Docker for training/serving (the scorer needs numpy 2.x and is its own image — do NOT
  pip-install fastembed into the pipeline venv; see `scorer/README.md`).

Background you should have read before starting: `docs/SCORING_RUBRIC_GUIDE.md` (the rubric
method) and the cold-start table in `docs/GETTING_STARTED.md`.

---

## Step 1: Personalize the rubric (`scorer/prompts/label.md`)

This file is the single source of truth the labeler judges against — ten careful minutes here
decide the model's quality. Fill EVERY `[BRACKETED]` block from the profile, keeping the
structure exactly (profile block → ordered decision procedure → full-range anchors →
calibration examples → breakdown-first): 

- WHO block: seniority line, 3–4 strength areas with concrete evidence, the explicit
  does-NOT-have list, targets in priority order, employment-type rule, workplace policy,
  languages.
- Decision procedure: concrete hard gates (seniority, location/region-lock, language, pay).
- **Calibration examples: ~6 few-shots written from THEIR profile covering every band
  including the hard gates** — this is the biggest consistency lever. Follow the shapes left
  in the file.
- Anti-squash: keep the "use the WHOLE scale decisively" wording — timid mid-band labels on
  clear matches poison the dataset.

Show the finished rubric to the user section by section and get an explicit confirmation.
NOTE: this file is hashed as `profile_version` — every future edit to it should be followed
by `python -m scorer.label --relabel` + retrain (tell the user this now).

## Step 2: Collect labels

Check the current count: lines in `scorer/data/labels.jsonl` (0 if absent — on a VPS setup
the file lives there, not locally). Target: **~800–1,000 clean rows** (train.py drops thin
rows and dedups reposts, so aim for the top of that range).

Collect in batches, each one a cheap single pass (~$0.09 per 50 jobs):
```
docker compose run --rm pipeline python -m scorer.label --n 300
```
Repeat until the target is reached (typically 2–4 batches if the link pool is deep enough; if
the pool runs dry, run `docker compose run --rm pipeline` to scrape more first). The watcher
also queues every job it scores into `scorer/data/pending_labels.jsonl`, which `scorer.label`
consumes first (no re-fetching) — so once the watcher is live it keeps feeding this forever.

## Step 2b: Fill in the engineered-feature lists

`scorer/features.py` adds cheap, high-signal features next to the embedding (years required,
core-stack overlap, off-stack overlap, workplace mode, region lock, seniority wording). Two of
those lists are yours and ship EMPTY:
- `_CORE_TOKENS` — regex tokens for your real strengths, as they appear in postings. Mirror the
  STRENGTHS block you just wrote in label.md (~20–30 tokens).
- `_OFFSTACK_TOKENS` — your NOT-list from label.md.
Also check `_REGION_LOCK_RE`: the default catches US-work-authorisation locks (the common case
on international boards). If you ARE US-authorised, replace it with the locks that exclude YOU.

Empty lists are safe (the model ignores constant features) but you are leaving signal on the
table — fill them before the first real train.

## Step 3: Train

```
docker compose build scorer          # first time only
docker compose run --rm -e SCORER_FEATURES=1 scorer
```
(`SCORER_FEATURES=1` includes the engineered features from Step 2b. `SCORER_TASK=classify` is
the default; `SCORER_MODEL=<name>` pins a family instead of letting the bake-off pick, and
`SCORER_TARGET_RECALL` sets where the decision cutoff lands.)

Read the printed report WITH the user. For the classifier path it prints, per model family,
cross-validated **AP** and **AUC** plus an honest held-out test AP/AUC, then the winner, then a
probability-cutoff sweep with precision/recall/F1 at the target recall. Calibration for a first
model: **test AUC ≥ ~0.75 with AP well above the base rate** is a usable start (it improves
with your feedback). AUC near 0.5 means the labels are inconsistent — re-check the rubric
(Step 1) before blaming the model. The artifact lands at `scorer/models/scorer.joblib` (the
previous one is kept as `.bak`).

## Step 4: Serve and switch the pipeline onto it

1. `docker compose up -d --build scorer-service`
2. Health check (the service is internal to the compose network — port 8000, no host port):
   `docker compose exec scorer-service python -c "import urllib.request; print(urllib.request.urlopen('http://localhost:8000/health').read().decode())"`
3. In `.env`: set `SCORING_ENGINE=model`; keep `SCORING_SHADOW=true` for now (Haiku grades
   the model in the background and mints free extra labels — turn it off later to reach
   zero scoring cost).
4. Apply: `docker compose up -d watcher notifier`.

## Step 5: Hand over the flywheel

Tell the user the maintenance loop, once, concretely:
- Every 📄 Build CV tap on the feed automatically records that job as a positive example;
  `/rate` ✅/❌ teaches it directly on borderline jobs. This feedback trains at 5× weight
  (`SCORER_FEEDBACK_WEIGHT`) and is the only signal that is THEIR taste rather than the
  labeler's guess.
- Weekly: a `scorer.label --n 300` batch, then retrain (`docker compose run --rm scorer` +
  `docker compose up -d --force-recreate scorer-service`). See `docs/COMMANDS.md`.
- Check the model has genuinely learned THEM:
  `docker compose run --rm scorer python -m scorer.eval_feedback` — when its AUC beats the
  labeler alone, set `SCORING_SHADOW=false`.
- Any rubric edit → `scorer.label --relabel` → retrain.

Stage complete when: `label.md` has no `[BRACKETED]` blocks left, `_CORE_TOKENS` in
`scorer/features.py` is non-empty, `scorer/models/scorer.joblib` exists, the service answers
`/health`, and `.env` has `SCORING_ENGINE=model`. Report all five explicitly — `/setup-all`
checks them.
