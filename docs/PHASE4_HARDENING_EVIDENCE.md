# Phase 4 hardening evidence

## Decision and stop boundary

**Status: ACCEPTED for deterministic Phase 4 software hardening; REJECTED as empirical market,
predictive, validation, strategy, or edge evidence.**

Task 12 evaluated the implementation from independent-review reference commit
`bd9f82b72f0e04a6300ac721607c0188c7f8430f` and completed its evidence/review chain through final
remote stop-gate record `047635865c49a0b3f882fadf16003a05c03b47ac`. This report closes the
original software-hardening checklist only. It does **not** authorize a real discovery run, inspect
final-holdout rows or outcomes, attach outcomes, calibrate thresholds on market data, validate a
behaviour, construct a strategy, or claim an edge.

The original Task 12 execution stopped here before Phase C. The later explicit instructions
authorize Task 13 and sequential continuation into Task 14 only after the Task 13 evidence
checkpoint is pushed and its remote SHA matches local `HEAD`; empirical action remains blocked
until that proof. No database, dump, protected raw dataset, research snapshot, real `DR-*` run, or
final holdout was read or mutated for this gate. The dashboard generator read the committed
reconciliation and freshness publications without mutation.
No real or durable repository trial ledger containing empirical receipts existed to consume or
mutate; the generator verified the empty ledger path read-only, and temporary test ledgers were
exercised to prove terminal accounting.

## Hardening commits reviewed

| Task | Implementation | Review/evidence checkpoint | Remote ancestry at cutoff |
|---|---|---|---|
| Task 7 — concrete provenance | `05248e1` | `ee120d5` | verified |
| Task 8 — structural cluster stability | `6b06012` | `cd7ec74` | verified |
| Task 9 — motif boundaries and stability | `5c2ee0d` | `eb301c7` | verified |
| Task 10 — transition uncertainty policy | `a7f8d5d` | `17ae818` | verified |
| Task 11 — semantic leakage evidence | `bd9f82b` | included in the same reviewed checkpoint | verified |
| Task 12 — verification and evidence | `b69e38973d5a30cacf636a399d0b30d76d794004` | verification evidence commit | pushed; remote ancestry verified |
| Task 12 — independent review closure | `035aa35c50724d2a8638846a2045b69e554f5a8e` | dashboard, plan, and evidence consistency correction | pushed; local and origin matched at this checkpoint |
| Task 12 — final remote stop-gate record | `047635865c49a0b3f882fadf16003a05c03b47ac` | administrative remote-state synchronization | pushed at `2026-07-19T15:00:55Z`; local and remote matched |
| Task 12 — prerequisite review closure | `3cf3721f1ec694b61ec1da78d0b7be24fea93caf` | distinct self/specification/quality review completion | pushed; Task 13 authoritative baseline, local and remote matched |

The Task 12 evidence and independent-review correction commits were pushed after leader
authorization. Local and origin both resolved to
`035aa35c50724d2a8638846a2045b69e554f5a8e` before this final administrative checklist sync.
That remote checkpoint does not authorize Phase C.

The final Task 12 prerequisite audit re-fetched the branch and independently verified local
`HEAD`, `origin/agent/research-lab-foundation`, and the GitHub branch ref at
`047635865c49a0b3f882fadf16003a05c03b47ac`. GitHub reported the corresponding push at
`2026-07-19T15:00:55Z`. No GitHub check runs were attached to that exact SHA; Task 12 therefore has
reproducible local verification evidence but no automated merge-enforcement claim.

## Independent Task 12 review closure

The follow-up review found stale phase-gate presentation rather than an algorithm defect. The
generator, Python tests, Node verifier, browser runtime validator, and committed public evidence now
agree that Phase 0 and original deterministic Phase 4 hardening are complete. Task 13 is the
authorized independent residual-risk audit, and Task 14 is authorized only after Task 13 is
committed, pushed, and verified against the remote branch. The canonical progress snapshot lists
Tasks 7–12 complete, Task 13 in progress, and Tasks 14–15 sequentially gated. The public evidence
was regenerated twice at the same
explicit UTC timestamp; both complete files had SHA-256
`6c88747b4580d106c93ecdff96f7e29264cd619b8605b45eff946dbc340b25e4`.

### Autonomous prerequisite review completion

The later Task 13 prerequisite audit identified that Task 12's implementer, specification, and
code-quality reviews had not been recorded as three distinct stages. Those stages were completed
before Task 13 implementation:

| Review | Scope | Findings and disposition | Result |
|---|---|---|---|
| Implementer self-review | Task 12 commits `b69e389` through `0476358`; changed paths, Lore history, cross-platform test repairs, dashboard consistency, generated-file and secret hygiene | Confirmed the production algorithms were unchanged. Found stale remote-state wording and incomplete final checkpoint chronology; corrected all affected evidence/status surfaces. | accepted |
| Independent specification review | Original Task 12 plan, inherited Task 6 chain, evidence hashes, remote ancestry, stop boundary, and current status documents | Found stale `EXPERIMENT_ACCURACY_OVERVIEW` remote wording, missing final `0476358` push/equality evidence, and stale Phase 0 gate wording. Each was corrected and independently rereviewed. | approved |
| Independent code-quality review | Test repairs, dashboard generator and validators, public JSON, evidence/status consistency, commit hygiene, and prohibited-file scan | Found the same three documentation chronology defects and no production algorithm defect. Corrections passed rereview with no remaining finding. | approved |

