# Experiment Accuracy and Reliability Overview

## Document control

- **Status:** Canonical review baseline
- **Assessment date:** 2026-07-18
- **Scope:** Current data evidence, reconciliation state, experiment artifacts, discovery methodology, and the evidentiary basis for future experiment reliability
- **Exclusions:** No final-holdout outcomes were inspected; no strategy, backtest, or live-trading claim is made
- **Governing sources:** `docs/PRD.md`, `AGENTS.md`, checksum-bearing artifacts under `data/exports/`, and the current implementation/tests

## Canonical verdict

**The repository cannot currently support a defensible numeric estimate of the accuracy of future market experiments.**

There are no completed real-market discovery trials, no completed Phase 5 validation trials, no cost-adjusted outcome results, and no empirical trial history from which to estimate a discovery or promotion rate. `docs/RESEARCH_LOG.md` contains no completed research session, `docs/HYPOTHESES.md` is an unpopulated template, and the only committed `DR-*` evidence is a synthetic golden replay. The immutable `trial-receipt-v2` ledger is now implemented, but the verified repository ledger is empty. The current empirical count is therefore `n = 0`; any single accuracy percentage would be invented.

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

### 2. Reconciliation: full-history evidence complete and explicitly promoted

The promoted public RR-000002 summary reports `557,075` audited rows, `481,332` replacement rows, and `3,697` Binance corrections. This proves that existing-row value disagreement is material enough to affect research, not merely a missing-row problem.

RR-000008 is now terminal across all `1,583` frozen work units: `1,359` completed and `224`
source-unavailable. The verified ledger contains `68,474,492` audited keys and `31,894,927`
replacement rows. Its bounded read-only promotion preflight verified the candidate replacement
logical SHA-256, prepared `285` eligible coverage intervals, and retained `261` explicit
residual-unavailable intervals containing `841,509` minutes.

The read-only preflight closed the audit before mutation. After explicit approval, RR-000008 was
atomically promoted with one immutable promotion row and `285` verified-coverage intervals. The
active reconciled view now binds canonical logical SHA-256
`8dd1af045a92d53b7a8e898764e9f9a90bc456373c16b7c9ac2670ee2c47608c` and publishes
`67,632,983` canonical rows: `35,738,056` verified dump matches, `31,884,870` fills, and `10,057`
corrections. Its checksum-verified receipt is
`data/exports/reconciliation/promotions/run_id=RR-000008/receipt.json`. This establishes
reconciliation provenance; it does not retroactively make an old snapshot eligible or constitute
market evidence. A new research snapshot still requires its own frozen provenance chain.

### 3. Discovery software: restored deterministic fixture replay, not market evidence

The committed Phase 4 fixture contains 16 discovery rows and eight development rows (`docs/DISCOVERY_MVP.md:100-105`). It exercises the real split, matrix, PCA, K-means, stability, motif, transition, behaviour, evidence, and publication APIs. The original review reproduced three cross-platform golden failures caused by last-bit SciPy/LAPACK variation entering identity-bearing PCA projections. The repair introduced an explicit `deterministic-pca-v2` canonical numeric boundary, fail-closed handling for non-identifiable singular subspaces, and a reviewed fixture migration (`docs/benchmarks/phase4-golden-drift-diagnosis.md`).

Fresh post-repair verification ran the 86-test discovery, behaviour, evidence, and golden suite on both Linux and Windows: **86 passed on each platform**. Ruff and Mypy also passed for the repaired discovery surface. This restores the software-fixture replay contract; it does not create market evidence. The fixture is not sampled market evidence, does not prove that its behaviours exist in cryptocurrency data, and is not evidence of profitability (`docs/DISCOVERY_MVP.md:137-148`; `docs/IMPLEMENTATION_STATUS.md:393-395`).

### 4. Real experiment evidence: absent

No real Phase 3 feature/event publication feeding discovery, real `DR-*` bundle, completed research-log entry, populated hypothesis record, Phase 5 outcome attachment, or cost-aware validation artifact exists. The only visible candle snapshot is a one-day, one-symbol XRPUSDT snapshot with 1,440 rows and a dirty commit identity (`data/exports/snapshots/dataset_version=phase0-xrpusdt-20180505-v1/manifest.json:1-23`).

The absence is now machine-readable rather than inferred from optimistic UI defaults. The dashboard
generator verifies the canonical ledger and reports exact mode/status counts: all four modes and all
five terminal statuses are zero. It rejects malformed receipts, checksum drift, extra files, and
interrupted staging, and it never counts the synthetic Phase 4 fixture as a real trial. Required
snapshot, feature-publication, registry, and normalizer identities are receipt-bound assertions;
this task does not prove their complete derivation chain.

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
| Existing-row venue reconciliation | Complete, promoted | High | RR-000008 is active, receipt-bound, replay-idempotent, and publishes 67,632,983 verified canonical rows |
| Outcome-blind access boundary | Structurally strong | High | Holdout rows are rejected and future/outcome feature classes are blocked |
| End-to-end normalization provenance | Required identities, derivation still unverified | High | Canonical receipts require snapshot, feature-publication, registry, and normalizer identities; source-backed chain verification remains a post-Phase-0 Phase 4 hardening gate |
| Immutable trial accounting | Implemented but empirically empty | High | Canonical terminal receipts are atomic, idempotent, fail closed, and counted by verified mode/status; repository count is `n=0` |
| Real detector/cluster stability | Unknown | High | No real discovery run exists |
| Motif stability and boundary safety | Insufficient | High | Motifs are not stability-tested and orchestration lacks explicit time/session-contiguity checks |
| Transition description | Structurally safe, statistically descriptive | High | The sole public path dwell-compresses, preserves boundary evidence, and exposes no adjacent-binomial/BH inference API |
| Predictive validity after costs | Not measured | High | Phase 5 and strategy validation have not run |
| Future experiment success rate | Not estimable (`n=0`) | High | No completed real research trials |

