# templates — the CV layout, filled per user

**One template, `base_cv.html`** (decided 2026-07-28). The reference instance kept two — `dev` and
`quant` — and `PROFILE_CAPTURE_PLAN.md` §6.5 planned for 2–3 with the user picking. That is
superseded: a user has **one** template and the per-job tailoring adapts it, which is what the
tailoring prompt was doing between the two anyway. It also means onboarding needs no eighth page.

## Status

**Identity removed, body not yet rendered.** The header is `<!--NAME-->` and `<!--CONTACT-->`, and
no name, address, phone, email or link from the reference instance survives — checked by
`tests/test_prompt_templates.py`.

**What remains:** the body still carries the previous owner's profile paragraph, experience entries
and education. Those sections need replacing with markers filled from `ProfileSchema` — the same
deterministic-render job as `libs/scoring/rubric.py` and `facts.py`:

| Marker | Filled from |
|---|---|
| `<!--NAME-->` | `identity.name` |
| `<!--CONTACT-->` | `identity.location`, `identity.links` |
| `<!--PROFILE-->` | a paragraph the tailoring prompt writes per job |
| `<!--EXPERIENCE-->` | `experience[]` — role, org, dates, bullets |
| `<!--EDUCATION-->` | `education[]` |
| `<!--SKILLS-->` | `skills` |

Until that exists, **docgen must not run**: it would produce a correctly-formatted CV carrying
somebody else's employment history. The guard is that no docgen worker exists yet.

## Why the layout is not generated

It is hand-tuned and load-bearing: A4 page rules, font paths, and the `@page` margin rule that stops
Chrome drawing a header over the content. Generated-per-user HTML would be unpredictable exactly
where predictability is the requirement, and every layout bug would become a per-user bug
(§6.5 — *buy, do not generate*).
