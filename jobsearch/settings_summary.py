"""'Your settings for this search': the settings a search used, shown at the top of its report."""
from __future__ import annotations

from pathlib import Path

# Relative to JOB_SEARCH_RESULTS/REPORT_*.md on github.com: the questionnaire list in this copy
CHANGE_LINK = "../../../issues/new/choose"


MAX_LINK_LENGTH = 7000   # GitHub refuses very long links ("414 URI Too Long")


def prefill_answers(root: Path) -> dict:
    """The current settings as questionnaire answers (text questions only: a link cannot fill in dropdowns)."""
    import tomllib
    from .pipeline import SETTINGS_DIR
    try:
        st = tomllib.load(open(Path(root) / SETTINGS_DIR / "job_search_settings.toml", "rb"))
        ex = tomllib.load(open(Path(root) / SETTINGS_DIR / "my_experience.toml", "rb"))
    except (OSError, ValueError):
        return {}
    lines = lambda xs: "\n".join(str(x) for x in (xs or []) if str(x).strip())
    num = lambda n: str(n) if n else ""
    jt, w, sal, f = st.get("job_titles", {}), st.get("where_you_live", {}), st.get("salary", {}), st.get("filters", {})
    titles = jt.get("titles") or [t for g in jt.get("group") or [] for t in g.get("titles", [])]
    rule = (f.get("requirement_rule") or [{}])[0]
    sk, ten = st.get("skills_matching", {}), ex.get("tenure", {})
    skills = []
    for s in ex.get("skills", []):
        name, kws = s.get("name", ""), [k for k in s.get("keywords", []) if k]
        plain = [name.split("(")[0].strip().lower()] + [p.strip().lower() for p in name.split("(")[1:] for p in [p.split(")")[0]]]
        skills.append(name if [k.lower() for k in kws] == [p for p in plain if p] else f"{name}: {', '.join(kws)}")
    on = lambda v: str(v).strip().lower() in ("yes", "y", "true", "on", "1")
    return {k: v for k, v in {
        "titles_1": lines(titles), "state": w.get("state", ""), "state_abbreviation": w.get("state_abbreviation", ""),
        "cities": lines(w.get("cities")), "min_single": num(sal.get("minimum_single_salary")),
        "min_range_start": num(sal.get("minimum_range_start")), "range_end_above": num(sal.get("range_end_must_be_above")),
        "skip_titles": lines(f.get("exclude_titles_containing")), "rule_words": lines(rule.get("terms")) if on(rule.get("on")) else "",
        "must_mention": lines(f.get("description_must_mention")), "certs_allowed": lines(f.get("certifications_allowed_before_hire")),
        "skip_employers": lines(f.get("exclude_employers")), "my_skills": lines(skills),
        "years": num(ten.get("years_of_experience")), "certs_held": lines(ten.get("certifications_held")),
        "companies": lines((st.get("companies_to_watch") or {}).get("job_page_links")),
        "skills_min": num(sk.get("minimum_skills_matched")) if on(sk.get("on")) else "",
        "skills_keywords": lines(sk.get("required_keywords")),
    }.items() if v}


def change_link(root: Path, base: str = "../../../issues/new") -> str:
    """A link that opens the questionnaire with the current answers filled in."""
    from urllib.parse import urlencode
    answers = prefill_answers(root)
    for drop in ("", "my_skills", "companies"):   # very long answers would make the link too long for GitHub
        answers.pop(drop, None)
        link = f"{base}?" + urlencode({"template": "job_search_setup.yml", "title": "Job search setup", **answers})
        if len(link) <= MAX_LINK_LENGTH:
            return link
    return CHANGE_LINK


def _list(items) -> str:
    items = [str(x) for x in (items or []) if str(x).strip()]
    return ", ".join(items) if items else "none"