## Ranked methodological blockers

1. **No empirical experiment exists.** There is nothing from which to estimate predictive accuracy, false-discovery rate, or promotion yield.
2. **A new research snapshot is still required.** RR-000008 provenance is established, but any
   snapshot frozen before promotion may preserve known row-value errors and cannot be relabelled.
3. **Provenance derivation is not end-to-end verified.** Canonical receipts now require snapshot,
   feature-publication, registry, and normalizer identities, but Task 5 verifies the asserted
   identities and published bytes rather than independently reconstructing their complete
   derivation chain (`src/market_structure_lab/discovery/runs.py`;
   `src/market_structure_lab/experiments/artifacts.py`).
4. **The fixture does not validate meaningful stability thresholds.** Its accepted run uses maximally permissive ARI/JS/coverage thresholds, so it proves policy plumbing rather than empirical stability (`tests/fixtures/phase4/discovery_run_v1.json`).
5. **Motif evidence is weaker than cluster evidence.** Motifs are computed once, only on the first selected feature, and are not tested across seeds, subsamples, adjacent periods, assets, or parameter perturbations. Grouping lacks explicit timestamp/session-contiguity checks after null-row removal (`src/market_structure_lab/discovery/runs.py`).
6. **Transition uncertainty defaults are descriptive only.** The canonical path correctly dwell-compresses and boundary-checks, but orchestration pins just 100 bootstrap iterations and block length two without a dependence diagnostic or sensitivity analysis (`src/market_structure_lab/discovery/runs.py`).
7. **Required hashes are asserted, not yet linked through the full derivation chain.** Receipt
   verification prevents mutation or omission, but the discovery layer does not yet independently
   prove that supplied rows came from the claimed snapshot, feature publication, normalizer, and
   commit.
8. **Semantic leakage remains a developer-declared risk.** Structural guards are good, but an improperly implemented future-derived feature with an innocuous name and incorrect leakage declaration could still pass.

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

> **Synthetic golden replay restored; source data conditionally viable; full-history reconciliation promoted and receipt-bound; real detector stability unmeasured; predictive and cost-adjusted accuracy not yet estimable.**

The explicit RR-000008 promotion approval has been consumed and Phase 0 is closed. This review does
not authorize Phase 4 hardening, a real discovery run, or Phase 5 validation. Later phases remain
separately approval-gated.

## Terse review findings

- `docs/RESEARCH_LOG.md:20-22: 🔴 blocker: no completed real research trial exists. Do not report experiment accuracy until trial artifacts exist.`
- `docs/benchmarks/phase4-golden-drift-diagnosis.md: 🟢 closed: cross-platform PCA identity drift is versioned, fail-closed, and verified by 86 passing tests on both Linux and Windows.`
- `data/exports/reconciliation/promotions/run_id=RR-000008/receipt.json: 🟢 closed: all 1,583 units, 285 coverage intervals, candidate hashes, active view, and idempotent replay verify; a new research snapshot remains separately gated.`
- `src/market_structure_lab/discovery/runs.py: 🟡 risk: receipts require normalizer/publication identities, but the full derivation chain is not independently verified.`
- `src/market_structure_lab/transitions/__init__.py: 🟢 closed: the raw-adjacent binomial/BH surface is deleted; the canonical API requires boundary-aware observations and publishes dwell-run support plus boundary evidence.`
- `src/market_structure_lab/experiments/artifacts.py: 🟢 closed: canonical terminal receipts are atomic, checksum-verified, immutable, idempotent, and retain failures/abandonment without swallowing the original exception.`
- `tests/fixtures/phase4/discovery_run_v1.json:350-356: 🟡 risk: accepted fixture thresholds cannot reject instability. Label it policy-plumbing evidence, not stability evidence.`
- `src/market_structure_lab/discovery/runs.py:_motif_payload: 🔴 bug: motif groups can bridge dropped rows, gaps, or sessions. Enforce explicit contiguity and add multi-axis motif stability.`
- `src/market_structure_lab/discovery/runs.py:54-56: 🟡 risk: 100 bootstraps with fixed block length 2 are under-justified for inference. Make dependence-calibrated settings part of the frozen policy.`
- `src/market_structure_lab/data/dashboard_evidence.py: 🟢 closed: dashboard freshness is generated from one verified plan/report bundle with committed-publication rollback protection and an explicit evidence cutoff.`
- `dashboard/src/components/reconciliation-status-panel.tsx: 🟢 closed: reconciliation promotion is displayed only from a checksum-verified receipt; snapshot and experiment eligibility remain explicit separate gates.`

