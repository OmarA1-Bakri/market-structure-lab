# Architecture

The repository is organised as a single-engineer quantitative research workspace. The emphasis is on reproducible analysis rather than production deployment.

## Repository layout

- data/ stores raw dump files, PostgreSQL persistence, and exports
- docker/ hosts local database and development containers
- docs/ contains the research backlog and project notes
- scripts/ contains operational scripts for inspection and pipeline setup
- src/ contains the actual research pipeline code

## Processing pipeline

1. Preserve raw OHLCV data in PostgreSQL or DuckDB
2. Load candles through the canonical dataset layer
3. Update auction state candle by candle
4. Build volume profiles and value areas
5. Detect HVN/LVN regions and auction structure
6. Track deterministic auction states
7. Estimate state-transition probabilities
8. Use statistically significant structure as the basis for later strategy work

## Database layout

The default research database is named `research`. PostgreSQL is intended to be the canonical storage layer for long-lived datasets, while DuckDB can be used for local exploratory analysis when convenient.

## Dataset layer

Research code should import from `src.datasets` and should not contain SQL. The first canonical
interface is:

```python
from src.datasets import load_dataset, load_symbol
```

`load_dataset` returns a Polars frame with ordered OHLCV columns:

```text
timestamp, symbol, timeframe, open, high, low, close, volume
```

Time windows are half-open: `start <= timestamp < end`.

## Experiment artifacts

Use `src.experiments.save_experiment_result` for research runs that need durable evidence. Each
run writes:

- `config.json`
- `metrics.json`
- `summary.md`
- `plots/`
- `artifacts/`

The artifact writer is intentionally small. It records outputs; it does not schedule jobs, optimize
strategies, or own research logic.

## Auction engine

Use `src.auction.AuctionEngine` for candle-by-candle deterministic state updates. The first engine
version builds an OHLCV-derived volume profile on each update and exposes:

- point of control
- value area low/high
- close location relative to value

The current volume-profile assumption is deliberately simple and auditable: without trade-level
data, each candle's volume is distributed equally across every price bin touched by its low-high
range. Replace this only when better source data is available and covered by regression tests.

## Structure layer

Use `src.structure` for deterministic features built from volume profiles. The first structural
features are:

- local high-volume and low-volume nodes
- point-of-control migration between profiles
- value-area midpoint migration between profiles
- value-area overlap width

These functions are intentionally deterministic feature primitives. Statistical significance and
transition probabilities should be estimated in later research modules using these primitives.

## Transition analysis

Use `src.transitions` to count adjacent observed state transitions and estimate conditional
probabilities:

```text
P(next_state | current_state)
```

Rows include support counts, so later research can filter low-observation transitions before
claiming statistical significance.
