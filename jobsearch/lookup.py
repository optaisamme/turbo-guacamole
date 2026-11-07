"""Jobs whose link could not be checked automatically, so the employer's own posting
should be looked up (a third-party listing, or a site that blocks automated checks)."""
from __future__ import annotations

import json
import urllib.parse

from .agencies import is_agency


def needs_employer_posting(db) -> list[dict]:
    rows = db.execute(
        "SELECT id, employer, title, canonical_url, source_urls, reasons, verification, first_seen FROM jobs "
        "WHERE closed_at IS NULL AND status = 'Needs Verification' ORDER BY first_seen DESC, id").fetchall()
    out = []
    for r in rows:
        reasons = " ".join(json.loads(r["reasons"] or "[]"))
        ver = json.loads(r["verification"] or "{}") or {}
        link_problem = ver.get("status") in ("blocked", "unverified", "js_required", "error") or "third-party" in reasons
        employer = (r["employer"] or "").strip()
        if not link_problem or not employer or not r["title"] or is_agency(employer):
            continue
        q = f'"{employer}" "{r["title"]}" careers'
        out.append({
            "job_id": r["id"], "employer": employer, "title": r["title"],
            "current_link": (json.loads(r["source_urls"] or "[]") or [r["canonical_url"]])[0],
            "why": ver.get("status") or "third-party",
            "search_link": "https://www.google.com/search?q=" + urllib.parse.quote_plus(q),
            "first_seen": (r["first_seen"] or "")[:10],
        })
    return out
