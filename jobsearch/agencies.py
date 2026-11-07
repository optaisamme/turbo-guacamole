"""Recruiting agencies that post jobs for unnamed employers. One list, kept in
jobsearch/advanced_settings.toml (sources.intermediary_boards). Their postings are treated as
third-party, and their boards are never read or saved as company careers pages."""
from __future__ import annotations

import functools
import tomllib
from pathlib import Path


@functools.lru_cache(maxsize=1)
def agency_names() -> frozenset[str]:
    with open(Path(__file__).with_name("advanced_settings.toml"), "rb") as f:
        names = tomllib.load(f).get("sources", {}).get("intermediary_boards", [])
    return frozenset(n.lower().replace(" ", "") for n in names)


def is_agency(name: str | None) -> bool:
    """True for an agency's board name or company name ("hirehangar", "Hire Hangar")."""
    return (name or "").lower().replace(" ", "") in agency_names()
