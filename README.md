# NVDA open interest, straight from the OCC

Daily report of call/put open interest per strike and expiration, pulled from
the Options Clearing Corporation instead of an aggregator. No API key, no paid
service.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Python 3.9+. The only dependency is matplotlib (for the chart); everything else
is standard library.

## Usage

```bash
.venv/bin/python nvda_oi.py                       # nearest expiration: stats, ranking, chart
.venv/bin/python nvda_oi.py --csv                 # also save data/NVDA_oi_YYYYMMDD.csv
.venv/bin/python nvda_oi.py --expiry 2026-10-16   # a specific expiration
.venv/bin/python nvda_oi.py --expiry all          # rank across the whole chain
.venv/bin/python nvda_oi.py --top 10              # top 10 contracts instead of 20
.venv/bin/python nvda_oi.py --symbol AAPL         # another underlying
```

| Option | Meaning |
|---|---|
| `--symbol` | Underlying (default `NVDA`) |
| `--expiry YYYY-MM-DD` | Expiration to rank. Also accepts `nearest` (default: first expiration not yet past) or `all` |
| `--top N` | Contracts in the "highest open interest" table and chart (default 20) |
| `--csv [PATH]` | Save long-format `expiry,strike,side,oi` for the whole chain. Default path `data/SYMBOL_oi_YYYYMMDD.csv`, dated by the data's as-of day |
| `--if-new` | Do nothing if the latest session's CSV is already saved |
| `--allow-stale` | Report what the OCC has even if the previous trading day is not out yet |
| `--force` | Overwrite a dated CSV whose contents differ |
| `--no-chart` | Skip the chart |
| `--rebuild` | Rewrite the web pages from the newest saved day without contacting the OCC |

Exit codes: `0` ok, `1` error, `75` the OCC has not published yet (try later).

Outputs: the report on stdout (stats, "highest open interest" ranking, and a
per-expiration table), the web pages `dashboard_SYMBOL.html` and
`history_SYMBOL.html`, the same stats and ranking as a chart in
`charts/SYMBOL_oi_YYYYMMDD_expYYYYMMDD.png` (`_all` for the whole chain), the
raw response in `oi_raw_SYMBOL.txt`, and with `--csv` the dated file in `data/`.
The CSV always holds the whole chain, whatever `--expiry` is, so history stays
complete. The OCC serves only the current snapshot, so a day that is not saved
cannot be fetched later.

## The web pages

Every run rewrites two pages, each a single self-contained file with its data
embedded (no server, no network needed to view them). A Latest / History
switch at the top of each goes to the other.

```bash
open dashboard_NVDA.html   # the latest day
open history_NVDA.html     # every saved day
```

`dashboard_NVDA.html` shows the Open Interest Stats table and the Highest Open
Interest Options ranking, with pickers for expiration (or all expirations),
calls/puts, and how many contracts to show, plus a table of every expiration.
It opens on the nearest expiration. Expirations that have passed are left out
of the picker, the tables and the totals, since those options no longer
exist. The page checks this against the date it is viewed, so a page made on
Friday and opened on Monday drops Friday's expiration too. Select an option in
the ranking to open its history.

`history_NVDA.html` tracks the Highest Open Interest Options from day to day.
Pick an expiration, calls/puts, and how many to show: each top option gets one
column per saved day and the change from the day before. Select an option, or
any strike from the Option menu, to chart its open interest day by day, with
the change from the previous day and from the first saved day, and where it
ranked among that expiration's calls (or puts) each day. The address keeps the
selection, so `history_NVDA.html#expiry=2026-10-09&option=240C` opens on the
240 call expiring 10/09/26.

The history comes from every CSV in `data/` plus the latest pull, so it starts
on the first saved day (2026-10-01) and grows by one column each trading day.
An expiration drops off once it has passed; its CSV rows stay in `data/`.

The layouts live in `dashboard_template.html` and `history_template.html`;
edit those, not the generated files. Reload a page after a data pull to see
the new day.

### Gamma exposure

```bash
.venv/bin/python nvda_gex.py                      # pull Cboe, write gex_NVDA.json
.venv/bin/python nvda_gex.py --symbol AAPL        # another underlying
.venv/bin/python nvda_gex.py --if-open            # only while the market's quotes are live
```

