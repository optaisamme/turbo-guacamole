"""Salary hard filter.

Rules (thresholds from JOB_SEARCH_SETTINGS/job_search_settings.toml; 0 = no minimum):
  single salary  -> pass if >= minimum_single_salary
  range          -> pass if lower >= minimum_range_start AND upper > range_end_must_be_above
Only employer-advertised annual BASE salary counts. Bonuses, commission, OTE,
equity, total compensation, and third-party estimates are ignored.
Location-specific bands: use the band applicable to the home state; if none can be
determined -> unclear. Hourly/contract pay is held separately (unclear, flagged).
"""
from __future__ import annotations

import re

from ..models import RuleResult, Evidence, PASS, FAIL, UNCLEAR
from ..text import excerpt


_NUM = r"\$\s?(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*([kK])?"
_NUM2 = r"\$?\s?(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*([kK])?"
_RANGE = re.compile(_NUM + r"\s*(?:-|–|—|to)\s*(?:USD\s*)?" + _NUM2, re.I)
_RANGE_AND = re.compile(_NUM + r"\s*and\s*(?:USD\s*)?" + _NUM, re.I)
_SINGLE = re.compile(_NUM, re.I)
_USD_RANGE = re.compile(r"\bUSD\s*(\d{1,3}(?:,\d{3})+)\s*(?:-|–|—|to)\s*(?:USD\s*)?(\d{1,3}(?:,\d{3})+)", re.I)

EXCLUDE_CTX = re.compile(r"\b(bonus|commission|OTE|on[\s-]target|equity|stock|RSU|total\s+(rewards|compensation|comp)|401\(?k\)?|stipend|reimburse|allowance|signing|relocation|revenue|budget|raised|donations?|funding|fundrais|million|billion|\$\d+(\.\d+)?\s*[mb]\b)", re.I)
HOURLY_CTX = re.compile(r"(/\s*(hr|hour)\b|per\s+hour|hourly|an\s+hour)", re.I)
ANNUAL_CTX = re.compile(r"\b(annual|annually|per\s+year|a\s+year|/\s*(yr|year)|yearly|salary|base\s+pay|pay\s+range|compensation\s+range|salary\s+range|base)\b", re.I)
STATE_LABEL = re.compile(r"\b(zone|tier|geo|location|region|market|area|state|california|new york|washington|colorado|nyc|sf|bay area|seattle|remote|us|united states|national)\b", re.I)


def _to_num(val: str, k: str | None) -> float:
    n = float(val.replace(",", ""))
    if k:
        n *= 1000
    return n


def evaluate_amounts(lo: float | None, hi: float | None) -> tuple[str, str]:
    """Pure decision on an annual amount or range. Returns (outcome, reason)."""
    from ..criteria import CRITERIA
    SINGLE_MIN, RANGE_LOWER_MIN, RANGE_UPPER_GT = CRITERIA["single_min"], CRITERIA["range_lower_min"], CRITERIA["range_upper_gt"]
    if lo is not None and hi is not None and lo > hi:
        lo, hi = hi, lo
    if lo is not None and hi is not None and lo == hi:
        return (PASS, f"salary ${lo:,.0f} is at or above your minimum of ${SINGLE_MIN:,}") if lo >= SINGLE_MIN else (FAIL, f"salary ${lo:,.0f} is below your minimum of ${SINGLE_MIN:,}")
    if lo is not None and hi is not None:
        if lo >= RANGE_LOWER_MIN and hi > RANGE_UPPER_GT:
            return PASS, f"range ${lo:,.0f}-${hi:,.0f} starts at or above ${RANGE_LOWER_MIN:,} and ends above ${RANGE_UPPER_GT:,}"
        why = []
        if lo < RANGE_LOWER_MIN:
            why.append(f"starts at ${lo:,.0f}, below ${RANGE_LOWER_MIN:,}")
        if hi <= RANGE_UPPER_GT:
            why.append(f"ends at ${hi:,.0f}, not above ${RANGE_UPPER_GT:,}")
        return FAIL, f"range ${lo:,.0f}-${hi:,.0f}: " + "; ".join(why)
    if lo is not None:  # minimum only ("starting at")
        if lo >= SINGLE_MIN:
            return PASS, f"lowest pay ${lo:,.0f} is at or above your minimum of ${SINGLE_MIN:,}"
        if lo < RANGE_LOWER_MIN:
            return FAIL, f"lowest pay ${lo:,.0f} is below ${RANGE_LOWER_MIN:,}"
        return UNCLEAR, f"minimum ${lo:,.0f} stated without upper bound"
    if hi is not None:  # maximum only ("up to")
        if hi <= RANGE_UPPER_GT:
            return FAIL, f"highest pay ${hi:,.0f} is not above ${RANGE_UPPER_GT:,}"
        return UNCLEAR, f"only a maximum (${hi:,.0f}) stated; lower bound unknown"
    return UNCLEAR, "no salary"


