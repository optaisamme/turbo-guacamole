"""Readable Markdown + HTML report: Qualified, Needs Verification, held hourly/contract,
Rejected examples, source coverage and limitations."""
from __future__ import annotations

import html
import json
from pathlib import Path

from .notify import _j

SOURCE_NOTES = {
    "LinkedIn, Indeed, Handshake, Idealist and 28 more": "Not searched automatically (no public data feed, sign-in "
    "required, blocked, or terms do not allow it). One-click searches for them are on the JOB_SEARCH_RESULTS page.",
}

def _rule(row, name):
    for r in _j(row, "rules", []) or []:
        if r["rule"] == name:
            return r
    return {}


LINK_NOTE = {"blocked": "site blocks automated checks", "unverified": "not checked", "js_required": "page needs a browser",
             "error": "could not be opened", "generic": "goes to a general careers page",
             "mismatch": "goes to a different job", "closed": "looks closed"}
SHORTER = [("No employer-advertised salary found", "no salary listed"),
           (" (per third-party listing; employer posting not confirmed)", " (job site's copy)"),
           ("Remote, but country/state eligibility not stated", "remote, but which states can apply is not stated"),
           ("Salary seen only on a third-party source; not confirmed on employer posting", "salary only on a job site's copy"),
           ("posting content not confirmed on employer/ATS source", "Posting only seen on a job site"),
           ("Required vs preferred unclear for", "unclear whether required:")]
OLD_PREFIX = {"salary: ": "Salary: ", "location: ": "Location: ", "cloud: ": "Salesforce Cloud: ",
              "certifications: ": "Certifications: "}   # wording saved by searches before 2026-11-04
LINK_WORDS = ("application route:", "application link", "link not checked", "closed:", "no longer open:")


def _label(r) -> str:
    """Job title plus short marks: (NPSP) and (skills match)."""
    from .skills_match import SKILLS_FAMILY
    keys = r.keys()
    return (r["title"] + (" (NPSP)" if "npsp" in keys and r["npsp"] else "")
            + (" (skills match)" if "family" in keys and r["family"] == SKILLS_FAMILY else ""))


def _short(text: str, n: int = 100) -> str:
    import re
    for a, b in SHORTER:
        text = text.replace(a, b)
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= n else text[: n - 1].rstrip(" ,;(") + "…"


def drop_state_list(text: str) -> str:
    """'Allowed-state list omits New Mexico (alabama, …)' -> 'Allowed-state list omits New Mexico'
    (searches before 2026-11-07 saved the list of states)."""
    import re
    return re.sub(r"(Allowed-state list omits [^();]+?) \([^)]*\)", r"\1", text or "")


def short_salary(x: str, verdict: str = " (too low)") -> str:
    """'Salary: range $60,000-$70,000: starts at $60,000, below $90,000; …' -> 'Salary: $60,000-$70,000 (too low)'."""
    import re
    m = re.match(r"(Salary: )(.*?)( \((?:per third-party listing[^)]*|job site's copy)\))?$", x)
    if not m:
        return x
    head, body, tail = m.group(1), m.group(2), m.group(3) or ""
    for pat, fmt in ((r"range (\$[\d,]+-\$[\d,]+)", "{0}"), (r"(?:single )?salary (\$[\d,]+)", "{0}"),
                     (r"(lowest|highest) pay (\$[\d,]+)", "{0} pay {1}")):
        hit = re.match(pat, body)
        if hit:
            return f"{head}{fmt.format(*hit.groups())}{verdict}{tail}"
    return x


def near_miss_reason(x: str) -> str:
    """A reason for the results page: no state list, short salary wording, "(job site's copy)"."""
    for a, b in OLD_PREFIX.items():
        if x.startswith(a):
            x = b + x[len(a):]
    return short_salary(drop_state_list(x)).replace(" (per third-party listing; employer posting not confirmed)",
                                                    " (job site's copy)")


def _plain_reason(x: str, short_pay: bool = False) -> str:
    """Current plain wording for a saved reason (older searches saved 'salary: …' style text)."""
    for a, b in OLD_PREFIX.items():
        if x.startswith(a):
            x = b + x[len(a):]
    x = drop_state_list(x)
    return _short(short_salary(x) if short_pay else x)


