"""Hard-filter tests. All inputs are synthetic TEST FIXTURES, not live listings."""
import pytest

from jobsearch.models import PASS, FAIL, UNCLEAR
from jobsearch.rules import salary, location, certs
from jobsearch.text import segment

URL = "https://example.test/job/1"


# ---------------------------------------------------------------- salary boundaries
@pytest.mark.parametrize("lo,hi,expected", [
    (100_000, 100_000, PASS),    # single salary exactly 100k
    (99_999, 99_999, FAIL),
    (90_000, 110_000, PASS),
    (95_000, 105_000, PASS),
    (90_000, 100_000, FAIL),     # range ending exactly at 100k: upper must be STRICTLY greater
    (85_000, 120_000, FAIL),     # lower below 90k
    (90_000, 100_001, PASS),
    (89_999, 150_000, FAIL),
])
def test_salary_amount_boundaries(lo, hi, expected):
    assert salary.evaluate_amounts(lo, hi)[0] == expected


@pytest.mark.parametrize("text,expected", [
    ("Salary: $100,000 per year.", PASS),
    ("The base salary range for this role is $90,000 - $110,000.", PASS),
    ("Base pay range: $95K–$105K annually.", PASS),
    ("Pay range: $90,000 to $100,000 per year.", FAIL),
    ("The annual salary range is $85,000-$120,000.", FAIL),
    ("Salary range $90-110K", PASS),
])
def test_salary_text_parsing(text, expected):
    assert salary.evaluate([], text, URL, True).outcome == expected


def test_salary_ignores_bonus_and_total_comp():
    t = "Total compensation of $150,000 - $180,000 including bonus. Base salary: $80,000 - $95,000."
    r = salary.evaluate([], t, URL, True)
    assert r.outcome == FAIL and r.data["max"] == 95_000


def test_salary_ote_only_is_unclear():
    r = salary.evaluate([], "OTE: $140,000 with uncapped commission.", URL, True)
    assert r.outcome == UNCLEAR


def test_salary_missing_is_unclear():
    assert salary.evaluate([], "Competitive salary and benefits.", URL, True).outcome == UNCLEAR


def test_salary_third_party_listing_counts_but_is_labeled():
    r = salary.evaluate([], "Salary: $120,000 per year", URL, authoritative=False)
    assert r.outcome == PASS and r.evidence[0].source == "third_party"


def test_salary_predicted_is_unclear():
    r = salary.evaluate([{"min": 120000, "max": 130000, "interval": "year", "predicted": True}], "", URL, authoritative=False)
    assert r.outcome == UNCLEAR


def test_salary_hourly_held_separately():
    r = salary.evaluate([], "Pay: $50 - $60 per hour", URL, True)
    assert r.outcome == UNCLEAR and r.data["compensation_type"] == "hourly"


def test_salary_contract_held_separately():
    r = salary.evaluate([], "Salary: $120,000 per year", URL, True, employment_type="Contract")
    assert r.outcome == UNCLEAR and r.data["compensation_type"] == "contract"


def test_salary_location_bands_nm_applicable():
    bands = [
        {"min": 120_000, "max": 140_000, "interval": "year", "label": "Zone A (CA, NY, WA)"},
        {"min": 85_000, "max": 100_000, "interval": "year", "label": "All other US locations"},
    ]
    assert salary.evaluate(bands, "", URL, True).outcome == FAIL


def test_salary_location_bands_nm_named_pass():
    bands = [
        {"min": 120_000, "max": 140_000, "interval": "year", "label": "Tier 1"},
        {"min": 92_000, "max": 115_000, "interval": "year", "label": "New Mexico"},
    ]
    assert salary.evaluate(bands, "", URL, True).outcome == PASS


def test_salary_location_bands_undeterminable_unclear():
    bands = [
        {"min": 120_000, "max": 140_000, "interval": "year", "label": "Zone 1"},
        {"min": 80_000, "max": 98_000, "interval": "year", "label": "Zone 2"},
    ]
    assert salary.evaluate(bands, "", URL, True).outcome == UNCLEAR


