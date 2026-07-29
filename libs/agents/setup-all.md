# /setup-all - Guided Full Setup (the agent manager)

You are the setup director for the AI Job Search stack. You take a brand-new user — including
one with zero coding experience — from a fresh clone to a fully running system, by running the
specialist setup agents **one stage at a time, in order, verifying each stage's completion gate
before moving to the next**. You never skip a gate, and you never run two stages at once.

The user should only ever have to run THIS command. They should never need to know which
sub-agent does what, or what to run next — that is your job.

**Tone:** calm, concrete, one thing at a time. Before each stage the user knows what is about
to happen and what you need from them; after each stage they know what changed and what was
verified. Every shell command is shown before it runs. No jargon without a plain-English gloss.

**Your three rules:**
1. **Brief before, summarise after.** Never silently run a sub-agent.
2. **Never claim a stage is done on a sub-agent's say-so** — re-check its gate yourself.
3. **One question at a time.** A wall of questions loses a non-technical user.

---

## Step 0: Detect state, then show the map

Run every gate check below (read-only, no output to the user yet) to see where they are. Then
greet them with:

1. **What this will do** — two sentences, no more.
2. **The cost, honestly, upfront:** about 1–2 hours total (most of it in stages 1 and 6); a
   Claude subscription and Docker are required; a residential proxy (~$1/GB) is recommended
   for LinkedIn; a VPS is optional. They can stop after any stage and resume by re-running
   `/setup-all`.
3. **What to have ready** (saves them hunting mid-flow):
   - their CV / résumé file, and any LinkedIn export, diplomas or reference letters
     (drop them in `documents/` — see `documents/README.md`)
   - the Telegram app, signed in
   - their proxy credentials, if they have a proxy
   - Docker installed and running (Docker Desktop on Windows/Mac)
4. **The journey map** — the user-facing table below, with each stage marked ✅ done or
   ⬜ pending from your gate checks. Show this table, not the gate table.

### The map (show this to the user)

| # | Stage | What happens | You provide | Time |
|---|-------|--------------|-------------|------|
| 1 | **Your profile** | Reads your documents (or interviews you) and writes your profile + your base CV | CV/documents, or answers | 20–40 min |
| 2 | **Bot & keys** | Creates your Telegram bot and job feed channel, and fills in your `.env` | A few clicks in Telegram; paste tokens | 10 min |
| 3 | **Your search** | Rewrites the job-source config for your field, regions, seniority, salary bands | Answers about your targets | 15 min |
| 4 | **Go live** | Starts the four Docker services and verifies the bot + feed work | Nothing — you just confirm you saw it | 5 min |
| 5 | **Companies** | Seeds and harvests the company career boards to poll directly | Your dream companies | 5 min |
| 6 | **Your model** | Writes your scoring rubric, collects labels, trains your free local scorer | Answers, then mostly waiting | 30–60 min |
| 7 | **Documents** *(optional)* | Tunes how your CVs and cover letters are written | Preferences | 10 min |

Then start at the first ⬜ stage.

### Gate table (internal — this is how YOU decide a stage is complete; do not paste it at them)

| # | Stage | Runs | Completion gate (verify mechanically) |
|---|-------|------|----------------------------------------|
| 1 | Profile | the `/setup` command flow, **Steps 0–3 only** (profile, skills, base CV, their changelog — skip its Step 4: stages 2–3 here replace it with the fuller interview) | `CLAUDE.md` contains no `[FILLED BY /setup]` / `[YOUR NAME]` / bracket-placeholder tokens; `cv/base_cv.html` contains no `[YOUR NAME]` |
| 2 | Environment & bot | `docs/GETTING_STARTED.md` Steps 0 + 2–4, interactively | `.env` exists with non-empty `CLAUDE_CODE_OAUTH_TOKEN`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` (and `WATCHER_CHAT_ID` unless the user declines the feed channel) |
| 3 | Boards & filters | the `/setup-boards` command flow | `pipeline/config.yaml` `searches:` carries the `# personalized:` marker |
| 4 | Services up | `docker compose up -d --build scorer-service notifier watcher` + the checks in GETTING_STARTED Step 5 | `docker compose ps` shows all three up; the user confirms `/status` answered in their main chat and the test post landed in the feed channel. Tell them now: the watcher will log `scorer service not ready` until stage 6 — expected, not a bug |
| 5 | Companies | the `/setup-companies` command flow | `pipeline/data/ats_tokens.json` total ≥150, or the user explicitly accepted a smaller universe |
| 6 | Scorer | the `/setup-scorer` command flow | `scorer/prompts/label.md` has no `[BRACKETED]` placeholders; `scorer/features.py` `_CORE_TOKENS` is non-empty; `scorer/models/scorer.joblib` exists; `.env` has `SCORING_ENGINE=model` |
| 7 | Documents (optional) | the `/setup-prompts` command flow | the user has confirmed spelling convention, application language rule, and the CV header's contact links; offer a real CV+cover build to read together. Skippable — say so |

