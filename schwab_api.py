"""Schwab's Trader API: signing in and reading option chains.

Shared by schwab_login.py (signs in on this computer and hands the login to the
cloud service), live_server.py (the cloud service) and schwab_probe.py (a test
run by hand). Nothing here prints a key, a secret or a token.

Schwab allows 120 requests a minute per app. An access token lasts 30 minutes
and is renewed with the refresh token, which lasts 7 days; after that someone
has to sign in again in a browser.
"""
from __future__ import annotations

import base64
import datetime as dt
import getpass
import gzip
import json
import math
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

from nvda_oi import ET

AUTHORIZE_URL = "https://api.schwabapi.com/v1/oauth/authorize"
TOKEN_URL = "https://api.schwabapi.com/v1/oauth/token"
CHAINS_URL = "https://api.schwabapi.com/marketdata/v1/chains"

# This computer's copy of the app key, the app secret and the latest login,
# readable by this user only and outside the repository.
STORE = Path.home() / ".config" / "schwab-probe" / "credentials.json"
DEFAULT_CALLBACK = "https://127.0.0.1"
REFRESH_DAYS = 7
# A whole chain too big for one call (SPY's is) comes in date windows, nearest first.
WINDOWS = ((0, 7), (8, 30), (31, 90), (91, 365), (366, 1500))


class SchwabError(Exception):
    """Schwab could not be reached, or refused."""


# --------------------------------------------------------------------------- #
# Signing in
# --------------------------------------------------------------------------- #

def load_store() -> dict:
    try:
        return json.loads(STORE.read_text())
    except (OSError, ValueError):
        return {}


def save_store(store: dict) -> None:
    STORE.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = STORE.with_suffix(".tmp")
    tmp.touch(mode=0o600)
    tmp.chmod(0o600)
    tmp.write_text(json.dumps(store))
    tmp.replace(STORE)


