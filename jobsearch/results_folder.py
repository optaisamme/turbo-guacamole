"""The JOB_SEARCH_RESULTS folder: where people read their results.

  JOB_SEARCH_RESULTS/README.md                       shown automatically when the folder is opened:
                                                     current matches, near misses, and a list of all reports
  JOB_SEARCH_RESULTS/REPORT_001_2026-11-06_13-47_UTC.md   one report per search, numbered, with date and time

Rewritten after every search, whether or not anything new was found.
"""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

FOLDER = "JOB_SEARCH_RESULTS"
# Relative to JOB_SEARCH_RESULTS/README.md on github.com: opens this copy's "Run job search" page.
RUN_LINK = "../../../actions/workflows/run_job_search.yml"


def _j(v, default):
    try:
        return json.loads(v) if v else default
    except (TypeError, ValueError):
        return default


def _reason(rules, name):
    for r in rules or []:
        if r.get("rule") == name:
            return r.get("reason", "")
    return ""


def _cell(s) -> str:
    return str(s or "").replace("|", "/").replace("\n", " ")


def report_name(run_id: int, finished_at: str) -> str:
    stamp = (finished_at or "")[:16].replace("T", "_").replace(":", "-")
    return f"REPORT_{run_id:03d}_{stamp}_UTC.md"


def _current_sections(db) -> list[str]:
    from .lookup import needs_employer_posting
    qualified = db.execute("SELECT * FROM jobs WHERE status='Qualified' AND closed_at IS NULL ORDER BY first_seen DESC").fetchall()
    from .report import near_misses
    near = near_misses(db)
    look = needs_employer_posting(db)
    from .report import _label, _salary_cell, near_miss_reason, unchecked_note, near_miss_link
    from .skills_match import SKILLS_FAMILY

    def match_rows(rows, skills=False):
        from .report import skills_matched_cell
        out = ["| Job | Company |" + (" Skills matched |" if skills else "") + " Salary | Location | Apply | First found |",
               "|---|---|" + ("---|" if skills else "") + "---|---|---|---|"]
        for r in rows:
            ver = _j(r["verification"], {}) or {}
            link = ver.get("apply_url") or r["apply_url"] or r["canonical_url"] or ""
            route = (" (third-party site, link not checked)" if (ver.get("detail") or "").startswith("Listed in the Himalayas job feed")
                     else " (third-party site)" if ver.get("apply_route") == "third_party" else "")
            sk = f"{_cell(skills_matched_cell(r))} | " if skills else ""
            out.append(f"| {_cell(_label(r))} | {_cell(r['employer'])} | {sk}{_cell(_salary_cell(r))} | "
                       f"{_cell(r['location_text'])} | [Apply]({link}){route} | {(r['first_seen'] or '')[:10]} |")
        return out

    by_title = [r for r in qualified if r["family"] != SKILLS_FAMILY]
    by_skills = [r for r in qualified if r["family"] == SKILLS_FAMILY]
    L = [f"## Jobs that match all your rules ({len(qualified)})", ""]
    L += match_rows(by_title) if by_title else ["None right now." if not by_skills else "None with your job titles right now."]
    if by_skills:
        L += ["", f"### Matches by skills (other titles) ({len(by_skills)})", "",
              "Their title is not one of yours, but the listing has your required keywords and enough of your skills.", ""]
        L += match_rows(by_skills, skills=True)
    L += ["", f"## Near Misses: Jobs that did not match one of your rules ({len(near)})", ""]
    if near:
        from .sources_summary import found_by
        L += ["| Job | Company | Why it did not match | Link | Found by | First found |", "|---|---|---|---|---|---|"]
        for r in near:
            why = "; ".join(near_miss_reason(x) for x in (_j(r["reasons"], []) or [])) + unchecked_note(r)
            L.append(f"| {_cell(_label(r))} | {_cell(r['employer'])} | {_cell(why)} | "
                     f"[Link]({near_miss_link(r)}) | {_cell(found_by(r))} | {(r['first_seen'] or '')[:10]} |")
    else:
        L.append("None right now.")
    L += ["", f"## Jobs whose link could not be checked automatically ({len(look)})", "",
          "These come from job sites that block automated checks, or only from a third-party site. "
          "Use **Find employer posting** to search for the same job on the employer's own website, then apply there.", ""]
    if look:
        L += ["| Job | Company | Listing found | Find the employer's posting | First found |", "|---|---|---|---|---|"]
        for x in look:
            L.append(f"| {_cell(x['title'])} | {_cell(x['employer'])} | [Listing]({x['current_link']}) | "
                     f"[Find employer posting]({x['search_link']}) | {x.get('first_seen', '')} |")
    else:
        L.append("None right now.")
    shown = {r["id"] for r in near} | {x["job_id"] for x in look}
    check = [r for r in db.execute("SELECT * FROM jobs WHERE status='Needs Verification' AND closed_at IS NULL "
                                   "ORDER BY first_seen DESC").fetchall() if r["id"] not in shown]
    L += ["", f"## Jobs that need checking ({len(check)})", "",
          "These jobs failed none of your rules, but the posting does not say enough to be sure "
          "(for example, no salary listed). Open the posting and check the items listed.", ""]
    if check:
        L += ["| Job | Company | What to check | Link |", "|---|---|---|---|"]
        for r in check:
            ver = _j(r["verification"], {}) or {}
            link = ver.get("apply_url") or r["apply_url"] or r["canonical_url"] or ""
            L.append(f"| {_cell(_label(r))} | {_cell(r['employer'])} | {_cell('; '.join(near_miss_reason(x) for x in _j(r['reasons'], [])))} | [Link]({link}) |")
    else:
        L.append("None right now.")
    return L


