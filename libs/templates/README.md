# templates — the CV layout, filled per user

**One template, `base_cv.html`** (decided 2026-07-28). The reference instance kept two — `dev` and
`quant` — and `PROFILE_CAPTURE_PLAN.md` §6.5 planned for 2–3 with the user picking. That is
superseded: a user has **one** template and the per-job tailoring adapts it, which is what the
tailoring prompt was doing between the two anyway. It also means onboarding needs no eighth page.

## Current state

**Rendered from the profile since 2026-07-28.** The template carries no identity and no employment
history — it is markers and layout. `cv.py` fills it:

| Marker | Filled from |
|---|---|
| `<!--NAME-->` | `identity.name` |
| `<!--CONTACT-->` | `identity.location`, `identity.links` |
| `<!--PROFILE-->` | composed from `targets` + `skills`; the tailoring prompt rewrites it per job |
| `<!--SKILLS-->` | `skills.*` → Core Competencies |
| `<!--EXPERIENCE-->` | `experience[]` — role, org, dates, stack, bullets, metrics |
| `<!--PROJECTS-->` | `projects[]` |
| `<!--EDUCATION-->` | `education[]` — no dates, thesis labelled "Research:" |
| `<!--LANGUAGES-->` | `constraints.languages` — spoken, never `skills.languages` |
| `<!--CERTIFICATIONS-->` | `certifications[]` — name, issuer, year |

```python
from libs.templates.cv import render_cv
html = render_cv(profile)                      # the full CV, all of their real experience
html = render_cv(profile, summary="…")         # with the profile paragraph supplied
```

**What it produces is the input to tailoring, not the output.** `tailor_cv.md` receives the rendered
document as its BASE TEMPLATE and returns a tailored copy — it cuts to the 4–6 entries that earn
their place for one job. So the renderer's job is completeness and truth, never selection.

Three rules the renderer keeps and the next change must not break:

- **A section with no data is omitted whole.** An empty "Projects" heading survives tailoring into a
  PDF an employer reads, and it looks like what it is: a bug in the product.
- **Everything from the profile is escaped.** A profile is captured from uploaded documents, which
  are untrusted text, and this HTML is handed to a headless Chrome.
- **Nothing is defaulted.** No language, no eligibility, no certification. An invented qualification
  on a real application is the one failure the whole facts-block design exists to prevent.

**Certifications is a schema field, not a dropped section.** It was briefly cut for want of one,
which was the wrong direction — the section vanishing takes a real credential off a real CV. So
`ProfileSchema` grew `certifications` (schema version 2) and capture fills it automatically,
because the model is handed the schema rather than a hand-written field list.

**Lato is resolved by name, not by `@font-face url()`** (changed 2026-07-28 with the docgen build).
The reference instance carried four url()s relative to the document, so every build had to rewrite
the path depth to match wherever it had just written the file — and a wrong depth is silent: Chrome
falls back to Arial and produces a correct-looking CV in the wrong typeface. `Dockerfile.docgen`
installs `fonts-lato` and **fails the build** if `fc-list` cannot then see it, which is what turns
that silent fallback into an error where somebody is watching.

## Why the layout is not generated

It is hand-tuned and load-bearing: A4 page rules, font paths, the `.entry` break rules that keep an
entry off a page boundary, and the `@page` margin rule that stops Chrome drawing a header over the
content. Generated-per-user HTML would be unpredictable exactly where predictability is the
requirement, and every layout bug would become a per-user bug (§6.5 — *buy, do not generate*).

The tailoring prompt is told the same thing from the other side: CSS is managed externally, it must
not emit a `<style>` block, and the pipeline restores this one after the call.

## Tests

`tests/test_cv_render.py` — markers, section omission, escaping, and the fields that must not be
confused. `tests/test_prompt_templates.py` covers the template's identity and the layout rules.
