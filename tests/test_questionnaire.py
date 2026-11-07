"""The setup questionnaire: GitHub issue-form answers -> settings files."""
import importlib.util
import pathlib
import shutil
import tomllib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("q", ROOT / "scripts/apply_questionnaire.py")
q = importlib.util.module_from_spec(spec)
spec.loader.exec_module(q)

SETTINGS = (ROOT / "JOB_SEARCH_SETTINGS/job_search_settings.toml").read_text()
EXPERIENCE = (ROOT / "JOB_SEARCH_SETTINGS/my_experience.toml").read_text()


def form(**answers):
    """Issue text the way GitHub writes a submitted issue form."""
    by_key = {v: k for k, v in q.LABELS.items()}
    parts = []
    for key in q.LABELS.values():
        parts += [f"### {by_key[key]}", "", answers.get(key, "_No response_"), ""]
    return "\n".join(parts)


def test_form_labels_match_the_script():
    import yaml
    f = yaml.safe_load(open(ROOT / ".github/ISSUE_TEMPLATE/job_search_setup.yml"))
    labels = [b["attributes"]["label"] for b in f["body"] if b["type"] != "markdown"]
    assert set(labels) == set(q.LABELS) and len(labels) == len(q.LABELS)


def test_full_answers_update_both_files():
    body = form(titles_1="Program Manager\nProgram Director\nData Analyst",
                remote_only="No, also hybrid and on-site jobs in my state", state="New Mexico", state_abbreviation="nm",
                cities="Albuquerque\nSanta Fe", min_single="$100,000", min_range_start="90000", range_end_above="100000",
                skip_titles="Intern\nMajor Gifts", cert_check="Yes", certs_allowed="CPR",
                companies="https://job-boards.greenhouse.io/examplecompany",
                skills_on="On", skills_keywords="Salesforce\nNPSP", skills_rule="At least one keyword must appear",
                skills_min="3", my_skills="Reports and dashboards: report, dashboard\nData cleanup: data quality, dedup",
                years="4", certs_held="CPR", weekly="On", weekly_day="Monday", run_now="Yes")
    answers = q.parse_issue_body(body)
    s, e, changes, extras = q.apply(answers, SETTINGS, EXPERIENCE)
    st, ex = tomllib.loads(s), tomllib.loads(e)
    assert st["job_titles"]["titles"] == ["Program Manager", "Program Director", "Data Analyst"]
    w = st["where_you_live"]
    assert (w["remote_only"], w["state"], w["state_abbreviation"], w["cities"]) == ("no", "New Mexico", "NM", ["Albuquerque", "Santa Fe"])
    assert st["salary"] == {"minimum_single_salary": 100000, "minimum_range_start": 90000, "range_end_must_be_above": 100000}
    assert st["filters"]["exclude_titles_containing"] == ["Intern", "Major Gifts"] and st["filters"]["check_certifications"] == "yes"
    assert st["companies_to_watch"]["job_page_links"] == ["https://job-boards.greenhouse.io/examplecompany"]
    assert st["skills_matching"] == {"on": "yes", "required_keywords": ["Salesforce", "NPSP"],
                                     "required_keywords_rule": "any", "minimum_skills_matched": 3}
    assert st["automatic_weekly_search"] == {"on": "yes", "day": "Monday"}
    assert ex["tenure"]["years_of_experience"] == 4 and ex["tenure"]["certifications_held"] == ["CPR"]
    assert [k["name"] for k in ex["skills"]] == ["Reports and dashboards", "Data cleanup"]
    assert ex["skills"][1]["keywords"] == ["data quality", "dedup"]
    assert extras["run_now"] and len(changes) >= 15
    assert s.startswith("# YOUR JOB SEARCH SETTINGS")      # the note at the top is kept


def test_blank_keeps_and_none_clears():
    s1, e1, _, _ = q.apply(q.parse_issue_body(form(titles_1="Program Manager", skip_titles="Intern", state="Utah")), SETTINGS, EXPERIENCE)
    s2, _, changes, _ = q.apply(q.parse_issue_body(form(titles_1="Program Manager", skip_titles="none")), s1, e1)
    st = tomllib.loads(s2)
    assert st["where_you_live"]["state"] == "Utah"                     # blank: kept
    assert st["filters"]["exclude_titles_containing"] == []            # none: cleared
    assert any(c.startswith("Title words to skip: cleared") for c in changes)


