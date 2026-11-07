"""Match a job title to one of the user's job titles (JOB_SEARCH_SETTINGS/job_search_settings.toml)."""
from __future__ import annotations

import re


def _c(p: str) -> re.Pattern:
    return re.compile(p, re.I)


def word_forms(w: str) -> str:
    """A title word that also matches its singular or plural: "victims" also finds "Victim",
    "advocate" also finds "Advocates", "policy" also finds "Policies"."""
    low = w.lower()
    if len(low) <= 3 or not low[-1].isalpha():
        return re.escape(w)
    if low.endswith("ies"):
        return re.escape(w[:-3]) + "(?:y|ies)"
    if low.endswith("ss"):
        return re.escape(w) + "(?:es)?"
    if low.endswith(("ches", "shes", "xes", "sses")):
        return re.escape(w[:-2]) + "(?:es)?"
    if low.endswith("s"):
        return re.escape(w[:-1]) + "s?"
    if low.endswith("y") and low[-2] not in "aeiou":
        return re.escape(w[:-1]) + "(?:y|ies)"
    if low.endswith(("ch", "sh", "x")):
        return re.escape(w) + "(?:es)?"
    return re.escape(w) + "s?"


class TitleMatcher:
    def __init__(self, profile: dict, skills: list[dict] | None = None):
        from .skills_match import SkillsMatcher
        self.skills = SkillsMatcher(profile.get("skills_matching"), skills)
        self.families = []
        for t in profile.get("titles", []):
            # all of the title's words, in any order: "Data Analyst" also finds "Analyst, Data" and "Senior Data Analyst";
            # each word also matches its singular or plural ("Victims Advocate" finds "Victim Advocate")
            pats = [_c("".join(r"(?=.*\b" + word_forms(w) + r"(?!\w))" for w in pt.split()))
                    for pt in t.get("plain_titles", []) if pt.split()]
            if pats:
                self.families.append({"track": t["track"], "family": t["family"], "include": pats,
                                      "require_any": [w.lower() for w in t.get("require_any", [])]})
        tr = profile.get("title_rules", {})
        # title words to skip: whole words or phrases, title only (the posting text is never checked)
        self.exclude = [_c(r"\b" + r"\s+".join(map(re.escape, w.split())) + r"\b")
                        for w in tr.get("exclude_plain", []) if isinstance(w, str) and w.strip()]
        self.must_mention = [w.lower() for w in tr.get("must_mention", [])]
        # listings with one of the job titles that were left out only by "words the description must mention"
        # (a set, because the same listing can be checked more than once)
        self.left_out_must_mention: set[tuple[str, str]] = set()

    def match_title(self, title: str) -> bool:
        """Title-only pre-screen (before the job description is downloaded)."""
        t = re.sub(r"\s+", " ", title or "").strip()
        return bool(t) and not any(p.search(t) for p in self.exclude) and any(
            p.search(t) for f in self.families for p in f["include"])

    def match(self, title: str, text: str = "") -> dict | None:
        t = re.sub(r"\s+", " ", title or "").strip()
        if not t or any(p.search(t) for p in self.exclude):
            return None
        low_text = (text or "").lower()
        title_matched = False
        for f in self.families:
            if not any(p.search(t) for p in f["include"]):
                continue
            title_matched = True
            if self.must_mention and not any(w in (t.lower() + " " + low_text) for w in self.must_mention):
                self.left_out_must_mention.add((t.lower(), low_text[:300]))
                continue
            if f["require_any"] and not any(w in low_text for w in f["require_any"]):
                continue
            return {"track": f["track"], "family": f["family"]}
        if title_matched:
            return None   # one of your titles, but a title condition failed: not a skills match either
        if self.must_mention and not any(w in (t.lower() + " " + low_text) for w in self.must_mention):
            return None
        return self.skills.match(t, text)   # other titles: skills-based matching (when on)
