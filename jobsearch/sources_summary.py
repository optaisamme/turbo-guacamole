"""'Where this search looked': every job site and source used in a run, and the exact web search
(Tavily) criteria. Built from the run record saved in data/jobs.sqlite."""
from __future__ import annotations

SITES = [  # (source name in the run record, name shown, what it searches)
    ("websearch", "Web search (Tavily)", "the whole web for each job title (criteria below)"),
    ("ats_watchlist", "Company careers pages", "every open job at the companies listed below "
                                               "(Greenhouse, Lever, Ashby, SmartRecruiters)"),
    ("remotive", "Remotive", "remote jobs, searched by your job titles"),
    ("remoteok", "Remote OK", "its newest remote jobs (about 100: all its feed gives)"),
    ("himalayas", "Himalayas", "remote jobs: {limit}"),
    ("themuse", "The Muse", "{limit}"),
    ("jobicy", "Jobicy", "{limit}"),
    ("weworkremotely", "We Work Remotely", "its newest 100 remote jobs, all categories (all its feed gives)"),
    ("hackernews", "Hacker News: Who is hiring?", "this month's hiring thread (mostly tech jobs)"),
]

# shown when a search did not record how many jobs it read (searches before 2026-11-07)
DEFAULT_LIMITS = {"himalayas": "how many it read was not recorded for this search",
                  "themuse": "its newest remote jobs, all fields (how many it read was not recorded for this search)",
                  "jobicy": "its US remote jobs (how many it read was not recorded for this search)"}
SIZE_SETTING = "the setup questionnaire (\"How many jobs to look through\")"


REMOVED = {"adzuna", "usajobs", "jooble", "reliefweb", "web_search_leads", "workingnomads", "jobspresso"}   # sources this tool no longer uses


def _worked(s: dict) -> str:
    st = s.get("state")
    return {"ok": "Yes", "needs_credentials": "Off: no key", "blocked": "No: the site blocked the request",
            "not_run": "Not run"}.get(st, "No: error")


def _skills(s: dict) -> str:
    """Skills-based matches; searches before 2026-11-05 did not record them."""
    return str(s["skills_matched"]) if "skills_matched" in s else "not recorded"


def _cell(x) -> str:
    return str(x if x is not None else "").replace("|", "/").replace("\n", " ")


def _minutes(started_at, finished_at):
    from datetime import datetime
    try:
        secs = (datetime.fromisoformat(finished_at) - datetime.fromisoformat(started_at)).total_seconds()
    except (TypeError, ValueError):
        return None
    return max(1, round(secs / 60))


