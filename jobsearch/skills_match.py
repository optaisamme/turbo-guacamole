"""Skills-based matching: find jobs whose title is not one of your job titles but whose posting
still fits. A listing is kept when
  1. the required keywords appear in the listing (all of them, or at least one: your choice), and
  2. the posting mentions at least `minimum_skills_matched` of the skills in your experience file.
Kept listings then go through every normal rule (salary, location, and so on).
Your title exclusions still apply."""
from __future__ import annotations

import re

SKILLS_FAMILY = "Skills match (other titles)"


def _yes(v) -> bool:
    if isinstance(v, str):
        return v.strip().lower() in ("yes", "y", "true", "on", "1")
    return bool(v)


def _hit(keyword: str, low_text: str) -> bool:
    """Short plain words match whole words; longer ones also match as part of a word ('automat')."""
    k = keyword.lower().strip()
    if not k:
        return False
    if len(k) <= 4 and k.isalpha():
        return bool(re.search(r"\b" + re.escape(k) + r"\b", low_text))
    # "nonprofit organizations" also finds "nonprofit organization"
    return k in low_text or (k.endswith("s") and not k.endswith("ss") and k[:-1] in low_text)


class SkillsMatcher:
    def __init__(self, settings: dict | None, skills: list[dict] | None):
        s = settings or {}
        self.on = _yes(s.get("on", False))
        self.required = [k.strip() for k in s.get("required_keywords", []) if isinstance(k, str) and k.strip()]
        self.rule = (str(s.get("required_keywords_rule") or "all")).strip().lower()
        self.minimum = int(s.get("minimum_skills_matched", 3) or 0)
        self.skills = [(sk.get("name", ""), [k for k in sk.get("keywords", []) if isinstance(k, str)])
                       for sk in (skills or []) if sk.get("keywords")]

    @property
    def active(self) -> bool:
        return self.on and bool(self.required or self.minimum)

    def keywords_present(self, low_text: str) -> bool:
        if not self.required:
            return True
        found = [re.search(r"(?<!\w)" + re.escape(k.lower()) + r"(?!\w)", low_text) is not None for k in self.required]
        return any(found) if self.rule == "any" else all(found)

    def matched_skills(self, low_text: str) -> list[str]:
        return [name for name, kws in self.skills if any(_hit(k, low_text) for k in kws)]

    def match(self, title: str, text: str) -> dict | None:
        if not self.active or not text:
            return None
        low = f"{title or ''}\n{text}".lower()
        if not self.keywords_present(low):
            return None
        skills = self.matched_skills(low)
        if len(skills) < self.minimum:
            return None
        return {"track": "S", "family": SKILLS_FAMILY, "skills": skills}

    def search_words(self) -> str:
        """Search words for sites that search by keyword (empty when off)."""
        return " ".join(self.required) if self.active else ""

    def summary(self) -> str:
        if not self.active:
            return "OFF"
        joined = (" and " if self.rule != "any" else " or ").join(f'"{k}"' for k in self.required) or "none"
        return (f"ON: keywords {joined} must appear"
                f"{'' if self.rule != 'any' else ' (at least one)'}, and at least {self.minimum} of your "
                f"{len(self.skills)} skills")
