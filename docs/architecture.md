# Architecture

The repository is organised as a single-engineer quantitative research workspace. The emphasis is on reproducible analysis rather than production deployment.

## Repository layout

- data/ stores raw dump files, PostgreSQL persistence, and exports
- docker/ hosts local database and development containers
- docs/ contains the research backlog and project notes
- scripts/ contains operational scripts for inspection and pipeline setup
- src/ contains the actual research pipeline code

## Processing pipeline

1. Ingest raw OHLCV data into PostgreSQL or DuckDB
2. Build volume profiles and value areas
3. Detect HVN/LVN regions and auction structure
4. Track persistent nodes and market states
5. Model state transitions and generate predictions
6. Use predictions as the basis for later strategy work

## Database layout

The default research database is named `research`. PostgreSQL is intended to be the canonical storage layer for long-lived datasets, while DuckDB can be used for local exploratory analysis when convenient.
