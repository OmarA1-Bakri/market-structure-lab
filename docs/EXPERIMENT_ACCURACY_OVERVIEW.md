# Experiment Accuracy and Reliability Overview

## Document control

- **Status:** Canonical review baseline
- **Assessment date:** 2026-07-17
- **Scope:** Current data evidence, reconciliation state, experiment artifacts, discovery methodology, and the evidentiary basis for future experiment reliability
- **Exclusions:** No final-holdout outcomes were inspected; no strategy, backtest, or live-trading claim is made
- **Governing sources:** `docs/PRD.md`, `AGENTS.md`, checksum-bearing artifacts under `data/exports/`, and the current implementation/tests

## Canonical verdict

**The repository cannot currently support a defensible numeric estimate of the accuracy of future market experiments.**

There are no completed real-market discovery trials, no completed Phase 5 validation trials, no cost-adjusted outcome results, and no empirical trial history from which to estimate a discovery or promotion rate. `docs/RESEARCH_LOG.md` contains no completed research session, `docs/HYPOTHESES.md` is an unpopulated template, and the only committed `DR-*` evidence is a synthetic golden replay. The current empirical count is therefore `n = 0`; any single accuracy percentage would be invented.

The correct object is not classification accuracy. Future experiments must report a **reliability vector**:

1. data integrity and usable coverage;
2. deterministic replay and provenance;
3. detector and motif stability;
4. descriptive transition uncertainty;
5. untouched predictive validation;
6. cost-adjusted expectancy and robustness;
7. trial-count and multiple-testing control.

A candidate is credible only when every applicable gate passes. Strong software replay cannot substitute for empirical validation, and a stable behaviour is not an edge.

## What has actually been demonstrated

### 1. Data integrity: strong row validity, conditional research fitness

The immutable dump contains `35,748,117` one-minute rows across 25 symbols. Direct inspection records zero duplicate canonical keys, missing required candle fields, malformed numeric values, invalid OHLC relationships, negative values, or off-minute timestamps (`docs/DATA_VIABILITY.md:27-42`).

That strength is limited by coverage and provenance:

- raw per-symbol-envelope coverage is `60.47%`, with `23,372,460` missing minutes in `19,192` gap runs (`docs/DATA_VIABILITY.md:53-90`);
- `quote_volume` and `trades` are absent in `47.668%` of source rows, so features using them can inherit source-era bias (`docs/DATA_VIABILITY.md:44-47`);
- `501,913` rows have zero volume and require explicit treatment and sensitivity analysis (`docs/DATA_VIABILITY.md:39`);
- nine symbols remain source-conflicted and quarantined: AR, AVAX, BTC, ETH, FET, PENDLE, RENDER, SOL, and SUI (`docs/DATA_VIABILITY.md:113-138`).

The latest checksum-bearing freshness report, frozen at `2026-07-17T00:15:00Z`, conserves `7,738,974` missing minutes after adding `11,580` rows. Its terminal distribution is four recovered symbols, 12 provider-absent symbols, and nine source conflicts. This is an auditable limitation, not a failure to be hidden.

### 2. Reconciliation: promising evidence, not a completed full-history result

The promoted public RR-000002 summary reports `557,075` audited rows, `481,332` replacement rows, and `3,697` Binance corrections. This proves that existing-row value disagreement is material enough to affect research, not merely a missing-row problem.

The broader RR-000008 plan contains `1,583` work units and was still advancing during this review. Its partial totals must not be treated as final or promotable. Under the reconciliation design, every unit must reach a terminal state and the promoted hashes, corrected keys, and eligible contiguous intervals must verify before use.

### 3. Discovery software: restored deterministic fixture replay, not market evidence

The committed Phase 4 fixture contains 16 discovery rows and eight development rows (`docs/DISCOVERY_MVP.md:100-105`). It exercises the real split, matrix, PCA, K-means, stability, motif, transition, behaviour, evidence, and publication APIs. The original review reproduced three cross-platform golden failures caused by last-bit SciPy/LAPACK variation entering identity-bearing PCA projections. The repair introduced an explicit `deterministic-pca-v2` canonical numeric boundary, fail-closed handling for non-identifiable singular subspaces, and a reviewed fixture migration (`docs/benchmarks/phase4-golden-drift-diagnosis.md`).

