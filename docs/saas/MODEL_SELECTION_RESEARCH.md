# Model Selection Research — Scoring & CV/Cover Generation

**Status:** research that fed the Claude API backend plan (deleted 2026-07-31 — it was written
for the reference instance's `claude -p` path, which this platform never had). The model and
pricing ladder below is still the current answer for scoring vs writing.
Prices are per **million tokens (MTok), input / output, standard tier**, gathered 2026-07-24 (see
Sources). Prices move monthly — re-check before committing.

> **Bottom line up front.** The pipeline has **two very different Claude jobs**, and the answer differs:
> - **Scoring / labeling** (mechanical, structured JSON, self-contained rubric) is **provider-portable**
>   and the place where cheap-model routing pays off cleanly.
> - **CV / cover writing** is **SACRED, voice-critical, and Claude-tuned** (`CLAUDE.md` → Prompt & Rule
>   Change Control). Moving it off Claude is a quality gamble *and* a large hidden migration cost.
>   Recommendation: keep writing on Claude (Haiku→Sonnet ladder); challenge it only through a real A/B.

---

## 1. The two jobs are different problems

| | Scoring / labeling | CV / cover writing |
|---|---|---|
| Output | Structured JSON (score + breakdown + short notes) | Long-form prose / HTML, voice matters |
| Prompt | Self-contained rubric (`scorer/prompts/label.md`) | SACRED, A/B-tuned (`pipeline/prompts/tailor_*.md`) |
| Grounding | None needed (rubric embeds "who Ruben is") | Needs `CLAUDE.md` context to avoid fabrication (2026-07-15 A/B) |
| Failure cost | A mis-scored job (cheap; feeds ML training data + routing) | A fabricated employer/metric on a real application (expensive, reputational) |
| Volume | ~1300 jobs/run, bulk, offline | ~5 CVs per on-demand build, low volume |
| Latency need | None (batchable) | Interactive-ish (user waits for a build) |
| Portability | **High** — structured task, any capable model | **Low** — tied to Claude prompt corpus + grounding mechanism |

A single "best model" answer is wrong. Route per job.

---

## 2. Cheap-model landscape (2026-07)

### Ultra-cheap tier (scoring candidates)

| Model | In | Out | Batch | Cache read | Speed (tok/s) | Notes |
|---|---|---|---|---|---|---|
| **GPT-5 nano** | $0.05 | $0.40 | −50% | ~$0.005 | mid | Cheapest overall; quality thin for prose |
| **Gemini 2.5 Flash-Lite** | $0.10 | $0.40 | $0.05/$0.20 | implicit | **~275** | **Retiring 2026-10-16** → 3.1 Flash-Lite $0.25/$1.50 |
| **GPT-5 mini** | $0.13 | $1.00 | −50% | ~$0.013 | mid | Good structured-task workhorse |
| **Gemini 2.5 Flash** | $0.15 | $1.25 | −50% | implicit | fast | >200K ctx bills higher |
| **Grok 4.1 Fast** | $0.20 | $0.50 | — | **$0.05** | **~455 (fastest)** | $175/mo free credits; cheap output |
| **DeepSeek V4 Pro** | $0.435 | $0.87 | — | discount | mid | Cheapest *balanced* in/out |
| **Kimi K2.6** | $0.95 | $4.00 | — | discount | mid | Near Haiku price, no clear edge |
| **Claude Haiku 4.5** | $1.00 | $5.00 | −50% | $0.10 | fast | **Current model; already validated** |

### Quality tier (writing candidates)

| Model | In | Out | Writing signal |
|---|---|---|---|
| **Claude Sonnet 5** | $3 ($2 intro) | $15 ($10) | Claude wins human-eval writing (see §4) |
| **GPT-5** | $1.25 | $10 | Strong tone/versatility; the real non-Claude challenger |
| **Gemini 3 Flash (preview)** | ~$0.50 | ~$0.50 | Fast, cheap; prose quality unproven for this use |
| **Kimi K3 / Grok 4.5** | $3 / $2 | $15 / $6 | Flagship-priced; no writing advantage found |

---

## 3. What Haiku + all the tricks actually gets us (scoring path)

Take the current Haiku scoring workload and stack every lever:

- **Base:** Haiku 4.5 $1 / $5.
- **Batch API −50%** (labeling is offline) → $0.50 / $2.50 effective.
- **Prompt caching** on the rubric+system prefix → cached input ~$0.10 (−90%) **IF the prefix clears
  Haiku's 4096-token minimum** (must measure — see plan §5). If under 4096, cache does nothing.
- **Prompt slimming** already in place: descriptions capped at 2500 chars (`LABEL_DESC_CHARS`),
  minified JSON, `max_thinking_tokens=0`, only rubric-relevant fields sent.

**Realistic Haiku-with-tricks floor: ~$0.50 / $2.50 effective, minus the cached-rubric portion.**
That is a solid **~2x** off today's estimated token cost, plus it stops burning Pro/Max quota.

**Could a cheaper model beat that?** On price, yes — GPT-5 nano ($0.05/$0.40) or Gemini 2.5
Flash-Lite ($0.10/$0.40) are ~10x cheaper per token than Haiku. But:
- The score feeds **training data for the local ML classifier** (BGE + SVC) *and* routes real
  application decisions. Label quality is not free to degrade.
- Cross-provider means a **second SDK, second key, second budget ledger, second failure mode**.
- Haiku labels are already **validated** against the rubric; a new judge needs a fresh relabel-A/B.

So the scoring recommendation is a **ladder, not a jump**: prove Haiku+batch+cache first (low risk,
real 2x, quota separation). *Then*, if bulk-relabel cost is still the bottleneck, A/B **Gemini 2.5
Flash-Lite** or **GPT-5 mini** as the labeler against Haiku on the same 100 rows and adopt only if the
score distribution holds. Do not skip straight to the cheapest model.

---

## 4. Why writing is a different (and riskier) call

- **Human-eval writing favors Claude.** A blind test (134 participants) had Claude winning the
  writing-specific rounds by 35–54 percentage points; commentary consistently names Claude for
  "professional writing, editorial consistency, voice and tone." GPT-5 is the credible challenger
  ("stronger for tone range and versatility"). Cheap Flash/nano tiers are framed as speed/coding
  picks, **not** prose picks.
- **The hidden migration cost is the real story.** Leaving Claude for CV/cover generation is *not* a
  model swap. It requires:
  1. Re-implementing the tool loop + injecting `CLAUDE.md` as a system prompt (the CLI does this for
     free; it is what prevents fabricated employers/metrics — 2026-07-15 A/B).
  2. **Re-tuning every SACRED prompt from scratch** — `tailor_cv.md`, `tailor_cover.md`, `outreach.md`,
     `interview.md`. A prompt tuned for Claude does **not** port; each provider has different
     instruction-following, formatting defaults, and refusal behavior.
  3. Re-validating the fabrication bans, the 6-second-test opener, the language/header rules, the
     em-dash ban — on the new model, per prompt.
- **Every one of those is a change-controlled, A/B-gated edit** under `CLAUDE.md`. The cost of getting
  it wrong is a fabricated fact on a live application.

**Verdict for writing:** stay on Claude. Cost-optimize *within* Claude — Haiku for the single-shot
build (already the default `cv_engine=single_shot`, `cv_model=haiku`), escalate to Sonnet only if
Haiku quality proves thin. Treat **GPT-5** (not a cheap model) as the *only* worth-testing non-Claude
challenger, and only through the existing `cv_eval.py` harness.

---

## 5. Prompt engineering — why "one prompt" won't port

The user is right that this is a prompt-engineering problem, and that is exactly why cross-provider is
expensive:

- **Prompts are model-specific.** Claude follows literal system-prompt instructions and needs *less*
  anti-"AI-slop" nudging; GPT tends to preamble and needs explicit "no preface" instructions; Gemini
  Flash tiers over-format (headers, bullets) unless told not to. The same `tailor_cv.md` produces
  different output shapes on each.
- **Structured output differs.** Claude uses `output_config.format`; OpenAI uses `response_format` /
  JSON schema; Gemini uses `responseSchema`; xAI/Kimi are OpenAI-compatible. The scoring path's
  `_extract_json_array` fallback works everywhere, but strict JSON mode is per-provider.
- **Thinking / effort knobs differ.** Haiku uses `budget_tokens` (we set 0 for scoring, 4096 for CVs);
  GPT-5 uses `reasoning_effort`; Gemini uses `thinkingBudget`; each needs its own tuning pass.
- **Implication:** any model added must get its **own prompt variant**, A/B-measured against the Claude
  baseline with `cv_eval.py` (writing) or a relabel-score-diff (scoring). Budget one tuning cycle per
  model per prompt — this is the real cost, not the token price.

---

## 6. Token-efficiency toolkit (applies to whichever model)

1. **Batch API −50%** — every provider offers it (Claude, OpenAI, Gemini). Use for all offline
   scoring/labeling. Not for watcher / verify_high / interactive CV builds (need sync answer).
2. **Prompt caching** — cache the stable prefix (rubric + system, or `CLAUDE.md` for writing):
   Claude −90% (4096-tok min on Haiku), Grok 75% cache, Kimi K3 ~90%, OpenAI ~90% auto, Gemini
   implicit + explicit. Verify the hit (`cache_read` > 0) — a byte-drift silently disables it.
3. **Structured outputs / JSON mode** — guarantees parseable scores, removes retry-on-malformed waste.
4. **Thinking off / minimal** for mechanical scoring (already `max_thinking_tokens=0`); thinking *on*
   (4096) for writing where it measurably helps quality (2026-07-20 finding).
5. **Prompt slimming** — already done: 2500-char description cap, minified JSON, only-scored-fields.
6. **Async / concurrency** — sync path already parallelizes (`cv_build_workers=5`, label
   `ThreadPoolExecutor(10)`). Batch API replaces concurrency with a single submit+poll for bulk work.

---

## 7. Speed / async notes

- **Throughput ranking (offline bulk):** Grok 4.1 Fast ~455 tok/s > Gemini Flash-Lite ~275 > GPT-5
  ~60–100. But for **batched offline scoring, per-request speed barely matters** — batch latency is
  ~minutes-to-1h regardless; you optimize *cost*, not tok/s.
- **Speed matters only on the sync paths:** watcher, verify_high, on-demand CV build. There, Haiku is
  already fast enough; Grok 4.1 Fast would be the raw-speed champion if a sync path ever became the
  bottleneck.
- **Async today:** the pipeline is already concurrent (thread pools). The API backend's win is the
  **batch** submit for bulk labeling, not more threads.

---

## 8. Recommendation

| Workload | Recommendation | Model ladder | Rationale |
|---|---|---|---|
| **Bulk scoring / labeling** | Move to API backend, phase 1 | Haiku 4.5 + **batch + cache** → *then* A/B Gemini 2.5 Flash-Lite / GPT-5 mini | Real ~2x now, low risk, quota separation; cheaper model only after a passed relabel-A/B |
| **verify_high (precision re-score)** | Keep sync; consider Sonnet 5 | Haiku → **Sonnet 5** | 12 jobs/run, precision worth the 3x on the ones you might apply to |
| **watcher** | Keep sync, cheapest fast model | Haiku (or Grok 4.1 Fast if speed-bound) | Low volume, needs a fast answer |
| **CV / cover / outreach / interview** | **Stay on Claude** | Haiku single-shot → **Sonnet** if thin; test **GPT-5** only via `cv_eval.py` | SACRED prose + fabrication risk + full re-tune cost; cheap models are a quality gamble |

**Do next (still planning — no code):** (1) measure the rubric prefix token count to settle the cache
question; (2) stand up the scoring A/B harness (Haiku-API vs Haiku-CLI, same 100 rows); (3) only if
bulk cost still bites, extend that harness to Gemini Flash-Lite / GPT-5 mini as challenger labelers.
Writing stays a Claude-internal Haiku↔Sonnet decision until there's a concrete reason to spend the
migration cost on a GPT-5 A/B.

---

## Sources

- [OpenAI API pricing (aipricing.guru)](https://www.aipricing.guru/openai-pricing/) · [GPT-5 nano (pricepertoken)](https://pricepertoken.com/pricing-page/model/openai-gpt-5-nano) · [GPT-5 mini (pricepertoken)](https://pricepertoken.com/pricing-page/model/openai-gpt-5-mini)
- [Gemini pricing (cloudzero)](https://www.cloudzero.com/blog/gemini-pricing/) · [Gemini 2.5 Flash-Lite (pricepertoken)](https://pricepertoken.com/pricing-page/model/google-gemini-2.5-flash-lite) · [Google Gemini API pricing (tldl.io)](https://www.tldl.io/resources/google-gemini-api-pricing)
- [xAI Grok pricing (aipricing.guru)](https://www.aipricing.guru/xai-pricing/) · [Grok 4.1 Fast vs Gemini 2.5 Flash (artificialanalysis.ai)](https://artificialanalysis.ai/models/comparisons/grok-4-1-fast-vs-gemini-2-5-flash)
- [Kimi K3 pricing (eesel)](https://www.eesel.ai/blog/kimi-k3-pricing) · [Moonshot API pricing (benchlm)](https://benchlm.ai/moonshot/api-pricing) · [DeepSeek vs Kimi cost (costbench)](https://costbench.com/compare/deepseek-vs-moonshot-kimi-api/)
- [Claude vs ChatGPT vs Gemini 6-round scorecard (teamai)](https://teamai.com/blog/ai-automation/claude-vs-chatgpt-vs-gemini-whos-winning-the-ai-war-in-2026/) · [Gemini 3 Flash vs GPT-5.2 vs Claude vs Grok (Medium)](https://medium.com/@cognidownunder/gemini-3-flash-vs-gpt-5-2-vs-claude-opus-4-5-vs-grok-4-1-the-real-winner-surprised-me-b43d0688452e)
