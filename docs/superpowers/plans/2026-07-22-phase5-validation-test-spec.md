# Task 15 Test Specification — Market Structure Lab Phase 5 Validation

## Purpose

Prove the validation stack is causal, bounded, dependence-aware, baseline-first, cost-aware, immutable, and unable to cross the programme final-holdout boundary accidentally.

## Task 14 prerequisite and phase-boundary regression

- Require Task 14 close-out checkpoint
  `5db2705a1cba37ee10d5eb3bd1fa470a5b628e3d` and evidence follow-up
  `3482882c864f1471ec1dd682631544d0c404c542` to remain in remote ancestry before any Phase 5
  outcome attachment.
- Preserve the four immutable Task 14 attempt receipts and the rejected `PG-000004 / DR-000704`
  conclusion; no Phase 5 test may mutate or relabel them.
- Reject any implementation path that attaches an outcome before the Task 15 checkpoint and the
  separate bounded implementation plan exist.
- Bind the Task 14 baseline, Task 15 plan/test-spec identities, code commit, and lockfile into the
  Phase 5 programme identity.

## Unit tests

### Aggregate-bar publication

- Aggregate complete one-minute rows to UTC 15m/1h/4h `[bar_open, bar_close)` bars.
- Preserve OHLCV arithmetic, ordered canonical-row count/identities/digest, parent snapshot SHA, segment, continuity, config, and artefact identity.
- Streaming/chunk partitions and one-batch execution are byte-identical.
- Reject partial periods, gaps, duplicates, reordering, source-digest corruption, mixed symbol/timeframe/segment, and cross-boundary input; never interpolate/fill.
- Every identity/seed/order key uses schema-versioned, domain-separated canonical JSON. Add
  field-boundary collision tests proving `{"a":"ab","b":"c"}` and `{"a":"a","b":"bc"}`
  differ, plus schema/domain/timestamp/numeric/tamper mutations for row, seed, holdout, baseline, and
  random-control identities.

### Exact causal clock and outcomes

- Candle timestamp is bar-open; signal cutoff is bar-close; ordinary entry is next contiguous bar-open.
- Family D entry is after a separately completed confirmation bar.
- `[entry, exit)` includes the entry minute and excludes exit.
- Test entry/exit minute boundaries, ties, gaps, missing entry/path/exit, discontinuity, wrong publication, and same-bar fantasy fills.
- Lock gross excursion formulae on the complete path: long
  `MFE=max(high/P_entry-1)`, long `MAE=min(low/P_entry-1)`, short
  `MFE=max(1-low/P_entry)`, short `MAE=min(1-high/P_entry)`; entry minute included, exit instant
  excluded, and fees excluded from MFE/MAE diagnostics.
- Detector inputs contain no outcome fields and outcome attachment cannot change detector identity.

### Candidate detectors and origins

- A/B/G/E/D exact completed-bar signals, warm-up nulls, resets, parameter identity, no future fields. Lock hour-to-target-bar conversion at 1h/4h, ATR prior indices, G compression bars before expansion, and all 184 `n_L+/-1` perturbation identities/counts. Lock every PRD formula, strict/tie comparison, current-bar rule, transition/re-arm, overlap suppression, 24/7 session rule, profile identity, entry/exit, delay, and side-aware MFE/MAE rule.
- A grid and inner-fold selector identity are bound into subordinate candidates.
- Negative A expectancy does not block valid comparator construction; invalid causality/data/support does.
- B/E expose the common baseline opportunity population, selected-zero vector, complement, and selection-rate-matched placebo without outcome selection; B migration/acceptance is fully known at the A cutoff and uses the same legal entry.
- D rejects an unfrozen level or same-bar confirmation/entry.
- A's primary statistic is the worst-control intersection-union contrast against naive,
  unconditional, and persistence vectors on matched strata; lock p-value/lower-bound construction
  and prove other A detector slots cannot be selected as uncounted controls.
- G ATR-only and Donchian-only controls implement the exact plan formulae, matching, legal entry,
  and shortage branch. D failed-Donchian omits only confirmation while retaining the candidate entry
  clock; D pseudo-level is exactly the same-symbol/timeframe/lookback/direction boundary frozen one
  UTC week earlier. Test cross-segment/missing prior-week rejection and exact matched strata.
- Human-origin H -> VP/VR lifecycle is accepted without an invented DR/B link; mismatched claimed behaviour identity rejects.
- Outcome-blind 15m gate uses detector event count/dependence only and creates a new preregistration identity.

### Walk-forward and interval leakage controls

