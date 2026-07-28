# Phase 5 Bounded Validation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the smallest deterministic, cost-aware, baseline-first Phase 5 validation stack that can truthfully reject or mark inconclusive the frozen A, B, G, E, and D candidate families without opening the final holdout prematurely.

**Architecture:** Add a narrow `research` validation boundary rather than overloading discovery semantics. Deterministic aggregate publications feed immutable candidate signals; a separate outcome attacher enforces completed-bar cutoffs and next-bar entry; folds, statistics, costs, and receipts remain small typed functions composed by one thin vertical runner. One `VP-######` programme owns the frozen ledger and final-access state, while independent `VR-######` receipts own terminal evaluation evidence.

**Tech Stack:** Python 3.13, Polars, SciPy, dataclasses/enums/plain functions, existing canonical JSON/hash/bounded-I/O/atomic-publication primitives, Pytest, Ruff, Mypy, Hatch/uv. No new dependency or third-party backtesting framework.

---

## Authoritative inputs and boundaries

- Task 14 close-out: `5db2705a1cba37ee10d5eb3bd1fa470a5b628e3d` plus evidence follow-up `3482882c864f1471ec1dd682631544d0c404c542`.
- Task 15 reviewed planning checkpoint: `807ac28ac5616fb837c1ccea1e2bc47572ae3984` plus evidence follow-up `afce8e889aa645cd9e48e90631698b87866f287f`.
- This bounded implementation plan must be committed, pushed, and remotely verified before source
  implementation. Its exact tracked-document SHA-256 and remote checkpoint SHA become mandatory
  `ValidationProgrammeConfig`, VP receipt, CLI-preflight, and real-programme identities.
- Validation contract: `docs/superpowers/plans/2026-07-22-phase5-validation.md`.
- Test contract: `docs/superpowers/plans/2026-07-22-phase5-validation-test-spec.md`.
- Family order is immutable: A, B, G, E, D.
- BTCUSDT, ETHUSDT, and every unresolved source-conflict symbol remain excluded.
- Initial timeframes are 1h and 4h. A later 15m G/D branch requires a separate outcome-blind gate and a new programme identity.
- The final temporal/asset holdout is never iterated until the programme commits one atomic, every-and-only eligible batch. Empty eligibility performs no access attempt.
- The first bounded real programme in this plan is development-only and must record zero real final
  access. The final-access machinery is proved only with synthetic fixtures; real final access needs
  a separately checkpointed post-development eligibility decision and is outside this bounded work
  unit.
- Phase 5 stops before strategy construction, paper/live execution, portfolio allocation, exchange integration, or leverage.

## File structure

Create only files that receive working code in the task that introduces them:

```text
src/market_structure_lab/
  core/
    identity.py                # public domain-separated canonical JSON/hash utility
  data/
    aggregate_bars.py          # complete streaming 1m -> 15m/1h/4h aggregation
    aggregate_publication.py   # immutable aggregate publication identity and bytes
  research/
    __init__.py                # narrow exported Phase 5 API
    models.py                  # VP/VR, signal, interval, outcome, status, and budget types
    candidates.py              # exact outcome-blind A/B/G/E/D detectors and controls
    outcomes.py                # legal entry, complete path, signed return, MFE/MAE
    splits.py                  # common-grid folds, interval purge/embargo, holdout capability
    costs.py                   # explicit spot-cash base/stress/delay event costs
    statistics.py              # weekly vectors, bootstrap, Holm, power/support decisions
    controls.py                # baselines, placebos, shifts, shuffles, random controls
    robustness.py              # ex-asset/year, perturbation, exposure/regime gates
    receipts.py                # immutable VP/VR publication and one-time access state
    validation.py              # thin bounded vertical orchestration only
tests/
  test_identity.py
  test_aggregate_bars.py
  test_aggregate_publication.py
  test_research_models.py
  test_research_candidates.py
  test_research_outcomes.py
  test_research_splits.py
  test_research_costs.py
  test_research_statistics.py
  test_research_controls.py
  test_research_robustness.py
  test_research_receipts.py
  test_research_validation.py
```

