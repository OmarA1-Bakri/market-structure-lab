# Market Structure Lab

A lightweight quantitative research lab for discovering statistically significant cryptocurrency
market-structure behavior from OHLCV data.

## Project goal

The long-term objective is to build a deterministic understanding of the auction process before
introducing trading logic. The intended sequence is:

Raw OHLCV -> canonical datasets and quality checks -> deterministic auction engine -> versioned
features and events -> outcome-blind behaviour discovery -> frozen AI interpretation -> untouched
validation -> edge catalogue -> cost-aware strategies and portfolios.

## Repository layout

- data/ contains database dumps, PostgreSQL data, and export artifacts
- docker/ contains database and Python container setup
- docs/ contains architecture notes, roadmap, and research questions
- scripts/ contains operational utilities such as database inspection
- src/ contains deterministic research code organised by pipeline stage
- tests/ contains regression tests for the repository scaffold

## Environment

This repository uses Python 3.13 and uv. It does not depend on Conda.
`pyproject.toml` is the single dependency definition.

### Install dependencies

```bash
uv venv
uv sync --locked
```

## Docker

Start PostgreSQL and pgAdmin:

```bash
docker compose up -d
```

The database is configured to restore reviewed cryptocurrency market data into a dedicated research
database named `research`.

## Restoring the database

The dump file is expected at:

```text
data/dumps/callscore.dump
```

The source archive is immutable and contains unrelated Callscore application data. During fresh
database initialization, the restore process verifies the archive size and SHA-256, then restores
only the raw `candles` and `ticks` objects listed in
`docker/postgres/init/crypto_only_restore.list`. It excludes creator, calls, videos, agents,
workflows, signals, positions, strategies, quarantine tables, and reproducible materialized views.

The approved objects are moved into the `market_data` schema. Callscore-derived candle columns
(`regime`, `confidence`, `returns`, `volatility`, and `volume_ratio`) are removed from the restored
copy so downstream research starts from source market fields. The original dump is never modified.

PostgreSQL initialization scripts run only when `data/postgres` is empty. This project never deletes
or reinitializes an existing database directory automatically; use a separately approved fresh
restore procedure when replacing an existing local database.

See [`docs/DATA_VIABILITY.md`](docs/DATA_VIABILITY.md) for the full row-quality and symbol-coverage
assessment and [`docs/IMPLEMENTATION_STATUS.md`](docs/IMPLEMENTATION_STATUS.md) for the exact Phase
0 through Phase 4 verification evidence.

## Daily candle freshness

`msl-sync-candles` freezes a minute-aligned UTC plan, admits only independently compatible real
source observations through the append-only supplement ledger, and reports every symbol with an
explicit terminal or retryable status. Terminal states include `up_to_date`, `recovered`,
`partially_recovered`, `provider_absent`, `non_trading`, and `source_conflict`. Daily sync updates
the dump-preferred PostgreSQL canonical view; it does not rewrite the dump or duplicate the full
dataset into Parquet every day.

```bash
uv run msl-sync-candles bootstrap --help
uv run msl-sync-candles plan --help
uv run msl-sync-candles run --help
uv run msl-sync-candles health --help
uv run msl-sync-candles snapshot --help
```

`bootstrap` is an explicit, idempotent first-use step for a deliberately restored crypto-only
database. Daily jobs begin with `plan`; they never initialize storage implicitly.

The unattended Windows runner preserves a pending frozen cutoff across retryable failures and
returns exit `2` for trustworthy terminal coverage limits. The registered task runs daily at 07:15
local time:

```powershell
pwsh.exe -NoLogo -NoProfile -NonInteractive `
  -File scripts/run_daily_candle_refresh.ps1 `
  -ProjectRoot D:\market-structure-lab
```

Immutable Parquet snapshots are created deliberately for frozen research runs. Snapshot publication
requires a checksum-verified freshness report by default; the explicit provenance-blocked policy
records uneven coverage in the snapshot identity. See
[`docs/DAILY_CANDLE_FRESHNESS.md`](docs/DAILY_CANDLE_FRESHNESS.md) for commands, exit statuses,
resume handling, pinned inputs, and storage paths. Nine source conflicts plus compatible-symbol
provider/partial gaps keep full-universe daily health red until new evidence resolves them.

## Running scripts

Inspect the database:

```bash
uv run msl-db-inspect --quick

# Exact row-quality and gap inspection (slower)
uv run msl-db-inspect --full --json data/exports/manifests/database-inspection-full.json
```

Load candles in research code:

```python
from market_structure_lab.datasets import load_dataset, load_symbol

btc = load_symbol("BTCUSDT")
eth = load_dataset(
    symbol="ETHUSDT",
    timeframe="1m",
    start="2025-01-01",
    end="2025-06-01",
)
```

Save reproducible experiment output:

```python
from market_structure_lab.experiments import ExperimentConfig, save_experiment_result

result = save_experiment_result(
    config=ExperimentConfig(
        run_id="value-migration-001",
        name="Value migration baseline",
        question="Does value migrate after imbalance?",
        hypothesis="Accepted upside imbalance shifts later value higher.",
    ),
    metrics={"observations": 1250},
    summary="Initial deterministic baseline.",
)
```

## Research principles