## Remediation checkpoint log

| Task | Commit | Remote verification | Evidence |
|---|---|---|---|
| Restore cross-platform golden replay | `0db02354ff344e441864afbe093f5477625ce13f` | `origin/agent/research-lab-foundation` matched local `HEAD` on 2026-07-17 | 86 targeted tests passed on Linux and Windows; Ruff; Mypy discovery; independent specification and code-quality approvals |
| Publish a source-backed experiment evidence contract | `e83a4c3d4bb27a5486ea5b79a8875f803dfbb5d2` | `origin/agent/research-lab-foundation` matched local `HEAD` on 2026-07-17 | 35 focused Python tests; Ruff; Mypy; Node contract verification; dashboard typecheck, lint, and production build; three-page Chromium visual verdict 97/100; independent specification and code-quality approvals |
| Retire unsafe adjacent-minute transition inference | `acead25c7afc0d619d1f0c76880ca001e0928f08` | `origin/agent/research-lab-foundation` matched local `HEAD` on 2026-07-17 | 141 leader-selected tests; Ruff; Mypy across 74 source files; Node contract verification; dashboard typecheck, lint, and production build; Chromium visual verdict 98/100; independent specification and code-quality approvals |
| Make experiment evidence reconstructible before empirical research | `eddf3da3b71c808b142373c31a22952009a23d8c` | `origin/agent/research-lab-foundation` matched local `HEAD` on 2026-07-17 | 84 focused Python tests; Ruff; Mypy across 75 source files; Node evidence and runtime mutation verification; dashboard typecheck, lint, and production build; two-page Chromium visual verdict 98/100; independent specification and code-quality approvals |
| Restore the repository-wide Phase 0 format gate | `fdfa423173e2a6cb38e40a28c23216b2ecbadd35` | `origin/agent/research-lab-foundation` matched local `HEAD` on 2026-07-17 | Clean Windows `uv sync --locked`; Ruff format/check across 136 files; 844 tests passed and 5 skipped; Mypy across 75 source files; package build; Docker Compose config; diff check |
| Make Phase 0 inputs fail closed before research reads | `b12ca2968a4b553fc9b23651c0578c9dccaedd86` | `origin/agent/research-lab-foundation` matched local `HEAD` on 2026-07-17 | 854 tests passed and 5 skipped; Ruff format/check across 136 files; Mypy across 75 source files; package build; Compose validation with non-secret injected values; independent specification and code-quality approvals |
| Bound verified Binance archive duplicate tracking | `4fdb2bff39f87d76d49e9cca3d09ef68238c0bca` | `origin/agent/research-lab-foundation` matched the implementation commit on 2026-07-17 | 54 source, sync, reconciliation, publication, manifest, and CLI tests; Ruff format/check; Mypy; 50x archive-size memory-scaling regression; independent code-review approval; the live frozen RR-000008 recovery remains separate and must execute from its recorded `52eb43276c10e9479d03b5469d4912046728b739` commit |
| Make reconciliation promotion fail closed on frozen evidence | `6137df6bfe7a1a2ac6805a9c451bae09767eca31` | `origin/agent/research-lab-foundation` matched local `HEAD` on 2026-07-17 | 886 tests passed and 5 skipped; Ruff format/check across 138 files; Mypy across 76 source files; package build; Compose validation with non-secret injected values; exact publication-set, bounded preflight, replacement-integrity, and apply-path regressions; independent specification and code-quality approvals; PostgreSQL integration remained skipped because the test database was unavailable |
| Prevent reconciliation schema preparation from deadlocking publication | `a3a993cd765ed47d862b36fa5bc1372e666066ad` | `origin/agent/research-lab-foundation` matched local `HEAD` on 2026-07-18 | 902 tests passed and 6 skipped on the Windows-native verification path; Ruff format/check across 141 files; Mypy across 77 source files; lockfile and Compose validation; all migration callers share the fail-fast publication lock domain and redact database failures; independent specification and code-quality approvals; the isolated PostgreSQL lock regression remained skipped because `MSL_TEST_POSTGRES_URL` was unavailable |
| Keep per-unit reconciliation recovery within bounded memory | `b53239552b6de3f1b44c1a9e9258d38537d0d889` | `origin/agent/research-lab-foundation` matched local `HEAD` on 2026-07-18 | Test-first `(run_id, work_unit_id)` lookup migration; disposable PostgreSQL index-only query-plan verification; live metadata and plan verification; RR-000008 promotion count remained zero |
| Keep ledger verification aligned with comparable candle fields | `781a61df3da4f985706509cc6f2f75793d4e90a0` | `origin/agent/research-lab-foundation` matched local `HEAD` on 2026-07-18 | Exact-match regression for absent optional dump fields; 43 candle/publication/preflight tests; Ruff; Mypy; bounded full-ledger semantic scan found zero invalid rows; read-only preflight subsequently passed |