Fresh prerequisite-closure verification produced `124 passed in 15.97s` for the Task 12
documentation-sensitive, golden, derived-publication, and motif tests; dashboard evidence
verification again accepted 25 symbols and 26 features. The complete Windows suite at the corrected
worktree produced **1,101 passed, 8 skipped in 147.98s**; retained log SHA-256 is
`1aef236c42ba934cf1979d13403fae6caed2c0c30a923d5e25234206d04e17d9`. No real discovery,
holdout access, outcome attachment, database mutation, or later-phase work occurred.

## Task 13 independent residual-risk verification

### Frozen Task 12 baseline

```text
Current local branch: agent/research-lab-foundation
Completed Task 12 local SHA: 3cf3721f1ec694b61ec1da78d0b7be24fea93caf
Completed Task 12 remote SHA: 3cf3721f1ec694b61ec1da78d0b7be24fea93caf
Local and remote Task 12 SHAs match: yes
Comparison with bd9f82b72f0e04a6300ac721607c0188c7f8430f: 4 ahead, 0 behind
Tracked worktree clean: yes; user-owned .codacy/ and .vscode/ remain untracked
Task 12 Pytest collection count: 1,109
Task 12 targeted-test result: 414 passed; prerequisite closure focus 124 passed
Task 12 full-suite result: 1,101 passed, 8 skipped on Windows
Task 12 dashboard verification result: evidence verification, lint, typecheck, and build passed
Task 12 cross-platform result: Windows suite passed; Linux/WSL AllSigned limitation retained
CI/checks attached to the exact Task 12 SHA: no; GitHub reported zero check runs
```

The exact baseline was pushed and local/remote equality was verified before the canonical plan was
amended or Task 13 production code was changed. No real discovery began.

### Step 1 verdict table

| Area | Verdict | Severity | Evidence | Reproduction | Required action |
|---|---|---:|---|---|---|
| 1. Auction transactionality | **Confirmed** | Critical | `AuctionEngine.update()` mutates policy/profile/cache/tracker before later fallible stages and overwrites a segment/gap reset with `window_reset`. | Fault injection left non-equivalent state after transition, snapshot, node, tracker, event, eviction, and reset-path failures; retry duplicated an active timestamp. | Add complete transactional rollback and preserve simultaneous reset causes. |
| 2. Feature batch state/context | **Confirmed** | High | `build_batch()` consumes hidden builder history and only removes incomplete output on failure. | Identical snapshots produced different first rows after pre-seeding; a row-cap failure left history advanced and made retry reject a duplicate timestamp. | Bind explicit start/input context and restore exact builder state and issuance on every failure. |
| 3. Integer normalization types | **Confirmed** | High | `transform_row()` returns float-normalized values under raw `FeatureRow` and registry identity. | Built-in integer features became floats and failed validation by the unchanged raw registry. | Introduce a separately typed normalized matrix bound to raw matrix and normalizer identity. |
| 4. Behaviour event bindings | **Confirmed** | High | Events, durations, and symbols are parallel positional sequences without row/publication keys. | Same-length reorder and foreign unique event IDs were accepted and changed behaviour medians/IDs. | Use canonical keyed row/event/duration bindings tied to a verified event publication. |
| 5. Fixture trailing provenance | **Confirmed** | High | `volume_change` claims two-observation causality but receives an unlinked scalar `previous_volume` in the same row. | All 18 contiguous fixture links used baseline `1000.0`, not the actual preceding volume; six starts/resets had no warm-up identity. | Truthfully rename as baseline-relative and independently review the identity/golden migration. |
| 6. Leakage approval reconstruction | **Confirmed** | High | Receipt self-hash includes but does not reconstruct `approval_sha256`; underlying evidence artifacts are digest-only. | A receipt with claimed `ff…ff` approval and embedded evidence reconstructing to another digest parsed successfully. | Reconstruct approval during validation and retain the internal-consistency/manual-review limitation. |
| 7. Event-publication linkage | **Confirmed** | High | Event publication accepts no verified source feature publication or receipt. | A publication with arbitrary `ff…ff` approval and no source feature/dependency/receipt identity succeeded. | Require exact verified source feature publication and bind row/partition/receipt/dependency identities before iteration. |
| 8. Structural missingness | **Confirmed** | High | `build_feature_matrix()` records only one aggregate dropped-null count and has no frozen drop policy. | Existing regression drops rows for different selected features but exposes only `dropped_null_rows == 2`; no feature/asset/period/state/cause evidence exists. | Add bounded grouped evidence, selected/excluded identity digests, and frozen reject policy without imputation. |
| 9. Aggregate work budgets | **Confirmed** | High | Several local caps exist, but profile touched bins, rolling bars, matrix/PCA work, run seed/iteration/fits, and dashboard output lack an aggregate preflight. | Config accepted `10^12` iterations and 1,000 seeds; `RollingBars` accepted `10^12`; allocation has no touched-bin bound and PCA runs before K-means cell caps. | Add identity-bound deterministic preflights before allocation/SVD/fits/serialization, reusing existing caps. |
| 10. Automated quality enforcement | **Partially confirmed** | Medium | Task 12 local/cross-platform/dashboard evidence is reproducible, but exact baseline has no workflow/check run and dashboard has no test script. | 1,109 tests collected; Windows executed 1,101 and skipped 8; GitHub check count `0`; workflow count `0`; `dashboard.scripts.test` absent. | Record the explicit enforcement risk; do not mislabel lint/typecheck/build as tests or add unrelated CI infrastructure. |

### Detailed reproduction records

1. **Auction transactionality.** `auction/engine.py:AuctionEngine.update`, every production window
   transition, `profiles/accumulator.py:ProfileAccumulator`, and
   `structure/nodes.py:NodePersistenceTracker` were fault-injected. Contribution/allocator failure,
   fixed-window rejection, and rejecting gap policy were already pre-mutation and preserved state;
   all post-transition failures did not. The defect predates Task 13 and relevant files are
   unchanged from `bd9f82b`. Acceptance is exact collaborator/private-state equivalence and clean
   retry equality for every injected stage, including combined segment/gap plus window reset.
