from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

from ..http import HttpClient, AccessBlocked
from ..models import RawPosting

log = logging.getLogger("jobsearch.adapters")

JOB_BOARD_HOSTS = (
    "linkedin.com", "indeed.com", "glassdoor.com", "ziprecruiter.com", "builtin.com", "monster.com",
    "simplyhired.com", "remotive.com", "remoteok.com", "himalayas.app", "adzuna.com", "jooble.org",
    "themuse.com", "wellfound.com", "dice.com", "careerbuilder.com", "idealist.org", "flexjobs.com",
    "weworkremotely.com", "workingnomads.com", "jobgether.com", "talent.com", "lensa.com", "jobright.ai",
    "builtinchicago.org", "builtinboston.com", "builtinla.com", "builtinnyc.com", "virtualvocations.com",
    "impactpool.org", "edtech.com", "career.io", "themomproject.com", "remotejobs.org", "jobs.philanthropy.com",
    "remoterocketship.com", "dailyremote.com", "cutshort.io",
)
ATS_HOSTS = (
    "greenhouse.io", "lever.co", "ashbyhq.com", "smartrecruiters.com", "myworkdayjobs.com", "workday.com",
    "icims.com", "workable.com", "bamboohr.com", "jazzhr.com", "applytojob.com", "recruitee.com",
    "jobvite.com", "taleo.net", "oraclecloud.com", "successfactors.com", "paylocity.com", "paycomonline.net",
    "ultipro.com", "ukg.com", "rippling.com", "dayforcehcm.com", "adp.com", "breezy.hr", "teamtailor.com",
    "pinpointhq.com", "usajobs.gov",
)


def host_of(url: str | None) -> str:
    import urllib.parse
    return (urllib.parse.urlsplit(url or "").hostname or "").lower()


def _host_in(url: str | None, hosts: tuple[str, ...]) -> bool:
    h = host_of(url)
    return bool(h) and any(h == x or h.endswith("." + x) for x in hosts)


def is_job_board(url: str | None) -> bool:
    return _host_in(url, JOB_BOARD_HOSTS)


# Never fetched: terms prohibit automated access, login/paywall required, or no permitted access path.
NO_FETCH_HOSTS = (
    "linkedin.com", "indeed.com", "glassdoor.com", "ziprecruiter.com", "builtin.com", "builtinchicago.org",
    "builtinboston.com", "builtinla.com", "builtinnyc.com", "builtinsf.com", "builtincolorado.com",
    "builtinaustin.com", "builtinseattle.com", "flexjobs.com", "virtualvocations.com",
    # terms forbid automated access, or the site blocks it, or it requires signing in
    "idealist.org", "diversityjobs.com", "techjobsforgood.com", "greenjobsboard.us", "remote100k.com",
    "conservationjobboard.com", "higheredjobs.com", "joinhandshake.com",
)


def is_no_fetch(url: str | None) -> bool:
    return _host_in(url, NO_FETCH_HOSTS)


def is_ats(url: str | None) -> bool:
    return _host_in(url, ATS_HOSTS)


@dataclass
class SourceStatus:
    name: str
    state: str = "not_run"  # ok | blocked | needs_credentials | error | not_run | disabled
    detail: str = ""
    fetched: int = 0
    matched: int = 0          # matched your job titles
    skills_matched: int = 0   # matched by skills-based matching (other titles)
    errors: list[str] = field(default_factory=list)
    searches: list[dict] = field(default_factory=list)   # web search: each search's words and settings
    limit: str = ""   # how much of the site this search read (shown in the report)
    boards_failed: list[str] = field(default_factory=list)   # careers pages that could not be read ("ats:board")


class Adapter:
    name = "base"
    kind = "aggregator"          # ats | aggregator | official | leads
    authoritative = False
    env_keys: tuple[str, ...] = ()

    def __init__(self, client: HttpClient, ctx: dict):
        self.client = client
        self.ctx = ctx

    def missing_credentials(self) -> list[str]:
        return [k for k in self.env_keys if not os.environ.get(k)]

    def run(self, status: SourceStatus) -> list[RawPosting]:
        miss = self.missing_credentials()
        if miss:
            status.state = "needs_credentials"
            status.detail = "missing env: " + ", ".join(miss)
            return []
        try:
            out = self.search(status)
            if status.state == "not_run":
                status.state = "error" if (status.errors and not out and all("HTTP" in e for e in status.errors)) else "ok"
                if status.state == "error":
                    status.detail = "; ".join(status.errors[:3])
            status.fetched = status.fetched or len(out)
            return out
        except AccessBlocked as e:
            status.state = "blocked"
            status.detail = str(e)
            return []
        except Exception as e:  # noqa: BLE001 — adapter failures must not stop the run
            log.exception("adapter %s failed", self.name)
            status.state = "error"
            status.detail = f"{type(e).__name__}: {e}"
            return []

    def search(self, status: SourceStatus) -> list[RawPosting]:
        raise NotImplementedError