Do not add all files at once. Each task below creates only its tested slice. Keep `experiments/artifacts.py` unchanged except for reuse of generic terminal-status concepts; its discovery-shaped `ExperimentConfig` is deliberately not reused for pure aggregate-bar validation.

### Task 1: Freeze programme identity, statuses, and work budgets

**Files:**
- Create: `src/market_structure_lab/core/identity.py`
- Modify: `src/market_structure_lab/core/__init__.py`
- Create: `src/market_structure_lab/research/__init__.py`
- Create: `src/market_structure_lab/research/models.py`
- Create: `tests/test_identity.py`
- Create: `tests/test_research_models.py`

- [ ] **Step 1: Write failing identity tests**

Test domain/schema/field-boundary separation and stable `VP-`/`VR-` identifiers:

```python
def test_hash_json_separates_domain_schema_and_field_boundaries() -> None:
    left = hash_json("programme-v1", {"a": "ab", "b": "c"})
    right = hash_json("programme-v1", {"a": "a", "b": "bc"})
    assert left != right
    assert left != hash_json("evaluation-v1", {"a": "ab", "b": "c"})

def test_validation_ids_are_content_addressed() -> None:
    assert programme_id({"families": ["A", "B", "G", "E", "D"]}).startswith("VP-")
    assert evaluation_id("VP-000001", {"role": "primary"}).startswith("VR-")
```

Also mutate schema version, UTC timestamp, integer/decimal representation, canonical row payload,
seed payload, holdout payload, baseline payload, random-control payload, and one serialized byte.
Every mutation must change or invalidate the corresponding identity; non-finite numbers reject.

- [ ] **Step 2: Run the tests and prove they fail**

Run: `uv run pytest -q tests/test_identity.py tests/test_research_models.py`

Expected: import/definition failures only; no production mutation has occurred.

- [ ] **Step 3: Implement canonical identity and typed contracts**

Implement public schema-versioned canonical JSON in `core.identity` with explicit finite-decimal,
UTC-timestamp, mapping-key, and sequence rules. Cross-module callers must not import private hash
helpers. Then implement validation dataclasses/enums for:

```python
class ExecutionStatus(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"
    ABANDONED = "abandoned"

class ScientificDecision(StrEnum):
    NOT_EVALUATED = "not_evaluated"
    REJECTED = "rejected"
    INCONCLUSIVE = "inconclusive"
    SUPPORTED_DEVELOPMENT = "supported_development"
    VALIDATED = "validated"
    PROMOTED = "promoted"

@dataclass(frozen=True, slots=True)
class ValidationWorkBudget:
    evaluation_count: int = 1_104
    core_count: int = 152
    primary_count: int = 64
    baseline_count: int = 192
    negative_control_count: int = 192
    robustness_count: int = 256
    perturbation_count: int = 184
    exposure_count: int = 64
    capacity_count: int = 64
    bootstrap_draws: int = 4_096
    max_source_rows: int = 25_000_000
    max_source_bytes: int = 8 * 1024 * 1024 * 1024
    max_aggregate_bars: int = 1_000_000
    max_symbols: int = 64
    max_ranges: int = 256
    max_candidates: int = 256
    max_events: int = 2_000_000
    max_outcomes: int = 2_000_000
    max_path_cells: int = 100_000_000
    max_outer_folds: int = 4
    max_inner_folds: int = 3
    max_bootstrap_cells: int = 262_144
    max_bootstrap_blocks: int = 64
    max_controls: int = 384
    max_placebos: int = 192
    max_artifacts: int = 20_000
    max_artifact_bytes: int = 64 * 1024 * 1024
    max_dashboard_bytes: int = 8 * 1024 * 1024
    max_final_batch_candidates: int = 64
```