def _plausible_annual(n: float) -> bool:
    return 20_000 <= n <= 1_000_000


def _label_applies_to_nm(label: str) -> str:
    """'yes' if a band label explicitly covers the home state (named, or 'all other US
    locations'/'nationwide'), otherwise 'unknown'."""
    from ..criteria import CRITERIA
    l = (label or "").lower()
    names = [CRITERIA["home_state"]] + list(CRITERIA.get("home_cities") or [])
    pat = "|".join(re.escape(n.lower()) for n in names if n)
    abbr = (CRITERIA.get("home_state_abbr") or "").lower()
    if (pat and re.search(pat, l)) or (abbr and re.search(r"\b" + re.escape(abbr) + r"\b", l)):
        return "yes"
    if re.search(r"all\s+other|all\s+(us|u\.s\.|united states)|nationwide|national\s+(range|band)|all\s+locations|all\s+remote", l):
        return "yes"
    return "unknown"


POST_EXCLUDE = re.compile(r"^\W{0,3}(in\s+)?(annual\s+|target\s+|signing\s+|sign[\s-]on\s+)?(bonus|commission|equity|ote|stipend|in\s+stock|rsus?|total)\b", re.I)


def _excluded(pre: str, post: str) -> bool:
    clause = re.split(r"(?<!\d)\.(?!\d)|;", pre)[-1]
    return bool(EXCLUDE_CTX.search(clause[-60:]) or POST_EXCLUDE.search(post))


def extract_text_salaries(text: str) -> list[dict]:
    """Find annual salary mentions in text. Returns list of {min,max,interval,context,label}."""
    found: list[dict] = []
    for line in text.split("\n"):
        range_spans: list[tuple[int, int]] = []
        for m in list(_RANGE.finditer(line)) + list(_RANGE_AND.finditer(line)) + list(_USD_RANGE.finditer(line)):
            ctx = line[max(0, m.start() - 120): m.end() + 60]
            if len(m.groups()) == 4:
                lo, hi = _to_num(m.group(1), m.group(2)), _to_num(m.group(3), m.group(4))
                # "$90-110K": apply trailing K to both
                if m.group(4) and not m.group(2) and lo < 1000:
                    lo *= 1000
            else:
                lo, hi = _to_num(m.group(1), None), _to_num(m.group(2), None)
            pre = line[max(0, m.start() - 60): m.start()]
            post = line[m.end(): m.end() + 40]
            interval = "hour" if HOURLY_CTX.search(post) or HOURLY_CTX.search(pre[-25:]) or (hi < 500) else "year"
            range_spans.append((m.start(), m.end()))
            if _excluded(pre, post):
                continue
            if interval == "year" and not (_plausible_annual(lo) and _plausible_annual(hi)):
                continue
            label = pre.split(".")[-1].strip(" :-–—")
            found.append({"min": lo, "max": hi, "interval": interval, "context": excerpt(ctx), "label": label})
        # single values only on lines that look like salary statements
        if ANNUAL_CTX.search(line) or HOURLY_CTX.search(line):
            for m in _SINGLE.finditer(line):
                if any(a <= m.start() < b for a, b in range_spans):
                    continue
                pre = line[max(0, m.start() - 60): m.start()]
                post = line[m.end(): m.end() + 40]
                if _excluded(pre, post):
                    continue
                n = _to_num(m.group(1), m.group(2))
                interval = "hour" if HOURLY_CTX.search(post[:20]) or n < 500 else "year"
                if interval == "year" and not _plausible_annual(n):
                    continue
                kind = "single"
                if re.search(r"(starting\s+(at|from)|minimum\s+of|at\s+least|from)\s*$", pre, re.I):
                    kind = "min"
                elif re.search(r"(up\s+to|maximum\s+of|max\s+of)\s*$", pre, re.I):
                    kind = "max"
                found.append({
                    "min": n if kind in ("single", "min") else None,
                    "max": n if kind in ("single", "max") else None,
                    "interval": interval, "context": excerpt(line[max(0, m.start() - 120): m.end() + 60]),
                    "label": pre.split(".")[-1].strip(" :-–—"),
                })
    # de-duplicate identical amounts
    uniq, seen = [], set()
    for f in found:
        key = (f["min"], f["max"], f["interval"])
        if key not in seen:
            seen.add(key)
            uniq.append(f)
    return uniq