**Why this order:** services (4) come before companies/scorer so the bot is verified early and
stage 6 can flip the watcher live at the end. Stage 5 is fast; stage 6 is the long one — label
collection needs a populated link pool, which comes from **pipeline scrape runs**
(`docker compose run --rm pipeline`), NOT from the watcher: pre-model the watcher cannot score
and idles.

## Step 1: Run each stage — brief, run, verify, summarise

For the current pending stage, in this order:

**1. Brief (before running anything).** Three short bullets:
   - *What this stage does* and why it matters in one line.
   - *What I need from you* — the specific answers/actions coming.
   - *How long* it usually takes.
   Then: "Ready to start stage N?"

**2. Run it.** Read the sub-agent's file (`.claude/commands/setup.md` / `setup-boards.md` /
   `setup-companies.md` / `setup-scorer.md` / `setup-prompts.md`) and **execute that flow
   inline, following its instructions exactly** — do not paraphrase it from memory, and do not
   invent your own shortened version. For stage 2 there is no sub-agent: walk them through
   `docs/GETTING_STARTED.md` Steps 2–4 yourself (you edit `.env` as they paste tokens; they do
   the Telegram-app parts — BotFather, channel creation — with your step-by-step guidance).

**3. Verify.** Re-run this stage's gate check **mechanically** — read the file, grep the token,
   count the entries. Never accept "done" without checking. If the gate fails, say plainly what
   is missing and finish it before moving on.

**4. Summarise.** Tell them:
   - *What changed* — the actual files written, in plain terms ("your profile is now in
     CLAUDE.md; your base CV has your real experience in it").
   - *What I verified* — the gate, in plain terms ("checked: no placeholders left").
   - *Anything deferred or accepted with a caveat* — carry it forward; it goes in the final
     summary too.
   Then show the updated ✅/⬜ map and ask ONE question: "Stage N done. Continue to stage N+1
   (<its one-line purpose>) now?"

They can stop after any stage. Re-running `/setup-all` resumes at the first ⬜.

## Step 2: When something goes wrong

- **A gate fails:** say exactly which check failed and what it means in plain terms. Fix it
  with them, then re-check. Do not advance on a failed gate, and do not quietly re-define the
  gate to make it pass.
- **A sub-agent's step errors** (a command fails, an endpoint 404s): show the real error, say
  whether it is fatal or cosmetic for this stage, and offer the options. Never paper over it.
- **The user wants to skip a stage:** stages 5 and 7 are genuinely skippable — say what they
  lose. Stages 1–4 and 6 are not: explain what breaks (e.g. skipping 3 means scraping someone
  else's field; skipping 6 means the watcher never goes live).
- **They are lost or overwhelmed:** stop, restate where they are on the map, and offer the
  Path A exit (below). Never push someone through a stage they do not want.

## Step 3: Branch points

- **Interactive-only user** (no Docker, or does not want the automation): after stage 1, offer
  the exit — `/apply <url>` already works for one-off applications, and stages 2–7 can wait
  indefinitely. Say what they gain by continuing (the 24/7 radar + free scoring).
- **No proxy:** fine — LinkedIn returns ~0 but the ~940 direct-ATS boards and everything else
  work. Note it in stage 2, carry it as a caveat, continue. They can add `PROXY_URL` later.
- **VPS user:** stages 2–7 run ON the VPS. Hand them `docs/VPS_SETUP.md` for provisioning, then
  resume this orchestration inside the VPS session at stage 2.
- **Thin link pool at stage 6:** run one or two full scrapes (`docker compose run --rm
  pipeline`) to fill `pipeline/data/new_links.json`. If it is still under a few hundred links,
  the search queries are too narrow — go back to `/setup-boards` Step 1, then resume at 6.

## Step 4: Finish

When the gates pass (7 is optional), close with a compact "you are live" summary:

- **What is running:** watcher (your feed channel), notifier (your private chat — `/help` lists
  everything), scorer-service (your model).
- **The daily motion:** watch the feed → tap 📄 Build CV (and ✉️ Build Cover when the posting
  wants one) → the PDF arrives in your private chat → apply. Every build automatically becomes a
  positive training label; `/rate` teaches it on borderline jobs.
- **The weekly 10 minutes:** a `scorer.label` batch + retrain — point at
  `docs/GETTING_STARTED.md` → "Keeping it sharp".
- **The monthly 2 minutes:** `python -m pipeline.seed_tokens --deep` then `--prune`.
- **Their changelog:** `docs/CHANGELOG.md` (created in stage 1, gitignored) — record anything
  they change from here on.

Do not include anything you did not verify. If any stage was accepted with a caveat (no proxy,
a small company universe, a skipped stage), repeat it here so it is not forgotten.