def test_salary_all_bands_pass():
    bands = [
        {"min": 120_000, "max": 140_000, "interval": "year", "label": "Zone 1"},
        {"min": 100_000, "max": 120_000, "interval": "year", "label": "Zone 2"},
    ]
    assert salary.evaluate(bands, "", URL, True).outcome == PASS


def test_salary_up_to_only():
    assert salary.evaluate([], "Salary up to $100,000 per year.", URL, True).outcome == FAIL
    assert salary.evaluate([], "Salary up to $130,000 per year.", URL, True).outcome == UNCLEAR


def test_salary_not_confused_by_org_revenue():
    r = salary.evaluate([], "We manage $16.2M in annual donations. Salary: $105,000 per year.", URL, True)
    assert r.outcome == PASS


# ---------------------------------------------------------------- location / NM
def L(loc, text, wt=None):
    return location.evaluate(loc, wt, text, URL, True)


def test_remote_anywhere_us_passes():
    assert L("Remote", "This role is fully remote and open to candidates anywhere in the United States.").outcome == PASS


def test_remote_us_passes_owner_policy():
    assert L("Remote - US", "This is a remote position.").outcome == PASS
    assert L("Remote", "Fully remote role for candidates based in the United States.").outcome == PASS


def test_remote_without_country_is_unclear():
    assert L("Remote", "This is a remote position.").outcome == UNCLEAR


def test_remote_us_with_other_state_label_is_unclear():
    assert L("Remote - Texas", "This is a remote position.").outcome == UNCLEAR


def test_join_us_is_not_a_us_location():
    assert L("Remote", "Join us! This is a remote position.").outcome == UNCLEAR


def test_nm_explicitly_excluded():
    r = L("Remote - US", "Fully remote. We are unable to hire in the following states: Alaska, Hawaii, New Mexico.")
    assert r.outcome == FAIL


def test_nm_excluded_by_abbreviation():
    assert L("Remote", "Remote (US). Excluding residents of CA, NY, NM.").outcome == FAIL


def test_allowed_state_list_without_nm():
    assert L("Remote", "Remote role. Candidates must reside in one of the following states: Texas, Colorado, Arizona.").outcome == FAIL


def test_allowed_state_list_with_nm():
    assert L("Remote", "Remote role. Candidates must reside in one of the following states: Texas, New Mexico, Arizona.").outcome == PASS


def test_anywhere_but_certain_states_is_unclear():
    t = "Remote anywhere in the US. We are not able to hire in all states; eligible states are listed on our careers page."
    assert L("Remote", t).outcome == UNCLEAR


def test_hybrid_rejected():
    assert L("Denver, CO (Hybrid)", "Great team.").outcome == FAIL
    assert L("Remote", "This is a hybrid role with 3 days per week in the office.").outcome == FAIL
    assert L("Austin, TX", "Join us.", wt="hybrid").outcome == FAIL


def test_onsite_not_remote_rejected():
    assert L("Chicago, IL", "Join our team in our downtown office.").outcome == FAIL


def test_non_us_rejected():
    assert L("Remote - Canada", "Fully remote.").outcome == FAIL


def test_new_mexico_not_confused_with_mexico():
    assert L("Remote - New Mexico", "Fully remote.").outcome == PASS


def test_travel_reported_separately():
    r = L("Remote", "Fully remote anywhere in the US. Travel up to 10% for annual team meetings.")
    assert r.outcome == PASS and r.data["travel"]


# ---------------------------------------------------------------- requirement rules: required vs preferred
def C(text, terms=("Epic", "Tableau")):
    from jobsearch.rules import custom
    return custom.evaluate_rule({"name": "test", "terms": list(terms), "reject_when": "required"}, segment(text), URL, True)


def test_req_required_rejected():
    assert C("Requirements:\n- 3+ years of reporting experience\n- Experience with Epic required").outcome == FAIL


def test_req_preferred_ok():
    assert C("Requirements:\n- 3+ years of reporting\nPreferred Qualifications:\n- Tableau experience").outcome == PASS


def test_req_a_plus_ok():
    assert C("Qualifications:\n- Tableau experience is a plus").outcome == PASS


