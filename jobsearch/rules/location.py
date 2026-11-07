"""Remote + home-state eligibility hard filter, travel and time-zone reporting.

Policy:
  * Must be fully remote. Hybrid / routine office attendance -> fail.
  * Must permit residence in the home state (when set). "Remote" alone is NOT nationwide eligibility.
  * Explicit "anywhere in the US" (no conflicting restriction) -> pass.
  * Home state explicitly excluded, or an allowed-state list omitting it -> fail.
  * Otherwise state eligibility ambiguous -> unclear (Needs Verification).
"""
from __future__ import annotations

import re

from ..models import RuleResult, Evidence, PASS, FAIL, UNCLEAR
from ..text import excerpt

STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California", "CO": "Colorado",
    "CT": "Connecticut", "DE": "Delaware", "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho",
    "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana",
    "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota",
    "MS": "Mississippi", "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York", "NC": "North Carolina",
    "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania",
    "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas",
    "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington", "WV": "West Virginia",
    "WI": "Wisconsin", "WY": "Wyoming", "DC": "District of Columbia",
}
_STATE_NAME_RE = re.compile(r"\b(" + "|".join(sorted((re.escape(v) for v in STATES.values()), key=len, reverse=True)) + r")\b", re.I)
_STATE_ABBR_RE = re.compile(r"(?<![A-Za-z])(" + "|".join(STATES.keys()) + r")(?![A-Za-z])")
def _home():
    from ..criteria import CRITERIA
    name = (CRITERIA.get("home_state") or "").strip()
    abbr = CRITERIA.get("home_state_abbr") or ""
    if not name:
        return "", None
    pat = r"(?i:\b" + r"\s+".join(map(re.escape, name.split())) + r"\b)"
    if abbr:
        pat += r"|(?<![A-Za-z])" + re.escape(abbr) + r"(?![A-Za-z])"
    return name, re.compile(pat)

REMOTE_RE = re.compile(r"\b(fully\s+remote|100%\s+remote|remote|work\s+from\s+home|wfh|telecommut\w*|work\s+from\s+anywhere|distributed\s+team)\b", re.I)
HYBRID_STRONG = [
    re.compile(r"\b(this|the)\s+(role|position|job|opportunity)\s+is\s+(a\s+)?hybrid\b", re.I),
    re.compile(r"\bhybrid\s+(role|position|schedule|work\s+(model|schedule|arrangement)|model|arrangement)\b", re.I),
    re.compile(r"\b(\d|one|two|three|four)\s+(or\s+more\s+)?days?\s+(per|a|each|every)\s+week\s+(in|at|from)\s+(the|our|an?)\s+\w*\s*office", re.I),
    re.compile(r"\bin[\s-]office\s+(at\s+least\s+)?(\d|one|two|three|four)\s+days", re.I),
    re.compile(r"\b(must|required\s+to|expected\s+to)\s+(be\s+able\s+to\s+)?(report|commute|come)\s+(in)?to\s+(the|our|an?)\s+\w*\s*office\b", re.I),
    re.compile(r"\b(on[\s-]?site|in[\s-]person|in[\s-]office)\s+(role|position|requirement)\b", re.I),
    re.compile(r"\bnot\s+(a\s+)?(fully\s+)?remote\b|\bremote\s+work\s+is\s+not\s+(available|an\s+option)", re.I),
]
HYBRID_NEGATION = re.compile(r"\b(not\s+(a\s+)?hybrid|no\s+(office|in[\s-]office)\s+(requirement|attendance))\b", re.I)
OFFICE_PERIODIC_UNCLEAR = re.compile(r"\b(monthly|once\s+a\s+month|every\s+month)\b.{0,40}\b(office|on[\s-]?site|in[\s-]person)\b|\b(office|on[\s-]?site|in[\s-]person)\b.{0,40}\b(monthly|once\s+a\s+month)\b", re.I)
LOC_HYBRID = re.compile(r"\bhybrid\b|\bon[\s-]?site\b|\bin[\s-]office\b", re.I)