Validate exact family order/counts, non-negative finite limits, `completed`/decision compatibility,
and `failed|abandoned -> not_evaluated`. Freeze an explicit 1,104-item slot roster and reject a
duplicate, missing, substituted, misclassified, reordered, wrong-family, wrong-role, wrong-timeframe,
wrong-horizon, or wrong-parameter slot even when the total remains 1,104. Bind Task 14, Task 15,
this remotely verified implementation-plan document/checkpoint, code, lockfile, dataset, cost,
control, split, roster, and budget identities in `ValidationProgrammeConfig`.

- [ ] **Step 4: Add exploding-iterable preflight tests and make them pass**

Prove every source-row/byte, aggregate-bar, symbol/range, candidate/trial, event/outcome,
path-cell, outer/inner-fold/evaluation, bootstrap-draw/cell/block, control/placebo/perturbation,
artefact-count/byte, dashboard-byte, and final-batch limit rejects before iterating or allocating.
Run the focused test files until green.

- [ ] **Step 5: Run static checks and checkpoint**

Run:

```bash
uv run ruff format --check src/market_structure_lab/core/identity.py src/market_structure_lab/research tests/test_identity.py tests/test_research_models.py
uv run ruff check src/market_structure_lab/core/identity.py src/market_structure_lab/research tests/test_identity.py tests/test_research_models.py
uv run mypy src/market_structure_lab/core/identity.py src/market_structure_lab/research
git diff --check
```

Create a Lore-compliant commit and push only after the focused tests pass.

### Task 2: Publish complete aggregate bars deterministically

**Files:**
- Create: `src/market_structure_lab/data/aggregate_bars.py`
- Create: `src/market_structure_lab/data/aggregate_publication.py`
- Create: `tests/test_aggregate_bars.py`
- Create: `tests/test_aggregate_publication.py`
- Modify: `src/market_structure_lab/data/__init__.py`

- [ ] **Step 1: Write failing aggregation tests**

Cover exact UTC `[open, close)` 15m/1h/4h arithmetic, source count/digest, segment identity, streaming-versus-one-batch equality, and pre-iteration budget rejection. Reject partial bars, gaps, duplicates, reordering, mixed symbols/timeframes/segments, and digest tampering; never fill a gap.

Mutate aggregate row schema, timestamp, numeric value, source ordering, parent identity, and one
serialized byte; every mutation must alter or invalidate row/publication identity.

- [ ] **Step 2: Prove the focused tests fail**

Run: `uv run pytest -q tests/test_aggregate_bars.py tests/test_aggregate_publication.py`

- [ ] **Step 3: Implement the minimal aggregate publication API**

Use `CanonicalAggregateBar` in `aggregate_bars.py` and `AggregatePublicationManifest` in
`aggregate_publication.py`, with row identities derived from ordered 1m source identities. Implement
a bounded iterator:

```python
def iter_complete_aggregate_bars(
    batches: Iterable[pl.DataFrame],
    *,
    target_timeframe: str,
    expected_source_sha256: str,
    budget: ValidationWorkBudget,
) -> Iterator[CanonicalAggregateBar]: ...
```

The first/last source minute must align exactly; every source minute is contiguous within a segment; partial target periods are rejected, not emitted.

- [ ] **Step 4: Publish twice and compare bytes**

Create two temporary clean roots in the test, publish identical synthetic rows through different chunk boundaries, and assert identical filenames, manifest bytes, row bytes, hashes, and IDs.

- [ ] **Step 5: Run focused/static checks and commit**

Run the owning tests plus Ruff/Mypy/diff-check, then create and push a distinct Lore checkpoint.

### Task 3: Implement exact outcome-blind A/B/G/E/D candidate detectors

**Files:**
- Create: `src/market_structure_lab/research/candidates.py`
- Create: `tests/test_research_candidates.py`
- Modify: `src/market_structure_lab/research/models.py`

- [ ] **Step 1: Write table-driven failing tests for every formula**

Lock:

- A SMA `(24h,72h)`, Donchian `24h/72h`, ATR24, and TSMOM `24h/72h` on 1h/4h.
- B profiles frozen at `t-2/t-1`, acceptance through completed `t`, and identical A entry clock.
- G three completed compression bars, current expansion, exact ATR-only and Donchian-only controls.
- E current completed volume strictly above prior-24h median, excluding current.
- D frozen Donchian level, breach, separate confirmation, `t+2` entry, failed-Donchian control, and exact prior-week pseudo-level.

Test ties, warm-up, gaps, segment reset, re-arm/transition, overlap suppression, long/short symmetry, no future fields, and all 184 adjacent-lookback identities.

- [ ] **Step 2: Prove detector tests fail**

Run: `uv run pytest -q tests/test_research_candidates.py`

- [ ] **Step 3: Implement immutable signals**

Use one causal type:

```python
@dataclass(frozen=True, slots=True)
class CandidateSignal:
    signal_id: str
    candidate_id: str
    family: Literal["A", "B", "G", "E", "D"]
    symbol: str
    timeframe: str
    direction: Literal[-1, 1]
    feature_start: datetime
    information_cutoff: datetime
    legal_entry: datetime
    source_publication_sha256: str
```

Keep detector inputs outcome-free. Candidate IDs bind exact parameter grid, A selector lineage, origin (`human_origin` when no Task 14 behaviour exists), comparator role, and source publication.

- [ ] **Step 4: Run focused/static checks and commit**

Run candidate tests, relevant profile/value-migration tests, Ruff, Mypy, diff-check, then commit/push.

### Task 4: Attach legal outcomes and path evidence

**Files:**
- Create: `src/market_structure_lab/research/outcomes.py`
- Create: `tests/test_research_outcomes.py`
- Modify: `src/market_structure_lab/research/models.py`

- [ ] **Step 1: Write failing boundary tests**

Cover bar-open timestamps, cutoff at bar close, ordinary next-bar entry, D post-confirmation entry, entry/exit minute inclusion, exit exclusion, wrong publication, missing entry/exit/path, discontinuity, and same-bar fantasy fill rejection.

- [ ] **Step 2: Lock side-aware formulas with unequal prices**

Assert:

```python
long_mfe = max(high / entry - 1.0)
long_mae = min(low / entry - 1.0)
short_mfe = max(1.0 - low / entry)
short_mae = min(1.0 - high / entry)
```

MFE/MAE exclude fees; net return receives cost evidence separately.

- [ ] **Step 3: Implement attachment over bounded, complete paths**

`attach_outcome(signal, aggregate_bars, minute_path, policy, budget)` must bind signal ID, aggregate/minute publication hashes, horizon, feature/label intervals, exact entry/exit prices, gross signed return, MFE/MAE, and path digest. It rejects before reading a final-holdout iterable without a capability token.

- [ ] **Step 4: Prove detector identity is invariant**

Attach two different synthetic outcome paths to the same frozen signal and assert the signal/candidate identities are unchanged.

- [ ] **Step 5: Run focused/static checks and commit**

Run outcomes plus candidates/aggregate tests, Ruff, Mypy, diff-check, then commit/push.

### Task 5: Freeze common-grid nested folds and holdout capability

**Files:**
- Create: `src/market_structure_lab/research/splits.py`
- Create: `tests/test_research_splits.py`

- [x] **Step 1: Write failing split and interval tests**

Test >=5 eligible symbols, >=730 common complete UTC days, source-conflict exclusion before row iteration, six equal chronological blocks, blocks 0–4 development, block 5 temporal holdout, common-grid symbol-order invariance, four expanding outer folds, four equal inner timestamp subblocks after frozen prefix remainder, and deterministic 20% asset holdout.

- [x] **Step 2: Test exact purge and embargo intersection edges**

Feature or label overlap purges. Candidate-horizon `[T_end,T_end+horizon)` embargo excludes intersections from current and subsequent expanding training/calibration. Bind every excluded identity.