def test_req_mixed_sentence_split():
    r = C("Qualifications:\n- Epic experience required; Tableau a plus")
    assert r.outcome == FAIL and r.data["required"] == ["Epic"]


def test_req_duty_after_hire_ok():
    assert C("Responsibilities:\n- Maintain our Tableau dashboards\nRequirements:\n- 2+ years reporting experience").outcome == PASS


def test_req_duty_demanding_prior_expertise():
    assert C("Responsibilities:\n- Leverage your deep Epic expertise to lead the rollout").outcome == FAIL


def test_req_mention_only_ok():
    assert C("About us: we recently moved our records to Epic.").outcome == PASS


def test_req_example_wording_ambiguous():
    assert C("Requirements:\n- Experience with reporting tools such as Tableau or Power BI").outcome == UNCLEAR


def test_req_no_section_ambiguous():
    assert C("Experience with Epic.").outcome == UNCLEAR


def test_req_or_similar_ok():
    assert C("Requirements:\n- Tableau or similar reporting experience").outcome == PASS


def test_req_not_required_wording():
    assert C("Requirements:\n- No Epic experience required").outcome == PASS


# ---------------------------------------------------------------- certifications (example allowed list: Food Handler, CPR)
def CE(text, prof=None):
    return certs.evaluate(segment(text), URL, True, prof)


def test_allowed_cert_required_passes_and_gap():
    r = CE("Requirements:\n- ServSafe Food Handler certification required")
    assert r.outcome == PASS
    assert any("not documented" in g for g in r.data["fit_gaps"])


def test_other_cert_required_prehire_rejected():
    assert CE("Requirements:\n- ITIL certification required").outcome == FAIL
    assert CE("Requirements:\n- Must hold an active PMP certification").outcome == FAIL


def test_other_cert_preferred_ok():
    assert CE("Preferred:\n- ITIL certification").outcome == PASS
    assert CE("Requirements:\n- ITIL certification preferred").outcome == PASS


def test_post_hire_cert_allowed_with_deadline():
    r = CE("Requirements:\n- Must obtain ITIL certification within six months of starting")
    assert r.outcome == PASS
    ph = r.data["post_hire"][0]
    assert "ITIL certification" in ph["names"] and "six months" in ph["deadline"]


def test_ambiguous_cert_deadline_unclear():
    assert CE("Requirements:\n- Must obtain ITIL certification").outcome == UNCLEAR


def test_cert_alternatives_with_allowed_option():
    assert CE("Requirements:\n- Food Handler or ITIL certification required").outcome == PASS


def test_cert_and_combination_rejected():
    assert CE("Requirements:\n- Food Handler and ITIL certifications required").outcome == FAIL


def test_degree_not_cert():
    assert CE("Requirements:\n- Bachelor's degree required").outcome == PASS


def test_cert_or_equivalent_experience_ok():
    assert CE("Requirements:\n- ITIL certification or equivalent experience").outcome == PASS


def test_cert_benefit_not_requirement():
    assert CE("Benefits:\n- We pay for certifications, including ITIL").outcome == PASS


def test_unnamed_cert_needs_verification():
    assert CE("Requirements:\n- Relevant certification required").outcome == UNCLEAR


def test_post_hire_cert_does_not_override_requirement_rule_failure():
    from jobsearch.classify import decide
    t = ("Requirements:\n- Epic experience required\n"
         "- Must earn Epic Analyst certification within 6 months of hire")
    rc = C(t)
    rce = certs.evaluate(segment(t), URL, True)
    assert rce.outcome == PASS and rc.outcome == FAIL
    status, _ = decide([rc, rce], None, True)
    assert status == "Rejected"


def test_salary_between_and():
    assert salary.evaluate([], "Salary between $95,000 and $115,000 per year.", URL, True).outcome == PASS


def test_salary_not_merged_with_401k():
    r = salary.evaluate([], "Salary: $100,000 per year and 401k matching.", URL, True)
    assert r.outcome == PASS and r.data["min"] == 100_000