@pytest.mark.parametrize("answers,problem", [
    ({"titles_1": "X", "min_single": "about 90k"}, "type a number only"),
    ({"titles_1": "X", "state_abbreviation": "New Mexico"}, "two letters"),
    ({"titles_1": "X", "companies": "https://www.example.com/careers"}, "not company job pages"),
])
def test_bad_answers_are_explained(answers, problem):
    with pytest.raises(q.AnswerProblem, match=problem):
        q.apply(q.parse_issue_body(form(**answers)), SETTINGS, EXPERIENCE)


def test_answers_are_only_ever_quoted_values():
    s, _, _, _ = q.apply(q.parse_issue_body(form(titles_1='Analyst"]\n[evil]\nx = 1')), SETTINGS, EXPERIENCE)
    st = tomllib.loads(s)
    assert "evil" not in st and st["job_titles"]["titles"] == ['Analyst"]', "[evil]", "x = 1"]


def test_only_owner_or_collaborator_can_change_settings(tmp_path, monkeypatch):
    for d in ("JOB_SEARCH_SETTINGS", "jobsearch", "scripts"):
        shutil.copytree(ROOT / d, tmp_path / d)
    monkeypatch.setattr(q, "ROOT", tmp_path)
    monkeypatch.setattr(q, "SETTINGS", tmp_path / "JOB_SEARCH_SETTINGS/job_search_settings.toml")
    monkeypatch.setattr(q, "EXPERIENCE", tmp_path / "JOB_SEARCH_SETTINGS/my_experience.toml")
    monkeypatch.setattr(q, "OUT", tmp_path / "out")
    monkeypatch.setenv("ISSUE_BODY", form(titles_1="Program Manager"))
    monkeypatch.setenv("AUTHOR_ASSOCIATION", "NONE")
    q.main()
    assert "can only be changed by its owner" in (tmp_path / "out/questionnaire_reply.md").read_text()
    assert q.SETTINGS.read_text() == SETTINGS
    monkeypatch.setenv("AUTHOR_ASSOCIATION", "OWNER")
    q.main()
    assert (tmp_path / "out/questionnaire_ok.txt").read_text() == "yes"
    assert tomllib.loads(q.SETTINGS.read_text())["job_titles"]["titles"] == ["Program Manager"]
    assert "Your settings are saved" in (tmp_path / "out/questionnaire_reply.md").read_text()
    monkeypatch.setenv("IS_TEMPLATE", "true")                      # the shared template is never changed
    before = q.SETTINGS.read_text()
    q.main()
    assert q.SETTINGS.read_text() == before and "shared template" in (tmp_path / "out/questionnaire_reply.md").read_text()
    monkeypatch.setenv("IS_TEMPLATE", "")
    monkeypatch.setenv("GITHUB_REPOSITORY", "optaisamme/job-search-automation-publicuse")
    q.main()
    assert q.SETTINGS.read_text() == before


def test_text_box_answers_in_code_blocks_are_read():
    body = "### Job titles to look for\n\n```text\nProgram Manager\nProgram Director\n```\n\n### Your state\n\nUtah\n"
    a = q.parse_issue_body(body)
    assert a["titles_1"] == "Program Manager\nProgram Director" and a["state"] == "Utah"


def test_search_size_choice_is_saved_and_recommended_is_the_default():
    import yaml
    f = yaml.safe_load(open(ROOT / ".github/ISSUE_TEMPLATE/job_search_setup.yml"))
    dd = next(b for b in f["body"] if b["attributes"].get("label") == "How many jobs to look through")
    # no preselected choice: "None" keeps the current choice, and a new copy starts with Recommended
    assert "default" not in dd["attributes"] and dd["attributes"]["options"][0].startswith("Recommended")
    assert tomllib.loads(q.build_settings(q.parse_issue_body(form(titles_1="X")))[0])["search_size"] == q.SEARCH_SIZES["recommended"]
    assert "recommend keeping" in yaml.safe_dump(f).lower()
    # the recommended choice equals the numbers the settings file starts with and the search uses by default
    from jobsearch.adapters.aggregators import SEARCH_SIZE_DEFAULTS
    assert q.SEARCH_SIZES["recommended"] == SEARCH_SIZE_DEFAULTS == tomllib.loads(SETTINGS)["search_size"]
    s, _, changes, _ = q.apply(q.parse_issue_body(form(titles_1="Data Analyst", search_size=dd["attributes"]["options"][1])),
                               SETTINGS, EXPERIENCE)
    assert tomllib.loads(s)["search_size"] == q.SEARCH_SIZES["fewer"]
    assert any(c.startswith("How many jobs to look through: Fewer") for c in changes)
    # an older copy without section 10 gets it added
    old = SETTINGS[:SETTINGS.index("[search_size]")]
    s, _, _, _ = q.apply(q.parse_issue_body(form(search_size="More (slower)")), old, EXPERIENCE)
    assert tomllib.loads(s)["search_size"] == q.SEARCH_SIZES["more"]
    with pytest.raises(q.AnswerProblem):
        q.apply(q.parse_issue_body(form(search_size="Huge")), SETTINGS, EXPERIENCE)


