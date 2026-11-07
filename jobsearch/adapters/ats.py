"""Official public ATS job-board APIs (documented, unauthenticated):
  Greenhouse Job Board API, Lever Postings API, Ashby Posting API, SmartRecruiters Posting API.
Boards to scan come from the watchlist (data/boards.json), which grows only from
real job links found during a search — ATS identifiers are never guessed."""
from __future__ import annotations

import re
import urllib.parse

from ..models import RawPosting
from ..text import html_to_text
from ..http import AccessBlocked
from .base import Adapter, SourceStatus

ATS_URL_PATTERNS = [
    ("greenhouse", re.compile(r"https?://(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/(?:embed/job_app\?for=)?([A-Za-z0-9_-]+)/jobs/(\d+)")),
    ("greenhouse", re.compile(r"https?://boards\.greenhouse\.io/embed/job_app\?(?:.*&)?for=([A-Za-z0-9_-]+)&token=(\d+)")),
    ("lever", re.compile(r"https?://jobs\.lever\.co/([A-Za-z0-9_.-]+)/([0-9a-f-]{36})")),
    ("ashby", re.compile(r"https?://jobs\.ashbyhq\.com/([^/?#]+)/([0-9a-f-]{36})")),
    ("smartrecruiters", re.compile(r"https?://jobs\.smartrecruiters\.com/([A-Za-z0-9_-]+)/(\d+)")),
]


BOARD_URL_PATTERNS = [  # a company's whole job page (no job id)
    ("greenhouse", re.compile(r"^https?://(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/([A-Za-z0-9_-]+)/?(?:[?#].*)?$")),
    ("lever", re.compile(r"^https?://jobs\.(?:eu\.)?lever\.co/([A-Za-z0-9_.-]+)/?(?:[?#].*)?$")),
    ("ashby", re.compile(r"^https?://jobs\.ashbyhq\.com/([^/?#]+)/?(?:[?#].*)?$")),
    ("smartrecruiters", re.compile(r"^https?://(?:jobs|careers)\.smartrecruiters\.com/([A-Za-z0-9_-]+)/?(?:[?#].*)?$")),
]

SUPPORTED_JOB_PAGES = ("job-boards.greenhouse.io/COMPANY, jobs.lever.co/COMPANY, "
                       "jobs.ashbyhq.com/COMPANY, jobs.smartrecruiters.com/COMPANY")


def parse_board_link(url: str) -> tuple[str, str] | None:
    """A company job-page link -> (ats, board). Also accepts a link to one job on that page."""
    u = (url or "").strip()
    for ats, pat in BOARD_URL_PATTERNS:
        m = pat.match(u)
        if m:
            return ats, urllib.parse.unquote(m.group(1))
    hit = parse_ats_url(u)
    if hit and hit[1]:
        return hit[0], hit[1]
    return None


def parse_ats_url(url: str) -> tuple[str, str, str] | None:
    for ats, pat in ATS_URL_PATTERNS:
        m = pat.search(url or "")
        if m:
            return ats, urllib.parse.unquote(m.group(1)), m.group(2)
    m = re.search(r"[?&]gh_jid=(\d+)", url or "")
    if m:
        return "greenhouse_custom", "", m.group(1)
    return None


