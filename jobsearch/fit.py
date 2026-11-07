"""Experience-fit assessment, kept separate from hard-filter eligibility.

No numeric score. Output lists:
  strong      - job asks for a skill the profile DEMONSTRATES (with profile evidence)
  transferable- job asks for a skill the profile supports only by inference
  gaps        - job asks for a skill NOT documented in the profile ("not documented",
                which is not proof the candidate lacks it)
  mandatory_unsupported - explicitly required qualifications not supported by the profile
"""
from __future__ import annotations

import re

from .text import Segment, excerpt

YEARS_RE = re.compile(r"(\d+)\s*\+?\s*(?:-\s*\d+\s*)?(?:\+\s*)?years?\s+(?:of\s+)?(?:[\w/&-]+\s+){0,4}?experience", re.I)
REQ_WORDS = re.compile(r"\b(required|must|minimum|at\s+least)\b", re.I)


def _kw_hit(kws: list[str], s: str) -> bool:
    s = s.lower()
    for k in kws:
        k = k.lower()
        if len(k) <= 4 and k.isalpha():
            if re.search(r"\b" + re.escape(k) + r"\b", s):
                return True
        elif k in s:
            return True
    return False


def assess(segments: list[Segment], exp_profile: dict, cert_result_data: dict | None = None) -> dict:
    rel = [s for s in segments if s.section in ("required", "preferred", "responsibilities", "other")]
    strong, transferable, gaps, mandatory_unsupported = [], [], [], []
    for sk in exp_profile.get("skills", []):
        hits = [s for s in rel if _kw_hit(sk["keywords"], s.text)]
        if not hits:
            continue
        entry = {"skill": sk["name"], "job_excerpt": excerpt(hits[0].text, 160), "profile_evidence": sk["evidence"]}
        (strong if sk["kind"] == "demonstrated" else transferable).append(entry)
    for g in exp_profile.get("gaps", []):
        hits = [s for s in rel if _kw_hit(g["keywords"], s.text)]
        if not hits:
            continue
        required_hit = next((h for h in hits if h.section == "required" and not re.search(r"prefer|plus|nice", h.text, re.I)), None)
        entry = {"skill": g["name"], "job_excerpt": excerpt((required_hit or hits[0]).text, 160),
                 "required": bool(required_hit), "status": "not documented in profile"}
        gaps.append(entry)
        if required_hit:
            mandatory_unsupported.append(f"{g['name']}: {excerpt(required_hit.text, 140)}")

    ten = exp_profile.get("tenure", {})
    documented = float(ten.get("years_of_experience", 0) or 0)
    years_req = []
    for s in rel:
        for m in YEARS_RE.finditer(s.text):
            n = int(m.group(1))
            if 0 < n < 30:
                years_req.append((n, s))
    if years_req and documented > 0:  # only compared when the user filled in their years
        n, s = max(years_req, key=lambda x: x[0])
        note = f"Requires {n}+ years ({excerpt(s.text, 120)}); your profile lists {documented:g} years of experience"
        if n > documented + 1 and (s.section == "required" or REQ_WORDS.search(s.text)):
            mandatory_unsupported.append(note)
        elif n > documented:
            gaps.append({"skill": "Years of experience", "job_excerpt": excerpt(s.text, 160), "required": s.section == "required",
                         "status": "may exceed the years in your profile"})
    for g in (cert_result_data or {}).get("fit_gaps", []):
        mandatory_unsupported.append(g)

    summary_bits = []
    if strong:
        summary_bits.append("Documented matches: " + ", ".join(e["skill"] for e in strong[:5]))
    if mandatory_unsupported:
        summary_bits.append(f"{len(mandatory_unsupported)} required item(s) not supported by profile")
    elif gaps:
        summary_bits.append("Gaps (not documented): " + ", ".join(g["skill"] for g in gaps[:4]))
    return {"strong": strong, "transferable": transferable, "gaps": gaps,
            "mandatory_unsupported": mandatory_unsupported, "summary": "; ".join(summary_bits)}
