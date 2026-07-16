# Daily Candle Freshness Operations

Daily freshness extends the live canonical PostgreSQL view with real, checksum-pinned one-minute
candles. It never changes the source dump or an existing Parquet snapshot. The scheduler-facing
command is `msl-sync-candles`; automation is configured separately only after these commands pass
against durable storage.

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
$compatibility = "data/exports/manifests/binance-spot-compatibility.json"
$compatibilitySha = "482f3a09a4e24a62b0ad92f5bb90028eead67fe961eb1d3320dd38d13fd105d2"

uv run msl-sync-candles plan `
  --compatibility $compatibility `
  --compatibility-sha256 $compatibilitySha `
  --output-dir data/exports/freshness
```

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

No production network sync or daily automation was executed as part of this implementation. The
verified integration profile is `tests/integration/test_freshness_postgres.py`; it requires an
explicit disposable database whose name contains `test` via `MSL_TEST_POSTGRES_URL`.