Fresh post-repair verification ran the 86-test discovery, behaviour, evidence, and golden suite on both Linux and Windows: **86 passed on each platform**. Ruff and Mypy also passed for the repaired discovery surface. This restores the software-fixture replay contract; it does not create market evidence. The fixture is not sampled market evidence, does not prove that its behaviours exist in cryptocurrency data, and is not evidence of profitability (`docs/DISCOVERY_MVP.md:137-148`; `docs/IMPLEMENTATION_STATUS.md:393-395`).

### 4. Real experiment evidence: absent

No real Phase 3 feature/event publication feeding discovery, real `DR-*` bundle, completed research-log entry, populated hypothesis record, Phase 5 outcome attachment, or cost-aware validation artifact exists. The only visible candle snapshot is a one-day, one-symbol XRPUSDT snapshot with 1,440 rows and a dirty commit identity (`data/exports/snapshots/dataset_version=phase0-xrpusdt-20180505-v1/manifest.json:1-23`).

Consequently:

- real detector stability is unknown;
- effective independent event count is unknown;
- transition support and interval width on market data are unknown;
- untouched temporal and asset-holdout performance is unknown;
- net expectancy, cost stress, deflated Sharpe, and backtest-overfitting probability are unknown.

## Current reliability assessment

| Dimension | Current assessment | Confidence | Basis |
|---|---|---:|---|
| Deterministic software replay | Green for the synthetic fixture on Linux and Windows | High | Fresh post-repair suite: 86 passed per platform; explicit `deterministic-pca-v2` identity |
| Required-field candle validity | Strong for inspected source rows | High | Zero duplicates/null-required/invalid-OHLC/off-grid rows |
| Full-universe temporal coverage | Weak and uneven | High | 7.739M minutes remain absent; 9 source conflicts; 12 provider-absent statuses |
| Existing-row venue reconciliation | In progress | High | RR-000008 is nonterminal; RR-000002 found material corrections |
| Outcome-blind access boundary | Structurally strong | High | Holdout rows are rejected and future/outcome feature classes are blocked |
| End-to-end normalization provenance | Insufficiently bound | High | Discovery config/input identity does not require the fitted normalizer or feature-publication hash |
| Real detector/cluster stability | Unknown | High | No real discovery run exists |
| Motif stability and boundary safety | Insufficient | High | Motifs are not stability-tested and orchestration lacks explicit time/session-contiguity checks |
| Transition description | Structurally safe, statistically descriptive | High | The sole public path dwell-compresses, preserves boundary evidence, and exposes no adjacent-binomial/BH inference API |
| Predictive validity after costs | Not measured | High | Phase 5 and strategy validation have not run |
| Future experiment success rate | Not estimable (`n=0`) | High | No completed real research trials |

## Ranked methodological blockers

