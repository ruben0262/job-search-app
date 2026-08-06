"""The objective half of CV evaluation — ported from the reference's `pipeline/cv_eval.py`.



**The judge prompt says these checks are already done.** `eval_cv.md` opens with "the objective

checks (keyword coverage, page count, bullet metrics) are already done for you and summarised

below — do NOT re-grade those", and the platform was handing it a character count and nothing

else. A model told a measurement exists and then not shown it does not abstain; it estimates, and

it estimates from the same text it is supposed to be judging subjectively.



## What this is, and what it is not



**It does not claim to be an ATS.** Applicant tracking systems do several different jobs and only

some are mechanical: the parse from PDF to text to fields is deterministic and either works or the

CV arrives as mush; knockout questions are form logic; recruiter boolean search over the parsed

text is exact-match; and the match/relevance score some vendors put on top is increasingly a

learned, semantic thing that no local check can reproduce.



So this measures only the part that holds under **both** regimes:



- **Is the text extractable at all** — hidden content, unreadable output. Mechanical, and a hard

  failure however clever the ranker downstream is.

- **Does the CV contain the posting's own terms** — decisive under a boolean search, still helpful

  under a semantic one.



The semantic half is the judge's, and `eval_cv.md` scores it the right way round: `keyword_

integration` rewards terms woven into real achievements and penalises stuffing.



**Nothing here is a target to maximise.** These numbers go to the judge as evidence and are never

reported as a score of their own. Optimising a CV for exact-term coverage *is* keyword stuffing,

which loses under a semantic ranker and with a human reader both — so a run that raised coverage

and dropped `human_voice` has made the CV worse, and the only number that says so is the judge's.



Lifted from the reference rather than reinvented. Every threshold, weight, stopword and tech term

in here came from reading real postings against real ATS results, and a fresh implementation would

be a different instrument wearing the same name.



The one deliberate change: `pdf_text` and its pypdf dependency are not ported. The platform already

has `services/docgen/pdf.py` doing PDF extraction, and the caller passes the text in.

"""

from __future__ import annotations

import re
from typing import Any


def _strip_style(html: str) -> str:

    """Drop `<style>` and `<script>` bodies before any text or visibility check.



    Without it, CSS rules read as document text and every `display:none` in a stylesheet counts as

    hidden content — the check would fire on every CV ever built.

    """

    return re.sub(r"(?is)<style\b.*?</style>", " ", html)





def html_to_text(html: str) -> str:

    """Visible text of the CV HTML — styles and tags removed, whitespace collapsed.



    The reference's own extractor rather than docgen's, and not because docgen's is worse: `libs`

    may not import `services`, and the boundary is the point. This module is the *measurement*, and

    a measurement that reaches into the thing it measures is one refactor away from agreeing with

    it by construction.

    """

    text = _strip_style(html)

    text = re.sub(r"(?is)<(script|head)\b.*?</\1>", " ", text)

    text = re.sub(r"<[^>]+>", " ", text)

    text = (text.replace("&amp;", "&").replace("&middot;", "·").replace("&ndash;", "-")

            .replace("&mdash;", "—").replace("&nbsp;", " ").replace("&rsquo;", "'")

            .replace("&ldquo;", '"').replace("&rdquo;", '"').replace("&eacute;", "e"))

    return re.sub(r"\s+", " ", text).strip()





TECH_PHRASES = [

    "machine learning", "deep learning", "reinforcement learning", "computer vision",

    "natural language processing", "large language models", "prompt engineering",

    "feature engineering", "time series", "data pipeline", "data engineering",

    "rest api", "rest apis", "restful api", "web services", "message queue",

    "event driven", "distributed systems", "microservices", "system design",

    "unit testing", "integration testing", "end to end testing", "test automation",

    "continuous integration", "continuous delivery", "version control",

    "object oriented", "functional programming", "design patterns", "code review",

    "cloud infrastructure", "infrastructure as code", "container orchestration",

    "high frequency", "low latency", "real time", "market making", "order flow",

    "quantitative research", "quantitative developer", "risk management",

    "portfolio optimisation", "portfolio optimization", "trading systems",

    "statistical modelling", "statistical modeling", "signal processing",

    "back end", "backend engineering", "front end", "full stack", "web development",

    "payment processing", "third party", "on chain", "digital assets",

]