NATIONWIDE = re.compile(
    r"anywhere\s+(with)?in\s+the\s+(u\.?s\.?a?\.?|united\s+states|us|continental\s+(u\.?s\.?|united\s+states))"
    r"|all\s+50\s+(u\.?s\.?\s+)?states|any\s+(u\.?s\.?|us)\s+state|in\s+any\s+(of\s+the\s+)?(50\s+)?states"
    r"|remote\s*[-–(,:]?\s*(anywhere\s+in\s+the\s+)?(u\.?s\.?|united\s+states|usa)\s*[-–(,:]?\s*nationwide"
    r"|nationwide\s+remote|remote\s+nationwide",
    re.I,
)
RESTRICTION_VAGUE = re.compile(
    r"\b(not\s+(able|eligible|currently\s+able)\s+to\s+(hire|employ)\s+in\s+(all|every|certain|some)\s+states"
    r"|(certain|select|specific|approved|eligible|registered)\s+(u\.?s\.?\s+)?states"
    r"|(hire|hiring|employ)\s+in\s+most\s+states|state\s+restrictions?\s+appl(y|ies))\b", re.I)
EXCLUSION_CUE = re.compile(
    r"\b(unable|not\s+able|cannot|can\s*not|can't|do(es)?\s+not|are\s+not|is\s+not|not\s+currently|not\s+eligible|excluding|except(ion)?|exclude[sd]?|outside\s+of|other\s+than|with\s+the\s+exception)\b", re.I)
ALLOWED_LIST_CUE = re.compile(
    r"(?:(?:able|eligible|authorized|registered|licensed)\s+to\s+(?:hire|employ|work)(?:\s+\w+){0,3}\s+in"
    r"|(?:currently\s+)?(?:hiring|hire|employ)(?:\s+\w+){0,3}\s+(?:in|from)\s+(?:the\s+following|these|select)"
    r"|must\s+(?:reside|live|be\s+located|be\s+based|be\s+a\s+resident)\s+(?:in|within)"
    r"|(?:reside|residing|located|based)\s+in\s+(?:one\s+of\s+)?(?:the\s+following|these)"
    r"|(?:following|these)\s+states\s*:"
    r"|open\s+to\s+(?:candidates|applicants|residents)\s+(?:located|residing|based)?\s*in)", re.I)
NON_US = re.compile(r"\b(canada|united\s+kingdom|\buk\b|europe|emea|apac|latam|latin\s+america|india|philippines|australia|germany|ireland|brazil|argentina|colombia|poland|portugal|spain|netherlands|france|singapore|japan|south\s+africa|costa\s+rica|(?<!new\s)mexico)\b", re.I)
US_RE = re.compile(r"\b(u\.?s\.?a?\.?|united\s+states|us|usa|america)\b", re.I)
US_LABEL = re.compile(r"\b(us|u\.s\.?|usa|u\.s\.a\.?|united\s+states|america|nationwide)\b", re.I)
US_TEXT = re.compile(
    r"\b(remote[\s,(\-–:]*(in\s+the\s+|within\s+the\s+)?(us|u\.s\.|usa|united\s+states))\b"
    r"|\b(u\.s\.|us|usa)[\s-]based\b"
    r"|\b(within|in|across|throughout)\s+the\s+(u\.s\.|us|usa|united\s+states)\b"
    r"|\bunited\s+states\b",
    re.I)
TRAVEL_RE = re.compile(r"\btravel", re.I)
NO_TRAVEL = re.compile(r"\bno\s+travel\b|\btravel\s+(is\s+)?not\s+required\b", re.I)
TZ_RE = re.compile(r"\b(EST|EDT|ET|Eastern|CST|CDT|CT|Central|MST|MDT|MT|Mountain|PST|PDT|PT|Pacific)\b\s*(time(\s+zone)?|hours|business\s+hours|tz)?", re.I)


def _clause_window(full: str, start: int, maxlen: int = 400) -> str:
    """Text from start to the end of the sentence or list that follows (bounded)."""
    w = full[start: start + maxlen]
    w = re.split(r"\n\s*\n", w)[0]
    m = re.search(r"[.!?]\s+(?=[A-Z])(?!(" + "|".join(re.escape(v) for v in STATES.values()) + r")\b)", w)
    return w[: m.start() + 1] if m else w


def _states_in(s: str) -> set[str]:
    names = {m.group(1).lower() for m in _STATE_NAME_RE.finditer(s)}
    abbrs = {STATES[m.group(1)].lower() for m in _STATE_ABBR_RE.finditer(s)}
    return names | abbrs


def travel_requirements(text: str) -> list[str]:
    out = []
    for line in re.split(r"(?<=[.!?])\s+|\n", text):
        if TRAVEL_RE.search(line) and not re.search(r"travel\s+(reimburse|stipend|allowance|insurance)", line, re.I):
            out.append(excerpt(line, 200))
    if any(NO_TRAVEL.search(o) for o in out):
        return [o for o in out if NO_TRAVEL.search(o)] or out
    return out[:4]


