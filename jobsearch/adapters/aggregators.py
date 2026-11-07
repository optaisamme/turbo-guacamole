"""Discovery sources with documented public/free APIs. Their content is third-party
(authoritative=False).
Results must be confirmed on the employer/ATS posting before they can qualify."""
from __future__ import annotations

import os
import urllib.parse

from ..models import RawPosting
from ..text import html_to_text
from .base import Adapter, SourceStatus


SEARCH_SIZE_DEFAULTS = {"newest_jobs_per_site": 1000, "jobs_per_search_word": 200, "jobicy_max_jobs": 5000}
SEARCH_SIZE_LIMITS = {"newest_jobs_per_site": (20, 5000), "jobs_per_search_word": (20, 1000), "jobicy_max_jobs": (100, 10000)}


def search_size(ctx: dict) -> dict:
    """How many jobs to read from sites that offer more than they send at once (settings: search_size)."""
    out = dict(SEARCH_SIZE_DEFAULTS)
    for k, v in (ctx.get("search_size") or {}).items():
        if k in out:
            try:
                lo, hi = SEARCH_SIZE_LIMITS[k]
                out[k] = max(lo, min(hi, int(v)))
            except (TypeError, ValueError):
                pass
    return out


def _pages(n: int, per_page: int) -> int:
    return max(1, -(-n // per_page))


def search_words(ctx: dict) -> list[str]:
    """All your job titles, plus the skills-matching keywords when that is on."""
    words = list(dict.fromkeys(ctx.get("queries", [])))
    if ctx.get("skill_words") and ctx["skill_words"] not in words:
        words.append(ctx["skill_words"])
    return words


class RemotiveAdapter(Adapter):
    # https://remotive.com/api-documentation — please link back to Remotive; low request volume.
    name = "remotive"

    def search(self, status: SourceStatus) -> list[RawPosting]:
        out, seen = [], set()
        for q in search_words(self.ctx):
            r = self.client.get("https://remotive.com/api/remote-jobs?" + urllib.parse.urlencode({"search": q, "limit": 100}))
            if r.status >= 400:
                status.errors.append(f"query {q!r}: HTTP {r.status}")
                continue
            for j in r.json().get("jobs", []):
                if j.get("id") in seen:
                    continue
                seen.add(j.get("id"))
                out.append(RawPosting(
                    source="remotive", source_url=j.get("url", ""), employer=j.get("company_name", ""), title=j.get("title", ""),
                    description_text=html_to_text(j.get("description")) + (f"\nSalary: {j.get('salary')}" if j.get("salary") else ""),
                    location_text=j.get("candidate_required_location", ""), apply_url=j.get("url"),
                    workplace_type="remote", employment_type=j.get("job_type"),
                    extra={"third_party_salary": j.get("salary"), "third_party_published": j.get("publication_date")},
                ))
        return out


class RemoteOKAdapter(Adapter):
    # https://remoteok.com/api — terms require attribution/link back to Remote OK.
    name = "remoteok"

    def search(self, status: SourceStatus) -> list[RawPosting]:
        r = self.client.get("https://remoteok.com/api")
        if r.status >= 400:
            raise RuntimeError(f"HTTP {r.status}")
        out = []
        for j in r.json()[1:]:
            if not isinstance(j, dict):
                continue
            out.append(RawPosting(
                source="remoteok", source_url=j.get("url", ""), employer=j.get("company", ""), title=j.get("position", ""),
                description_text=html_to_text(j.get("description")), location_text=j.get("location", ""),
                workplace_type="remote", apply_url=j.get("apply_url"),
                extra={"third_party_salary": [j.get("salary_min"), j.get("salary_max")], "third_party_published": j.get("date")},
            ))
        return out


class HimalayasAdapter(Adapter):
    # https://himalayas.app/api — search returns 20 per page (?page=N); the browse feed pages by ?cursor=.
    name = "himalayas"
    # Pages (20 jobs each) come from settings: search_size.jobs_per_search_word and newest_jobs_per_site.

    def _posting(self, j: dict) -> RawPosting:
        return RawPosting(
            source="himalayas", source_url=j.get("guid") or "", employer=j.get("companyName", ""), title=j.get("title", ""),
            description_text=html_to_text(j.get("description")),
            location_text="; ".join(j.get("locationRestrictions") or []), workplace_type="remote",
            apply_url=j.get("applicationLink"), employment_type=j.get("employmentType"),
            structured_salaries=([{"min": j.get("minSalary"), "max": j.get("maxSalary"), "interval": "year",
                                   "context": f"Himalayas listing salary {j.get('currency') or ''} {j.get('minSalary')}-{j.get('maxSalary')}"}]
                                 if j.get("minSalary") and j.get("maxSalary") and (j.get("currency") or "USD") == "USD"
                                 and (j.get("salaryPeriod") or "annual") == "annual" else []),
            extra={"third_party_salary": [j.get("minSalary"), j.get("maxSalary")], "third_party_published": j.get("pubDate"),
                   "expiry": j.get("expiryDate")},
        )

    def search(self, status: SourceStatus) -> list[RawPosting]:
        out, seen = [], set()

        def add(jobs):
            for j in jobs:
                gid = j.get("guid") or j.get("applicationLink")
                if gid and gid not in seen:
                    seen.add(gid)
                    out.append(self._posting(j))

        size = search_size(self.ctx)
        search_pages, browse_pages = _pages(size["jobs_per_search_word"], 20), _pages(size["newest_jobs_per_site"], 20)
        status.limit = f"up to {search_pages * 20:,} jobs for each search word, plus its {browse_pages * 20:,} newest jobs"
        for q in search_words(self.ctx):
            for page in range(1, search_pages + 1):
                r = self.client.get("https://himalayas.app/jobs/api/search?" + urllib.parse.urlencode({"q": q, "country": "US", "page": page}))
                if r.status >= 400:
                    status.errors.append(f"query {q!r} page {page}: HTTP {r.status}")
                    break
                jobs = r.json().get("jobs", [])
                add(jobs)
                if len(jobs) < 20:
                    break
        cursor = None
        for _ in range(browse_pages):
            r = self.client.get("https://himalayas.app/jobs/api?" + urllib.parse.urlencode({"limit": 20, **({"cursor": cursor} if cursor else {})}))
            if r.status >= 400:
                status.errors.append(f"newest jobs feed: HTTP {r.status}")
                break
            d = r.json()
            add(d.get("jobs", []))
            cursor = d.get("nextCursor")
            if not cursor:
                break
        return out


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


class TheMuseAdapter(Adapter):
    # https://www.themuse.com/developers/api/v2 — public jobs API, all job types; no key needed at low volume.
    # Not searchable by keyword, so the newest jobs are collected and matched to your titles here.
    name = "themuse"

    def search(self, status: SourceStatus) -> list[RawPosting]:
        from ..criteria import CRITERIA
        remote = CRITERIA.get("remote_only", True)
        out, seen = [], set()
        pages = _pages(search_size(self.ctx)["newest_jobs_per_site"], 20)   # 20 jobs per page
        status.limit = f"its {pages * 20:,} newest remote jobs, all fields"
        for page in range(1, pages + 1):
            params = [("page", page)] + ([("location", "Flexible / Remote")] if remote else [])
            r = self.client.get("https://www.themuse.com/api/public/jobs?" + urllib.parse.urlencode(params))
            if r.status >= 400:
                status.errors.append(f"page {page}: HTTP {r.status}")
                break
            d = r.json()
            for j in d.get("results", []):
                if j.get("id") in seen:
                    continue
                seen.add(j.get("id"))
                locs = [l.get("name", "") for l in j.get("locations") or []]
                url = (j.get("refs") or {}).get("landing_page", "")
                out.append(RawPosting(
                    source="themuse", source_url=url, employer=(j.get("company") or {}).get("name", ""), title=j.get("name", ""),
                    description_text=html_to_text(j.get("contents")), location_text="; ".join(locs), apply_url=url,
                    workplace_type="remote" if locs and all(l == "Flexible / Remote" for l in locs) else None,
                    extra={"third_party_published": j.get("publication_date")},
                ))
            if page >= (d.get("page_count") or 0):
                break
        return out


class JobicyAdapter(Adapter):
    # https://jobicy.com/jobs-rss-feed — free remote-jobs API. Jobicy asks to be credited with a link
    # and for apply buttons to go to the original job URL (results link to each Jobicy job page).
    name = "jobicy"


    def search(self, status: SourceStatus) -> list[RawPosting]:
        """Every US remote job Jobicy lists, 100 per page, following its page cursor."""
        out, seen, cursor = [], set(), None
        pages = _pages(search_size(self.ctx)["jobicy_max_jobs"], 100)   # 100 jobs per page
        status.limit = f"all of its US remote jobs (up to {pages * 100:,})"
        for _ in range(pages):
            params = {"count": 100, "geo": "usa", **({"cursor": cursor} if cursor else {})}
            r = self.client.get("https://jobicy.com/api/v2/remote-jobs?" + urllib.parse.urlencode(params))
            if r.status >= 400:
                if not out:
                    raise RuntimeError(f"HTTP {r.status}")
                status.errors.append(f"page after {len(out)} jobs: HTTP {r.status}")
                break
            d = r.json()
            new = [j for j in d.get("jobs", []) if j.get("id") not in seen]
            seen.update(j.get("id") for j in new)
            out.extend(self._posting(j) for j in new)
            cursor = d.get("nextCursor")
            if not d.get("hasMore") or not cursor or not new:
                break
        return out

    def _posting(self, j: dict) -> RawPosting:
        lo, hi = _num(j.get("salaryMin")), _num(j.get("salaryMax"))
        yearly = (j.get("salaryPeriod") or "") == "yearly" and (j.get("salaryCurrency") or "USD") == "USD"
        return RawPosting(
            source="jobicy", source_url=j.get("url", ""), employer=j.get("companyName", ""), title=j.get("jobTitle", ""),
            description_text=html_to_text(j.get("jobDescription")), location_text=j.get("jobGeo", ""),
            workplace_type="remote", apply_url=j.get("url"),
            employment_type=", ".join(j.get("jobType") or []) or None,
            structured_salaries=([{"min": lo, "max": hi, "interval": "year", "context": f"Jobicy listing salary USD {lo:,.0f}-{hi:,.0f}"}]
                                 if yearly and lo and hi else []),
            extra={"third_party_salary": [lo, hi], "third_party_published": j.get("pubDate")},
        )


class WeWorkRemotelyAdapter(Adapter):
    # https://weworkremotely.com/remote-job-rss-feed — public RSS feed of the newest remote jobs, all categories.
    name = "weworkremotely"

    def search(self, status: SourceStatus) -> list[RawPosting]:
        import xml.etree.ElementTree as ET
        r = self.client.get("https://weworkremotely.com/remote-jobs.rss")
        if r.status >= 400:
            raise RuntimeError(f"HTTP {r.status}")
        out = []
        for it in ET.fromstring(r.body).findall("./channel/item"):
            f = {c.tag.split("}")[-1]: (c.text or "").strip() for c in it}
            company, _, title = f.get("title", "").partition(": ")
            if not title:
                company, title = "", f.get("title", "")
            where = "; ".join(x for x in (f.get("region"), f.get("country"), f.get("state")) if x)
            out.append(RawPosting(
                source="weworkremotely", source_url=f.get("link", ""), employer=company.strip(), title=title.strip(),
                description_text=html_to_text(f.get("description")), location_text=where, workplace_type="remote",
                apply_url=f.get("link"), employment_type=f.get("type") or None,
                extra={"third_party_published": f.get("pubDate"), "expiry": f.get("expires_at")},
            ))
        return out


class HackerNewsHiringAdapter(Adapter):
    # Hacker News "Ask HN: Who is hiring?" monthly thread, read through the free public HN Search API
    # (https://hn.algolia.com/api). Each top-level comment is one employer's post, usually starting
    # "Company | Job title | Location | ...". Mostly tech jobs.
    name = "hackernews"

    def search(self, status: SourceStatus) -> list[RawPosting]:
        import html as _html
        import re as _re
        r = self.client.get("https://hn.algolia.com/api/v1/search_by_date?tags=story,author_whoishiring&hitsPerPage=10")
        if r.status >= 400:
            raise RuntimeError(f"HTTP {r.status}")
        story = next((h for h in r.json().get("hits", []) if "who is hiring" in (h.get("title") or "").lower()), None)
        if not story:
            status.detail = "no current \"Who is hiring?\" thread found"
            return []
        t = self.client.get(f"https://hn.algolia.com/api/v1/items/{story['objectID']}")
        if t.status >= 400:
            raise RuntimeError(f"HTTP {t.status}")
        status.detail = story.get("title", "")
        matcher = self.ctx.get("title_matcher")
        out = []
        for c in t.json().get("children") or []:
            raw = c.get("text") or ""
            if not raw:
                continue
            first = _html.unescape(_re.split(r"<p>", raw, maxsplit=1)[0])
            first = _re.sub(r"<[^>]+>", "", first).strip()
            parts = [x.strip() for x in first.split("|") if x.strip()]
            if len(parts) < 2:
                continue
            title = next((x for x in parts[1:] if matcher and matcher.match_title(x)), parts[1])
            links = [_html.unescape(u) for u in _re.findall(r'href="([^"]+)"', raw)]
            url = f"https://news.ycombinator.com/item?id={c.get('id')}"
            out.append(RawPosting(
                source="hackernews", source_url=url, employer=parts[0], title=title,
                description_text=html_to_text(raw), location_text=" | ".join(parts[2:]),
                apply_url=links[0] if links else url,
                workplace_type="remote" if _re.search(r"\bremote\b", first, _re.I) and not _re.search(r"\b(onsite|on-site|hybrid)\b", first, _re.I) else None,
                extra={"third_party_published": c.get("created_at")},
            ))
        return out