# Single-token tech / skill terms.

TECH_TOKENS = {

    "python", "go", "golang", "javascript", "typescript", "react", "node", "nodejs",

    "java", "c", "cpp", "rust", "ruby", "php", "kotlin", "swift", "scala", "html", "css",

    "sql", "nosql", "postgresql", "postgres", "mysql", "mongodb", "sqlite", "redis",

    "docker", "kubernetes", "k8s", "linux", "aws", "gcp", "azure", "cloudflare",

    "terraform", "ansible", "nginx", "kafka", "rabbitmq", "grpc", "graphql", "websocket",

    "rest", "api", "apis", "oauth", "oauth2", "jwt", "saml", "sso",

    "git", "github", "gitlab", "ci", "cd", "cicd", "devops", "prometheus", "grafana",

    "numpy", "pandas", "polars", "scipy", "scikit", "sklearn", "pytorch", "tensorflow",

    "keras", "optuna", "xgboost", "ml", "ai", "llm", "llms", "nlp", "genai", "embeddings",

    "fiber", "fastapi", "flask", "django", "express", "spring", "playwright", "pytest",

    "selenium", "cypress", "stripe", "openai", "claude", "anthropic", "langchain",

    "etl", "elt", "spark", "airflow", "dbt", "snowflake", "databricks", "tableau",

    "backtesting", "backtest", "quant", "trading", "derivatives", "equities", "futures",

    "var", "sharpe", "garch", "arbitrage", "blockchain", "solidity", "web3",

    "microservices", "async", "concurrency", "multithreading", "agile", "scrum", "tdd",

    "bloomberg", "matlab", "r", "sas", "excel", "vba", "bash", "powershell",

}



# Synonym / normalisation classes: matching either surface form counts as a hit.

SYNONYMS = [

    {"go", "golang"}, {"javascript", "js"}, {"typescript", "ts"},

    {"machine learning", "ml"}, {"artificial intelligence", "ai"},

    {"large language models", "large language model", "llm", "llms", "genai", "gen ai"},

    {"natural language processing", "nlp"}, {"kubernetes", "k8s"},

    {"postgresql", "postgres"}, {"continuous integration", "ci"},

    {"continuous delivery", "continuous deployment", "cd"}, {"ci/cd", "cicd", "ci cd"},

    {"rest", "restful", "rest api", "rest apis", "restful api"},

    {"websocket", "websockets", "ws"}, {"oauth", "oauth2", "oauth 2"},

    {"node", "nodejs", "node.js"}, {"scikit", "sklearn", "scikit-learn", "scikit learn"},

    {"test driven development", "tdd"}, {"real time", "real-time", "realtime"},

    {"back end", "backend", "back-end"}, {"front end", "frontend", "front-end"},

    {"full stack", "full-stack", "fullstack"}, {"c++", "cpp"},

]