2. **Feature batches.** `features/builder.py:FeatureBuilder.build_batch/update/reset` begins from
   undeclared `_history`, `_latest`, `_last_location`, and `_location_dwell`; batch identity contains
   output/registry identities but not ordered input or start context. The defect predates Task 13 and
   was not introduced after `bd9f82b`. Acceptance is explicit fresh/continuation context,
   input-stream digest, exact rollback, absent incomplete issuance, and retry bytes matching clean
   execution.
3. **Normalization.** `features/normalization.py:RobustNormalizer.transform_row`, strict raw
   validation in `features/registry.py`, and normalization reuse in `discovery/runs.py` were compared.
   `location_dwell_bars` and `max_node_persistence_bars` reproduce the raw-integer/normalized-float
   mismatch. The defect predates Task 13. Acceptance is a normalized type carrying row/order, source
   matrix/input, normalizer/artifact, algorithm, and float values without impersonating raw rows.
4. **Behaviour bindings.** `discovery/behaviours.py:freeze_behaviours` and
   `discovery/runs.py:_run_discovery_implementation` accept positional arrays. Reordered and foreign
   IDs passed length/uniqueness checks. The defect predates Task 13. Acceptance is exact keyed row
   coverage, uniqueness, canonical order, verified publication identity, and a stable binding digest.
5. **Fixture provenance.** `tests/phase4_fixture_producer.py` and the v3 JSON fixture were scanned by
   symbol/timeframe/segment/one-minute continuity. Fourteen discovery and four development links
   mismatch; starts/resets lack warm-up identities. The producer is byte-identical to `bd9f82b`.
   Acceptance is a truthful current-row baseline contract or fully linked causal predecessor.
6. **Leakage receipt.** `data/derived.py:LeakageAuditReceipt/read_leakage_audit_receipt` accepted a
   syntactically valid forged approval after receipt SHA recomputation. Existing bounded parsing,
   symlink rejection, receipt artifact hash, and manual-review flags remain valid. The defect
   predates Task 13. Acceptance is reconstructed approval equality and bounded artifact verification
   when artifacts are supplied; absent artifacts retain an explicit limitation.
7. **Event linkage.** `data/derived.py:publish_market_events/_validate_event_identity` was invoked
   with a valid synthetic event and unrelated approval; publication succeeded with all source
   feature receipt/dependency fields absent. The defect predates Task 13. Acceptance is rejection
   before event iteration on any source publication, registry, dependency, approval, receipt, row,
   or partition mismatch.
8. **Missingness.** `discovery/matrix.py:build_feature_matrix` and its existing null-row regression
   prove complete-case selection but retain only an aggregate count. The defect predates Task 13;
   no missingness policy exists. Acceptance is bounded grouped audit evidence plus frozen maximum
   total/per-feature drop fractions and rejection when exceeded.
9. **Work budgets.** Read-only configuration reproduction accepted `max_iterations=10**12`, 1,000
   seeds, and `RollingBars(max_bars=10**12)`. Static tracing found no touched-bin allocation cap and
   PCA SVD precedes K-means cell validation. Existing motif, transition, feature-record, derived
   publication, and bundle read caps are already fixed. Acceptance is pre-allocation rejection for
   each confirmed uncovered path, with every admissibility-changing limit in configuration or
   artifact identity.
10. **Quality enforcement.** `pytest --collect-only` reported 1,109 tests; the corrected Task 12
    Windows execution reported 1,101 passed and eight PostgreSQL skips. GitHub returned zero checks
    on `3cf3721`; no `.github/workflows` file or dashboard `test` script exists. This is not an
    algorithm defect. Acceptance is truthful local verification evidence and an explicit unresolved
    merge-enforcement/frontend-test risk.

### Task 13 corrections and focused verification

The confirmed findings were corrected without adding dependencies or accessing market data:

| Area | Correction | Focused proof |
|---|---|---|
| Auction transactionality | Window policy and profile accumulator now retain reversible journals until every collaborator commit and engine-field assignment succeeds; finalization is non-fallible. Active profile contributions and aggregate contribution cells are identity-bound and restored on rollback. | Fault injection covers contribution, allocation, eviction, snapshot, node detection/tracking, structural events, resets, both post-commit faults, and deterministic clean retry. |
| Feature batch state | Batch construction requires a fresh builder, binds the ordered input stream and starting context, restores exact state and issuance on failure, and enforces row, record, and aggregate-byte limits. | Hidden pre-seed, partial-write, cap rejection, revoked issuance, retry, and replay regressions pass. |
| Normalization type boundary | Raw `FeatureRow` remains registry-validated; normalized floats are carried only by `NormalizedFeatureMatrix`, bound to source matrix, feature order, normalizer, and algorithm identity. | Integer features transform without weakening raw integer validation or masquerading under the raw registry. |
| Behaviour/event bindings | `BehaviourEventBinding` joins canonical `row_id`, canonical SHA-derived event ID, positive duration, and verified event-publication SHA; validation is keyed, coverage-exact, duplicate-safe, and order-independent. | Reorder, missing, extra, duplicate, foreign-publication, semantic-swap, and filtered-row cases pass. |
| Fixture provenance | The inaccurate trailing `volume_change` contract is now the truthful contemporaneous `volume_deviation_from_baseline`; no preceding-row claim remains. | Registry/source schema, producer, feature, behaviour, motif, interpretation, and dashboard identities were independently migrated and reviewed byte-by-byte. |
| Leakage receipt | Receipt validation reconstructs `LeakageAuditApproval` from embedded schema, registry, reviewer, review artifact, field-test contracts, and negative-test evidence before comparing the approval SHA. | Forged caller-supplied approval digests and changed receipt/artifact identities are rejected. |
| Event publication provenance | Event publication requires and verifies the exact feature publication, registry, dependency contract, approval, receipt logical/artifact identity, partitions, and source rows before event iteration. | Unrelated or incomplete source identities reject before publication; bounded source indexing scans each partition once. |
| Missingness | `MissingnessPolicy` freezes total/per-feature drop limits and evidence groups; matrices publish selected/excluded identity digests and grouped feature/asset/period/state/continuity evidence without imputation. | Policy excess rejects before model work and is retained as an explicit terminal result. |
| Aggregate work | Deterministic budgets now cover touched/active profile cells, window capacity versus cadence, feature batches, derived publication rows/partitions/bytes, matrices/PCA, clusters/seeds/iterations/fits, exact trial bundles, and dashboard bytes. | Pathological configurations and sized inputs reject before expensive provenance scans, allocation, SVD/fits, or filesystem expansion; unsized publication streams remain runtime bounded and may finish earlier partitions before their terminal count is known. |
| Quality enforcement | Repository-native verification remains reproducible locally; dashboard evidence, lint, typecheck, and build are distinct checks. | No dashboard `test` script and no exact-SHA required GitHub checks exist; this remains an explicit enforcement limitation, not a test-pass claim. |

