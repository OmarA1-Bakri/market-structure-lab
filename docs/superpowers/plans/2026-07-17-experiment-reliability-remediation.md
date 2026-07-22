# Experiment Reliability Remediation Plan

> **For implementation agents:** Execute test-first, one task at a time. Use
> `superpowers:subagent-driven-development` in the current session or
> `superpowers:executing-plans` in an isolated execution session. Every task requires an
> implementer self-review, specification review, and code-quality review before the next task.

**Goal:** Build and verify an auditable, stage-gated research program that first closes RR-000008
and Phase 0 without automatic promotion, then—only after each explicit approval—hardens provenance,
outcome-blind discovery, stability, dependence-aware statistics, semantic leakage controls,
untouched validation, realistic execution-cost modeling, and trial-program calibration so the lab
can determine rather than assume whether reproducible market behaviours contain genuine
cost-adjusted edges.

**Architecture:** Repair the foundation before widening research scope. Complete and preflight
row-level candle reconciliation, stopping before promotion; bind snapshot, feature-publication,
registry, normalizer, code, and lock identities into one verified discovery input; keep unsafe
transition inference retired; make trials immutable and terminal-status complete; strengthen cluster
and motif stability; then run real outcome-blind discovery only after the repository phase gate is
explicitly opened. Candidate outcomes, financial validation, strategy construction, and promotion
remain separate later approval boundaries.

**Tech stack:** Python 3.13, uv, Polars/Parquet, PostgreSQL 17, SQLAlchemy/Psycopg 3, Pytest, Ruff,
Mypy, React/Vite/TypeScript for the read-only dashboard.

**Canonical inputs:**

- `docs/PRD.md`
- `AGENTS.md`
- `docs/EXPERIMENT_ACCURACY_OVERVIEW.md`
- `docs/DATA_VIABILITY.md`
- `docs/DISCOVERY_MVP.md`
- `docs/RESEARCH_SKILL_WORKFLOW_SWEEP.md`
- `docs/superpowers/specs/2026-07-16-binance-row-reconciliation-design.md`

---

## Requirements summary

1. Preserve the immutable dump and append-only evidence history.
2. Do not inspect final-holdout outcomes while repairing Phase 0 or Phase 4.
3. Do not start a real discovery, validation, strategy, or trading run during the Phase 0 lane.
4. Restore the red Phase 4 golden replay before treating software reproducibility as evidence.
5. Finish and verify RR-000008 before freezing any research snapshot from corrected data.
6. Bind derived and discovery data to verified snapshot, registry, normalizer, code, and lock
   artifacts rather than accepting unverified hash strings.
7. Remove the adjacent-state/binomial transition path from research inference.
8. Make every trial immutable, replayable, and terminally classified, including failures and
   abandonment.
9. Prevent motifs from crossing missing rows, time gaps, symbols, sessions, timeframes, or segments.
10. Measure cluster and motif stability across seeds, subsamples, periods, assets, regimes, and
    parameter perturbations.
11. Make transition horizons and dependence-aware uncertainty part of the frozen run policy.
12. Strengthen semantic leakage evidence without pretending static naming rules can prove semantics.
13. Generate dashboard evidence from checksum-verified latest pointers and use accurate labels.
14. End each phase with a written evidence report and require explicit approval before the next
    phase.
15. Treat market-structure definition, behaviour stability, predictive validity, and cost-adjusted
    edge validity as distinct claims with distinct gates.
16. Pre-register the complete experiment search space, trial budget, rejection rules, negative
    controls, and effective-sample/power assumptions before the first real run.
17. Require naive and simple statistical baselines before any higher-capacity ML model; model
    complexity must earn its place through untouched incremental evidence.
18. Measure factor/beta exposure, parameter sensitivity, regime/asset dependence, survivorship and
    universe-selection risk, and backtest-overfitting risk before any edge claim.
19. Bind any materially used agent skill/workflow name and source version into the research record;
    skill guidance is review input, not evidence or authority.
20. Re-run the skill and workflow sweep before Phase 5 planning and record install/use/rejection
    decisions without adding dependencies implicitly.

## Global constraints

- Never modify `data/dumps/callscore.dump` or restored immutable source rows.
- Never delete `data/postgres`, run `docker compose down -v`, reinitialize PostgreSQL, drop schemas,
  or replace large derived datasets without explicit user confirmation.
- Reconciliation promotion and full research-snapshot publication are gated production/data
  mutations; prepare and verify them first, then obtain the required confirmation.
- Do not interpolate, forward-fill, or synthesize candles.
- Do not add dependencies.
- Keep large reads streaming and bounded-memory.
- Preserve user-owned `.codacy/`, `.vscode/`, `.coverage`, and ignored data artifacts.
- Use Lore-protocol commits; one coherent task per commit.
- Do not regenerate golden hashes merely to make tests green. First prove whether code or fixture is
  wrong, then independently review the selected correction.
- Do not copy generic financial-skill thresholds into experiment policy. Freeze thresholds only
  after repo-specific justification, power/effective-sample analysis, and independent review.
- Do not introduce deep learning, reinforcement learning, AutoML, feature stores, MLflow, W&B,
  Airflow, Kubeflow, or new modeling dependencies without a measured need and explicit approval.
- Agent skills may review or propose experiments; deterministic code and frozen promotion rules
  decide accepted, rejected, inconclusive, or failed status.

## Active progress snapshot — 2026-07-22

