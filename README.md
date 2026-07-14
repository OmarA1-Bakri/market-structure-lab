# Market Structure Lab

A lightweight quantitative research lab for discovering statistically significant cryptocurrency
market-structure behavior from OHLCV data.

## Project goal

The long-term objective is to build a deterministic understanding of the auction process before
introducing trading logic. The intended sequence is:

Raw OHLCV data -> Dataset layer -> Auction engine -> Volume profile -> Value area -> HVN/LVN ->
Auction structure -> Auction state -> Transition analysis -> Feature discovery -> Strategy research.

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
uv sync
```

## Docker

Start PostgreSQL and pgAdmin:

```bash
docker compose up -d
```

The database is configured to restore the provided dump into a dedicated research database named `research`.

## Restoring the database

The dump file is expected at:

```text
data/dumps/callscore.dump
```

The PostgreSQL container will attempt to restore it automatically during initialization.

## Running scripts

Inspect the database:

```bash
uv run python scripts/inspect_database.py
```

Load candles in research code:

```python
from src.datasets import load_dataset, load_symbol

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
from src.experiments import ExperimentConfig, save_experiment_result

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
- keep algorithms inside src/
- keep notebooks exploratory only
- favour reproducibility over cleverness
- keep SQL inside dataset modules, not experiments