The first independent specification review found seven blockers: post-commit rollback, derived
publication bounds, incomplete golden/dashboard migration, missing leakage occurrence wording,
standalone binding validation, stale authorization wording, and long-window admission. The first
independent code-quality review found eight blockers: discovery preflight ordering, derived buffer
bounds, multiplicative event scans, aggregate profile cells, aggregate feature-batch bytes,
stability-fit iteration mismatch, incomplete exact bundle accounting, and positional event
verification. Every finding received a focused reproduction and correction. The fixture reviewer
then blocked the first proposed hash migration because it omitted Task 13 missingness/work-budget
identity; the harness was corrected, regenerated twice, and independently approved before any
expected hash was changed.

### Task 13 three-part review record

The reviews below are distinct execution stages. Each finding was reproduced against the Task 13
tree, dispositioned on repository evidence rather than reviewer authority, and followed by the
smallest affected regression before the next review pass.

| Review | Reviewed scope | Findings and severity | Disposition and evidence | Residual risk |
|---|---|---|---|---|
| Implementer self-review | All Task 13 production/test changes, derived identities, golden fixtures, dashboard contract, plan/status/evidence text, prohibited-file boundaries, and the final diff | **High:** a collaborator failure after transaction commit could bypass complete rollback; release faults both after and before journal close could convert a successful commit into either a caller-visible failure or an attached journal that broke the next update. **Medium:** the first golden migration omitted new missingness/work-budget identities. | Corrected. Accumulator/window journals retain undo state through validated finalization; release cleanup is non-throwing and force-detaches even when the normal release callable faults before close. Fault injection proves exact rollback/retry and a successful subsequent update. The golden harness now freezes and publishes both new policy identities. | Python cannot make an arbitrarily monkeypatched force-detach primitive infallible, but the production force-detach operations are bounded local clear/detach assignments with no I/O or user callback. |
| Independent specification review | Exact Task 13 acceptance text; auction, builder, normalization, binding, fixture, leakage, publication, missingness, budget, quality, authorization, and stop-gate requirements | **High:** post-commit rollback was incomplete; derived publication limits were incomplete; event binding could be validated without the publication; long-window admission could exceed capacity. **Medium:** golden/dashboard migration, leakage occurrence wording, and authorization wording were incomplete or stale. | Corrected and regression-tested. Transaction journals remain reversible through all pre-swap stages; derived rows/partitions/bytes are bounded; binding construction requires the verified publication; cadence-aware capacity rejects at engine creation; dashboard/goldens were independently migrated; the receipt limitation and Task 14 sequencing are explicit. | Final closure still depends on the recorded full verification, Task 13 Lore checkpoint, push, and local/remote equality. |
| Independent code-quality review | Changed production paths and tests, with adversarial complexity/preflight/fault injection rather than fixture-hash agreement | **High:** expensive provenance work preceded deterministic limits; unsized derived buffers and event-source scans could multiply work; aggregate profile cells and feature bytes were not capped; stability fit counts and exact bundle accounting diverged; positional event verification remained possible; finalize/release fault paths leaked transaction state. | Corrected and rereproduced. Limits now execute before expensive work where cardinality is knowable, streaming paths remain runtime-bounded, partition indexing is one-pass, aggregate budgets are frozen, fit/bundle arithmetic is exact, event joins are keyed, post-finalize failure rolls back, and pre/post-close release faults leave the next update operational. | Unsized streams cannot know terminal cardinality before iteration and may commit earlier bounded partitions before a later item causes runtime rejection; this is explicitly documented rather than called preflight. |
| Independent fixture-migration review | Phase 2 profile identity and Phase 4 registry/config/run/event/interpretation/dashboard identity cascades, including old-versus-new byte maps | **High:** the first proposed Phase 4 migration did not bind missingness evidence or the discovery work budget. | First migration rejected. Harness corrected; two new clean roots matched by every filename and byte; unchanged statistical artefacts were proved byte-identical; only diagnosed contract/identity/evidence bytes changed. The final Phase 2 and Phase 4 migrations were approved before expected hashes changed. | The fixture thresholds remain software-contract examples, not empirical research thresholds. |