| Task | Status | Evidence / blocker |
|---|---|---|
| Task 1 — golden replay | Complete | Cross-platform deterministic PCA repair and reviewed fixture migration; 86 discovery/golden tests passed on Linux and Windows. |
| Task 2 — RR-000008 | Complete and explicitly promoted | All 1,583 frozen work units are terminal and verified. The approved append-only promotion has one row, 285 non-overlapping coverage intervals, an immutable receipt, exact origin counts, zero out-of-coverage replacements, and byte-identical replay. |
| Task 3 — dashboard contract | Complete through the Task 14 evidence boundary | The source-backed snapshot reports `complete_promoted`, 1,583/1,583 verified units, four real discovery receipts, exact `PG-000004` identities, `not_estimable` accuracy, and Task 15 as the next evidence gate. |
| Task 4 — transition safety | Complete | Raw adjacent binomial/BH research surface removed; boundary-aware dwell/event path is canonical. |
| Task 5 — trial ledger | Complete | `trial-receipt-v2` is immutable and terminal-status complete; Task 14 now retains four verified real discovery receipts. |
| Task 6 — Phase 0 gate | Complete | Promotion, canonical-view audit, dashboard, written evidence, full verification, Lore commit, and remote SHA check are complete at `388bf19089b6f1011879f65eec546e383b5884b5`. |
| Task 7 — concrete provenance | Complete | Snapshot, derived publication, registry/dependencies, normalizer, split, feature order, commit, lock, and admitted rows fail closed before matrix construction. |
| Task 8 — structural cluster stability | Complete | Seed, unstratified subsample, adjacent-period structure, asset, regime, and nearby-parameter evidence are separately recorded and frozen. |
| Task 9 — motif boundaries and stability | Complete | Multivariate motifs cannot bridge hard sequence boundaries and retain separate multi-axis stability/support evidence. |
| Task 10 — transition uncertainty | Complete | Horizon, bootstrap, block selection, confidence, diagnostics, sensitivity, work caps, and descriptive-only terminology are frozen. |
| Task 11 — semantic leakage evidence | Complete | Dependency/builder contracts, independent review receipts, negative tests, and pre-consumption guards are bound; residual manual review remains explicit. |
| Task 12 — original Phase 4 hardening gate | Complete and remotely checkpointed | The evidence/review chain is complete through prerequisite-closure baseline `3cf3721f1ec694b61ec1da78d0b7be24fea93caf`; local and remote match. Task 12 authorizes only the explicitly instructed Task 13 audit, not Phase C. |
| Task 13 — independent residual-risk verification | Complete and remotely checkpointed | Every finding was reproduced, confirmed defects were corrected, all three reviews and the final verification chain passed, and implementation checkpoint `59a9814a8e2a609bf1ea0bd5d0320ed0f7b33ee4` was pushed and remotely verified. |
| Task 14 — first real outcome-blind discovery | Complete and remotely checkpointed | Four immutable attempts are retained. Bounded `PG-000004` / `DR-000704` completed with a rejected programme vector, no holdout-row access, and no outcome attachment. Reviewed close-out checkpoint `5db2705a1cba37ee10d5eb3bd1fa470a5b628e3d` was pushed and remotely verified. |
| Task 15 — separate Phase 5 validation plan | Complete and remotely checkpointed | The independently approved separate plan and test specification are `2026-07-22-phase5-validation.md` and `2026-07-22-phase5-validation-test-spec.md`; checkpoint `807ac28ac5616fb837c1ccea1e2bc47572ae3984` was pushed and remotely verified before Phase 5 source code. |
| Phase 5 bounded implementation plan | Complete and remotely checkpointed | The independently approved eleven-work-unit plan is checkpointed at `e655152ab729b3e1e530f1bc044febca3509a6fd`; its tracked bytes are SHA-256 `d3520669352f0d85a27569edeefcfe84ff785e1f928ae0cc41edf91915c16111`. It contains no outcome result and authorises the bounded source TDD sequence only. |
| Phase 5 work unit 1 — identities and bounds | Complete and remotely checkpointed | The exact roster/status/identity/work-budget boundary passed 77 focused tests and both independent reviews at `8ee49b1184d46ae508e82688a14a73aa76baabb1`; no outcome or holdout access exists. |
| Phase 5 work unit 2 — aggregate publications | Complete and remotely checkpointed | Complete 1m -> 15m/1h/4h aggregation, parent-snapshot provenance, authenticated bounded spooling, cross-platform no-follow publication, and immutable success/failure receipts passed both independent reviews at `388abdfdd9fb09a888785a9c55cc2ebb7f642592`; no outcome or holdout access exists. |

## Skill and workflow routing

The authoritative sweep is `docs/RESEARCH_SKILL_WORKFLOW_SWEEP.md`.

- **Use now:** `superpowers:systematic-debugging`, `superpowers:test-driven-development`,
  `data-analytics:analyze-data-quality`, `data-analytics:validate-data`, and
  `superpowers:verification-before-completion`.
- **Use for Phase 4 planning/review:** `ralplan`, `quantitative-research`, and `llm-evaluation` only
  for the non-authoritative AI interpretation layer.
- **Use for approved empirical calibration:** `autoresearch-goal`, but only after the mission,
  validator, trial budget, negative controls, and holdout restrictions are frozen.
- **External candidate for later Phase 5/6 review:**
  `wshobson/agents@backtesting-frameworks`; do not install or activate it under Phase 0.
- **Defer/reject:** deep-learning/RL trading, provider-specific market-microstructure, portfolio,
  execution, and MLOps-platform workflows until their explicit phase and data prerequisites exist.

## GitHub checkpoint policy

- Work on the current task branch or a dedicated reviewed task branch; never push remediation work
  directly to the protected default branch.
- After every completed task: run its targeted checks, inspect the staged diff for secrets and large
  or generated files, create one Lore-compliant commit, and push it to `origin`.
- During a task that spans more than two hours of active changes, push a coherent recovery checkpoint
  at least once per work session. Mark incomplete verification with `Not-tested:`; never disguise a
  failing or partial checkpoint as completed work.
- Before every push run `git diff --check`, confirm the intended branch and staged paths, and exclude
  dumps, database volumes, credentials, caches, reconciliation payloads, and generated run artifacts.
- After every push verify the remote branch resolves to the local `HEAD`; record the commit SHA,
  task number, checks run, and push time in the relevant phase evidence report.
- Never force-push, rewrite published history, merge the remediation branch, or publish a release/tag
  without an explicit instruction covering that action.
- Phase-gate evidence reports are mandatory GitHub checkpoints even if the preceding task was pushed
  recently.

## Acceptance criteria

### Phase 0 exit criteria

- [x] RR-000008 has exactly one terminal verified manifest for every frozen work unit.
- [x] Promotion preflight proves no missing/extra work units, hash drift, unresolved keys, or
      conflicting intervals.
- [x] Any promotion occurs only after explicit user confirmation and produces a verified immutable
      promotion manifest.
- [x] The corrected canonical view has no duplicate keys, no invalid OHLC rows, and no unverified
      fallback inside promoted intervals.
- [x] Legacy transition inference cannot be invoked without boundary-aware event/dwell inputs.
- [x] Experiment artifacts cannot overwrite an existing run with different content and retain all
      terminal outcomes.
- [x] Dashboard freshness and reconciliation labels match checksum-verified current artifacts.
- [x] The Phase 4 golden integration tests pass byte-identically without unexplained fixture updates.
- [x] Ruff, Mypy, targeted tests, the full Pytest suite, package build, and Compose validation pass.
- [x] A Phase 0 evidence report lists every command, hash, residual risk, and the remaining phase
      gates. Stop here for explicit approval.
- [x] Every completed Phase 0 task has a verified remote commit SHA recorded in the evidence report.

### Phase 4 hardening exit criteria

- [x] Discovery verifies the complete derivation chain from snapshot through normalized feature
      publication.
- [x] Normalizer and publication identity drift fail before PCA or clustering reads rows.
- [x] Motifs cannot bridge dropped/null rows or any hard sequence boundary.
- [x] Cluster and motif stability policies are separately measured and frozen.
- [x] Adjacent-period stability covers frequency, centroid, and within-cluster dispersion drift.
- [x] Subsample stability does not force every base cluster into every sample.
- [x] Transition horizon, bootstrap iterations, block policy, confidence level, and diagnostics are
      frozen run inputs.
- [x] Feature leakage evidence includes declared source dependencies and an independent audit.
- [x] A real outcome-blind discovery may run only after explicit approval and must not access final
      holdout rows or outcomes.
