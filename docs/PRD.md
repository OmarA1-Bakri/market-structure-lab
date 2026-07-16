# Market Structure Lab — Master PRD and Codex Implementation Prompt

## Strategic outcome

Market Structure Lab is intended to become an autonomous quantitative research and trading system that:

1. reconstructs deterministic auction structure from market data;
2. discovers recurring behaviours without requiring a human hypothesis;
3. uses AI to describe those behaviours and propose falsifiable theories;
4. validates candidates on untouched data with strict statistical controls;
5. promotes only robust candidates into an edge catalogue;
6. converts validated edges into cost-aware strategies;
7. combines several low-correlation strategies into a risk-controlled portfolio;
8. paper-trades, shadows, deploys, monitors, and retires edges automatically.

The target is not a guaranteed “money printer.” The target is a repeatable edge-discovery and capital-allocation machine that produces positive expectancy after fees, slippage, funding, latency, and drawdown constraints.

---

# Product Requirements Document

## 1. Document control

- **Product:** Market Structure Lab
- **Version:** 1.0
- **Status:** Build directive
- **Owner:** Omar A1-Bakri
- **Primary operator:** One engineer working in VS Code with Codex/AI assistance
- **Primary language:** Python 3.13

## 2. Mission

> Build the most accurate, auditable representation of cryptocurrency market structure possible; discover recurring market behaviours from data with minimal human bias; validate them rigorously; and deploy only those that demonstrate durable, cost-adjusted positive expectancy.

## 3. Problem statement

Most retail trading systems begin with an indicator or chart pattern, optimise it on historical data, and confuse in-sample fit with an edge. Market Structure Lab must invert that process.

The platform must first build a reliable language for the market auction, then discover recurring behaviours, then allow AI to propose explanations, and only then test whether any behaviour supports a tradeable edge.

## 4. Product vision

The mature system is a closed research-to-execution loop:

```text
Raw market data
        ↓
Data quality and canonical datasets
        ↓
Deterministic auction engine
        ↓
Versioned feature and event datasets
        ↓
Outcome-blind behaviour discovery
        ↓
AI interpretation and hypothesis generation
        ↓
Untouched out-of-sample validation
        ↓
Behaviour catalogue
        ↓
Validated edge catalogue
        ↓
Strategy construction and simulation
        ↓
Portfolio risk and execution
        ↓
Paper, shadow, and live trading
        ↓
Drift monitoring, retirement, and rediscovery
```

## 5. What success means

The product is successful when it can:

- reconstruct market structure for any symbol and historical timestamp;
- generate deterministic auction snapshots reproducibly;
- discover stable recurring behaviours across time and/or assets;
- describe discovered behaviours without inventing unsupported causal claims;
- generate falsifiable hypotheses with traceable evidence;
- reject false discoveries aggressively;
- demonstrate net positive expectancy on untouched data after conservative costs;
- reproduce backtest decisions candle by candle without look-ahead;
- paper-trade the same strategy logic used in research;
- deploy small capital with explicit risk and kill switches;
- detect edge degradation and stop trading automatically.

## 6. What the product is not

This is not:

- a public SaaS product;
- an enterprise data platform;
- a generic backtesting framework;
- a collection of TradingView indicators;
- an LLM that reads raw candles and guesses trades;
- a deep-learning project by default;
- a leverage-first trading bot;
- a promise of guaranteed profit.

Do not add microservices, Kubernetes, Kafka, Airflow, MLflow, dependency injection frameworks, or governance-heavy processes unless a measured bottleneck justifies them.

## 7. Core principles

### 7.1 Data before models

Raw candles are immutable. Every derived dataset must be reproducible from a known raw dataset, configuration, code commit, and feature version.

### 7.2 Deterministic before probabilistic

POC, value area, profile nodes, VWAP, value migration, balance, acceptance, and other auction concepts must have auditable deterministic definitions before machine learning consumes them.

### 7.3 Discovery before hypothesis

Discovery runs must not require a prewritten hypothesis. Human hypotheses may be stored as examples or separate research tracks, but they must not bias the primary discovery pipeline.

### 7.4 Outcome-blind discovery

Future returns, MFE, MAE, trade labels, or continuation/reversal labels must not be used to form behaviour clusters. Outcomes are attached only after behaviour definitions are frozen.

### 7.5 AI interprets evidence; it does not manufacture it

An LLM may receive cluster summaries, representative windows, feature distributions, transition statistics, and charts. It may name behaviours and propose theories. It may not declare an edge. Validation code makes that decision.