1. **No empirical experiment exists.** There is nothing from which to estimate predictive accuracy, false-discovery rate, or promotion yield.
2. **Full-history reconciliation is unfinished.** A research snapshot frozen before RR-000008 completes and is deliberately promoted could preserve known row-value errors.
3. **Normalization provenance is not end-to-end bound.** `DiscoveryRunConfig` carries snapshot, registry, commit, and lock hashes, but not a required train-fitted normalizer artifact or feature-publication manifest (`src/market_structure_lab/discovery/runs.py:67-86,403-447`; `src/market_structure_lab/discovery/matrix.py:48-60`).
4. **Trial accounting is incomplete.** Discovery manifests have only `completed` and `rejected_unstable`; pre-publication failures/abandonment are not durable. The generic experiment writer can overwrite an existing run ID and does not require the full PRD provenance/decision record (`src/market_structure_lab/discovery/runs.py`; `src/market_structure_lab/experiments/artifacts.py`).
5. **The fixture does not validate meaningful stability thresholds.** Its accepted run uses maximally permissive ARI/JS/coverage thresholds, so it proves policy plumbing rather than empirical stability (`tests/fixtures/phase4/discovery_run_v1.json`).
6. **Motif evidence is weaker than cluster evidence.** Motifs are computed once, only on the first selected feature, and are not tested across seeds, subsamples, adjacent periods, assets, or parameter perturbations. Grouping lacks explicit timestamp/session-contiguity checks after null-row removal (`src/market_structure_lab/discovery/runs.py`).
7. **Transition uncertainty defaults are descriptive only.** The canonical path correctly dwell-compresses and boundary-checks, but orchestration pins just 100 bootstrap iterations and block length two without a dependence diagnostic or sensitivity analysis (`src/market_structure_lab/discovery/runs.py`).
8. **Dataset hashes are asserted, not linked through the full derivation chain.** Exact supplied rows are hashed, but the discovery layer does not prove that they came from the claimed snapshot, feature publication, normalizer, and commit.
9. **Semantic leakage remains a developer-declared risk.** Structural guards are good, but an improperly implemented future-derived feature with an innocuous name and incorrect leakage declaration could still pass.

## Canonical gates for future accuracy claims

These gates define what must be reported. Thresholds must be frozen before the relevant data are evaluated; they must not be tuned after viewing the final holdout.

### Gate A — Data fitness

Required evidence:

- immutable snapshot and content hashes;
- exact symbol/timeframe/date universe and contiguous segments;
- no duplicate, invalid, unordered, or unexplained rows;
- explicit missing-minute and zero-volume policy;
- no sequence crossing a symbol, session, segment, or material gap;
- completed and promoted reconciliation provenance for every admitted interval;
- optional-field availability reported by symbol and era.

### Gate B — Outcome-blind detector reliability

Required metrics:

- byte-identical replay;
- seed and subsample adjusted Rand indices;
- adjacent-period distribution and centroid/dispersion drift;
- per-cluster asset and regime coverage;
- nearby cluster-count and parameter perturbations;
- motif recurrence/stability across the same axes;
- effective event counts after dwell compression;
- frozen rejection thresholds and a recorded result for every attempted configuration.

The numeric values used in unit tests are not automatically research thresholds. Real thresholds require explicit preregistration and justification before the first market run.

### Gate C — Descriptive transition reliability

Required evidence:

- event-level or dwell-compressed units;
- configurable horizons;
- no boundary crossings;
- support and effective-support counts;
- block-length selection/sensitivity;
- sufficiently resolved bootstrap intervals with interval width reported;
- symbol, session, regime, and period breakdowns;
- no promotion from a legacy adjacent-binomial p-value.

Transition probability is descriptive recurrence, not return prediction.

### Gate D — Phase 5 predictive validation

Required evidence:

- detector, candidate, outcomes, horizons, metrics, and promotion criteria frozen before outcomes attach;
- chronological walk-forward evaluation;
- purging and embargo for overlapping labels;
- untouched temporal holdout and asset holdout where feasible;
- block-bootstrap confidence intervals on event-level effects;
- negative, shuffled, and placebo controls;
- all trials retained with multiplicity correction;
- final-holdout net-expectancy interval lower bound above zero under the predeclared rule.

A minimum event count must be preregistered from the intended uncertainty/power calculation, not selected after seeing results.

### Gate E — Cost and execution robustness

Before promotion to an edge, report net expectancy after conservative fees, spread, slippage, funding, latency, fill probability, missed fills, turnover, and capacity. The result must remain positive under a preregistered adverse-cost stress and parameter perturbation. Gross backtest performance is not an edge.

### Gate F — Experiment-program calibration

Record every successful, rejected, inconclusive, failed, and abandoned trial. After `n` real preregistered validation trials and `s` promotions, report `s/n` with an exact or Beta-binomial uncertainty interval. Until then, the system has no empirical estimate of its own experiment yield.

## Decision rule

