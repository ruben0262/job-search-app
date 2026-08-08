"""The cover-letter checks, against the letter that made them necessary.

`CLOUDBEDS` is the real letter delivered on 2026-06-22 for the Cloudbeds Staff Software Engineer
posting, verbatim. It kept every rule the live prompt phrased as a pass over the text and broke
every rule that asked it to weigh the draft, which is the whole reason this module exists. If a
check here stops firing on it, the check has been broken, not the letter fixed.
"""
from __future__ import annotations

from libs.scoring import coverchecks

# ── the letter as delivered ───────────────────────────────────────────────────────────────────

CLOUDBEDS = """<!DOCTYPE html>
<html lang="en"><head><style>.name { font-weight: bold; }</style></head>
<body>
<div class="header"><div class="name">Ruben Lekli</div>
  <div class="contact">lekliruben@gmail.com +447729711592 LinkedIn</div></div>
<p class="date">22 June 2026</p>
<p>Dear Cloudbeds Hiring Team,</p>
<p>Cloudbeds builds Revenue Intelligence systems that transform how hoteliers understand demand
and make pricing decisions. I have spent recent years doing similar work, shipping data-intensive
platforms that process complex signals reliably at scale. The Staff Software Engineer role appeals
to me precisely because the technical challenges, spanning distributed systems to event-driven
services to analytical stores, are exactly where I have developed deepest expertise.</p>
<p>At FlowSolutions, I led development of a multi-channel platform routing real-time messages
across WhatsApp, Telegram, Discord, and Instagram through a Go backend with WebSocket hubs, REST
APIs, and event-driven service boundaries. I built a prompt engineering evaluation platform
orchestrating A/B testing across multiple LLM models with automated refinement loops. Both
projects taught me to value strong service boundaries, clear data contracts, and observability at
every layer.</p>
<p>The deeper technical fit is quantitative systems work. I built a production backtesting
framework processing multi-year time series with walk-forward validation across 71 out-of-sample
windows and bias-corrected Sharpe metrics. The engine runs on ETL pipelines ingesting market data
from multiple sources, MongoDB analytical stores, and real-time execution logging. I learned to
balance analytical correctness with operational reliability, never cutting corners on statistical
bias, and building systems that run robustly 24/7.</p>
<p>I led a six-person team on a complex software project, reinforcing what I value most in
engineering. Staying hands-on in the codebase while setting architecture and raising the technical
effectiveness of people around me is how I work best. I would welcome the opportunity to bring
this approach to your team.</p>
<p>Kind regards,</p>
<p class="signature">Ruben Lekli</p>
</body></html>"""

# The CV the letter was built from, as plain text, cut to the passages the letter drew on.
CV_TEXT = ("Ruben Lekli Budapest, Hungary. Architected and deployed distributed multi-channel lead "
           "qualification platform using Go (Fiber) and WebSocket hubs routing real-time messages "
           "across five channels in production. Built production Python backtesting framework "
           "supporting multi-asset, multi-factor portfolio construction with walk-forward "
           "validation across 71 out-of-sample windows; implemented DSR/PSR statistical bias "
           "correction. Led 6-person team delivering custom IDE and version control system.")

# A letter that keeps every rule the live prompt states, written by hand as the control. It is
# not necessarily a *good* letter by a reader's standards, only a rule-abiding one — which is
# exactly the point: the checks must be passable, and passing them is not the same as being good.
CLEAN = """<!DOCTYPE html>
<html lang="en"><head><style>.name { font-weight: bold; }</style></head>
<body>
<div class="header"><div class="name">Ruben Lekli</div>
  <div class="contact">lekliruben@gmail.com +447729711592</div></div>
<p>Dear Cloudbeds Hiring Team,</p>
<p>Last winter I spent three days tracing why a pricing feed went stale every Tuesday at noon.
The answer was a cache nobody owned, and rebuilding that path as one query is the kind of work
the Staff Software Engineer post at Cloudbeds is made of. I have been doing that sort of thing
for a while now.</p>
<p>Most of what I build carries signals that turn into money, which changes how careful you have
to be. One system I wrote ingests market data through several pipelines, keeps it somewhere it
can be queried, and writes down every execution as it happens. It has run without me touching it
for eleven months, across 71 separate test windows before it ever went live.</p>
<p>The part I like most is the debugging, and it always has been. A colleague once showed me two
dashboards that disagreed by four percent, and the cause took a weekend and one very long query
to find. It was a stale join key, and I still think about it.</p>
<p>Cloudbeds is working on pricing problems at a scale I have not had access to yet, and I want
in on that. Give me a quarter and I will know your data better than the dashboards do.</p>
<p>Kind regards,</p>
<p class="signature">Ruben Lekli</p>
</body></html>"""