### 7.6 Every trial is recorded

Discovery settings, feature sets, model settings, prompts, random seeds, outputs, failures, and rejected candidates must be retained. Failed research is an asset.

### 7.7 Costs and execution are part of the edge

A gross backtest is not an edge. Fees, spread, slippage, funding, latency, order type, fill assumptions, and capacity must be included before promotion.

### 7.8 Portfolio before leverage

Leverage is considered only after a strategy has survived out-of-sample and paper validation. Capital is allocated across low-correlation edges, not concentrated in one attractive backtest.

## 8. Data scope and constraints

### 8.1 Current data

- approximately 20 million one-minute OHLCV candles;
- approximately 18 liquid cryptocurrencies;
- approximately two years of history;
- continuously updated PostgreSQL source database.

### 8.2 Known limitations

One-minute OHLCV does not provide exact trade direction, order-book state, queue position, exact volume-at-price, liquidation maps, open interest, funding, ETF flows, or exchange-flow data.

The initial platform must therefore label volume-profile calculations as OHLCV-derived approximations and must not use terms such as “institutional buying” as proven causal facts.

### 8.3 Future enrichments

The architecture must permit later addition of:

- aggregate trades;
- bid/ask or order-book snapshots;
- funding rates;
- open interest;
- liquidation data;
- spread and depth;
- venue-specific fees;
- ETF and flow data for BTC where appropriate.

These are later enrichments, not blockers for the first research system.

## 9. Current repository baseline

The repository already contains useful foundations:

- a Polars dataset loader;
- an OHLCV-derived volume-profile baseline;
- a candle-by-candle auction engine;
- deterministic auction-location states;
- local HVN/LVN detection;
- value-migration comparison;
- empirical transition matrices;
- binomial transition-enrichment tests;
- Benjamini-Hochberg screening;
- experiment artifact writing;
- unit tests for these components.

These components should be preserved conceptually but refactored where required by this PRD.

## 10. Mandatory corrections to the current repository

### 10.1 Correct Python packaging

Migrate from importing a package named `src` to a conventional source layout:

```text
src/
    market_structure_lab/
        ...
```

All imports must use `market_structure_lab.*`. Update Hatch configuration and tests.

### 10.2 Fix PostgreSQL restore wiring

The current restore script is mounted under a nested directory below `/docker-entrypoint-initdb.d`, which the standard image does not treat as a recursive script directory.

Mount the restore script directly as:

```text
/docker-entrypoint-initdb.d/10_restore_dump.sh
```

and the dump as:

```text
/docker-entrypoint-initdb.d/callscore.dump
```

The restore script must:

- run only during fresh database initialisation;
- fail fast on restore errors;
- use `--no-owner` and `--no-acl`;
- not use `--clean` against a fresh database;
- print clear start and finish messages.

The health check must use the configured database and user rather than hard-coded values.

### 10.3 Replace fake inspection with real inspection

`scripts/inspect_database.py` currently reports an empty manufactured table list. Replace it with real database inspection that reports:

- schemas;
- tables and estimated/exact counts;
- columns and types;
- candidate OHLCV tables;
- symbols;
- timeframes;
- minimum and maximum timestamps;
- duplicate key counts;
- invalid OHLC rows;
- null counts;
- missing-minute summaries by symbol;
- large gaps;
- sample rows.

The script must never print database passwords.

### 10.4 Stop assuming the candle table schema

Do not assume the restored table is named `ohlcv` or that its columns match the current loader. Inspect the real dump first, then create a configurable mapping from source schema to canonical candle schema.

### 10.5 Make data loading bounded-memory

The current loader materialises all SQL rows as Python dictionaries before creating a Polars frame. Replace this for large reads with batched/server-side loading and provide partitioned Parquet export.

### 10.6 Replace floating-point price keys internally

Volume profiles must use integer bin indices internally. Convert to prices only at boundaries. This avoids floating-point key drift and improves deterministic equality.

### 10.7 Separate profile calculation from window policy

The profile algorithm and the profile window must be independent.

Required window policies:

- fixed range;
- rolling duration/bar count;
- session/day/week/month;
- anchored timestamp/event;
- composite range.

“Visible range” is a UI selection, not a production research primitive. It may exist only as a visual adapter.

### 10.8 Remove unbounded cumulative auction state as the default

The current auction engine accumulates every candle forever. The engine must be created with an explicit window policy and support adding and removing candle contribution for rolling windows.

### 10.9 Stop importing private helper functions

