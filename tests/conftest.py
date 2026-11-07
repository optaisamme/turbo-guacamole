"""Test helpers. Everything here is a TEST FIXTURE; no network access is used."""
import json
import shutil
from pathlib import Path

import pytest

from jobsearch.http import Response

ROOT = Path(__file__).resolve().parent.parent


class FakeClient:
    """Maps URL -> (status, body, final_url, redirect_chain). Unknown URLs return 404."""

    def __init__(self, routes=None):
        self.routes = dict(routes or {})
        self.calls = []

    def set(self, url, body, status=200, final_url=None, chain=None):
        if not isinstance(body, str):
            body = json.dumps(body)
        self.routes[url] = (status, body, final_url or url, chain or ([] if not final_url else [final_url]))

    def get(self, url, headers=None, html=False):
        self.calls.append(url)
        if url in self.routes:
            st, body, final, chain = self.routes[url]
        else:
            st, body, final, chain = 404, "not found", url, []
        return Response(url, final, st, {}, body, chain)

    def post_json(self, url, payload, headers=None):
        return self.get(url)


def job_page(title, employer, apply=True, closed=False):
    body = f"<html><head><title>{title} - {employer}</title></head><body><h1>{title}</h1><p>{employer}</p>"
    if closed:
        body += "<p>This job is no longer accepting applications.</p>"
    if apply:
        body += "<form id='application_form'><input type='file' name='resume'></form><button>Submit application</button>"
    return body + "</body></html>"


GOOD_DESC = (
    "<p>About the role</p><p>This role is fully remote and open to candidates anywhere in the United States.</p>"
    "<h3>Responsibilities</h3><ul><li>Administer Salesforce, build reports and dashboards</li>"
    "<li>Work with our Service Cloud instance</li></ul>"
    "<h3>Requirements</h3><ul><li>3+ years of Salesforce administration experience</li>"
    "<li>Experience with Salesforce Nonprofit Success Pack (NPSP)</li></ul>"
    "<h3>Preferred Qualifications</h3><ul><li>Sales Cloud experience</li></ul>"
    "<p>The base salary range for this role is $95,000 - $115,000.</p>"
)


def gh_job(jid, title, desc=GOOD_DESC, board="acme", req=None):
    return {"id": jid, "title": title, "absolute_url": f"https://job-boards.greenhouse.io/{board}/jobs/{jid}",
            "location": {"name": "Remote - US"}, "content": desc, "updated_at": "2026-10-01T00:00:00Z",
            "requisition_id": req, "first_published": "2026-09-30T00:00:00Z"}


def setup_board(client, jobs, board="acme", employer="Acme Nonprofit"):
    client.set(f"https://boards-api.greenhouse.io/v1/boards/{board}", {"name": employer})
    client.set(f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true", {"jobs": jobs})
    for j in jobs:
        client.set(f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs/{j['id']}?pay_transparency=true", j)
        client.set(j["absolute_url"], job_page(j["title"], employer))


# Example settings used by the tests (test data only; the real settings file starts empty).
TEST_SETTINGS = """
[[job_titles.group]]
name = "Salesforce Administrator"
track = "A"
titles = ["Salesforce Administrator", "Salesforce Business Systems Analyst"]

[where_you_live]
state = "New Mexico"
state_abbreviation = "NM"

[salary]
minimum_single_salary = 100000
minimum_range_start = 90000
range_end_must_be_above = 100000
"""


@pytest.fixture(autouse=True)
def example_criteria():
    """Rule tests run against an example set of criteria (New Mexico, $100k salary rule,
    certification check on with Food Handler and CPR allowed)."""
    from jobsearch import criteria
    criteria.configure({"home_state": "New Mexico", "home_state_abbr": "NM", "home_cities": ["Albuquerque"],
                        "single_min": 100000, "range_lower_min": 90000, "range_upper_gt": 100000,
                        "cert_filter_on": True, "allowed_prehire_certs": ["Food Handler", "CPR"]})
    yield
    criteria.configure(None)


@pytest.fixture
def root(tmp_path):
    r = tmp_path / "proj"
    (r / "config").mkdir(parents=True)
    # tests use the built-in defaults and a fixed blank profile, never the user's EDIT_ME files
    shutil.copy(ROOT / "jobsearch/advanced_settings.toml", r / "config/search_profile.toml")
    shutil.copy(ROOT / "tests/fixtures/blank_experience.toml", r / "config/experience_profile.toml")
    (r / "config/settings.toml").write_text(TEST_SETTINGS)
    (r / "data").mkdir()
    (r / "data/boards.json").write_text(json.dumps({"greenhouse": [{"board": "acme", "employer": "Acme Nonprofit"}]}))
    return r
