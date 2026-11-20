# Research logs

Notes from intraday option-wall reviews of SPY and NVDA. Not used by the site build.

| Path | Content |
|---|---|
| `oi_levels_log.csv` | Daily top OI strikes near spot, whether price tested them, and the result |
| `user_trades.csv` | Trades the owner actually placed, with the setup behind each |
| `friday_oi_paper_trades.csv` | Mon–Thu paper trades on Friday-expiry spreads chosen from Friday OI; closed out on Friday |
| `weekly/YYYY-MM-DD.md` | Weekly summary (Monday's date): magnet hit rate, wall hold rate, rule changes |
| `snapshots/SYM_EXPIRY_asofDATE.csv` | Strike-level OI for one expiry within ±6% of spot (`date,strike,call_oi,put_oi`) |
| `rules.md` | Rules in force, newest first |
| `scripts/` | `cboe2csv.py`, `summ.py` (OI/max pain/EM/GEX summary, snapshot diff), `intraday.py` (VWAP σ bands) |

Data: Cboe delayed quotes (`cdn.cboe.com/api/global/delayed_quotes/options/SYM.json`), Yahoo chart API.
