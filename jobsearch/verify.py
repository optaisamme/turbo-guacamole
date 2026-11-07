"""Resolve employer postings and verify that an application route for the exact role is live.

HTTP 200 alone is never treated as proof a job is open. Checks:
  * ATS API still lists the job (when ATS-backed)
  * final URL after redirects, still on a job-detail page (not a generic careers/search page)
  * page identifies the same role (title) and employer
  * no closed/expired/filled markers
  * visible application form or apply control for that role
"""
from __future__ import annotations

import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from .http import AccessBlocked
from .models import RawPosting, Verification
from .text import html_to_text
from .adapters import ats as atsmod
from .adapters.base import is_job_board, is_ats, host_of, is_no_fetch
from .adapters.jsonld import parse_jsonld_jobposting, posting_from_jsonld

CLOSED_RE = re.compile(
    r"no\s+longer\s+(accepting\s+applications|available|open|active|being\s+accepted)"
    r"|(job|posting|position|requisition|vacancy|opening)\s+(has\s+)?(been\s+)?(expired|closed|filled|removed|archived)"
    r"|position\s+has\s+been\s+filled|this\s+(job|position|role|posting)\s+(is\s+)?(closed|no\s+longer)"
    r"|job\s+not\s+found|the\s+job\s+you('|’)?re\s+looking\s+for|couldn('|’)?t\s+find\s+(that|the|this)\s+job"
    r"|not\s+currently\s+accepting\s+applications|applications?\s+(are|is)\s+(now\s+)?closed"
    r"|page\s+not\s+found|404\s+error|this\s+job\s+has\s+expired",
    re.I)
APPLY_RE = re.compile(
    r"apply\s+(for\s+this\s+(job|position|role)|now|to\s+this\s+job|here|online)|submit\s+(your\s+)?application"
    r"|start\s+(your\s+)?application|i('|’)?m\s+interested|easy\s+apply|\bapply\b",
    re.I)
FORM_RE = re.compile(r"<form[^>]*(applic|apply|job_app)|id=[\"']application|name=[\"'](resume|job_application)|type=[\"']file[\"']|application_form|lever-apply|postings-btn|_ashby|data-qa=[\"']btn-apply", re.I)
GENERIC_PATH = re.compile(r"^/?(careers?|jobs?|join(-us)?|opportunities|open-positions|search|work-with-us|en/?(careers|jobs)?)/?$|/search\b|[?&](q|keywords?|query)=", re.I)
STOP = {"the", "a", "an", "of", "and", "for", "to", "in", "at", "with", "-", "–", "remote", "us", "usa", "i", "ii", "iii", "iv", "|"}
ABBR = {"sr": "senior", "jr": "junior", "mgr": "manager", "admin": "administrator", "bsa": "business systems analyst"}


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _tokens(s: str) -> list[str]:
    toks = re.findall(r"[a-z0-9+]+", (s or "").lower())
    out = []
    for t in toks:
        out.extend(ABBR.get(t, t).split())
    return [t for t in out if t not in STOP and len(t) > 1]


def title_matches(title: str, page_text: str) -> bool:
    want = set(_tokens(title))
    if not want:
        return False
    have = set(_tokens(page_text[:200000]))
    return len(want & have) / len(want) >= 0.8


def employer_matches(employer: str, page_text: str, url: str) -> bool | None:
    toks = [t for t in _tokens(employer) if len(t) > 3 and t not in {"inc", "llc", "corp", "company", "group", "foundation"}]
    if not toks:
        return None
    hay = (page_text[:200000] + " " + url).lower()
    return any(t in hay for t in toks)


# Only these settings are passed to the browser helper. Secrets (API keys, tokens) are never passed,
# because the helper opens untrusted job pages.
RENDER_ENV_KEYS = ("PATH", "HOME", "LANG", "TMPDIR", "NODE_PATH", "PLAYWRIGHT_BROWSERS_PATH", "PW_EXECUTABLE",
                   "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY", "https_proxy", "http_proxy", "no_proxy",
                   "NODE_EXTRA_CA_CERTS", "SSL_CERT_FILE")


def render_env(user_agent: str | None = None) -> dict:
    import os
    env = {k: os.environ[k] for k in RENDER_ENV_KEYS if k in os.environ}
    if user_agent:
        env["JOBSEARCH_UA"] = user_agent   # identify as this tool, like every other request
    return env


def render(url: str, timeout: int = 75, user_agent: str | None = None) -> dict | None:
    from .http import check_url, UnsafeURL
    try:
        check_url(url)
    except UnsafeURL as e:
        return {"error": str(e)}
    script = Path(__file__).with_name("render.mjs")
    try:
        cp = subprocess.run(["node", str(script), url], capture_output=True, text=True, timeout=timeout,
                            env=render_env(user_agent))
        return json.loads(cp.stdout.strip().splitlines()[-1]) if cp.stdout.strip() else None
    except (subprocess.SubprocessError, json.JSONDecodeError, FileNotFoundError, IndexError):
        return None


