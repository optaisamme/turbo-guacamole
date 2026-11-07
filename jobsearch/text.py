"""Text normalization and section-aware segmentation of job descriptions.

All posting content is untrusted data: it is only parsed with regular expressions,
never executed or interpreted as instructions.
"""
from __future__ import annotations

import hashlib
import html as _html
import re
from dataclasses import dataclass

_BLOCK_TAGS = re.compile(r"</?(p|div|br|li|ul|ol|h[1-6]|tr|section|article|header|footer|strong|b)\b[^>]*>", re.I)
_TAG = re.compile(r"<[^>]+>")
_SCRIPT = re.compile(r"<(script|style|noscript)\b.*?</\1>", re.I | re.S)


def html_to_text(raw: str | None) -> str:
    if not raw:
        return ""
    s = raw
    # Some APIs return HTML-escaped HTML.
    if "&lt;" in s and "<" not in s:
        s = _html.unescape(s)
    s = _SCRIPT.sub(" ", s)
    s = re.sub(r"<li\b[^>]*>", "\n- ", s, flags=re.I)
    s = _BLOCK_TAGS.sub("\n", s)
    s = _TAG.sub(" ", s)
    s = _html.unescape(s)
    s = s.replace("\xa0", " ")
    s = re.sub(r"[ \t\r\f\v]+", " ", s)
    s = re.sub(r"\n\s*\n+", "\n", s)
    return s.strip()


def norm(s: str | None) -> str:
    return re.sub(r"\s+", " ", (s or "").lower()).strip()


def content_hash(*parts: str) -> str:
    """Hash of normalized content: whitespace/case/punctuation/number-insensitive
    so trivial edits and refreshed dates do not register as new content."""
    joined = " ".join(norm(p) for p in parts if p)
    joined = re.sub(r"\d{1,2}/\d{1,2}/\d{2,4}|\d{4}-\d{2}-\d{2}", " ", joined)
    joined = re.sub(r"[^a-z0-9$]+", " ", joined)
    joined = re.sub(r"\s+", " ", joined).strip()
    return hashlib.sha256(joined.encode()).hexdigest()[:20]


SECTION_PATTERNS = [
    ("preferred", re.compile(r"\b(preferred|nice[\s-]to[\s-]have|bonus\s+points|bonus|a\s+plus|pluses|desired|desirable|ideal(ly)?|extra\s+credit|would\s+be\s+great|optional)\b", re.I)),
    ("other", re.compile(r"\b(about\s+(us|the\s+(company|organization|team))|who\s+we\s+are|our\s+(mission|story|values|company)|equal\s+(employment\s+)?opportunity|eeo)\b", re.I)),
    ("benefits", re.compile(r"\b(benefits?|perks|compensation|salary|pay\s+range|pay\s+transparency|what\s+we\s+offer)\b", re.I)),
    ("responsibilities", re.compile(r"\b(responsibilit(y|ies)|what\s+you('|’)?ll\s+do|duties|day[\s-]to[\s-]day|your\s+impact|in\s+this\s+role|the\s+role|key\s+functions|essential\s+functions|you\s+will)\b", re.I)),
    ("required", re.compile(r"\b(requirements?|required|qualifications?|must[\s-]haves?|what\s+you('|’)?ll\s+need|what\s+you\s+bring|what\s+we('|’)?re\s+looking\s+for|who\s+you\s+are|about\s+you|you\s+have|skills|experience|minimum|basic|knowledge)\b", re.I)),
]


@dataclass
class Segment:
    text: str
    section: str  # required | preferred | responsibilities | benefits | other


def _is_heading(line: str) -> bool:
    raw = line.strip()
    if not raw or raw[0] in "-•*·▪◦" or len(raw) > 80:
        return False
    l = raw.strip()
    if l.endswith(":"):
        return len(l.split()) <= 8
    words = l.split()
    if len(words) > 6 or re.search(r"[.;,!?]\s*$", l):
        return False
    long_words = [w for w in words if len(w) > 3]
    if l.isupper():
        return True
    return bool(long_words) and sum(w[:1].isupper() for w in long_words) / len(long_words) >= 0.6


def classify_heading(line: str) -> str | None:
    for name, pat in SECTION_PATTERNS:
        if pat.search(line):
            return name
    return None


def segment(text: str) -> list[Segment]:
    """Split into sentence-level segments tagged with the section heading they fall under."""
    out: list[Segment] = []
    section = "other"
    for raw_line in text.split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        if _is_heading(line):
            h = classify_heading(line)
            if h:
                section = h
                rest = line.split(":", 1)[1].strip() if ":" in line else ""
                if rest:
                    out.append(Segment(rest, section))
                continue
        # inline "Preferred: ..." / "Required: ..."
        m = re.match(r"^[-•*\s]*(preferred|required|nice to have|bonus|must have|minimum qualifications|preferred qualifications)\s*:\s*(.+)$", line, re.I)
        if m:
            sec = classify_heading(m.group(1)) or section
            out.extend(Segment(s, sec) for s in _sentences(m.group(2)))
            continue
        out.extend(Segment(s, section) for s in _sentences(line))
    return out


def _sentences(line: str) -> list[str]:
    line = line.lstrip("-•* ").strip()
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z])|;\s+", line)
    return [p.strip() for p in parts if p.strip()]


def excerpt(s: str, limit: int = 240) -> str:
    s = re.sub(r"\s+", " ", s).strip()
    return s if len(s) <= limit else s[: limit - 1] + "…"