STOPWORDS = {

    "the", "and", "for", "with", "you", "your", "our", "are", "will", "have", "has",

    "this", "that", "from", "into", "over", "under", "about", "their", "them", "they",

    "who", "what", "when", "where", "which", "while", "would", "could", "should", "can",

    "all", "any", "but", "not", "yes", "how", "why", "out", "off", "per", "via", "etc",

    "a", "an", "of", "in", "on", "to", "as", "at", "by", "or", "is", "be", "we", "us",

    "role", "team", "work", "working", "join", "looking", "help", "build", "building",

    "experience", "years", "year", "strong", "good", "great", "new", "using", "use",

    "skills", "ability", "able", "including", "across", "within", "well", "like", "one",

    "company", "job", "position", "candidate", "candidates", "apply", "must", "plus",

    "based", "make", "made", "get", "got", "need", "needs", "want", "day", "days", "week",

    "more", "most", "other", "some", "such", "than", "then", "these", "those", "also",

    "you'll", "we're", "we'll", "you're", "it's", "its", "high", "level", "part", "full",

    # Role / seniority words — too generic to be useful ATS keywords on their own.

    "developer", "engineer", "engineering", "graduate", "junior", "senior", "mid",

    "lead", "staff", "principal", "software", "intern", "internship", "analyst", "manager",

    # Posting-metadata words (leak from job_context.txt headers, not the JD substance).

    "posting", "applicants", "remote", "hybrid", "onsite", "on-site", "location", "source",

    "url", "date", "posted", "deadline", "language", "english", "cover", "letter",

    "required", "documents", "method", "contact", "email", "screening", "questions",

    "title", "employment", "type", "seniority", "industry", "function", "mentioned",

    "appears", "visible", "provided", "specified", "unspecified", "none", "enabled",

    "responsibilities", "qualifications", "requirements", "description", "details",

    "fit", "score", "model", "understand", "assessment", "salary", "target", "band",

}



# Strong past-tense action verbs a compliant experience bullet should open with.

STRONG_VERBS = {

    "built", "shipped", "designed", "developed", "engineered", "architected", "created",

    "delivered", "deployed", "launched", "implemented", "integrated", "automated",

    "reduced", "cut", "improved", "optimised", "optimized", "scaled", "accelerated",

    "trained", "modelled", "modeled", "simulated", "analysed", "analyzed", "backtested",

    "provisioned", "containerised", "containerized", "hardened", "instrumented",

    "led", "owned", "coordinated", "drove", "established", "wrote", "rewrote", "refactored",

    "prototyped", "constructed", "wired", "achieved", "reached", "grew", "generated",

    "streamlined", "migrated", "debugged", "profiled", "captured", "reverse-engineered",

    "handled", "processed", "routed", "orchestrated", "published", "researched", "applied",

}



# Task-description openers that must NOT start a bullet (banned by tailor_cv.md).

BANNED_OPENERS = [

    "led development of", "worked on", "responsible for", "was involved in",

    "helped with", "assisted in", "participated in", "tasked with", "in charge of",

]



# Cliches / filler banned by tailor_cv.md + 03-writing-style.md.

BANNED_CLICHES = [

    "institutional-quality", "research protocols", "end-to-end ownership", "end to end",

    "leveraging", "leverage my", "robust", "seamlessly", "seamless", "cutting-edge",

    "cutting edge", "best-in-class", "best in class", "results-driven", "results driven",

    "passionate", "spanning", "spearheaded", "dynamic", "innovative", "ambitious",

    "proactive", "hit the ground running", "drive results", "synergies", "delve",

    "world-class", "game-changing", "thought leader", "self-starter", "go-getter",

    "detail-oriented", "team player", "wheelhouse", "hard-working", "track record of",

]



# Provenance tells that must NEVER appear anywhere — a hard gate. Describing data/work as

# AI-produced raises "how was this made?" questions (tailor_cv.md, 03-writing-style.md).

PROVENANCE_TELLS = [

    "llm-labelled", "llm labelled", "llm-labeled", "llm labeled", "ai-labelled",

    "ai labelled", "ai-labeled", "claude-labelled", "model-labelled", "auto-labelled",

    "llm-generated", "llm generated", "ai-generated data", "machine-generated data",

]



# Framing tells — banned when they describe EXPERIENCE (profile/bullets): work must not read

# as coursework (CLAUDE.md). A soft penalty, NOT a gate, and only checked against prose — a

# "Relevant coursework:" line in the Education section is legitimate and must not be flagged.

FRAMING_TELLS = [

    "studied", "coursework", "as part of a module", "as part of my degree",

    "in a module", "for a module", "for my degree", "for coursework",

]



LANGUAGES_CANONICAL = "english (native), hungarian (native)"

PORTFOLIO_HOST = "rubenlekli.com"