- [x] **Step 3: Implement metadata-only final-holdout capability**

Freeze public metadata separately from an unforgeable factory-created access receipt:

```python
@dataclass(frozen=True, slots=True)
class FinalHoldoutBatch:
    programme_id: str
    candidate_ids: tuple[str, ...]
    batch_sha256: str

def freeze_final_batch(eligible: Sequence[CandidateEligibility]) -> FinalHoldoutBatch: ...

@dataclass(frozen=True, slots=True)
class VerifiedFinalAccess:
    programme_id: str
    batch_sha256: str
    access_receipt_sha256: str
    _factory_token: InitVar[object | None] = None
```

`FinalHoldoutBatch` is metadata only and grants no row access. Sort every-and-only eligible
candidates by A/B/G/E/D then ID. Empty batch cannot create an access receipt. Outcome attachment
accepts only `VerifiedFinalAccess` returned after atomic programme-scoped `access_attempted`; reject
direct construction, forged/stale/wrong-programme/wrong-hash/reordered/incomplete/extra batches
before final-row iteration. Held-out symbols/block 5 remains unused.

Mutate holdout schema, programme ID, timestamp bounds, candidate order, batch hash, and eligibility
receipt bytes; every mutation rejects or changes the identity.

- [x] **Step 4: Run focused/static checks and commit**

Run splits/outcomes tests, Ruff, Mypy, diff-check, then commit/push.

### Task 6: Implement costs, controls, bootstrap, multiplicity, and decisions

**Files:**
- Create: `src/market_structure_lab/research/costs.py`
- Create: `src/market_structure_lab/research/controls.py`
- Create: `src/market_structure_lab/research/statistics.py`
- Create: `tests/test_research_costs.py`
- Create: `tests/test_research_controls.py`
- Create: `tests/test_research_statistics.py`

- [ ] **Step 1: Write failing cost-policy tests**

Lock explicit spot-cash funding `not_applicable_spot_v1`, side-aware entry/exit rates at unequal prices, entry-notional normalisation, base/doubled/delayed/fill/missed-zero scenarios, no zero-cost default, missing-policy `failed/not_evaluated`, and valid-policy missing event coverage `completed/inconclusive`.

- [ ] **Step 2: Write failing baseline/control tests**

Implement count/calendar/direction/horizon-matched naive, unconditional, and persistence A controls; B/E common opportunity selected-zero/complement/placebo; G exact ATR/Donchian controls; D exact failed-Donchian/prior-week controls; label shuffle, +1-week causal shift, and SHA scalar random feature. Matching shortage is inconclusive.

For baseline, seed, and random-control identities, test schema/domain/timestamp/numeric/field/tamper
mutations as well as output-independent deterministic replay.

- [ ] **Step 3: Write failing statistical-policy tests**

Lock Monday UTC weekly means, exactly 4,096 seeded draws, indices 102/3993, corrected p-value, weekly sample SD, MDE tie identities, support formula, interval-width gate, family alpha 0.01, Holm with unevaluable `p=1`, A worst-of-three and B/G/E/D worst-of-two intersection-union decisions.

- [ ] **Step 4: Implement small pure functions**

Keep costs, matching, weekly aggregation, bootstrap, multiplicity, and decision classification independent. Do not build a generic backtester or estimator framework.

- [ ] **Step 5: Run focused/static checks and commit**

Run the three owning test modules plus outcomes/candidates, Ruff, Mypy, diff-check, then commit/push.

### Task 7: Implement robustness and exposure gates

**Files:**
- Create: `src/market_structure_lab/research/robustness.py`
- Create: `tests/test_research_robustness.py`

- [ ] **Step 1: Write failing robustness tests**

Lock ex-strongest-asset/year development-only ranking and ties, all adjacent-lookback perturbations, base/stress/delay lower-bound Booleans, positive 3-of-4 plus final fold, and one-asset/year/narrow-threshold kill rules.

