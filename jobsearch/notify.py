"""Notification payloads and digest rendering. Delivery itself is performed by the
configured channel (Gmail draft created by the scheduled Claude session via the Gmail
connector), which then records the outcome with `jobsearch outbox mark`."""
from __future__ import annotations

import json


def _j(row, k, default=None):
    v = row[k]
    if v is None:
        return default
    try:
        return json.loads(v)
    except (TypeError, ValueError):
        return v


def build_payload(row) -> dict:
    rules = {r["rule"]: r for r in _j(row, "rules", [])}
    ver = _j(row, "verification", {}) or {}
    fit = _j(row, "fit", {}) or {}
    loc = rules.get("location", {})
    cert = rules.get("certifications", {})
    sal = rules.get("salary", {})
    post_hire = [{"names": p.get("names"), "deadline": p.get("deadline") or "deadline not stated"} for p in cert.get("data", {}).get("post_hire", [])]
    return {
        "job_id": row["id"],
        "employer": row["employer"], "title": row["title"], "track": row["track"], "family": row["family"],
        "salary": sal.get("reason"), "salary_evidence": [e["excerpt"] for e in sal.get("evidence", [])][:2],
        "remote_eligibility": loc.get("reason"), "location_label": row["location_text"],
        "travel": (loc.get("data") or {}).get("travel") or [],
        "timezone": (loc.get("data") or {}).get("timezone") or [],
        "posting_date": row["published_date"],
        "apply_url": ver.get("apply_url") or row["apply_url"],
        "apply_route": ver.get("apply_route"),
        "link_not_checked": (ver.get("detail") or "").startswith("Listed in the Himalayas job feed"),
        "fit_summary": fit.get("summary"),
        "fit_strong": [f"{s['skill']} — {s['profile_evidence']}" for s in fit.get("strong", [])[:5]],
        "cert_filter": cert.get("reason"),
        "custom_rules": [f"{k[6:]}: {v.get('reason')}" for k, v in rules.items() if k.startswith("rule: ")],
        "post_hire_certifications": post_hire,
        "gaps": [g["skill"] + (" (required)" if g.get("required") else "") for g in fit.get("gaps", [])][:6],
        "mandatory_unsupported": fit.get("mandatory_unsupported", [])[:6],
        "verified_at": row["last_verified"],
        "repost_note": row["repost_note"],
        "first_seen": row["first_seen"],
    }


def render_item(p: dict, kind: str) -> str:
    label = {"new": "NEW", "newly_verified": "NEWLY VERIFIED (first seen earlier; not newly posted)",
             "baseline": "ALREADY LISTED WHEN YOU STARTED"}.get(kind, kind.upper())
    lines = []
    head = f"[{label}] {p['title']} — {p['employer']}"
    lines.append(head)
    lines.append(f"Job title group: {p['family']}" + (f" (track {p['track']})" if p.get("track") else ""))
    lines.append(f"Advertised annual base salary: {p['salary']}")
    lines.append(f"Remote / location eligibility: {p['remote_eligibility']}  [location: {p['location_label']}]")
    lines.append("Travel: " + ("; ".join(p["travel"]) if p["travel"] else "none stated"))
    if p["timezone"]:
        lines.append("Working-hours note: " + "; ".join(p["timezone"]))
    lines.append("Posting date: " + (p["posting_date"] or "not provided by employer") + f" (first seen by automation: {p['first_seen']})")
    route = {"employer_ats": "employer ATS (direct)", "employer_site": "employer site (direct)",
             "third_party": "THIRD-PARTY APPLICATION (not employer-direct)"}.get(p["apply_route"], p["apply_route"] or "unknown")
    if p.get("link_not_checked"):
        route += "; LINK NOT CHECKED: listed in the Himalayas job feed during this search, but the site blocks automated checks"
    lines.append(f"Apply: {p['apply_url']}  [{route}]")
    lines.append(f"Fit: {p['fit_summary'] or 'see report'}")
    for s in p["fit_strong"][:3]:
        lines.append(f"  Match: {s}")
    for c in p.get("custom_rules", []):
        lines.append(f"Your rule — {c}")
    lines.append(f"Certification filter: {p['cert_filter']}")
    if p["post_hire_certifications"]:
        for c in p["post_hire_certifications"]:
            lines.append(f"  Post-hire certification condition: {', '.join(c['names'] or [])} — {c['deadline']}")
    if p["mandatory_unsupported"]:
        lines.append("Required items not supported by profile: " + " | ".join(p["mandatory_unsupported"]))
    if p["gaps"]:
        lines.append("Gaps (not documented in profile): " + ", ".join(p["gaps"]))
    if p.get("repost_note"):
        lines.append(f"Repost check: {p['repost_note']}")
    lines.append(f"Application link verified: {p['verified_at']} (UTC)")
    return "\n".join(lines)


def render_near_miss(p: dict) -> str:
    head = f"{p['title']} — {p['employer']}"
    lines = [head, f"Link: {p.get('link') or 'no link available'}"]
    lines += [f"  • {r}" for r in p["reasons"]]
    return "\n".join(lines)


def render_digest(items: list[tuple[str, dict]], near: list[dict] | None = None) -> dict:
    near = near or []
    first = [(k, p) for k, p in items if k == "baseline"]
    new = [(k, p) for k, p in items if k != "baseline"]
    parts = []
    if items:
        parts.append(f"{len(items)} matching job{'s' if len(items) != 1 else ''}")
    if near:
        parts.append(f"{len(near)} near miss{'es' if len(near) != 1 else ''}")
    subject = ("Job search results (first run): " if first else "Job search results: ") + ", ".join(parts)
    body = []
    if first:
        body += ["INITIAL RESULTS: jobs that already matched all your rules when you started", ""]
        for kind, p in first:
            body.append(render_item(p, kind))
            body.append("")
    if new:
        body += [f"MATCHING JOBS: {len(new)} new job(s) that pass all your rules. "
                 "Each application link was re-checked just before this post.", ""]
        for kind, p in new:
            body.append(render_item(p, kind))
            body.append("")
    if near:
        body += ["NEAR MISSES: jobs that failed exactly ONE of your rules (reason shown; each is shared once)", ""]
        for p in near:
            body.append(render_near_miss(p))
            body.append("")
    body.append("All other jobs checked, and why they did not match, are in the JOB_SEARCH_RESULTS folder in this repository.")
    body.append("This automation never applies to jobs or contacts employers.")
    return {"subject": subject, "body": "\n".join(body)}
