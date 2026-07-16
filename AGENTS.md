# AGENTS.md

## Scope and authority

This file applies to the entire repository.

Market Structure Lab is a private quantitative research project for one engineer working with AI assistance. It is not a SaaS product, public framework, or enterprise platform.

Instruction precedence:

1. The user's current explicit instruction.
2. `docs/PRD.md`.
3. This file.
4. Other repository documentation.
5. Existing code and tests.

Read `docs/PRD.md` and this file before substantial work. Do not silently change the mission or skip phase gates.

---

# Mission

Build an auditable system that:

1. reconstructs deterministic cryptocurrency auction structure;
2. discovers recurring behaviours without requiring a human hypothesis;
3. uses AI to interpret frozen evidence and propose falsifiable theories;
4. validates candidates on untouched data with strict statistical controls;
5. promotes only robust, cost-adjusted behaviours into an edge catalogue;
6. converts validated edges into strategies and portfolios;
7. paper-trades, deploys cautiously, monitors drift, and retires decayed edges.

A guaranteed "money printer" does not exist. The target is a repeatable edge-discovery and capital-allocation machine that can produce positive expectancy after fees, spread, slippage, funding, latency, missed fills, and risk limits.

Canonical progression:

```text
Raw data
  → canonical datasets and quality checks
  → deterministic auction engine
  → versioned features and events
  → outcome-blind behaviour discovery
  → AI interpretation and hypothesis generation
  → untouched validation
  → behaviour and edge catalogues
  → cost-aware strategy construction
  → portfolio risk and execution
  → paper, shadow, and live deployment
  → drift monitoring and retirement
```

Do not skip stages.

---

# Current phase

Unless the user explicitly changes it, work on **Phase 0: trustworthy foundation** only.

Phase 0 priorities, in order:

1. Migrate to `src/market_structure_lab/` and update imports, build config, scripts, and tests.
2. Fix PostgreSQL restore mounts, script behaviour, and health checks.
3. Replace manufactured database inspection with real schema and data-quality inspection.
4. Inspect the restored dump before defining source-table mappings.
5. Implement configurable source-to-canonical candle mapping.
6. Make large reads bounded-memory and add partitioned Parquet export.
7. Use integer price-bin indices internally.
8. Separate profile calculation from explicit window policies.
9. Remove unbounded cumulative auction state as the default.
10. Stop importing private helpers across modules.
11. Correct transition analysis for serial dependence, gaps, sessions, and symbol boundaries.
12. Support `discovery`, `hypothesis`, `validation`, and `strategy` experiment modes.
13. Remove or implement placeholder operational scripts.
14. Run and document the Phase 0 verification suite.

Do not begin clustering, AI hypothesis generation, strategy optimisation, exchange integration, or live execution during Phase 0.

When Phase 0 is complete, stop and present evidence. Continue only after explicit user approval.

---

# Hard research constraints

## Data integrity

- Raw data is immutable.
- Never modify `data/dumps/callscore.dump`.
- Never commit dumps, database volumes, secrets, caches, or generated run artefacts unless explicitly requested.
- Every derived dataset must be reproducible from a dataset snapshot, mapping, configuration, feature version, and code commit.
- Never print passwords or credential-bearing connection URLs.

## Deterministic before probabilistic

Define and test these concepts before machine learning consumes them:

- Volume profile
- POC
- VAH and VAL
- VWAP and anchored VWAP
- HVNs and LVNs
- Value migration
- Balance and imbalance
- Acceptance and rejection
- Auction location and state

Do not use machine learning to conceal an unstable market representation.

## Discovery before hypothesis

Experiment modes:

- `discovery`: no hypothesis required;
- `hypothesis`: human, AI, or joint falsifiable theory;
- `validation`: frozen candidate tested on untouched data;
- `strategy`: validated edge translated into trading rules.

The primary discovery pipeline must not require a human trading idea.

## Outcome-blind discovery

Do not use future outcomes while forming behaviours. Exclude:

- future returns;
- MFE and MAE;
- continuation/reversal labels;
- target-hit labels;
- profitability;
- future volatility labels.

Freeze the detector or cluster definition before outcomes are attached.

## AI role

AI may interpret frozen evidence packs containing representative windows, feature distributions, stability metrics, transitions, and charts.

AI may describe behaviours, name them neutrally, propose mechanisms, identify alternative explanations, and write falsifiable hypotheses.

AI may not:

- validate an edge;
- infer institutional, whale, or smart-money activity from OHLCV alone;
- tune thresholds after seeing final-holdout results;
- hide contradictory or failed evidence.

Validation code and predefined promotion criteria determine status.

## Trial accounting

Record successful, failed, inconclusive, and abandoned trials. A run should retain:

- stable ID and mode;
- dataset and feature versions;
- code commit;
- config and seed;
- symbols, ranges, and splits;
- algorithms and parameters;
- AI prompt/model metadata when used;
- metrics, artefacts, warnings, and conclusion.

Never report one attractive result without the size of the search that produced it.

## Costs and leverage

A gross backtest is not an edge. Before promotion, model realistic fees, spread, slippage, funding, latency, fill probability, missed fills, turnover, and capacity.

Portfolio robustness comes before leverage. Never use leverage to rescue weak expectancy.

---

# Data truth and terminology

The initial source is approximately 20 million one-minute OHLCV candles across roughly 18 liquid cryptocurrencies and about two years of history.

OHLCV does not reveal exact aggressor direction, queue position, order-book depth, participant identity, exact trade-by-trade volume at price, open interest, funding, or liquidation maps.

Therefore:

- call OHLCV-derived volume profiles approximations;
- keep allocation models explicit and versioned;
- use neutral, observable terminology;
- do not claim institutional causation from OHLCV alone.

Prefer names such as `ValueAcceptanceAfterExpansion`, `AuctionSnapshot`, `ProfileWindow`, and `BehaviourCandidate`.

Avoid unsupported names such as `InstitutionalAccumulation`, `SmartMoneyEntry`, and `WhaleSupport`.

---

# Architecture rules

Use a conventional source layout:

```text
src/
  market_structure_lab/
    core/
    data/
    profiles/
    auction/
    features/
    events/
    discovery/
    research/
    statistics/
    backtest/
    portfolio/
    cli/
```

Create directories only when they contain implemented code. Do not scaffold speculative packages.

All imports must use `market_structure_lab.*`. Do not add new imports from a package named `src`.

Maintain these boundaries:

```text
profile calculation ≠ window policy
behaviour discovery ≠ outcome evaluation
edge validation ≠ strategy construction
strategy definition ≠ fill simulation
research detector ≡ paper/live detector
```

## Data layer

Only the data layer may contain source-specific SQL.

Research, notebooks, features, discovery, statistics, and models must use canonical APIs such as:

```python
candles = load_candles(
    symbol="BTCUSDT",
    timeframe="1m",
    start="2025-01-01T00:00:00Z",
    end="2025-02-01T00:00:00Z",
)
```

Do not assume a table name or source schema before inspecting the restored database.

Large reads must have a bounded-memory path. Do not materialise millions of SQL rows as Python dictionaries. Prefer batched/server-side reads and partitioned Parquet queried with Polars or DuckDB.

## Profile and auction engine

- Use integer bin indices internally; convert to price only at public boundaries.
- Keep allocation models separate from window policies.
- Every auction engine has an explicit window policy.
- Unbounded cumulative state is not the default.
- Do not import underscore-prefixed helpers across modules.
- The auction engine is the canonical candle-by-candle deterministic state calculator.
- Do not create competing definitions of POC, value area, acceptance, balance, or migration.

Window policies may include fixed, rolling, session, anchored, and composite ranges when implemented and tested. Visible range is a UI selection, not a production research primitive.

## Discovery

Maintain two complementary feature tracks:

1. auction-informed features such as POC migration, value width, VWAP distance, acceptance, balance, and node persistence;
2. minimally assumptive sequence features such as normalised returns, candle geometry, volatility, and volume changes.

Algorithms discover recurring structure. AI interprets frozen evidence rather than raw millions of candles.

---

# Statistical rules

Adjacent minute observations are serially dependent and are not independent samples.

Where relevant, use:

- dwell/run compression;
- event-level samples;
- configurable horizons;
- no transitions across symbol boundaries or material gaps;
- symbol/session grouping;
- block bootstrap;
- purging and embargo for overlapping labels;
- chronological walk-forward evaluation;
- asset holdouts where feasible.

Before discovery, freeze:

- discovery/train period;
- development-validation period;
- final untouched holdout.

The final holdout must not be used for feature selection, cluster selection, threshold tuning, hypothesis writing, parameter optimisation, or strategy construction.

Where applicable, use multiple-testing correction, block-bootstrap confidence intervals, deflated Sharpe analysis, negative controls, shuffled/placebo tests, parameter perturbation, and regime/asset breakdowns.

A behaviour is not an edge. A hypothesis is not an edge. A profitable in-sample backtest is not an edge.

---

# Coding rules

- Target Python 3.13.
- Use `uv`; `pyproject.toml` is the dependency source of truth.
- Prefer Polars over pandas.
- Use UTC-aware timestamps.
- Prefer dataclasses, enums, protocols, and plain functions over inheritance-heavy designs.
- Keep functions small, typed, explicit, and deterministic.
- Avoid mutable global state, hidden I/O, and magic numbers.
- Make randomness explicit and seedable.
- Do not add dependencies without a measured need.
- Do not introduce framework abstractions before two real implementations justify them.
- Keep public APIs narrow.
- Never import private underscore-prefixed helpers from another module.
- Fail loudly on corrupt data, leakage risk, restore failure, and invalid configuration.
- Never log secrets.

