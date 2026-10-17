#!/usr/bin/env python3
"""Gamma exposure snapshot for one underlying, from Cboe's delayed quotes.

Source (public, no key, 15 minutes delayed, undocumented):
  * https://cdn.cboe.com/api/global/delayed_quotes/options/NVDA.json
      The feed behind Cboe's own delayed quotes page: the underlying's price and,
      for every listed contract, implied volatility, volume and open interest.
      Its open interest is the OCC's.

Writes gex_SYMBOL.json, the snapshot the web page draws its gamma exposure
chart from, and refreshes the copy embedded in dashboard_SYMBOL.html. A pull
that fails or looks wrong leaves the previous snapshot in place.

Exit codes: 0 ok, 1 error, 75 the market is closed and --if-open was given
(nothing pulled; the previous snapshot stands).
"""
from __future__ import annotations

import argparse
import bisect
import datetime as dt
import json
import math
import re
import sys
from collections import defaultdict

import nvda_oi
from nvda_oi import ET, OCCError, embeddable, gex_path_for, http_get, page_path, rel

CHAIN_URL = "https://cdn.cboe.com/api/global/delayed_quotes/options/{symbol}.json"

RATE = 0.04                # risk-free rate; gamma is barely sensitive to it
YEAR = 365 * 86400
MIN_SECONDS = 30 * 60      # floor on time to expiry: gamma diverges in an expiration's last minutes
NEAR = 0.10                # "near the money" is within 10% of spot
MIN_IV_COVERAGE = 0.8      # share of near-the-money strikes that must carry an implied volatility

EXIT_OK, EXIT_ERROR, EXIT_CLOSED = 0, 1, 75
DELAY = dt.timedelta(minutes=15)                     # how far Cboe's quotes run behind
SESSION = (dt.time(9, 30), dt.time(16, 10))          # regular hours, plus slack for a late final pull
# Just after the open the feed lists quotes with no implied volatility yet (the 9:45
# pull, holding 9:30 quotes, found none on Oct 14-16 2026); later pulls have it.
OPENING = dt.time(10, 0)

PAGE_DATA = re.compile(r'(<script id="data" type="application/json">)(.*?)(</script>)', re.S)


class CboeError(Exception):
    """Cboe returned something unusable."""


class NoVolatilityYet(CboeError):
    """The quotes carry too little implied volatility to use."""


def quotes_in_session(now: dt.datetime) -> bool:
    """Whether the delayed quotes on offer right now are from today's regular session.

    Outside it the feed only repeats the last close, or shows thin pre-market quotes.
    Early closes are not known here; those afternoons just re-pull the closing snapshot.
    """
    start, end = SESSION
    return nvda_oi.is_trading_day(now.date()) and start <= (now - DELAY).time() <= end


# --------------------------------------------------------------------------- #
# Fetching and parsing
# --------------------------------------------------------------------------- #

def fetch_chain(symbol: str) -> dict:
    body = http_get(CHAIN_URL.format(symbol=symbol))
    try:
        return json.loads(body)["data"]
    except (ValueError, KeyError, TypeError) as exc:
        raise CboeError(f"unexpected delayed-quotes response: {body[:200]!r}") from exc


def parse_contract(code: str, symbol: str):
    """(expiry, strike, side) from an OCC code like 'NVDA261016C00220000'.

    None for another product on the same underlying (adjusted series and the like).
    """
    root, tail = code[:-15], code[-15:]
    if root != symbol:
        return None
    try:
        expiry = dt.datetime.strptime(tail[:6], "%y%m%d").date()
        side = {"C": "call", "P": "put"}[tail[6]]
        return expiry, int(tail[7:]) / 1000, side
    except (ValueError, KeyError) as exc:
        raise CboeError(f"cannot parse option code {code!r}") from exc


def strike_iv(strike: float, sides: dict, spot: float) -> float:
    """One implied volatility per strike: the out-of-the-money side's.

    In-the-money quotes are wide and Cboe often reports no volatility for them
    at all; the other side of the same strike is the fallback.
    """
    order = ("put", "call") if strike < spot else ("call", "put")
    for side in order:
        iv = sides.get(side, {}).get("iv") or 0
        if iv > 0:
            return float(iv)
    return 0.0


def fill_gaps(ivs: dict) -> None:
    """Give strikes without a volatility one interpolated from their neighbours."""
    known = sorted(k for k, iv in ivs.items() if iv > 0)
    if not known:
        return
    for strike in [k for k, iv in ivs.items() if iv <= 0]:
        i = bisect.bisect(known, strike)
        if 0 < i < len(known):
            lo, hi = known[i - 1], known[i]
            ivs[strike] = ivs[lo] + (ivs[hi] - ivs[lo]) * (strike - lo) / (hi - lo)
        else:
            ivs[strike] = ivs[known[0] if i == 0 else known[-1]]