- [x] The Task 13 residual-risk audit has reproduced and classified all ten review areas, corrected
      every confirmed defect, completed the three-part review loop, passed targeted/full/golden
      verification, and recorded a verified remote checkpoint.
- [x] Every completed Phase 4 hardening task through Task 12 has a verified remote commit SHA
      recorded in the evidence report; the final Task 12 prerequisite baseline is `3cf3721`.

### Phase 5 readiness criteria

- [ ] A separate approved plan freezes candidates, outcomes, horizons, purging/embargo, metrics,
      power/effective-sample assumptions, negative controls, multiplicity correction, costs, and
      promotion rules before any validation outcome is read.
- [ ] No strategy construction begins until a candidate passes untouched, cost-adjusted validation.

---

## Dependency order

```text
Task 1 golden diagnosis
  ├─> Task 2 RR-000008 completion/promotion
  ├─> Task 3 dashboard evidence correction
  ├─> Task 4 safe transition API
  └─> Task 5 immutable trial accounting
      └─> Task 6 Phase 0 evidence gate and explicit approval

Phase 0 evidence gate and explicit approval
  └─> Task 7 provenance chain
      ├─> Task 8 cluster stability
      ├─> Task 9 motif boundaries and stability
      ├─> Task 10 transition uncertainty policy
      └─> Task 11 semantic leakage evidence
          └─> Task 12 original Phase 4 hardening verification
              └─> Task 13 independent residual-risk verification

Task 13 completion and final Phase B approval gate
  └─> Task 14 first real outcome-blind discovery
      └─> Task 15 separate Phase 5 validation plan
```

---

## Phase A — Phase 0 correctness and evidence closure

### Task 1: Diagnose and restore the Phase 4 golden replay

**Files:**

- Inspect/modify only after diagnosis:
  - `src/market_structure_lab/discovery/behaviours.py`
  - `src/market_structure_lab/discovery/evidence.py`
  - `src/market_structure_lab/discovery/runs.py`
  - `tests/test_phase4_golden.py`
  - `tests/fixtures/phase4/discovery_run_v1.json`
  - `tests/fixtures/phase4/discovery_interpretation_input_v1.json`
  - `tests/fixtures/phase4/discovery_interpretation_response_v1.json`
- Add: `docs/benchmarks/phase4-golden-drift-diagnosis.md`

- [ ] **Step 1: Reproduce and freeze the failure evidence**

Run the exact current failing suite and capture generated versus expected behaviour IDs, artifact
hashes, manifest hashes, and interpretation IDs:

```powershell
uv run pytest -q tests/test_phase4_golden.py -vv
```

Expected initial state: three failures at the replay, interpretation-input, and interpretation-
publication boundaries. Do not alter fixtures yet.

- [ ] **Step 2: Locate the first behaviour-changing commit**

Use `git log`, focused diffs, and—if necessary—`git bisect run` with the single golden replay test.
Record whether the change was intentional schema/algorithm evolution, nondeterminism, missing version
bump, or an actual bug. The diagnosis must explain every changed byte, not only the top-level hash.

- [ ] **Step 3: Add regression tests for the root cause**

Tests must prove:

- two clean output roots produce identical bytes;
- behaviour IDs are derived from the documented stable payload only;
- evidence packs reference the regenerated run's exact behaviour IDs;
- interpretation publication rejects stale IDs and accepts exact current IDs;
- algorithm/schema changes require an explicit version change and reviewed fixture migration.

- [ ] **Step 4: Apply the minimum correction**

Fix code if the new output violates the frozen contract. Update fixtures only if the algorithm change
is proven intentional, versioned, and independently reviewed. Never mix both approaches without a
written reason.

- [ ] **Step 5: Verify replay closure**

```powershell
uv run pytest -q tests/test_phase4_golden.py tests/test_discovery_evidence.py
uv run ruff check src/market_structure_lab/discovery tests/test_phase4_golden.py
uv run mypy src/market_structure_lab/discovery
git diff --check
```

**Acceptance:** all golden tests pass twice from clean temporary roots; the diagnosis names the root
cause and the selected repair; no unexplained fixture hash changes remain.

---

### Task 2: Complete and verify RR-000008, preflight promotion, then stop

**Files/artifacts:**

- Existing plan: `data/exports/reconciliation/RR-000008.run.json`
- Existing partitions: `data/exports/reconciliation/run_id=RR-000008/`
- Source:
  - `src/market_structure_lab/data/reconciliation/`
  - `src/market_structure_lab/cli/reconcile_candles.py`
  - `src/market_structure_lab/data/migrations/0003_reconciliation_work_unit_lookup.sql`
- Tests:
  - `tests/test_reconciliation_manifests.py`
  - `tests/test_reconciliation_publication.py`
  - `tests/test_reconciliation_orchestrator.py`
  - `tests/test_reconciliation_migration.py`
  - `tests/integration/test_reconciliation_postgres.py`

- [ ] **Step 1: Lock the live incident evidence and write the failing lookup regression**

Record the stopped-worker count, RR run/code/lock/Python identities, 1,322 filesystem manifests,
1,320 database units, both filesystem-only IDs, current promotion set, sanitized PostgreSQL error
class/message, and existing replacement indexes. Add a migration regression requiring an idempotent
index whose leading columns are exactly `(run_id, work_unit_id)`. Add an isolated PostgreSQL test or
query-plan assertion proving a per-work-unit count does not require a parallel sequential scan.

Run the focused test before implementation and confirm it fails because the index is absent:

```powershell
uv run pytest -q tests/test_reconciliation_migration.py `
  tests/integration/test_reconciliation_postgres.py -k "work_unit_lookup or replacement_count"