def rows(root: Path, prof: dict, exp: dict) -> list[list[str]]:
    """[setting, value] pairs, in the order of the setup questionnaire."""
    import tomllib
    from .criteria import CRITERIA as C
    from .pipeline import SETTINGS_DIR, watched_boards
    from .titles import TitleMatcher
    from .adapters.aggregators import search_size, SEARCH_SIZE_DEFAULTS
    try:
        settings = tomllib.load(open(Path(root) / SETTINGS_DIR / "job_search_settings.toml", "rb"))
    except (OSError, ValueError):
        settings = {}
    out = []
    titles = prof.get("titles", [])
    for t in titles:
        out.append([f"Job titles ({t['family']})" if len(titles) > 1 else "Job titles", _list(t.get("plain_titles"))])
    if not prof.get("titles"):
        out.append(["Job titles", "none"])
    out.append(["Remote jobs only?", "Yes" if C.get("remote_only", True) else "No, also hybrid and on-site jobs in my state"])
    out.append(["Your state", f"{C['home_state']} ({C.get('home_state_abbr') or '-'})" if C.get("home_state") else "not set"])
    if not C.get("remote_only", True):
        out.append(["Cities where you could work on site", _list(C.get("home_cities"))])
    money = lambda n: f"${n:,}" if n else "no minimum"
    out.append(["Lowest single salary you would accept", money(C.get("single_min", 0))])
    out.append(["Lowest acceptable start of a salary range", money(C.get("range_lower_min", 0))])
    out.append(["A salary range must end above", money(C.get("range_upper_gt", 0))])
    tr = prof.get("title_rules", {})
    out.append(["Title words to skip", _list(tr.get("exclude_plain"))])
    out.append(["Words the job description must mention", _list(tr.get("must_mention"))])
    out.append(["Check certification requirements?", "Yes" if C.get("cert_filter_on", True) else "No"])
    if C.get("cert_filter_on", True):
        out.append(["Certifications you have, or could get before being hired", _list(C.get("allowed_prehire_certs"))])
    out.append(["Employers to skip", _list((prof.get("employer_rules") or {}).get("exclude"))])
    rules = [r for r in C.get("custom_rules", []) if r.get("on", True)]
    for r in rules:
        when = "when the job requires them" if r.get("reject_when", "required") == "required" else "whenever they are mentioned"
        out.append([r["name"], f"{_list(r['terms'])} ({when})"])
    if not rules:
        out.append(["Words that rule out a job", "none"])
    out.append(["Your skills", _list(s.get("name") for s in exp.get("skills", []))])
    ten = exp.get("tenure", {}) or {}
    out.append(["Years of experience in your field", str(ten.get("years_of_experience") or "not set")])
    out.append(["Certifications you hold", _list(ten.get("certifications_held"))])
    watched, _ = watched_boards(prof)
    out.append(["Companies to watch", _list(b["board"] for b in watched)])
    out.append(["Skills-based matching", TitleMatcher(prof, exp.get("skills", [])).skills.summary()])
    sz = search_size({"search_size": settings.get("search_size") or {}})
    out.append(["How many jobs to look through",
                ("Recommended: " if sz == SEARCH_SIZE_DEFAULTS else "")
                + f"{sz['newest_jobs_per_site']:,} newest jobs per site, {sz['jobs_per_search_word']:,} for each job title, "
                  f"Jobicy up to {sz['jobicy_max_jobs']:,}"])
    wk = settings.get("automatic_weekly_search") or {}
    on = str(wk.get("on", "no")).strip().lower() in ("yes", "y", "true", "on", "1")
    out.append(["Automatic weekly searches", f"On, every {str(wk.get('day', 'Friday')).capitalize()}" if on else "Off"])
    return out


def section(pairs: list[list[str]] | None, link: str = CHANGE_LINK, title: str = "Your settings for this search") -> list[str]:
    if not pairs:
        return []
    cell = lambda x: str(x).replace("|", "/").replace("\n", " ")
    L = [f"## {title}", "", f"[click here to change these settings]({link})", "",
         "| Setting | Your answer |", "|---|---|"]
    L += [f"| {cell(k)} | {cell(v)} |" for k, v in pairs]
    return L + [""]