# Blend + gate knobs.

# ---------------------------------------------------------------------------

# Text extraction helpers

# ---------------------------------------------------------------------------





def _tokens(text: str) -> list[str]:

    return re.findall(r"[a-z0-9][a-z0-9+.#-]*", text.lower())





def _norm(term: str) -> str:

    return re.sub(r"[\s_-]+", " ", term.lower()).strip()





def _expand(term: str) -> set[str]:

    """All synonym surface forms for a term (for presence matching)."""

    n = _norm(term)

    forms = {n}

    for cls in SYNONYMS:

        if n in {_norm(x) for x in cls}:

            forms |= {_norm(x) for x in cls}

    return forms





def _present(term: str, haystack_norm: str) -> bool:

    """Is any synonym form of `term` present in normalised text (word-boundary for tokens,

    substring for phrases)."""

    for form in _expand(term):

        if " " in form:

            if form in haystack_norm:

                return True

        elif re.search(rf"(?<![a-z0-9]){re.escape(form)}(?![a-z0-9])", haystack_norm):

            return True

    return False





def _count(term: str, haystack_norm: str) -> int:

    total = 0

    for form in _expand(term):

        if " " in form:

            total += haystack_norm.count(form)

        else:

            total += len(re.findall(rf"(?<![a-z0-9]){re.escape(form)}(?![a-z0-9])", haystack_norm))

    return total





# ---------------------------------------------------------------------------

# Keyword extraction from the JD

# ---------------------------------------------------------------------------



def extract_jd_keywords(jd_text: str, title: str = "", company: str = "",

                        top_n: int = 15) -> list[dict[str, Any]]:

    """Rank the JD's highest-weight keywords. Tech phrases/tokens dominate (the real ATS

    keywords); generic repeated terms count for little; company-name and role/meta words are

    excluded. Returns [{term, weight, critical, in_title}] sorted strongest first."""

    jd_norm = _norm(jd_text)

    title_norm = _norm(title)

    # Exclude company-name tokens (e.g. "Bending Spoons") — they are not skills.

    company_toks = {t for t in _tokens(company) if len(t) >= 3}

    tokens = _tokens(jd_text)

    freq: dict[str, int] = {}

    for t in tokens:

        freq[t] = freq.get(t, 0) + 1



    scored: dict[str, float] = {}

    is_tech: dict[str, bool] = {}



    def bump(term: str, w: float, tech: bool = False) -> None:

        n = _norm(term)

        scored[n] = scored.get(n, 0.0) + w

        is_tech[n] = is_tech.get(n, False) or tech



    # Tech phrases present in the JD (strongest signal, phrase match).

    for ph in TECH_PHRASES:

        if _norm(ph) in jd_norm:

            bump(ph, 6 + 2 * jd_norm.count(_norm(ph)), tech=True)

    # Tech tokens present in the JD.

    for tok in TECH_TOKENS:

        c = freq.get(tok, 0)

        if c and tok not in company_toks:

            bump(tok, 4 + min(c, 4), tech=True)

    # Generic significant terms: repeated >=2x, real word, not stop/company — LOW weight, so a

    # domain word ("payments", "latency") can surface but noise never outranks a tech term.

    for t, c in freq.items():

        if (c >= 2 and len(t) >= 4 and t not in STOPWORDS and not t.isdigit()

                and t not in company_toks and t.isalpha()):

            bump(t, 0.5 + min(c, 4) * 0.4)

    # A title token adds weight only if it is a tech term (generic role words are stopped,

    # company words excluded) — the title should not manufacture junk keywords.

    for t in _tokens(title):

        if t in TECH_TOKENS and t not in company_toks:

            bump(t, 3, tech=True)



    # Collapse synonym duplicates onto the highest-weighted surface form.

    merged: dict[str, float] = {}

    canon: dict[str, str] = {}

    for term, w in scored.items():

        key = None

        for cls in SYNONYMS:

            if term in {_norm(x) for x in cls}:

                key = min(cls, key=len)

                break

        key = _norm(key) if key else term

        if w > merged.get(key, -1):

            canon[key] = term

        merged[key] = merged.get(key, 0.0) + w



    # Rank by weight; break ties toward tech terms so a real skill never sits below noise.

    ranked = sorted(merged.items(),

                    key=lambda kv: (-kv[1], not is_tech.get(canon.get(kv[0], kv[0]), False)))[:top_n]

    out: list[dict[str, Any]] = []

    for i, (key, w) in enumerate(ranked):

        term = canon.get(key, key)

        out.append({"term": term, "weight": round(w, 1), "critical": i < 6,

                    "tech": is_tech.get(term, False), "in_title": _present(term, title_norm)})

    return out