def _manual_boards(root: Path) -> list[str]:
    """Search links for popular boards, built from the job titles in settings."""
    from . import manual_boards, pipeline
    from .criteria import CRITERIA
    try:
        prof, _ = pipeline.load_config(root)
    except Exception:  # noqa: BLE001 — settings problems are reported by the settings check
        return []
    titles = [t for g in prof.get("titles", []) for t in (g.get("plain_titles") or [])]   # every job title
    return manual_boards.section(titles, CRITERIA.get("remote_only", True), CRITERIA.get("home_state", ""))


def _companies(root: Path) -> list[str]:
    """Companies whose careers pages are read every search: found automatically, and added in the questionnaire."""
    from . import pipeline
    names = {"greenhouse": "Greenhouse", "lever": "Lever", "ashby": "Ashby", "smartrecruiters": "SmartRecruiters"}
    try:
        prof, _ = pipeline.load_config(root)
        added, _bad = pipeline.watched_boards(prof)
    except Exception:  # noqa: BLE001
        added = []
    found = pipeline.load_boards(root)
    from .pipeline import MAX_SEARCHES_WITHOUT_MATCH as M
    rows = [(b.get("employer") or b["board"], names.get(ats, ats), "found automatically",
             f"{int(b.get('searches_without_match', 0))} of {M}") for ats, lst in found.items() for b in lst]
    rows += [(b["board"], names.get(b["ats"], b["ats"]), "you added it", "never removed") for b in added]
    L = ["## Companies checked every search", "",
         "The tool reads every open job on these companies' careers pages each search. Companies are found "
         "automatically when a matching job leads to a careers page on Greenhouse, Lever, Ashby or SmartRecruiters "
         "(through a job site or the web search), or you can add them under \"Companies to watch\" in the questionnaire.", "",
         f"A company found automatically is removed after more than {M} searches in a row with no job matching "
         "your job titles or skills. It is added again if a matching job shows up later. Companies you added are never removed.", ""]
    if not rows:
        return L + ["None yet.", ""]
    L += ["| Company | Careers page runs on | How it was added | Searches in a row without a matching job |",
          "|---|---|---|---|"]
    L += [f"| {_cell(n)} | {a} | {h} | {c} |" for n, a, h, c in sorted(rows, key=lambda x: x[0].lower())]
    return L + [""]


