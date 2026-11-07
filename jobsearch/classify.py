"""Combine hard-filter results + verification into a status. Fit never overrides filters."""
from __future__ import annotations

from .models import RawPosting, Verification, RuleResult, QUALIFIED, NEEDS_VERIFICATION, REJECTED, PASS, FAIL, UNCLEAR
from .rules import salary, location, certs, custom
from .text import segment
from . import fit as fitmod


def evaluate_posting(p: RawPosting, exp_profile: dict, text_override: str | None = None,
                     authoritative_override: bool | None = None) -> dict:
    """Apply all hard filters to a posting. Returns dict with rule results (no verification)."""
    text = text_override if text_override is not None else p.description_text
    auth = p.authoritative if authoritative_override is None else authoritative_override
    url = p.canonical_url or p.source_url
    segs = segment(text)
    structured = [s for s in ([p.structured_salary] if p.structured_salary else []) + list(p.structured_salaries) if s]
    r_salary = salary.evaluate(structured, text, url, auth, p.employment_type)
    r_loc = location.evaluate(p.location_text, p.workplace_type, text, url, auth, p.extra.get("applicant_locations"))
    r_cert = certs.evaluate(segs, url, auth, exp_profile.get("tenure", {}).get("certifications_held", []))
    fit = fitmod.assess(segs, exp_profile, r_cert.data)
    r_custom = custom.evaluate_all(segs, url, auth)
    return {"rules": [r_salary, r_loc, r_cert] + r_custom, "fit": fit, "authoritative": auth}


RULE_LABELS = {"salary": "Salary", "location": "Location", "certifications": "Certifications"}


def plain_reason(r) -> str:
    """'Salary: …', 'Location: …', or 'Your rule "Coding": …' instead of internal rule names."""
    name = r.rule
    label = f'Your rule "{name[6:]}"' if name.startswith("rule: ") else RULE_LABELS.get(name, name.capitalize())
    return f"{label}: {r.reason}"


def decide(rules: list[RuleResult], verification: Verification | None, authoritative: bool) -> tuple[str, list[str]]:
    reasons: list[str] = []
    fails = [r for r in rules if r.outcome == FAIL]
    if fails:
        return REJECTED, [plain_reason(r) for r in fails]
    unclear = [r for r in rules if r.outcome == UNCLEAR]
    reasons += [plain_reason(r) for r in unclear]
    if verification is None or verification.status != "verified":
        st = verification.status if verification else "not verified"
        from .verify import plain_link_reason
        reasons.append(plain_link_reason(verification))
    # Owner policy (2026-11-02): third-party listings and outside-website application links
    # may qualify. They are labeled "THIRD-PARTY" everywhere they are shown (never employer-direct).
    if reasons:
        return NEEDS_VERIFICATION, reasons
    return QUALIFIED, [plain_reason(r) for r in rules]