- [ ] **Step 2: Write failing exposure/regime tests**

Test target-excluded market return/trend, >=3 non-target assets, prior-24h non-target volatility/turnover ranks, inner-training scaling, exact zero trend excluded from four regimes, two-week support, two-positive-regime rule, clearly negative rejection, and rank-deficient OLS inconclusive.

Lock exactly one intercept, lexicographic reference asset/year treatment coding, train-only scaling
and matrix-rank decisions, unseen validation levels encoded as all-zero non-reference indicators,
and the exact `one supported positive regime -> inconclusive` branch.

- [ ] **Step 3: Implement deterministic diagnostics**

Use SciPy/standard linear algebra already installed. Bind design-matrix rank, reference levels, scaling, exclusions, scores, and all Boolean gates. Diagnostics may reject or make evidence inconclusive; they may not silently promote.

Add typed `CapacityEvidence`, `PromotionEligibility`, and `RetirementRecord` contracts. Missing or
non-finite capacity preserves a supported/validated research decision but sets
`promotion_eligibility=inconclusive_capacity`; it can never promote. Bind retirement reason,
effective time, superseding identity, and invalidation evidence, and reject any tamper.

- [ ] **Step 4: Run focused/static checks and commit**

Run robustness/statistics tests, Ruff, Mypy, diff-check, then commit/push.

### Task 8: Publish immutable VP/VR receipts and atomic final access

**Files:**
- Create: `src/market_structure_lab/research/receipts.py`
- Create: `tests/test_research_receipts.py`

- [ ] **Step 1: Write failing immutable receipt tests**

Test stable IDs, canonical bytes, exact code/lock/data/config/cost/control/budget hashes, exact 1,104-entry VP ledger, one evaluation per VR, terminal status/decision compatibility, overwrite/symlink/missing/extra/reordered/oversized/corrupt rejection, and retry retention.

Assert the roster decomposition exactly: 152 core executions, of which exactly 64 are tagged primary,
plus 192 baselines, 192 negative controls, 256 robustness, 184 perturbations, 64 exposures, and 64
capacity diagnostics, for 1,104 distinct ledger entries. Verify every slot's family, role, timeframe,
horizon, and parameter identity; totals alone are insufficient.

- [ ] **Step 2: Write failing atomic access tests**

Use two processes/threads or an atomic-create fixture to prove only one access attempt commits.
Commit the programme-scoped access record before obtaining any final-row iterator. Replay and
crash/partial failure consume the attempt. Empty batch touches nothing. Directly constructed tokens,
forged eligibility, wrong programmes/hashes, stale/reordered/incomplete/extra candidate sets, and
multiple candidate-owned attempts reject before iteration.

- [ ] **Step 3: Implement bounded atomic publications**

Reuse `core.artifact_io` no-follow bounded reads and the atomic directory/publication pattern from `experiments.artifacts`, without importing underscore-prefixed helpers. Publish human summary plus machine receipt; every artifact budget is checked before write.

- [ ] **Step 4: Run clean-root replay and commit**

Publish the same synthetic VP/VR tree twice from clean roots and compare every filename and byte. Run Ruff/Mypy/diff-check, then commit/push.

### Task 9: Complete the thin vertical validation lifecycle

**Files:**
- Create: `src/market_structure_lab/research/validation.py`
- Create: `tests/test_research_validation.py`
- Modify: `src/market_structure_lab/research/__init__.py`

- [ ] **Step 1: Write a failing synthetic no-edge lifecycle test**

Exercise aggregate publication -> human-origin candidate -> A first -> subordinate B/G/E/D -> legal outcomes -> nested folds -> controls/costs/statistics/robustness -> terminal VP/VR receipts. Assert all 1,104 ledger slots are terminally accounted for, every family is rejected/inconclusive, and final holdout access count remains zero.

- [ ] **Step 2: Write a failing known-effect development test**

Use a causal synthetic fixture that reaches `supported_development` without opening final holdout. Change outer outcomes to disagree with inner selection and prove only inner selection is used.