def write(root: Path) -> dict:
    """Write this search's numbered report and the folder's README. Returns paths and text."""
    root = Path(root)
    folder = root / FOLDER
    folder.mkdir(exist_ok=True)
    dbp = root / "data/jobs.sqlite"
    if not dbp.exists():
        text = (f"# {FOLDER.replace('_', ' ')}\n\nNo search has finished yet. If you just saved your settings, your first search may already be running: click the **Actions** tab at the top of your copy, then **Run job search** on the left. A yellow dot means a search is running; a green check means it finished. Your results appear here when it finishes (about 10 to 20 minutes).\n\n"
                f"To start one: **[START A SEARCH NOW]({RUN_LINK})**, then click the grey **Run workflow** button "
                "and the green **Run workflow** button.\n\n" + "\n".join(_manual_boards(root)) + "\n")
        (folder / "README.md").write_text(text)
        return {"index": str(folder / "README.md"), "report": None, "text": text}
    db = sqlite3.connect(dbp)
    db.row_factory = sqlite3.Row
    last = db.execute("SELECT * FROM runs WHERE finished_at IS NOT NULL ORDER BY id DESC LIMIT 1").fetchone()
    outbox_p = root / "out/outbox.json"
    outbox = _j(outbox_p.read_text(), {}) if outbox_p.exists() else {}
    current = _current_sections(db)

    report_file = None
    if last:
        c = _j(last["counts"], {})
        name = report_name(last["id"], last["finished_at"])
        R = [f"# REPORT {last['id']}: search run {last['finished_at'][:16].replace('T', ' ')} UTC", "",
             f"Checked {c.get('discovered', 0)} job listings; {c.get('title_matched', 0)} matched your job titles"
             + (f" and {c['skills_matched']} matched your skills" if c.get("skills_matched") else "") + ".", ""]
        from .settings_summary import section as settings_section, rows as settings_rows, change_link
        used = c.get("settings_used")
        if not used:   # searches before 2026-11-07 did not save their settings: show the current ones
            try:
                from .pipeline import load_config
                used = settings_rows(root, *load_config(root))
            except Exception:  # noqa: BLE001
                used = None
        R += settings_section(used, link=change_link(root))
        from .report import must_mention_note
        R += must_mention_note(c)
        R += ["## New in this search", ""]
        from .report import new_in_run, new_jobs_table
        ids, near_new = new_in_run(db, last["id"])
        if ids:
            R.append(f"**{len(ids)} new job{'s' if len(ids) != 1 else ''} that match all your rules**")
            R += [""] + new_jobs_table(db, ids)
        elif near_new:
            R += ["No new jobs match all your rules. New near misses are in the near-miss table below.", ""]
        elif c.get("title_matched", 0) == 0 and not c.get("skills_matched") and c.get("left_out_must_mention"):
            R += ["Nothing new in this search. Jobs with your titles were found, but all were left out by "
                  "\"Words the job description must mention\" (see the note above).", ""]
        elif c.get("title_matched", 0) == 0 and not c.get("skills_matched"):
            R += ["Nothing new in this search.", "",
                  "**None of the listings matched your job titles.** To find more:", "",
                  "1. Use shorter, common titles. For example, \"Data Analyst\" also finds \"Senior Data Analyst\" "
                  "and \"Analyst, Data\", but \"Data Analyst for Nonprofit Programs\" finds almost nothing.",
                  "2. Add other names for the same job, for example Program Manager, Program Director and Program Lead.",
                  "3. Leave \"Words the job description must mention\" empty to start; add words later if you get "
                  "too many jobs.",
                  "4. Add the free Tavily web search key (SETUP.md Step 6). It finds jobs that are on no job site.",
                  "5. Add employers you like under \"Companies to watch\" in the questionnaire.", ""]
        else:
            R += ["Nothing new in this search."
                  + (" The jobs below were already reported earlier." if c.get("qualified") or c.get("needs_verification") else ""), ""]
        full_p = root / "out/full_report.md"
        if not full_p.exists():   # no full report from this search: show the current lists instead
            R += current
        else:   # each job is listed once, in the full report (the current lists are on the folder's main page)
            full = full_p.read_text()
            full = re.sub(r"^# .*\n", "", full, count=1)
            full = re.sub(r"(?m)^### ", "#### ", full)
            full = re.sub(r"(?m)^## ", "### ", full)
            R += ["", "## Full report: jobs found in this search", "", full]
        report_file = folder / name
        report_file.write_text("\n".join(R) + "\n")

    reports = sorted(folder.glob("REPORT_*.md"), reverse=True)
    I = ["# JOB SEARCH RESULTS", "",
         "**This page shows your latest results.** It is updated after every search.", "",
         f"To search again: **[START A SEARCH NOW]({RUN_LINK})**, then click the grey **Run workflow** "
         "button and the green **Run workflow** button.", ""]
    if last:
        I += [f"**Latest search:** {last['finished_at'][:16].replace('T', ' ')} UTC. "
              f"Full details: [{reports[0].name}]({reports[0].name})" if reports else "", ""]
        from .settings_summary import section as settings_section, rows as settings_rows, change_link
        used = _j(last["counts"], {}).get("settings_used")
        if not used:   # searches before 2026-11-07 did not save their settings: show the current ones
            try:
                from .pipeline import load_config
                used = settings_rows(root, *load_config(root))
            except Exception:  # noqa: BLE001
                used = None
        I += settings_section(used, link=change_link(root), title="Your settings for the latest search")
    I += current
    I += [""] + _manual_boards(root)
    I += _companies(root)
    if last:
        from .sources_summary import section as where_section
        I += where_section(_j(last["sources"], []), last["started_at"], last["finished_at"])
    I += ["", "## All reports (newest first)", "",
          "Each search adds one numbered report with its date and time (UTC).", ""]
    I += [f"{i}. [{p.name}]({p.name})" for i, p in enumerate(reports, 1)] or ["None yet."]
    index = "\n".join(I) + "\n"
    (folder / "README.md").write_text(index)
    return {"index": str(folder / "README.md"), "report": str(report_file) if report_file else None, "text": index}
