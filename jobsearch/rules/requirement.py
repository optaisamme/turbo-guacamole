"""Required-vs-preferred classification of a term in a job posting.

Used by requirement rules (rules/custom.py) with reject_when = "required":
  * preferred / optional / "a plus" / "nice to have"     -> acceptable
  * a duty after hire ("maintain X", "work with X")       -> acceptable
  * required qualification, or a duty demanding prior expertise -> required
  * genuinely ambiguous wording                           -> ambiguous (Needs Verification)
"""
from __future__ import annotations

import re

from ..text import Segment

PREFERRED_CUE = re.compile(r"\b(preferred|preferably|a\s+plus|is\s+a\s+plus|nice[\s-]to[\s-]have|bonus|desired|desirable|ideal(ly)?|optional|helpful|advantage(ous)?|beneficial|would\s+be\s+great|welcome|not\s+required|appreciated|big\s+plus|added\s+plus)\b", re.I)
NEGATED_REQ = re.compile(r"\b(not\s+(required|necessary|a\s+requirement)|no\s+(prior\s+)?\w+(\s+\w+){0,3}\s+(experience\s+)?(is\s+)?(required|necessary|needed))\b", re.I)
REQUIRED_CUE = re.compile(r"\b(required|requires?|must|minimum|at\s+least|\d+\+?\s*(\+|plus)?\s*years?|proven|demonstrated|strong|extensive|deep|expert|expertise|hands[\s-]on|solid|significant|essential|mandatory|necessary|need\s+to\s+have|you\s+have|you\s+bring|you('|’)ve)\b", re.I)
EXPERIENCE_WORD = re.compile(r"\b(experience[ds]?|expertise|expert|knowledge|knowledgeable|proficien\w*|background|familiar\w*|worked\s+with|hands[\s-]on|track\s+record|understanding|skilled|mastery|fluency)\b", re.I)
EXAMPLE_CUE = re.compile(r"\b(such\s+as|e\.g\.|for\s+example|like)\b", re.I)
ALTERNATIVE_ANY = re.compile(r"\b(or\s+(similar|equivalent|comparable|other)|or\s+another\s+(crm|platform))\b", re.I)
PRIOR_IN_DUTY = re.compile(r"\b(leverage|apply|bring|use|using)\s+(your\s+)?(?:[\w'-]+\s+){0,3}(experience|expertise|knowledge)\b|\byour\s+(prior\s+)?(experience|expertise)\b|\bas\s+an?\s+(expert|seasoned)\b", re.I)


def _clause_of(seg: str, name: str) -> str:
    idx = seg.lower().find(name.lower())
    if idx < 0:
        return seg
    start = max(seg.rfind(",", 0, idx), seg.rfind("(", 0, idx), seg.rfind(";", 0, idx)) + 1
    ends = [i for i in (seg.find(",", idx), seg.find(")", idx), seg.find(";", idx)) if i >= 0]
    end = min(ends) if ends else len(seg)
    return seg[start:end]


def classify_mention(seg: Segment, name: str) -> tuple[str, str]:
    """Return (kind, why). kind in: required | preferred | duty | mention | ambiguous | alternative."""
    s = seg.text
    clause = _clause_of(s, name)
    if NEGATED_REQ.search(s):
        return "preferred", "explicitly not required"
    if PREFERRED_CUE.search(clause):
        return "preferred", "preferred/optional wording"
    if ALTERNATIVE_ANY.search(s):
        return "alternative", "'or similar/equivalent' alternative offered"
    seg_pref = bool(PREFERRED_CUE.search(s))
    seg_req = bool(REQUIRED_CUE.search(s))
    exp = bool(EXPERIENCE_WORD.search(s))
    example = bool(EXAMPLE_CUE.search(s))

    if seg.section == "preferred":
        return ("ambiguous", "required wording under a preferred heading") if re.search(r"\b(required|must)\b", s, re.I) else ("preferred", "listed under preferred/nice-to-have heading")
    if seg_pref and seg_req:
        return "ambiguous", "sentence mixes required and preferred wording"
    if seg_pref:
        return "preferred", "preferred/optional wording"
    if seg.section == "responsibilities":
        if PRIOR_IN_DUTY.search(s):
            return "required", "responsibility explicitly demands prior expertise"
        return "duty", "work with the product after hire; prior experience not stated as required"
    if seg.section == "required":
        if exp and example:
            return "ambiguous", "named only as an example ('such as'/'e.g.') in requirements"
        if exp or seg_req:
            return "required", "listed in required qualifications"
        return "ambiguous", "listed in requirements without clear experience wording"
    # section unknown / other
    if exp and re.search(r"\b(required|must|minimum|mandatory|essential)\b", s, re.I):
        return "required", "explicit required wording"
    if exp and seg_req:
        return "ambiguous", "experience wording without a clear required/preferred section"
    if exp:
        return "ambiguous", "experience wording without a clear required/preferred section"
    return "mention", "mentioned only"