The auction engine currently imports underscore-prefixed profile functions. Expose stable public profile primitives or a dedicated accumulator.

### 10.10 Correct transition independence assumptions

Adjacent minute states are highly serially dependent. Do not treat every adjacent transition as an independent observation.

Add:

- dwell/run compression;
- event-level transitions;
- configurable transition horizons;
- block-bootstrap uncertainty;
- symbol/session grouping;
- no transitions across gaps or symbol boundaries.

### 10.11 Make hypotheses optional

The current experiment configuration requires a hypothesis. Introduce experiment modes:

- `discovery` — no hypothesis required;
- `hypothesis` — a human or AI hypothesis is specified;
- `validation` — a frozen candidate is tested on untouched data;
- `strategy` — a validated edge is translated into trade logic.

## 11. Target repository structure

Create directories only when they contain implemented code.

```text
src/market_structure_lab/
    core/
        config.py
        ids.py
        time.py
        types.py
    data/
        catalog.py
        connection.py
        mapping.py
        quality.py
        loader.py
        export.py
    profiles/
        models.py
        bins.py
        allocation.py
        calculator.py
        accumulator.py
        windows.py
        nodes.py
    auction/
        models.py
        engine.py
        vwap.py
        acceptance.py
        balance.py
        migration.py
    features/
        definitions.py
        builder.py
        normalization.py
        registry.py
    events/
        models.py
        fixed_windows.py
        change_points.py
        structural_events.py
    discovery/
        datasets.py
        reduction.py
        clustering.py
        motifs.py
        stability.py
        outcomes.py
        interpretation.py
    research/
        experiments.py
        manifests.py
        catalogue.py
        promotion.py
    statistics/
        bootstrap.py
        multiple_testing.py
        sharpe.py
        validation.py
        transitions.py
    backtest/
        models.py
        engine.py
        fills.py
        costs.py
        metrics.py
    portfolio/
        allocation.py
        exposure.py
        correlation.py
        risk.py
    execution/                 # create only when paper/live work begins
    cli/
        main.py
```

## 12. Storage design

### 12.1 Canonical raw storage

PostgreSQL remains the canonical source for raw market data.

### 12.2 Derived analytical storage

Use partitioned Parquet for large derived datasets:

```text
data/derived/
    dataset_version=v001/
        feature_set=v001/
            symbol=BTCUSDT/
                year=2025/
                    month=01/
```

DuckDB and Polars may query these files directly.

### 12.3 Research artefacts

```text
research/
    runs/
        DR-000001/
        HR-000001/
        VR-000001/
    behaviours/
        B-000001/
    hypotheses/
        H-000001/
    edges/
        E-000001/
    strategies/
        S-000001/
```

Each record must have both a machine-readable manifest and a human-readable summary.

## 13. Stable identifiers and lifecycle

### 13.1 Identifiers

- `DS-######` — dataset snapshot
- `FS-######` — feature-set version
- `DR-######` — discovery run
- `B-######` — discovered behaviour
- `H-######` — hypothesis
- `VR-######` — validation run
- `E-######` — validated edge
- `S-######` — strategy
- `BT-######` — backtest
- `PT-######` — paper-trading run
- `LT-######` — live deployment

### 13.2 Lifecycle

```text
Discovered behaviour
        ↓
Stable / unstable / duplicate / noise
        ↓
Hypothesis proposed
        ↓
Rejected / inconclusive / supported
        ↓
Edge candidate
        ↓
Validated / rejected
        ↓
Strategy candidate
        ↓
Backtest approved / rejected
        ↓
Paper approved / rejected
        ↓
Live / paused / retired
```

## 14. Canonical data models

### 14.1 Candle

Required fields:

- timestamp UTC;
- symbol;
- venue if available;
- timeframe;
- open;
- high;
- low;
- close;
- volume;
- source identifier.

Validation:

- `low <= open, close <= high`;
- `high >= low`;
- non-negative volume;
- unique canonical key;
- ordered timestamps.

### 14.2 AuctionSnapshot

Required fields:

- timestamp;
- symbol;
- profile/window identifier;
- POC;
- VAH;
- VAL;
- VWAP and slope where defined;
- value-area width;
- POC velocity;
- value midpoint velocity;
- close location relative to value;
- HVN/LVN summary;
- balance/imbalance metrics;
- acceptance/rejection metrics;
- volatility and volume context;
- feature/model versions.

### 14.3 MarketEvent

Required fields:

- event ID;
- symbol;
- start and end timestamps;
- event type;
- trigger definition;
- feature vector at event time;
- no future outcome fields in the discovery representation.

