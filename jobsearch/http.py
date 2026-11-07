"""HTTP client: per-host rate limiting, retries with exponential backoff, redirect
tracking, robots.txt respect for HTML pages. Never bypasses CAPTCHAs or auth."""
from __future__ import annotations

import ipaddress
import json
import logging
import random
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from dataclasses import dataclass, field

log = logging.getLogger("jobsearch.http")


class AccessBlocked(Exception):
    """Raised when access is denied (403/401/CAPTCHA/proxy/robots). Not retried."""


class UnsafeURL(AccessBlocked):
    """A link this tool refuses to open: not a normal web address, or one that points at this computer
    or a private network (job listings are untrusted and could contain such links)."""


ALLOWED_PORTS = (None, 80, 443)
BLOCKED_HOST_SUFFIXES = (".local", ".localhost", ".internal", ".lan", ".home.arpa")
MAX_REDIRECTS = 5
MAX_BODY_BYTES = 10_000_000


def _bad_ip(ip: ipaddress._BaseAddress) -> bool:
    return (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved
            or ip.is_unspecified or getattr(ip, "is_site_local", False)
            or (isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None and _bad_ip(ip.ipv4_mapped)))


def check_url(url: str, resolve=socket.getaddrinfo) -> None:
    """Raise UnsafeURL unless the link is a normal public http(s) web address."""
    try:
        p = urllib.parse.urlsplit(url)
        port = p.port
    except ValueError as e:
        raise UnsafeURL(f"not a valid web address: {url[:200]}") from e
    if p.scheme not in ("http", "https"):
        raise UnsafeURL(f"not a web address (http or https): {url[:200]}")
    host = (p.hostname or "").rstrip(".").lower()
    if not host:
        raise UnsafeURL(f"no host name: {url[:200]}")
    if port not in ALLOWED_PORTS:
        raise UnsafeURL(f"unusual port {port}: {url[:200]}")
    if host == "localhost" or host.endswith(BLOCKED_HOST_SUFFIXES):
        raise UnsafeURL(f"points at this computer or a private network: {host}")
    try:
        ips = [ipaddress.ip_address(host)]
    except ValueError:
        try:
            ips = [ipaddress.ip_address(a[4][0].split("%")[0]) for a in resolve(host, None)]
        except (OSError, ValueError, UnicodeError):
            return   # the name does not resolve here; the request itself will fail
    for ip in ips:
        if _bad_ip(ip):
            raise UnsafeURL(f"points at this computer or a private network: {host} ({ip})")


@dataclass
class Response:
    url: str
    final_url: str
    status: int
    headers: dict
    body: str
    redirect_chain: list[str] = field(default_factory=list)

    def json(self):
        return json.loads(self.body)


