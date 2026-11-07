"""End-to-end run: discover -> title-match -> resolve employer posting -> hard filters ->
verify application route -> classify -> persist/dedupe -> newness -> outbox -> report."""
from __future__ import annotations

import json
import re
import logging
import shutil
import tempfile
import tomllib
from dataclasses import asdict
import html as _html
from pathlib import Path

from .adapters.aggregators import (RemotiveAdapter, RemoteOKAdapter, HimalayasAdapter, TheMuseAdapter, JobicyAdapter,
                                   WeWorkRemotelyAdapter, 
                                   HackerNewsHiringAdapter)
from .adapters.ats import AtsWatchlistAdapter, FETCHERS
from .adapters.base import SourceStatus
from .adapters.websearch import WebSearchAdapter
from .agencies import is_agency
from .skills_match import SKILLS_FAMILY
from .classify import evaluate_posting, decide, plain_reason
from .http import HttpClient
from .models import RawPosting, Verification, QUALIFIED, REJECTED, NEEDS_VERIFICATION
from .notify import build_payload
from .store import Store, norm_key, now_iso
from .text import content_hash
from .titles import TitleMatcher
from . import report as reportmod
from . import verify as verifymod

log = logging.getLogger("jobsearch.pipeline")

ADAPTERS = {  # order matters: web search first so newly discovered boards are scanned in the same run
    "websearch": WebSearchAdapter, "ats_watchlist": AtsWatchlistAdapter, "remotive": RemotiveAdapter,
    "remoteok": RemoteOKAdapter, "himalayas": HimalayasAdapter, "themuse": TheMuseAdapter, "jobicy": JobicyAdapter,
    "weworkremotely": WeWorkRemotelyAdapter, "hackernews": HackerNewsHiringAdapter,
}


SETTINGS_DIR = "JOB_SEARCH_SETTINGS"   # filled in by the setup questionnaire


def load_config(root: Path) -> tuple[dict, dict]:
    """Load search configuration. JOB_SEARCH_SETTINGS/job_search_settings.toml (written by the questionnaire), when present,
    overrides the hard-filter criteria, job titles and search keywords in jobsearch/advanced_settings.toml."""
    from . import criteria
    cfg = root / SETTINGS_DIR if (root / SETTINGS_DIR).exists() else root / "config"   # "config" = older copies/tests
    adv = cfg / "search_profile.toml"           # optional local override (developers)
    if not adv.exists():
        adv = Path(__file__).with_name("advanced_settings.toml")  # built-in defaults; not edited by users
    with open(adv, "rb") as f:
        prof = tomllib.load(f)
    ep = cfg / "my_experience.toml"
    if not ep.exists():
        ep = cfg / "experience_profile.toml"
    exp = {}
    if ep.exists():
        with open(ep, "rb") as f:
            exp = tomllib.load(f)
    sp = cfg / "job_search_settings.toml"
    if not sp.exists():
        sp = cfg / "settings.toml"
    if sp.exists():
        with open(sp, "rb") as f:
            settings = tomllib.load(f)
        criteria.configure(criteria.from_settings(settings), base=criteria.NEUTRAL)
        jt = settings.get("job_titles") or {}
        # one list of job titles (older copies had up to 3 groups: their titles are combined)
        titles = list(dict.fromkeys(t.strip() for t in (jt.get("titles") or [t for g in jt.get("group") or [] for t in g.get("titles", [])])
                                    if isinstance(t, str) and t.strip()))
        # With a settings file, only what the user wrote is used: no built-in titles or filters.
        prof["titles"] = [{"track": "", "family": "Your job titles", "plain_titles": titles, "require_any": [], "include": []}] \
            if titles else []
        flt = settings.get("filters") or {}
        prof["title_rules"] = {"must_mention": list(flt.get("description_must_mention", [])),
                               "exclude_plain": list(flt.get("exclude_titles_containing", []))}
        prof["skills_matching"] = dict(settings.get("skills_matching") or {})
        prof["employer_rules"] = {"exclude": list(flt.get("exclude_employers", []))}
        prof["search_size"] = dict(settings.get("search_size") or {})
        prof["watch_links"] = [u for u in ((settings.get("companies_to_watch") or {}).get("job_page_links") or [])
                               if isinstance(u, str) and u.strip()]
        prof.setdefault("search", {})["queries"] = titles   # every job title is searched
    else:
        criteria.configure(None)
    return prof, exp


