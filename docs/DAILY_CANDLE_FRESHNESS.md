# Daily Candle Freshness Operations

Daily freshness extends the live canonical PostgreSQL view with real, checksum-pinned one-minute
candles. It never changes the source dump or an existing Parquet snapshot. The scheduler-facing
command is `msl-sync-candles`; the registered Windows task invokes the reviewed runner only after
the durable command path passed against the live database.

## Frozen inputs and storage

The currently reviewed compatibility artifact has SHA-256
`482f3a09a4e24a62b0ad92f5bb90028eead67fe961eb1d3320dd38d13fd105d2`. Keep its checksum-bearing
JSON outside Git under `data/exports/manifests/` and pass both its path and hash on every run. The
source dump must remain `data/dumps/callscore.dump` with SHA-256
`1B6BCB39AF41048B53729E9B094F0229163EB6FF6AF9563ADB666C96F5FD4DA4`.

The default operational paths are:

- `data/cache/binance/`: resumable source archives and REST payloads;
- `data/exports/freshness/`: immutable plans/reports plus the atomic `latest.json` pointer;
- `data/exports/canonical/dataset_version=<version>/`: deliberately frozen Parquet snapshots.

All three paths are ignored by Git. Database credentials come from the existing `POSTGRES_*`
settings and are never printed by the CLI.

## Daily plan and sync

PowerShell example (replace the compatibility path with the retained reviewed artifact):

```powershell
$compatibility = "data/exports/manifests/recovery-callscore-20260714-validated.json"
$compatibilitySha = "482f3a09a4e24a62b0ad92f5bb90028eead67fe961eb1d3320dd38d13fd105d2"

uv run msl-sync-candles bootstrap `
  --compatibility $compatibility `
  --compatibility-sha256 $compatibilitySha `
  --dump-path data/dumps/callscore.dump

uv run msl-sync-candles plan `
  --compatibility $compatibility `
  --compatibility-sha256 $compatibilitySha `
  --output-dir data/exports/freshness
```

`bootstrap` is the explicit first-use step after a reviewed crypto-only restore. It verifies the
dump, compatibility artifact, restored row count, and mapping before idempotently applying the
existing append-only recovery migration. It does not fetch candles, rerun the restore, or mutate
restored rows. Repeating it is safe. Daily runs start at `plan`; they do not bootstrap storage
implicitly.

The plan freezes the last fully closed UTC minute when `--as-of` is omitted. Record the emitted
`manifest` path, inspect its totals, and verify the exact write set without network or database
writes:

```powershell
uv run msl-sync-candles run `
  --manifest <emitted-plan-path> `
  --compatibility $compatibility `
  --compatibility-sha256 $compatibilitySha
```

Apply that same frozen plan only after retaining the dry-run evidence:

```powershell
uv run msl-sync-candles run `
  --manifest <emitted-plan-path> `
  --compatibility $compatibility `
  --compatibility-sha256 $compatibilitySha `
  --dump-path data/dumps/callscore.dump `
  --cache-dir data/cache/binance `
  --output-dir data/exports/freshness `
  --apply

uv run msl-sync-candles health --output-dir data/exports/freshness
```

Exit `0` means every symbol is `up_to_date` or `recovered`. Exit `2` is a trustworthy unhealthy
result: at least one symbol is stale, unresolved, provider-absent, failed, or provenance-blocked.
Exit `1` means an input, checksum, database, lock, network, or source-integrity failure prevented a
valid terminal report. The JSON report gives every symbol one terminal status and conserves planned,
recovered, and remaining minutes.

Current reviewed evidence has nine `source_conflict` symbols (AR, AVAX, BTC, ETH, FET, PENDLE,
RENDER, SOL, and SUI). Therefore the full 25-symbol universe is unhealthy by design, and a daily job
must alert rather than report success. The updater fetches no candle for those symbols until a new
independently reviewed compatibility artifact resolves their provenance.

## Resume and failure handling

- Re-run `run --apply` with the identical plan after an interruption. Completed batches are durable,
  source downloads resume, and append-only writes are idempotent.
- Do not create a new cutoff to hide a failed older run. Retain its report/error, finish or explicitly
  classify it, then plan the next cutoff.
- A lock error means another recovery or snapshot publication is active. Let it finish, then re-run
  the same manifest.
- A checksum or compatibility mismatch is not retryable by override. Restore the reviewed artifact
  or perform a new compatibility review.
- Never delete `data/postgres`, use `docker compose down -v`, alter supplements, or reinitialize the
  database as recovery handling.

## Unattended Windows runner

`scripts/run_daily_candle_refresh.ps1` executes the scheduler contract without installing packages,
bootstrapping storage, or publishing Parquet:

```powershell
pwsh.exe -NoLogo -NoProfile -NonInteractive `
  -File scripts/run_daily_candle_refresh.ps1 `
  -ProjectRoot D:\market-structure-lab
```

The runner requires the installed `.venv\Scripts\msl-sync-candles.exe`, the checksum-pinned
compatibility artifact, and an existing `data/postgres/PG_VERSION`. It starts the existing Compose
PostgreSQL service only when needed:

- an existing stopped container uses `docker compose start postgres`;
- a missing container may use `docker compose up -d --no-deps postgres` only when the durable
  PostgreSQL version marker already exists;
- an empty or uninitialised `data/postgres` fails closed.