def test_search_size_numbers_are_kept_within_limits():
    from jobsearch.adapters.aggregators import search_size, _pages
    sz = search_size({"search_size": {"newest_jobs_per_site": 999999, "jobs_per_search_word": 1, "jobicy_max_jobs": "x"}})
    assert sz == {"newest_jobs_per_site": 5000, "jobs_per_search_word": 20, "jobicy_max_jobs": 5000}
    assert (_pages(1000, 20), _pages(30, 20), _pages(5000, 100)) == (50, 2, 50)


def test_older_question_labels_are_still_read():
    a = q.parse_issue_body("### Skills-based matching\n\nOn\n\n### More job titles: a second kind of job\n\nData Analyst\n")
    assert a == {"skills_on": "On"}          # old title boxes are ignored; one box for all titles now


def test_unanswered_dropdowns_keep_settings():
    """GitHub writes "None" for a dropdown left unanswered (seen in a real copy on 2026-11-07)."""
    import yaml
    f = yaml.safe_load(open(ROOT / ".github/ISSUE_TEMPLATE/job_search_setup.yml"))
    dropdown_keys = {q.LABELS[b["attributes"]["label"]] for b in f["body"] if b["type"] == "dropdown"}
    assert dropdown_keys == q.DROPDOWNS
    body = form(titles_1="Abortion Advocate\nDomestic Violence Advocate", remote_only="None", cert_check="None",
                skills_on="None", skills_rule="None", weekly="None", weekly_day="None", run_now="Yes")
    s, _, changes, extras = q.apply(q.parse_issue_body(body), SETTINGS, EXPERIENCE)
    st, before = tomllib.loads(s), tomllib.loads(SETTINGS)
    assert st["job_titles"]["titles"] == ["Abortion Advocate", "Domestic Violence Advocate"]
    assert st["where_you_live"] == before["where_you_live"] and st["automatic_weekly_search"] == before["automatic_weekly_search"]
    assert st["filters"]["check_certifications"] == before["filters"]["check_certifications"]
    assert extras["run_now"]
    # typing "none" in a text box still clears a list
    s2, _, _, _ = q.apply(q.parse_issue_body(form(skip_titles="none")), s, EXPERIENCE)
    assert tomllib.loads(s2)["filters"]["exclude_titles_containing"] == []


def test_start_search_links_to_the_running_search(tmp_path, monkeypatch):
    from datetime import datetime, timezone
    monkeypatch.setattr(q, "OUT", tmp_path)
    monkeypatch.setenv("GITHUB_REPOSITORY", "someone/my-job-search")
    calls = []

    def fake_api(method, path, payload=None):
        calls.append((method, path, payload))
        if method == "POST":
            return {}
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        return {"workflow_runs": [{"created_at": now, "html_url": "https://github.com/someone/my-job-search/actions/runs/7"}]}
    monkeypatch.setattr(q, "_api", fake_api)
    assert q.start_search(wait_seconds=0) == 0
    assert calls[0] == ("POST", "actions/workflows/run_job_search.yml/dispatches", {"ref": "main"})
    msg = (tmp_path / "questionnaire_search.md").read_text()
    assert msg.startswith("## Your job search is running")
    assert "**[Click here to watch it](https://github.com/someone/my-job-search/actions/runs/7)**" in msg
    assert "**Actions** tab" in msg


def test_start_search_failure_says_why_and_how_to_start_it(tmp_path, monkeypatch):
    import io, urllib.error
    monkeypatch.setattr(q, "OUT", tmp_path)
    monkeypatch.setenv("GITHUB_REPOSITORY", "someone/my-job-search")

    def fake_api(method, path, payload=None):
        raise urllib.error.HTTPError("u", 403, "Forbidden", {}, io.BytesIO(b'{"message": "Resource not accessible by integration"}'))
    monkeypatch.setattr(q, "_api", fake_api)
    assert q.start_search(wait_seconds=0) == 1
    msg = (tmp_path / "questionnaire_search.md").read_text()
    assert "could not be started automatically" in msg and "403: Resource not accessible by integration" in msg
    assert "https://github.com/someone/my-job-search/actions/workflows/run_job_search.yml" in msg