The final independent specification rereview approved the corrected tree after **392** exact focused
tests and found no remaining code or documentation defect. The final independent code-quality
rereview approved after **480** focused tests, Ruff format/lint, Mypy across 78 source files,
`git diff --check`, and the dashboard evidence/lint/typecheck/build chain. Its adversarial
release-before-close reproduction left no attached journal and the next auction update succeeded.

The independently reviewed final Phase 4 migration preserves every numerical/statistical result.
Stable and rejected `projection.json`, `stability.json`, and `transitions.json` are byte-identical to
Task 12; motif numerical evidence and cluster assignments/centroids/inertia are unchanged. Changed
bytes are limited to the truthful feature name, registry/config/run identity cascades, canonical
event bindings, explicit missingness/work-budget evidence, and corresponding interpretation and
dashboard identities. The frozen missingness policy selected all 16 discovery and eight development
rows, with zero exclusions. Its SHA-256 is
`724a84e667e8bfa0d9d1586929f90bd47850b7cef6cc64ea404f75d611bc733b`.
The work-budget SHA-256 is
`fae49bd65c767128dbdb5fc26061c60c2c79b0693ab5ad902435ed9e3669f2d7`.

Task 13 focused evidence available before the final full-suite gate:

- consolidated state/provenance/budget suite: **565 passed in 46.52s**;
- independently migrated Phase 2 replay: **4 passed**, with stream SHA-256
  `e6cbfec8062f48ac4dc3552aa7416bfc2fa6f3ca37810161bc4129da4f7b06b3`;
- final Phase 4 golden suite: **7 passed in 6.09s**;
- dashboard Python contract: **32 passed in 6.46s**;
- dashboard `npm ci`, evidence verification, lint, typecheck, and production build: passed;
- dashboard automated test command: **Not-tested: dashboard has no configured automated test command.**

## Task 12 defects found by the original gate

The gate found two narrow test-surface defects and no production algorithm defect:

1. `ruff format --check .` identified one non-canonical layout in the Task 9 regression file
   `tests/test_discovery_motifs.py`. Ruff reformatted that file; no assertion or behaviour changed.
2. The first Windows-native complete suite reached a Task 11 provenance test that wrote `_SUCCESS`
   with `Path.write_text`. Windows translated LF to CRLF, so verification correctly rejected the
   marker before reaching the intended receipt-binding assertion. The test now writes the canonical
   ASCII bytes directly. The specific regression passed on Linux and Windows before the full Windows
   suite was rerun.

Neither repair loosens a verifier, changes a research threshold, changes fixture outputs, accesses
market data, or changes a production API.

## Verification commands and exact results

Logs are local generated verification evidence under `.omx/logs/` or `/tmp`; they are deliberately
ignored rather than committed as repository artefacts.

| Command | Result | Log SHA-256 |
|---|---|---|
| `uv sync --locked` | passed; 120 packages resolved, 117 checked | `8d7d5a75c37bbf4cfdb65b094b617a665986b13cbd109a1b40e74c9e4afb102d` |
| `uv run ruff format --check .` before repair | rejected one Task 9 test file; gate did not waive it | `c73c26653766953ee2c8d72b4de77b7b5e6bbe1388b83f8be22cc2e69bc12889` |
| `uv run ruff format --check . && uv run ruff check .` after repair | passed; 144 files formatted, all lint checks passed | `98a81ced92fce627b772be5b971fb63decf87ed7166ee3e54503023bc14d1de4` |
| Phase B targeted Python suite | `414 passed in 75.02s` | `cad5c1addd23dd698c6a5cf5d546eb35db4ec26501c088987a27b7170f936be0` |
| Trial-accounting suite | `50 passed in 5.35s` | `c18037668615353cd5b52f6808e11abcc80f5bfa2dd500a4a37b9f7ba9171ee7` |
| Holdout/leakage pre-consumption sentinels | `10 passed in 18.39s` | `87e3a4d8f211c2ba835f70afcf35abb5f69f966163f5802f1146ea553bbc4805` |
| Bounded-run/search/bootstrap guards | `13 passed in 19.86s` | `bd834c48b30820bb033dd2f6924710d20aa9e4ca4be365d8e05118b664d9b29e` |
| `uv run pytest -q` in WSL | completed: 13 AllSigned policy failures, 1,087 passed, 8 skipped in 154.74s | `c367ad9f0e91d726e923b7e60b1dad15d3d77a4be990044cbcd777461a9cd261` |
| First Windows full suite with process-only bypass | reached all tests: 1 failed, 1,099 passed, 8 skipped; exposed the CRLF test defect | `31d6a837914ed2d34b5623898234aa4b2ea3473aaaf2ff19debce40d8a20d4ca` |
| Corrected provenance regression on Linux | `1 passed in 12.48s` | `4be525f3a41b04b95ac25e719f209697a11ed133002e90f3e736b22c97390376` |
| Corrected provenance regression on Windows | `1 passed in 5.27s` | `e59fccd07accec1e19d41e9c56093becb020593c08a1602a182ed2373c67a23d` |
| `powershell.exe -NoProfile -ExecutionPolicy Bypass ... pytest -q` from `D:\market-structure-lab` | **1,100 passed, 8 skipped in 272.04s** | `96fa878bdc9e527ceaf10696e05cf396ef8ae6a1c09170084d159728655e4ae7` |
| `uv run mypy src` | passed; no issues in 78 source files | `8707af011cac965901023609ab97de11c08f549edf305f8304cbb0121d700dbd` |
| `uv lock --check` | passed; 120 packages resolved | `19f2256c651d4ef8abeb5b7dea9deb25fd920ffd885da7e2e839dffca69b48a1` |
| `uv build` | passed; source distribution and wheel built | `186e5dd79aaceea230c85eae1967e481bcc78d2d63521aed863a943683fa6317` |
| `POSTGRES_PASSWORD=... PGADMIN_DEFAULT_PASSWORD=... docker compose config --quiet` | passed with non-secret process-only check values | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| Dashboard evidence verification, lint, typecheck, and build | `passed; evidence contract verified (25 symbols, 26 features), lint/typecheck clean, production build completed` | `842b0c0f3612a26dcdc506faa27576d00a8ac900c71d3d2aff388a10d1bbc5f8` |
| Corrected dashboard evidence generation twice at `2026-07-19T14:36:43Z` | passed; both complete files were byte-identical | `9602efdd6e681514d300eb36d5b89e501c64fae4de4ef817039ea83b2d144d34` |
| Corrected dashboard contract verification, lint, typecheck, and build | passed; public contract verified and production build completed | `af4c357edeed4b45d0ce160bf4659318bd39382f865a99afc67def04379f3b8a` |
| `git diff --check` before documentation | passed | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| Final Ruff, 81 doc-sensitive/provenance tests, Mypy, lock, and diff validation | passed; 144 files formatted, 81 tests, 78 typed source files | `a3c9ce0021bc8af0f1e43dfa7e1ff6ef94c5505bed8e171bc0b91233b17fac6d` |
| Follow-up Ruff, 82 doc-sensitive/provenance tests, Mypy, lock, and diff validation | passed; 144 files formatted, 82 tests, 78 typed source files | `c9764e0b8f33c65fd97b9ced8cd56cbeae57dfb5bd503933b8906d53adda5bf6` |