def load_boards(root: Path) -> dict:
    p = root / "data/boards.json"
    return json.loads(p.read_text()) if p.exists() else {}


def watched_boards(prof: dict) -> tuple[list[dict], list[str]]:
    """Company job pages typed in settings (section "companies to watch"). Returns (boards, links not understood)."""
    from .adapters.ats import parse_board_link
    ok, bad = [], []
    for link in prof.get("watch_links", []):
        hit = parse_board_link(link)
        if hit:
            ok.append({"ats": hit[0], "board": hit[1], "employer": None})
        else:
            bad.append(link)
    return ok, bad


def with_watched(boards: dict, watched: list[dict]) -> dict:
    """Boards to scan this run: the saved list plus the settings list (the settings list is not saved,
    so deleting a link from settings stops it being scanned)."""
    out = {k: [dict(x) for x in v] for k, v in boards.items()}
    for b in watched:
        lst = out.setdefault(b["ats"], [])
        if not any(x["board"].lower() == b["board"].lower() for x in lst):
            lst.append({"board": b["board"], "employer": b.get("employer")})
    return out


MAX_SEARCHES_WITHOUT_MATCH = 5   # a company found by the search is dropped after more than this many


def age_boards(boards: dict, found: set[tuple[str, str]], failed: set[tuple[str, str]] | None = None) -> list[dict]:
    """Companies the search found by itself (data/boards.json) are dropped after more than
    MAX_SEARCHES_WITHOUT_MATCH searches in a row in which their careers page had no job matching
    your titles. Companies added in the questionnaire ("Companies to watch") are not in this file and are never dropped.
    A careers page that could not be read this search ("failed") does not count either way.
    Returns the dropped companies."""
    dropped = []
    for ats, lst in boards.items():
        keep = []
        for b in lst:
            if (ats, b["board"].lower()) in found:
                b["searches_without_match"] = 0
            elif (ats, b["board"].lower()) in (failed or set()):
                pass   # unknown this time: the page could not be read
            else:
                b["searches_without_match"] = int(b.get("searches_without_match", 0)) + 1
            if b["searches_without_match"] > MAX_SEARCHES_WITHOUT_MATCH:
                dropped.append({"ats": ats, **b})
            else:
                keep.append(b)
        lst[:] = keep
    return dropped


def save_boards(root: Path, boards: dict, added: list[dict], aged: bool = False):
    changed = aged
    for b in added:
        if is_agency(b.get("board")):
            continue
        lst = boards.setdefault(b["ats"], [])
        if not any(x["board"] == b["board"] for x in lst):
            lst.append({"board": b["board"], "employer": b.get("employer"), "added": now_iso(), "searches_without_match": 0})
            changed = True
    if changed:
        (root / "data").mkdir(exist_ok=True)
        (root / "data/boards.json").write_text(json.dumps(boards, indent=2, sort_keys=True))
    return changed


def plain_name(s: str | None) -> str | None:
    """'hims &amp;#038; hers' -> 'hims & hers' (codes are sometimes encoded twice)."""
    if not s:
        return s
    for _ in range(3):
        t = _html.unescape(s)
        if t == s:
            break
        s = t
    return re.sub(r"\s+", " ", s).strip()