```

- [ ] **Step 2: Add and safely apply the work-unit replacement index**

Create `0003_reconciliation_work_unit_lookup.sql` with one idempotent index on
`market_data.candle_reconciliation_replacements(run_id, work_unit_id)`. Apply it only while the
RR-000008 worker count is zero and the shared recovery advisory-lock domain is clear. Do not change
the frozen RR run, code commit, `uv.lock`, Python executable, manifests, or replacement rows.

Verify from PostgreSQL metadata and `EXPLAIN (FORMAT JSON)` that the count path uses the new index;
capture duration and database health without logging credentials or bound SQL data.

- [ ] **Step 3: Resume without creating a new run identity**

Verify the frozen run hash and resume only missing work units. Do not count directory existence as
success; verify each manifest, part checksum, work-unit identity, classification conservation, and
terminal status. Keep batches at no more than 100 units and stop fail-closed on any nonzero worker
exit, manifest/database mismatch, memory error, checksum drift, or unexpected promotion row.

- [ ] **Step 4: Add/confirm completion-audit tests**

The audit must fail for a missing unit, duplicate unit, extra unit, stale stage, missing `_SUCCESS`,
part hash mismatch, row-count mismatch, nonterminal status, changed run plan, or overlapping promoted
interval.

- [x] **Step 5: Reconcile the complete filesystem/database ledgers**

Require exactly 1,583 unique frozen work-unit IDs in each ledger, identical manifest/status/row and
replacement-count/hash evidence per ID, zero filesystem-only units, zero database-only units, and
classification/field-difference conservation. Re-verify every `_SUCCESS`, manifest, part set,
Parquet checksum, streamed row count, and replacement logical hash before preflight.

- [x] **Step 6: Produce a read-only promotion preflight**

Preflight output must include all 1,583 expected units, terminal-status counts, classification and
field-difference totals by symbol/era, candidate replacement logical hash, residual unavailable
ranges, and exact database/view changes that promotion would make.

- [x] **Step 7: Refresh dashboard and run the Phase 0 verification suite**

Regenerate the dashboard snapshot only from the fully verified RR bundle. Run the targeted tests,
full Pytest suite, Ruff format/check, Mypy, lock check, package build, Compose validation, dashboard
evidence verification, typecheck, lint, and production build. Update the Phase 0 evidence report and
push a Lore-compliant checkpoint whose remote SHA is re-read and recorded.

- [x] **Step 8: Stop for explicit promotion confirmation**

Promotion mutates durable research state. Do not promote automatically. After confirmation, use the
existing explicit promotion command; never reinitialize PostgreSQL or replace the dump.

- [x] **Step 9: After approval only, verify the promoted corrected view**

Prove unique canonical keys, correct precedence, no fallback inside promoted intervals, no
supplements/corrections outside verified intervals, exact counts/hashes, bounded reads, and stable
replay of the promotion manifest.

- [x] **Step 10: After approval only, freeze the promoted reconciliation evidence report**

Update `docs/DATA_VIABILITY.md` and `docs/IMPLEMENTATION_STATUS.md` only from checksum-verified final
artifacts. Do not declare experiment eligibility until a deliberate snapshot is separately frozen.

**Acceptance before the user gate:** every work unit is terminal and verified, read-only preflight
passes, dashboard and full verification are current, the checkpoint is pushed, and RR-000008 remains
unpromoted. **Acceptance after a separately confirmed promotion:** the corrected view and promotion
report replay exactly and unresolved intervals remain explicit.

---

### Task 3: Make dashboard status source-backed and semantically exact

**Files:**

- Modify: `dashboard/src/data/lab-data.ts`
- Modify: `dashboard/src/pages/overview-page.tsx`
- Modify: `dashboard/src/components/reconciliation-status-panel.tsx`
- Modify/create generator under the existing dashboard/data publication pattern
- Update generated:
  - `dashboard/public/data/reconciliation-status.json`
  - current freshness summary artifact
- Tests: existing dashboard tests or new focused Vitest tests beside the affected modules

- [ ] **Step 1: Write failing stale-pointer and wording tests**

Prove that dashboard data are derived from `data/exports/freshness/latest.json` plus its verified
report hash, reject mismatched hashes, expose the evidence timestamp, and never label partial
RR-000002 work-unit evidence as full-history “promotable symbol coverage.”

- [ ] **Step 2: Replace hard-coded freshness totals**

Generate the public summary from checksum-verified latest pointers. Keep the browser bundle free of
database credentials and raw private database access.

- [ ] **Step 3: Correct reconciliation labels**

Use “audited keys,” “verified work units,” and “promoted verified intervals” precisely. Display the
run ID, cutoff, algorithm version, audited row count, completion state, and manifest hash.

- [ ] **Step 4: Verify dashboard build and evidence**

```powershell
cd dashboard
npm test -- --run
npm run lint
npm run build
```

**Acceptance:** dashboard figures match the current checksum-bearing artifacts; stale or tampered
inputs fail generation; no label implies more coverage than the artifact proves.

---

### Task 4: Retire unsafe legacy transition inference

**Files:**

- Modify/remove:
  - `src/market_structure_lab/transitions/matrix.py`
  - `src/market_structure_lab/transitions/significance.py`
  - `src/market_structure_lab/transitions/screening.py`
  - `src/market_structure_lab/transitions/__init__.py`
- Reuse/extend: `src/market_structure_lab/discovery/transitions.py`
- Tests:
  - `tests/test_transitions.py`
  - `tests/test_transition_significance.py`
  - `tests/test_transition_screening.py`
  - `tests/test_discovery_transitions.py`

- [ ] **Step 1: Write regression tests for the prohibited assumptions**

Tests must fail any API that treats adjacent minute states as independent or crosses symbol,
timeframe, segment, session, material-gap, or non-contiguous boundaries. Include repeated dwell runs
and demonstrate that event/dwell compression changes effective support.

- [ ] **Step 2: Select one canonical transition observation contract**

Promote the boundary-aware `ClusterObservation`/event-level pattern into the canonical transition
surface. Do not maintain two competing statistical definitions.

- [ ] **Step 3: Remove binomial/BH claims from serial adjacent counts**

Either delete the legacy significance/screening API or convert it into an explicitly descriptive
adapter that cannot emit inferential significance. Update imports, docs, and tests.

- [ ] **Step 4: Verify boundaries and compatibility**

```powershell
uv run pytest -q tests/test_transitions.py tests/test_transition_significance.py tests/test_transition_screening.py tests/test_discovery_transitions.py
uv run ruff check src/market_structure_lab/transitions src/market_structure_lab/discovery/transitions.py tests
uv run mypy src/market_structure_lab/transitions src/market_structure_lab/discovery/transitions.py
```

**Acceptance:** no public research API can produce an independence-based p-value from raw adjacent
minutes; all transitions carry explicit effective support and boundary evidence.

---

### Task 5: Replace mutable generic experiment output with an immutable trial ledger

**Files:**

- Modify: `src/market_structure_lab/experiments/artifacts.py`
- Modify: `src/market_structure_lab/experiments/__init__.py`
- Modify: `src/market_structure_lab/discovery/runs.py`
- Add focused trial models/publication module only if two real writers justify separation
- Tests:
  - `tests/test_experiments.py`
  - `tests/test_discovery_runs.py`

- [ ] **Step 1: Write failing immutability and lifecycle tests**

Cover identical replay, conflicting reuse of a run ID, stale/extra files, interrupted staging,
failed, inconclusive, abandoned, rejected, and completed states. Existing published bytes must never
be overwritten.

- [ ] **Step 2: Define the minimum canonical trial manifest**

Require run ID/mode/status, dataset and feature publication identities, split, detector/candidate
version, code commit, lock hash, config, seed, symbols/ranges, parent IDs, metrics schema, warnings,
conclusion, started/completed UTC timestamps, and all artifact hashes. Validation/strategy modes must
also require frozen outcome/cost policy references.

- [ ] **Step 3: Publish atomically and append terminal receipts**

Use a sibling staging directory, canonical JSON, checksum verification, and fail-closed idempotency.
Persist a failure/abandonment receipt even when the main algorithm raises before normal publication.

- [ ] **Step 4: Migrate callers without fabricating old evidence**

Keep any legacy artifact readable, label it legacy, and do not invent absent provenance. New runs use
the canonical schema only.

- [ ] **Step 5: Verify the ledger**

```powershell
uv run pytest -q tests/test_experiments.py tests/test_discovery_runs.py
uv run ruff check src/market_structure_lab/experiments src/market_structure_lab/discovery/runs.py tests/test_experiments.py tests/test_discovery_runs.py
uv run mypy src/market_structure_lab/experiments src/market_structure_lab/discovery/runs.py
```

**Acceptance:** every attempted run has one immutable terminal receipt; conflicting replay fails;
trial counts can be reconstructed without scanning chat history or overwriting evidence.

---

### Task 6: Run the Phase 0 verification and stop gate

**Files:**

- Update: `docs/IMPLEMENTATION_STATUS.md`
- Update: `docs/EXPERIMENT_ACCURACY_OVERVIEW.md`
- Create: `docs/PHASE0_REMEDIATION_EVIDENCE.md`

- [x] **Step 1: Run targeted suites from Tasks 1–5**
- [x] **Step 2: Run the complete verification chain**

```powershell
uv sync --locked
uv run ruff format --check .
uv run ruff check .
uv run pytest -q
uv run mypy src
uv build
docker compose config --quiet
git diff --check
```

- [x] **Step 3: Perform manual integrity review**

Check secrets, dump hash, large/generated files, ignored volumes, bounded-memory paths, UTC handling,
outcome leakage, symbol/session/gap boundaries, exact docs/artifact agreement, and no final-holdout
access.

- [x] **Step 4: Write the evidence report and stop**

Record changed files, commands/results, reconciliation hashes/counts, residual unavailable ranges,
test gaps, and every unresolved risk. Do not begin Phase B without explicit user approval.

---

## Phase B — Phase 4 discovery hardening (requires explicit approval after Phase A)

### Task 7: Bind the complete snapshot-to-discovery provenance chain

**Files:**

- Modify: `src/market_structure_lab/data/derived.py`
- Modify: `src/market_structure_lab/features/models.py`
- Modify: `src/market_structure_lab/features/normalization.py`
- Modify: `src/market_structure_lab/discovery/splits.py`
- Modify: `src/market_structure_lab/discovery/runs.py`
- Tests:
  - `tests/test_derived_publication.py`
  - `tests/test_feature_normalization.py`
  - `tests/test_discovery_splits.py`
  - `tests/test_discovery_runs.py`

- [x] **Step 1: Write provenance-drift tests**

Reject a missing normalizer, wrong training partition, wrong selected feature list, changed
normalizer bytes, mismatched feature publication, mismatched snapshot manifest, changed registry,
uncommitted/dirty identity, wrong code commit, or changed lockfile before matrix construction.

- [x] **Step 2: Make normalization identity mandatory for discovery features**

Do not merely add another trusted string. Supply and verify the actual `RobustNormalizer` artifact
and checksum-bearing `DerivedPublicationManifest`. Require a clean immutable dataset snapshot and the
exact feature partition hashes used by the discovery/development inputs.

- [x] **Step 3: Introduce one verified discovery provenance value**

It should bind snapshot manifest hash, derived publication hash, registry hash, normalizer hash,
feature names/order, split hash, code commit, and lock hash. `DiscoveryRunConfig` stores that identity;
`run_discovery` verifies the supplied artifacts and row content against it.

- [x] **Step 4: Keep rows narrow**

Avoid duplicating large manifests into every `FeatureRow`. Rows retain stable IDs; the verified
publication manifest owns dataset-wide provenance.

**Acceptance:** forged or mismatched hashes cannot reach PCA/K-means; identical verified artifacts
replay; memory remains bounded.

---

### Task 8: Strengthen cluster stability evidence

**Files:**

- Modify: `src/market_structure_lab/discovery/stability.py`
- Modify: `src/market_structure_lab/discovery/runs.py`
- Tests: `tests/test_discovery_stability.py`, `tests/test_phase4_golden.py`

- [x] **Step 1: Add failing drift and sampling tests**

Create cases where cluster frequencies stay constant while centroids or within-cluster dispersion
shift; these must fail the adjacent-period policy. Prove subsamples are deterministic but do not
force one member from every base cluster.

- [x] **Step 2: Extend adjacent-period evidence**

Report frequency JS distance, centroid displacement in frozen projected units, within-cluster scale
change, assignment confidence/margin drift, and per-cluster support by period/asset.

- [x] **Step 3: Separate minimum support from coverage**

Require explicit per-cluster event counts; a cluster cannot pass solely because it appears once in
many assets.

- [x] **Step 4: Replace fixture plumbing thresholds**

Use a deterministic fixture with meaningful pass/fail margins. Thresholds remain software fixtures,
not automatic real-research policy. Document that distinction.

**Acceptance:** constant-frequency structural drift is detected; rare/empty clusters fail closed;
the golden replay remains byte-identical after an explicit algorithm-version update.

---

### Task 9: Enforce motif boundaries and add motif stability

**Files:**

- Modify: `src/market_structure_lab/discovery/motifs.py`
- Modify: `src/market_structure_lab/discovery/runs.py`
- Reuse a shared boundary primitive from the canonical transition path
- Tests: `tests/test_discovery_motifs.py`, `tests/test_discovery_runs.py`, `tests/test_phase4_golden.py`

- [x] **Step 1: Add hard-boundary regression tests**

Cover null-row removal, one-minute gaps, session changes, symbol/timeframe/segment changes, duplicate
timestamps, out-of-order rows, and non-contiguous `information_cutoff`. No motif window may span any
break.

- [x] **Step 2: Build motif inputs from explicit contiguous sequences**

Do not append only `values[0]` to a coarse `(symbol, timeframe, segment_id)` bucket. Preserve row
identity/timestamps, split at every approved boundary, and record the feature or multivariate
definition used.

- [x] **Step 3: Implement motif stability evidence**

Measure recurrence/rank/shape agreement across deterministic seeds or tie policies, subsamples,
adjacent development periods, assets/regimes, window lengths, exclusion zones, and nearby distance
parameters. Record rejected motifs rather than silently dropping them.

- [x] **Step 4: Define promotion interaction**

Cluster behaviours may remain frozen independently, but unstable motifs cannot be published as
supporting recurring-sequence evidence. Make this distinction explicit in metrics and summaries.

**Acceptance:** boundary tests pass; every published motif has separate stability/support evidence;
first-feature-only behavior is removed or explicitly versioned as a univariate baseline.

---

### Task 10: Make transition uncertainty a frozen, dependence-calibrated policy

**Files:**

- Modify: `src/market_structure_lab/discovery/runs.py`
- Modify: `src/market_structure_lab/discovery/transitions.py`
- Tests: `tests/test_discovery_transitions.py`, `tests/test_discovery_runs.py`

- [x] **Step 1: Move hard-coded values into validated run configuration**

Freeze horizon, bootstrap iterations, confidence level, block-length rule/value, minimum effective
support, interval-width reporting, and sensitivity settings into config/run identity.

- [x] **Step 2: Add dependence diagnostics**

Select or justify block length from dwell/event sequences using a deterministic predeclared rule;
report sensitivity across nearby block lengths. Do not optimize the rule against attractive
transition results.

- [x] **Step 3: Improve interval-resolution tests**

Test deterministic intervals, low-support behavior, zero destination counts, multiple boundaries,
long dwell runs, and convergence/sensitivity as bootstrap iterations increase.

- [x] **Step 4: Preserve the descriptive boundary**

Do not add a significance or edge claim. Metrics and summaries must call these conditional
recurrence estimates.

**Acceptance:** no hidden transition defaults remain; weak/unstable intervals are visibly rejected
or marked descriptive; run identity changes with every uncertainty-policy change.

---

### Task 11: Strengthen semantic leakage evidence

**Files:**

- Modify: `src/market_structure_lab/features/registry.py`
- Modify feature builder/definition modules under `src/market_structure_lab/features/`
- Modify: `src/market_structure_lab/data/derived.py`
- Tests: `tests/test_feature_registry.py`, `tests/test_feature_builder.py`,
  `tests/test_derived_publication.py`

- [x] **Step 1: Define auditable feature dependencies**

Each feature definition records source fields, trailing window, observable cutoff rule, warm-up,
normalization requirement, and an explicit future/outcome prohibition. Hash this into registry
identity.

- [x] **Step 2: Add adversarial leakage fixtures**

Include innocuously named future returns, full-period normalization, centered windows, global
max/min, future-dependent labels, and timestamps whose values are observable only after the declared
cutoff. These must fail publication.

- [x] **Step 3: Produce an independent leakage-audit receipt**

Publication records which registered builder produced each field and the tested cutoff/dependency
contract. Static metadata is evidence, not proof; retain reviewer and negative-test evidence.

- [x] **Step 4: Verify no holdout iteration**

Keep generator tests proving rejected holdout inputs are never consumed, even once.

**Acceptance:** every discovery feature has inspectable dependency/cutoff evidence; known semantic
leakage patterns fail; the documentation states the residual manual-review limit.

---

### Task 12: Run the Phase 4 hardening verification and stop gate

**Files:**

- Update: `docs/DISCOVERY_MVP.md`
- Update: `docs/IMPLEMENTATION_STATUS.md`
- Update: `docs/EXPERIMENT_ACCURACY_OVERVIEW.md`
- Create: `docs/PHASE4_HARDENING_EVIDENCE.md`

- [x] Run all Phase B targeted tests.
- [x] Run the complete repository verification chain from Task 6.
- [x] Re-run golden publication twice from clean roots and compare every byte.
- [x] Review bounded memory, holdout non-access, provenance linkage, trial accounting, sequence
      boundaries, and absence of inferential/edge claims.
- [x] Write the evidence report and stop for explicit approval before any real discovery run.

**Acceptance:** all original Phase B verification requirements pass; the Phase 4 hardening evidence
report records every command, hash, residual risk, and unavailable check; one Lore-compliant Task 12
checkpoint is pushed and its remote SHA is verified.

Task 12 does not authorize Phase C or a real discovery run. When Task 13 has been explicitly
authorized, continue directly into Task 13 without an additional approval pause. No real discovery,
final-holdout access, outcome attachment, validation, strategy, execution, leverage, or trading work
may begin.

---

### Task 13: Independently verify and close residual Phase 4 hardening risks

**Authoritative baseline:** the completed Task 12 local and remote SHA
`3cf3721f1ec694b61ec1da78d0b7be24fea93caf`.

This is a separate post-Task-12 adversarial verification task. It challenges software correctness,
state transactions, type contracts, provenance, leakage evidence, publication linkage,
missingness-selection, bounded work, and quality enforcement. It does not run real discovery,
inspect holdout rows or outcomes, attach outcomes, estimate accuracy, validate an edge, construct a
strategy, or begin Phase 5/execution/trading.

**Inspect and modify only after diagnosis:**

- `src/market_structure_lab/auction/engine.py`, `auction/windows.py`, and relevant profile/node
  collaborators;
- `src/market_structure_lab/features/builder.py`, `normalization.py`, and `registry.py`;
- `src/market_structure_lab/data/derived.py`;
- `src/market_structure_lab/discovery/matrix.py`, `behaviours.py`, `runs.py`, and `splits.py`;
- `tests/phase4_fixture_producer.py` and the canonical owning tests;
- the Phase 4 discovery, implementation-status, accuracy, evidence, and roadmap documents.

- [x] **Step 1: Freeze baseline and reproduce every independent finding**

Record branch, exact Task 12 local/remote SHA and equality, comparison with `bd9f82b`, tracked
worktree state, collection count, targeted/full/dashboard/cross-platform results, and attached CI
checks. For each area below, inspect implementation and tests, run the smallest reproduction, and
record only `Confirmed`, `Partially confirmed`, `Refuted`, `Already fixed`, or
`Not independently verifiable`, with severity, symbol, evidence, root cause, provenance, required
correction, and regression acceptance criteria:

1. `AuctionEngine.update()` transactional correctness across every mutable collaborator, fault
   stage, reset, and retry; failed updates must restore exact pre-call state and preserve every
   applicable reset cause.
2. `FeatureBuilder.build_batch()` hidden state, explicit continuation context, input-stream binding,
   rollback, issuance/revocation, and deterministic retry.
3. Integer-feature normalization boundaries; normalized floats must not impersonate raw
   integer-valued `FeatureRow` values or the raw registry identity.
4. Behaviour event/duration bindings; row, event, duration, and event-publication identities must be
   joined by a stable explicit key rather than parallel position.
5. Phase 4 fixture `volume_change` provenance; a trailing observation must be the actual preceding
   causal row with matching symbol/timeframe/segment/continuity, or the feature must be renamed.
6. Leakage approval/receipt reconstruction; validation must reconstruct approval identity from
   reviewer, review artifact, per-field tests, and negative-test evidence while retaining the manual
   review limitation.
7. Event-publication linkage to the exact verified source feature publication, registry,
   dependency contract, approval, receipt logical identity, and receipt artifact.
8. Structural missingness and complete-case selection; bounded evidence and a frozen rejection or
   inconclusive policy must make selection auditable without imputation.
9. Aggregate resource/work budgets for profile, rows/records/matrix, PCA/clustering/stability,
   motifs, transitions, evidence, publication, and dashboard paths; invalid configurations must
   fail deterministic preflight before expensive work.
10. Automated quality enforcement; distinguish collection, executed tests, cross-platform checks,
    dashboard checks, CI attachment, and local claims without calling lint/typecheck/build tests.

Do not change production code, schemas, identities, manifests, fixtures, or expected hashes before
the relevant failing reproduction or refuting proof exists. Publish the ten-area verdict table
before broad correction.

- [x] **Step 2: Correct confirmed state, type, and binding defects test-first**

Make auction advancement transactional using a pure transition, provisional atomic swap, or
complete rollback snapshot that includes window policy, node tracker, contribution cache, profile,
latest input/snapshot, symbol, and timeframe. Preserve simultaneous reset causes. Make feature
batches depend only on declared input plus explicit frozen continuation, bind the ordered input
stream and starting context, revoke incomplete issuance, restore exact state, and support clean
retry. Represent transformed values in a separately typed normalized layer rather than weakening
raw integer validation. Replace behaviour positional metadata with a frozen keyed binding enforcing
coverage, uniqueness, canonical order, source-publication identity, and deterministic hash. Run
each failing test red before the minimum correction and green immediately after it.

- [x] **Step 3: Correct confirmed provenance, missingness, and budget defects test-first**

For genuine trailing features, prove previous timestamp/order, symbol, timeframe, segment,
continuity, and value linkage; otherwise truthfully rename and independently review every identity
migration and byte change. Reconstruct leakage approvals during receipt validation and use bounded
no-follow hash verification when underlying artifacts are supplied; otherwise state that internal
identity consistency does not prove the claimed review occurred. Reject unrelated event/feature
publication identities before row iteration. Publish bounded missingness evidence with frozen total
and per-feature drop policies and deterministic rejected/inconclusive outcomes; never impute. Add
only confirmed missing preflights, reusing existing bounded-work primitives, and freeze every limit
that changes output, admissibility, or evidence volume.

- [x] **Step 4: Verify, review, evidence, checkpoint, push, and stop**

Run the focused auction/window/feature/derived/matrix/run/split/golden tests, Ruff format and lint,
Mypy, `git diff --check`, the dashboard `npm ci`/evidence/lint/typecheck/build chain, the complete
repository `uv sync --locked`/Pytest/build/Compose chain, applicable Windows/PowerShell checks, and
two independent clean-root golden replays comparing every filename and byte. Record that the
dashboard has no automated test command when still true. Determine whether checks attach to exact
final `HEAD`; add no external or governance-heavy tooling and record any remaining enforcement risk.

Complete and record implementer self-review, independent specification review, and independent
code-quality review. Resolve every finding and rerun affected tests plus the final full chain. Update
`docs/PHASE4_HARDENING_EVIDENCE.md`, create one distinct Lore-compliant Task 13 commit, push without
history rewriting, verify local/remote equality, create a small evidence-only follow-up only if the
final SHA must be recorded, and verify equality again before Task 14.

**Acceptance:** every independent residual-risk finding has a reproduced verdict; confirmed defects
are corrected and regression-tested; targeted and complete verification are finished; clean golden
publications replay byte-identically or every intentional migration is independently reviewed; the
three-part review loop is complete; written evidence is updated; the final Lore-compliant Task 13
checkpoint is pushed; and the remote branch SHA is verified against local `HEAD`.

The final Phase B boundary remains mandatory. Under the explicit 2026-07-19 continuous-execution
instruction, proceed into Task 14 only after the Task 13 evidence checkpoint is pushed and its remote
SHA is verified. No real discovery may begin before that proof.

---

## Phase C — Empirical calibration (separately gated)

### Task 14: Freeze and run the first real outcome-blind discovery

This task resolves the “no empirical discovery evidence” gap only. It does **not** estimate edge
accuracy and must not attach outcomes.

**Prerequisites:** Phase A and B evidence accepted; RR-000008 promoted; deliberate immutable
snapshot and Phase 3 feature/event publication approved; clean commit; split metadata frozen with
holdout rows inaccessible.

- [x] Pre-register the eligible contiguous universe, discovery/development/final-holdout metadata,
      asset holdouts, features, normalizer, cluster/motif search space, stability policies, trial
      count, negative controls, and rejection rules.
- [x] Pre-register universe-selection and survivorship-bias evidence, optional-field/zero-volume
      policies, regime definitions that use only contemporaneous or trailing information, naive
      baselines, and a fixed complexity/search budget.
- [x] Freeze the snapshot and derived publication without inspecting final-holdout outcomes.
- [x] Run bounded outcome-blind discovery and retain every completed/rejected/failed/inconclusive
      trial.
- [x] Publish the reliability vector: data evidence, replay hashes, effective supports, cluster/motif
      stability, transitions with intervals, asset/regime breakdowns, and contradictions.
- [x] Run independent data/evidence and adversarial quantitative review lanes. Record the review
      surfaces and accept or reject every finding; unavailable external skill runtimes are not
      claimed as executed.
- [x] Stop before outcome attachment. Do not claim an edge.

**Acceptance:** satisfied by bounded `PG-000004` / `DR-000704`: its end-to-end identities and
programme vector verify, the frozen stability decision is `rejected`, all four attempts are counted,
and final-holdout rows and outcomes remain untouched. See
`docs/TASK14_OUTCOME_BLIND_DISCOVERY_EVIDENCE.md`.

---

### Task 15: Create the separate Phase 5 validation plan

Do not implement Phase 5 under this canonical remediation plan. Task 14 froze no behaviour for
advancement, so Task 15 uses explicitly preregistered human-origin OHLCV candidate families and must
not fabricate a `DR-*` or behaviour lineage. The separate consensus plan and companion test
specification are:

- `docs/superpowers/plans/2026-07-22-phase5-validation.md`;
- `docs/superpowers/plans/2026-07-22-phase5-validation-test-spec.md`.

They define:

- candidate-specific outcomes, horizons, and one causal information cutoff;
- purging/embargo and chronological nested walk-forward folds;
- untouched temporal and asset holdouts plus one-time final-holdout access;
- effective-sample and power analysis before selecting a minimum event count;
- dependence-aware block bootstrap and interval-width decision rules;
- naive, unconditional, persistence, and simple trend/breakout baselines before any higher-capacity
  model; no higher-capacity ML is in the initial scope;
- mean net event-return and paired incremental-lift estimands appropriate to the frozen continuous
  outcomes; classification discrimination/calibration metrics are not claimed applicable;
- shuffled labels, time shifts, random features, placebo behaviours, and other negative controls;
- the complete trial/search count, family definitions, multiplicity correction, and false-discovery
  accounting;
- factor/beta, momentum, volatility, liquidity, asset, and regime exposure diagnostics so disguised
  systematic exposure is not called edge;
- parameter perturbation, the complete frozen specification count, and degradation across folds;
  PBO and deflated Sharpe are explicitly inapplicable to this initial chronological mean-event-return
  design unless a later separately authorised plan changes the estimand;
- conservative fees, spread, slippage, funding, latency, fill probability, missed fills, turnover,
  market impact, and capacity stress;
- promotion, rejection, inconclusive, and retirement rules fixed before outcomes are read;
- a decision on the vetted `backtesting-frameworks` skill and any selected supplemental skill after
  source review, with no implicit package or service adoption.

The plans freeze the order A -> B -> G -> E -> D, with B/G/E/D subordinate to named simple controls,
and prefer the simplest model capable of passing the frozen test. ML is not presumed.

**Acceptance:** the separate Phase 5 plan and test specification are independently reviewed,
committed, pushed, and remotely verified before outcome attachment. The user's 2026-07-22 master
prompt is the explicit downstream authorisation to proceed immediately into a separate bounded
implementation plan after that checkpoint; it does not authorise strategy, leverage, paper/live
trading, or history rewriting.

---

## Verification matrix

| Risk | Primary proof | Negative proof |
|---|---|---|
| Golden drift | Byte-identical repeated replay | Version/fixture mismatch rejected |
| Incomplete reconciliation | 1,583 verified terminal receipts | Missing/extra/stale unit fails preflight |
| Bad promotion | Verified interval/hash manifest | Unverified fallback and overlap rejected |
| Stale dashboard | Latest-pointer/hash-derived JSON | Tampered/stale pointer fails generation |
| Serial transition bias | Boundary-aware dwell/event tests | Raw adjacent significance unavailable |
| Trial loss | Immutable terminal receipts | Conflicting reuse/overwrite rejected |
| Normalization leakage | Verified training normalizer chain | Dev/holdout-fitted or changed artifact rejected |
| Provenance forgery | Actual manifest/content verification | Correct-format wrong hash rejected |
| Cluster instability | Frequency/centroid/dispersion/support metrics | Constant-frequency drift rejected |
| Motif boundary leak | Explicit contiguous sequence tests | Dropped row/gap/session crossing rejected |
| Weak transition CI | Frozen dependence policy and sensitivity | Low support/wide interval cannot promote |
| Semantic outcome leakage | Builder dependency audit + adversarial tests | Innocuous future feature rejected |
| False accuracy claim | Reliability vector and `n` trial count | Single unsupported percentage prohibited |
| Skill-induced methodology drift | Skill/source/version receipt plus reviewed adoption decision | Generic external threshold cannot enter frozen policy |
| Model complexity overfit | Naive/simple baselines, fixed search budget, nested walk-forward | Higher-capacity model rejected without untouched incremental value |
| Disguised systematic exposure | Frozen factor/beta/regime diagnostics | Candidate rejected when effect vanishes after exposure adjustment |
| Universe/survivorship bias | Point-in-time eligible universe and admission audit | Current-survivor-only universe cannot support promotion |
| Validation selection bias | Complete trial ledger, fixed family grid, and within-family Holm correction | Attractive selected run cannot hide the search that produced it |
| Cost/capacity fantasy | Conservative component cost model and adverse stress | Gross or zero-impact result cannot promote |

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| Blindly updating golden fixtures hides a regression | Root-cause report and independent fixture-migration review before hash changes |
| Long RR-000008 execution is confused with completion | Verify all frozen work-unit receipts and hashes; never infer from liveness or directory count |
| Promotion changes durable research truth prematurely | Mandatory read-only preflight and explicit confirmation gate |
| Provenance fields become ceremonial strings | Verify supplied artifact bytes and derivation relationships before row access |
| New abstractions duplicate existing publication logic | Reuse atomic publication, canonical JSON, hash, and boundary primitives; add a module only when two implementations require it |
| Stability thresholds are tuned to the first real run | Pre-register policy and retain all rejected trials |
| More bootstrap iterations create false confidence | Calibrate block policy and report effective support/sensitivity, not only iteration count |
| Semantic leakage is claimed “solved” by metadata | Pair declared dependencies with adversarial tests and independent review; document residual limits |
| Full-universe gaps bias experiments | Freeze only approved contiguous scoped intervals; never weaken gates or impute |
| Phase boundaries are silently skipped | Written evidence and verified remote checkpoints through Task 14, then a separate verified Task 15 plan before Phase 5 source code |
| A popular skill injects unreviewed assumptions | Treat skills as checklists; record version and adoption decisions; preregister project-specific policy |
| ML is introduced before a baseline earns it | Require naive and simple baselines, a fixed search budget, and untouched incremental evidence |
| A discovered edge is beta, momentum, volatility, or liquidity exposure | Freeze exposure diagnostics and require residual robustness before promotion |
| Research automation optimizes to the holdout | Freeze the validator and trial budget; use `autoresearch-goal` only before outcome access and ledger every attempt |

## Definition of done

The deterministic hardening programme is complete only when Tasks 1–13 pass their verification
gates and the final Task 13 Phase B evidence checkpoint is accepted. Tasks 14–15 remain later,
sequentially gated empirical work and are explicitly authorized by the 2026-07-19 instruction only
after their prerequisites. The broader goal is not to guarantee an edge; it is to create
a process capable of rejecting false structure and false alpha while identifying any behaviour that
survives reproducibility, stability, untouched validation, exposure adjustment, costs, and
program-level trial accounting. The programme does not become “accurate” merely by completing code
changes; empirical accuracy remains unestimated until enough preregistered Phase 5 trials exist to
report a trial-yield interval.

## Execution handoff

Recommended mode after approval: use a durable goal owner plus coordinated lanes because data
reconciliation, dashboard correction, statistical APIs, and artifact/provenance work are partly
independent but converge on shared full-suite gates.

- **Primary durable path:** `$ultragoal` owns the plan, phase gates, evidence ledger, and stop rules.
- **Parallel execution:** `$team` may coordinate bounded lanes after Task 1 diagnoses the golden
  failure. Do not run multiple implementers on shared discovery files concurrently.
- **Sequential fallback:** `$ralph` only if a single-owner verification/fix loop is explicitly
  preferred.
- **Research follow-up:** `$autoresearch-goal` is appropriate only for the later approved empirical
  calibration deliverable, not for Phase A code repair.

Suggested staffing after approval:

| Lane | Role | Scope | Reasoning |
|---|---|---|---|
| Golden/provenance | `debugger` then `executor` | Tasks 1 and 7 | High; identity drift is cross-file and evidence-sensitive |
| Reconciliation | `executor` + `verifier` | Task 2 | High; durable data state and bounded-memory proof |
| Dashboard | `executor` | Task 3 | Medium; isolated read-only evidence surface |
| Statistics | `architect`, `executor`, `test-engineer` | Tasks 4, 8–10 | High; serial dependence and stability contracts |
| Trial/leakage | `executor`, `code-reviewer` | Tasks 5 and 11 | High; immutable evidence and semantic safeguards |
| Final gates | `verifier` | Tasks 6 and 12 | High; independent completion evidence |

Launch hints after explicit approval:

```bash
# Durable leader-owned execution ledger
$ultragoal docs/superpowers/plans/2026-07-17-experiment-reliability-remediation.md

# Coordinated Phase A execution, if parallelism is selected
omx team 4:executor "Execute Phase A of docs/superpowers/plans/2026-07-17-experiment-reliability-remediation.md; preserve task ownership and stop at the Phase 0 evidence gate"
```

Team verification must return per-task commits/diffs, targeted test receipts, reconciliation
preflight evidence, and unresolved risks. The durable goal owner checkpoints those receipts and does
not mark Phase A complete until the independent verifier confirms the full gate.
