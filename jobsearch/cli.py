"""Command line interface.

  python -m jobsearch run [--dry-run] [--sources a,b] [--no-render]
  python -m jobsearch outbox prepare            # re-verify pending alerts, write out/outbox.json
  python -m jobsearch outbox mark --ids 1,2 --status delivered|failed --channel results_page --ref <id> [--error msg]
  python -m jobsearch outbox list
  python -m jobsearch report
  python -m jobsearch boards add --ats greenhouse --board <token> --employer "<name>"
  python -m jobsearch status
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from . import pipeline
from .adapters.ats import SUPPORTED_JOB_PAGES
from .store import Store

ROOT = Path(__file__).resolve().parent.parent


def _setup_logging(root: Path):
    (root / "logs").mkdir(exist_ok=True)
    fmt = logging.Formatter('{"ts":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","msg":%(message)r}')
    h1 = logging.FileHandler(root / "logs/jobsearch.log")
    h1.setFormatter(fmt)
    h2 = logging.StreamHandler(sys.stderr)
    h2.setFormatter(fmt)
    logging.basicConfig(level=logging.INFO, handlers=[h1, h2])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="jobsearch")
    ap.add_argument("--root", default=str(ROOT))
    ap.add_argument("--db", default=None)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--dry-run", action="store_true")
    r.add_argument("--sources", default=None)
    r.add_argument("--no-render", action="store_true")
    o = sub.add_parser("outbox")
    o.add_argument("action", choices=["prepare", "mark", "list"])
    o.add_argument("--ids", default="")
    o.add_argument("--status", choices=["delivered", "failed"], default=None)
    o.add_argument("--channel", default="results_page")
    o.add_argument("--ref", default=None)
    o.add_argument("--error", default=None)
    o.add_argument("--no-render", action="store_true")
    sub.add_parser("report")
    b = sub.add_parser("boards")
    b.add_argument("action", choices=["add", "list"])
    b.add_argument("--ats")
    b.add_argument("--board")
    b.add_argument("--employer")
    sub.add_parser("status")
    sub.add_parser("needs-employer-posting",
                   help="list jobs whose link could not be checked, with a search link for the employer's own posting")
    sub.add_parser("check", help="check that the settings files can be read, and summarize them")
    a = ap.parse_args(argv)
    root = Path(a.root)
    db = Path(a.db) if a.db else root / "data/jobs.sqlite"
    _setup_logging(root)

    if a.cmd == "run":
        info = pipeline.run(root, db, dry_run=a.dry_run, sources=a.sources.split(",") if a.sources else None,
                            use_renderer=not a.no_render)
        if not a.dry_run:
            from . import results_folder
            info["results"] = results_folder.write(root)["report"]
        print(json.dumps({k: info.get(k) for k in ("run_id", "mode", "is_baseline", "counts", "report", "results")}, indent=2))
        print(json.dumps([{k: s[k] for k in ("name", "state", "detail", "fetched", "matched")} for s in info["sources"]], indent=2))
        return 0
    if a.cmd == "outbox":
        if a.action == "prepare":
            out = pipeline.prepare_outbox(root, db, use_renderer=not a.no_render)
            (root / "out").mkdir(exist_ok=True)
            (root / "out/outbox.json").write_text(json.dumps(out, indent=2))
            print(json.dumps(out, indent=2))
            return 0
        st = Store(db)
        if a.action == "list":
            for n in st.db.execute("SELECT n.id, n.kind, n.status, n.attempts, n.channel_ref, j.employer, j.title FROM notifications n JOIN jobs j ON j.id=n.job_id ORDER BY n.id"):
                print(dict(n))
            return 0
        if not a.status or not a.ids:
            print("--ids and --status required", file=sys.stderr)
            return 2
        for nid in [int(x) for x in a.ids.split(",") if x.strip()]:
            st.mark_notification(nid, a.status, channel=a.channel, ref=a.ref, error=a.error)
        print(f"marked {a.ids} {a.status}")
        return 0
    if a.cmd == "report":
        st = Store(db)
        last = st.db.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()
        info = {"started_at": last["started_at"] if last else None, "mode": last["mode"] if last else None,
                "is_baseline": bool(last["is_baseline"]) if last else False,
                "sources": json.loads(last["sources"] or "[]") if last else [], "errors": json.loads(last["errors"] or "[]") if last else []}
        from . import report
        print(report.build(st, info, root / "out"))
        return 0
    if a.cmd == "boards":
        boards = pipeline.load_boards(root)
        if a.action == "list":
            print(json.dumps(boards, indent=2))
            return 0
        pipeline.save_boards(root, boards, [{"ats": a.ats, "board": a.board, "employer": a.employer}])
        print("added")
        return 0
    if a.cmd == "check":
        try:
            prof, exp = pipeline.load_config(root)
        except Exception as e:  # noqa: BLE001 — shown to non-technical users
            print(f"SETTINGS PROBLEM: {e}")
            print("Open the file named above, fix the line mentioned (often a missing quote, comma or bracket), and save.")
            return 1
        from .criteria import CRITERIA
        C = CRITERIA
        print("SWITCHED ON / OFF IN YOUR SETTINGS")
        print("Remote jobs only: " + ("ON (hybrid and on-site jobs are left out)" if C.get("remote_only", True)
                                      else "OFF (hybrid and on-site jobs are kept" + (f" in {C['home_state']})" if C.get("home_state") else ")")))
        print(f"Home state check: {C['home_state'] + ' (' + (C.get('home_state_abbr') or '-') + ')' if C.get('home_state') else 'OFF (no state set)'}")
        if C["single_min"] or C["range_lower_min"] or C["range_upper_gt"]:
            money = lambda n: f"${n:,}" if n else "no minimum"
            print(f"Salary: one salary at least {money(C['single_min'])}; a range must start at {money(C['range_lower_min'])} "
                  f"or more and end above {money(C['range_upper_gt'])}")
        else:
            print("Salary minimum: OFF")
        if C.get("cert_filter_on", True):
            print(f"Certification check: ON; allowed before hire: {', '.join(C['allowed_prehire_certs']) or 'none'}")
        else:
            print("Certification check: OFF")
        for r in C.get("custom_rules", []):
            print(f"Rule '{r['name']}': {'ON' if r.get('on', True) else 'OFF'}; reject when {r.get('reject_when', 'required')}: {', '.join(r['terms'])}")
        tr = prof.get("title_rules", {})
        if "must_mention" in tr:
            print(f"Description must mention: {', '.join(tr['must_mention']) or 'OFF'}")
        if "exclude_plain" in tr:
            print(f"Title words to skip (title only): {', '.join(tr['exclude_plain']) or 'OFF (none)'}")
        if "employer_rules" in prof:
            print(f"Employers to skip: {', '.join(prof['employer_rules']['exclude']) or 'OFF (none)'}")
        for t in prof["titles"]:
            print(f"Job titles{' (' + t['family'] + ')' if len(prof['titles']) > 1 else ''}: {', '.join(t.get('plain_titles') or [])}")
        watched, bad = pipeline.watched_boards(prof)
        from .titles import TitleMatcher
        sm = TitleMatcher(prof, exp.get("skills", [])).skills
        print(f"Skills-based matching: {sm.summary()}")
        if sm.on and sm.rule not in ("all", "any"):
            print('SETTINGS PROBLEM: the skills-based matching keyword rule must be "all" or "any". Answer the setup questionnaire again to fix it.')
            return 1
        if sm.active and sm.minimum and not sm.skills:
            print("Note: skills-based matching is on, but you have not listed any skills yet (questionnaire Step 6), "
                  "so it will not find any jobs until you add some.")
        import tomllib as _t
        from .pipeline import SETTINGS_DIR
        try:
            wk = _t.load(open(root / SETTINGS_DIR / "job_search_settings.toml", "rb")).get("automatic_weekly_search", {})
        except (OSError, ValueError):
            wk = {}
        wk_on = str(wk.get("on", "no")).strip().lower() in ("yes", "y", "true", "on", "1")
        from .adapters.aggregators import search_size, SEARCH_SIZE_DEFAULTS
        try:
            sz = search_size({"search_size": _t.load(open(root / SETTINGS_DIR / "job_search_settings.toml", "rb")).get("search_size", {})})
        except (OSError, ValueError):
            sz = dict(SEARCH_SIZE_DEFAULTS)
        print(f"How many jobs to look through: {sz['newest_jobs_per_site']:,} newest jobs per site, "
              f"{sz['jobs_per_search_word']:,} for each job title, Jobicy up to {sz['jobicy_max_jobs']:,}"
              + (" (Recommended)" if sz == SEARCH_SIZE_DEFAULTS else ""))
        print("Automatic weekly searches: " + (f"ON, every {str(wk.get('day', 'Friday')).capitalize()}" if wk_on else "OFF"))
        print(f"Companies to watch: {', '.join(b['board'] for b in watched) or 'none added (optional: only for companies whose careers page runs on Greenhouse, Lever, Ashby or SmartRecruiters; all job sites are still searched)'}")
        if bad:
            for link in bad:
                print(f"COMPANY LINK NOT UNDERSTOOD: {link}")
            print("In the questionnaire's 'Companies to watch', each link must be a company's job page on one of these sites: "
                  f"{SUPPORTED_JOB_PAGES}. Fix or delete the link above and save.")
            return 1
        print(f"Experience profile: {len(exp.get('skills', []))} skills, {len(exp.get('gaps', []))} gap areas")
        if not prof["titles"]:
            print("NO JOB TITLES SET YET. Answer the setup questionnaire (Issues tab, New issue, Job search setup). "
                  "Nothing will be searched until you save at least one job title.")
            return 1
        print("Settings OK.")
        return 0
    if a.cmd == "needs-employer-posting":
        from .lookup import needs_employer_posting
        print(json.dumps(needs_employer_posting(Store(db).db), indent=1))
        return 0
    if a.cmd == "status":
        st = Store(db)
        for r_ in st.db.execute("SELECT id, started_at, finished_at, mode, is_baseline, success, counts FROM runs ORDER BY id DESC LIMIT 5"):
            print(dict(r_))
        print("baseline_done:", st.baseline_done())
        print("pending notifications:", len(st.pending_notifications()))
        return 0
    return 1