class _RedirectRecorder(urllib.request.HTTPRedirectHandler):
    max_redirections = MAX_REDIRECTS

    def __init__(self, check=check_url):
        self.chain: list[str] = []
        self.check = check

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self.check(newurl)   # every redirect is checked like the first link
        self.chain.append(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


CAPTCHA_MARKERS = ("captcha", "cf-challenge", "are you a robot", "verify you are human", "access denied", "px-captcha")


class HttpClient:
    def __init__(self, user_agent: str, min_interval: float = 1.5, max_retries: int = 4, timeout: float = 25,
                 respect_robots: bool = True, sleep=time.sleep, check=check_url):
        self.ua = user_agent
        self.min_interval = min_interval
        self.max_retries = max_retries
        self.timeout = timeout
        self.respect_robots = respect_robots
        self._last: dict[str, float] = {}
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self.sleep = sleep
        self.check = check
        self.stats: dict[str, dict] = {}

    def _throttle(self, host: str):
        last = self._last.get(host)
        if last is not None:
            wait = self.min_interval - (time.monotonic() - last)
            if wait > 0:
                self.sleep(wait)
        self._last[host] = time.monotonic()

    def robots_allowed(self, url: str) -> bool:
        p = urllib.parse.urlsplit(url)
        base = f"{p.scheme}://{p.netloc}"
        if base not in self._robots:
            rp = urllib.robotparser.RobotFileParser()
            try:
                r = self._raw("GET", base + "/robots.txt", {}, None, check_robots=False, retries=1)
                if r.status >= 400:
                    rp = None
                else:
                    rp.parse(r.body.splitlines())
            except Exception:
                rp = None  # robots unreachable: treat as no explicit disallow
            self._robots[base] = rp
        rp = self._robots[base]
        return True if rp is None else rp.can_fetch(self.ua, url)

    def get(self, url: str, headers: dict | None = None, html: bool = False) -> Response:
        if html and self.respect_robots and not self.robots_allowed(url):
            raise AccessBlocked(f"robots.txt disallows {url}")
        return self._raw("GET", url, headers or {}, None, check_robots=False)

    def post_json(self, url: str, payload: dict, headers: dict | None = None) -> Response:
        h = {"Content-Type": "application/json", **(headers or {})}
        return self._raw("POST", url, h, json.dumps(payload).encode(), check_robots=False)

    def _raw(self, method, url, headers, data, check_robots=False, retries=None) -> Response:
        host = urllib.parse.urlsplit(url).netloc
        st = self.stats.setdefault(host, {"requests": 0, "errors": 0, "blocked": 0})
        retries = self.max_retries if retries is None else retries
        attempt = 0
        while True:
            attempt += 1
            self.check(url)
            self._throttle(host)
            rec = _RedirectRecorder(self.check)
            opener = urllib.request.build_opener(rec)
            req = urllib.request.Request(url, data=data, method=method,
                                         headers={"User-Agent": self.ua, "Accept": "*/*", **headers})
            st["requests"] += 1
            try:
                with opener.open(req, timeout=self.timeout) as r:
                    body = r.read(MAX_BODY_BYTES).decode(r.headers.get_content_charset() or "utf-8", "replace")
                    resp = Response(url, r.geturl(), r.status, dict(r.headers), body, rec.chain)
                    low = body[:20000].lower()
                    if any(m in low for m in CAPTCHA_MARKERS) and len(body) < 20000:
                        st["blocked"] += 1
                        raise AccessBlocked(f"challenge/CAPTCHA page at {url}")
                    return resp
            except urllib.error.HTTPError as e:
                body = ""
                try:
                    body = e.read(200_000).decode("utf-8", "replace")
                except Exception:
                    pass
                if e.code in (401, 403, 407):
                    st["blocked"] += 1
                    raise AccessBlocked(f"HTTP {e.code} at {url}")
                if e.code in (429, 500, 502, 503, 504) and attempt <= retries:
                    ra = e.headers.get("Retry-After") if e.headers else None
                    delay = float(ra) if ra and ra.isdigit() else min(60, 2 ** attempt) + random.random()
                    log.warning("retry %s after HTTP %s in %.1fs", url, e.code, delay)
                    self.sleep(delay)
                    continue
                st["errors"] += 1
                return Response(url, e.geturl() if hasattr(e, "geturl") else url, e.code, dict(e.headers or {}), body, rec.chain)
            except AccessBlocked:
                raise
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
                msg = str(getattr(e, "reason", e))
                if "403" in msg or "Tunnel" in msg or "tunnel" in msg:
                    st["blocked"] += 1
                    raise AccessBlocked(f"network policy/proxy denied {host}: {msg}")
                if "CERTIFICATE_VERIFY_FAILED" in msg or "certificate verify failed" in msg:
                    st["errors"] += 1
                    raise   # a bad security certificate does not fix itself on retry
                if attempt <= retries:
                    delay = min(60, 2 ** attempt) + random.random()
                    log.warning("retry %s after %s in %.1fs", url, msg, delay)
                    self.sleep(delay)
                    continue
                st["errors"] += 1
                raise