It never invokes `bootstrap`, `snapshot`, `docker compose down`, volume deletion, or database
reinitialisation. Docker Desktop itself must already be running.

The runner holds a host file lock for the complete workflow; the recovery engine additionally holds
its PostgreSQL advisory lock while apply publishes supplements. After planning, the runner writes
`data/exports/freshness/automation/pending.json` atomically, then uses that exact manifest for dry-run
and apply. An operational failure preserves the pending file so the next invocation resumes the
older cutoff instead of hiding it behind a newer plan. A terminal report removes the pending file
and atomically writes `automation/latest.json`.

Runner exits are narrower than the underlying health command:

- exit `0`: all reviewed symbols are current;
- exit `2`: every non-current symbol has terminal evidence: either a `source_conflict` derived from
  the pinned compatibility artifact, or a compatible-symbol `provider_absent`, `non_trading`, or
  `partially_recovered` result with explicit remaining ranges;
- exit `1`: Docker, database, lock, checksum, network, coverage, report-identity, or unexpected
  symbol-status failure.

Thus the current nine reviewed conflicts and any explicitly terminal compatible-symbol coverage
limits remain alerts. The runner archives their terminal report and removes the pending cutoff so
the next daily invocation can plan a newer tail. `fetch_failed`, `unresolved`,
`source_unavailable`, a missing expected conflict, or an internally inconsistent status is an
operational failure that preserves the pending manifest for retry. The runner records no
credentials or connection URLs.

`scripts/register_daily_candle_refresh.ps1` defines the Windows Task Scheduler handoff. The reviewed
default is daily at 07:15 local time, running PowerShell 7 with `-NoProfile -NonInteractive`, starting
after a missed trigger, and refusing overlapping task instances:

```powershell
pwsh.exe -NoLogo -NoProfile -File scripts/register_daily_candle_refresh.ps1 -WhatIf
```

Remove `-WhatIf` only after one durable live refresh has completed and its terminal evidence has
been reviewed. The task uses the current interactive Windows account so it can reach Docker
Desktop. Scheduler-level retries are intentionally omitted; the runner's pending-manifest state is
the resumability mechanism.

The task is now registered as `Market Structure Lab - Daily Candle Refresh`. It is ready, runs at
07:15 local time, starts after a missed trigger, ignores overlapping instances, and next invokes:

```text
C:\Program Files\PowerShell\7\pwsh.exe -NoLogo -NoProfile -NonInteractive
  -File D:\market-structure-lab\scripts\run_daily_candle_refresh.ps1
  -ProjectRoot D:\market-structure-lab
```

## First durable live refresh evidence

- cutoff: `2026-07-16T11:23:00Z`;
- plan SHA-256: `754d49560efccbe6ac40d23b5b190fa6c791c352951da35ac1c6aff6e40981d2`;
- recovery run: `5ba02c00-c622-41fb-b6c5-1ef960825f88`;
- inserted rows: `8,567,720` in `9,750` completed batches, zero failed batches;
- terminal resolutions: 13 recovered ranges, 3 partially recovered ranges, and 154
  provider-absent ranges;
- logical supplement hash:
  `cfaba512a869075336f27136a6792cb3d07246c8b4e6656a7c08e52dda59189d`;
- report SHA-256:
  `d518e9ca850a2c06338da84f2ef921c8efe2ed3f50c52f5a37375ff54678d210`;
- report result: coverage conserved, `8,567,720` recovered minutes, `7,731,254` remaining minutes,
  and health exit `2`;
- identical recovery replay: zero inserted rows, unchanged `25,048,401` validated supplements,
  identical run ID and logical hash.

The terminal alert contains nine reviewed `source_conflict` symbols, three compatible partial
symbols, and nine compatible provider-absent symbols. Plans are immutable, checksum-verified
evidence. Do not manually edit them, including newline normalization on Windows.

## Deliberate immutable snapshots

Daily sync updates `market_data.candles_canonical`, the dump-preferred live canonical view. It does
not duplicate the complete dataset into Parquet every day. Freeze a new immutable snapshot only for
a declared research dataset version:

```powershell
uv run msl-sync-candles snapshot `
  --report <checksum-verified-report-path> `
  --compatibility $compatibility `
  --compatibility-sha256 $compatibilitySha `
  --dump-path data/dumps/callscore.dump `
  --output-root data/exports/canonical `
  --dataset-version canonical-20260716-v1 `
  --config-version freshness-snapshot-v1 `
  --code-commit <full-git-commit>
```

The default policy requires a healthy report. `--allow-provenance-blocked` is an explicit exception
for research that deliberately accepts uneven symbol coverage: it admits only `provenance_pending`,
`source_conflict`, or `source_unavailable` states, never partial recovery, provider absence, fetch
failure, or unresolved ranges. The snapshot identity pins the dump, complete supplement-state hash,
freshness plan/report, compatibility artifact, cutoff, policy, mapping, configuration, and code
commit. Publication streams bounded batches from the canonical view under the recovery advisory
lock and delegates atomic UTC-date Parquet publication to the existing exporter. Reusing a dataset
version with different evidence fails closed; existing snapshots are never rewritten.

The durable live sync and Windows task registration above are complete. Disposable integration
profiles remain available in `tests/integration/test_freshness_postgres.py` and
`tests/integration/test_freshness_planner_postgres.py`; they require an explicit database whose name
contains `test` via `MSL_TEST_POSTGRES_URL`.