def token_request(store: dict, fields: dict) -> dict:
    """POST to the token endpoint. Errors report Schwab's reason, never the request."""
    pair = f"{store['app_key']}:{store['app_secret']}".encode()
    request = urllib.request.Request(
        TOKEN_URL, data=urllib.parse.urlencode(fields).encode(),
        headers={"Authorization": "Basic " + base64.b64encode(pair).decode(),
                 "Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        # Schwab's reply, not the request, so it holds neither the secret nor the code.
        reason = exc.read()[:300].decode("utf-8", "replace") or "empty reply"
        raise SchwabError(f"token request refused (HTTP {exc.code}): {reason}") from None
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        raise SchwabError(f"token request failed: {type(exc).__name__}") from None


def keep_tokens(store: dict, tokens: dict, now: float, new_login: bool) -> None:
    store["access_token"] = tokens["access_token"]
    store["access_expires"] = now + int(tokens.get("expires_in", 1800))
    store["refresh_token"] = tokens.get("refresh_token", store.get("refresh_token"))
    if new_login:
        store["logged_in_at"] = now


def log_in(store: dict) -> None:
    """A browser login on this computer; asks for the app's key and secret the first time."""
    if not store.get("app_key"):
        store["app_key"] = getpass.getpass("App key (hidden): ").strip()
        store["app_secret"] = getpass.getpass("App secret (hidden): ").strip()
        answer = input(f"Callback URL registered on the app [{DEFAULT_CALLBACK}]: ").strip()
        store["callback"] = answer or DEFAULT_CALLBACK
        save_store(store)  # a login retry shouldn't ask for these again
    url = AUTHORIZE_URL + "?" + urllib.parse.urlencode(
        {"client_id": store["app_key"], "redirect_uri": store["callback"]})
    print("\nA browser window opens on Schwab's login. Sign in and approve the app. It then")
    print("lands on a page that fails to load; that is expected. Copy that page's whole")
    print("address and paste it below straight away, since the code in it lasts seconds.\n")
    if not webbrowser.open(url):
        raise SchwabError("could not open a browser")
    opened = time.time()
    pasted = getpass.getpass("Address of the page it landed on (hidden): ").strip()
    code = urllib.parse.parse_qs(urllib.parse.urlsplit(pasted).query).get("code", [""])[0]
    if not code:
        raise SchwabError("that address holds no code; run this again")
    now = time.time()
    print(f"Pasted {now - opened:.0f}s after the login page opened.")
    keep_tokens(store, token_request(store, {"grant_type": "authorization_code", "code": code,
                                             "redirect_uri": store["callback"]}), now, True)
    save_store(store)


def renew(store: dict) -> str:
    """A valid access token from the refresh token, without a browser."""
    now = time.time()
    if store.get("access_token") and now < store.get("access_expires", 0) - 60:
        return store["access_token"]
    if not store.get("refresh_token"):
        raise SchwabError("not signed in")
    keep_tokens(store, token_request(store, {"grant_type": "refresh_token",
                                             "refresh_token": store["refresh_token"]}), now, False)
    return store["access_token"]


def access_token(store: dict) -> str:
    """A valid access token for a run on this computer, logging in again if need be."""
    try:
        token = renew(store)
        save_store(store)
        return token
    except SchwabError as exc:
        if store.get("refresh_token"):
            print(f"Renewing failed, logging in again: {exc}")
    log_in(store)
    return store["access_token"]


# --------------------------------------------------------------------------- #
# Option chains
# --------------------------------------------------------------------------- #

def get_chain(token: str, symbol: str, **extra) -> dict:
    """One call. Returns its status, timing, size, rate headers and parsed body."""
    query = {"symbol": symbol, "contractType": "ALL", "strategy": "SINGLE",
             "includeUnderlyingQuote": "true", **extra}
    request = urllib.request.Request(
        CHAINS_URL + "?" + urllib.parse.urlencode(query),
        headers={"Authorization": "Bearer " + token,
                 "Accept": "application/json", "Accept-Encoding": "gzip"})
    started = time.time()
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            status, headers, raw = response.status, response.headers, response.read()
    except urllib.error.HTTPError as exc:
        status, headers, raw = exc.code, exc.headers, exc.read()
    except (urllib.error.URLError, TimeoutError) as exc:
        raise SchwabError(f"chain request failed: {type(exc).__name__}") from None
    wire = len(raw)
    if headers.get("Content-Encoding") == "gzip":
        raw = gzip.decompress(raw)
    result = {"status": status, "seconds": time.time() - started, "wire": wire, "bytes": len(raw),
              "limits": {k: v for k, v in headers.items()
                         if "rate" in k.lower() or k.lower() == "retry-after"}}
    try:
        result["body"] = json.loads(raw) if status == 200 else None
    except ValueError:
        result["status"], result["body"] = "not JSON", None
    if result["body"] is None:
        result["error"] = raw[:200].decode("utf-8", "replace")
    return result


def succeeded(call: dict) -> bool:
    return call["status"] == 200 and (call["body"].get("status") or "SUCCESS") != "FAILED"


def contracts(body: dict):
    for side in ("callExpDateMap", "putExpDateMap"):
        for strikes in (body.get(side) or {}).values():
            for rows in strikes.values():
                yield from rows


def whole_chain(token: str, symbol: str, pause: float = 1, split: bool = False) -> tuple:
    """(merged body, calls made): one call if it fits, else one per date window.
    With `split`, for a chain known to be too big, it goes straight to the windows."""
    calls = []
    if not split:
        first = get_chain(token, symbol)
        calls.append(first)
        if succeeded(first):
            return first["body"], calls
        if first["status"] in (401, 429):
            return None, calls
    today = dt.datetime.now(ET).date()
    merged = None
    for near, far in WINDOWS:
        time.sleep(pause)
        part = get_chain(token, symbol,
                         fromDate=(today + dt.timedelta(days=near)).isoformat(),
                         toDate=(today + dt.timedelta(days=far)).isoformat())
        calls.append(part)
        if not succeeded(part):
            return None, calls
        if merged is None:
            merged = part["body"]
            continue
        for side in ("callExpDateMap", "putExpDateMap"):
            merged.setdefault(side, {}).update(part["body"].get(side) or {})
    return merged, calls


def stamp(millis) -> dt.datetime | None:
    return dt.datetime.fromtimestamp(millis / 1000, ET) if millis else None


def newest(body: dict, field: str):
    return max((c.get(field) or 0 for c in contracts(body)), default=0)


def spot_of(body: dict) -> float:
    underlying = body.get("underlying") or {}
    return float(underlying.get("last") or underlying.get("mark") or body.get("underlyingPrice") or 0)


def good_iv(value) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(value) and value > 0


def as_cboe(body: dict) -> dict:
    """The chain in the layout nvda_gex.build_snapshot reads, so the chart's own
    calculation runs on it unchanged. Schwab's volatility is in percent."""
    underlying = body.get("underlying") or {}
    quoted = stamp(underlying.get("quoteTime") or newest(body, "quoteTimeInLong"))
    return {
        "current_price": spot_of(body),
        "last_trade_time": (quoted or dt.datetime.now(ET)).replace(tzinfo=None).isoformat(timespec="seconds"),
        "options": [{"option": c["symbol"].replace(" ", ""),
                     "iv": c["volatility"] / 100 if good_iv(c.get("volatility")) else 0,
                     "open_interest": c.get("openInterest") or 0,
                     "volume": c.get("totalVolume") or 0}
                    for c in contracts(body)],
    }