- prefer small, typed, readable functions
- keep algorithms inside `src/market_structure_lab/`
- keep notebooks exploratory only
- favour reproducibility over cleverness
- keep source-specific SQL inside data modules, not experiments

## Missing-candle recovery

Recovery is an explicit, auditable stage. It plans exact internal gaps, validates Binance Spot
compatibility independently for each symbol, downloads checksum-pinned source observations, and
writes validated rows to append-only supplemental storage. It never mutates the restored dump rows,
and incompatible symbols remain quarantined with a terminal reason.

```bash
uv run msl-backfill-candles plan --help
uv run msl-backfill-candles validate --help
uv run msl-backfill-candles fetch --help
uv run msl-backfill-candles report --help
```

The frozen recovery manifest, immutable dump identity, source checksums, completed bounded-batch
checkpoints, per-gap classifications, and logical supplement hash make interrupted runs resumable
and repeated runs idempotent. `market_structure_lab.data.load_canonical_gap_boundaries` derives the
exact remaining hard boundaries from the post-recovery canonical series; downstream sequence work
must assign segment IDs from those boundaries rather than reuse broader pre-recovery gaps.

## Deterministic auction reconstruction

Phase 2 reconstructs bounded, replayable auction state from canonical candles. Price-volume maps
use integer bin indices internally; prices are exposed only at public boundaries. Every engine must
receive an explicit window policy, allocation-model version, bin definition, dataset version, and
configuration version. There is no implicit unbounded cumulative profile.

```python
from datetime import UTC, datetime

from market_structure_lab.auction import AuctionCandle, AuctionEngine, RollingBars
from market_structure_lab.profiles import FixedStepBins, UniformAllocation

engine = AuctionEngine(
    binning=FixedStepBins(step=0.50),
    allocation=UniformAllocation(),
    window_policy=RollingBars(max_bars=1_440),
    dataset_version="canonical-v1",
    config_version="auction-v1",
)
snapshot = engine.update(
    AuctionCandle(
        timestamp=datetime(2025, 1, 1, tzinfo=UTC),
        symbol="BTCUSDT",
        timeframe="1m",
        open=93_500.0,
        high=93_501.0,
        low=93_499.5,
        close=93_500.5,
        volume=12.0,
        segment_id=0,
    )
)
```

Available bounded policies include rolling bar count, rolling duration, a fixed half-open UTC
range, and UTC day/week/month sessions. A material gap is rejected by default or may be configured
to reset the stream; a changed canonical segment always resets it. Snapshots are frozen and include
profile/version identity, POC, value area, VWAP, location, node zones and persistence, migration,
and stable structural event IDs. Canonical JSON serialization and stream hashes provide replay
evidence. See [`docs/market_structure.md`](docs/market_structure.md) for the exact OHLCV
approximations and limitations.

## Feature and event datasets

Phase 3 turns immutable auction snapshots into outcome-blind discovery evidence. The audited
`FS-000001` registry contains 26 causal features: 16 derived from the Phase 2 auction representation
and 10 minimally assumptive candle-sequence features. Feature rows use the exclusive candle close
as their information cutoff, preserve every upstream identity, reset at hard boundaries, and never
interpolate or fill missing observations.

Robust normalisation is fitted only on an explicit training partition with exact bounded-memory
median/IQR runs. Discovery events use half-open intervals, registered feature vectors observable at
their cutoff, causal prior-history baselines, stable provenance-rich IDs, and streaming overlap
evidence. Feature and event publications are atomic, idempotent, checksum-manifested, and written in
bounded deterministic Parquet parts.

```python
from market_structure_lab.data.derived import publish_feature_rows
from market_structure_lab.events import make_event, segment_fixed_windows
from market_structure_lab.features import FeatureBuilder, builtin_feature_registry
```

See [`docs/FEATURE_EVENT_DATASETS.md`](docs/FEATURE_EVENT_DATASETS.md) for the registered formulas,
missing-value rules, normalisation contract, event semantics, and publication invariants. Phase 3
does not attach outcomes, discover behaviours, validate edges, or construct strategies.

## Outcome-blind discovery MVP

Phase 4 freezes chronological discovery, development, and final-holdout metadata before fitting.
Registered numeric features enter a bounded matrix, inspectable PCA, deterministic seeded K-means,
multi-axis stability checks, bounded motifs, and boundary-aware transition summaries. Unstable
cluster definitions are retained as rejected runs and cannot enter the behaviour catalogue.

Discovery runs publish atomic checksum-pinned `DR-*` bundles. AI may interpret only frozen evidence
packs and must record complete provenance; it cannot alter detector fields, validate an edge, or
claim profitability. The final holdout and all future outcomes remain inaccessible until Phase 5.

Replay the committed stable and rejected golden runs with:

```bash
uv run pytest -q tests/test_phase4_golden.py
```

See [`docs/DISCOVERY_MVP.md`](docs/DISCOVERY_MVP.md) for algorithms, caps, artifact formats,
Markov-like transition caveats, the frozen AI interpretation handoff, and the Phase 5 boundary.

The discovery software, including PCA/K-means, stability analysis, motifs, and Markov-like
boundary-aware transitions, is ready for real market experiments. The remaining handoff is
deliberate: freeze an immutable market snapshot and publish its Phase 3 feature/event dataset.
Those experiments remain outcome-blind Phase 4 research, not Phase 5 validation or evidence of an
edge.