# ---------------------------------------------------------------- configurable criteria (settings.toml)
def test_settings_change_home_state_and_salary():
    from jobsearch import criteria
    try:
        criteria.configure({"home_state": "Ohio", "home_state_abbr": "OH", "single_min": 80000,
                            "range_lower_min": 70000, "range_upper_gt": 80000})
        assert L("Remote - US", "Fully remote. We are unable to hire in the following states: Ohio, Alaska.").outcome == FAIL
        assert L("Remote - US", "Fully remote. We are unable to hire in the following states: New Mexico.").outcome == PASS
        assert salary.evaluate_amounts(75000, 85000)[0] == PASS
    finally:
        criteria.configure(None)


def test_settings_allowed_cert_list():
    from jobsearch import criteria
    criteria.configure({"cert_filter_on": True, "allowed_prehire_certs": ["PMP"]})
    assert CE("Requirements:\n- Must hold an active PMP certification").outcome == PASS
    criteria.configure({"cert_filter_on": True, "allowed_prehire_certs": []})
    assert CE("Requirements:\n- PMP certification required").outcome == FAIL
    criteria.configure({"cert_filter_on": False})
    assert CE("Requirements:\n- PMP certification required").outcome == PASS


FILLED_SETTINGS = """
[job_titles]
titles = ["Salesforce Administrator", "Advancement Services"]

[where_you_live]
state = "Ohio"
state_abbreviation = "OH"

[salary]
minimum_single_salary = 80000

[filters]
description_must_mention = ["Salesforce"]
exclude_titles_containing = ["Developer"]
check_certifications = true
certifications_allowed_before_hire = ["Salesforce Administrator"]
"""


def _settings_root(tmp_path, text):
    import shutil
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    (tmp_path / "config").mkdir()
    shutil.copy(root / "jobsearch/advanced_settings.toml", tmp_path / "config/search_profile.toml")
    shutil.copy(root / "tests/fixtures/blank_experience.toml", tmp_path / "config/experience_profile.toml")
    (tmp_path / "config/settings.toml").write_text(text)
    return tmp_path


def test_blank_example_settings_filter_nothing(tmp_path):
    from pathlib import Path
    from jobsearch import pipeline, criteria
    from jobsearch.titles import TitleMatcher
    root = Path(__file__).resolve().parent.parent
    try:
        prof, exp = pipeline.load_config(_settings_root(tmp_path, (root / "tests/fixtures/blank_settings.toml").read_text()))
        K = criteria.CRITERIA
        assert prof["titles"] == [] and exp.get("skills", []) == []
        assert K["home_state"] == "" and K["single_min"] == 0 and not K["cert_filter_on"]
        assert K["custom_rules"] == [] and prof["title_rules"] == {"must_mention": [], "exclude_plain": []}
        assert salary.evaluate([], "Salary: $40,000 per year", URL, True).outcome == PASS
        assert CE("Requirements:\n- PMP certification required").outcome == PASS
        assert L("Remote - US", "We are unable to hire in the following states: New Mexico.").outcome == PASS
        assert TitleMatcher(prof).match("Salesforce Administrator", "Salesforce") is None  # no titles yet
    finally:
        criteria.configure(None)


def test_filled_settings_apply(tmp_path):
    from jobsearch import pipeline, criteria
    from jobsearch.titles import TitleMatcher
    try:
        prof, exp = pipeline.load_config(_settings_root(tmp_path, FILLED_SETTINGS))
        m = TitleMatcher(prof)
        assert m.match("Senior Salesforce Administrator", "Salesforce admin role") is not None
        assert m.match("Salesforce Developer", "Salesforce") is None
        assert m.match("Advancement Services Manager", "Salesforce donor database") is not None
        assert m.match("Advancement Services Manager", "donor database") is None   # must mention "Salesforce"
        assert prof["search"]["queries"] == ["Salesforce Administrator", "Advancement Services"]
        assert criteria.CRITERIA["home_state"] == "Ohio" and criteria.CRITERIA["single_min"] == 80000
        assert CE("Requirements:\n- PMP certification required").outcome == FAIL
    finally:
        criteria.configure(None)


