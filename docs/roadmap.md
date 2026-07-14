# Roadmap

Repository Readiness

- keep Python pinned to 3.13 through uv
- keep `pyproject.toml` as the only dependency definition
- keep notebooks exploratory and production logic in `src/`
- maintain `docs/RESEARCH_LOG.md` as the lab notebook

Dataset Layer

- expose canonical OHLCV loading through `src.datasets`
- keep SQL out of experiments and notebooks
- preserve immutable raw data and return reproducible Polars frames

Database

- provision PostgreSQL locally with Docker
- restore research dump into the dedicated research database
- create inspection tooling for schema and data quality

Profiles

- build deterministic OHLCV-derived volume profile engine
- build deterministic value area engine
- detect local HVN/LVN regions

Structure

- build auction structure summaries
- compare value migration between profiles
- track persistent nodes over time

State Modelling

- classify deterministic close location relative to value
- estimate empirical market state transitions
- evaluate probabilistic state models

Prediction

- generate short-horizon predictions from structure features

Trading

- evaluate strategies only after structure and state modelling are stable
