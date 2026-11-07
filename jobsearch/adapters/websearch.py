"""Web search for job postings outside the job sites this tool reads directly.

Optional: runs only when a web search key is set (TAVILY_API_KEY). For each of your job titles it runs
two searches:
  1. Careers pages: postings on the systems companies use for their own careers pages
     (Greenhouse, Lever, Ashby, SmartRecruiters, Workday).
  2. The wider web: any other page that looks like a job posting for that title.
Each result is then handled like any other job: the tool opens the link, uses the employer's own posting
when it can find it, applies your rules, and checks the link. Companies found on Greenhouse, Lever, Ashby or
SmartRecruiters are remembered and their whole careers page is read in later searches.

The search service is pluggable: add a class with the same `search` method to PROVIDERS to use another one.
"""
from __future__ import annotations

import os
import re

from ..models import RawPosting
from .base import Adapter, SourceStatus
from .ats import parse_board_link
from ..agencies import is_agency

CAREERS_DOMAINS = ["job-boards.greenhouse.io", "boards.greenhouse.io", "jobs.lever.co", "jobs.ashbyhq.com",
                   "jobs.smartrecruiters.com", "myworkdayjobs.com"]
# Sites left out of the wider-web search, in groups (each group is labeled in the report).
EXCLUDE_GROUPS = [
    ("Sites this tool never opens (sign-in required, automated access blocked, or not allowed by their terms)",
     ["linkedin.com", "indeed.com", "glassdoor.com", "ziprecruiter.com", "builtin.com", "idealist.org",
      "joinhandshake.com", "flexjobs.com", "virtualvocations.com", "diversityjobs.com", "techjobsforgood.com",
      "greenjobsboard.us", "remote100k.com", "conservationjobboard.com", "higheredjobs.com"]),
    ("Job sites this tool already reads directly",
     ["himalayas.app", "remoteok.com", "remotive.com", "themuse.com", "jobicy.com", "weworkremotely.com",
      "news.ycombinator.com"]),
    ("Other job sites (skipped because the web search is only for jobs on employers' own websites)",
     ["monster.com", "careerbuilder.com", "simplyhired.com", "simplyhired.co", "handshake.com", "wellfound.com",
      "dice.com", "hiring.cafe", "workingnomads.com", "jobspresso.co", "adzuna.com", "jooble.org", "jobgether.com",
      "talent.com", "lensa.com", "careerjet.com", "jobleads.com", "salary.com", "theladders.com", "roberthalf.com",
      "edtech.com", "recruit.net", "jobright.ai", "learn4good.com", "remote.co", "jobs.now", "remoterocketship.com",
      "dailyremote.com", "cutshort.io"]),
    ("Social media", ["facebook.com", "instagram.com", "x.com", "twitter.com", "reddit.com", "youtube.com"]),
]
EXCLUDE_DOMAINS = [d for _, group in EXCLUDE_GROUPS for d in group]

# A page title like "Remote Program Manager Jobs in Maryland" or "Search Jobs" is a list of jobs, not one job.
LIST_PAGE = re.compile(r"(?i)(\bjobs\b(?! at\b)|\bsearch\b|\bresults for\b|\bopenings\b|^\s*careers?\s*$)")
LIST_URL = re.compile(r"(?i)(/search\b|[?&](q|query|keywords?)=|/q-|-jobs?(\.html|/?$)|/jobs/?$|/careers/?$)")


def _is_list_page(url: str, title: str) -> bool:
    """True for search-result and category pages; one job's own page passes."""
    return bool(LIST_URL.search(url) or LIST_PAGE.search(re.sub(r"(?i)myworkdayjobs\.com", "", title or "")))


def _excluded(url: str) -> bool:
    """Never keep results on sites this tool does not open or already reads directly."""
    from urllib.parse import urlsplit
    host = urlsplit(url).netloc.lower().split(":")[0]
    return any(host == d or host.endswith("." + d) for d in EXCLUDE_DOMAINS)


class TavilySearch:
    """https://docs.tavily.com/documentation/api-reference/endpoint/search — 1 credit per basic search."""
    key_env = "TAVILY_API_KEY"

    def __init__(self, client):
        self.client = client
        self.key = os.environ.get(self.key_env, "").strip()

    def search(self, query: str, include_domains=None, exclude_domains=None, max_results: int = 20,
               time_range: str = "month") -> list[dict]:
        body = {"query": query, "search_depth": "basic", "max_results": max_results, "topic": "general",
                "time_range": time_range}
        if include_domains:
            body["include_domains"] = list(include_domains)
        if exclude_domains:
            body["exclude_domains"] = list(exclude_domains)
        r = self.client.post_json("https://api.tavily.com/search", body, headers={"Authorization": f"Bearer {self.key}"})
        if r.status in (401, 403):
            raise RuntimeError("the web search key was not accepted. Check the TAVILY_API_KEY secret")
        if r.status == 429 or r.status == 432 or r.status == 433:
            raise RuntimeError("the web search service's monthly free allowance is used up")
        if r.status >= 400:
            raise RuntimeError(f"web search failed (HTTP {r.status})")
        return [{"url": x.get("url", ""), "title": x.get("title", ""), "content": x.get("content", "")}
                for x in (r.json() or {}).get("results") or []]