The WSL failures were not treated as passes. Every failure was in
`tests/test_daily_candle_refresh_runner.py` and reported that a temporary PowerShell script was not
digitally signed under the host's `AllSigned` policy. The complete suite was therefore executed from
the Windows checkout with a non-persistent, process-level `-ExecutionPolicy Bypass`; all executable
tests passed there. The eight skips are the existing isolated PostgreSQL integration profiles for
which `MSL_TEST_POSTGRES_URL` was not supplied. Task 12 performs no database mutation.

Built artefacts were local and ignored:

- sdist SHA-256: `a0e19ca98c29cb2829508b047fcd777ce31c7956e2f9aca0c8021e22d8c39192`;
- wheel SHA-256: `6b9a9d1e095c5c97332f107e1e2a3c1403e6994305f4b51fab9b9bb7658543a1`.

## Task 12 independent clean-root replay

A standalone verifier created two independent empty temporary roots for each run, replayed through
the real Phase 4 golden APIs, enumerated every regular file, compared bytes by relative name, and
hashed a canonical map of every file SHA-256 and byte length. The verifier log is
`.omx/logs/phase4-golden-byte-replay.json`, SHA-256
`737196109e1eb8262526860d25f845e920dc79c2fc9a602b749228052d71efb0`.

| Publication | Files compared | Every byte identical | Bundle-map SHA-256 |
|---|---:|---|---|
| stable discovery `DR-000601` | 10 | yes | `7011c3ade5bcad7b43a77069c55fecbf24541083bec31ae0b0ebfc99abac3315` |
| rejected discovery `DR-000602` | 10 | yes | `a688e3adefb774347090991a0cc294bd47cddb508adf8b25b28667e697efac45` |
| stable run plus interpretation publication | 13 | yes | `131351adaf3cfae341b8cb6db2ad05609036f5e336550d6e01525b0e7bfb7b1b` |

This is stronger than comparing only manifests. It compared config, projection, clustering,
stability, behaviour, motif, transition, metrics, summary, run manifest, evidence,
interpretations, and interpretation-manifest bytes as applicable.

Task 12 committed software-fixture identities were:

- fixture file: `2fa71a218c6066a165c222a7c1d0386cb9bb3cb6e02cd309d9156871bcb0ed07`;
- stable manifest: `eb09fe6c3773bb7454626701495b5c0674beb09d22ac9fbe729da08faf4b872b`;
- rejected manifest: `700f2a95b5ac01efc9ce582c826022071ed9f27517553e849e0c867bd6e918f0`;
- interpretation input: `9a695a20ca0e7757de142f4c6508c2955e9d1849a36dcce331559d9f34d65cec`;
- interpretation response: `d7a965d9b8c40c4af760e665c88d84f26001f79c5df515f7587f47556acfa1ec`;
- interpretation publication manifest:
  `b10edc9c5d99ed28468bbd97cdac9f0dba60a98a49e44b4bc408046b0a385119`;
- generated dashboard evidence:
  `6c88747b4580d106c93ecdff96f7e29264cd619b8605b45eff946dbc340b25e4`.

## Task 13 independently reviewed clean-root replay

After the first fixture review blocked an incomplete identity migration, the final harness froze
the missingness policy and discovery work budget, emitted `missingness.json`, and included both
identities in config, metrics, run identity, and manifests. Two separate clean output roots then
matched by every filename and byte. A separate pair including the interpretation publication also
matched completely.

| Publication | Files compared | Every byte identical | Bundle-map SHA-256 |
|---|---:|---|---|
| stable discovery `DR-000601` | 11 | yes | `e63396623fac3306ad78ff24ac2778298cb2ec137ddffc80a0d4ddb65475772b` |
| rejected discovery `DR-000602` | 11 | yes | `7fad89fba024c9f5d8b56182262229f99fdf6572b459f73d3df7b1f95eca9ec6` |
| stable run plus interpretation publication | 14 | yes | `2799c65d8f0e0bfacd502b882821022502ed0445a4d41761066dc87e9f6ba8fb` |

Task 13 reviewed fixture/publication identities are:

- Phase 2 replay fixture: `0188b91a55d41a7f1fac271e83bfdaf477d0187a9a62180c311ac3eceb657816`;
- Phase 4 fixture: `f516650c962c87a22466b5d986321666daeeb6464ca122bc3c14aac82eff61d5`;
- stable manifest: `404da0b473a6b3d065c9ce009b37cb89d91e1394c28382b64854901e1295d27e`;
- rejected manifest: `4ca6d4b6b3ca8e89b56c853f1b4235338ee5fa92d9b536e9cb3e07057ed371e9`;
- interpretation input: `9f6b8bb56143b1ce6298d5e409fff41d50bbd1bb10cd5901bf0fbcf455d3aae6`;
- interpretation response: `ca49bc5572d22042723891f3e1047e6812f8e4595f6ce9e04fae793e32eb4577`;
- interpretation publication manifest:
  `57959a105814d8e090f5353cb571c292cf3c8a4dc69806ba20a8a8381439937f`;
- generated dashboard evidence at `2026-07-19T18:28:44Z`:
  `afa9eddaf4d4bffdca88327896537740a5710088d514d72c40fa8eecc20d1287`.

## Task 13 final pre-checkpoint verification

The final verification below was run after the release-before-close correction and both independent
rereview approvals. Logs are ignored runtime evidence under `.omx/logs/task13-final/`; their hashes
are recorded so the local command evidence can be distinguished from commit-message claims.

| Command / gate | Exact result | Log SHA-256 |
|---|---|---|
| Exact Task 13 focused suite | **392 passed in 36.19s** | `8f178abdd778099ac0821ccc3b7fefe6385e2158ca87224128d951273db9a0c2` |
| `uv run pytest --collect-only -q` | **1,250 tests collected in 3.59s** | `aed2c2100a6777bacde6fe283231b229b19b99680bf85ee12ee7bc77e858215b` |
| `uv sync --locked` in the repaired default environment | passed; 120 packages resolved and 117 checked | `2f7745205d8d2f2ce3a0e47cfcb72d33c8d34989343c82f3a930c3eabc0b3273` |
| Ruff format/check, Ruff lint, Mypy, `git diff --check` | passed; 144 files formatted, all lint checks passed, no issues in 78 source files | `0d68be2c284fbc2f17473a3bf0914565a8d7056d3227eb5d236a525eac03effe` |
| Complete Linux/WSL suite | completed: **1,229 passed, 8 skipped, 13 failed** solely because the Windows host's `AllSigned` policy rejected unsigned temporary PowerShell fixture copies | `6e843e07ccb659dc5c8d96ed2ff9e5ef548ae970fe5eae73983cd55ca1fb0cd9` |
| Complete Windows-native suite with process-only `-ExecutionPolicy Bypass` | **1,242 passed, 8 skipped in 242.77s** | `9fd43ce1f8bf79fd3ca2aa69680af70fbdc110a1829217d4d99f58dee0b8c07b` |
| `uv build` against a clean temporary copy of the exact tracked worktree | source distribution and wheel built successfully; the clean copy avoids WSL/NTFS traversal of ignored environments and dashboard dependencies | `6dc573a6274dcfcaa5e4188b91cf2bb8cc082c55149efdf45602f0f1d8ddb281` |
| `docker compose config --quiet` with non-secret process-only check values | passed | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| Dashboard `npm ci`, evidence verification, lint, typecheck, build | passed; evidence verified 25 symbols and 26 features; production build completed | `b139255ebd54fe8e659e3d0c6500979e6f45c706ff2fc6aa140601f3fb77bdb6` |
| Two final Phase 4 clean-root replays | **7 passed twice**; 69 regular artifact files matched filename-by-filename and byte-for-byte; comparison map `d979260607434afca541522404eb454b58eb4652bd9c98d171c951122097febc` | `436f53f3794ae6c422c2aa8950a67669e2409dd0433340b0ded04aa7c0afb0d3` |

The two pytest `current` symlinks point to their respective temporary roots and were correctly
excluded as runner metadata; every regular publication/evidence file was compared. The eight skips
are isolated PostgreSQL integration profiles because no disposable `MSL_TEST_POSTGRES_URL` was
supplied. Task 13 changed no database implementation or source data.

**Not-tested:** the dashboard has no configured automated test command. Evidence verification,
lint, type checking, and build are not described as frontend tests. **Not-tested:** no required
GitHub Actions workflow or exact-SHA continuous merge gate exists in this repository; this remains
an explicit enforcement risk rather than a local-pass claim. **Not-tested:** the Codacy MCP analysis
surface was unavailable in this session; no dependency was added and Ruff, Mypy, tests, build, and
the independent reviews are the repository-native evidence.

Task 13 implementation checkpoint SHA: **pending commit**. The exact pushed SHA and remote equality
will be recorded in the evidence-only closure checkpoint before Task 14 begins.

## Manual integrity review

### Bounded memory and work caps — accepted

Feature publication uses fixed Parquet row buffers and bounded control-artifact readers. Normalizer
fitting externalizes sorted runs under `max_rows_per_run`. Discovery input has an explicit row cap;
PCA/K-means use bounded rows and iterations. Motif policy caps windows, axes, universes, parameter
variants, and retained candidates. Transition estimation rejects excess serialized estimates,
stored bootstrap samples, or conservative draw work before bootstrap execution. The focused
13-test bounds suite passed. No unbounded market-data collection was introduced.

### Holdout and outcome non-access — accepted

`make_discovery_input` rejects a holdout partition before calling `iter()` on the supplied rows;
its exploding-iterator tests passed for fit and stability purposes. Direct `DiscoveryInput`
construction is sealed. The Phase 4 fixture contains holdout metadata but no `holdout_rows`, and the
interpretation pack contains no holdout content or future outcome. Feature leakage approval and
semantic dependency checks execute before producer replay, as proven by the 10 focused sentinels.
No final-holdout path was opened during Task 12.