def evaluate(structured: list[dict], text: str, url: str | None, authoritative: bool,
             employment_type: str | None = None) -> RuleResult:
    from ..criteria import CRITERIA
    if not (CRITERIA["single_min"] or CRITERIA["range_lower_min"] or CRITERIA["range_upper_gt"]):
        found = extract_text_salaries(text)
        note = f"; advertised: {found[0]['context']}" if found else ""
        return RuleResult("salary", PASS, "No salary minimum set in settings" + note, [], {})
    cands = [dict(s, context=s.get("context") or s.get("label") or "structured salary field") for s in structured if s]
    from_struct = bool(cands)
    if not cands:
        cands = extract_text_salaries(text)
    src = "ats_api" if from_struct and authoritative else ("employer_page" if authoritative else "third_party")
    ev = [Evidence(excerpt(c["context"]), url, src) for c in cands[:4]]

    if not cands:
        return RuleResult("salary", UNCLEAR, "No employer-advertised salary found", [], {})
    if any(c.get("predicted") for c in cands):
        return RuleResult("salary", UNCLEAR, "Only an estimated/predicted salary is available; not employer-advertised", ev,
                          {"candidates": cands})

    annual = [c for c in cands if c.get("interval", "year") in ("year", "annual", "yearly", "1 YEAR")]
    hourly = [c for c in cands if c not in annual]
    if not annual and hourly:
        return RuleResult("salary", UNCLEAR, "Hourly/contract compensation only; held separately (no conversion policy approved)", ev,
                          {"compensation_type": "hourly", "candidates": cands})
    if employment_type and re.search(r"contract|temporary|temp\b|freelance", employment_type, re.I):
        return RuleResult("salary", UNCLEAR, f"Employment type '{employment_type}'; contract roles held separately", ev,
                          {"compensation_type": "contract", "candidates": cands})

    chosen = annual
    if len(annual) > 1:
        # multiple bands: choose the home-state band if determinable
        nm = [c for c in annual if _label_applies_to_nm(c.get("label", "")) == "yes"]
        if len(nm) == 1:
            chosen = nm
        else:
            outcomes = {evaluate_amounts(c["min"], c["max"])[0] for c in annual}
            if outcomes == {PASS}:
                return RuleResult("salary", PASS, "Multiple bands advertised; every band passes", ev, {"bands": annual})
            if outcomes == {FAIL}:
                return RuleResult("salary", FAIL, "Multiple bands advertised; every band fails", ev, {"bands": annual})
            return RuleResult("salary", UNCLEAR, "Multiple salary bands; band applicable to your state not determinable", ev,
                              {"bands": annual})
    c = chosen[0]
    outcome, reason = evaluate_amounts(c["min"], c["max"])
    return RuleResult("salary", outcome, reason, [Evidence(excerpt(c["context"]), url, src)],
                      {"min": c["min"], "max": c["max"], "interval": "year"})
