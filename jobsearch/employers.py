"""Employers to skip: jobs from these employers are never shown, and their careers pages are not read."""
from __future__ import annotations

import re


def _norm(s: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


class EmployerBlock:
    def __init__(self, names):
        self.names = [n.strip() for n in (names or []) if isinstance(n, str) and n.strip()]
        words = [_norm(n) for n in self.names if _norm(n)]
        # whole words: "Acme" skips "Acme Corp" and "Acme Health", not "Acmeworks"
        self._pats = [re.compile(r"(?:^| )" + re.escape(w) + r"(?: |$)") for w in words]
        self._boards = {w.replace(" ", "") for w in words}   # careers-page names have no spaces ("acmecorp")

    def blocked(self, employer: str | None, board: str | None = None) -> bool:
        e = _norm(employer)
        if e and any(p.search(e) for p in self._pats):
            return True
        return bool(board) and _norm(board).replace(" ", "") in self._boards