`nvda_gex.py` pulls Cboe's delayed option chain, saves `gex_NVDA.json` and
swaps it into `dashboard_NVDA.html` if that page exists. The page's Gamma
Exposure card then shows net exposure per strike, the gamma profile (net
exposure if the stock traded at each price) and four levels: spot, call wall,
put wall and zero gamma. It follows the page's expiration picker and has its
own strike range and open interest / volume pickers.

- Exposure is dollars per 1% move: gamma x contracts x 100 x spot squared x 1%.
  Calls count as positive and puts as negative, the usual assumption that
  dealers are long calls and short puts.
- Gamma is Black-Scholes, computed in the browser from one implied volatility
  per strike (the out-of-the-money side's), a 4% rate and time to the 16:00
  Eastern close on expiration day, floored at 30 minutes.
- Quotes are 15 minutes delayed. Open interest changes once a day, so between
  pulls only the stock price and implied volatility move the chart.
- A pull that fails, or that lacks implied volatility on more than a fifth of
  the strikes within 10% of spot, exits with an error and keeps the previous
  snapshot.
- With `--if-open` nothing is pulled unless the delayed quotes are from today's
  regular session (about 9:45 to 16:25 Eastern on trading days); otherwise it
  exits `75` and the last snapshot, normally the close, stands.

A hosted page fetches `gex_NVDA.json` from beside itself every 5 minutes and
redraws when it is newer; a page opened as a local file keeps the embedded
copy until the next pull.

#### Live price

The card can follow the stock's trades as they happen. On the "Live price:
off" line under the card's heading, "Turn on", paste a Finnhub API key (free
at https://finnhub.io/register) and "Connect". The page then streams NVDA's
trades from Finnhub's websocket and redraws the card at the latest price, at
most once a second: the bars, the tiles, the table and the spot line. Time to
expiry counts down from each trade, so an expiration drops out at its 16:00
close. Open interest, implied volatility and volume stay the latest
snapshot's, so the profile, the walls and zero gamma move little during the
day; what moves is the price against them, and the exposure at it.

- The key is kept in that browser only (localStorage) and sent only to
  Finnhub, never to the repository or the published page. Each browser (the
  phone, the Mac) needs it once; "Turn off" forgets it.
- The price is the median of the last five trades, so one stray print doesn't
  move it; a trade stamped more than 10 seconds before the newest is skipped.
- The strike window stays centred on the snapshot's price until the live price
  moves more than half the strike range away, so the bars don't slide with
  every trade. A readout opened by touch or the keyboard holds the redraw
  until it closes; a mouse readout carries over to the redrawn chart.
- The connection closes while the page is hidden and reopens when it is
  shown. After three failed tries in a row it stops, says why and goes back
  to the snapshot's price; "Try again" restarts it.

### On a phone, or anywhere

https://kshu231.github.io/nvda-open-interest/ is the same dashboard, hosted
by GitHub Pages, and
https://kshu231.github.io/nvda-open-interest/history_NVDA.html the history.
Two GitHub Actions workflows keep them current on GitHub's servers; neither
depends on this Mac.

`.github/workflows/daily.yml` ("Daily open interest") pulls the OCC. A run
that finds a new day commits its CSV to `data/` and republishes both pages; a
timer-started run that finds nothing new changes nothing. It is started
through the evening, when the day's numbers usually appear (they were out by
21:06 Eastern on 2026-10-08), so the page can show a day's close the same
evening, and again before the next open in case the evening missed it.

`.github/workflows/gex.yml` ("Gamma exposure refresh") refreshes the gamma
exposure chart every 30 minutes through the market day. It pulls Cboe,
rebuilds both pages from the saved open interest (`nvda_oi.py --rebuild`, so
the OCC is not asked again) and republishes. Outside market hours it does
nothing, and the daily workflow carries the closing snapshot over when it
republishes.

To run either on demand: the repository's Actions tab, the workflow's name,
"Run workflow".

#### What starts them

GitHub's own schedule trigger started runs hours late or not at all, so two
Google Cloud Scheduler jobs (project `nvda-open-interest`, `us-central1`)
start the workflows through GitHub's API instead. Both use the time zone
`America/New_York`, an HTTP `POST` target, and the headers
`Authorization: Bearer TOKEN`, `Accept: application/vnd.github+json` and
`Content-Type: application/json`.

| Job | Frequency | URL ends in | Body |
|---|---|---|---|
| `nvda-open-interest` | `15,45 9-16 * * 1-5` | `gex.yml/dispatches` | `{"ref":"main"}` |
| `nvda-oi-daily` | `15,45 8,20-23 * * 1-5` | `daily.yml/dispatches` | `{"ref":"main","inputs":{"timer":"true"}}` |