def build_snapshot(symbol: str, data: dict, now: dt.datetime) -> dict:
    """The compact form the page reads: per expiration, one row per strike of
    [strike, call OI, put OI, call volume, put volume, implied volatility]."""
    try:
        spot = float(data["current_price"])
        quote = dt.datetime.fromisoformat(data["last_trade_time"]).replace(tzinfo=ET)
        chain = defaultdict(lambda: defaultdict(dict))
        for option in data["options"]:
            parsed = parse_contract(option["option"], symbol)
            if parsed:
                expiry, strike, side = parsed
                chain[expiry][strike][side] = option
    except (KeyError, TypeError, ValueError) as exc:
        raise CboeError(f"the delayed-quotes layout changed: {exc!r}") from exc
    if spot <= 0:
        raise CboeError(f"no price for {symbol}")

    def count(sides, side, field):
        return int(sides.get(side, {}).get(field) or 0)

    expiries, near, covered = [], 0, 0
    for expiry in sorted(chain):
        seconds = (dt.datetime.combine(expiry, dt.time(16), ET) - quote).total_seconds()
        if seconds <= 0:
            continue  # expired by the time of these quotes
        ivs = {strike: strike_iv(strike, sides, spot) for strike, sides in chain[expiry].items()}
        at_the_money = [iv for strike, iv in ivs.items() if abs(strike / spot - 1) <= NEAR]
        near += len(at_the_money)
        covered += sum(iv > 0 for iv in at_the_money)
        fill_gaps(ivs)
        expiries.append({
            "date": expiry.isoformat(),
            "t": round(max(seconds, MIN_SECONDS) / YEAR, 6),
            "rows": [[strike,
                      count(sides, "call", "open_interest"), count(sides, "put", "open_interest"),
                      count(sides, "call", "volume"), count(sides, "put", "volume"),
                      round(ivs[strike], 4)]
                     for strike, sides in sorted(chain[expiry].items())],
        })
    if not expiries:
        raise CboeError(f"no unexpired standard {symbol} options in the response")
    if covered < MIN_IV_COVERAGE * near:
        raise NoVolatilityYet(f"only {covered} of {near} near-the-money strikes carry an implied "
                        f"volatility (market closed, or the feed is degraded)")
    return {
        "symbol": symbol,
        "spot": spot,
        "quoteTime": quote.isoformat(timespec="seconds"),
        "pulled": now.isoformat(timespec="seconds"),
        "rate": RATE,
        "expiries": expiries,
    }


# --------------------------------------------------------------------------- #
# Gamma exposure (the page computes the same thing in the browser)
# --------------------------------------------------------------------------- #

def gamma(spot: float, strike: float, t: float, iv: float) -> float:
    """Black-Scholes gamma; the same for a call and a put."""
    if iv <= 0:
        return 0.0
    vol = iv * math.sqrt(t)
    d1 = (math.log(spot / strike) + (RATE + iv * iv / 2) * t) / vol
    return math.exp(-d1 * d1 / 2) / (math.sqrt(2 * math.pi) * spot * vol)


def exposure(snapshot: dict, spot: float | None = None) -> dict:
    """{strike: [call, put]} dollar gamma per 1% move with the stock at `spot`,
    all expirations: gamma x open interest x 100 shares x 1% of spot squared."""
    spot = spot or snapshot["spot"]
    out = defaultdict(lambda: [0.0, 0.0])
    for expiry in snapshot["expiries"]:
        for strike, call_oi, put_oi, _call_vol, _put_vol, iv in expiry["rows"]:
            unit = gamma(spot, strike, expiry["t"], iv) * spot * spot
            out[strike][0] += unit * call_oi
            out[strike][1] += unit * put_oi
    return out


def net_exposure(snapshot: dict, spot: float | None = None) -> float:
    """Calls count as positive, puts as negative (dealers long calls, short puts)."""
    return sum(call - put for call, put in exposure(snapshot, spot).values())


def levels(snapshot: dict) -> dict:
    spot = snapshot["spot"]
    by_strike = exposure(snapshot)
    # Zero gamma: where the net exposure would change sign, nearest to spot.
    flip, previous = None, None
    for i in range(201):
        price = spot * (0.5 + 0.005 * i)
        net = net_exposure(snapshot, price)
        if previous and (previous[1] < 0) != (net < 0):
            cross = previous[0] + (price - previous[0]) * -previous[1] / (net - previous[1])
            if flip is None or abs(cross - spot) < abs(flip - spot):
                flip = cross
        previous = (price, net)
    return {
        "net": sum(call - put for call, put in by_strike.values()),
        "call_wall": max(by_strike, key=lambda k: by_strike[k][0]),
        "put_wall": max(by_strike, key=lambda k: by_strike[k][1]),
        "zero_gamma": flip,
    }