def _check_table(rows) -> list[str]:
    """Jobs that need checking: one short row each, easy to skim."""
    from .models import Verification
    from .verify import plain_link_reason
    if not rows:
        return ["None.", ""]
    L = ["| Job | Company | What to check | Link | First found |", "|---|---|---|---|---|"]
    for r in rows:
        ver = _j(r, "verification", {}) or {}
        v = Verification(**{k: x for k, x in ver.items() if k in Verification.__dataclass_fields__}) if ver else None
        link_reason = plain_link_reason(v) if ver.get("status") != "verified" else None
        reasons = [_plain_reason(x) for x in (_j(r, "reasons", []) or [])
                   if x != link_reason and not x.lower().startswith(LINK_WORDS)]   # the Link column says this
        if link_reason:
            reasons.append("Check the job is still open")
        cell = lambda x: str(x or "").replace("|", "/").replace("\n", " ")
        L.append(f"| {cell(_label(r))} | {cell(r['employer'])} | {cell('; '.join(reasons))} | "
                 f"{_link_cell(r, ver)} | {(r['first_seen'] or '')[:10]} |")
    return L + [""]



SHORT_LOCATION = {
    "Remote within the United States; no state restriction stated": "remote, anywhere in the US",
    "Explicitly remote anywhere in the United States with no conflicting state restriction": "remote, anywhere in the US",
}


def _link_cell(r, ver: dict) -> str:
    from .verify import FEED_LISTED
    link = ver.get("apply_url") or r["canonical_url"] or ((_j(r, "source_urls", []) or [""])[0])
    if ver.get("status") == "verified":
        note = ("Himalayas listing, not opened" if (ver.get("detail") or "").startswith(FEED_LISTED)
                else "job site" if ver.get("apply_route") == "third_party" else "employer's site")
    else:
        note = LINK_NOTE.get(ver.get("status"), "not checked")
    return f"[Open]({link}) ({note})"


def _salary_cell(r) -> str:
    import re
    reason = (_rule(r, "salary") or {}).get("reason", "")
    m = re.search(r"\$[\d,]+(?:\.\d+)?(?:\s*[-–]\s*\$[\d,]+(?:\.\d+)?)?", reason)
    return m.group(0).replace(" ", "") if m else "not listed"


def _fit_cell(r) -> str:
    fit = _j(r, "fit", {}) or {}
    if not fit:
        return ""
    parts = [f"{len(fit.get('strong') or [])} skills match"]
    import re
    missing = [re.sub(r"\s*\(.*?\)", "", g["skill"]) for g in (fit.get("gaps") or []) if g.get("required")][:3]
    if missing:
        parts.append("missing (required): " + ", ".join(missing))
    return "; ".join(parts)


def skills_matched_cell(r) -> str:
    """The skills from your experience file that a skills-matched job's listing mentions."""
    got = (_j(r, "fit", {}) or {}).get("skills_matched")
    return ", ".join(got) if got else "not recorded"


def _match_table(rows, skills: bool = False) -> list[str]:
    """Jobs that match all rules: one short row each, easy to skim. skills=True adds the skills matched."""
    if not rows:
        return ["None.", ""]
    cell = lambda x: str(x or "").replace("|", "/").replace("\n", " ")
    L = ["| Job | Company |" + (" Skills matched |" if skills else "") + " Salary | Location | Fit with your experience | Link | First found |",
         "|---|---|" + ("---|" if skills else "") + "---|---|---|---|---|"]
    for r in rows:
        ver = _j(r, "verification", {}) or {}
        loc = (_rule(r, "location") or {}).get("reason", "")
        if loc in SHORT_LOCATION:
            loc = SHORT_LOCATION[loc]
        elif loc.startswith("Remote within the United States"):
            loc = "remote, US"
        else:
            loc = _short(loc, 60) if loc else cell(r["location_text"])
        L.append(f"| {cell(_label(r))} | {cell(r['employer'])} | " + (f"{cell(skills_matched_cell(r))} | " if skills else "")
                 + f"{_salary_cell(r)} | {cell(loc)} | {cell(_fit_cell(r))} | "
                 f"{_link_cell(r, ver)} | {(r['first_seen'] or '')[:10]} |")
    return L + [""]



def _one_rule_failed(row) -> bool:
    """Exactly one rule failed. Other rules passed or could not be checked."""
    return [x.get("outcome") for x in (_j(row, "rules", []) or [])].count("fail") == 1


def _unchecked_rules(row) -> list[str]:
    """Names of the rules that could not be checked for a job (for example, no salary listed)."""
    from .classify import RULE_LABELS
    names = [x.get("rule", "") for x in (_j(row, "rules", []) or []) if x.get("outcome") == "unclear"]
    return [f'Your rule "{n[6:]}"' if n.startswith("rule: ") else RULE_LABELS.get(n, n.capitalize()) for n in names]