# What Haiku actually emits, taken from the first real run: the prose is wrapped in a container
# div, and the contact line sits in the header rather than in a paragraph. A non-greedy container
# pattern swallows every paragraph as one block, which collapsed 7 of the first 8 letters and
# reduced one to nine words while the suite stayed green — the fixtures above were all flat.
NESTED = """<!DOCTYPE html>
<html><head><style>.profile p { margin: 0 0 10px; }</style></head>
<body>
<div class="cv-header">
  <div class="cv-name">Ruben Lekli</div>
  <div class="cv-contact"><span>lekliruben@gmail.com</span><span>+447729711592</span></div>
</div>
<hr class="header-rule">
<div class="profile">
  <p>Dear Hiring Team,</p>
  <p>At FlowSolutions I architected a platform routing real-time messages across five production
  channels. The AI and MLOps Engineer post at Logicalis is the next step for someone who built
  both the integration layer and the infrastructure under it.</p>
  <p>I spent the last year building a classifier that reached an AUC of 0.82 and retrains itself
  on feedback. It has not needed me since March.</p>
  <p>Yours sincerely,</p>
  <p>Ruben Lekli</p>
</div>
</body></html>"""


def codes(report: coverchecks.Report) -> set[str]:
    return {finding.code for finding in report.findings}


def test_paragraphs_survive_a_container_div():
    """The failure that voided the first measured run: prose nested inside a wrapper."""
    blocks = coverchecks.paragraphs(NESTED)

    assert len(blocks) == 2, blocks
    assert blocks[0].startswith("At FlowSolutions")
    assert blocks[1].endswith("It has not needed me since March.")
    assert not any("Yours sincerely" in block for block in blocks)
    assert not any("lekliruben@gmail.com" in block for block in blocks)


def test_a_nested_letter_is_measured_not_silently_emptied():
    """A parser that finds nothing must not read as a letter with no violations."""
    report = coverchecks.check(NESTED, company="Logicalis", title="AI / MLOps Engineer")

    assert report.body_words > 60, report.body_words
    assert "TOO_SHORT" in codes(report)          # it genuinely is short, and that is now visible


# ── the shape of the letter ───────────────────────────────────────────────────────────────────

def test_body_is_the_prose_between_greeting_and_signoff():
    """The header, contact line, date and signature are not prose and must not be measured."""
    blocks = coverchecks.paragraphs(CLOUDBEDS)

    assert len(blocks) == 4
    assert blocks[0].startswith("Cloudbeds builds Revenue Intelligence")
    assert blocks[-1].endswith("bring this approach to your team.")
    assert not any("lekliruben@gmail.com" in block for block in blocks)
    assert not any("22 June 2026" in block for block in blocks)


def test_the_contact_line_colon_is_not_counted_against_the_body():
    """A letter whose only colon is in the contact block scores zero colons."""
    letter = CLEAN.replace("<div class=\"contact\">lekliruben@gmail.com",
                           "<div class=\"contact\">email: lekliruben@gmail.com")

    assert not coverchecks.colons(coverchecks.body(letter))


# ── what the delivered letter actually did ────────────────────────────────────────────────────

def test_the_delivered_letter_keeps_the_literal_mechanical_rules():
    """Colons, apostrophes, word count and a number: four literal passes, four kept.

    The fifth mechanical rule is the only one of the five that is not a literal pass — it asks
    where a phrase sits across sentences, and accepts the title's `closest honest equivalent` —
    and it is the one of the five the model missed. That is the thesis of this whole module in a
    single letter, so it is asserted rather than described.
    """
    report = coverchecks.check(CLOUDBEDS, company="Cloudbeds", title="Staff Software Engineer")

    assert [finding.code for finding in report.mechanical] == ["OPEN_NO_TITLE"], report.counts()


def test_the_delivered_letter_breaks_the_judgement_rules():
    """8 rules, all of them ones that asked the model to weigh the whole draft."""
    report = coverchecks.check(CLOUDBEDS, company="Cloudbeds", title="Staff Software Engineer",
                               cv_text=CV_TEXT)
    found = codes(report)

    assert "OPEN_DESCRIBES_COMPANY" in found      # "Cloudbeds builds Revenue Intelligence..."
    assert "TELLS_THE_MATCH" in found             # "The deeper technical fit is..."
    assert "WEAK_CLOSE" in found                  # "I would welcome the opportunity..."
    assert "BANNED_PHRASE" in found               # "run robustly 24/7"
    assert "EXHAUSTIVE_LIST" in found             # "WhatsApp, Telegram, Discord, and Instagram"
    assert "DENSE_PARAGRAPH" in found             # a four-sentence paragraph
    assert "CV_LIFT" in found                     # "walk-forward validation across 71..."


