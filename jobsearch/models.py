from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any

QUALIFIED = "Qualified"
NEEDS_VERIFICATION = "Needs Verification"
REJECTED = "Rejected"

PASS = "pass"
FAIL = "fail"
UNCLEAR = "unclear"


@dataclass
class Evidence:
    """A short supporting excerpt and the URL it came from."""
    excerpt: str
    url: str | None = None
    source: str | None = None  # e.g. "ats_api", "employer_page", "third_party"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class RuleResult:
    rule: str
    outcome: str  # pass | fail | unclear
    reason: str
    evidence: list[Evidence] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        return d


@dataclass
class RawPosting:
    """A posting as returned by an adapter, before rules are applied."""
    source: str                    # adapter name, e.g. "greenhouse", "remotive"
    source_url: str                # page where the posting was discovered
    employer: str
    title: str
    description_text: str          # plain text
    location_text: str = ""
    authoritative: bool = False    # True when content comes from employer site / official ATS
    ats: str | None = None         # greenhouse | lever | ashby | smartrecruiters | workday | ...
    ats_board: str | None = None   # board token / company slug
    ats_job_id: str | None = None
    requisition_id: str | None = None
    canonical_url: str | None = None   # employer/ATS job-detail URL
    apply_url: str | None = None       # candidate application URL as reported by source
    published_date: str | None = None  # only when the source actually provides it
    workplace_type: str | None = None  # remote | hybrid | onsite | None
    structured_salary: dict | None = None  # {"min":..,"max":..,"interval":"year","currency":"USD","label":..}
    structured_salaries: list[dict] = field(default_factory=list)  # multiple bands, each with optional "label"
    employment_type: str | None = None  # full-time | contract | part-time | temporary
    extra: dict = field(default_factory=dict)


@dataclass
class Verification:
    status: str                 # verified | closed | generic | mismatch | blocked | js_required | error | unverified
    final_url: str | None = None
    apply_url: str | None = None
    apply_route: str | None = None  # employer_ats | employer_site | third_party
    redirect_chain: list[str] = field(default_factory=list)
    http_status: int | None = None
    detail: str = ""
    checked_at: str | None = None
    page_text: str | None = None  # not persisted

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("page_text", None)
        return d