def test_workflows_point_at_files_that_exist():
    import re
    wf = ROOT / ".github/workflows"
    q_wf = (wf / "apply_setup_questionnaire.yml").read_text()
    assert (wf / q.WORKFLOW_FILE).exists()
    assert q_wf.index("name: Start a search") < q_wf.index("name: Reply on the issue")   # the reply can link to it
    for f in wf.glob("*.yml"):
        for script in re.findall(r"python (scripts/\S+\.py)", f.read_text()):
            assert (ROOT / script).exists(), (f.name, script)
    for doc in ("README.md", "HOW_TO_RUN_A_SEARCH_NOW.md", "JOB_SEARCH_RESULTS/README.md", "jobsearch/results_folder.py"):
        for name in re.findall(r"actions/workflows/([\w.-]+\.yml)", (ROOT / doc).read_text()):
            assert (wf / name).exists(), (doc, name)


def test_reply_starts_with_the_search_link(tmp_path, monkeypatch):
    monkeypatch.setattr(q, "OUT", tmp_path)
    (tmp_path / "questionnaire_reply.md").write_text("**Your settings are saved.**\n")
    (tmp_path / "questionnaire_ok.txt").write_text("yes")
    (tmp_path / "questionnaire_search.md").write_text("## Your job search is running\n\n**[Click here to watch it](u)**.\n")
    sent = []
    monkeypatch.setenv("GITHUB_REPOSITORY", "someone/my-job-search")
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    monkeypatch.setenv("ISSUE_NUMBER", "1")
    import urllib.request, io, json as _json

    class R(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *a): return False
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=30: (sent.append(req), R(b"{}"))[1])
    assert q.post_reply() == 0
    body = _json.loads(sent[0].data)["body"]
    assert body.startswith("## Your job search is running") and body.index("Click here to watch it") < body.index("Your settings are saved")


def test_words_that_rule_out_a_job():
    body = form(rule_words="security clearance\nTS/SCI", rule_when="Mentioned anywhere in the posting")
    s, _, changes, _ = q.apply(q.parse_issue_body(body), SETTINGS, EXPERIENCE)
    r = tomllib.loads(s)["filters"]["requirement_rule"][0]
    assert r == {"name": "Words that rule out a job", "on": "yes", "terms": ["security clearance", "TS/SCI"], "reject_when": "mentioned"}
    s2, _, _, _ = q.apply(q.parse_issue_body(form(rule_words="none")), s, EXPERIENCE)
    assert tomllib.loads(s2)["filters"]["requirement_rule"][0]["on"] == "no"     # cleared: off


def test_settings_file_has_no_editing_instructions():
    text = (ROOT / "JOB_SEARCH_SETTINGS/job_search_settings.toml").read_text()
    assert "questionnaire" in text and "pencil" not in text and "group" not in text and "EXAMPLE" not in text
    assert len([l for l in text.splitlines() if l.startswith("#")]) <= 6


def test_form_explains_the_web_search_key_but_never_asks_for_it():
    import yaml
    f = yaml.safe_load(open(ROOT / ".github/ISSUE_TEMPLATE/job_search_setup.yml"))
    text = "\n".join(b["attributes"].get("value", "") for b in f["body"] if b["type"] == "markdown")
    assert "TAVILY_API_KEY" in text and "New repository secret" in text and "don't paste your key into this form" in text
    for b in f["body"]:
        if b["type"] in ("input", "textarea"):
            assert "key" not in b["attributes"]["label"].lower() or "keyword" in b["attributes"]["label"].lower()


def test_linkedin_style_skills_and_quoted_keywords_work():
    assert q.as_list('Salesforce, "data analysis", “program management”') == ["Salesforce", "data analysis", "program management"]
    e, names = q.replace_skills(EXPERIENCE, "Customer Relationship Management (CRM)\nData Collection\n\"Community Engagement\"")
    skills = tomllib.loads(e)["skills"]
    assert names == ["Customer Relationship Management (CRM)", "Data Collection", "Community Engagement"]
    assert skills[0]["keywords"] == ["customer relationship management", "crm"]
    assert skills[1]["keywords"] == ["data collection"] and skills[2]["keywords"] == ["community engagement"]
    from jobsearch.skills_match import _hit
    assert _hit("nonprofit organizations", "experience with a nonprofit organization") and _hit("crm", "our crm is salesforce")