# ---------------------------------------------------------------------------

# Deterministic checks

# ---------------------------------------------------------------------------



def _experience_bullets(html: str) -> list[str]:

    """Experience bullets = <li> that do NOT open with a <strong> label (those are the

    competency bullets). Returns their visible text."""

    out = []

    for m in re.finditer(r"(?is)<li\b[^>]*>(.*?)</li>", html):

        inner = m.group(1)

        if re.match(r"(?is)\s*<strong\b", inner):

            continue

        text = html_to_text(inner)

        if len(text) >= 30:

            out.append(text)

    return out





def _profile_text(html: str) -> str:

    m = re.search(r'(?is)<p\b[^>]*class="[^"]*profile[^"]*"[^>]*>(.*?)</p>', html)

    return html_to_text(m.group(1)) if m else ""





ZERO_WIDTH = re.compile("[​-‏⁠-⁤﻿‪-‮]")

HIDE_STYLE = re.compile(

    r"(?is)(display\s*:\s*none|visibility\s*:\s*hidden|opacity\s*:\s*0(?!\.)|"

    r"font-size\s*:\s*0(?:px|pt)?\b|color\s*:\s*#?f{3,6}\b|text-indent\s*:\s*-\d{3,}|left\s*:\s*-\d{3,})")





def _scan_hidden(html: str) -> list[str]:

    body = _strip_style(html)

    issues: list[str] = []

    zw = len(ZERO_WIDTH.findall(body))

    if zw:

        issues.append(f"{zw} invisible/zero-width char(s)")

    hidden = sorted({m.group(1).split(":")[0].strip().lower() for m in HIDE_STYLE.finditer(body)})

    if hidden:

        issues.append("inline hiding style(s): " + ", ".join(hidden))

    return issues





# A bullet "quantifies" if it ends on a real number OR a concrete outcome anchor the tailor_cv

# policy explicitly endorses ("in production", "in under 1ms", "with zero downtime"). This

# matches the stated rule so a legitimate technique bullet is not penalised into fabrication.

_METRIC_RE = re.compile(

    r"\d|%|~|\bzero\b|\bsub-|\bunder\b|\bproduction\b|\bdeployed\b|\blive\b|"

    r"\breal[- ]time\b|\bdowntime\b|\blatency\b|\bthroughput\b|\bconcurrent\b|"

    r"\bper (?:second|day|request|user)\b|\bmillion\b|\bthousand\b|\bk\+", re.I)





def _bullet_ok(b: str) -> dict[str, bool]:

    low = b.lower()

    first = re.split(r"[\s,]", low.strip(), 1)[0].strip(".:;")

    banned = any(low.startswith(op) for op in BANNED_OPENERS)

    verb = first in STRONG_VERBS

    metric = bool(_METRIC_RE.search(b))

    return {"verb": verb, "metric": metric, "banned": banned,

            "ok": verb and metric and not banned}







