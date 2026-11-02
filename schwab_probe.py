#!/usr/bin/env python3
"""Measure what Schwab's option chains would give the gamma exposure chart.

A one-off test, run by hand in a terminal: it answers whether the chains are
really live, whether SPY's fits in one call, and how long a pull takes. It reads
only market data, makes a handful of calls, and changes nothing in the project.

The first run asks for the app key, the app secret and a browser login (see
schwab_api.py for where they are kept). The report printed at the end holds none of them, so it is safe
to share.

Exit codes: 0 finished, 1 could not sign in or reach Schwab.
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
import time

import nvda_gex
import nvda_oi
from nvda_oi import ET
from schwab_api import (REFRESH_DAYS, SchwabError, access_token, as_cboe, contracts, good_iv,
                        load_store, newest, spot_of, stamp)
import schwab_api


def get_chain(store: dict, symbol: str, **extra) -> dict:
    return schwab_api.get_chain(access_token(store), symbol, **extra)


def whole_chain(store: dict, symbol: str) -> tuple:
    return schwab_api.whole_chain(access_token(store), symbol)


def fingerprint(body: dict) -> dict:
    # Schwab's reference names these bidPrice and askPrice; the live replies vary.
    return {c["symbol"]: (c.get("bid", c.get("bidPrice")), c.get("ask", c.get("askPrice")),
                          c.get("volatility"), c.get("quoteTimeInLong"))
            for c in contracts(body)}


def describe_levels(snapshot: dict) -> str:
    found = nvda_gex.levels(snapshot)
    flip = f"{found['zero_gamma']:.1f}" if found["zero_gamma"] else "none"
    return (f"spot {snapshot['spot']:.2f}, net {nvda_gex.dollars(found['net'])}, call wall "
            f"{found['call_wall']:g}, put wall {found['put_wall']:g}, zero gamma {flip}")


# --------------------------------------------------------------------------- #
# The report
# --------------------------------------------------------------------------- #

def lag(moment: dt.datetime | None, now: dt.datetime) -> str:
    if not moment:
        return "absent"
    return f"{moment:%H:%M:%S} ET ({(now - moment).total_seconds():.0f}s before now)"


def report_calls(calls: list) -> None:
    for i, call in enumerate(calls, 1):
        line = (f"  Call {i}: status {call['status']}, {call['seconds']:.1f}s, "
                f"{call['wire'] / 1e6:.2f} MB on the wire, {call['bytes'] / 1e6:.2f} MB unpacked")
        print(line)
        if call["limits"]:
            print(f"    Rate headers: {call['limits']}")
        if call.get("error"):
            print(f"    Error: {call['error']!r}")


def report_chain(symbol: str, body: dict, now: dt.datetime) -> None:
    rows = list(contracts(body))
    spot = spot_of(body)
    underlying = body.get("underlying") or {}
    near = [c for c in rows if spot and abs(c.get("strikePrice", 0) / spot - 1) <= nvda_gex.NEAR]
    print(f"  Chain status : {body.get('status')}, isDelayed {body.get('isDelayed')}, "
          f"underlying delayed {underlying.get('delayed')}")
    print(f"  Contracts    : {len(rows):,} in {len(body.get('callExpDateMap') or {})} expirations")
    print(f"  Stock        : {spot:.2f}, quote {lag(stamp(underlying.get('quoteTime')), now)}")
    print(f"  Newest option quote: {lag(stamp(newest(body, 'quoteTimeInLong')), now)}")
    print(f"  Newest option trade: {lag(stamp(newest(body, 'tradeTimeInLong')), now)}")
    print(f"  Volatility   : {sum(good_iv(c.get('volatility')) for c in near)} of {len(near)} "
          f"contracts near the money, {sum(good_iv(c.get('volatility')) for c in rows)} of {len(rows):,} overall")
    print(f"  Fields       : gamma on {sum(good_iv(c.get('gamma')) for c in rows):,}, "
          f"open interest on {sum(1 for c in rows if c.get('openInterest')):,}, "
          f"volume on {sum(1 for c in rows if c.get('totalVolume')):,}")
    try:
        ours = nvda_gex.build_snapshot(symbol, as_cboe(body), now)
    except nvda_gex.CboeError as exc:
        print(f"  Chart        : could not build a snapshot: {exc}")
        return
    print(f"  Open interest: {nvda_gex.occ_check(symbol, ours)}")
    print(f"  Chart (Schwab): {describe_levels(ours)}")
    try:
        cboe = nvda_gex.build_snapshot(symbol, nvda_gex.fetch_chain(symbol), now)
        print(f"  Chart (Cboe)  : {describe_levels(cboe)}")
    except (nvda_oi.OCCError, nvda_gex.CboeError) as exc:
        print(f"  Chart (Cboe)  : unavailable: {exc}")


def report_single_day(store: dict, symbol: str, body: dict) -> None:
    """Time one expiration alone, the nearest and the largest: what picking a day would cost."""
    days = {key.split(":")[0]: sum(len(rows) for rows in strikes.values())
            for key, strikes in (body.get("callExpDateMap") or {}).items()}
    if not days:
        return
    today = dt.datetime.now(ET).date().isoformat()
    upcoming = [day for day in sorted(days) if day >= today] or sorted(days)
    for day in dict.fromkeys((upcoming[0], max(upcoming, key=days.get))):
        time.sleep(1)
        call = get_chain(store, symbol, fromDate=day, toDate=day)
        found = sorted({key.split(":")[0] for key in (call["body"] or {}).get("callExpDateMap") or {}})
        print(f"  One day {day}: status {call['status']}, {call['seconds']:.1f}s, "
              f"{call['wire'] / 1e6:.2f} MB on the wire, "
              f"{sum(1 for _ in contracts(call['body'] or {})):,} contracts, expirations returned {found}")
        if call.get("error"):
            print(f"    Error: {call['error']!r}")


def report_changes(store: dict, symbol: str, repeats: int, wait: int) -> None:
    """Pull again a few times: a live feed moves between pulls."""
    previous = None
    for i in range(repeats):
        if i:
            time.sleep(wait)
        call = get_chain(store, symbol)
        now = dt.datetime.now(ET)
        if call["status"] != 200:
            print(f"  Pull {i + 1}: status {call['status']}")
            return
        body = call["body"]
        current = fingerprint(body)
        moved = sum(current[k] != previous[k] for k in current if k in previous) if previous else "-"
        print(f"  Pull {i + 1} at {now:%H:%M:%S}: stock {spot_of(body):.2f}, "
              f"quote {lag(stamp((body.get('underlying') or {}).get('quoteTime')), now)}, "
              f"contracts changed since the last pull: {moved}")
        previous = current


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--repeat", type=int, default=3, help="NVDA pulls to compare (default: 3)")
    p.add_argument("--wait", type=int, default=30, help="seconds between them (default: 30)")
    args = p.parse_args(argv)

    store = load_store()
    try:
        access_token(store)
        now = dt.datetime.now(ET)
        print(f"\nSchwab option chain test, {now:%a %Y-%m-%d %H:%M:%S} ET")
        open_now = nvda_oi.is_trading_day(now.date()) and dt.time(9, 30) <= now.time() <= dt.time(16)
        print("Market " + ("open" if open_now else "CLOSED: the freshness results below prove nothing"))
        if store.get("logged_in_at"):
            age = (time.time() - store["logged_in_at"]) / 86400
            print(f"Login {age:.1f} days old (reported to last {REFRESH_DAYS} days)")
        for symbol in nvda_oi.SYMBOLS:
            print(f"\n{symbol}")
            body, calls = whole_chain(store, symbol)
            report_calls(calls)
            if body:
                report_chain(symbol, body, dt.datetime.now(ET))
                report_single_day(store, symbol, body)
            time.sleep(1)
        call = get_chain(store, nvda_oi.SYMBOLS[0], entitlement="NP")
        if call["status"] == 200:
            print(f"\nWith entitlement=NP (non-professional): isDelayed {call['body'].get('isDelayed')}, "
                  f"underlying delayed {(call['body'].get('underlying') or {}).get('delayed')}")
        else:
            print(f"\nWith entitlement=NP: status {call['status']}")
        print(f"\nFreshness, {nvda_oi.SYMBOLS[0]} pulled {args.repeat} times, {args.wait}s apart")
        report_changes(store, nvda_oi.SYMBOLS[0], args.repeat, args.wait)
    except SchwabError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