def timezone_requirements(text: str) -> list[str]:
    out = []
    for line in re.split(r"(?<=[.!?])\s+|\n", text):
        if TZ_RE.search(line) and re.search(r"\b(hours|time\s*zone|timezone|overlap|available|working)\b", line, re.I):
            # avoid matching 'PT' inside unrelated words and 'MT' noise without hour context
            out.append(excerpt(line, 200))
    return out[:3]


def _in_person(loc: str, kind: str, ev, data, res) -> RuleResult:
    """remote_only = "no": hybrid / on-site jobs are kept if they are in the home state (when set)."""
    from ..criteria import CRITERIA
    data["arrangement"] = kind
    home = (CRITERIA.get("home_state") or "").strip()
    if not home:
        return res(PASS, f"{kind.capitalize()} job (location: {loc or 'not stated'}); remote-only is off and no home state is set")
    states = _states_in(loc) if loc else set()
    cities = [c for c in CRITERIA.get("home_cities") or [] if c.strip()]
    if home.lower() in states or any(re.search(r"\b" + re.escape(c) + r"\b", loc, re.I) for c in cities):
        data["nm_eligible"] = True
        return res(PASS, f"{kind.capitalize()} job in your state ({loc})")
    if states or (NON_US.search(loc) and not US_RE.search(loc)):
        data["nm_eligible"] = False
        return res(FAIL, f"{kind.capitalize()} job outside your state ({loc})")
    return res(UNCLEAR, f"{kind.capitalize()} job; its location is not clear ({loc or 'not stated'})")