- [ ] **Step 3: Implement a thin orchestrator**

`run_validation_programme(config, sources, output_root)` delegates to tested functions; it does not implement formulas inline. Failures create terminal receipts and never overwrite earlier attempts.

- [ ] **Step 4: Add preflight and replay integration tests**

Reject BTC/ETH/source conflicts, <5 symbols, <730 common days, wrong task ancestry, dirty/uncommitted code, changed lockfile, missing cost policy, over-budget grids, and final iterables before iteration. Two clean roots must be byte-identical.

- [ ] **Step 5: Run the focused Phase 5 suite and checkpoint**

Run:

```bash
uv run pytest -q \
  tests/test_aggregate_publication.py \
  tests/test_aggregate_bars.py \
  tests/test_identity.py \
  tests/test_research_models.py \
  tests/test_research_candidates.py \
  tests/test_research_outcomes.py \
  tests/test_research_splits.py \
  tests/test_research_costs.py \
  tests/test_research_controls.py \
  tests/test_research_statistics.py \
  tests/test_research_robustness.py \
  tests/test_research_receipts.py \
  tests/test_research_validation.py
```

Then run Ruff/Mypy/diff-check, create a Lore checkpoint, push, and verify remote/local equality.

### Task 10: Run the bounded real non-final programme or publish a truthful terminal preflight receipt

**Files:**
- Create: `src/market_structure_lab/cli/run_validation_program.py`
- Create: `tests/test_run_validation_program_cli.py`
- Modify: `pyproject.toml`
- Create: `docs/PHASE5_VALIDATION_EVIDENCE.md`
- Modify: `docs/IMPLEMENTATION_STATUS.md`
- Modify: `docs/RESEARCH_LOG.md`

- [ ] **Step 1: Write failing CLI boundary tests**

Require explicit `VP` config, clean committed code, verified Task 14/15 ancestry, the remotely
verified bounded implementation-plan checkpoint and tracked-document SHA-256, immutable output
root, no BTC/ETH/conflict symbols, non-final development-only execution, and safe terminal exit
codes. There is no `--open-final-holdout` convenience flag.

- [ ] **Step 2: Implement the thin CLI**

The CLI parses paths/IDs only, loads canonical metadata/publications, calls `run_validation_programme`, redacts raw SQL/credentials, and prints stable receipt locations plus terminal statuses.

- [ ] **Step 3: Freeze and checkpoint the exact real programme config before outcomes**

Publish and commit the source-universe metadata, aggregate config, cost policy, family grid, folds,
holdout metadata, exact 1,104-entry roster, work budget, Task 14/15 lineage, bounded implementation
plan document/checkpoint, implementation source checkpoint, and lockfile identities. The source
checkpoint must already exist and be remotely verified; the later preregistration receipt refers to
it and does not attempt an impossible self-referential commit hash. Push the preregistration and
verify remote equality before running.

- [ ] **Step 4: Execute only the bounded development programme**

If compatible >=5-symbol/730-day source coverage and event-level cost evidence are available, run the non-final development lifecycle. If prerequisites fail, publish immutable `failed/not_evaluated` preflight receipts. If valid cost coverage or effective support is incomplete, publish `completed/inconclusive`. Do not weaken a gate, substitute BTC/ETH, inspect final rows, or fabricate success.

- [ ] **Step 5: Record exact evidence**

Document immutable VP/VR IDs, source/aggregate counts and hashes, selected/excluded event identities, fold purge/embargo, complete ledger count, controls, bootstrap/support/intervals, costs/capacity, robustness/exposures, statuses/decisions, and final access count. State which families were killed, inconclusive, or only supported in development.

### Task 11: Adversarial review, complete verification, evidence, and final Phase 5 stop

