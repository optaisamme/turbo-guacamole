"""Turn technical problem messages from a search into plain sentences for the report.
Used when the report is written, so messages saved by older searches are translated too."""
from __future__ import annotations

import re
from urllib.parse import urlsplit

URL = re.compile(r"https?://[^\s'\"]+")


def _site_names() -> dict:
    from .sources_summary import SITES
    return {name: shown for name, shown, _ in SITES}


def _problem(text: str) -> str:
    t = text.lower()
    if "robots.txt" in t:
        return "the site's rules for automated tools ask this tool not to open that page, so it was not opened"
    if "403" in t or "accessblocked" in t or "proxy denied" in t or "forbidden" in t:
        return "the site refused automated access (error 403), so that page could not be opened"
    if "404" in t:
        return "the page was not found (error 404)"
    if "429" in t:
        return "the site asked this tool to slow down (error 429, too many requests)"
    m = re.search(r"\b5\d\d\b", t)
    if m:
        return f"the site had a temporary problem (error {m.group(0)})"
    if "timed out" in t or "timeout" in t:
        return "the site did not respond in time"
    if "certificate" in t or "ssl" in t:
        return "the site's security certificate could not be checked, so the page was not opened"
    if "name or service not known" in t or "getaddrinfo" in t or "nodename" in t:
        return "the site could not be reached (address not found)"
    if "key was not accepted" in t or "allowance is used up" in t:
        return text.split(": ", 1)[-1]
    detail = re.sub(r"\s+", " ", text).strip()[:160]
    return f"something went wrong (technical detail: {detail})"


def plain_error(msg: str) -> str:
    """'remoteok: query 'x': HTTP 404' -> 'Remote OK, while searching for "x": the page was not found (error 404).'"""
    names = _site_names()
    rest = msg or ""
    site = ""
    head, sep, tail = rest.partition(": ")
    if sep and head in names:
        site, rest = names[head], tail
    context = ""
    m = re.match(r"query '([^']*)'(?: page (\d+))?: ", rest)
    if m:
        context = f'while searching for "{m.group(1)}"' + (f" (results page {m.group(2)})" if m.group(2) else "")
        rest = rest[m.end():]
    elif rest.startswith("newest jobs feed: "):
        context, rest = "while reading its newest jobs", rest[len("newest jobs feed: "):]
    elif re.match(r"page \d+: ", rest):
        n = rest.split(":", 1)[0]
        context, rest = f"while reading {n}", rest.split(": ", 1)[1]
    elif rest.startswith("resolve "):
        context, rest = "while looking for the employer's own posting", rest[len("resolve "):]
    elif rest.startswith("verify "):
        context, rest = "while checking an application link", rest[len("verify "):]
    elif rest.startswith("recheck job "):
        n = rest[len("recheck job "):].split(":", 1)[0]
        context, rest = f"while re-checking job #{n}", rest.split(": ", 1)[1] if ": " in rest else rest
    elif re.match(r"(greenhouse|lever|ashby|smartrecruiters):[^:]+: ", rest):
        board = rest.split(": ", 1)[0]
        context, rest = f"while reading the careers page {board.split(':', 1)[1]}", rest.split(": ", 1)[1]
    urls = URL.findall(rest)
    url = urls[0].rstrip(".,;):") if urls else ""
    host = urlsplit(url).netloc.replace("www.", "") if url else ""
    where = ", ".join(x for x in (site, context) if x)
    sentence = (f"{where}: " if where else "") + _problem(rest)
    if host:
        sentence = sentence.replace("the site", host, 1)
    if not (host and sentence.startswith(host)):
        sentence = sentence[0].upper() + sentence[1:]
    sentence += "."
    if url:
        sentence += f" Page: {url}"
    return sentence
