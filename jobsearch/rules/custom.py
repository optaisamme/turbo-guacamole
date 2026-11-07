"""User-defined requirement rules from JOB_SEARCH_SETTINGS/job_search_settings.toml ([[filters.requirement_rule]]).

Each rule: name, on (true/false), terms (list), reject_when:
  "required"  -> reject only when the posting REQUIRES prior experience with a term
                 (preferred / "a plus" / after-hire duties are fine; unclear wording -> Needs Verification)
  "mentioned" -> reject whenever a term appears anywhere in the posting
"""
from __future__ import annotations

import re

from ..models import RuleResult, Evidence, PASS, FAIL, UNCLEAR
from ..text import Segment, excerpt
from .requirement import classify_mention


def _term_re(term: str) -> re.Pattern:
    return re.compile(r"(?<![A-Za-z0-9])" + r"\s+".join(map(re.escape, term.split())) + r"(?![A-Za-z0-9])", re.I)


def evaluate_rule(rule: dict, segments: list[Segment], url: str | None, authoritative: bool) -> RuleResult:
    name = rule.get("name") or "custom rule"
    mode = (rule.get("reject_when") or "required").lower()
    src = "employer_page" if authoritative else "third_party"
    terms = [t for t in rule.get("terms", []) if t and t.strip()]
    required, ambiguous, acceptable = [], [], []
    for seg in segments:
        for t in terms:
            m = _term_re(t).search(seg.text)
            if not m:
                continue
            found = m.group(0)
            if mode == "mentioned":
                required.append((t, "mentioned", seg))
                continue
            from .certs import POST_HIRE
            if POST_HIRE.search(seg.text):
                acceptable.append((t, "can be obtained after hire", seg))
                continue
            kind, why = classify_mention(seg, found)
            (required if kind == "required" else ambiguous if kind == "ambiguous" else acceptable).append((t, why, seg))
    ev = [Evidence(f"[{t} – {why}] {excerpt(seg.text)}", url, src) for t, why, seg in (required + ambiguous + acceptable)[:5]]
    data = {"mode": mode, "required": [t for t, _, _ in required], "ambiguous": [t for t, _, _ in ambiguous],
            "acceptable": [t for t, _, _ in acceptable]}
    label = f"rule: {name}"
    if required:
        names = ", ".join(sorted({t for t, _, _ in required}))
        msg = f"Posting mentions {names}" if mode == "mentioned" else f"Hard requirement for prior {names} experience"
        return RuleResult(label, FAIL, msg, ev, data)
    if ambiguous:
        return RuleResult(label, UNCLEAR, f"Required vs preferred unclear for {', '.join(sorted({t for t, _, _ in ambiguous}))}", ev, data)
    if acceptable:
        return RuleResult(label, PASS, f"{', '.join(sorted({t for t, _, _ in acceptable}))} mentioned but not required", ev, data)
    return RuleResult(label, PASS, "No listed terms mentioned", [], data)


def evaluate_all(segments: list[Segment], url: str | None, authoritative: bool) -> list[RuleResult]:
    from ..criteria import CRITERIA
    return [evaluate_rule(r, segments, url, authoritative) for r in CRITERIA.get("custom_rules", []) if r.get("on", True)]