def test_each_questionnaire_replaces_all_settings():
    """Seen in a real copy: a second questionnaire left 'Words the job description must mention' blank,
    but the first answer ('nsps') stayed and kept removing every job."""
    first, _, _, _ = q.build_settings(q.parse_issue_body(form(titles_1="Salesforce Administrator", must_mention="nsps",
                                                              skip_titles="cloud", min_single="75000")))
    assert tomllib.loads(first)["filters"]["description_must_mention"] == ["nsps"]
    second, _, _, _ = q.build_settings(q.parse_issue_body(form(titles_1="Salesforce Administrator", min_single="50000")))
    st = tomllib.loads(second)
    assert st["filters"]["description_must_mention"] == [] and st["filters"]["exclude_titles_containing"] == []
    assert st["salary"]["minimum_single_salary"] == 50000 and st["job_titles"]["titles"] == ["Salesforce Administrator"]


def test_starting_settings_match_a_new_copy():
    if tomllib.loads((ROOT / "JOB_SEARCH_SETTINGS/job_search_settings.toml").read_text())["job_titles"]["titles"]:
        pytest.skip("this copy already has settings")
    for name in ("job_search_settings.toml", "my_experience.toml"):
        assert tomllib.loads((ROOT / "jobsearch/defaults" / name).read_text()) == \
               tomllib.loads((ROOT / "JOB_SEARCH_SETTINGS" / name).read_text()), name


def test_privacy_warning_comes_before_the_first_question():
    import yaml
    f = yaml.safe_load(open(ROOT / ".github/ISSUE_TEMPLATE/job_search_setup.yml"))
    first_question = next(i for i, b in enumerate(f["body"]) if b["type"] != "markdown")
    before = "\n".join(b["attributes"]["value"] for b in f["body"][:first_question])
    assert "### Your privacy" in before and "If your copy is\n        public" not in before
    assert "anyone can read them" in before and "Do not enter passwords" in before and "Tavily" in before


def test_change_link_fills_in_current_answers_and_round_trips(tmp_path):
    """'click here to change these settings' opens the questionnaire with the current answers; submitting it
    unchanged (dropdowns left on None, which a link cannot fill in) keeps every setting."""
    import yaml
    from urllib.parse import urlsplit, parse_qs
    import sys
    sys.path.insert(0, str(ROOT))
    from jobsearch import settings_summary
    full = form(titles_1="Program Manager\nCase Manager", remote_only="No, also hybrid and on-site jobs in my state",
                state="New Mexico", state_abbreviation="NM", cities="Albuquerque", min_single="70000", min_range_start="65000",
                skip_titles="Intern", rule_words="security clearance", rule_when="Mentioned anywhere in the posting",
                must_mention="nonprofit", cert_check="Yes", certs_allowed="CPR", skip_employers="Acme Staffing",
                my_skills="Customer Relationship Management (CRM)\nData: data entry, spreadsheets", years="4", certs_held="CPR",
                companies="https://job-boards.greenhouse.io/examplecompany", skills_on="On", skills_min="2",
                skills_keywords="Salesforce", skills_rule="At least one keyword must appear", search_size="More (slower)",
                weekly="On", weekly_day="Tuesday")
    s, e, _, _ = q.build_settings(q.parse_issue_body(full))
    (tmp_path / "JOB_SEARCH_SETTINGS").mkdir()
    (tmp_path / "JOB_SEARCH_SETTINGS/job_search_settings.toml").write_text(s)
    (tmp_path / "JOB_SEARCH_SETTINGS/my_experience.toml").write_text(e)
    link = settings_summary.change_link(tmp_path)
    assert link.startswith("../../../issues/new?template=job_search_setup.yml&title=Job+search+setup&")
    filled = {k: v[0] for k, v in parse_qs(urlsplit(link).query).items() if k not in ("template", "title")}
    ids = {b["id"]: b for b in yaml.safe_load(open(ROOT / ".github/ISSUE_TEMPLATE/job_search_setup.yml"))["body"] if "id" in b}
    assert set(filled) <= {i for i, b in ids.items() if b["type"] in ("input", "textarea")}   # only text questions
    # what GitHub writes when that form is submitted unchanged
    by_key = {v: k for k, v in q.LABELS.items()}
    parts = []
    for key in q.LABELS.values():
        v = filled.get(key, "")
        if ids[key]["type"] == "dropdown":
            v = "None"
        elif ids[key]["type"] == "textarea":
            v = "```text\n" + v + "\n```"
        parts += [f"### {by_key[key]}", "", v or "_No response_", ""]
    s2, e2, _, extras = q.build_settings(q.parse_issue_body("\n".join(parts)), s)
    assert tomllib.loads(s2) == tomllib.loads(s) and tomllib.loads(e2)["skills"] == tomllib.loads(e)["skills"]
