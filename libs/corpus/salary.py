"""Salary extraction from posting text + Ruben's suggested-ask bands (feed card 💰/🎯 lines)."""
from __future__ import annotations

import re
from typing import Any

# ── extraction ────────────────────────────────────────────────────────

# Currency markers. Symbols bind tight to the number; codes/words may trail the amount.
_SYM = {"£": "GBP", "€": "EUR", "$": "USD"}
_CODES = {"gbp": "GBP", "eur": "EUR", "euro": "EUR", "euros": "EUR", "usd": "USD",
          "chf": "CHF", "pln": "PLN", "zł": "PLN", "zl": "PLN", "huf": "HUF", "ft": "HUF",
          "czk": "CZK", "ron": "RON", "sek": "SEK", "nok": "NOK", "dkk": "DKK"}

# 45,000 | 1 200 000 | 1.200.000 | 65 | 62.5  (thousands split by space/NBSP/narrow-NBSP/comma/dot)
_TSEP = "'   ,."
_NUM = r"\d{1,3}(?:[" + _TSEP + r"]\d{3})+|\d+(?:\.\d+)?"
_KNUM = rf"(?:{_NUM})\s?[kK]?"
_SEP = r"\s*(?:-|–|—|to|and|és)\s*"

# symbol-first range/single:  £45,000 - £55,000   |   €80k   |   $ 90,000
_P_SYM = re.compile(rf"(?P<cur>[£€$])\s?(?P<a>{_KNUM})(?:{_SEP}[£€$]?\s?(?P<b>{_KNUM}))?")
# code-after range/single:   60 000 - 80 000 PLN   |   1 200 000 Ft   |   65k EUR
_P_CODE = re.compile(
    rf"(?P<a>{_KNUM})(?:{_SEP}(?P<b>{_KNUM}))?\s?"
    rf"(?P<cur>GBP|EUR|euros?|USD|CHF|PLN|zł|zl|HUF|Ft|CZK|RON|SEK|NOK|DKK)\b",
    re.IGNORECASE)
# code-first range/single:   CHF 110'000 - 130'000   |   PLN 25 000   |   EUR 65k
_P_CODE_FIRST = re.compile(
    rf"\b(?P<cur>GBP|EUR|USD|CHF|PLN|HUF|CZK|RON|SEK|NOK|DKK)\s?"
    rf"(?P<a>{_KNUM})(?:{_SEP}(?:GBP|EUR|USD|CHF|PLN|HUF|CZK|RON|SEK|NOK|DKK)?\s?(?P<b>{_KNUM}))?")

_P_YEAR = re.compile(r"(?i)\b(?:per\s+(?:year|annum)|/\s?(?:year|yr|annum|a)\b|annual|yearly|"
                     r"p\.?a\.?\b|évente|éves)")
_P_MONTH = re.compile(r"(?i)\b(?:per\s+month|/\s?(?:month|mo|m[oó]nap)\b|monthly|/h[oó]\b|"
                      r"havi|havonta|mies|miesi[eę]cznie|brutto\s?/\s?m)")
_P_DAY = re.compile(r"(?i)\b(?:per\s+day|/\s?day\b|daily|day\s?rate)")
_P_HOUR = re.compile(r"(?i)\b(?:per\s+hour|/\s?(?:hour|hr|h)\b|hourly|[oó]ránként)")

# Things that look like salaries but aren't.
_P_FALSE = re.compile(r"(?i)401\s?\(?k\)?|\bK8s\b")


def _amount(tok: str) -> float | None:
    tok = tok.strip()
    if tok[-1:].lower() == "k":
        mult, tok = 1000.0, tok[:-1].strip()
    else:
        mult = 1.0
    for ch in "'   ,":
        tok = tok.replace(ch, "")
    # Dots: thousands separators when every group after the first has 3 digits ("1.200.000", "45.000"); otherwise a decimal point ("62.5").
    if "." in tok:
        head, *rest = tok.split(".")
        if rest and all(len(g) == 3 for g in rest):
            tok = head + "".join(rest)
    try:
        return float(tok) * mult
    except ValueError:
        return None