def must_mention_note(c: dict) -> list[str]:
    """Say how many listings had a job title but were left out by "Words the job description must mention"."""
    n = c.get("left_out_must_mention") or 0
    if not n:
        return []
    words = ", ".join(c.get("must_mention_words") or [])
    return [f"**{n} listing{'s' if n != 1 else ''} had one of your job titles but {'were' if n != 1 else 'was'} left out, "
            f"because the description did not mention any of these words: {words}.** If that is not what you want, "
            "change \"Words the job description must mention\" in the setup questionnaire (check the spelling, or type `none`).", ""]


OUTCOME_WORDS = {"pass": "meets your rule", "fail": "does not meet your rule", "unclear": "could not be checked"}
SOURCE_WORDS = {"third_party": "job site's copy, "}


def _quote(text: str, n: int = 220) -> str:
    """Posting text is untrusted: shown only as plain quoted text (no HTML, links or formatting)."""
    import html
    import re
    t = re.sub(r"\s+", " ", text or "").strip()
    t = t if len(t) <= n else t[: n - 1].rstrip() + "…"
    return html.escape(t).replace("`", "'").replace("[", "(").replace("]", ")").replace("*", "").replace("_", " ")


def evidence_block(r) -> list[str]:
    """One fold-out block per job: each rule's decision with the posting text it was based on, and the link check."""
    from .classify import RULE_LABELS
    from urllib.parse import urlsplit
    lines = []
    for x in _j(r, "rules", []) or []:
        name = x.get("rule", "")
        label = f'Your rule "{name[6:]}"' if name.startswith("rule: ") else RULE_LABELS.get(name, name.capitalize())
        reason = f"{label}: {x.get('reason', '')}"
        reason = short_salary(reason, "" if x.get("outcome") == "pass" else " (too low)") if name == "salary" else reason
        said = _quote(_plain_reason(reason), 200).rstrip(".")
        line = f"{said} ({OUTCOME_WORDS.get(x.get('outcome'), x.get('outcome'))})."
        ev = next((e for e in x.get("evidence") or [] if e.get("excerpt")), None)
        if ev:
            host = urlsplit(ev.get("url") or "").netloc.removeprefix("www.") or "posting"
            line += f' Posting says: "{_quote(ev["excerpt"])}" ({SOURCE_WORDS.get(ev.get("source"), "")}{host})'
        lines.append(line)
    ver = _j(r, "verification", {}) or {}
    if ver:
        line = f"Application link: {_quote(ver.get('detail') or ver.get('status') or '', 200).rstrip('.')}."
        if ver.get("checked_at"):
            line += f" Checked {str(ver['checked_at'])[:16].replace('T', ' ')} UTC."
        lines.append(line)
    if r["last_seen"]:
        lines.append(f"Last found in a search: {str(r['last_seen'])[:16].replace('T', ' ')} UTC.")
    title = _quote(f"{_label(r)} ({r['employer'] or 'employer not given'})", 120)
    return ["<details><summary>" + title + "</summary>", ""] + [f"{i}. {l}" for i, l in enumerate(lines, 1)] + ["", "</details>"]


def evidence_section(rows, limit: int = 100) -> list[str]:
    """Collapsed evidence for each job in a list, newest first (click a job to open it)."""
    if not rows:
        return []
    rows = sorted(rows, key=lambda r: r["first_seen"] or "", reverse=True)[:limit]
    L = ["**Evidence:** click a job to see why it is in this list, with the posting's own words.", ""]
    for r in rows:
        L += evidence_block(r)
    return L + [""]


def unchecked_note(row) -> str:
    """' (other rules could not be checked: Salary)' when other rules could not be checked."""
    unchecked = _unchecked_rules(row)
    return f" (other rules could not be checked: {', '.join(unchecked)})" if unchecked else ""


def near_misses(db) -> list:
    """Open jobs where exactly one rule failed (other rules passed or could not be checked), newest first."""
    rows = db.execute("SELECT * FROM jobs WHERE status='Rejected' AND closed_at IS NULL "
                      "ORDER BY first_seen DESC, id DESC").fetchall()
    return [r for r in rows if _one_rule_failed(r)]


def near_miss_link(r) -> str:
    ver = _j(r, "verification", {}) or {}
    return ver.get("final_url") or r["canonical_url"] or r["apply_url"] or ((_j(r, "source_urls", []) or [""])[0])