- Outer tests are chronological and disjoint; all selection happens inside inner training/calibration folds.
- A sentinel makes the outer-best comparator differ from the inner-best and proves only the inner-best is used.
- Purge by explicit feature/label interval intersection; embargo by frozen time interval.
- Reject too-short folds, intersecting protected intervals, changed asset holdouts, or final-holdout iterables before iteration.
- Derive fold boundaries from the common timeframe timestamp grid and apply them to every symbol;
  symbol insertion, deletion, and reordering cannot move a boundary.
- Apply interval purge and the exact candidate-horizon embargo at every inner and outer test
  boundary, including exclusion from subsequent expanding training/calibration; test each
  intersection edge and identity-bound excluded rows.

### Statistics, multiplicity, and controls

- Exactly 4,096 SHA-seeded Monday UTC-week draws use equal weekly-mean units synchronised across assets; assert admissible-week rules, indices 102/3993, corrected two-sided p-value, estimator-consistent weekly sigma/power, inner-training/frozen-development source, and <2/empty/zero-variance/MDE<=0 branches. MDE is the identity-bound maximum finite positive inner-training base cost (all-development maximum for final); assert ties and invalid costs.
- Verify five fixed family alpha allocations of `0.01`, 95% intervals, exact `152` core, `64` primary and `1,104` total ledger entries, Holm within family, `p=1` for unevaluable primary slots, and diagnostic-control exclusion from promotion.
- B/E paired incremental estimand, subset/complement decomposition, oracle-leakage sentinel, and rate-matched placebo. G/D worst-of-two-control intersection-union p-value/lower-bound construction and slot count.
- Status branches are exact: missing/invalid preregistered cost policy or non-positive/non-finite
  configured aggregate base cost -> execution `failed`, scientific `not_evaluated`; fewer than two
  admissible weeks, inadequate power support, zero variance, excessive interval width, control
  matching shortage, or missing event-level cost coverage under a valid policy -> execution
  `completed`, scientific `inconclusive`; a sufficiently supported corrected lower bound at or below
  zero -> execution `completed`, scientific `rejected`.
- Label-shuffle donors are all legal opportunities in frozen strata before lookup at original event IDs; signals shift forward one week only while feature/entry/label intervals remain inside identical programme/publication/identity/fold/non-final component; test inner, outer, asset and block-5 boundaries. SHA scalar random-feature selections/directions exactly match stratum counts without outcomes; insufficient matching is inconclusive.

### Costs, robustness, capacity, and decisions

- Lock side-aware gross return and entry-notional normalisation of entry rates, exit rates times `P_exit/P_entry`, and funding rates times mark/entry; test unequal prices for long/short, deterministic fill/missed zero, doubled rates/squared fill, and delay; no zero-cost default.
- Freeze the initial instrument as spot cash with identity-bound `not_applicable_spot_v1` funding;
  reject funding rows in that programme and require a new programme for perpetual applicability.
- Funding applicability, missed fills/fill probability, turnover/opportunities, and capacity/impact availability are explicit.
- Missing event-level cost coverage under a valid preregistered policy is inconclusive; a missing or
  invalid preregistered policy remains `failed` / `not_evaluated`. Missing capacity preserves a
  supported/validated decision but sets `promotion_eligibility=inconclusive_capacity`.
- Ex-strongest-asset/year uses the restricted primary weekly-mean base-cost contrast, largest value, lexicographically smallest symbol/earliest-year tie-break, development only, and binds scores/exclusion. Lock positive 3-of-4 plus final-fold, adjacent-lookback, and every lower-bound Boolean. OLS tests fix one intercept, lexicographic reference asset/year treatment coding, train-only scaling/rank, unseen-level zero coding, exact matrix rank, and the rank-deficiency inconclusive branch.
- Exposure tests exclude the target asset from equal-weight market return and market-trend factors,
  require at least three non-target assets, rank target volatility/turnover only against non-target
  completed prior-24h values, and fit scaling/regime volatility median on inner training only. Add
  target-self-inclusion, exact-zero market-trend, and outcome-fitted-regime sentinels. A zero trend
  leaves the event outside regime diagnostics and reduces support without creating a fifth regime.
  Lock the four non-zero-trend/volatility regimes,
  two-week support, two-positive-regime rule, clearly negative regime rejection, and insufficient
  regime support inconclusive branch. An explicit fixture with exactly one supported positive regime
  must be `inconclusive`, not rejected or passed.
- Exposure diagnostics and perturbations are required for promotable evidence.
- Execution status is orthogonal to scientific decision: completed/rejected is valid; failed or abandoned requires `not_evaluated`; failed/promoted is impossible.
- Retirement/invalidation metadata is identity-bound.

### Validation work budget

