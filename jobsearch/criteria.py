"""Runtime hard-filter criteria. Everything is off until set in
JOB_SEARCH_SETTINGS/job_search_settings.toml (see from_settings)."""
from __future__ import annotations

DEFAULTS = {
    "home_state": "", "home_state_abbr": "", "home_cities": [],
    "single_min": 0, "range_lower_min": 0, "range_upper_gt": 0,
    "allowed_prehire_certs": [], "cert_filter_on": False,
    "custom_rules": [],   # user-defined requirement rules (see rules/custom.py)
    "remote_only": True,  # keep only fully remote jobs (setting: remote_only = "yes")
}

# Starting point when a settings.toml exists: nothing filters until the user sets it.
NEUTRAL = {
    "remote_only": True,
    "home_state": "", "home_state_abbr": "", "home_cities": [],
    "single_min": 0, "range_lower_min": 0, "range_upper_gt": 0,
    "allowed_prehire_certs": [], "cert_filter_on": False,
    "custom_rules": [],
}

CRITERIA: dict = dict(DEFAULTS)


def configure(values: dict | None, base: dict | None = None) -> dict:
    CRITERIA.clear()
    CRITERIA.update(DEFAULTS if base is None else base)
    for k, v in (values or {}).items():
        if v is not None:
            CRITERIA[k] = v
    return CRITERIA


def _yes(v) -> bool:
    """Accept true/false or "yes"/"no" (easier for non-programmers)."""
    if isinstance(v, str):
        return v.strip().lower() in ("yes", "y", "true", "on", "1")
    return bool(v)


def from_settings(settings: dict) -> dict:
    """Map the settings file (written by the questionnaire) onto criteria keys."""
    out: dict = {}
    w = settings.get("where_you_live", {})
    out["remote_only"] = _yes(w.get("remote_only", "yes"))
    if w.get("state"):
        out["home_state"] = w["state"]
        out["home_state_abbr"] = w.get("state_abbreviation", "")
        out["home_cities"] = w.get("cities", [])
    s = settings.get("salary", {})
    if "minimum_single_salary" in s:
        out["single_min"] = int(s["minimum_single_salary"])
    if "minimum_range_start" in s:
        out["range_lower_min"] = int(s["minimum_range_start"])
    if "range_end_must_be_above" in s:
        out["range_upper_gt"] = int(s["range_end_must_be_above"])
    f = settings.get("filters", {})
    if "certifications_allowed_before_hire" in f:
        out["allowed_prehire_certs"] = list(f["certifications_allowed_before_hire"])
    if "check_certifications" in f:
        out["cert_filter_on"] = _yes(f["check_certifications"])
    rules = f.get("requirement_rule")
    if rules is not None:
        custom = []
        for i, r in enumerate(rules, 1):
            terms = [t for t in r.get("terms", []) if t.strip()]
            if terms:
                custom.append({"name": (r.get("name") or "").strip() or f"Rule {i}", "on": _yes(r.get("on", True)),
                               "terms": terms, "reject_when": (r.get("reject_when") or "required").strip().lower()})
        out["custom_rules"] = custom
    return out