def resolve_employer_posting(client, p: RawPosting) -> RawPosting:
    """For third-party discoveries, try to reach the employer/ATS posting. Never guesses IDs:
    only follows links the source itself provided."""
    if p.authoritative:
        return p
    for cand in [p.apply_url, p.canonical_url]:
        if not cand or is_job_board(cand):
            continue
        parsed = atsmod.parse_ats_url(cand)
        try:
            if not parsed or parsed[0] not in atsmod.FETCHERS:
                r = client.get(cand, html=True)
                if r.status >= 400:
                    continue
                parsed = atsmod.parse_ats_url(r.final_url)
                if not (parsed and parsed[0] in atsmod.FETCHERS):
                    if is_job_board(r.final_url):
                        continue
                    d = parse_jsonld_jobposting(r.body)
                    if d:
                        q = posting_from_jsonld(d, cand, r.final_url)
                        q.employer = q.employer or p.employer
                        q.extra["discovered_via"] = p.source_url
                        q.extra["third_party_sources"] = [p.source_url]
                        return q
                    continue
            ats, board, jid = parsed
            q = atsmod.FETCHERS[ats](client, board, jid, p.employer)
            if q:
                q.extra["discovered_via"] = p.source_url
                q.extra["third_party_sources"] = [p.source_url]
                q.extra["new_board"] = {"ats": ats, "board": board, "employer": p.employer}
                return q
        except AccessBlocked:
            continue
        except Exception:  # noqa: BLE001
            continue
    return p


def verify(client, p: RawPosting, use_renderer: bool = True) -> Verification:
    checked = now_iso()
    # 1. ATS still lists it?
    if p.ats in atsmod.FETCHERS and p.ats_board and p.ats_job_id:
        try:
            live = atsmod.FETCHERS[p.ats](client, p.ats_board, p.ats_job_id, p.employer)
        except AccessBlocked as e:
            return Verification("blocked", detail=f"ATS API blocked: {e}", checked_at=checked)
        except Exception as e:  # noqa: BLE001
            return Verification("error", detail=f"ATS API error: {e}", checked_at=checked)
        if live is None:
            return Verification("closed", detail="job no longer returned by the official ATS API", checked_at=checked)
    if p.extra.get("closed"):
        return Verification("closed", detail=p.extra.get("lead_note", "closed"), checked_at=checked)

    cands = [u for u in dict.fromkeys([p.apply_url, p.canonical_url, p.extra.get("embed_apply_url")]) if u]
    if not cands:
        return Verification("unverified", detail="no application or job-detail URL provided by any source", checked_at=checked)
    first = None
    for u in cands:
        v = _check_page(client, p, u, use_renderer, checked)
        if v.status == "verified":
            return v
        if v.status in ("closed", "mismatch") and first is None:
            return v  # definitive negative from the primary page
        first = first or v
    return feed_listing(p, first, checked) or first


FEED_LISTED = "Listed in the Himalayas job feed during this search"
FEED_SOURCES = {"himalayas"}   # sites whose own job feed is trusted when they block link checks


def feed_listing(p: RawPosting, ver: Verification | None, checked: str) -> Verification | None:
    """Owner policy: a job from a site that blocks automated link checks (Himalayas) counts as open when
    the site's own job feed listed it during this search and its closing date has not passed.
    It is always labeled third-party, link not checked."""
    import time
    if p.source not in FEED_SOURCES or ver is None or ver.status not in ("blocked", "js_required", "error"):
        return None
    try:
        expiry = int(float(p.extra.get("expiry")))
    except (TypeError, ValueError):
        return None
    if expiry <= time.time():
        return None
    closes = time.strftime("%Y-%m-%d", time.gmtime(expiry))
    return Verification("verified", final_url=ver.final_url or p.apply_url, apply_url=p.apply_url or p.source_url,
                        apply_route="third_party", http_status=ver.http_status, checked_at=checked,
                        detail=f"{FEED_LISTED} (closing date {closes}). The site blocks automated checks, "
                               "so the link itself was not opened.")


