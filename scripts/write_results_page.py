"""Update the JOB_SEARCH_RESULTS folder (numbered report + folder page) after a search,
and show the same results on the GitHub Actions run page."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from jobsearch import results_folder  # noqa: E402


def main() -> int:
    out = results_folder.write(ROOT)
    # only now, with the page written, are this search's new jobs recorded as shown
    outbox_p = ROOT / "out/outbox.json"
    if outbox_p.exists() and (ROOT / "data/jobs.sqlite").exists():
        import json
        from jobsearch.pipeline import record_shown
        record_shown(ROOT / "data/jobs.sqlite", json.loads(outbox_p.read_text()).get("notification_ids", []))
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        base = f"{os.environ.get('GITHUB_SERVER_URL', 'https://github.com')}/{os.environ.get('GITHUB_REPOSITORY', '')}/blob/main/{results_folder.FOLDER}/"
        repo_url = f"{os.environ.get('GITHUB_SERVER_URL', 'https://github.com')}/{os.environ.get('GITHUB_REPOSITORY', '')}/"
        # links on the results page are relative to the folder; on the Actions page they must be full links
        text = (out["text"].replace("](REPORT_", "](" + base + "REPORT_")
                .replace("](../../../", "](" + repo_url).replace("](../", "](" + repo_url + "blob/main/"))
        with open(summary, "a") as f:
            f.write(text + f"\n\nAll results: {base}README.md\n")
    print(f"Updated {results_folder.FOLDER}: {out['report'] or 'no search yet'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