### 14.4 Behaviour

Required fields:

- behaviour ID;
- discovery run ID;
- frozen detector/cluster definition;
- representative events;
- feature centroid/distribution;
- stability metrics;
- frequency and duration;
- asset and regime coverage;
- human/AI description;
- status.

### 14.5 Hypothesis

Required fields:

- hypothesis ID;
- originating behaviour IDs;
- origin: AI, human, or joint;
- falsifiable statement;
- detector definition frozen before validation;
- predicted outcome and horizon;
- validation plan;
- status and evidence.

### 14.6 Edge

Required fields:

- edge ID;
- source hypothesis;
- eligible universe/regime;
- net expected return distribution;
- uncertainty interval;
- decay profile;
- turnover and capacity;
- cost assumptions;
- invalidation conditions;
- approved strategy uses.

## 15. Dataset and data-quality requirements

### 15.1 Canonical dataset API

Research code must use APIs such as:

```python
candles = load_candles(
    symbol="BTCUSDT",
    timeframe="1m",
    start="2025-01-01T00:00:00Z",
    end="2025-02-01T00:00:00Z",
)
```

No SQL in notebooks, experiments, feature code, or models.

### 15.2 Dataset snapshots

Every run must record:

- source table mapping;
- timestamp range;
- symbols;
- row count;
- quality summary;
- dataset hash or deterministic fingerprint;
- code commit;
- dump/source identity.

### 15.3 Data split policy

Before discovery, freeze chronological partitions:

- discovery/train period;
- development validation period;
- final holdout period.

The final holdout must not be used for feature selection, cluster selection, threshold tuning, hypothesis writing, or strategy optimisation.

Add asset holdouts where feasible so a behaviour can be tested on unseen symbols as well as unseen time.

## 16. Volume-profile requirements

### 16.1 Allocation models

Implement and compare versioned OHLCV allocation models:

1. uniform across touched bins — transparent baseline;
2. typical-price allocation — single-price baseline;
3. triangular/close-weighted allocation — optional approximation;
4. lower-timeframe reconstruction — when a higher timeframe is built from available one-minute bars;
5. trade-derived exact allocation — future data source.

No allocation model may be described as exact when based only on OHLCV.

### 16.2 Binning

Support:

- exchange tick size;
- fixed price step;
- percentage/log-price bins;
- target bin count;
- volatility-scaled step.

All profile definitions and parameters must be versioned.

### 16.3 Value area

Support at minimum:

- POC-outward contiguous expansion;
- configurable target fraction;
- deterministic tie-breaking;
- unit tests for gaps, plateaus, ties, and sparse profiles.

### 16.4 Node detection

Provide:

- raw local-extrema baseline;
- optional smoothing;
- prominence;
- width;
- plateau handling;
- node zones rather than only single-price points;
- node persistence tracking.

## 17. Auction-engine requirements

The engine must:

- accept candles in timestamp order;
- reject or explicitly handle gaps and duplicates;
- be parameterised by window policy;
- produce immutable snapshots;
- support rolling add/remove contribution efficiently;
- maintain POC, value area, VWAP, nodes, and migration metrics;
- emit structural events;
- never use future information;
- permit exact replay from a dataset snapshot and configuration.

The engine must not claim to identify institutions. It identifies observable auction behaviour.

## 18. Feature requirements

### 18.1 Dual discovery tracks

To reduce human feature bias, generate two feature families:

#### Auction-informed features

Examples:

- POC level and velocity;
- VAH/VAL and midpoint velocity;
- value width and concentration;
- VWAP distance and slope;
- close location within value;
- HVN/LVN distances and persistence;
- acceptance/rejection scores;
- balance/imbalance;
- volume and volatility regime;
- transition dwell time.

#### Minimally assumptive sequence features

Examples:

- normalised returns;
- candle range and body ratios;
- wick ratios;
- volume changes;
- realised volatility;
- autocorrelation summaries;
- shape embeddings or motifs.

The platform must compare discoveries from both tracks.

### 18.2 Normalisation

Features must be comparable across assets with different prices and volatility. Use robust, train-fitted normalisation. Never fit scalers on validation or holdout data.

### 18.3 Feature registry

Each feature must have:

- name;
- definition;
- units;
- required history;
- missing-value behaviour;
- version;
- leakage classification;
- tests.

## 19. Event segmentation requirements

Discovery must not rely only on every overlapping minute window.

Support:

- fixed non-overlapping windows;
- rolling windows for exploratory use;
- session boundaries;
- change-point events;
- volatility/volume expansion events;
- value-area exit/re-entry events;
- POC migration events;
- node tests and traversals.

Every event must have a clear information cutoff timestamp.

## 20. Behaviour-discovery requirements

### 20.1 Discovery is outcome-blind

Cluster and motif formation must exclude forward returns and trade outcomes.

### 20.2 Baseline methods

Implement simple baselines before advanced models:

- PCA for inspectable reduction;
- K-means or Gaussian mixtures as deterministic baselines;
- density clustering where useful;
- hierarchical clustering for comparison;
- matrix-profile or motif discovery for sequence shapes;
- deterministic transition/state analysis.

HMMs and deep sequence models are later consumers, not the first implementation.

### 20.3 Stability

A behaviour is not catalogued unless it demonstrates acceptable stability across:

- random seeds where applicable;
- bootstrap or subsample runs;
- adjacent time periods;
- at least part of the asset universe;
- reasonable parameter perturbations.

### 20.4 Outcome characterisation

Only after behaviour definitions are frozen, attach:

- forward return distributions at multiple horizons;
- MFE and MAE;
- time to threshold;
- continuation/reversal frequencies;
- volatility after the event;
- liquidity/cost proxies;
- transition destinations.

## 21. AI research-agent requirements

The LLM/AI layer operates on structured summaries, not raw millions of rows.

Inputs may include:

- behaviour manifests;
- feature distributions;
- representative charts/events;
- nearest and contrasting behaviours;
- stability results;
- discovery-period outcome summaries;
- transition tables.

Outputs must include:

- plain-language description;
- neutral behaviour name;
- candidate mechanism clearly marked as inference;
- falsifiable hypothesis;
- required detector fields;
- proposed validation horizon and metrics;
- reasons the finding may be spurious;
- links to source run and behaviour IDs.

The AI must not:

- mark an edge as validated;
- see the final holdout before hypothesis freeze;
- silently change detectors or thresholds;
- use causal language without evidence;
- create a trade based only on a chart narrative.

Record AI provider/model, prompt, temperature, timestamp, and response hash.

## 22. Hypothesis example policy

The previously discussed “Institutional VWAP Acceptance” setup may be retained only as a clearly labelled human example:

```text
research/examples/HUMAN-EXAMPLE-IVA.md
```

Rename it to neutral language such as “Value Acceptance After Impulsive Expansion.” It must not be included as a privileged discovery target or used to tune the unsupervised discovery pipeline.

## 23. Statistical-validation requirements

### 23.1 Unit of analysis

Use independent or approximately independent events, not every serially correlated minute.

### 23.2 Required safeguards

- chronological out-of-sample validation;
- purging and embargo where feature/label windows overlap;
- walk-forward analysis;
- block bootstrap confidence intervals;
- multiple-testing control;
- trial-count logging;
- parameter sensitivity analysis;
- symbol and regime breakdowns;
- negative controls and shuffled-label tests;
- cost stress tests;
- deflated/probabilistic Sharpe analysis when strategies are evaluated;
- probability-of-backtest-overfitting analysis where practical.

### 23.3 Edge-promotion default gates

Defaults are internal starting thresholds and must remain configurable.

A candidate cannot be promoted to `E-*` unless:

- its detector was frozen before final validation;
- it has sufficient effective independent support, with 500 events as the default target;
- its net expectancy confidence interval excludes zero on final holdout;
- it survives multiple-testing correction;
- it remains positive under at least 2x the base estimated transaction costs;
- it is not dependent on one short period or one symbol unless explicitly approved as a specialised edge;
- nearby parameter values produce broadly similar results;
- its failure and invalidation regimes are documented;
- all trials used to reach it are recorded.

## 24. Strategy and backtesting requirements

Strategy construction starts only from validated edges.

The backtester must:

- use the same detector code as research;
- enforce information cutoffs;
- avoid same-bar fantasy fills;
- model market and limit orders conservatively;
- include fees, spread, slippage, funding, and latency;
- support partial/no fills where relevant;
- prevent overlapping event leakage;
- report gross and net results;
- expose per-symbol, per-regime, and per-period performance;
- produce a full trade ledger and decision log.

Initial strategy approval defaults:

- positive final-holdout net expectancy;
- annualised net Sharpe target of at least 1.0;
- deflated Sharpe confidence target of at least 95%;
- profit factor target of at least 1.15;
- positive performance under doubled costs;
- acceptable max drawdown at target risk, initially no more than 15%;
- no single asset responsible for more than 35% of portfolio PnL unless the strategy is explicitly single-asset;
- at least 300 independent trades/events where the strategy frequency permits.