# --------------------------------------------------------------------------- #
# Output
# --------------------------------------------------------------------------- #

def occ_check(symbol: str, snapshot: dict) -> str:
    """How Cboe's open interest compares with the newest saved OCC day."""
    saved = nvda_oi.saved_days(symbol)
    if not saved:
        return "no saved OCC day to compare with"
    day = max(saved)
    occ = {(expiry, float(strike), side): oi
           for expiry, strike, side, oi in nvda_oi.read_csv_rows(saved[day])}
    shared = differ = 0
    for expiry in snapshot["expiries"]:
        for strike, call_oi, put_oi, *_ in expiry["rows"]:
            for side, oi in (("call", call_oi), ("put", put_oi)):
                if (expiry["date"], strike, side) in occ:
                    shared += 1
                    differ += occ[(expiry["date"], strike, side)] != oi
    if not shared:
        return f"no contracts in common with the OCC file for {day:%a %Y-%m-%d}"
    if differ:
        return (f"differs from the OCC file for {day:%a %Y-%m-%d} on {differ:,} of "
                f"{shared:,} contracts (a newer day?)")
    return f"matches the OCC as of {day:%a %Y-%m-%d} on all {shared:,} shared contracts"


def dollars(value: float) -> str:
    sign = "-" if value < 0 else "+"
    size = abs(value)
    return f"{sign}${size / 1e9:.2f}B" if size >= 1e9 else f"{sign}${size / 1e6:.1f}M"


def print_report(symbol: str, snapshot: dict, now: dt.datetime) -> None:
    quote = dt.datetime.fromisoformat(snapshot["quoteTime"])
    rows = [row for expiry in snapshot["expiries"] for row in expiry["rows"]]
    found = levels(snapshot)
    flip = f"{found['zero_gamma']:.1f}" if found["zero_gamma"] else "none within 50% of spot"
    print(f"{symbol} gamma exposure (source: Cboe delayed quotes)")
    print(f"  Quotes as of : {quote:%a %Y-%m-%d %H:%M} ET, {symbol} at {snapshot['spot']:.2f}")
    print(f"  Retrieved    : {now:%a %Y-%m-%d %H:%M} ET")
    print(f"  Chain        : {len(snapshot['expiries'])} expirations, {len(rows):,} strikes, "
          f"{sum(r[1] + r[2] for r in rows):,} contracts of open interest")
    print(f"  Open interest: {occ_check(symbol, snapshot)}")
    print(f"  Per 1% move  : net {dollars(found['net'])}, call wall {found['call_wall']:g}, "
          f"put wall {found['put_wall']:g}, zero gamma {flip} (all expirations)")


def save_snapshot(symbol: str, snapshot: dict):
    """Write the snapshot, and swap it into the page if one has been generated."""
    path = gex_path_for(symbol)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(snapshot, separators=(",", ":")))
    tmp.replace(path)

    def swap(match):
        payload = json.loads(match.group(2))
        payload["gex"] = snapshot
        return match.group(1) + embeddable(payload) + match.group(3)

    page = page_path("dashboard", symbol)
    try:
        text, swapped = PAGE_DATA.subn(swap, page.read_text(encoding="utf-8"), count=1)
    except (FileNotFoundError, ValueError):
        swapped = 0
    if swapped:
        page.write_text(text, encoding="utf-8")
    return path, page if swapped else None


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--symbol", default="NVDA", help="underlying symbol (default: NVDA)")
    p.add_argument("--if-open", action="store_true",
                   help="pull only while the delayed quotes are from today's regular session "
                        "(about 9:45 to 16:25 Eastern on trading days); otherwise exit 75")
    args = p.parse_args(argv)
    symbol = args.symbol.strip().upper()
    now = dt.datetime.now(ET)
    if args.if_open and not quotes_in_session(now):
        print(f"Market closed ({now:%a %Y-%m-%d %H:%M} ET): no new quotes to pull.")
        return EXIT_CLOSED
    try:
        snapshot = build_snapshot(symbol, fetch_chain(symbol), now)
    except NoVolatilityYet as exc:
        if args.if_open and (now - DELAY).time() < OPENING:
            print(f"Too soon after the open: {exc}. Keeping the previous snapshot.")
            return EXIT_CLOSED
        print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except (OCCError, CboeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT_ERROR
    print_report(symbol, snapshot, now)
    path, page = save_snapshot(symbol, snapshot)
    print(f"\n  Snapshot: {rel(path)}")
    if page:
        print(f"  Page:     {rel(page)}")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