def _reject_table(rows, limit: int = 100) -> list[str]:
    """Jobs that did not match: one short row each, newest first."""
    if not rows:
        return ["None.", ""]
    cell = lambda x: str(x or "").replace("|", "/").replace("\n", " ")
    rows = sorted(rows, key=lambda r: r["first_seen"] or "", reverse=True)
    L = ["| Job | Company | Why it did not match | Link | First found |", "|---|---|---|---|---|"]
    for r in rows[:limit]:
        title = _label(r)
        why = "; ".join(_plain_reason(x, short_pay=True) for x in (_j(r, "reasons", []) or []))
        why += unchecked_note(r)
        link = r["canonical_url"] or ((_j(r, "source_urls", []) or [""])[0])
        L.append(f"| {cell(title)} | {cell(r['employer'] or 'employer not given')} | {cell(why)} | "
                 f"[Open]({link}) | {(r['first_seen'] or '')[:10]} |")
    if len(rows) > limit:
        L += ["", f"{len(rows) - limit} more are not shown."]
    return L + [""]


def build(store, run_info: dict, out_dir: Path, title: str = "Job Search Report") -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    # only jobs found in this search; nothing is carried over from earlier searches
    run_id, started = run_info.get("run_id"), run_info.get("started_at")
    rows = store.jobs("closed_at IS NULL AND last_run_id = ?", (run_id,)) if run_id else store.jobs("closed_at IS NULL")
    q = [r for r in rows if r["status"] == "Qualified"]
    held = [r for r in rows if r["status"] == "Needs Verification" and r["compensation_type"] in ("hourly", "contract")]
    nv = [r for r in rows if r["status"] == "Needs Verification" and r not in held]
    rej = [r for r in rows if r["status"] == "Rejected"]
    closed = store.jobs("closed_at IS NOT NULL AND closed_at >= ?", (started,)) if started else store.jobs("closed_at IS NOT NULL")
    near = [r for r in rej if _one_rule_failed(r)]
    md = [f"# {title}", "",
          f"Search started: {(run_info.get('started_at') or '')[:16].replace('T', ' ')} UTC", ""]
    if run_info.get("fixtures"):
        md += ["**TEST DATA** (not live listings).", ""]
    md += [f"Totals for this search: match all your rules {len(q)} | need checking {len(nv)} | hourly or contract {len(held)} | "
           f"did not match {len(rej)} ({len(near)} near misses listed; {len(rej) - len(near)} failed 2 or more rules) | "
           f"no longer open {len(closed)}",
           ""]
    md += must_mention_note(run_info.get("counts") or {})
    if run_info.get("employers_to_skip"):
        md += [f"Employers you skip: {', '.join(run_info['employers_to_skip'])} "
               f"({run_info.get('employers_skipped', 0)} listings skipped in this search).", ""]
    md += ["## Jobs that match all your rules", ""]
    from .skills_match import SKILLS_FAMILY
    md += _match_table([r for r in q if r["family"] != SKILLS_FAMILY])
    md += evidence_section([r for r in q if r["family"] != SKILLS_FAMILY])
    q_skills = [r for r in q if r["family"] == SKILLS_FAMILY]
    if q_skills:
        md += ["### Matches by skills (other titles)", "",
               "Their title is not one of yours, but the listing has your required keywords and enough of your skills.", ""]
        md += _match_table(q_skills, skills=True)
        md += evidence_section(q_skills)
    md += ["## Jobs that need checking", ""]
    md += _check_table(nv)
    md += evidence_section(nv)
    md += ["## Hourly or contract jobs", "",
           "Pay is listed per hour or per contract, so the yearly salary rule was not applied. Check the pay yourself.", ""]
    md += _check_table(held)
    md += evidence_section(held)
    md += ["## Near Misses: Jobs that did not match one of your rules", "", "Newest first, 100 at most.", ""]
    md += _reject_table(near)
    md += evidence_section(near)
    from .sources_summary import section as where_section
    md += [""] + where_section(run_info.get("sources", []), run_info.get("started_at"), run_info.get("finished_at"))
    for k, v in SOURCE_NOTES.items():
        md.append(f"**{k}:** {v}")
    dropped = run_info.get("dropped_boards") or []
    if dropped:
        md += ["", "Companies no longer checked (no job matching your job titles or skills in more than 5 searches in a row): "
               + ", ".join(sorted(b.get("employer") or b["board"] for b in dropped)) + "."]
    md += ["", "Good to know:",
           "1. Only the job sites marked **Yes** in the \"Searched this time?\" column of \"Where this search looked\" were searched. No tool finds every job.",
           "2. \"First found\" is when this tool first saw a job, not when the employer posted it.",
           "3. Rules are checked by reading the posting's text. When the wording is unclear, the job is put under "
           "\"need checking\" instead of guessed.",
           "4. The fit notes compare the posting with your experience file. A skill not listed there shows as \"not documented\"."]
    errs = run_info.get("errors") or []
    if errs:
        from .plain_errors import plain_error
        md += ["", "## Problems during this search", "",
               "These did not stop the search. Where a page is listed, you can open it yourself.", ""] + [f"{i}. {plain_error(e)}" for i, e in enumerate(errs[:30], 1)]
    text = "\n".join(md) + "\n"
    p = out_dir / "full_report.md"   # included in JOB_SEARCH_RESULTS/REPORT_*.md by results_folder.py
    p.write_text(text)
    return p