These are promotion gates, not optimisation objectives.

## 25. Portfolio and risk requirements

A real production system should combine several validated edges.

Required controls:

- maximum position risk;
- maximum portfolio heat;
- symbol and correlated-cluster exposure caps;
- leverage cap;
- liquidation-price buffer;
- daily and rolling drawdown stops;
- volatility scaling;
- stale-data and exchange-disconnect kill switches;
- edge-level pause/retire controls;
- no martingale sizing;
- fractional Kelly only after conservative shrinkage, with a hard cap.

Optimise first for survival and consistency, not maximum return.

## 26. Paper, shadow, and live requirements

Promotion sequence:

```text
Historical validation
    ↓
Paper trading with simulated fills
    ↓
Shadow trading using live market data and recorded intended orders
    ↓
Small-notional live trading
    ↓
Gradual scaling
```

Default live-promotion gate:

- at least 8 weeks or 100 qualifying paper/shadow trades, whichever is longer for the strategy frequency;
- no material unexplained divergence between research and live detector output;
- realised costs within the stressed assumptions;
- no unresolved operational failures;
- drawdown and behaviour frequencies within expected ranges.

## 27. Edge monitoring and retirement

For every live edge, monitor:

- occurrence rate;
- feature-distribution drift;
- outcome drift;
- net expectancy;
- realised costs;
- hit rate and payoff distribution;
- drawdown;
- correlation with other edges;
- detector failures.

An edge must be automatically paused when configured degradation thresholds are breached. Retirement is a normal outcome, not a failure of the platform.

## 28. TradingView/Pine requirement

TradingView Pine Script is a downstream visual validation and alerting layer only.

After an edge is validated:

- reproduce the detector in Pine where Pine limitations permit;
- display profiles, value areas, behaviour/edge state, invalidation, and alerts;
- verify Pine signals against Python golden fixtures;
- never treat Pine as the research source of truth.

## 29. Experiment and artefact requirements

Every run must write a manifest containing:

- run ID and mode;
- question/hypothesis when applicable;
- dataset snapshot ID;
- feature-set ID;
- code commit;
- configuration;
- random seed;
- environment/lock hash;
- input time and asset partitions;
- metrics;
- artefact file hashes;
- completion status;
- parent run/candidate IDs.

A discovery run may have no hypothesis.

## 30. CLI requirements

Provide a small Typer-based CLI only when the underlying functions exist.

Target commands:

```text
msl db inspect
msl data validate
msl data export
msl auction build
msl features build
msl discover run
msl discover describe
msl validate run
msl backtest run
msl paper run
msl report show
```

No placeholder commands.

## 31. Non-functional requirements

### Reproducibility

Same dataset, config, and code must generate identical deterministic outputs.

### Performance

The system must process the full dataset in partitions without loading 20 million rows into Python objects at once.

### Testability

Every deterministic algorithm requires unit and property-style tests. Integration tests may use small fixtures. The large dump is not required in CI.

### Observability

Long-running jobs must show progress, elapsed time, partition, row count, and output location.

### Simplicity

Prefer functions and dataclasses. Use classes only for stateful engines or clear domain entities. No service layer for its own sake.

## 32. Delivery roadmap

### Phase 0 — Correct the foundation

Deliver:

- conventional package layout;
- working Docker restore;
- secure configuration;
- real database inspection;
- all existing tests migrated and passing;
- placeholder scripts removed or implemented.

Exit criteria:

- `uv sync` succeeds;
- `uv run pytest` succeeds;
- `uv run ruff check .` succeeds;
- `docker compose config` succeeds;
- fresh database initialisation restores the dump or reports a precise schema/restore blocker.

### Phase 1 — Establish data truth

Deliver:

- actual source-schema mapping;
- complete data-quality report;
- canonical candle loader;
- bounded-memory reads;
- partitioned Parquet export;
- dataset snapshot manifests.

Exit criteria:

- all 18 symbols and true date ranges are reported;
- duplicates, gaps, nulls, and invalid candles are quantified;
- a one-symbol date range can be loaded reproducibly;
- a large range can be exported without unbounded memory.

### Phase 2 — Auction representation

Deliver:

- refactored integer-bin profile engine;
- allocation-model interface;
- window-policy interface;
- rolling/session/fixed profiles;
- VWAP and value-area primitives;
- refactored auction engine;
- deterministic snapshots and structural events.