# ---------------------------------------------------------------- user-defined requirement rules
def test_custom_rule_required_vs_preferred():
    from jobsearch import criteria
    from jobsearch.rules import custom
    try:
        criteria.configure({"custom_rules": [{"name": "Coding", "on": True, "terms": ["Apex"], "reject_when": "required"}]})
        fail = custom.evaluate_all(segment("Requirements:\n- 2+ years of Apex development experience required"), URL, True)
        ok = custom.evaluate_all(segment("Preferred Qualifications:\n- Apex experience is a plus"), URL, True)
        duty = custom.evaluate_all(segment("Responsibilities:\n- Maintain existing Apex triggers with our developers"), URL, True)
        assert fail[0].outcome == FAIL and ok[0].outcome == PASS and duty[0].outcome == PASS
    finally:
        criteria.configure(None)


def test_custom_rule_mentioned_and_off():
    from jobsearch import criteria
    from jobsearch.rules import custom
    try:
        criteria.configure({"custom_rules": [
            {"name": "Clearance", "on": True, "terms": ["security clearance"], "reject_when": "mentioned"},
            {"name": "Off rule", "on": False, "terms": ["Salesforce"], "reject_when": "mentioned"}]})
        res = custom.evaluate_all(segment("About us:\nCandidates with a security clearance are welcome. Salesforce role."), URL, True)
        assert len(res) == 1 and res[0].outcome == FAIL
    finally:
        criteria.configure(None)


def test_custom_term_not_matched_inside_words():
    from jobsearch.rules import custom
    r = custom.evaluate_rule({"name": "x", "terms": ["SQL"], "reject_when": "mentioned"}, segment("We use NoSQLite tools."), URL, True)
    assert r.outcome == PASS


def test_custom_rule_rejects_in_full_classification():
    from jobsearch import criteria
    from jobsearch.classify import evaluate_posting, decide
    from jobsearch.models import RawPosting, Verification
    try:
        criteria.configure({"custom_rules": [{"name": "Coding", "on": True, "terms": ["Apex"], "reject_when": "required"}]})
        p = RawPosting("t", URL, "Acme", "Salesforce Administrator",
                       "Remote anywhere in the United States.\nRequirements:\n- Apex experience required\n"
                       "Salary: $120,000 per year", authoritative=True)
        ev = evaluate_posting(p, {})
        status, reasons = decide(ev["rules"], Verification("verified"), True)
        assert status == "Rejected" and any('Your rule "Coding"' in r for r in reasons)
    finally:
        criteria.configure(None)


def test_fill_in_slots_with_yes_no(tmp_path):
    from pathlib import Path
    from jobsearch import pipeline, criteria
    root = Path(__file__).resolve().parent.parent
    text = (root / "tests/fixtures/blank_settings.toml").read_text()
    # what the questionnaire writes into the blank settings file
    text = text.replace('[job_titles]\ntitles = []', '[job_titles]\ntitles = ["Salesforce Administrator"]', 1)
    text = text.replace('name = "Words that rule out a job"\non = "no"\nterms = []',
                        'name = "Words that rule out a job"\non = "yes"\nterms = ["security clearance"]', 1)
    assert 'titles = ["Salesforce Administrator"]' in text and 'on = "yes"' in text
    try:
        prof, exp = pipeline.load_config(_settings_root(tmp_path, text))
        assert [t["plain_titles"] for t in prof["titles"]] == [["Salesforce Administrator"]]
        assert criteria.CRITERIA["custom_rules"] == [{"name": "Words that rule out a job", "on": True,
                                                      "terms": ["security clearance"], "reject_when": "required"}]
    finally:
        criteria.configure(None)