**Files:**
- Modify: `docs/PHASE5_VALIDATION_EVIDENCE.md`
- Modify: `docs/IMPLEMENTATION_STATUS.md`
- Modify: `docs/RESEARCH_LOG.md`
- Modify: `docs/DISCOVERY_MVP.md` only when wording must distinguish frozen discovery from validation
- Modify: dashboard evidence fixtures only if Phase 5 evidence is already an approved read-only surface

- [ ] **Step 1: Perform implementer self-review**

Inspect every changed line for same-bar execution, future fields, final-holdout access, pooled-minute independence, zero-cost defaults, uncounted trials, identity omission, unbounded work, secret/dump/generated-run leakage, and doc overclaim.

- [ ] **Step 2: Perform independent specification and code-quality reviews**

Review against both Task 15 documents and this plan. Classify each finding, correct confirmed issues TDD-first, and rerun affected tests.

- [ ] **Step 3: Perform adversarial quantitative review**

Challenge hidden look-ahead, same-bar fantasy fills, holdout relaxation, dependence inflation, trend/beta/volatility explanations, multiplicity, cost fantasy, ex-asset/year fragility, BTC/ETH contamination, and unsupported promotion. Resolve every confirmed finding.

- [ ] **Step 4: Run the complete verification chain**

```bash
uv sync --locked
uv run ruff format --check .
uv run ruff check .
uv run pytest -q
uv run mypy src
uv build
docker compose config --quiet
git diff --check
cd dashboard
npm ci
npm run verify:evidence
npm run lint
npm run typecheck
npm run build
```

Run `npm test` only if configured; otherwise record `Not-tested: dashboard has no configured automated test command.` Run applicable native Windows/PowerShell tests or record the exact host limitation.

- [ ] **Step 5: Verify deterministic replay and boundaries**

Compare two clean aggregate/VP/VR output roots byte-for-byte. Confirm the real programme's
final-holdout access count is exactly zero. Synthetic tests prove the atomic access state, but this
bounded work unit does not open real final rows. Do not construct a strategy or trading system.

- [ ] **Step 6: Commit, push, and stop**

Create one coherent Lore close-out commit, push without force, verify remote branch SHA equals local `HEAD`, and record the SHA in evidence. The final report states exactly what passed, failed, was rejected, remained inconclusive, or was not tested. Stop before Phase 6, portfolio, paper/live trading, exchange integration, or leverage.

## Planning review record

- Repository capability map: **complete**. The plan reuses canonical data, segment, profile,
  bounded-I/O, snapshot, and immutable-publication primitives while rejecting discovery-specific
  configs, statuses, events, splits, transitions, and work budgets for validation semantics.
- Independent specification review: **approved after revision**. Repairs bind the implementation
  plan itself, enumerate the full work-budget surface, validate the exact 1,104-slot roster, seal
  final access behind a factory-created atomic receipt, scope the first real run to zero final
  access, and lock capacity, retirement, OLS, regime, and identity-mutation contracts.
- This review approves the plan, not validation results. No outcome or final-holdout row was read.

## Implementation acceptance

- All source changes were introduced by failing tests first.
- Exact family order A/B/G/E/D and the 1,104-entry programme ledger are immutable.
- A comparators exist before B/G/E/D; subordinate families cannot bypass them.
- Signals are outcome-blind and all entries are next-bar legal.
- Aggregate rows, paths, folds, controls, costs, statistics, robustness, and receipts are bounded and identity-bound.
- Missing evidence maps to truthful failed/not-evaluated or completed/inconclusive outcomes.
- The final holdout cannot be iterated accidentally and has at most one programme-scoped atomic access attempt.
- The real bounded programme is development-only and records zero final-holdout access; a future
  real final batch requires its own post-development checkpoint and instruction.
- Synthetic no-edge and known-effect development lifecycles pass without final access.
- The bounded real non-final programme either terminates with evidence or produces a truthful immutable preflight failure.
- Complete repository/dashboard verification and three-part plus adversarial review are recorded.
- No strategy, portfolio, paper/live trading, exchange, leverage, or history rewriting occurs.