Do not publish a single “accuracy” percentage for the laboratory. Publish the reliability vector and each gate's evidence. The only currently defensible global status is:

> **Synthetic golden replay restored; source data conditionally viable; full-history reconciliation incomplete; real detector stability unmeasured; predictive and cost-adjusted accuracy not yet estimable.**

Under the repository phase gate, this review does not authorize a real Phase 4 or Phase 5 experiment. Complete the approved foundation/reconciliation work, freeze a deliberate immutable market snapshot and Phase 3 publication, and obtain explicit phase approval before advancing.

## Terse review findings

- `docs/RESEARCH_LOG.md:20-22: 🔴 blocker: no completed real research trial exists. Do not report experiment accuracy until trial artifacts exist.`
- `docs/benchmarks/phase4-golden-drift-diagnosis.md: 🟢 closed: cross-platform PCA identity drift is versioned, fail-closed, and verified by 86 passing tests on both Linux and Windows.`
- `data/exports/reconciliation/RR-000008.run.json:work_units: 🟡 risk: full-history audit is nonterminal. Block snapshot eligibility until every unit and promotion hash verify.`
- `src/market_structure_lab/discovery/runs.py:67-86: 🔴 risk: run identity omits required normalizer/publication provenance. Bind train-fitted normalizer and feature manifest hashes.`
- `src/market_structure_lab/transitions/__init__.py: 🟢 closed: the raw-adjacent binomial/BH surface is deleted; the canonical API requires boundary-aware observations and publishes dwell-run support plus boundary evidence.`
- `src/market_structure_lab/experiments/artifacts.py:44-85: 🔴 risk: repeated run IDs overwrite evidence and failed trials are lost. Make trial records immutable and terminal-status complete.`
- `tests/fixtures/phase4/discovery_run_v1.json:350-356: 🟡 risk: accepted fixture thresholds cannot reject instability. Label it policy-plumbing evidence, not stability evidence.`
- `src/market_structure_lab/discovery/runs.py:483-507: 🔴 bug: motif groups can bridge dropped rows, gaps, or sessions. Enforce explicit contiguity and add multi-axis motif stability.`
- `src/market_structure_lab/discovery/runs.py:54-56: 🟡 risk: 100 bootstraps with fixed block length 2 are under-justified for inference. Make dependence-calibrated settings part of the frozen policy.`
- `src/market_structure_lab/data/dashboard_evidence.py: 🟢 closed: dashboard freshness is generated from one verified plan/report bundle with committed-publication rollback protection and an explicit evidence cutoff.`
- `dashboard/src/components/reconciliation-status-panel.tsx: 🟢 closed: reconciliation is labelled as bounded audited keys or partial/complete-unpromoted work-unit evidence; no promotion or research eligibility is inferred without a receipt.`

## Remediation checkpoint log

| Task | Commit | Remote verification | Evidence |
|---|---|---|---|
| Restore cross-platform golden replay | `0db02354ff344e441864afbe093f5477625ce13f` | `origin/agent/research-lab-foundation` matched local `HEAD` on 2026-07-17 | 86 targeted tests passed on Linux and Windows; Ruff; Mypy discovery; independent specification and code-quality approvals |
| Publish a source-backed experiment evidence contract | `e83a4c3d4bb27a5486ea5b79a8875f803dfbb5d2` | `origin/agent/research-lab-foundation` matched local `HEAD` on 2026-07-17 | 35 focused Python tests; Ruff; Mypy; Node contract verification; dashboard typecheck, lint, and production build; three-page Chromium visual verdict 97/100; independent specification and code-quality approvals |
| Retire unsafe adjacent-minute transition inference | `acead25c7afc0d619d1f0c76880ca001e0928f08` | `origin/agent/research-lab-foundation` matched local `HEAD` on 2026-07-17 | 141 leader-selected tests; Ruff; Mypy across 74 source files; Node contract verification; dashboard typecheck, lint, and production build; Chromium visual verdict 98/100; independent specification and code-quality approvals |