# ---------------------------------------------------------------- any certification, no built-in list needed
def test_unlisted_certification_named_from_posting():
    from jobsearch import criteria
    try:
        criteria.configure({"cert_filter_on": True, "allowed_prehire_certs": []})
        r = CE("Requirements:\n- ServSafe Food Handler certification required")
        assert r.outcome == FAIL and "ServSafe Food Handler certification" in r.reason
        r = CE("Requirements:\n- Must hold a valid food safety certification")
        assert r.outcome == FAIL and "food safety certification" in r.reason
        assert CE("Requirements:\n- Certified Nursing Assistant required").outcome == FAIL
        assert CE("Requirements:\n- Relevant certification required").outcome == UNCLEAR  # no name: not guessed
        r = CE("Requirements:\n- Must obtain ServSafe Food Handler certification within 30 days of hire")
        assert r.outcome == PASS and r.data["post_hire"]
        assert CE("Preferred:\n- ServSafe Food Handler certification").outcome == PASS
        criteria.configure({"cert_filter_on": True, "allowed_prehire_certs": ["Food Handler"]})
        assert CE("Requirements:\n- ServSafe Food Handler certification required").outcome == PASS
    finally:
        criteria.configure(None)


def test_requirement_rule_allows_after_hire():
    from jobsearch.rules import custom
    rule = {"name": "Food safety", "terms": ["ServSafe"], "reject_when": "required"}
    assert custom.evaluate_rule(rule, segment("Requirements:\n- ServSafe certification required"), URL, True).outcome == FAIL
    assert custom.evaluate_rule(rule, segment("Requirements:\n- Must obtain ServSafe within 30 days of hire"), URL, True).outcome == PASS


# ---------------------------------------------------------------- remote_only setting
def test_remote_only_on_by_default_rejects_onsite():
    assert L("Chicago, IL", "Join our team in our downtown office.").outcome == FAIL
    assert L("Denver, CO (Hybrid)", "Great team.").outcome == FAIL


def test_remote_only_off_keeps_in_state_jobs():
    from jobsearch import criteria
    criteria.configure({"remote_only": False, "home_state": "New Mexico", "home_state_abbr": "NM", "home_cities": ["Albuquerque"]})
    assert L("Albuquerque, NM", "Join our team in our downtown office.").outcome == PASS
    assert L("Santa Fe, New Mexico (Hybrid)", "Great team.").outcome == PASS
    assert L("Chicago, IL", "Join our team in our downtown office.").outcome == FAIL
    assert L("", "Join our team in our office.").outcome == UNCLEAR
    assert L("Remote - US", "This is a remote position.").outcome == PASS  # remote jobs still checked as before
    assert L("Remote - US", "Fully remote. We are unable to hire in the following states: New Mexico.").outcome == FAIL


def test_remote_only_off_without_state_keeps_any_location():
    from jobsearch import criteria
    criteria.configure({"remote_only": False})
    assert L("Chicago, IL", "Join our team in our downtown office.").outcome == PASS


def test_remote_only_setting_parsed():
    from jobsearch import criteria
    assert criteria.from_settings({"where_you_live": {"remote_only": "no"}})["remote_only"] is False
    assert criteria.from_settings({"where_you_live": {}})["remote_only"] is True


def test_older_title_groups_are_combined(tmp_path):
    from jobsearch import pipeline, criteria
    old = '[[job_titles.group]]\ntitles = ["Data Analyst"]\n\n[[job_titles.group]]\ntitles = ["Program Manager"]\n'
    try:
        prof, _ = pipeline.load_config(_settings_root(tmp_path, old))
        assert [t["plain_titles"] for t in prof["titles"]] == [["Data Analyst", "Program Manager"]]
        assert prof["search"]["queries"] == ["Data Analyst", "Program Manager"]
    finally:
        criteria.configure(None)


def test_title_words_match_singular_and_plural():
    from jobsearch.titles import TitleMatcher
    prof = {"titles": [{"track": "", "family": "Your job titles", "plain_titles": ["victims advocate", "Policy Analyst", "Case Manager"]}]}
    m = TitleMatcher(prof)
    assert m.match("Victim Advocate (Volunteer)") and m.match("Victims Advocate") and m.match("Victim Advocates")
    assert m.match("Senior Policies Analyst") and m.match("Case Managers")
    assert not m.match("Victimology Advocate") and not m.match("Policy Analytics Lead")
    assert not m.match("Advocate")                      # every word is still needed
