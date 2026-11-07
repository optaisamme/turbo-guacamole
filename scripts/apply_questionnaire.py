"""Apply the "Job search setup" questionnaire (a GitHub issue form) to the settings files.

Runs in GitHub Actions when the copy's owner (or a collaborator) submits the form. Reads the
answers from the issue text, updates JOB_SEARCH_SETTINGS (settings and experience files), checks
the settings, and writes a plain-language reply for the issue.

Answers are untrusted text: they are only ever written into the settings as quoted values.
Each questionnaire replaces all settings: answers are applied to the starting settings
(jobsearch/defaults/), so a text question left blank is off or empty. A dropdown left on "None"
keeps its current choice. The results page's change link fills in the current text answers.

  python scripts/apply_questionnaire.py            read ISSUE_BODY, update settings, write out/questionnaire_reply.md
  python scripts/apply_questionnaire.py --reply    post the reply on the issue (and close it when everything worked)
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SETTINGS = ROOT / "JOB_SEARCH_SETTINGS/job_search_settings.toml"
EXPERIENCE = ROOT / "JOB_SEARCH_SETTINGS/my_experience.toml"
DEFAULT_SETTINGS = ROOT / "jobsearch/defaults/job_search_settings.toml"     # the settings a new copy starts with
DEFAULT_EXPERIENCE = ROOT / "jobsearch/defaults/my_experience.toml"
OUT = ROOT / "out"
TEMPLATE_REPO = "optaisamme/job-search-automation-publicuse"   # the shared template: never changed
ALLOWED = {"OWNER", "MEMBER", "COLLABORATOR"}   # who may change this copy's settings
DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
MAX_ITEMS, MAX_LEN = 40, 200

# question label (exactly as in .github/ISSUE_TEMPLATE/job_search_setup.yml) -> answer key
LABELS = {
    "Job titles to look for": "titles_1",
    "Remote jobs only?": "remote_only",
    "Your state": "state",
    "Your state's two-letter abbreviation": "state_abbreviation",
    "Cities where you could work on site": "cities",
    "Lowest single salary you would accept": "min_single",
    "Lowest acceptable start of a salary range": "min_range_start",
    "A salary range must end above": "range_end_above",
    "Words the job description must mention": "must_mention",
    "Title words to skip": "skip_titles",
    "Employers to skip": "skip_employers",
    "Words that rule out a job": "rule_words",
    "Rule out a job when these words are…": "rule_when",
    "Check certification requirements?": "cert_check",
    "Certifications you have, or could get before being hired": "certs_allowed",
    "Companies to watch": "companies",
    "Turn on skills-based matching?": "skills_on",
    "Skills-based matching: keywords the listing must include": "skills_keywords",
    "All keywords, or at least one?": "skills_rule",
    "How many of your skills must the listing mention?": "skills_min",
    "Your skills": "my_skills",
    "Years of experience in your field": "years",
    "Certifications you hold": "certs_held",
    "How many jobs to look through": "search_size",
    "Automatic weekly searches": "weekly",
    "Day for automatic weekly searches": "weekly_day",
    "Start a search right after saving?": "run_now",
}


# "How many jobs to look through": the numbers written to the settings file
SEARCH_SIZES = {
    "recommended": {"newest_jobs_per_site": 1000, "jobs_per_search_word": 200, "jobicy_max_jobs": 5000},
    "fewer": {"newest_jobs_per_site": 200, "jobs_per_search_word": 100, "jobicy_max_jobs": 500},
    "more": {"newest_jobs_per_site": 2000, "jobs_per_search_word": 400, "jobicy_max_jobs": 10000},
}


# question labels used by earlier versions of the form, so an older questionnaire issue can still be edited
OLD_LABELS = {
    "Skills-based matching": "skills_on",
    "Skills-based matching: required keywords": "skills_keywords",
    "Keywords the listing must include": "skills_keywords",
    "Skills-based matching: how many keywords must appear?": "skills_rule",
    "Skills-based matching: how many of your skills must the posting mention?": "skills_min",
    "More job titles: a second kind of job": None,
    "More job titles: a third kind of job": None,
}


# Dropdown questions. GitHub writes "None" for a dropdown left unanswered; that means "keep my setting".
DROPDOWNS = {"remote_only", "cert_check", "rule_when", "skills_on", "skills_rule", "search_size", "weekly", "weekly_day", "run_now"}


class AnswerProblem(ValueError):
    """An answer that cannot be used; the message is shown to the user."""


# ---------------------------------------------------------------- reading the form
def parse_issue_body(body: str) -> dict:
    """GitHub writes each answer under '### <question label>'; empty answers are '_No response_'."""
    answers, current, lines = {}, None, []

    def flush():
        key = LABELS.get(current) or OLD_LABELS.get(current)
        if key:
            text = "\n".join(lines).strip()
            m = re.fullmatch(r"(`{3,})[\w-]*\n(.*?)\n?\1", text, re.S)   # answers from plain text boxes come as a code block
            if m:
                text = m.group(2).strip()
            if key in DROPDOWNS and text == "None":
                text = ""   # dropdown left unanswered
            answers[key] = "" if text in ("", "_No response_") else text

    for line in (body or "").replace("\r\n", "\n").split("\n"):
        m = re.match(r"^###\s+(.+?)\s*$", line)
        if m:
            flush()
            current, lines = m.group(1), []
        else:
            lines.append(line)
    flush()
    return answers


def _clean(s: str) -> str:
    s = re.sub(r"[\x00-\x1f\x7f]", " ", s).strip()
    if len(s) > MAX_LEN:
        raise AnswerProblem(f'"{s[:40]}…" is too long (at most {MAX_LEN} characters per line).')
    return s


def _unquote(x: str) -> str:
    """'  "data analysis" ' -> 'data analysis' (quotation marks would otherwise be searched for too)."""
    return x.strip(" -*•\t").strip("\"'“”‘’").strip()


def as_list(text: str) -> list[str] | None:
    """One item per line (commas also separate). None = keep current; [] = clear ("none")."""
    if not text:
        return None
    if text.strip().lower() == "none":
        return []
    items = [_clean(_unquote(x)) for line in text.split("\n") for x in line.split(",")]
    items = list(dict.fromkeys(x for x in items if x))
    if len(items) > MAX_ITEMS:
        raise AnswerProblem(f"Too many items in one answer (at most {MAX_ITEMS}).")
    return items


def as_number(text: str, label: str) -> int | None:
    if not text:
        return None
    if text.strip().lower() == "none":
        return 0
    digits = re.sub(r"[,$\s]", "", text)
    if not digits.isdigit():
        raise AnswerProblem(f'"{label}": type a number only, for example 90000 (you typed "{_clean(text)[:40]}").')
    return int(digits)


def as_text(text: str) -> str | None:
    if not text:
        return None
    return "" if text.strip().lower() == "none" else _clean(text)


def as_choice(text: str) -> str | None:
    return text.strip().lower() if text else None


# ---------------------------------------------------------------- writing TOML values
def toml_value(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, list):
        return "[" + ", ".join(toml_value(x) for x in v) + "]"
    return json.dumps(str(v), ensure_ascii=False)   # a JSON string is a valid TOML basic string


def set_value(text: str, header: str, key: str, value, occurrence: int = 0, append_block: str = "") -> str:
    """Replace `key = …` inside the given table (the n-th time the header appears).
    Values may span several lines (lists); the replacement is written on one line.
    If the table or key is missing (an older copy), the table is added at the end."""
    lines = text.split("\n")
    seen, start = -1, None
    for i, line in enumerate(lines):
        if line.strip() == header:
            seen += 1
            if seen == occurrence:
                start = i
                break
    if start is None:
        if append_block:   # add the whole table with its other settings, then set this value in it
            return set_value(text.rstrip("\n") + "\n\n" + append_block + "\n", header, key, value, occurrence)
        return text.rstrip("\n") + "\n\n" + f"{header}\n{key} = {toml_value(value)}" + "\n"
    i = start + 1
    while i < len(lines) and not lines[i].lstrip().startswith("["):
        if re.match(rf"^{re.escape(key)}\s*=", lines[i]):
            end, depth = i, lines[i].split("#")[0].count("[") - lines[i].split("#")[0].count("]")
            while depth > 0 and end + 1 < len(lines):
                end += 1
                depth += lines[end].count("[") - lines[end].count("]")
            lines[i:end + 1] = [f"{key} = {toml_value(value)}"]
            return "\n".join(lines)
        i += 1
    lines.insert(i, f"{key} = {toml_value(value)}")
    return "\n".join(lines)


# ---------------------------------------------------------------- applying answers
def apply(answers: dict, settings_text: str, experience_text: str) -> tuple[str, str, list[str], dict]:
    """Return (new settings text, new experience text, list of changes in plain words, extras)."""
    s, e, changed = settings_text, experience_text, []

    def put(header, key, value, label, occurrence=0, append_block=""):
        nonlocal s
        s = set_value(s, header, key, value, occurrence, append_block)
        shown = ", ".join(value) if isinstance(value, list) else value
        changed.append(f"{label}: {shown if shown not in ('', []) else 'cleared'}")

    v = as_list(answers.get("titles_1", ""))
    if v is not None:
        put("[job_titles]", "titles", v, "Job titles")

    r = as_choice(answers.get("remote_only", ""))
    if r:
        put("[where_you_live]", "remote_only", "yes" if r.startswith("yes") else "no", "Remote jobs only")
    for k, key, label in (("state", "state", "Your state"), ("state_abbreviation", "state_abbreviation", "State abbreviation")):
        v = as_text(answers.get(k, ""))
        if v is not None:
            if key == "state_abbreviation" and v and not re.fullmatch(r"[A-Za-z]{2}", v):
                raise AnswerProblem(f'"Your state\'s two-letter abbreviation": type two letters, for example NM (you typed "{v}").')
            put("[where_you_live]", key, v.upper() if key == "state_abbreviation" else v, label)
    v = as_list(answers.get("cities", ""))
    if v is not None:
        put("[where_you_live]", "cities", v, "Cities")

    for k, key, label in (("min_single", "minimum_single_salary", "Lowest single salary"),
                          ("min_range_start", "minimum_range_start", "Lowest salary range start"),
                          ("range_end_above", "range_end_must_be_above", "Salary range must end above")):
        v = as_number(answers.get(k, ""), label)
        if v is not None:
            put("[salary]", key, v, label)

    for k, key, label in (("must_mention", "description_must_mention", "Description must mention"),
                          ("skip_titles", "exclude_titles_containing", "Title words to skip"),
                          ("skip_employers", "exclude_employers", "Employers to skip"),
                          ("certs_allowed", "certifications_allowed_before_hire", "Certifications allowed before hire")):
        v = as_list(answers.get(k, ""))
        if v is not None:
            put("[filters]", key, v, label)
    c = as_choice(answers.get("cert_check", ""))
    if c:
        put("[filters]", "check_certifications", "yes" if c.startswith("yes") else "no", "Check certifications")

    rule = "[[filters.requirement_rule]]"
    rule_block = f'{rule}\nname = "Words that rule out a job"\non = "no"\nterms = []\nreject_when = "required"'
    v = as_list(answers.get("rule_words", ""))
    if v is not None:
        put(rule, "terms", v, "Words that rule out a job", append_block=rule_block)
        s = set_value(s, rule, "on", "yes" if v else "no")
        s = set_value(s, rule, "name", "Words that rule out a job")
    c = as_choice(answers.get("rule_when", ""))
    if c:
        put(rule, "reject_when", "mentioned" if c.startswith("mentioned") else "required",
            "Rule out a job when the words are", append_block=rule_block)

    v = as_list(answers.get("companies", ""))
    if v is not None:
        from jobsearch.adapters.ats import parse_board_link, SUPPORTED_JOB_PAGES
        bad = [x for x in v if not parse_board_link(x)]
        if bad:
            raise AnswerProblem(f'"Companies to watch": these links are not company job pages this tool can read: '
                                f'{", ".join(bad)}. Use links that start with {SUPPORTED_JOB_PAGES}.')
        put("[companies_to_watch]", "job_page_links", v, "Companies to watch")

    sk_block = '[skills_matching]\non = "no"\nrequired_keywords = []\nrequired_keywords_rule = "all"\nminimum_skills_matched = 3'
    c = as_choice(answers.get("skills_on", ""))
    if c:
        put("[skills_matching]", "on", "yes" if c.startswith("on") else "no", "Skills-based matching", append_block=sk_block)
    v = as_list(answers.get("skills_keywords", ""))
    if v is not None:
        put("[skills_matching]", "required_keywords", v, "Skills-matching keywords", append_block=sk_block)
    c = as_choice(answers.get("skills_rule", ""))
    if c:
        put("[skills_matching]", "required_keywords_rule", "any" if c.startswith("at least one") else "all",
            "Skills-matching keyword rule", append_block=sk_block)
    v = as_number(answers.get("skills_min", ""), "How many of your skills must the listing mention?")
    if v is not None:
        put("[skills_matching]", "minimum_skills_matched", v, "Skills-matching minimum skills", append_block=sk_block)

    c = as_choice(answers.get("search_size", ""))
    if c:
        name = next((k for k in SEARCH_SIZES if c.startswith(k)), None)
        if name is None:
            raise AnswerProblem('"How many jobs to look through": choose Recommended, Fewer or More.')
        size = SEARCH_SIZES[name]
        block = "[search_size]\n" + "\n".join(f"{k} = {v}" for k, v in size.items())
        for k, v in size.items():
            s = set_value(s, "[search_size]", k, v, append_block=block)
        changed.append(f"How many jobs to look through: {name.capitalize()} "
                       f"({size['newest_jobs_per_site']:,} newest jobs per site, {size['jobs_per_search_word']:,} for each "
                       f"job title, Jobicy up to {size['jobicy_max_jobs']:,})")

    wk_block = '[automatic_weekly_search]\non = "no"\nday = "Friday"'
    c = as_choice(answers.get("weekly", ""))
    if c:
        put("[automatic_weekly_search]", "on", "yes" if c.startswith("on") else "no", "Automatic weekly searches",
            append_block=wk_block)
    d = (answers.get("weekly_day") or "").strip().capitalize()
    if d:
        if d not in DAYS:
            raise AnswerProblem(f'"Day for automatic weekly searches": choose a day of the week (you chose "{d}").')
        put("[automatic_weekly_search]", "day", d, "Weekly search day", append_block=wk_block)

    # experience file
    v = as_number(answers.get("years", ""), "Years of experience")
    if v is not None:
        e = set_value(e, "[tenure]", "years_of_experience", v)
        changed.append(f"Years of experience: {v}")
    v = as_list(answers.get("certs_held", ""))
    if v is not None:
        e = set_value(e, "[tenure]", "certifications_held", v)
        changed.append(f"Certifications you hold: {', '.join(v) or 'cleared'}")
    skills_text = answers.get("my_skills", "")
    if skills_text:
        e, names = replace_skills(e, skills_text)
        changed.append(f"Your skills: {', '.join(names) or 'cleared'}")

    extras = {"run_now": (as_choice(answers.get("run_now", "")) or "").startswith("yes")}
    return s, e, changed, extras


def replace_skills(experience_text: str, skills_text: str) -> tuple[str, list[str]]:
    """Replace every [[skills]] block with the questionnaire's list. [[gaps]] blocks are kept.
    Each line is 'Skill name: word, word' (the words a posting might use)."""
    if skills_text.strip().lower() == "none":
        lines = []
    else:
        lines = [_clean(x) for x in skills_text.split("\n") if x.strip(" -*•\t")]
    if len(lines) > MAX_ITEMS:
        raise AnswerProblem(f"Too many skills (at most {MAX_ITEMS}).")
    current = tomllib.loads(experience_text)
    marker = "# (skills from the questionnaire are saved below)"
    old_marker = "# (add your [[skills]] and [[gaps]] blocks below)"   # older copies
    experience_text = experience_text.replace(old_marker, marker)
    head = experience_text.split(marker)[0].rstrip("\n") if marker in experience_text else \
        re.split(r"\n\[\[(?:skills|gaps)\]\]", experience_text)[0].rstrip("\n")
    blocks, names = [], []
    for line in lines:
        name, _, words = line.strip(" -*•\t").partition(":")
        name = _unquote(name)
        if words.strip():
            kws = [_unquote(w).lower() for w in words.split(",") if _unquote(w)]
        else:   # no words given: the skill's name, and any short form in brackets ("Customer Relationship Management (CRM)")
            kws = [re.sub(r"\s*\([^)]*\)", "", name).strip().lower()] + [p.strip().lower() for p in re.findall(r"\(([^)]*)\)", name)]
            kws = [k for k in dict.fromkeys(kws) if k]
        names.append(name)
        blocks.append(f'[[skills]]\nname = {toml_value(name)}\nkind = "demonstrated"\nkeywords = {toml_value(kws)}\n'
                      f'evidence = "Added through the setup questionnaire"')
    for g in current.get("gaps", []):
        blocks.append(f'[[gaps]]\nname = {toml_value(g.get("name", ""))}\nkeywords = {toml_value(g.get("keywords", []))}')
    out = head + "\n\n" + marker + "\n" + ("\n\n".join(blocks) + "\n" if blocks else "")
    return out, names


def current_choices(settings_text: str) -> dict:
    """The current dropdown choices, as answers. A link cannot fill in dropdowns, so a dropdown left on
    "None" keeps its current choice."""
    try:
        st = tomllib.loads(settings_text or "")
    except tomllib.TOMLDecodeError:
        return {}
    yes = lambda v: str(v).strip().lower() in ("yes", "y", "true", "on", "1")
    w, f, sk, wk = st.get("where_you_live", {}), st.get("filters", {}), st.get("skills_matching", {}), st.get("automatic_weekly_search", {})
    rules = f.get("requirement_rule") or [{}]
    sz = st.get("search_size") or {}
    out = {"remote_only": "yes" if yes(w.get("remote_only", "yes")) else "no",
           "cert_check": "yes" if yes(f.get("check_certifications", "no")) else "no",
           "rule_when": "mentioned" if str(rules[0].get("reject_when", "required")).lower() == "mentioned" else "required",
           "skills_on": "on" if yes(sk.get("on", "no")) else "off",
           "skills_rule": "at least one" if str(sk.get("required_keywords_rule", "all")).lower() == "any" else "all",
           "weekly": "on" if yes(wk.get("on", "no")) else "off",
           "weekly_day": str(wk.get("day") or "Friday")}
    preset = next((name for name, v in SEARCH_SIZES.items() if all(sz.get(k) == n for k, n in v.items())), None)
    if preset:
        out["search_size"] = preset
    return out


def build_settings(answers: dict, current_settings: str = "") -> tuple[str, str, list[str], dict]:
    """Every questionnaire replaces all settings: answers are applied to the starting settings, never to the
    previous answers, so a text question left blank is off or empty. A dropdown left on "None" keeps its
    current choice (the "change these settings" link fills in text answers, but cannot fill in dropdowns)."""
    answers = dict(answers)
    for k, v in current_choices(current_settings).items():
        if not answers.get(k):
            answers[k] = v
    s, e, changes, extras = apply(answers, DEFAULT_SETTINGS.read_text(), DEFAULT_EXPERIENCE.read_text())
    if not answers.get("search_size"):   # custom numbers (not one of the three choices): keep them
        try:
            for k, v in (tomllib.loads(current_settings).get("search_size") or {}).items():
                s = set_value(s, "[search_size]", k, v)
        except tomllib.TOMLDecodeError:
            pass
    return s, e, changes, extras


# ---------------------------------------------------------------- run
def main() -> int:
    OUT.mkdir(exist_ok=True)
    reply, ok, changed_files, run_now = [], False, False, False
    assoc = os.environ.get("AUTHOR_ASSOCIATION", "")
    if os.environ.get("IS_TEMPLATE", "").lower() == "true" or os.environ.get("GITHUB_REPOSITORY") == TEMPLATE_REPO:
        reply = ["This is the shared template, so its settings are never changed. To set up your own job search, "
                 "make your own copy first (SETUP.md, Step 1), then fill in the questionnaire in your copy."]
    elif assoc not in ALLOWED:
        reply = ["This copy's settings can only be changed by its owner or a collaborator, so these answers "
                 "were not used. To set up your own job search, make your own copy first (SETUP.md, Step 1)."]
    else:
        answers = parse_issue_body(os.environ.get("ISSUE_BODY", ""))
        if not answers:
            reply = ["No questionnaire answers were found in this issue. Open **Issues**, then **New issue**, "
                     "then **Job search setup**, and fill in the form."]
        else:
            old_s, old_e = SETTINGS.read_text(), EXPERIENCE.read_text()
            try:
                new_s, new_e, changes, extras = build_settings(answers, old_s)
                tomllib.loads(new_s), tomllib.loads(new_e)
                SETTINGS.write_text(new_s)
                EXPERIENCE.write_text(new_e)
                check = subprocess.run([sys.executable, "-m", "jobsearch", "check"], cwd=ROOT, capture_output=True, text=True)
                if check.returncode != 0:
                    SETTINGS.write_text(old_s)
                    EXPERIENCE.write_text(old_e)
                    reply = ["**Your settings were not saved.** The settings check found a problem:", "",
                             "```text", check.stdout.strip()[-3000:], "```", "",
                             "Edit this issue to fix your answer (the **...** menu, then **Edit**); it is checked again when you save."]
                else:
                    ok, changed_files = True, (new_s != old_s or new_e != old_e)
                    reply = ["**Your settings are saved.**" if changed_files else "**Nothing changed:** every answer was blank or the same as before.",
                             "", "What changed:", ""] + [f"{i}. {c}" for i, c in enumerate(changes, 1)] + \
                            ["", "Your settings now:", "", "```text", check.stdout.strip()[-3000:], "```"]
                    reply += ["", "To change something later, use **click here to change these settings** at the top of "
                              "the JOB_SEARCH_RESULTS folder page: it opens this questionnaire with your current answers filled in."]
                    run_now = extras["run_now"]
            except AnswerProblem as e:
                reply = ["**Your settings were not saved.**", "", str(e), "",
                         "Edit this issue to fix your answer (the **...** menu, then **Edit**); it is checked again when you save."]
            except tomllib.TOMLDecodeError as e:
                reply = ["**Your settings were not saved.** A settings file in JOB_SEARCH_SETTINGS could not be read "
                         f"({e}). It was probably changed by hand. Ask the person who shared this tool for help."]
    (OUT / "questionnaire_reply.md").write_text("\n".join(reply) + "\n")
    (OUT / "questionnaire_ok.txt").write_text("yes" if ok else "no")
    (OUT / "questionnaire_changed.txt").write_text("yes" if changed_files else "no")
    (OUT / "questionnaire_run_now.txt").write_text("yes" if run_now else "no")
    print("\n".join(reply))
    return 0


WORKFLOW_FILE = "run_job_search.yml"


def _api(method: str, path: str, payload=None):
    import urllib.request
    req = urllib.request.Request(f"https://api.github.com/repos/{os.environ['GITHUB_REPOSITORY']}/{path}",
                                 data=json.dumps(payload).encode() if payload is not None else None, method=method,
                                 headers={"Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}",
                                          "Accept": "application/vnd.github+json", "User-Agent": "job-search-automation"})
    with urllib.request.urlopen(req, timeout=30) as r:
        body = r.read()
        return json.loads(body) if body else {}


def start_search(wait_seconds: float = 3, tries: int = 10) -> int:
    """Start a search and write what happened, with a link to the search, for the reply on the issue."""
    import time
    import urllib.error
    from datetime import datetime, timezone, timedelta
    repo = os.environ["GITHUB_REPOSITORY"]
    page = f"{os.environ.get('GITHUB_SERVER_URL', 'https://github.com')}/{repo}/actions/workflows/{WORKFLOW_FILE}"
    since = (datetime.now(timezone.utc) - timedelta(seconds=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
    OUT.mkdir(exist_ok=True)
    try:
        _api("POST", f"actions/workflows/{WORKFLOW_FILE}/dispatches", {"ref": os.environ.get("DEFAULT_BRANCH") or "main"})
    except Exception as e:  # noqa: BLE001 — the reason is shown to the user
        reason = str(e)
        if isinstance(e, urllib.error.HTTPError):
            try:
                reason = f"{e.code}: {json.loads(e.read() or b'{}').get('message', '')}"
            except ValueError:
                pass
        (OUT / "questionnaire_search.md").write_text(
            f"**The search could not be started automatically** (GitHub answered {reason}). Start it yourself: "
            f"[open the Run job search page]({page}), then click **Run workflow** and the green **Run workflow** button.\n")
        print(f"Could not start a search: {reason}")
        return 1
    url = ""
    for _ in range(tries):   # find the search that just started, to link to it
        time.sleep(wait_seconds)
        try:
            runs = _api("GET", f"actions/workflows/{WORKFLOW_FILE}/runs?event=workflow_dispatch&per_page=5").get("workflow_runs", [])
        except Exception:  # noqa: BLE001
            runs = []
        new = [r for r in runs if (r.get("created_at") or "") >= since]
        if new:
            url = new[0].get("html_url", "")
            break
    (OUT / "questionnaire_search.md").write_text(
        "## Your job search is running\n\n"
        + (f"**[Click here to watch it]({url})**" if url else f"**[Click here to see your searches]({page})**")
        + ". It takes about 10 to 20 minutes. When it finishes, your results are in the **JOB_SEARCH_RESULTS** folder "
        "(**Code** tab).\n\nAny time: " + "click the **Actions** tab at the top of your copy, then **Run job search** on the left. A yellow dot means a search is running; a green check means it finished." + "\n")
    print(f"Search started: {url or page}")
    return 0


def post_reply() -> int:
    """Post the reply on the issue; close the issue when the settings were saved."""
    import urllib.request
    repo, token, number = os.environ["GITHUB_REPOSITORY"], os.environ["GITHUB_TOKEN"], os.environ["ISSUE_NUMBER"]
    body = (OUT / "questionnaire_reply.md").read_text()
    if (OUT / "questionnaire_search.md").exists():   # the search the questionnaire started: its link comes first
        body = (OUT / "questionnaire_search.md").read_text().rstrip("\n") + "\n\n---\n\n" + body

    def call(method, path, payload):
        req = urllib.request.Request(f"https://api.github.com/repos/{repo}/{path}", data=json.dumps(payload).encode(),
                                     method=method, headers={"Authorization": f"Bearer {token}",
                                                             "Accept": "application/vnd.github+json",
                                                             "User-Agent": "job-search-automation"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read() or b"{}")

    call("POST", f"issues/{number}/comments", {"body": body})
    if (OUT / "questionnaire_ok.txt").read_text().strip() == "yes":
        call("PATCH", f"issues/{number}", {"state": "closed", "state_reason": "completed"})
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    raise SystemExit(post_reply() if "--reply" in sys.argv else start_search() if "--start-search" in sys.argv else main())
