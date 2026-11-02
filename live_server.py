#!/usr/bin/env python3
"""The live gamma exposure service: Schwab's option chains, read when a page asks.

Runs on Google Cloud Run (see the README). One endpoint:

  GET /chain?symbol=NVDA&expiry=2026-11-06    one expiration (one Schwab call)
  GET /chain?symbol=NVDA&expiry=all           every expiration (SPY's takes five calls)

It answers with the same snapshot nvda_gex.py writes from Cboe, so the page draws
either one, plus `loginExpires`, when the Schwab login runs out. Open interest is
Schwab's, which is missing on some contracts; the page puts the OCC's in its place.

Nothing is stored. A reply is kept in memory for a few seconds, so pages open on
several devices share one call, and the service stops calling Schwab beyond
MAX_CALLS a minute, half of Schwab's limit of 120.

The login comes from Secret Manager (SECRET, written by schwab_login.py), or with
LOCAL_STORE=1 from this computer's copy, for a test run. Nothing logged or
returned holds a key, a secret or a token.
"""
from __future__ import annotations

import base64
import collections
import datetime as dt
import gzip
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import nvda_gex
import nvda_oi
import schwab_api
from nvda_oi import ET
from schwab_api import SchwabError

SECRET = os.environ.get("SECRET", "schwab-credentials")
LOCAL_STORE = os.environ.get("LOCAL_STORE") == "1"
ALLOWED_ORIGINS = set(filter(None, os.environ.get(
    "ALLOWED_ORIGINS", "https://kshu231.github.io").split(",")))

MAX_CALLS = 60                       # Schwab calls a minute, for every page together
FRESH = {"day": 15, "all": 45}       # seconds a reply is served again from memory
RELOAD_AFTER = 60                    # seconds before a refused login is read again
REREAD = 300                         # seconds before the saved login is read again anyway
LONGEST = dt.timedelta(days=3 * 366)  # the furthest expiration asked for
WINDOW_CALLS = len(schwab_api.WINDOWS)

METADATA = "http://metadata.google.internal/computeMetadata/v1/"
SECRETS_API = "https://secretmanager.googleapis.com/v1/projects/{project}/secrets/{secret}/versions/latest:access"


class Busy(Exception):
    """Calling Schwab now would go past MAX_CALLS."""


# --------------------------------------------------------------------------- #
# The login
# --------------------------------------------------------------------------- #

def metadata(path: str) -> bytes:
    request = urllib.request.Request(METADATA + path, headers={"Metadata-Flavor": "Google"})
    with urllib.request.urlopen(request, timeout=10) as response:
        return response.read()


def read_secret() -> dict:
    """The latest login schwab_login.py saved to Secret Manager."""
    try:
        project = metadata("project/project-id").decode()
        token = json.loads(metadata("instance/service-accounts/default/token"))["access_token"]
        request = urllib.request.Request(SECRETS_API.format(project=project, secret=SECRET),
                                         headers={"Authorization": "Bearer " + token})
        with urllib.request.urlopen(request, timeout=10) as response:
            payload = json.loads(response.read())["payload"]["data"]
        return json.loads(base64.b64decode(payload))
    except (urllib.error.URLError, TimeoutError, ValueError, KeyError) as exc:
        raise SchwabError(f"could not read the saved login: {type(exc).__name__}") from None


class Login:
    """The Schwab login, renewed as it runs out, read again when Schwab refuses it."""

    def __init__(self):
        self.lock = threading.Lock()
        self.store: dict = {}
        self.read_at = 0.0

    def load(self) -> None:
        store = schwab_api.load_store() if LOCAL_STORE else read_secret()
        if store.get("refresh_token") == self.store.get("refresh_token"):
            # The same login: keep the access token made from it.
            for key in ("access_token", "access_expires"):
                if key in self.store:
                    store[key] = self.store[key]
        self.store = store
        self.read_at = time.time()

    def token(self) -> str:
        with self.lock:
            # Read every few minutes, so a new login replaces the old one before it runs out.
            if not self.store or time.time() - self.read_at > REREAD:
                self.load()
            try:
                return schwab_api.renew(self.store)
            except SchwabError:
                # Perhaps schwab_login.py has saved a new login since.
                if time.time() - self.read_at < RELOAD_AFTER:
                    raise
                self.load()
                return schwab_api.renew(self.store)

    def forget_access(self) -> None:
        with self.lock:
            self.store.pop("access_token", None)

    def expires(self) -> str | None:
        signed_in = self.store.get("logged_in_at")
        if not signed_in:
            return None
        at = dt.datetime.fromtimestamp(signed_in + schwab_api.REFRESH_DAYS * 86400, ET)
        return at.isoformat(timespec="seconds")


LOGIN = Login()


# --------------------------------------------------------------------------- #
# Calling Schwab
# --------------------------------------------------------------------------- #

calls_made: collections.deque = collections.deque()
calls_lock = threading.Lock()
too_big: set[str] = set()   # symbols whose whole chain will not come back in one call


def spend(count: int) -> None:
    """Count `count` calls against the minute's allowance, or raise Busy."""
    with calls_lock:
        now = time.time()
        while calls_made and calls_made[0] < now - 60:
            calls_made.popleft()
        if len(calls_made) + count > MAX_CALLS:
            raise Busy()
        calls_made.extend([now] * count)