Exit criteria:

- golden fixtures pass;
- rolling add/remove output matches full recomputation;
- performance benchmark is recorded;
- all approximations are documented.

### Phase 3 — Feature and event datasets

Deliver:

- feature registry;
- auction-informed features;
- minimally assumptive sequence features;
- event segmentation;
- partitioned feature/event datasets;
- no future fields in discovery inputs.

Exit criteria:

- leakage audit passes;
- features are stable and versioned;
- event counts and overlap are reported.

### Phase 4 — Discovery MVP

Deliver:

- discovery/development/holdout split;
- baseline reduction and clustering;
- cluster/motif stability analysis;
- outcome-blind frozen behaviours;
- AI behaviour descriptions and candidate hypotheses;
- behaviour catalogue.

Exit criteria:

- at least one complete `DR-*` run is reproducible;
- unstable clusters are rejected;
- final holdout remains unread by discovery and interpretation steps.

### Phase 5 — Validation and edge promotion

Deliver:

- outcome attachment;
- block bootstrap;
- purged/embargoed validation;
- multiple-testing framework;
- parameter and cost stress;
- validation reports;
- edge catalogue and promotion logic.

Exit criteria:

- candidates are rejected or promoted by code-defined gates;
- all tested candidates and trials are retained;
- at least one full hypothesis lifecycle is demonstrated, even if rejected.

### Phase 6 — Strategy and backtest

Deliver:

- event-driven backtester;
- cost/fill model;
- trade ledger;
- strategy specification from edge records;
- portfolio metrics and risk constraints.

Exit criteria:

- no look-ahead tests pass;
- synthetic fill tests pass;
- gross/net/stressed results are available;
- final holdout remains separate from tuning.

### Phase 7 — Paper and live

Deliver:

- live data adapter;
- paper and shadow order ledger;
- Kraken execution adapter when approved;
- risk engine and kill switches;
- monitoring and edge retirement.

Exit criteria:

- paper/shadow promotion gates pass;
- small-notional live deployment is explicitly approved;
- rollback and pause controls are tested.

## 33. Definition of the “money printer”

The project earns that informal label only when:

- multiple independently validated edges exist;
- portfolio net expectancy is positive after conservative costs;
- performance survives untouched data and live shadowing;
- drawdowns remain inside an agreed risk budget;
- no single fragile behaviour controls the result;
- the system stops or reduces risk when its assumptions fail;
- live results remain acceptably close to the research distribution.

The true product is not one magical model. It is a machine that repeatedly discovers, validates, operates, monitors, and retires edges while protecting capital.

---

# Codex Master Implementation Prompt

## Role

You are the lead quantitative engineer implementing Market Structure Lab.

You are working in an existing Python repository. Read the complete `docs/PRD.md` before changing code. Treat it as the source of truth.

This is a private, founder-led quantitative research system. Optimise for correctness, research velocity, reproducibility, and capital safety. Do not introduce enterprise architecture or speculative abstractions.

## Current repository context

The repository already has:

- a basic Polars/SQLAlchemy OHLCV loader;
- an OHLCV-derived volume-profile baseline;
- a cumulative auction engine;
- auction-location states;
- HVN/LVN and value-migration primitives;
- transition matrices, binomial enrichment, and Benjamini-Hochberg screening;
- experiment artifact helpers;
- tests for the above.

Preserve useful behaviour, but refactor it to satisfy the PRD.

## Non-negotiable rules

1. Do not modify, delete, commit, or rewrite `data/dumps/callscore.dump`.
2. Do not fabricate the restored database schema. Inspect it.
3. Do not build trading strategies, exchange execution, or Pine Script before their PRD phase.
4. Do not let SQL escape the data package.
5. Do not put production logic in notebooks.
6. Do not require a hypothesis for discovery runs.
7. Do not use outcome labels to form discovery clusters.
8. Do not claim causal “institutional” behaviour from OHLCV.
9. Do not load the full 20-million-row dataset into Python dictionaries or a single in-memory frame.
10. Do not add heavy dependencies such as PyTorch until a measured need exists.
11. Do not leave placeholder scripts or fake reports.
12. Do not create empty architecture folders for future phases.
13. Run tests and static checks after every coherent change.
14. Keep changes small enough to review, but complete enough to work.

## Working method

Implement one PRD phase at a time.

For each phase:

1. inspect current files and tests;
2. state the concrete implementation plan;
3. implement production code;
4. add/update tests;
5. run relevant commands;
6. update documentation and `docs/IMPLEMENTATION_STATUS.md`;
7. report files changed, commands run, results, assumptions, and blockers;
8. stop at the phase exit gate unless explicitly instructed to continue.

Never report success without terminal evidence.

## First execution scope: Phase 0 only

Implement **Phase 0 — Correct the foundation** now.

### Task 1 — Add the PRD and status document

- Save the supplied PRD as `docs/PRD.md`.
- Create `docs/IMPLEMENTATION_STATUS.md` with phases, status, completed work, blockers, and next action.
- Update README links without rewriting the entire project philosophy unnecessarily.

### Task 2 — Correct package layout

Migrate:

```text
src/<packages>
```

into:

```text
src/market_structure_lab/<packages>
```

Update:

- all imports;
- `pyproject.toml` Hatch package configuration;
- tests;
- scripts.

Do not retain a Python package named `src`.

### Task 3 — Fix Docker database bootstrap

Inspect `docker-compose.yml` and `docker/postgres/init/restore_dump.sh`.

Implement direct root-level mounts so the Postgres entrypoint sees the script:

- `callscore.dump` mounted at `/docker-entrypoint-initdb.d/callscore.dump:ro`;
- restore script mounted at `/docker-entrypoint-initdb.d/10_restore_dump.sh:ro`.

Update the restore script to:

- use `set -euo pipefail`;
- confirm the dump exists;
- run `pg_restore --exit-on-error --no-owner --no-acl` into `$POSTGRES_DB`;
- avoid `--clean` on a fresh database;
- print clear messages.

Update health checks to use container environment variables with escaped Compose syntax.

Do not destroy an existing local database volume automatically. Document the exact command the operator must run when a fresh restore is required.

### Task 4 — Build real configuration

Create a small typed settings module using environment variables and `pydantic-settings` only if it is already an installed dependency; otherwise use a small dataclass and `python-dotenv`.

Requirements:

- never print passwords;
- provide a redacted database URL for logs;
- centralise DB/table mapping settings;
- no global mutable settings singleton.

### Task 5 — Replace fake database inspection

Replace the current manufactured inspection report with a real read-only database inspector.

It must:

- connect to configured PostgreSQL;
- list schemas, tables, and columns;
- identify candidate OHLCV tables based on column names;
- report row counts, symbols, timeframes, min/max timestamps;
- report duplicate-key counts where a candidate mapping is known;
- report invalid OHLC relationships, nulls, and timestamp gaps;
- support `--quick` and `--full` modes;
- print a useful human report and optionally write JSON;
- never expose credentials.

Do not hard-code the table name `ohlcv` until the restored schema proves it.

### Task 6 — Remove or implement placeholders

Review:

- `scripts/restore_db.py`;
- `scripts/build_profiles.py`;
- `scripts/build_features.py`;
- `scripts/benchmark.py`.

Delete placeholders that add no value, or convert them into real thin CLI wrappers only when the underlying implementation exists. Do not leave scripts that merely print “placeholder.”

### Task 7 — Preserve existing functionality

Migrate and retain the existing deterministic profile, auction, structure, transition, significance, screening, experiment, and dataset tests unless the PRD requires behaviour changes.

Do not begin the Phase 2 profile refactor in Phase 0 beyond changes required for packaging and correctness.

### Task 8 — Verification

Run, at minimum:

```powershell
uv sync
uv run pytest -q
uv run ruff check .
uv run mypy src

docker compose config
```

When Docker is available, validate a fresh test restore without deleting the operator’s existing data directory. Use a temporary Compose project or temporary volume/path. If the dump restore is too large for the current run, at least validate script discovery and `pg_restore -l`, and document the exact unexecuted step.

## Phase 0 acceptance criteria

Phase 0 is complete only when:

- imports use `market_structure_lab.*`;
- the package builds correctly with Hatch/uv;
- existing unit tests pass;
- Ruff passes;
- Mypy either passes or has a short documented list of justified exclusions;
- Compose configuration validates;
- the restore script is mounted where the Postgres entrypoint will execute it;
- the inspector queries the real DB rather than emitting fake rows;
- no password is printed;
- no placeholder scripts remain;
- README and implementation status reflect reality.

## What to output after implementation

Provide:

1. concise summary;
2. exact files changed;
3. important design decisions;
4. commands run and outputs/status;
5. blockers or unverified steps;
6. Phase 0 acceptance checklist;
7. the single next recommended action: Phase 1 data truth.

Do not proceed to Phase 1 in the same run unless explicitly instructed.