Jupyter notebooks are for exploration, visualisation, and demonstrations. Canonical algorithms belong in the package and are imported by notebooks.

Operational scripts must perform real work or be removed. Prefer thin CLI wrappers over duplicated logic.

---

# Destructive-operation safety

The repository is commonly used from Windows/PowerShell with Docker Desktop and may also be used through WSL2.

Obtain explicit user confirmation before:

- deleting `data/postgres`;
- running `docker compose down -v`;
- reinitialising PostgreSQL;
- modifying or replacing the dump;
- dropping schemas or databases;
- running destructive migrations;
- replacing large derived datasets.

PostgreSQL entrypoint scripts run only on a fresh data directory. Do not imply that changing an init script reruns restoration against an existing volume.

Restore scripts must fail fast and use safe fresh-database options such as `--no-owner` and `--no-acl`. Health checks must use configured database and user values.

---

# Agent workflow

## Before editing

1. Read `docs/PRD.md` and this file.
2. Inspect relevant code, tests, config, and docs.
3. Confirm the active phase.
4. Preserve working implementations where possible.
5. Identify destructive operations before running them.

## Plan

For broad changes, state concisely:

- what will change;
- why;
- what will remain unchanged;
- how it will be verified.

Do not create a speculative roadmap for a small task.

## Implement

- Make the smallest coherent change.
- Avoid unrelated refactors.
- Do not duplicate algorithms.
- Do not create placeholders for later phases.
- Preserve working behaviour unless the requirement changes it.
- Keep research and future paper/live detectors identical.

## Verify

Run targeted tests first, then the applicable full suite.

Standard PowerShell commands:

```powershell
uv sync
uv run ruff check .
uv run pytest -q
uv run mypy src
```

Formatting when needed:

```powershell
uv run ruff format .
```

Docker configuration validation:

```powershell
docker compose config
```

Do not run destructive reset commands without consent. Never claim a test passed unless it ran successfully.

## Review and report

Before finishing, check secrets, large files, imports, leakage, bounded-memory behaviour, UTC handling, deterministic output, symbol/session/gap boundaries, docs, and temporary artefacts.

Report:

- files changed;
- behaviour implemented;
- commands and results;
- assumptions;
- unresolved risks or blockers;
- whether the phase gate is satisfied.

Do not hide failures or soften correctness concerns.

---

# Testing expectations

Add tests when behaviour changes. Cover, where relevant:

- happy path and boundaries;
- empty datasets;
- invalid OHLC rows;
- nulls and duplicates;
- missing periods and large gaps;
- symbol and timeframe boundaries;
- UTC/time-zone handling;
- deterministic binning;
- rolling add/remove equivalence;
- no-look-ahead behaviour;
- fixed-seed reproducibility;
- serial-dependence-aware transitions.

Use small deterministic fixtures for ordinary tests. Do not require the full dump for unit tests. Mark database integration tests explicitly and make them skippable when PostgreSQL is unavailable.

---

# Definition of done

A task is complete only when:

- it matches the active phase and `docs/PRD.md`;
- relevant tests pass;
- Ruff passes for touched code;
- Mypy passes for touched code or limitations are documented;
- no secrets, dumps, volumes, caches, or generated noise are introduced;
- no look-ahead or outcome leakage is introduced;
- large-data code has a bounded-memory path;
- docs match actual behaviour;
- the final report contains evidence, not assertions.

Research work also requires, where applicable, a frozen manifest, dataset and feature versions, seed/config, trial accounting, machine-readable metrics, a human-readable conclusion, and explicit accepted/rejected/inconclusive status.

---

# Prohibited shortcuts

Do not:

- build a strategy before validating a behaviour;
- feed future outcomes into discovery;
- inspect the final holdout during exploration;
- treat all minute transitions as independent;
- claim exact volume-at-price from OHLCV;
- use institutional or whale narratives as facts without supporting data;
- tune repeatedly without recording every trial;
- assess an edge without realistic costs;
- add leverage to weak expectancy;
- introduce microservices, Kafka, Airflow, Kubernetes, MLflow, or a feature-store platform without a measured bottleneck;
- add APIs, dashboards, broker integrations, or live execution before their approved phase;
- replace simple working code with abstraction that has no measurable benefit;
- fabricate inspection output, test results, or operational readiness.

Every phase ends with a written evidence report. The next phase begins only after explicit user approval.