def section(sources: list[dict], started_at: str | None = None, finished_at: str | None = None) -> list[str]:
    by = {s.get("name"): s for s in sources or []}
    companies = (by.get("ats_watchlist") or {}).get("companies")
    L = ["## Where this search looked", "",
         "Every job site and source used in the last search, and how many listings each gave.", ""]
    mins = _minutes(started_at, finished_at)
    L += ["**Why only part of some sites is read:** some sites send a set number of jobs (Remote OK about 100, "
          "We Work Remotely 100). For Himalayas, The Muse and Jobicy, this tool reads up to the numbers set in "
          f"{SIZE_SETTING}. Reading more makes each search take longer"
          + (f": this search took about {mins} minute{'s' if mins != 1 else ''}" if mins else "")
          + ". GitHub stops a search after 60 minutes.", ""]
    L += [
         "| Job site | What it searches | Searched this time? | Listings checked | Matched your job titles | Matched your skills |",
         "|---|---|---|---|---|---|"]
    known = [n for n, _, _ in SITES]
    for name, shown, what in SITES:
        s = by.get(name)
        if s is None:
            continue
        what = what.format(limit=s.get("limit") or DEFAULT_LIMITS.get(name, ""))
        L.append(f"| {shown} | {what} | {_worked(s)} | {s.get('fetched', 0)} | {s.get('matched', 0)} | {_skills(s)} |")
    for name, s in by.items():   # any source not described above (removed sources are not shown)
        if name not in known and name not in REMOVED:
            L.append(f"| {_cell(name)} |  | {_worked(s)} | {s.get('fetched', 0)} | {s.get('matched', 0)} | {_skills(s)} |")
    if companies is not None:
        L += ["", f"### Company careers pages read ({len(companies)})", "",
              "How a company gets on this list: it is added when a job matching your job titles or skills is found on its "
              "own careers page (Greenhouse, Lever, Ashby or SmartRecruiters), through a job site or the web search. "
              "Its whole careers page is then read every search. It is dropped after more than 5 searches in a row "
              "with no matching job. Staffing agencies on this tool's agency list, and employers you skip, are never added. Companies you add in the questionnaire are never dropped.",
              "", (", ".join(_cell(c) for c in sorted(companies, key=str.lower)) if companies else "None yet") + "."]
    elif "ats_watchlist" in by:
        L += ["", "### Company careers pages read", "", "Not recorded for this search (searches from 2026-11-05 on list them)."]
    L += ["", "### Web search (Tavily) criteria", ""]
    ws = by.get("websearch")
    if not ws:
        L.append("The web search is not part of this search.")
    elif ws.get("state") == "needs_credentials":
        L.append("Off this time: no Tavily key is set, so no web search was run.")
    else:
        from .adapters.websearch import CAREERS_DOMAINS, EXCLUDE_GROUPS
        rows = ws.get("searches") or []
        if rows:
            L += ["| # | Search words | Where | Pages dated within | Links returned | Matches | Other postings (not a full match) |",
                  "|---|---|---|---|---|---|---|"]
            old = False
            for i, r in enumerate(rows, 1):
                if "kept" in r and "matches" not in r:
                    old = True
                matches = r.get("matches", "not recorded" if "kept" in r else "")
                others = r.get("others", "not recorded" if "kept" in r else "")
                L.append(f"| {i} | {_cell(r.get('words'))} | {_cell(r.get('where'))} | {_cell(r.get('period'))} | "
                         f"{_cell(r.get('results', ''))} | {matches} | {others} |")
            L += ["", "1. **Pages dated within:** Tavily sends back only web pages it dates as published or updated "
                  "in that period. Pages it cannot date are sent back too.",
                  "2. **Links returned:** each search sends back a list of links, one per web page, up to 20 "
                  "(the most Tavily allows). Each search uses 1 of the 1,000 free monthly searches. Some links "
                  "are job postings. The rest are pages listing many jobs, sites this tool already reads, or "
                  "links an earlier search in the table already found.",
                  "3. **Matches:** job postings from those links that match all your rules.",
                  "4. **Other postings (not a full match):** job postings that had a different job title, did not "
                  "meet a rule, or still need their link checked."]
            if old:
                L.append("5. Searches before 2026-11-07 did not record Matches or Other postings.")
        else:
            L.append(_cell(ws.get("detail")) or "No searches were recorded.")
        L += ["", "Company careers pages searched: " + ", ".join(CAREERS_DOMAINS) + ".", "",
              "Left out of the wider-web search:", ""]
        L += [f"{n}. **{label}:** " + ", ".join(group) + "." for n, (label, group) in enumerate(EXCLUDE_GROUPS, 1)]
    return L + [""]


SOURCE_LABELS = {"greenhouse": "Company careers page (Greenhouse)", "lever": "Company careers page (Lever)",
                 "ashby": "Company careers page (Ashby)", "smartrecruiters": "Company careers page (SmartRecruiters)"}
HOST_LABELS = {"himalayas.app": "Himalayas", "remoteok.com": "Remote OK", "remotive.com": "Remotive",
               "themuse.com": "The Muse", "jobicy.com": "Jobicy", "weworkremotely.com": "We Work Remotely",
               "news.ycombinator.com": "Hacker News: Who is hiring?"}


def found_by(row) -> str:
    """The job site(s) or search that found a job, from its saved sources and listing links."""
    import json
    from urllib.parse import urlsplit
    site_names = {name: shown for name, shown, _ in SITES}

    def load(key):
        try:
            return json.loads(row[key] or "[]")
        except (KeyError, IndexError, TypeError, ValueError):
            return []

    names = []
    for s in load("sources"):
        label = SOURCE_LABELS.get(s) or site_names.get(s)
        if label and label not in names:
            names.append(label)
    for url in load("source_urls"):
        host = urlsplit(url or "").netloc.lower().removeprefix("www.")
        label = HOST_LABELS.get(host)
        if label and label not in names:
            names.append(label)
    if not names and "employer_page" in load("sources"):
        names.append("Employer's website (reached from another listing)")
    return ", ".join(names) or "not recorded"