def _period(context: str, currency: str, top: float) -> str:
    if _P_HOUR.search(context):
        return "hour"
    if _P_DAY.search(context):
        return "day"
    if _P_MONTH.search(context):
        return "month"
    if _P_YEAR.search(context):
        return "year"
    # No explicit marker: infer from magnitude conventions.
    if currency == "HUF":
        return "month" if top < 8_000_000 else "year"
    if currency in ("PLN", "CZK", "RON"):
        return "month" if top < 60_000 else "year"
    return "year" if top >= 8_000 else ""


def extract(text: str | None) -> dict[str, Any] | None:
    """First plausible salary mention in `text`, or None."""
    if not text:
        return None
    hay = text[:12000]
    best: dict[str, Any] | None = None
    for pat in (_P_SYM, _P_CODE, _P_CODE_FIRST):
        for m in pat.finditer(hay):
            ctx = hay[max(m.start() - 60, 0):m.end() + 60]
            if _P_FALSE.search(ctx):
                continue
            cur_tok = m.group("cur")
            currency = _SYM.get(cur_tok) or _CODES.get(cur_tok.lower(), "")
            if not currency:
                continue
            a = _amount(m.group("a"))
            b = _amount(m.group("b")) if m.group("b") else None
            if a is None:
                continue
            lo, hi = (min(a, b), max(a, b)) if b is not None else (a, a)
            period = _period(ctx, currency, hi)
            # Plausibility gates: an hourly/day rate can be small; otherwise tiny numbers ("save $5", "€2 coffee") and absurd ones are noise, not compensation.
            if period in ("hour", "day"):
                if not (5 <= lo <= 5000):
                    continue
            elif hi < 500 or hi > 100_000_000:
                continue
            # Ranges are more trustworthy than single amounts: return the FIRST range immediately; remember the first single as a fallback.
            cand = {"min": lo, "max": hi, "currency": currency, "period": period,
                    "raw": m.group(0).strip()}
            if b is not None:
                return cand
            best = best or cand
    return best


# ── suggested ask ─────────────────────────────────────────────────────

# Rough static FX to EUR for the below-target comparison ONLY (never displayed).
_TO_EUR = {"EUR": 1.0, "GBP": 1.17, "USD": 0.92, "CHF": 1.06, "PLN": 0.235,
           "HUF": 0.0025, "CZK": 0.041, "RON": 0.20, "SEK": 0.088, "NOK": 0.086, "DKK": 0.134}

_ANNUAL = {"year": 1, "month": 12, "day": 260, "hour": 2080, "": 1}

# Default target bands (gross; HU/PL monthly per local convention, others annual).
_DEFAULT_BANDS: dict[str, dict[str, Any]] = {
    "UK":     {"currency": "GBP", "period": "year",  "dev": [48_000, 65_000],  "quant": [70_000, 100_000]},
    "EU":     {"currency": "EUR", "period": "year",  "dev": [55_000, 75_000],  "quant": [70_000, 95_000]},
    "CH":     {"currency": "CHF", "period": "year",  "dev": [100_000, 125_000], "quant": [120_000, 150_000]},
    "HU":     {"currency": "HUF", "period": "month", "dev": [1_100_000, 1_600_000], "quant": [1_400_000, 2_000_000]},
    "PL":     {"currency": "PLN", "period": "month", "dev": [18_000, 26_000],  "quant": [22_000, 30_000]},
    "GLOBAL": {"currency": "USD", "period": "year",  "dev": [70_000, 95_000],  "quant": [90_000, 120_000]},
}

_HU_HINTS = ("hungary", "budapest", "magyarorsz", "debrecen", "szeged", "győr", "gyor",
             "pécs", "pecs")
_PL_HINTS = ("poland", "polska", "warsaw", "warszawa", "krakow", "kraków", "wroclaw", "gdansk")
_UK_HINTS = ("united kingdom", "uk", "england", "scotland", "wales", "london", "manchester")
_CH_HINTS = ("switzerland", "zurich", "zürich", "geneva", "basel", "schweiz")
_GLOBAL_HINTS = ("worldwide", "anywhere", "global", "united states", "usa", "americas")