PROVIDERS = {"tavily": TavilySearch}


def _clean_title(title: str, url: str) -> tuple[str, str]:
    """Best guess at (job title, employer) from a search result's page title."""
    t = re.sub(r"\s+", " ", title or "").strip()
    t = re.sub(r"(?i)\s*[-|]\s*myworkdayjobs\.com\s*$", "", t)          # Workday adds " - Myworkdayjobs.com"
    t = re.sub(r"^[^|]{1,40} careers \| ", "", t, flags=re.I)            # "Stripe Careers | Title"
    m = re.match(r"(?i)job application for (.+?) at (.+)$", t)          # Greenhouse
    if m:
        return m.group(1).strip(), m.group(2).strip()
    hit = parse_board_link(url)
    if hit and "lever.co" in url:                                        # Lever: "Company - Title"
        company, _, job = t.partition(" - ")
        if job:
            return job.strip(), company.strip()
    m = re.match(r"(.+?) (?:@|at) (.+?)(?: \| .*)?$", t)                 # "Title @ Company" / "Title at Company"
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return t, ""


class WebSearchAdapter(Adapter):
    name = "websearch"
    env_keys = ("TAVILY_API_KEY",)

    def search(self, status: SourceStatus) -> list[RawPosting]:
        from ..criteria import CRITERIA
        provider = PROVIDERS[os.environ.get("WEB_SEARCH_PROVIDER", "tavily").strip().lower() or "tavily"](self.client)
        matcher = self.ctx.get("title_matcher")
        remote = CRITERIA.get("remote_only", True)
        where = "remote" if remote else (CRITERIA.get("home_state") or "")
        titles = list(dict.fromkeys(self.ctx.get("queries", [])))   # every job title (search word)
        out, seen, searches = [], set(), 0
        terms = [f'"{t}"' for t in titles]
        kw = self.ctx.get("skill_keywords") or []
        if kw:   # skills-based matching: also search for the required keywords
            terms.append((" OR " if self.ctx.get("skill_rule") == "any" else " ").join(f'"{k}"' for k in kw))
        for term in terms:
            plan = [
                (f'{term} {where}'.strip(), CAREERS_DOMAINS, None, 20, "month"),
                (f'{term} job opening apply {where}'.strip(), None, EXCLUDE_DOMAINS, 20, "week"),
            ]
            for query, inc, exc, n, rng in plan:
                log = {"words": query, "where": "company careers pages" if inc else "the wider web",
                       "period": {"week": "last 7 days", "month": "last 30 days"}.get(rng, rng), "max_results": n}
                status.searches.append(log)
                try:
                    results = provider.search(query, include_domains=inc, exclude_domains=exc, max_results=n, time_range=rng)
                    searches += 1
                    log["results"] = len(results)
                except RuntimeError as e:
                    log["results"] = f"failed: {e}"
                    status.errors.append(str(e))
                    status.detail = f"{searches} searches; stopped: {e}"
                    if not out:
                        status.state = "error"   # report shows "No: error" instead of "Yes"
                    return out
                kept_before = len(out)
                for x in results:
                    url = x["url"]
                    if not url or url in seen or _excluded(url):
                        continue
                    if inc is None and _is_list_page(url, x.get("title", "")):
                        continue   # wider-web result that lists many jobs: not one posting
                    seen.add(url)
                    title, employer = _clean_title(x["title"], url)
                    p = RawPosting(source="websearch", source_url=url, employer=employer, title=title,
                                   description_text=x.get("content", ""), apply_url=url,
                                   extra={"found_via": f"web search: {query}"})
                    hit = parse_board_link(url)
                    if hit and not is_agency(hit[1]) and matcher and matcher.match(title, p.description_text):
                        # a matching job on a supported careers page: read that company's whole page this search too
                        self.ctx.setdefault("boards_added", []).append({"ats": hit[0], "board": hit[1], "employer": employer or None})
                    out.append(p)
                    log.setdefault("urls", []).append(url)
                log["kept"] = len(out) - kept_before
        status.detail = f"{searches} searches ({searches} of the free monthly allowance used)"
        return out