def _record(p: RawPosting, match: dict, ev: dict, ver: Verification | None, status: str, reasons: list[str]) -> dict:
    rules = [r.to_dict() for r in ev["rules"]]
    sal = next(r for r in ev["rules"] if r.rule == "salary")
    loc = next(r for r in ev["rules"] if r.rule == "location")
    cert = next(r for r in ev["rules"] if r.rule == "certifications")
    srcs = [p.source_url] + p.extra.get("third_party_sources", [])
    if p.extra.get("discovered_via"):
        srcs.append(p.extra["discovered_via"])
    return {
        "employer": plain_name(p.employer), "title": plain_name(p.title), "track": match["track"], "family": match["family"],
        "norm_key": norm_key(p.employer, p.title, p.location_text), "ats": p.ats, "ats_board": p.ats_board,
        "ats_job_id": p.ats_job_id, "requisition_id": p.requisition_id, "canonical_url": p.canonical_url,
        "apply_url": (ver.apply_url if ver and ver.status == "verified" else None) or p.apply_url,
        "apply_route": ver.apply_route if ver else None,
        "source_urls": [u for u in dict.fromkeys(srcs) if u], "sources": [p.source],
        "location_text": p.location_text, "last_verified": ver.checked_at if ver else None,
        "published_date": p.published_date, "status": status, "reasons": reasons, "rules": rules,
        "verification": ver.to_dict() if ver else None,
        "fit": {**(ev["fit"] or {}), **({"skills_matched": match["skills"]} if match.get("skills") else {})}, "cert_conditions": cert.data.get("post_hire", []),
        "salary": {"outcome": sal.outcome, "reason": sal.reason, **sal.data},
        "location": {"outcome": loc.outcome, "reason": loc.reason, **{k: v for k, v in loc.data.items() if k != "travel"}},
        "travel": loc.data.get("travel", []),
        "content_hash": content_hash(p.title, p.description_text),
        "authoritative": int(ev["authoritative"]),
        "compensation_type": sal.data.get("compensation_type"),
    }


