"""SQLite persistence: jobs, runs, notifications, source status.

Deduplication keys (in order): (ats, ats_job_id) -> (employer, requisition_id) -> canonical URL
-> any known source URL -> (employer, normalized title, normalized location).
Repost detection: same employer + normalized title but a different ATS id / requisition id
-> linked via repost_of with a confidence note; never assumed new without flagging.
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  employer TEXT, title TEXT, track TEXT, family TEXT,
  norm_key TEXT, ats TEXT, ats_board TEXT, ats_job_id TEXT, requisition_id TEXT,
  canonical_url TEXT, apply_url TEXT, apply_route TEXT, source_urls TEXT DEFAULT '[]', sources TEXT DEFAULT '[]',
  location_text TEXT, first_seen TEXT, last_seen TEXT, last_verified TEXT, published_date TEXT,
  status TEXT, prev_status TEXT, reasons TEXT, rules TEXT, verification TEXT, fit TEXT,
  cert_conditions TEXT, salary TEXT, location TEXT, travel TEXT,
  content_hash TEXT, authoritative INTEGER DEFAULT 0, repost_of INTEGER, repost_note TEXT,
  first_qualified TEXT, baseline INTEGER DEFAULT 0, closed_at TEXT, compensation_type TEXT,
  last_run_id INTEGER
);
CREATE INDEX IF NOT EXISTS ix_jobs_ats ON jobs(ats, ats_job_id);
CREATE INDEX IF NOT EXISTS ix_jobs_norm ON jobs(norm_key);
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT, finished_at TEXT, mode TEXT,
  is_baseline INTEGER DEFAULT 0, success INTEGER DEFAULT 0, counts TEXT, sources TEXT, errors TEXT
);
CREATE TABLE IF NOT EXISTS notifications (
  id INTEGER PRIMARY KEY AUTOINCREMENT, job_id INTEGER, kind TEXT, created_at TEXT,
  status TEXT,          -- pending | delivered | failed | held | suppressed_baseline | cancelled
  attempts INTEGER DEFAULT 0, last_error TEXT, delivered_at TEXT, channel TEXT, channel_ref TEXT,
  payload TEXT, run_id INTEGER
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_notif_job_kind ON notifications(job_id, kind);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def norm_title(t: str) -> str:
    t = (t or "").lower()
    t = re.sub(r"\b(sr\.?|senior)\b", "senior", t)
    t = re.sub(r"\b(remote|us|usa|united states|\(.*?\)|-\s*remote)\b", " ", t)
    t = re.sub(r"[^a-z0-9]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def norm_employer(e: str) -> str:
    e = (e or "").lower()
    e = re.sub(r"\b(inc|llc|ltd|corp|corporation|co|company|pbc|the)\b\.?", " ", e)
    return re.sub(r"[^a-z0-9]+", "", e)


def norm_url(u: str | None) -> str | None:
    if not u:
        return None
    u = u.strip()
    u = re.sub(r"[?#].*$", "", u) if not re.search(r"[?&](gh_jid|token|jobId|id)=", u) else re.sub(r"&?utm_[^&]+", "", u)
    return u.rstrip("/").lower()


def norm_key(employer: str, title: str, location: str = "") -> str:
    # Location is deliberately excluded: boards label the same remote role differently
    # ("Remote", "United States", "USA"); distinct ATS ids still prevent false merges.
    return f"{norm_employer(employer)}|{norm_title(title)}"


class Store:
    def __init__(self, path: str | Path):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    # ------------------------------------------------------------- meta
    def get_meta(self, k: str, default=None):
        r = self.db.execute("SELECT value FROM meta WHERE key=?", (k,)).fetchone()
        return json.loads(r["value"]) if r else default

    def set_meta(self, k: str, v):
        self.db.execute("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, json.dumps(v)))

    def baseline_done(self) -> bool:
        return bool(self.db.execute("SELECT 1 FROM runs WHERE is_baseline=1 AND success=1").fetchone())

    # ------------------------------------------------------------- runs
    def start_run(self, mode: str) -> int:
        cur = self.db.execute("INSERT INTO runs(started_at, mode) VALUES(?,?)", (now_iso(), mode))
        return cur.lastrowid

    def finish_run(self, run_id: int, success: bool, is_baseline: bool, counts: dict, sources: list, errors: list):
        self.db.execute("UPDATE runs SET finished_at=?, success=?, is_baseline=?, counts=?, sources=?, errors=? WHERE id=?",
                        (now_iso(), int(success), int(is_baseline), json.dumps(counts), json.dumps(sources), json.dumps(errors), run_id))
        self.db.commit()

    # ------------------------------------------------------------- dedupe
    def find_existing(self, rec: dict) -> sqlite3.Row | None:
        q = self.db.execute
        if rec.get("ats") and rec.get("ats_job_id"):
            r = q("SELECT * FROM jobs WHERE ats=? AND ats_job_id=?", (rec["ats"], rec["ats_job_id"])).fetchone()
            if r:
                return r
        if rec.get("requisition_id"):
            for r in q("SELECT * FROM jobs WHERE requisition_id=?", (rec["requisition_id"],)).fetchall():
                if norm_employer(r["employer"]) == norm_employer(rec["employer"]):
                    return r
        cu = norm_url(rec.get("canonical_url"))
        urls = {norm_url(u) for u in rec.get("source_urls", []) + [rec.get("canonical_url"), rec.get("apply_url")] if u}
        if urls:
            for r in q("SELECT * FROM jobs").fetchall():
                known = {norm_url(u) for u in json.loads(r["source_urls"] or "[]") + [r["canonical_url"], r["apply_url"]] if u}
                if (cu and cu in known) or (urls & known):
                    return r
        r = q("SELECT * FROM jobs WHERE norm_key=? AND closed_at IS NULL ORDER BY id LIMIT 1", (rec["norm_key"],)).fetchone()
        if r and not (r["ats_job_id"] and rec.get("ats_job_id") and r["ats_job_id"] != rec["ats_job_id"] and r["ats"] == rec.get("ats")):
            return r
        return None

    def find_repost_candidate(self, rec: dict) -> sqlite3.Row | None:
        """Same employer + normalized title, different ATS id/requisition (possibly reposted)."""
        rows = self.db.execute("SELECT * FROM jobs WHERE norm_key=?", (rec["norm_key"],)).fetchall()
        for r in rows:
            if (r["ats_job_id"] and r["ats_job_id"] != rec.get("ats_job_id")) or (r["requisition_id"] and r["requisition_id"] != rec.get("requisition_id")):
                return r
            if r["content_hash"] and r["content_hash"] == rec.get("content_hash"):
                return r
        return None

    # ------------------------------------------------------------- upsert
    JSON_FIELDS = ("source_urls", "sources", "reasons", "rules", "verification", "fit", "cert_conditions",
                   "salary", "location", "travel")

    def upsert(self, rec: dict, run_id: int) -> tuple[int, str]:
        """Insert or update. Returns (job_id, change) where change in new|updated|unchanged|content_changed."""
        ts = now_iso()
        existing = self.find_existing(rec)
        vals = {k: (json.dumps(v) if k in self.JSON_FIELDS else v) for k, v in rec.items() if k not in ("id",)}
        if existing is None:
            rp = self.find_repost_candidate(rec)
            if rp is not None:
                vals["repost_of"] = rp["id"]
                same = rp["content_hash"] == rec.get("content_hash")
                vals["repost_note"] = (f"Possible repost of job #{rp['id']} (same employer/title; "
                                       f"{'identical' if same else 'different'} description; "
                                       f"previous id {rp['ats_job_id'] or rp['requisition_id'] or 'n/a'}, now {rec.get('ats_job_id') or rec.get('requisition_id') or 'n/a'})")
            vals.update(first_seen=ts, last_seen=ts, last_run_id=run_id)
            cols = ",".join(vals)
            cur = self.db.execute(f"INSERT INTO jobs({cols}) VALUES({','.join('?' * len(vals))})", list(vals.values()))
            return cur.lastrowid, "new"
        jid = existing["id"]
        if existing["closed_at"] and not rec.get("authoritative") and re.search(
                r"replaced by the employer's own posting|employer's own posting is closed", existing["reasons"] or ""):
            # a third-party site still lists a job the employer has replaced or closed: keep it closed
            self.db.execute("UPDATE jobs SET last_seen=?, last_run_id=? WHERE id=?", (ts, run_id, jid))
            return jid, "unchanged"
        old_urls = json.loads(existing["source_urls"] or "[]")
        merged_urls = list(dict.fromkeys(old_urls + rec.get("source_urls", [])))
        old_sources = json.loads(existing["sources"] or "[]")
        merged_sources = list(dict.fromkeys(old_sources + rec.get("sources", [])))
        vals["source_urls"] = json.dumps(merged_urls)
        vals["sources"] = json.dumps(merged_sources)
        # keep authoritative data if the new record is a weaker third-party copy
        if existing["authoritative"] and not rec.get("authoritative"):
            keep = {"source_urls": vals["source_urls"], "sources": vals["sources"]}
            self.db.execute("UPDATE jobs SET source_urls=?, sources=?, last_seen=?, last_run_id=? WHERE id=?",
                            (keep["source_urls"], keep["sources"], ts, run_id, jid))
            return jid, "unchanged"
        change = "content_changed" if existing["content_hash"] != rec.get("content_hash") else "updated"
        vals["prev_status"] = existing["status"]
        vals["last_seen"] = ts
        vals["last_run_id"] = run_id
        vals["closed_at"] = None
        for k in ("first_seen", "first_qualified", "baseline", "repost_of", "repost_note"):
            vals.pop(k, None)
        sets = ",".join(f"{k}=?" for k in vals)
        self.db.execute(f"UPDATE jobs SET {sets} WHERE id=?", list(vals.values()) + [jid])
        return jid, change

    def mark_closed(self, job_id: int, detail: str):
        self.db.execute("UPDATE jobs SET closed_at=?, prev_status=status, status=?, reasons=? WHERE id=?",
                        (now_iso(), "Closed", json.dumps([detail]), job_id))

    def job(self, job_id: int) -> sqlite3.Row:
        return self.db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()

    def jobs(self, where: str = "1=1", args=()) -> list[sqlite3.Row]:
        return self.db.execute(f"SELECT * FROM jobs WHERE {where} ORDER BY id", args).fetchall()

    # ------------------------------------------------------------- notifications
    def ever_notified(self, job_id: int) -> bool:
        r = self.db.execute("SELECT 1 FROM notifications WHERE job_id=? AND kind != 'near_miss' AND status IN ('delivered','suppressed_baseline')", (job_id,)).fetchone()
        return bool(r)

    def repost_chain_notified(self, job_id: int) -> bool:
        j = self.job(job_id)
        seen = set()
        while j is not None and j["repost_of"] and j["repost_of"] not in seen:
            seen.add(j["repost_of"])
            if self.ever_notified(j["repost_of"]):
                return True
            j = self.job(j["repost_of"])
        return False

    def queue_notification(self, job_id: int, kind: str, payload: dict, run_id: int, status: str = "pending") -> int | None:
        try:
            cur = self.db.execute(
                "INSERT INTO notifications(job_id, kind, created_at, status, payload, run_id) VALUES(?,?,?,?,?,?)",
                (job_id, kind, now_iso(), status, json.dumps(payload), run_id))
            return cur.lastrowid
        except sqlite3.IntegrityError:
            return None  # already queued/sent for this job+kind

    def has_notification(self, job_id: int) -> bool:
        return bool(self.db.execute("SELECT 1 FROM notifications WHERE job_id=?", (job_id,)).fetchone())

    def pending_notifications(self) -> list[sqlite3.Row]:
        return self.db.execute("SELECT * FROM notifications WHERE status IN ('pending','failed') ORDER BY id").fetchall()

    def mark_notification(self, nid: int, status: str, channel: str | None = None, ref: str | None = None, error: str | None = None):
        row = self.db.execute("SELECT status FROM notifications WHERE id=?", (nid,)).fetchone()
        if row is None:
            raise KeyError(f"notification {nid} not found")
        if row["status"] == "delivered" and status != "delivered":
            raise ValueError(f"notification {nid} already delivered; refusing to change to {status}")
        if row["status"] == "delivered" and status == "delivered":
            return  # idempotent
        self.db.execute(
            "UPDATE notifications SET status=?, attempts=attempts+1, channel=COALESCE(?,channel), channel_ref=COALESCE(?,channel_ref), "
            "last_error=?, delivered_at=CASE WHEN ?='delivered' THEN ? ELSE delivered_at END WHERE id=?",
            (status, channel, ref, error, status, now_iso(), nid))
        self.db.commit()

    def commit(self):
        self.db.commit()