The URLs begin
`https://api.github.com/repos/kshu231/nvda-open-interest/actions/workflows/`.
`TOKEN` is a fine-grained GitHub personal access token limited to this
repository with Actions set to read and write. It lives only in the scheduler
jobs. GitHub answers `204` when a run has been started.

- The `timer` input tells the daily workflow not to republish when the OCC
  has nothing new, which is most firings.
- Each run that saves a new day adds a row to `data/occ_arrivals.csv`: the
  day, when it was saved (Eastern), what started the run and its run number.
  `scheduler` is the job above, `dispatch` is "Run workflow" or any other
  caller of GitHub's API, `push` is a change to the code. With the job firing
  every half hour, the row shows which firing first found the day's numbers,
  and the firing before it is the last one that did not. Use it to narrow the
  job's hours.
- The first job's 9:15 and 16:45 firings fall outside market hours and end
  without publishing.
- `gcloud scheduler jobs update http --update-headers` replaces every header,
  so restate all three when changing one.

## Where the numbers come from

- `https://marketdata.theocc.com/series-search?symbolType=U&symbol=NVDA` is a
  tab-delimited file with one row per expiration and strike: product symbol,
  year, month, day, strike integer, strike decimals (thousandths), a `C P` flag
  for which sides are listed, call OI, put OI, position limit. It has no date.
- `https://marketdata.theocc.com/mdapi/open-interest?report_date=MM/DD/YYYY`
  is the JSON behind the OCC's Open Interest page. Its
  `lastBusDateOI.activityDate` is the latest trading day the OCC has published
  open interest for. The script prints that as the "as of" date.
- Only standard listed options (product symbol equal to the underlying) are
  counted. FLEX series (`1NVDA`, `2NVDA`) are excluded and their size is shown
  on the "Excluded" line.
- `https://cdn.cboe.com/api/global/delayed_quotes/options/NVDA.json` is the
  feed behind Cboe's own delayed quotes page: the stock price and, for every
  listed contract, bid/ask, implied volatility, greeks, volume and open
  interest. It is undocumented and could change without notice. Its open
  interest is the OCC's; each `nvda_gex.py` run reports whether it still
  matches the newest saved OCC day.

Because series-search carries no date, the script cross-checks it against saved
history: if the OCC's date has moved on but the series data is identical to the
previous saved day, it treats the data as not refreshed yet.
The reverse happens in the evening: the OCC posts the day's numbers to
series-search hours before its date moves on (that catches up by morning). So
after the close a run compares series-search with the file saved for the date
the OCC reports. Unchanged means the day's numbers aren't out yet (exit `75`);
changed means they are, and they are saved under that day's date, with "posted
after the close" on the page. If the reported day was never saved, a run saves
it only while that day's own expiring options are still listed, which only its
data does; otherwise it saves nothing and exits `75`. The next morning's run
checks the day again against the OCC's dated data and replaces it if the OCC
revised it.

## Schedule (macOS)

A LaunchAgent (`~/Library/LaunchAgents/com.nvda-oi.daily.plist`) runs
`run_daily.sh` Monday to Friday at 08:30 and 09:20, before the 09:30 open.
Each run pulls fresh data, writes the report to `logs/nvda_oi.log`, the chart
to `charts/` and the dated CSV to `data/`. The 09:20 run is both a retry (if
the OCC had not published by 08:30) and a refresh: if the OCC changed the data
in between, the CSV is replaced and the log carries a WARNING.

```bash
./schedule_macos.sh status
./schedule_macos.sh install 08:30 09:20   # change the times
./schedule_macos.sh uninstall
```

Times are the Mac's local time, which is Eastern while the system time zone is
`America/New_York`. If the Mac is asleep at a scheduled time the run happens
on wake. Monday's run picks up Friday's close. On a market holiday the run
re-reports the last session and saves nothing new.

## Measuring when the OCC publishes

```bash
.venv/bin/python probe_availability.py --interval 5 --max-hours 20
```

Optional. Polls both endpoints and appends to `data/probe_log.csv`; the first
row where `asof` and `series_sha` change is the publication time. The time each
new day was first saved is also recorded in `data/availability_log.csv`, which
shows whether the 08:30 run is early enough.
