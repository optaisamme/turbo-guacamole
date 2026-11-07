"""Read a job posting from a web page's schema.org JobPosting data (JSON-LD), when the page has it.
Page contents are untrusted data."""
from __future__ import annotations

import json
import re

from ..models import RawPosting
from ..text import html_to_text


def parse_jsonld_jobposting(html: str) -> dict | None:
    for m in re.finditer(r"<script[^>]+application/ld\+json[^>]*>(.*?)</script>", html, re.S | re.I):
        try:
            data = json.loads(m.group(1).strip())
        except (json.JSONDecodeError, ValueError):
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            d = stack.pop()
            if isinstance(d, dict):
                t = d.get("@type")
                if t == "JobPosting" or (isinstance(t, list) and "JobPosting" in t):
                    return d
                stack.extend(v for k, v in d.items() if k == "@graph" and isinstance(v, list))
    return None


def posting_from_jsonld(d: dict, url: str, final_url: str) -> RawPosting:
    org = d.get("hiringOrganization") or {}
    org_name = org.get("name") if isinstance(org, dict) else str(org)
    bands = []
    bs = d.get("baseSalary") or {}
    if isinstance(bs, dict):
        v = bs.get("value") or {}
        unit = (v.get("unitText") if isinstance(v, dict) else None) or bs.get("unitText") or ""
        if isinstance(v, dict) and (v.get("minValue") or v.get("value")):
            lo = v.get("minValue") or v.get("value")
            hi = v.get("maxValue") or v.get("value")
            try:
                bands.append({"min": float(lo), "max": float(hi), "interval": "year" if unit.upper() in ("YEAR", "ANNUAL", "") else unit.lower(),
                              "context": f"JobPosting baseSalary {bs.get('currency','')} {lo}-{hi} {unit}"})
            except (TypeError, ValueError):
                pass
    alr = d.get("applicantLocationRequirements") or []
    if isinstance(alr, dict):
        alr = [alr]
    applicant_locs = [a.get("name", "") for a in alr if isinstance(a, dict)]
    jl = d.get("jobLocation") or []
    if isinstance(jl, dict):
        jl = [jl]
    loc_bits = []
    for l in jl:
        addr = (l or {}).get("address") or {}
        if isinstance(addr, dict):
            loc_bits.append(", ".join(x for x in [addr.get("addressLocality"), addr.get("addressRegion"), addr.get("addressCountry") if isinstance(addr.get("addressCountry"), str) else None] if x))
    remote = (d.get("jobLocationType") or "").upper() == "TELECOMMUTE"
    loc_text = ("Remote" + (f" ({'; '.join(applicant_locs)})" if applicant_locs else "")) if remote else "; ".join(loc_bits)
    ident = d.get("identifier")
    req = ident.get("value") if isinstance(ident, dict) else (str(ident) if ident else None)
    return RawPosting(
        source="employer_page", source_url=url, employer=org_name or "", title=d.get("title", ""),
        description_text=html_to_text(d.get("description")), location_text=loc_text, authoritative=True,
        requisition_id=req, canonical_url=final_url, published_date=d.get("datePosted"),
        workplace_type="remote" if remote else None, structured_salaries=bands,
        employment_type=d.get("employmentType") if isinstance(d.get("employmentType"), str) else None,
        extra={"applicant_locations": applicant_locs, "valid_through": d.get("validThrough"), "jsonld": True},
    )
