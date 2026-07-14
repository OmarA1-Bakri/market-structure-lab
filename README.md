# Market Structure Lab

A lightweight quantitative research platform for exploring cryptocurrency market structure with Python, PostgreSQL, Polars, DuckDB, and Plotly.

## Project goal

The long-term objective is to build a deterministic understanding of market structure before introducing trading logic. The intended sequence is:

Raw OHLCV data -> Volume profile -> Value area -> HVN/LVN -> Auction structure -> Market state -> Probabilistic state model -> Prediction -> Strategy.

## Repository layout

- data/ contains database dumps, PostgreSQL data, and export artifacts
- docker/ contains database and Python container setup
- docs/ contains architecture notes, roadmap, and research questions
- scripts/ contains operational utilities such as database inspection
- src/ contains research code organised by pipeline stage
- tests/ contains regression tests for the repository scaffold

## Environment

This repository uses Python 3.13 and uv. It does not depend on Conda.

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

## Research principles

- prefer small, typed, readable functions
- keep algorithms inside src/
- keep notebooks exploratory only
- favour reproducibility over cleverness