# ---------------------------------------------------------------- Greenhouse
def gh_job(client, board: str, job_id: str, employer: str | None = None) -> RawPosting | None:
    r = client.get(f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs/{job_id}?pay_transparency=true")
    if r.status == 404:
        return None
    if r.status >= 400:
        raise RuntimeError(f"greenhouse HTTP {r.status}")
    j = r.json()
    return _gh_posting(j, board, employer or _gh_company(client, board))


_gh_names: dict[str, str] = {}


def _gh_company(client, board: str) -> str:
    if board not in _gh_names:
        try:
            r = client.get(f"https://boards-api.greenhouse.io/v1/boards/{board}")
            _gh_names[board] = r.json().get("name") or board if r.status == 200 else board
        except Exception:
            _gh_names[board] = board
    return _gh_names[board]


def _gh_posting(j: dict, board: str, employer: str) -> RawPosting:
    text = html_to_text(j.get("content"))
    bands = []
    for pr in j.get("pay_input_ranges") or []:
        try:
            bands.append({"min": pr["min_cents"] / 100, "max": pr["max_cents"] / 100, "interval": "year",
                          "currency": pr.get("currency_type"), "label": pr.get("title") or "",
                          "context": f"{pr.get('title') or 'Pay range'}: {pr.get('currency_type','')} {pr['min_cents']/100:,.0f}-{pr['max_cents']/100:,.0f}"})
        except (KeyError, TypeError):
            continue
    loc = (j.get("location") or {}).get("name", "")
    return RawPosting(
        source="greenhouse", source_url=j.get("absolute_url") or "", employer=employer, title=j.get("title", ""),
        description_text=text, location_text=loc, authoritative=True, ats="greenhouse", ats_board=board,
        ats_job_id=str(j.get("id")), requisition_id=j.get("requisition_id") or None,
        canonical_url=j.get("absolute_url"), apply_url=j.get("absolute_url"),
        published_date=j.get("first_published") or None, structured_salaries=bands,
        extra={"embed_apply_url": f"https://boards.greenhouse.io/embed/job_app?for={board}&token={j.get('id')}",
               "updated_at": j.get("updated_at")},
    )


# ---------------------------------------------------------------- Lever
def _lever_posting(j: dict, site: str, employer: str | None) -> RawPosting:
    parts = [j.get("descriptionPlain") or html_to_text(j.get("description"))]
    for lst in j.get("lists") or []:
        parts.append(f"{lst.get('text','')}:")
        parts.append(html_to_text(lst.get("content")))
    parts.append(j.get("additionalPlain") or html_to_text(j.get("additional")))
    sr = j.get("salaryRange") or None
    sal = None
    if sr and sr.get("min") is not None:
        interval = (sr.get("interval") or "").lower()
        sal = {"min": sr.get("min"), "max": sr.get("max"), "currency": sr.get("currency"),
               "interval": "year" if "year" in interval or "annual" in interval else interval or "unknown",
               "context": f"Lever salaryRange {sr.get('currency','')} {sr.get('min')}-{sr.get('max')} {sr.get('interval','')}"}
    cats = j.get("categories") or {}
    return RawPosting(
        source="lever", source_url=j.get("hostedUrl", ""), employer=employer or site, title=j.get("text", ""),
        description_text="\n".join(p for p in parts if p), location_text=cats.get("location", ""),
        authoritative=True, ats="lever", ats_board=site, ats_job_id=j.get("id"), canonical_url=j.get("hostedUrl"),
        apply_url=j.get("applyUrl"), workplace_type=(j.get("workplaceType") or None),
        structured_salary=sal, employment_type=cats.get("commitment"),
        published_date=None,  # Lever 'createdAt' is record creation, stored separately
        extra={"lever_created_at": j.get("createdAt"), "all_locations": cats.get("allLocations")},
    )


def lever_job(client, site: str, job_id: str, employer: str | None = None) -> RawPosting | None:
    r = client.get(f"https://api.lever.co/v0/postings/{site}/{job_id}")
    if r.status == 404:
        return None
    if r.status >= 400:
        raise RuntimeError(f"lever HTTP {r.status}")
    return _lever_posting(r.json(), site, employer)


# ---------------------------------------------------------------- Ashby
def _ashby_posting(j: dict, org: str, employer: str | None) -> RawPosting:
    bands = []
    comp = j.get("compensation") or {}
    for c in comp.get("summaryComponents") or []:
        if (c.get("compensationType") or "").lower() == "salary":
            iv = (c.get("interval") or "").upper()
            bands.append({"min": c.get("minValue"), "max": c.get("maxValue"), "currency": c.get("currencyCode"),
                          "interval": "year" if "YEAR" in iv else iv.lower() or "unknown",
                          "context": f"Ashby compensation: {comp.get('compensationTierSummary') or ''}".strip()})
    wt = j.get("workplaceType")
    if not wt and j.get("isRemote") is True:
        wt = "remote"
    locs = [j.get("location") or ""] + [s.get("location", "") for s in (j.get("secondaryLocations") or []) if isinstance(s, dict)]
    return RawPosting(
        source="ashby", source_url=j.get("jobUrl", ""), employer=employer or org, title=j.get("title", ""),
        description_text=j.get("descriptionPlain") or html_to_text(j.get("descriptionHtml")),
        location_text=" | ".join(l for l in locs if l), authoritative=True, ats="ashby", ats_board=org,
        ats_job_id=j.get("id"), canonical_url=j.get("jobUrl"), apply_url=j.get("applyUrl"),
        workplace_type=(wt.lower() if isinstance(wt, str) else None), structured_salaries=bands,
        employment_type=j.get("employmentType"), published_date=j.get("publishedAt"),
    )


def ashby_board(client, org: str) -> list[dict]:
    r = client.get(f"https://api.ashbyhq.com/posting-api/job-board/{urllib.parse.quote(org)}?includeCompensation=true")
    if r.status == 404:
        return []
    if r.status >= 400:
        raise RuntimeError(f"ashby HTTP {r.status}")
    return r.json().get("jobs") or []


def ashby_job(client, org: str, job_id: str, employer: str | None = None) -> RawPosting | None:
    for j in ashby_board(client, org):
        if j.get("id") == job_id:
            return _ashby_posting(j, org, employer)
    return None


# ---------------------------------------------------------------- SmartRecruiters
def sr_job(client, company: str, job_id: str, employer: str | None = None) -> RawPosting | None:
    r = client.get(f"https://api.smartrecruiters.com/v1/companies/{company}/postings/{job_id}")
    m = re.search(r"does not match companyIdentifier \(([A-Za-z0-9_-]+)\) from publication", r.body or "") if r.status == 400 else None
    if m:  # the official API reports the publication's own company identifier; use it (not guessed)
        company = m.group(1)
        r = client.get(f"https://api.smartrecruiters.com/v1/companies/{company}/postings/{job_id}")
    if r.status == 404:
        return None
    if r.status >= 400:
        raise RuntimeError(f"smartrecruiters HTTP {r.status}")
    j = r.json()
    if j.get("active") is False:
        return None
    sec = ((j.get("jobAd") or {}).get("sections") or {})
    parts = []
    for key, head in (("jobDescription", "Job Description"), ("qualifications", "Qualifications"), ("additionalInformation", "Additional Information")):
        if sec.get(key):
            parts.append(f"{sec[key].get('title') or head}:")
            parts.append(html_to_text(sec[key].get("text")))
    loc = j.get("location") or {}
    loc_text = ", ".join(x for x in [loc.get("city"), loc.get("region"), loc.get("country")] if x)
    if loc.get("remote"):
        loc_text = f"Remote ({loc_text})" if loc_text else "Remote"
    url = j.get("postingUrl") or f"https://jobs.smartrecruiters.com/{company}/{job_id}"
    return RawPosting(
        source="smartrecruiters", source_url=url, employer=(j.get("company") or {}).get("name") or employer or company,
        title=j.get("name", ""), description_text="\n".join(parts), location_text=loc_text, authoritative=True,
        ats="smartrecruiters", ats_board=company, ats_job_id=str(j.get("id")), requisition_id=j.get("refNumber"),
        canonical_url=url, apply_url=j.get("applyUrl") or url, workplace_type="remote" if loc.get("remote") else None,
        published_date=j.get("releasedDate"), employment_type=((j.get("typeOfEmployment") or {}).get("label")),
    )


FETCHERS = {"greenhouse": gh_job, "lever": lever_job, "ashby": ashby_job, "smartrecruiters": sr_job}


class AtsWatchlistAdapter(Adapter):
    """Scans every board in the watchlist and returns postings whose titles match."""
    name = "ats_watchlist"
    kind = "ats"
    authoritative = True

    def search(self, status: SourceStatus) -> list[RawPosting]:
        boards: dict = {k: list(v) for k, v in self.ctx.get("boards", {}).items()}
        from ..agencies import is_agency
        for b in self.ctx.get("boards_added", []):  # boards found by the web search earlier in this run
            if is_agency(b["board"]):
                continue   # recruiting agencies are never read as company careers pages
            lst = boards.setdefault(b["ats"], [])
            if not any(x["board"] == b["board"] for x in lst):
                lst.append({"board": b["board"], "employer": b.get("employer")})
        matcher = self.ctx["title_matcher"]
        out: list[RawPosting] = []
        per = {}
        for ats, entries in boards.items():
            for entry in entries:
                slug, employer = entry["board"], entry.get("employer")
                key = f"{ats}:{slug}"
                try:
                    out.extend(self._scan(ats, slug, employer, matcher))
                    per[key] = "ok"
                except AccessBlocked as e:
                    per[key] = f"blocked: {e}"
                    status.boards_failed.append(f"{ats}:{slug.lower()}")
                except Exception as e:  # noqa: BLE001
                    per[key] = f"error: {type(e).__name__}: {e}"
                    status.errors.append(f"{key}: {e}")
                    status.boards_failed.append(f"{ats}:{slug.lower()}")
        status.detail = f"{len(per)} boards scanned; " + ", ".join(f"{k}={v}" for k, v in list(per.items())[:20])
        if per and all(v.startswith("blocked") for v in per.values()):
            status.state = "blocked"
        return out

    def _scan(self, ats, slug, employer, matcher) -> list[RawPosting]:
        c = self.client
        res = []
        if ats == "greenhouse":
            r = c.get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true")
            if r.status >= 400:
                raise RuntimeError(f"HTTP {r.status}")
            emp = employer or _gh_company(c, slug)
            for j in r.json().get("jobs", []):
                if matcher.match(j.get("title", ""), html_to_text(j.get("content"))):
                    p = gh_job(c, slug, str(j["id"]), emp)  # detail call for pay ranges / requisition id
                    if p:
                        res.append(p)
        elif ats == "lever":
            skip = 0
            while True:  # documented limit/skip pagination keeps large boards bounded
                r = c.get(f"https://api.lever.co/v0/postings/{slug}?mode=json&limit=100&skip={skip}")
                if r.status >= 400:
                    raise RuntimeError(f"HTTP {r.status}")
                page = r.json()
                for j in page:
                    p = _lever_posting(j, slug, employer)
                    if matcher.match(p.title, p.description_text):
                        res.append(p)
                if len(page) < 100 or skip >= 5000:
                    break
                skip += 100
        elif ats == "ashby":
            for j in ashby_board(c, slug):
                p = _ashby_posting(j, slug, employer)
                if matcher.match(p.title, p.description_text):
                    res.append(p)
        elif ats == "smartrecruiters":
            r = c.get(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100")
            if r.status >= 400:
                raise RuntimeError(f"HTTP {r.status}")
            for j in r.json().get("content", []):
                if matcher.match_title(j.get("name", "")):  # title pre-screen; description checked after detail
                    p = sr_job(c, slug, str(j["id"]), employer)
                    if p and matcher.match(p.title, p.description_text):
                        res.append(p)
        return res