For each limit, including the exact 1,104 evaluation count and 4,096 draws, use a counting/exploding iterable or allocation spy to prove rejection before iteration/allocation: source rows/bytes; aggregate bars; symbols/ranges; candidates/trials; events/outcomes; path cells; outer/inner folds/evaluations; bootstrap draws/cells/blocks; controls/placebos/perturbations; artefact count/bytes; dashboard evidence; final-holdout candidates/batch.

### Receipts and final-holdout batch

- Stable VP programme and VR evaluation IDs, canonical JSON, exact
  artefact/code/lock/data/config/cost/control/work-budget hashes. One VP owns the 1,104-entry ledger
  and one final-access state; each VR owns one immutable evaluation and cannot create another access
  state.
- Existing output, symlink, oversized, missing, extra, reordered, or changed artefact rejects.
- Retry cannot overwrite; every failed/abandoned/rejected/inconclusive attempt remains counted.
- Programme preflight and per-candidate eligibility are separate. Batch is every-and-only eligible candidate ordered A/B/G/E/D then ID and hash-bound; empty batch makes no access attempt. Temporal component is development symbols/block 5; asset component is held-out symbols/blocks 0–4; held-out symbols/block 5 is unused.
- Concurrency race permits one atomic access attempt; replay rejects; crash/partial failure consumes the attempt; no row is touched before the access record commits.
- A later candidate or selector cannot be added to the frozen batch.

## Integration milestones

1. **Vertical synthetic no-edge lifecycle:** aggregate publication -> human-origin H candidate -> legal outcomes -> nested folds -> A comparator -> B/G/E/D controls -> costs/statistics -> terminal VP/VR receipts. Every family rejects/inconclusive, all trials counted, no final holdout read. Implement this as a thin vertical orchestrator over small typed functions, not a production monolith later refactored.
2. **Known-effect development lifecycle:** a causal fixture reaches `supported_development`; parameter/cost/robustness behaviour matches the oracle without opening final holdout.
3. **Family-order lifecycle:** A evidence exists first; B/G/E/D bind its fold-specific comparator even if A expectancy is negative.
4. **BTC/ETH conflict gate:** rejects before row iteration.
5. **Immutable replay:** two clean roots produce identical files/bytes/manifests/receipts.
6. **Programme final-holdout lifecycle:** eligibility preflight, atomic one-time access, concurrency/crash/replay rejection, no sequential peeking.
7. **Bounded real non-final run:** compatible source only, exact work/search counts, all terminal attempts retained.
8. **Coverage/fold freeze:** metadata-only universe, six equal blocks, four expanding outer folds, exact prefix-remainder exclusion and three inner validation folds at 1h/4h, deterministic 20% asset holdout, and unchanged replay; too-short/fewer-than-five universe rejects before outcomes.
9. **Final eligibility:** flip programme and candidate conjuncts independently; verify canonical every-and-only batch order/hash, empty-batch no-access, and whole-batch atomic state transitions.
10. **Final decision:** known-pass and known-fail holdout-only batches; development-result changes cannot alter final statistics, selection, or multiplicity; missing evidence maps inconclusive and completed criterion failure maps rejected.

## Observability/evidence assertions

Evidence reports source/aggregate counts and digests; selected/excluded events; causal timing; fold purge/embargo counts; common/subset/complement/placebo counts; complete trial count; bootstrap/support/intervals/corrections; costs/capacity; robustness/exposures; artefact budgets; execution status; scientific decision; final-holdout access count; and exact reason for every kill/inconclusive result.

## Verification chain

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

Run `npm test` only when configured; otherwise record exactly `Not-tested: dashboard has no configured automated test command.` Run applicable Windows/PowerShell checks or record the exact environment limitation. Never claim collection as execution.

## Acceptance matrix

| Claim | Required proof |
|---|---|
| Causal clock | exact bar-open/bar-close/cutoff/entry/path boundaries and missing-path rejection |
| No leakage | outcome/future/final sentinels reject before iteration |
| Dependence | interval purge/embargo, event units, synchronised block bootstrap |
| Nested selection | inner-only selector sentinel and bound complete A grid |
| Incremental lift | B/E common population, subset, complement, paired contrast, matched placebo |
| Costs/capacity | non-zero provenance, stresses, turnover, promotion block when unavailable |
| Multiplicity | exact immutable family search and Holm correction |
| Immutable | byte replay, overwrite and artefact corruption rejection |
| Bounded | every budget rejects before iteration/allocation |
| Final holdout | frozen programme batch, one atomic attempt, concurrency/crash/replay denial |
| Honest conclusion | execution status and scientific decision are separate and code-defined |
