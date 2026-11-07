"""Popular job boards this tool may not search automatically (no public data feed, sign-in required,
blocked to automated requests, or terms that forbid automated access). The results page lists one-click
search links for them, built from your job titles, so you can open each search yourself."""
from __future__ import annotations

import json
import re
import urllib.parse

q = urllib.parse.quote_plus


def _slug(t: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", t.lower()).strip("-")


def _where(r: bool, s: str, remote_word: str = "Remote") -> str:
    """Location text for boards that take one: "Remote" when remote-only is on, else the user's state."""
    return remote_word if r else s


# Each link builder takes (title, remote_only, state). Grouped so the results page stays readable.
GROUPS = [
    ("Large job sites", [
        ("LinkedIn", "largest professional network",
         lambda t, r, s: f"https://www.linkedin.com/jobs/search/?keywords={q(t)}" + ("&f_WT=2" if r else (f"&location={q(s)}" if s else ""))),
        ("Indeed", "largest job search site",
         lambda t, r, s: f"https://www.indeed.com/jobs?q={q(t)}" + (f"&l={q(_where(r, s))}" if _where(r, s) else "")),
        ("Google Jobs", "jobs collected from many sites",
         lambda t, r, s: f"https://www.google.com/search?q={q(' '.join(x for x in (t, _where(r, s, 'remote'), 'jobs') if x))}&ibp=htl;jobs"),
        ("Glassdoor", "jobs plus company reviews and salaries",
         lambda t, r, s: f"https://www.glassdoor.com/Job/jobs.htm?sc.keyword={q(t)}"),
        ("ZipRecruiter", "large job search site",
         lambda t, r, s: f"https://www.ziprecruiter.com/jobs-search?search={q(t)}" + (f"&location={q(_where(r, s))}" if _where(r, s) else "")),
        ("Monster", "large job search site",
         lambda t, r, s: f"https://www.monster.com/jobs/search?q={q(t)}" + (f"&where={q(_where(r, s, 'remote'))}" if _where(r, s) else "")),
        ("CareerBuilder", "large job search site",
         lambda t, r, s: f"https://www.careerbuilder.com/jobs?keywords={q(t)}" + (f"&location={q(_where(r, s, 'remote'))}" if _where(r, s) else "")),
        ("SimplyHired", "jobs collected from many sites",
         lambda t, r, s: f"https://www.simplyhired.com/search?q={q(t)}" + (f"&l={q(_where(r, s, 'remote'))}" if _where(r, s) else "")),
        ("HiringCafe", "jobs collected from company career pages",
         lambda t, r, s: "https://hiring.cafe/?searchState=" + q(json.dumps({"searchQuery": t}))),
        ("Ladders", "jobs paying $100k or more",
         lambda t, r, s: f"https://www.theladders.com/jobs/search-jobs?keywords={q(t)}"),
        ("Remote100k", "remote jobs paying $100k or more (no keyword search: opens its US remote jobs list)",
         lambda t, r, s: "https://remote100k.com/remote-jobs/usa"),
    ]),
    ("Students, early career, hourly and government", [
        ("Handshake", "jobs for students and recent graduates (sign in with your school account)",
         lambda t, r, s: f"https://app.joinhandshake.com/stu/postings?query={q(t)}"),
        ("CareerOneStop Job Finder", "US Department of Labor search, including state job banks",
         lambda t, r, s: f"https://www.careeronestop.org/Toolkit/Jobs/find-jobs-results.aspx?keyword={q(t)}"
                         f"&location={q(s or 'United States')}&radius=25"),
        ("USAJOBS", "official US federal government jobs",
         lambda t, r, s: f"https://www.usajobs.gov/search/results/?k={q(t)}" + (f"&l={q(s)}" if (s and not r) else "")),
        ("Snagajob", "hourly and part-time jobs",
         lambda t, r, s: f"https://www.snagajob.com/search?q={q(t)}"),
        ("Upwork", "freelance and contract work",
         lambda t, r, s: f"https://www.upwork.com/nx/search/jobs/?q={q(t)}"),
        ("AmeriCorps", "paid national service positions (no keyword search: opens the starting page)",
         lambda t, r, s: "https://americorps.gov/join"),
    ]),
    ("Nonprofit, public service and international", [
        ("Idealist", "nonprofit and social-impact jobs",
         lambda t, r, s: f"https://www.idealist.org/en/jobs?q={q(t)}"),
        ("Chronicle of Philanthropy Jobs", "nonprofit and fundraising jobs",
         lambda t, r, s: f"https://jobs.philanthropy.com/searchjobs/?Keywords={q(t)}"),
        ("ReliefWeb", "humanitarian and international development jobs (run by the United Nations)",
         lambda t, r, s: f"https://reliefweb.int/jobs?search={q(t)}"),
        ("Devex", "international development jobs",
         lambda t, r, s: f"https://www.devex.com/jobs/search?query={q(t)}"),
        ("Green Jobs Board", "climate and environmental jobs",
         lambda t, r, s: f"https://www.greenjobsboard.us/jobs?search={q(t)}"),
        ("HigherEdJobs", "jobs at colleges and universities",
         lambda t, r, s: f"https://www.higheredjobs.com/search/advanced_action.cfm?Keyword={q(t)}"),
    ]),
    ("Diversity and inclusion", [
        ("DiversityJobs", "jobs at employers focused on diversity and inclusion",
         lambda t, r, s: f"https://diversityjobs.com/candidate/job_search/quick/results?keyword={q(t)}"
                         + (f"&location={q(_where(r, s, 'remote'))}" if _where(r, s) else "")),
        ("PowerToFly", "jobs at employers recruiting women and underrepresented groups",
         lambda t, r, s: f"https://powertofly.com/jobs/?keywords={q(t)}"),
        ("Talentally (Professional Diversity Network)", "diversity-focused job network",
         lambda t, r, s: f"https://talentally.com/jobs?keywords={q(t)}"),
        ("Hire Heroes USA", "jobs for veterans and military spouses (no keyword search: opens the job board)",
         lambda t, r, s: "https://jobs.hireheroesusa.org/"),
    ]),
    ("Tech and startups", [
        ("Tech Jobs for Good", "tech jobs at mission-driven organizations",
         lambda t, r, s: f"https://techjobsforgood.com/jobs/?q={q(t)}"),
        ("Wellfound", "startup jobs",
         lambda t, r, s: f"https://wellfound.com/role/r/{_slug(t)}"),
        ("Welcome to the Jungle", "jobs at growing companies (formerly Otta)",
         lambda t, r, s: f"https://www.welcometothejungle.com/en/jobs?query={q(t)}"),
        ("Built In", "tech and startup jobs",
         lambda t, r, s: f"https://builtin.com/jobs?search={q(t)}"),
        ("Dice", "technology jobs",
         lambda t, r, s: f"https://www.dice.com/jobs?q={q(t)}"),
    ]),
]

BOARDS = [b for _, boards in GROUPS for b in boards]


def section(titles: list[str], remote_only: bool, state: str = "") -> list[str]:
    """Markdown lines: one table per group, one row per board, one search link for each of your job titles."""
    titles = [t for t in dict.fromkeys(t.strip() for t in titles) if t]
    L = ["## Popular job boards to search yourself", "",
         "These boards cannot be searched by this tool (no public data feed, sign-in required, blocked to "
         "automated requests, or their terms do not allow automated searching). Each link opens that board's "
         "search for one of your job titles. Open them yourself, as often as you like.", ""]
    one = len(titles) == 1
    if not titles:
        return L + ["Add a job title (setup questionnaire) to get links here.", ""]
    head = "| Job board | What it is | " + ("Search" if one else "Search for each of your job titles") + " |"
    label = lambda t: t.replace("|", "/").replace("[", "(").replace("]", ")")
    for group, boards in GROUPS:
        L += [f"### {group}", "", head, "|---|---|---|"]
        for name, what, link in boards:
            L.append(f"| {name} | {what} | " + " · ".join(f"[{label(t)}]({link(t, remote_only, state)})" for t in titles) + " |")
        L.append("")
    return L