def run(root: Path, db_path: Path, dry_run: bool = False, sources: list[str] | None = None,
        use_renderer: bool = True, client: HttpClient | None = None, fixtures: bool = False,
        report_dir: Path | None = None) -> dict:
    prof, exp = load_config(root)
    boards = load_boards(root)
    from .employers import EmployerBlock
    block = EmployerBlock((prof.get("employer_rules") or {}).get("exclude"))
    boards_trimmed = False
    for lst in boards.values():   # companies found earlier whose employer is now skipped
        keep = [b for b in lst if not block.blocked(b.get("employer"), b.get("board"))]
        boards_trimmed |= len(keep) != len(lst)
        lst[:] = keep
    employers_skipped = 0
    hc = prof["http"]
    client = client or HttpClient(hc["user_agent"], hc["min_interval_seconds"], hc["max_retries"], hc["timeout_seconds"], hc["respect_robots_for_html"])
    matcher = TitleMatcher(prof, exp.get("skills", []))

    work_db = db_path
    tmpdir = None
    if dry_run:
        tmpdir = tempfile.mkdtemp(prefix="jobsearch-dry-")
        work_db = Path(tmpdir) / "jobs.sqlite"
        if db_path.exists():
            shutil.copy(db_path, work_db)
    store = Store(work_db)
    run_id = store.start_run("dry_run" if dry_run else ("fixtures" if fixtures else "live"))
    was_baseline_done = store.baseline_done()
    if block.names:
        for row in store.jobs("closed_at IS NULL"):
            if block.blocked(row["employer"], row["ats_board"]):
                store.mark_closed(row["id"], "the employer is on your list of employers to skip")

    ctx = {"search_size": dict(prof.get("search_size") or {}),
           "skill_words": matcher.skills.search_words(), "skill_keywords": matcher.skills.required if matcher.skills.active else [],
           "skill_rule": matcher.skills.rule,
           "queries": prof["search"]["queries"], "boards": with_watched(boards, watched_boards(prof)[0]), "title_matcher": matcher}
    if block.names:
        ctx["boards"] = {ats: [b for b in lst if not block.blocked(b.get("employer"), b.get("board"))]
                         for ats, lst in ctx["boards"].items()}
    statuses: list[SourceStatus] = []
    raw: list[RawPosting] = []
    for name in (sources or list(ADAPTERS)):
        st = SourceStatus(name)
        ad = ADAPTERS[name](client, ctx)
        got = ad.run(st)
        st.fetched = len(got)
        statuses.append(st)
        raw.extend(got)

    if block.names:   # employers to skip: their listings are dropped here
        kept = [p for p in raw if not block.blocked(p.employer, p.ats_board)]
        employers_skipped += len(raw) - len(kept)
        raw = kept
    for p in raw:   # some sites send names with HTML codes, e.g. "hims &amp;#038; hers"
        p.title, p.employer = plain_name(p.title), plain_name(p.employer)
    # title match (pre-screen on discovery content)
    matched: list[tuple[RawPosting, dict]] = []
    for p in raw:
        m = matcher.match(p.title, p.description_text)
        if m:
            matched.append((p, m))
            for st in statuses:
                if st.name == p.source or (st.name == "ats_watchlist" and p.source in FETCHERS):
                    if m.get("family") == SKILLS_FAMILY:
                        st.skills_matched += 1
                    else:
                        st.matched += 1
                    break

    errors: list[str] = [f"{s.name}: {s.detail}" for s in statuses if s.state in ("error",)] + [f"{s.name}: {e}" for s in statuses for e in s.errors]
    counts = {"discovered": len(raw), "title_matched": sum(1 for _, m in matched if m.get("family") != SKILLS_FAMILY),
              "skills_matched": sum(1 for _, m in matched if m.get("family") == SKILLS_FAMILY), "qualified": 0, "needs_verification": 0, "rejected": 0,
              "closed": 0, "new_jobs": 0, "notifications_queued": 0}
    seen_ids: set[int] = set()
    new_boards: list[dict] = list(ctx.get("boards_added", []))

    web_outcome: dict[str, str] = {}   # web search link -> result (counted per search in the report)
    for p, m in matched:
        web_url = p.source_url if p.source == "websearch" else None
        try:
            p = verifymod.resolve_employer_posting(client, p)
        except Exception as e:  # noqa: BLE001
            errors.append(f"resolve {p.source_url}: {e}")
        if block.blocked(p.employer, p.ats_board):   # the employer's name was found only now
            employers_skipped += 1
            continue
        if p.extra.get("new_board"):
            new_boards.append(p.extra["new_board"])
        if is_agency(p.ats_board):
            p.authoritative = False
            p.extra["intermediary"] = True
        m2 = matcher.match(p.title, p.description_text) or m
        if p.extra.get("closed"):
            ev = evaluate_posting(p, exp)
            ver = Verification("closed", final_url=p.source_url, detail=p.extra.get("lead_note", "closed"), checked_at=now_iso())
            rec = _record(p, m2, ev, ver, "Closed", [f"No longer open: {ver.detail}"])
            jid, _ = store.upsert(rec, run_id)
            store.mark_closed(jid, f"No longer open: {ver.detail}")
            m_lookup = re.search(r"employer lookup for job (\d+)", str(p.extra.get("found_via") or ""))
            if m_lookup:
                old_id = int(m_lookup.group(1))
                old = store.job(old_id)
                if old is not None and (old_id == jid or old["closed_at"] is None):
                    store.mark_closed(old_id, "the employer's own posting is closed (found by employer lookup)")
            counts["closed"] += 1
            seen_ids.add(jid)
            continue
        thin = len(p.description_text.strip()) < 300
        ev = evaluate_posting(p, exp)
        if thin:
            # No usable posting text (e.g. job-board link not fetched): never reject on absent evidence.
            for r in ev["rules"]:
                if r.outcome == "fail":
                    r.outcome, r.reason = "unclear", "posting text unavailable; " + r.reason
        if not ev["authoritative"]:
            for r in ev["rules"]:
                if r.outcome == "fail":
                    r.reason += " (per third-party listing; employer posting not confirmed)"
        prelim = [r for r in ev["rules"] if r.outcome == "fail"]
        others_pass = all(r.outcome == "pass" for r in ev["rules"] if r not in prelim)
        ver = None
        # Verify when nothing failed, or when exactly one rule failed and the rest passed
        # (needed to tell whether the job is a one-criterion near miss).
        if not prelim or (len(prelim) == 1 and others_pass):
            try:
                ver = verifymod.verify(client, p, use_renderer=use_renderer)
            except Exception as e:  # noqa: BLE001
                ver = Verification("error", detail=str(e), checked_at=now_iso())
                errors.append(f"verify {p.canonical_url or p.source_url}: {e}")
            # If the verified page is the employer posting and the discovery copy was third-party,
            # re-run filters on the employer's own text.
            if ver.status == "verified" and not ev["authoritative"] and ver.apply_route != "third_party" and ver.page_text:
                ev = evaluate_posting(p, exp, text_override=ver.page_text, authoritative_override=True)
        if ver is not None and ver.status == "closed":
            status, reasons = "Closed", [f"No longer open: {ver.detail}"]
        else:
            status, reasons = decide(ev["rules"], ver, ev["authoritative"])
        if web_url:
            web_outcome[web_url] = status
        rec = _record(p, m2, ev, ver, status, reasons)
        jid, change = store.upsert(rec, run_id)
        if store.job(jid)["closed_at"]:
            seen_ids.add(jid)
            continue  # replaced or closed by the employer's own posting
        m_lookup = re.search(r"employer lookup for job (\d+)", str(p.extra.get("found_via") or ""))
        if m_lookup and p.authoritative and int(m_lookup.group(1)) != jid:
            old_id = int(m_lookup.group(1))
            old = store.job(old_id)
            if old is not None and old["closed_at"] is None:
                store.mark_closed(old_id, f"replaced by the employer's own posting (job #{jid})")
                for n in store.db.execute("SELECT id FROM notifications WHERE job_id=? AND status IN ('pending','failed','held')", (old_id,)).fetchall():
                    store.mark_notification(n["id"], "cancelled", error=f"replaced by employer posting job #{jid}")
        if status == "Closed":
            store.mark_closed(jid, reasons[0])
            counts["closed"] += 1
            for n in store.db.execute("SELECT id FROM notifications WHERE job_id=? AND status IN ('pending','failed')", (jid,)).fetchall():
                store.mark_notification(n["id"], "cancelled", error="job closed before delivery")
        seen_ids.add(jid)
        if change == "new":
            counts["new_jobs"] += 1
        key = {QUALIFIED: "qualified", NEEDS_VERIFICATION: "needs_verification", REJECTED: "rejected"}.get(status)
        if key:
            counts[key] += 1

        # ---- near misses: exactly one criterion failed or unresolved, all others passed
        if status in (REJECTED, NEEDS_VERIFICATION):
            issues = [plain_reason(r) for r in ev["rules"] if r.outcome != "pass"]
            if ver is None or ver.status != "verified":
                issues.append(verifymod.plain_link_reason(ver))
            if len(issues) == 1:
                row = store.job(jid)
                link = (ver.final_url if ver and ver.final_url else None) or row["canonical_url"] or row["apply_url"] \
                    or (json.loads(row["sources"] and row["source_urls"] or "[]") or [None])[0]
                payload = {"job_id": jid, "title": row["title"], "employer": row["employer"], "link": link,
                           "status": status, "reasons": issues}
                if store.queue_notification(jid, "near_miss", payload, run_id):
                    counts["near_misses_queued"] = counts.get("near_misses_queued", 0) + 1

        # ---- newness / notifications
        if status == QUALIFIED:
            # an unsent near-miss notice is superseded by the qualified alert
            store.db.execute("UPDATE notifications SET status='cancelled', last_error='superseded: job now qualifies' "
                             "WHERE job_id=? AND kind='near_miss' AND status IN ('pending','failed')", (jid,))
            row = store.job(jid)
            if row["first_qualified"] is None:
                store.db.execute("UPDATE jobs SET first_qualified=? WHERE id=?", (now_iso(), jid))
            payload = build_payload(store.job(jid))
            prior = store.db.execute("SELECT id, status FROM notifications WHERE job_id=? AND kind != 'near_miss'", (jid,)).fetchall()
            if prior:
                # never notify twice; a held (failed pre-send check) alert is re-queued once it verifies again
                for n in prior:
                    if n["status"] == "held":
                        store.db.execute("UPDATE notifications SET status='pending', payload=? WHERE id=?", (json.dumps(payload), n["id"]))
                        counts["notifications_queued"] += 1
                continue
            if not was_baseline_done:  # first successful run = silent baseline
                store.queue_notification(jid, "baseline", payload, run_id, status="suppressed_baseline")
                store.db.execute("UPDATE jobs SET baseline=1 WHERE id=?", (jid,))
            elif store.repost_chain_notified(jid):
                store.queue_notification(jid, "repost", payload, run_id, status="suppressed_baseline")
            else:
                kind = "new" if change == "new" else "newly_verified"
                if store.queue_notification(jid, kind, payload, run_id):
                    counts["notifications_queued"] += 1

    # previously-active ATS jobs not seen this run: re-check via official API
    for row in store.jobs("closed_at IS NULL AND ats IS NOT NULL AND status IN ('Qualified','Needs Verification')"):
        if row["id"] in seen_ids or row["ats"] not in FETCHERS:
            continue
        try:
            live = FETCHERS[row["ats"]](client, row["ats_board"], row["ats_job_id"], row["employer"])
            if live is None:
                store.mark_closed(row["id"], "no longer returned by official ATS API")
                counts["closed"] += 1
                for n in store.db.execute("SELECT id FROM notifications WHERE job_id=? AND status IN ('pending','failed')", (row["id"],)):
                    store.mark_notification(n["id"], "cancelled", error="job closed before delivery")
        except Exception as e:  # noqa: BLE001
            errors.append(f"recheck job {row['id']}: {e}")

    # A run counts as successful (and can become the baseline) only if at least one
    # source actually returned listings; all-blocked or empty runs never set the baseline.
    success = any(s.state == "ok" and s.fetched > 0 for s in statuses)
    is_baseline = success and not was_baseline_done
    store.commit()
    for st in statuses:   # web search: per search, jobs that match all rules vs other kept links
        for log in st.searches:
            urls = log.pop("urls", [])
            if "kept" in log:
                log["matches"] = sum(1 for u in urls if web_outcome.get(u) == QUALIFIED)
                log["others"] = log["kept"] - log["matches"]
    counts["employers_skipped"] = employers_skipped
    counts["left_out_must_mention"] = len(matcher.left_out_must_mention)   # had a job title, but none of the words
    counts["must_mention_words"] = list(matcher.must_mention)
    try:
        from .settings_summary import rows as settings_rows
        counts["settings_used"] = settings_rows(root, prof, exp)   # shown at the top of this search's report
    except Exception as e:  # noqa: BLE001 — never stop a search over the summary
        errors.append(f"settings summary: {e}")
    src_dicts = [asdict(s) for s in statuses]
    # record which company careers pages this search read (shown in each report)
    scanned = {(e.get("employer") or e["board"]) for lst in ctx["boards"].values() for e in lst}
    scanned |= {(b.get("employer") or b["board"]) for b in ctx.get("boards_added", []) if not is_agency(b["board"])}
    for d in src_dicts:
        if d["name"] == "ats_watchlist":
            d["companies"] = sorted(scanned, key=str.lower)
    store.finish_run(run_id, success, is_baseline, counts, src_dicts, errors[:100])
    dropped_boards: list[dict] = []
    if not dry_run:
        aged = False
        ats_status = next((s for s in statuses if s.name == "ats_watchlist"), None)
        if ats_status is not None and ats_status.state == "ok":   # count only searches that read careers pages
            found = {(p.ats, (p.ats_board or "").lower()) for p, _ in matched if p.ats and p.ats_board}
            found |= {(b["ats"], b["board"].lower()) for b in new_boards}
            failed = {tuple(k.split(":", 1)) for k in ats_status.boards_failed}
            dropped_boards = age_boards(boards, found, failed)
            aged = True
        save_boards(root, boards, [b for b in new_boards if not block.blocked(b.get("employer"), b.get("board"))],
                    aged=aged or boards_trimmed)
    times = store.db.execute("SELECT started_at, finished_at FROM runs WHERE id=?", (run_id,)).fetchone()
    run_info = {"run_id": run_id, "started_at": times["started_at"], "finished_at": times["finished_at"], "mode": "dry_run" if dry_run else ("fixtures" if fixtures else "live"),
                "is_baseline": is_baseline, "sources": src_dicts, "errors": errors, "counts": counts, "fixtures": fixtures,
                "new_boards": new_boards, "dropped_boards": dropped_boards,
                "employers_to_skip": block.names, "employers_skipped": employers_skipped}
    rdir = report_dir or (root / ("out/practice-run" if dry_run else "out"))
    rp = reportmod.build(store, run_info, rdir, "Initial Job Search Report (baseline)" if is_baseline else "Job Search Report")
    run_info["report"] = str(rp)
    store.db.close()
    if tmpdir:
        shutil.rmtree(tmpdir, ignore_errors=True)
    return run_info