### Concrete provenance linkage — accepted for software, not empirically exercised

Factory-created `DiscoveryProvenance` binds the checksum-verified snapshot manifest, derived publication,
feature registry and dependency contract, fitted normalizer, selected feature order, split, clean
code commit, and lockfile. `run_discovery` authenticates the concrete artefacts and their rows before
matrix construction; changed bytes, partitions, registry, normalizer, commit, lockfile, or feature
order fail first. This contract passed its targeted regressions. Task 12 did not create or bless a
new market snapshot, so no claim is made that a real source-backed derivation chain has run.

### Cluster and motif stability — accepted as a software contract

Cluster stability separately records seed, unstratified deterministic subsample, adjacent-period,
asset, and parameter evidence. Adjacent-period checks include frequency JS distance, centroid
displacement, within-cluster scale change, assignment-margin drift, and explicit per-cluster event
support. Motifs use boundary-safe multivariate sequences and retain seed/tie, subsample, nearby
parameter, asset, period, and frozen outcome-blind regime evidence. Rejected motifs remain recorded;
they cannot support published recurrence evidence. Fixture thresholds are software pass/fail values,
not calibrated research thresholds.

### Sequence and transition safety — accepted

The shared contiguous-boundary contract splits or rejects null-row gaps, timestamp gaps, session,
symbol, timeframe, segment, duplicate timestamp, out-of-order timestamp, and non-contiguous
information-cutoff changes. Transition input is dwell-compressed within those boundaries only. The
uncertainty policy freezes horizon, bootstrap seed/iterations, confidence, block rule/value,
effective support, interval-width handling, and nearby block sensitivity into run identity.
Published transition language is `conditional_recurrence_estimate` with `descriptive`,
`descriptive_only`, or `rejected` evidence status. No p-value, independent-minute significance,
return prediction, promotion, or edge claim is exposed.

### Leakage evidence and manual-review limit — accepted with retained limitation

Each registered discovery feature hashes its source fields, trailing window, cutoff rule, warm-up,
normalization requirement, future/outcome prohibition, and exact builder identity. The independent
receipt binds reviewer evidence, adversarial negative tests, producer output, serialized
publication, and dependency contracts. `metadata_proves_no_leakage` remains false and
`residual_manual_review_required` remains true. This gate confirms that the declared contract is
bound and tested; it cannot mathematically prove that arbitrary source declarations or builder code
are truthful. The receipt proves internal identity consistency but does not independently prove
that the claimed review occurred. Human code review remains mandatory.

### Trial accounting — accepted, empirically empty

The canonical ledger retains completed, rejected, failed, inconclusive, and abandoned terminal
receipts atomically and immutably. The 50-test accounting suite passed. The repository has no
verified real trial directory and the dashboard reports all four experiment modes and all five
terminal statuses as zero. The software fixture is explicitly excluded from real-trial counts.
Experiment yield and accuracy remain `not_estimable`.

### Secrets, generated files, and Phase C absence — accepted

Review found no new credential, dump, database-volume, cache, generated run directory, or large
tracked binary. Build and dashboard outputs remain ignored. Pre-existing untracked `.codacy/` and
`.vscode/` files were not modified or staged. No `backtest`, `portfolio`, strategy, exchange,
execution, validation, or real discovery implementation was added; no real `DR-*`, Phase 5 outcome,
or strategy artefact exists.

## Residual risks and limitations

1. All Phase 4 evidence remains synthetic software-fixture evidence. There is no empirical behaviour
   recurrence, detector accuracy, transition reliability on market data, predictive validity,
   profitability, cost-adjusted expectancy, or edge evidence.
2. The fixture policies demonstrate deterministic pass/reject plumbing; their numeric thresholds
   are not justified market-research thresholds and must not be reused without preregistration.
3. Static leakage metadata plus tests cannot prove arbitrary implementation semantics. Independent
   source/builder review remains required for every admitted feature.
4. A new checksum-bound research snapshot and feature publication must be frozen during authorized
   Task 14 after the verified Task 13 remote checkpoint. Existing old snapshots cannot be relabelled
   as eligible.
5. The final holdout remains untouched and unavailable to discovery. Phase 5 requires a separate
   approved validation plan.
6. Eight database integration profiles remained skipped because no disposable PostgreSQL URL was
   supplied. Phase B changed no database code and the complete non-database Windows suite passed.
7. Task 14 is authorized only after the Task 13 remote checkpoint is verified. Outcome attachment
   and every later research gate remain separately approval-gated.
8. Sized derived-publication inputs receive deterministic cardinality preflight. Unsized iterables
   enforce the same frozen row/partition/byte bounds during streaming, but may write completed
   partitions before a later item proves that the total bound is exceeded. This is bounded runtime
   rejection, not a claim of zero filesystem expansion for an unknowable terminal cardinality.

## Gate checklist and conclusion

- [x] All Task 7–11 targeted tests passed.
- [x] The complete repository suite passed on Windows under a process-only policy bypass.
- [x] Stable, rejected, and interpretation publications replayed byte-identically from independent
      clean roots.
- [x] Boundedness, holdout non-access, provenance, trial accounting, sequence boundaries,
      descriptive terminology, and no-edge claims were manually reviewed.
- [x] Documentation and dashboard evidence use the committed fixture identities.
- [x] No Phase C work, market run, holdout access, database mutation, or strategy work occurred.

**Conclusion:** Task 13 deterministic software hardening is accepted subject to the final
verification, Lore checkpoint, push, and remote-SHA equality recorded below. Empirical validity is
not implied. The latest instruction authorizes Task 14 only after that exact remote checkpoint; no
real discovery may start before it.
