# Pillar One: XAU fundamental data collection

Updated 2026-09-09. The data collection layer is implemented; a gold fundamental
trading model is not yet validated. Collection requires no LLM, GPU, or API key.

## Research scope

The initial focus is Ourbit Gold (XAU)/USDT. Its official listing announced an
August 24, 2026 launch at 04:20 UTC. `XAU_USDT` is an internal research identifier;
the exact API symbol, contract multiplier, financing, account fees, and executable
quotes remain unverified. XAUT is a different instrument and is not substituted.
[Ourbit listing](https://www.ourbit.com/support/articles/17827791513443)

Gold needs its own technical benchmark and fundamental labels. Previous BTC
results do not establish gold performance. Pillar One should eventually provide
fundamental agreement, warnings, vetoes, or abstention alongside Pillar Two's
timing. The combined gate must prove an improvement after costs on later data.

## What is collected

| Driver | Current source/data | Practical boundary |
|---|---|---|
| Monetary policy and financial stability | Fed releases, speeches/testimony, historical press index | Archive entries are headlines/links; most are broader policy context rather than gold-specific events. |
| Growth and inflation | BEA release summaries; configured BLS CPI feed | Actual/prior/consensus extraction is not implemented. BLS currently returns HTTP 403. |
| Employment | Configured BLS employment feed | Current collection is blocked by HTTP 403. |
| Scheduled release risk | BLS iCalendar parser and configured feed | UTC/DST handling is tested, but no calendar events were captured because of HTTP 403. |
| Opportunity cost | Nominal and real Treasury daily yield curves from 2003 | Daily context, not a tradable quote or an original historical vintage. |
| Dollar, positioning, physical demand, geopolitical shocks | Still to acquire | Requires additional verified, appropriately licensed sources. |

The sources and their verification limits are listed in
[external references](references.md#gold-and-fundamental-capture-sources).
The scope follows economic drivers; it does not assume that an inflation headline
or falling yield always implies the same profitable gold direction.

## Captured evidence

The bootstrap audit verified **16,557 records** from 55 raw responses:

- 4,626 Fed historical headline entries, including 1,071 monetary-policy entries.
- 82 publisher feed summaries.
- 11,849 daily yield-curve records, spanning 2003-01-02 through 2026-09-04.
  These contain 101,085 non-missing tenor observations.

A September 9 refresh recovered the Fed monetary feed and brought the corpus to
**16,574 records**: 4,626 archive entries, 97 feed summaries, and 11,851 daily curves.
These units must not be added together and described as independent news events
or training examples. The latest reports and exports record exact counts.

Original captures remain under `data/research/pillar_one_xau/`. The corrected
corpus and ongoing checkpoints are under `data/research/pillar_one_xau_v1/`.
The rebuild reused original response bytes and receipt timestamps. It corrected
date-only archive parsing and separated recurring monthly releases. It did not
download history again or assign artificial earlier availability.

## Time, revisions, and deduplication

Each response has request/receipt timestamps, status, a SHA256 hash, and parse
diagnostics. Raw bytes are content-addressed and checked before reuse. SQLite
retains immutable record versions and their sightings in later fetches. HTTP
validators are saved only after an accepted parse; failures receive a persistent
backoff of at least an hour. Requests are bounded and concurrency defaults to 3.

`available_at` equals the first receipt of a normalized version. It is separate
from the publisher's timestamp, observation date, and scheduled release time.
Historical Treasury data downloaded today cannot be used as known information
in a backtest from 2003. Original vintage/revision histories remain missing.

Archive dates without explicit zones remain date/local-time metadata. They never
become fresh news or synthesized UTC release times. Actuals, consensus, prior
values, and surprises remain null. Missing Treasury values remain null; negative
real yields are retained. Ambiguous/nonexistent calendar local times, recurrence
rules needing expansion, malformed items, and future news are quarantined.

Exact feed content supports conservative syndication grouping on nearby publication
dates. Monthly announcements reusing the same URL remain distinct. Archive
headlines alone cannot merge unrelated policy decisions. This is not a complete
semantic duplicate detector, and event grouping requires review before training.

`FundamentalStore.context(at)` filters by recorded availability and reports fresh
news, upcoming events, and dated yield curves with their ages. It currently always
abstains. The existing `run_news_service` and training builder are not connected
to these exports; archive headlines and curves are not drop-in labeled examples.

## Run and resume

From the repository root, with project dependencies installed:

```powershell
# Fetch sources that are due, then exit.
.venv\Scripts\python.exe -m apps.run_fundamental_collector

# Resume missing historical Treasury years without redownloading completed years.
.venv\Scripts\python.exe -m apps.run_fundamental_collector --backfill

# Collect for 24 hours in this terminal; Ctrl+C stops it.
.venv\Scripts\python.exe -m apps.run_fundamental_collector --duration-seconds 86400
```

The configuration is `configs/pillar_one_xau_capture.yaml`. It permits reviewed
public HTTPS hosts and contains no credentials. RSS polling is every 5-10 minutes;
the archive index is daily and curves/calendar are every 6 hours. The 24-hour
duration is a run limit, not evidence that a 24-hour stability test has passed.
Keep the machine awake; shutdown/sleep interrupts collection. Restart resumes
checkpoints, but this is not an installed operating-system service.

Use one collector per directory. `status.json` is an atomic heartbeat/report
updated about every 30 seconds during a sustained run. If launched in the
background, its PID, start time, and log paths are recorded separately in
`collector_process.json`. Check the actual process and heartbeat before assuming
that collection is running. To stop that specific recorded process:

```powershell
$captureInfo = Get-Content data/research/pillar_one_xau_v1/collector_process.json | ConvertFrom-Json
$captureProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $($captureInfo.pid)"
if ($captureProcess -and
    $captureProcess.CommandLine -like '*apps.run_fundamental_collector*' -and
    $captureProcess.CreationDate.ToUniversalTime().ToString('o') -eq $captureInfo.creation_time_utc) {
    Stop-Process -Id $captureInfo.pid
}
```

The PID/start-time check avoids stopping an unrelated process after PID reuse.
For a clean foreground stop use Ctrl+C. SQLite transactions and retained raw
responses support recovery after interruption; forced process termination may
leave an unindexed raw response or temporary status file.

## Audit, export, and parser changes

```powershell
.venv\Scripts\python.exe -m research.fundamental_capture_audit `
  --report artifacts/xau_capture_audit_new.json `
  --export data/research/pillar_one_xau_v1/corpus_new.jsonl

# Rebuild into a NEW directory when normalization rules change.
.venv\Scripts\python.exe -m research.fundamental_capture_audit `
  --rebuild-to data/research/pillar_one_xau_rebuilt `
  --report artifacts/xau_rebuild_new.json
```

Reports, exports, and rebuild destinations must be new paths. Rebuild audits the
original responses, reparses accepted bodies, and preserves receipt times. Review
the new corpus before changing `storage_dir` to it. Keep the database and its WAL
consistent when backing up a running collector; an ordinary file copy of only
`capture.sqlite3` during writes is insufficient. An export is an unlabeled snapshot,
not a replacement for raw responses and fetch metadata.

Recorded milestone artifacts:

- `artifacts/xau_pillar_one_2026_09_09/rebuild_audit.json`
- `artifacts/xau_pillar_one_2026_09_09/refresh_report.json`
- `data/research/pillar_one_xau_v1/bootstrap_corpus.jsonl`
- `data/research/pillar_one_xau_v1/corpus_2026_09_09.jsonl`

All 343 tests pass, including 28 capture-specific tests; Ruff and strict mypy pass.

## Next validation gates

1. Resolve the verified Ourbit XAU contract and capture synchronized trades/BBO/L2,
   mark/funding/status, a gold reference, and exchange/receive timestamps.
2. Obtain full relevant release text and point-in-time forecast consensus; extract
   actual/prior/revised values with units and release IDs. Add broader geopolitical,
   dollar, positioning, and demand coverage. Do not invent expectations with an LLM.
3. Match news availability to executable XAU quotes, enforce maximum quote distance,
   handle duplicates/revisions, and purge overlapping labels across splits.
4. Test a simple fundamental baseline, a gold-specific technical baseline, and their
   combined filter on identical untouched dates with account-verified stressed costs.
   Report net expectancy, coverage, calibration, and avoided losses as well as win rate.
5. Consider fine-tuning only after the local dataset and model gates pass. The rented
   GPU milestone has not been reached.