def fetch(symbol: str, expiry: str) -> dict:
    """The chain as a snapshot for the page."""
    if expiry == "all":
        split = symbol in too_big
        spend(WINDOW_CALLS if split else 1 + WINDOW_CALLS)
        body, calls = schwab_api.whole_chain(LOGIN.token(), symbol, pause=0.2, split=split)
        if body and len(calls) > 1:
            too_big.add(symbol)
    else:
        spend(1)
        calls = [schwab_api.get_chain(LOGIN.token(), symbol, fromDate=expiry, toDate=expiry)]
        body = calls[0]["body"] if schwab_api.succeeded(calls[0]) else None
    if body is None:
        last = calls[-1]
        if last["status"] == 401:
            LOGIN.forget_access()
        raise SchwabError(f"chain request answered {last['status']}")
    now = dt.datetime.now(ET)
    try:
        snapshot = nvda_gex.build_snapshot(symbol, schwab_api.as_cboe(body), now)
    except nvda_gex.NoVolatilityYet:
        raise
    except nvda_gex.CboeError:
        if any(schwab_api.contracts(body)):
            raise
        # Expired, or a day with no options: an empty chart rather than an error.
        snapshot = {"symbol": symbol, "spot": schwab_api.spot_of(body),
                    "quoteTime": now.isoformat(timespec="seconds"),
                    "pulled": now.isoformat(timespec="seconds"), "rate": nvda_gex.RATE, "expiries": []}
    snapshot["source"] = "Schwab"
    snapshot["loginExpires"] = LOGIN.expires()
    return snapshot


cache: dict = {}
cache_locks: collections.defaultdict = collections.defaultdict(threading.Lock)


def cached(symbol: str, expiry: str) -> bytes:
    """The reply as compact JSON, from memory while it is fresh. One page at a time
    calls Schwab for the same chain; the others wait for its reply."""
    key = (symbol, expiry)
    fresh = FRESH["all" if expiry == "all" else "day"]
    with cache_locks[key]:
        hit = cache.get(key)
        if hit and time.time() - hit[0] < fresh:
            return hit[1]
        body = json.dumps(fetch(symbol, expiry), separators=(",", ":")).encode()
        cache[key] = (time.time(), body)
        # Drop replies nobody has asked for again, so memory stays small.
        for old in [k for k, v in cache.items() if time.time() - v[0] > 600]:
            cache.pop(old, None)
        return body


# --------------------------------------------------------------------------- #
# The web service
# --------------------------------------------------------------------------- #

def valid_expiry(text: str) -> bool:
    if text == "all":
        return True
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return False
    try:
        day = dt.date.fromisoformat(text)
    except ValueError:
        return False
    today = dt.datetime.now(ET).date()
    return today <= day <= today + LONGEST


class Handler(BaseHTTPRequestHandler):
    server_version = "live-gex"
    sys_version = ""

    def reply(self, status: int, body: bytes, cache_seconds: int = 0) -> None:
        origin = self.headers.get("Origin")
        zipped = len(body) > 1000 and "gzip" in self.headers.get("Accept-Encoding", "")
        if zipped:
            body = gzip.compress(body, 6)
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", f"private, max-age={cache_seconds}" if cache_seconds else "no-store")
        self.send_header("Vary", "Origin, Accept-Encoding")
        if origin in ALLOWED_ORIGINS:
            self.send_header("Access-Control-Allow-Origin", origin)
        if zipped:
            self.send_header("Content-Encoding", "gzip")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def error(self, status: int, message: str) -> None:
        self.reply(status, json.dumps({"error": message}).encode())

    def do_GET(self) -> None:
        url = urllib.parse.urlsplit(self.path)
        if url.path == "/":
            return self.reply(200, b'{"ok":true}')
        if url.path != "/chain":
            return self.error(404, "not found")
        origin = self.headers.get("Origin")
        if origin and origin not in ALLOWED_ORIGINS:
            return self.error(403, "this page may not use the service")
        query = urllib.parse.parse_qs(url.query)
        symbol = (query.get("symbol") or [""])[0].upper()
        expiry = (query.get("expiry") or [""])[0]
        if symbol not in nvda_oi.SYMBOLS or not valid_expiry(expiry):
            return self.error(400, "symbol or expiry not offered")
        try:
            self.reply(200, cached(symbol, expiry), FRESH["day"])
        except Busy:
            self.error(429, "too many requests to Schwab this minute")
        except nvda_gex.NoVolatilityYet:
            self.error(503, "Schwab's quotes carry no implied volatility yet")
        except (SchwabError, nvda_gex.CboeError) as exc:
            self.error(503, f"Schwab unavailable: {exc}")

    def log_message(self, fmt, *args) -> None:
        # The path holds only the symbol and the expiration.
        print(f"{self.command} {self.path} {args[1] if len(args) > 1 else ''}", flush=True)


def main() -> None:
    port = int(os.environ.get("PORT", "8080"))
    print(f"Listening on port {port}", flush=True)
    ThreadingHTTPServer(("", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