def _check_page(client, p: RawPosting, url: str, use_renderer: bool, checked: str) -> Verification:
    if not url:
        return Verification("unverified", detail="no application or job-detail URL provided by any source", checked_at=checked)
    if is_no_fetch(url):
        return Verification("unverified", final_url=url, apply_route="third_party",
                            detail=f"link is on {host_of(url)}, which is not fetched (terms/login restrictions)", checked_at=checked)
    try:
        r = client.get(url, html=True)
    except AccessBlocked as e:
        return Verification("blocked", final_url=url, detail=str(e), checked_at=checked)
    except Exception as e:  # noqa: BLE001
        return Verification("error", final_url=url, detail=f"{type(e).__name__}: {e}", checked_at=checked)

    v = Verification("unverified", final_url=r.final_url, redirect_chain=r.redirect_chain, http_status=r.status, checked_at=checked)
    if r.status in (404, 410):
        v.status, v.detail = "closed", f"HTTP {r.status}"
        return v
    if r.status >= 400:
        v.status, v.detail = "error", f"HTTP {r.status}"
        return v
    text = html_to_text(r.body)
    page_title = (re.search(r"<title[^>]*>(.*?)</title>", r.body, re.S | re.I) or [None, ""])[1] if r.body else ""
    combined = f"{page_title}\n{text}"
    has_form = bool(FORM_RE.search(r.body))
    has_apply = bool(APPLY_RE.search(text))
    rendered = False
    if use_renderer and (len(text) < 400 or not title_matches(p.title, combined)) and not CLOSED_RE.search(text):
        rd = render(r.final_url, user_agent=getattr(client, "ua", None))
        if rd and not rd.get("error"):
            rendered = True
            combined = f"{rd.get('title','')}\n{rd.get('text','')}"
            text = rd.get("text", "")
            v.final_url = rd.get("final_url") or v.final_url
            has_form = has_form or rd.get("form_count", 0) > 0 or rd.get("has_file_input", False)
            has_apply = has_apply or bool(rd.get("apply_links"))
    v.page_text = combined[:50000]

    # 2. redirected to another job?
    exp = atsmod.parse_ats_url(url)
    got = atsmod.parse_ats_url(v.final_url or "")
    if exp and got and exp[2] != got[2]:
        v.status, v.detail = "mismatch", f"redirected to a different job id ({got[2]})"
        return v
    if CLOSED_RE.search(text[:20000]):
        v.status, v.detail = "closed", "page states the job is closed/expired/filled"
        return v
    path = re.sub(r"^https?://[^/]+", "", v.final_url or "")
    if not title_matches(p.title, combined):
        if GENERIC_PATH.search(path) or path in ("", "/"):
            v.status, v.detail = "generic", "redirected to a generic careers/search page"
        elif len(text) < 400 and not rendered:
            v.status, v.detail = "js_required", "page content requires JavaScript rendering; renderer unavailable or failed"
        else:
            v.status, v.detail = "mismatch", "final page does not identify the same role title"
        return v
    em = employer_matches(p.employer, combined, v.final_url or "")
    if em is False:
        v.status, v.detail = "mismatch", "final page does not identify the employer"
        return v
    if not (has_form or has_apply):
        v.status, v.detail = "unverified", "role page found but no application form or apply control detected"
        return v
    v.status = "verified"
    v.apply_url = v.final_url
    v.apply_route = "third_party" if (is_job_board(v.final_url) or p.extra.get("intermediary")) else ("employer_ats" if is_ats(v.final_url) else "employer_site")
    v.detail = ("application form present" if has_form else "apply control present") + ("; rendered with headless browser" if rendered else "")
    return v


def plain_link_reason(ver: Verification | None) -> str:
    """Explain a link-check result in everyday words (shown in results and reports)."""
    if ver is None:
        return "Application link: not checked yet."
    d = ver.detail or ""
    site = host_of(ver.final_url or "") or ""
    m = re.search(r"https?://([^/\s]+)", d)
    if m:
        site = m.group(1).lower()
    site = site.removeprefix("www.") or "the job"
    st = ver.status
    if st == "blocked":
        if "CAPTCHA" in d or "challenge" in d:
            why = "the site showed a 'prove you are human' page"
        elif "robots.txt" in d:
            why = "the site's rules for automated tools asked it not to open this page"
        elif "network policy" in d or "proxy" in d:
            why = "the computer running the search was not allowed to reach it"
        elif "HTTP 401" in d:
            why = "the site asked for a sign-in (error 401)"
        elif "HTTP 403" in d:
            why = "the site refused (error 403, \"access forbidden\")"
        else:
            why = "the site refused"
        return (f"Application link not checked: the {site} website does not allow automated checks. When this tool tried to open the job page, {why}. "
                "This tool does not get around such blocks. Open the link yourself to see if the job is still open.")
    if st == "closed":
        if "404" in d or "410" in d:
            return "The job appears to be closed: its page no longer exists (error 404)."
        if "ATS API" in d:
            return "The job appears to be closed: the employer's hiring system no longer lists it."
        return "The job appears to be closed: its page says it is closed, expired or filled."
    if st == "generic":
        return "Application link problem: the link now goes to a general careers page, not to this job."
    if st == "mismatch":
        return "Application link problem: the link now goes to a different job, or the page does not name this job."
    if st == "js_required":
        return ("Application link not checked: the job page only loads in a full web browser, and this search "
                "could not read it. Open the link yourself.")
    if st == "error":
        return "Application link not checked: the job page could not be opened (a temporary error). It will be tried again next search."
    if st == "unverified":
        if "not fetched" in d or "terms/login" in d:
            return (f"Application link not checked: the only link is on {site}, which this tool does not open "
                    "(its terms do not allow automated access, or it requires signing in).")
        if "no application or job-detail URL" in d:
            return "Application link not found: no link to this job was found."
        if "no application form or apply control" in d:
            return "Application link not confirmed: the job page was found, but it has no Apply button or application form."
        return "Application link not confirmed."
    return f"Application link: {st}."