def _html(md: str, title: str) -> str:
    body = []
    for line in md.split("\n"):
        e = html.escape(line)
        if line.startswith("### "):
            body.append(f"<h3>{e[4:]}</h3>")
        elif line.startswith("## "):
            body.append(f"<h2>{e[3:]}</h2>")
        elif line.startswith("# "):
            body.append(f"<h1>{e[2:]}</h1>")
        elif line.startswith("|"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            if set("".join(cells)) <= set("-"):
                continue
            body.append("<tr>" + "".join(f"<td>{html.escape(c)}</td>" for c in cells) + "</tr>")
        elif line.strip():
            body.append(f"<p>{e}</p>")
    joined = "\n".join(body).replace("</p>\n<tr>", "</p>\n<table><tr>")
    joined = joined.replace("</tr>\n<p>", "</tr></table>\n<p>").replace("</tr>\n<h", "</tr></table>\n<h")
    css = ("body{font-family:system-ui,sans-serif;max-width:1000px;margin:24px auto;padding:0 16px;line-height:1.45;background:#fff;color:#111}"
           "h3{margin-top:22px}table{border-collapse:collapse;font-size:13px}td{border:1px solid #ccc;padding:4px 6px;vertical-align:top}"
           "@media (prefers-color-scheme: dark){body{background:#111;color:#eee}td{border-color:#444}}")
    return f"<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>{html.escape(title)}</title><style>{css}</style></head><body>{joined}</body></html>"


NEW_KIND = {"new": "New", "newly_verified": "Newly verified (seen before)", "baseline": "Already listed when you started"}


def new_jobs_table(db, notification_ids) -> list[str]:
    """New matching jobs from one search, one short row each (instead of the long alert text)."""
    ids = [int(i) for i in (notification_ids or [])]
    if not ids:
        return []
    marks = ",".join("?" * len(ids))
    rows = db.execute(f"SELECT j.*, n.kind AS nkind FROM notifications n JOIN jobs j ON j.id = n.job_id "
                      f"WHERE n.id IN ({marks}) ORDER BY j.first_seen DESC", ids).fetchall()
    cell = lambda x: str(x or "").replace("|", "/").replace("\n", " ")
    L = ["| Job | Company | Salary | Apply | What's new |", "|---|---|---|---|---|"]
    for r in rows:
        ver = _j(r, "verification", {}) or {}
        L.append(f"| {cell(_label(r))} | {cell(r['employer'] or 'employer not given')} | {_salary_cell(r)} | "
                 f"{_link_cell(r, ver)} | {NEW_KIND.get(r['nkind'], cell(r['nkind']))} |")
    return L + [""]


def new_in_run(db, run_id) -> tuple[list[int], int]:
    """From the saved job database: (new matching-job alert ids, number of new near misses) for one search.
    Uses only saved data, so the results page is the same wherever it is rebuilt."""
    rows = db.execute("SELECT id, kind, status FROM notifications WHERE run_id = ?", (run_id,)).fetchall()
    ids = [r["id"] for r in rows if r["kind"] != "near_miss" and r["status"] not in ("cancelled", "held", "suppressed_baseline")]
    run = db.execute("SELECT started_at FROM runs WHERE id = ?", (run_id,)).fetchone()
    near = sum(1 for r in near_misses(db) if run and (r["first_seen"] or "") >= (run["started_at"] or ""))
    return ids, near