def record_shown(db_path: Path, ids: list[int]) -> int:
    """After the results page is written: record these new jobs and near misses as shown there."""
    store = Store(db_path)
    n = 0
    for nid in ids:
        row = store.db.execute("SELECT status FROM notifications WHERE id=?", (nid,)).fetchone()
        if row and row["status"] in ("pending", "failed", "suppressed_baseline"):
            store.mark_notification(nid, "delivered", channel="results_page", ref="JOB_SEARCH_RESULTS")
            n += 1
    store.commit()
    store.db.close()
    return n


def prepare_outbox(root: Path, db_path: Path, client: HttpClient | None = None, use_renderer: bool = True) -> dict:
    """Re-verify every pending notification's application link immediately before sending.
    Jobs failing re-verification are held (not sent) and moved to Needs Verification."""
    prof, _ = load_config(root)
    hc = prof["http"]
    client = client or HttpClient(hc["user_agent"], hc["min_interval_seconds"], hc["max_retries"], hc["timeout_seconds"], hc["respect_robots_for_html"])
    store = Store(db_path)
    items, ids, held = [], [], []
    near, near_ids = [], []
    # First run: jobs that already matched were recorded silently (baseline). Post them once,
    # together, as "initial results" so the user can see them; never again afterwards.
    first_run = store.db.execute("SELECT * FROM notifications WHERE kind='baseline' AND status='suppressed_baseline' "
                                 "AND channel IS NULL ORDER BY id").fetchall()
    for n in list(store.pending_notifications()) + list(first_run):
        row = store.job(n["job_id"])
        if n["kind"] == "near_miss":
            # Near misses are informational: no pre-send verification; skip if the job has since closed.
            if row["closed_at"]:
                store.mark_notification(n["id"], "cancelled", error="job closed before sharing")
                continue
            near.append(json.loads(n["payload"]))
            near_ids.append(n["id"])
            continue
        if row["closed_at"] or row["status"] != QUALIFIED:
            # The job was re-evaluated after this alert was queued and no longer meets every rule.
            store.mark_notification(n["id"], "cancelled", error=f"job no longer meets all rules (now {row['status']})")
            continue
        p = RawPosting(source=(json.loads(row["sources"] or "[]") or ["db"])[0], source_url=row["canonical_url"] or "",
                       employer=row["employer"], title=row["title"], description_text="", ats=row["ats"],
                       ats_board=row["ats_board"], ats_job_id=row["ats_job_id"], canonical_url=row["canonical_url"],
                       apply_url=row["apply_url"], authoritative=bool(row["authoritative"]))
        ver = verifymod.verify(client, p, use_renderer=use_renderer)
        prev = json.loads(row["verification"] or "{}") or {}
        latest_run = store.db.execute("SELECT MAX(id) FROM runs").fetchone()[0]
        if (ver.status in ("blocked", "js_required", "error") and (prev.get("detail") or "").startswith(verifymod.FEED_LISTED)
                and row["last_run_id"] == latest_run):
            ver = Verification(**{k: v for k, v in prev.items() if k in Verification.__dataclass_fields__})  # still in the feed this search
        if ver.status != "verified":
            reason = "Checked again just before posting: " + verifymod.plain_link_reason(ver)
            store.mark_notification(n["id"], "held", error=reason)
            new_status = "Closed" if ver.status == "closed" else NEEDS_VERIFICATION
            store.db.execute("UPDATE jobs SET status=?, reasons=?, verification=?, last_verified=? WHERE id=?",
                             (new_status, json.dumps([reason]), json.dumps(ver.to_dict()), ver.checked_at, row["id"]))
            if ver.status == "closed":
                store.db.execute("UPDATE jobs SET closed_at=? WHERE id=?", (now_iso(), row["id"]))
            held.append({"notification_id": n["id"], "job_id": row["id"], "reason": reason})
            continue
        store.db.execute("UPDATE jobs SET last_verified=?, verification=?, apply_url=? WHERE id=?",
                         (ver.checked_at, json.dumps(ver.to_dict()), ver.apply_url, row["id"]))
        payload = build_payload(store.job(row["id"]))
        items.append((n["kind"], payload))
        ids.append(n["id"])
    # Jobs are recorded as shown only after the results page is written (record_shown), so a failed
    # results page never hides a new job.
    store.commit()
    from .notify import render_digest
    out = {"notification_ids": ids + near_ids, "match_ids": ids, "near_miss_ids": near_ids, "held": held,
           "draft": render_digest(items, near) if (items or near) else None}
    store.db.close()
    return out