def test_the_delivered_letter_tells_the_match_more_than_once():
    """Three separate announcements of the fit, in a prompt that bans it five times over."""
    tells = coverchecks.telling_the_match(coverchecks.body(CLOUDBEDS))

    assert len(tells) >= 3, [str(finding) for finding in tells]


def test_a_clean_letter_trips_nothing():
    """The checks must be passable, or they measure nothing but their own strictness."""
    report = coverchecks.check(CLEAN, company="Cloudbeds", title="Staff Software Engineer",
                               cv_text=CV_TEXT)

    assert report.clean, report.counts()


# ── the individual rules, each against something that is not the Cloudbeds letter ──────────────

def test_an_opening_that_leads_with_work_is_not_flagged():
    assert not coverchecks.restating_opening(
        "Last winter I rebuilt a pricing feed that went stale every Tuesday at noon.")


def test_an_opening_that_restates_the_advert_is_flagged():
    report = coverchecks.restating_opening(
        "Amberelectric is looking for a Full Stack Engineer to join the platform team.")

    assert [finding.code for finding in report] == ["OPEN_RESTATES_POSTING"]


def test_a_company_description_opening_is_flagged_whatever_the_verb():
    for opener in ("Cloudbeds builds Revenue Intelligence systems for hotels.",
                   "Monzo is a bank that started on mobile.",
                   "Stripe powers payments for millions of businesses."):
        assert coverchecks.restating_opening(opener), opener


def test_cv_lifts_catch_a_copied_bullet_and_ignore_a_shared_term():
    lifted = coverchecks.cv_lifts(
        "I ran walk-forward validation across 71 out-of-sample windows on that book.", CV_TEXT)
    shared = coverchecks.cv_lifts("The service uses WebSocket hubs in production.", CV_TEXT)

    assert lifted
    assert not shared


def test_an_invented_figure_is_caught_and_a_restated_one_is_not():
    """Both examples are real output from the `b-anecdote` arm, which forced a concrete moment
    and got concrete numbers the candidate cannot support."""
    invented = coverchecks.unsupported_numbers(
        "The pipeline serves thousands of customers and ingests 10,000+ records per second.",
        CV_TEXT)

    assert [finding.code for finding in invented] == ["NUMBER_NOT_IN_CV"]
    assert "10,000+" in invented[0].detail


def test_the_same_figure_written_differently_is_the_same_figure():
    """`10k+` in the CV supports `10,000` in the letter. Only a genuinely new number counts."""
    cv = "deduplication layer handling 10k+ records in under 1ms"

    assert not coverchecks.unsupported_numbers("It handled 10,000 records without complaint.", cv)
    assert coverchecks.unsupported_numbers("It handled 40,000 records without complaint.", cv)


def test_an_honest_metric_from_the_cv_passes():
    assert not coverchecks.unsupported_numbers(
        "That framework ran across 71 out-of-sample windows.", CV_TEXT)


def test_honesty_findings_are_counted_apart_from_style():
    """A fabricated figure is not a style violation and must not be averaged in with one."""
    report = coverchecks.check(CLOUDBEDS, company="Cloudbeds", title="Staff Software Engineer",
                               cv_text=CV_TEXT)

    assert all(finding.code not in coverchecks.MECHANICAL for finding in report.honesty)
    assert all(finding.code not in coverchecks.JUDGEMENT for finding in report.honesty)


def test_word_count_uses_the_band_the_prompt_asks_for_not_the_scan_threshold():
    """The live prompt targets 200-260 and its final scan only cuts above 280, so a 275-word
    letter passes the scan while missing the target. The band is what gets measured."""
    assert coverchecks.word_count(" ".join(["word"] * 275))[0].code == "TOO_LONG"
    assert not coverchecks.word_count(" ".join(["word"] * 240))


def test_a_dense_paragraph_is_counted_per_paragraph():
    findings = coverchecks.paragraph_density(
        ["One. Two. Three. Four.", "A short one. And another."])

    assert [finding.code for finding in findings] == ["DENSE_PARAGRAPH"]


def test_counts_are_one_line_per_rule():
    report = coverchecks.check(CLOUDBEDS, company="Cloudbeds", title="Staff Software Engineer")

    assert report.counts()["TELLS_THE_MATCH"] >= 3
    assert report.body_words == len(coverchecks.words(coverchecks.body(CLOUDBEDS)))