# HU-only boards — anything they carry is a Hungarian job regardless of location string.
HU_SOURCES = {"profession", "dreamjobs", "nofluffjobs_hu", "jooble"}

# Mirrors cv_builder._QUANT_RE (kept local: this module must stay import-light for the watcher).
_QUANT_RE = re.compile(
    r"\bquant|\btrading|\btrader|\bderivativ|\bhedge fund|\bsystematic|"
    r"\bportfolio|\bmarket mak|\balpha\b|\bfixed income|\bresearcher", re.IGNORECASE)


def _region(job: dict[str, Any], posted: dict[str, Any] | None) -> str:
    loc = f"{job.get('location') or ''} {job.get('title') or ''}".lower()
    src = (job.get("source") or "").lower()
    if any(h in loc for h in _HU_HINTS) or src in HU_SOURCES:
        return "HU"
    if any(h in loc for h in _CH_HINTS):
        return "CH"
    # Match the short UK hints only as whole words — "ukrainian" must not hit "uk".
    if any(re.search(rf"\b{re.escape(h)}\b", loc) for h in _UK_HINTS):
        return "UK"
    if any(h in loc for h in _PL_HINTS):
        return "PL"
    cur = (posted or {}).get("currency")
    region_by_cur = {"HUF": "HU", "PLN": "PL", "GBP": "UK", "CHF": "CH"}
    if cur in region_by_cur:
        return region_by_cur[cur]
    if any(h in loc for h in _GLOBAL_HINTS) or cur == "USD":
        return "GLOBAL"
    return "EU"


def _bands(user: dict[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    """The defaults, with this user's overrides on top."""
    user = user or {}
    merged = {k: dict(v) for k, v in _DEFAULT_BANDS.items()}
    for region, band in user.items():
        if isinstance(band, dict):
            merged.setdefault(str(region).upper(), {}).update(band)
    return merged


def _to_eur_annual(amount: float, currency: str, period: str) -> float | None:
    rate = _TO_EUR.get(currency)
    if not rate:
        return None
    return amount * rate * _ANNUAL.get(period or "year", 1)


def suggest(job: dict[str, Any], posted: dict[str, Any] | None,
            bands: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Target ask band for this job (+ below_target when the posting clearly pays less)."""
    band = _bands(bands).get(_region(job, posted))
    if not band:
        return None
    bucket = "quant" if _QUANT_RE.search(job.get("title") or "") else "dev"
    rng = band.get(bucket) or band.get("dev")
    if not (isinstance(rng, (list, tuple)) and len(rng) == 2):
        return None
    out = {"min": float(rng[0]), "max": float(rng[1]),
           "currency": band.get("currency", "EUR"), "period": band.get("period", "year"),
           "below_target": False}
    if posted:
        top = _to_eur_annual(posted["max"], posted["currency"], posted["period"])
        floor = _to_eur_annual(out["min"], out["currency"], out["period"])
        if top is not None and floor is not None and top < floor * 0.95:
            out["below_target"] = True
    return out


# ── formatting (feed card + job_context.txt) ──────────────────────────

def _fmt_num(v: float) -> str:
    if v >= 1_000_000:
        s = f"{v / 1_000_000:.1f}".rstrip("0").rstrip(".")
        return f"{s}M"
    if v >= 1_000:
        s = f"{v / 1_000:.1f}".rstrip("0").rstrip(".")
        return f"{s}k"
    return f"{v:.0f}"


def fmt(sal: dict[str, Any] | None) -> str:
    """'55k–75k EUR/yr' | '1.2M–1.6M HUF/mo' | '' when None."""
    if not sal:
        return ""
    per = {"year": "/yr", "month": "/mo", "day": "/day", "hour": "/hr"}.get(sal.get("period") or "", "")
    lo, hi = sal["min"], sal["max"]
    rng = _fmt_num(lo) if lo == hi else f"{_fmt_num(lo)}–{_fmt_num(hi)}"
    return f"{rng} {sal['currency']}{per}"