def evaluate(location_text: str, workplace_type: str | None, text: str, url: str | None,
             authoritative: bool, structured_locations: list[str] | None = None) -> RuleResult:
    loc = location_text or ""
    full = f"{loc}\n{text}"
    src = "employer_page" if authoritative else "third_party"
    ev: list[Evidence] = []
    data: dict = {"location_label": loc, "travel": travel_requirements(text), "timezone": timezone_requirements(text)}

    def res(outcome: str, reason: str) -> RuleResult:
        return RuleResult("location", outcome, reason, ev[:5], data)

    # ---- 1. fully remote? -------------------------------------------------
    from ..criteria import CRITERIA
    remote_only = CRITERIA.get("remote_only", True)
    wt = (workplace_type or "").lower()
    if wt in ("hybrid", "onsite", "on-site", "in_office", "in-office"):
        ev.append(Evidence(f"workplace type: {workplace_type}; location: {loc}", url, src))
        if not remote_only:
            return _in_person(loc, "hybrid" if wt == "hybrid" else "on-site", ev, data, res)
        return res(FAIL, f"Workplace type is {workplace_type}")
    if LOC_HYBRID.search(loc):
        ev.append(Evidence(f"location: {loc}", url, src))
        if not remote_only:
            return _in_person(loc, "hybrid" if re.search(r"hybrid", loc, re.I) else "on-site", ev, data, res)
        return res(FAIL, "Location label indicates hybrid/on-site")
    for pat in HYBRID_STRONG:
        m = pat.search(text)
        if m and not HYBRID_NEGATION.search(text[max(0, m.start() - 40): m.end() + 40]):
            ev.append(Evidence(excerpt(text[max(0, m.start() - 100): m.end() + 100]), url, src))
            if not remote_only:
                return _in_person(loc, "hybrid", ev, data, res)
            return res(FAIL, "Posting requires hybrid/routine office attendance")
    remote = wt == "remote" or bool(REMOTE_RE.search(loc)) or bool(REMOTE_RE.search(text))
    if not remote:
        ev.append(Evidence(f"location: {loc or '(none)'}", url, src))
        if not remote_only:
            return _in_person(loc, "on-site", ev, data, res)
        return res(FAIL, "Not advertised as remote")
    m = REMOTE_RE.search(loc) or REMOTE_RE.search(text)
    if m:
        hay = loc if REMOTE_RE.search(loc) else text
        ev.append(Evidence(excerpt(hay[max(0, m.start() - 80): m.end() + 120]), url, src))
    periodic = OFFICE_PERIODIC_UNCLEAR.search(text)
    data["remote"] = True

    # ---- 2. home-state eligibility (only when a home state is set) -------
    HOME, NM_RE = _home()
    home_l = HOME.lower()
    # 2a. explicit exclusion of the home state
    for em in (EXCLUSION_CUE.finditer(full) if NM_RE else []):
        window = _clause_window(full, em.start())
        if NM_RE.search(window) and re.search(r"\b(state|states|reside|residents?|located|hire|employ|locations?)\b", window, re.I):
            ev.append(Evidence(excerpt(window, 300), url, src))
            data["nm_eligible"] = False
            return res(FAIL, f"{HOME} explicitly excluded")

    # 2b. allowed-state list
    for am in ALLOWED_LIST_CUE.finditer(full):
        # an exclusion cue just before the list ("unable to hire in the following states:")
        # makes it a list of EXCLUDED states, not allowed ones
        lead = re.split(r"[.!?\n]", full[max(0, am.start() - 80): am.start() + len(am.group(0))])[-1]
        if EXCLUSION_CUE.search(lead):
            continue
        window = _clause_window(full, am.start(), 600)
        listed = _states_in(window[len(am.group(0)):])
        if not listed:
            continue
        ev.append(Evidence(excerpt(window, 300), url, src))
        if not HOME:
            data["nm_eligible"] = None
            return res(UNCLEAR, f"Hiring limited to listed states ({', '.join(sorted(listed))[:120]}); no home state set in settings")
        if home_l in listed:
            data["nm_eligible"] = True
            return res(UNCLEAR if periodic else PASS,
                       f"{HOME} appears in the allowed-state list" + ("; periodic office attendance mentioned" if periodic else ""))
        data["nm_eligible"] = False
        return res(FAIL, f"Allowed-state list omits {HOME}")

    # 2c. location label names a non-US region only
    loc_states = _states_in(loc) if loc else set()
    if loc and NON_US.search(loc) and not US_RE.search(loc) and not loc_states:
        ev.append(Evidence(f"location: {loc}", url, src))
        data["nm_eligible"] = False
        return res(FAIL, "Remote location is outside the United States")
    if HOME and home_l in loc_states:
        data["nm_eligible"] = True
        return res(UNCLEAR if periodic else PASS, f"Location label names {HOME}")
    if structured_locations:
        sl = " | ".join(structured_locations)
        st = _states_in(sl)
        if HOME and home_l in st:
            ev.append(Evidence(f"applicant location requirements: {sl}", url, src))
            data["nm_eligible"] = True
            return res(PASS, f"Structured applicant-location list includes {HOME}")

    vague = RESTRICTION_VAGUE.search(full)
    nation = NATIONWIDE.search(full)
    if nation:
        ev.append(Evidence(excerpt(full[max(0, nation.start() - 80): nation.end() + 80]), url, src))
    if vague:
        ev.append(Evidence(excerpt(full[max(0, vague.start() - 100): vague.end() + 150]), url, src))
        data["nm_eligible"] = None
        return res(UNCLEAR, f"Posting says hiring is limited to certain states; {HOME} not confirmed")
    remote_us_label = re.search(r"remote\s*[-–,(:/]?\s*(?:in\s+the\s+|within\s+the\s+)?(?:us|u\.s\.|usa|united\s+states)\b", loc, re.I)
    if loc_states and remote_us_label and not nation:
        data["nm_eligible"] = True
        ev.append(Evidence(f"location: {loc}", url, src))
        if periodic:
            return res(UNCLEAR, "Remote within the US offered, but periodic office attendance mentioned")
        return res(PASS, "Remote within the United States offered (the label also names an office location)")
    if loc_states and not nation:
        data["nm_eligible"] = None
        return res(UNCLEAR, f"Location label names {', '.join(sorted(loc_states))}; residency requirement unclear")
    if nation:
        data["nm_eligible"] = True
        if periodic:
            return res(UNCLEAR, "Remote anywhere in US, but periodic office attendance mentioned")
        return res(PASS, "Explicitly remote anywhere in the United States with no conflicting state restriction")
    # Owner policy (2026-11-02): remote within the United States with no state restriction passes.
    us = US_LABEL.search(loc) or US_TEXT.search(text)
    if us:
        hay = loc if US_LABEL.search(loc) else text
        m2 = US_LABEL.search(loc) or US_TEXT.search(text)
        ev.append(Evidence(excerpt(hay[max(0, m2.start() - 80): m2.end() + 80]), url, src))
        data["nm_eligible"] = True
        if periodic:
            return res(UNCLEAR, "Remote within the US, but periodic office attendance mentioned")
        return res(PASS, "Remote within the United States; no state restriction stated")
    data["nm_eligible"] = None
    return res(UNCLEAR, "Remote, but country/state eligibility not stated")