def deterministic(jd_text: str, title: str, html: str, cv_text: str,

                  pdf_meta: dict[str, Any], company: str = "") -> dict[str, Any]:

    """Run every objective check. Returns sub-scores, a 0-100 det_score, gate flags, issues."""

    cv_norm = _norm(cv_text)

    html_text = html_to_text(html)

    profile = _profile_text(html)

    issues: list[str] = []

    gates: dict[str, bool] = {}



    # --- Keyword coverage (vs the text the ATS extracts) ---

    keywords = extract_jd_keywords(jd_text, title, company)

    matched, critical_total, critical_hit, freq_ok = [], 0, 0, 0

    missing_critical = []

    for kw in keywords:

        hit = _present(kw["term"], cv_norm)

        kw["present"] = hit

        kw["count"] = _count(kw["term"], cv_norm) if hit else 0

        if hit:

            matched.append(kw["term"])

        if kw["critical"]:

            critical_total += 1

            if hit:

                critical_hit += 1

                if kw["count"] >= 2:

                    freq_ok += 1

            else:

                missing_critical.append(kw["term"])

    cov_all = len(matched) / len(keywords) if keywords else 1.0

    cov_crit = critical_hit / critical_total if critical_total else 1.0

    freq_ratio = freq_ok / critical_total if critical_total else 1.0

    # keyword sub-score /30: coverage_all (12) + critical coverage (12) + freq (6)

    kw_score = 12 * cov_all + 12 * cov_crit + 6 * freq_ratio

    if cov_crit < 0.5:

        issues.append(f"only {critical_hit}/{critical_total} critical keywords present "

                      f"(missing: {', '.join(missing_critical[:6])})")

    title_in_profile = any(_present(w, _norm(profile)) for w in _tokens(title)

                           if len(w) >= 4 and w not in STOPWORDS)

    if title and not title_in_profile:

        issues.append("job title (or a close term) not echoed in the profile paragraph")



    # --- Bullet-formula compliance /20 ---

    bullets = _experience_bullets(html)

    checks = [_bullet_ok(b) for b in bullets]

    n = len(checks) or 1

    ok_ratio = sum(c["ok"] for c in checks) / n

    banned_hits = [bullets[i] for i, c in enumerate(checks) if c["banned"]]

    no_metric = [bullets[i] for i, c in enumerate(checks) if not c["metric"]]

    bullet_score = 20 * ok_ratio

    if banned_hits:

        issues.append(f"{len(banned_hits)} bullet(s) open with a banned task phrase")

    if len(no_metric) > n * 0.4:

        issues.append(f"{len(no_metric)}/{len(bullets)} experience bullets lack a metric/number")



    # --- Repeated bullet openers (AI-tell) ---

    openers = [re.split(r"\s", b.strip(), 1)[0].lower() for b in bullets]

    opener_counts = {o: openers.count(o) for o in set(openers)}

    max_opener = max(opener_counts.values()) if opener_counts else 0

    repeated_openers = {o: c for o, c in opener_counts.items() if c >= 3}



    # --- Style cleanliness /15 ---

    prose = (profile + " " + " ".join(bullets)).lower()

    cliches = sorted({c for c in BANNED_CLICHES if c in prose})

    emdash_prose = ("—" in (profile + " ".join(bullets))) or ("&mdash;" in

                    (_profile_text(html) or "")) or bool(re.search(

                        r"(?is)<li\b[^>]*>(?:(?!</li>).)*(?:—|&mdash;)", html))

    style_penalty = 3 * len(cliches) + (5 if emdash_prose else 0) + 3 * max(0, len(repeated_openers))

    style_score = max(0.0, 15 - style_penalty)

    if cliches:

        issues.append("banned cliche(s): " + ", ".join(cliches[:6]))

    if emdash_prose:

        issues.append("em-dash present in prose/bullets (banned outside title headers)")

    if repeated_openers:

        issues.append("repeated bullet opener(s): "

                      + ", ".join(f"{o}×{c}" for o, c in repeated_openers.items()))



    # --- Format / ATS readability /20 ---

    pages = pdf_meta.get("pages")

    chars = len(cv_text.strip())

    fmt_score, fmt_ok = 20.0, True

    if pages is None:

        issues.append("page count unknown (no PDF or pypdf unavailable)")

        fmt_score -= 2

    elif pages > 2:

        issues.append(f"PDF is {pages} pages (hard limit is 2)")

        fmt_score -= 10

        fmt_ok = False

    elif pages == 0:

        issues.append("PDF has 0 readable pages")

        fmt_score -= 15

        fmt_ok = False

    low = cv_text.lower()

    for sect in ("experience", "education"):

        if sect not in low:

            issues.append(f"section '{sect}' not found in extracted text")

            fmt_score -= 3

    if chars < 400:

        issues.append(f"only {chars} chars extracted — PDF may be image-based/near-empty")

        fmt_score -= 8

        fmt_ok = False

    lines = [ln for ln in cv_text.split("\n") if ln.strip()]

    if lines and sum(len(ln) for ln in lines) / len(lines) < 8:

        issues.append("very short avg line length — text may be column-scrambled for ATS")

        fmt_score -= 5

    if not re.search(r"[\w.+-]+@[\w-]+\.\w+", cv_text):

        issues.append("email not detected in extracted text (contact may be ATS-unreadable)")

        fmt_score -= 3

    fmt_score = max(0.0, fmt_score)



    # --- Integrity /15 + hard gates ---

    integ_score, integ = 15.0, []

    languages_ok = LANGUAGES_CANONICAL in html_text.lower()

    if not languages_ok:

        integ.append("Languages line is not exactly 'English (Native), Hungarian (Native)'")

        integ_score -= 5

    portfolio_ok = PORTFOLIO_HOST in html.lower()

    if not portfolio_ok:

        integ.append("Portfolio link (rubenlekli.com) missing from header")

        integ_score -= 4

    hidden = _scan_hidden(html)

    if hidden:

        integ.append("hidden/invisible content: " + "; ".join(hidden))

        integ_score -= 6

    prov = sorted({p for p in PROVENANCE_TELLS if p in prose or p in html_text.lower()})

    if prov:

        integ.append("provenance tell(s) — work reads as AI-produced: " + ", ".join(prov))

        integ_score -= 6

    framing = sorted({p for p in FRAMING_TELLS if p in prose})   # prose only: not Education coursework

    if framing:

        integ.append("experience framed as coursework/study: " + ", ".join(framing))

        integ_score -= 3

    integ_score = max(0.0, integ_score)

    issues.extend(integ)



    det_score = round(kw_score + bullet_score + style_score + fmt_score + integ_score, 1)



    # Hard gates — a true here fails the CV outright regardless of score. Reserved for genuine

    # DISQUALIFIERS (silent-reject / integrity), NOT fixable style nits: a >2-page or image-only

    # PDF an ATS chokes on, hidden text, work described as AI-produced, or an ATS keyword wipeout.

    # Languages-wording and em-dash-in-prose are rule issues (penalised above) but not

    # disqualifiers, and the pipeline strips em-dashes deterministically at build time anyway.

    gates = {

        "over_two_pages": bool(pages and pages > 2),

        "unreadable_pdf": (pages == 0) or (chars < 400),

        "hidden_content": bool(hidden),

        "provenance_tell": bool(prov),

        "critical_keyword_gap": cov_crit < 0.34,

    }



    return {

        "det_score": det_score,

        "subscores": {"keyword": round(kw_score, 1), "bullets": round(bullet_score, 1),

                      "style": round(style_score, 1), "format": round(fmt_score, 1),

                      "integrity": round(integ_score, 1)},

        "keyword_coverage": {"all_pct": round(100 * cov_all), "critical_pct": round(100 * cov_crit),

                             "critical_twice_pct": round(100 * freq_ratio),

                             "matched": matched, "missing_critical": missing_critical,

                             "keywords": keywords},

        "bullets": {"total": len(bullets), "compliant": sum(c["ok"] for c in checks),

                    "banned_openers": banned_hits, "missing_metric": no_metric,

                    "max_same_opener": max_opener},

        "title_in_profile": title_in_profile,

        "pages": pages, "extracted_chars": chars, "pdf_method": pdf_meta.get("method"),

        "gates": gates, "issues": issues,

    }



