# Task 14 Outcome-Blind Discovery Evidence

## Scope and decision boundary

Task 14 exercised the first real, source-backed outcome-blind discovery programme. It did not read
temporal- or asset-holdout rows, attach outcomes, estimate predictive accuracy, validate an edge, or
construct a strategy. The bounded terminal programme was scientifically **rejected** by its frozen
stability policy. That rejection is a valid empirical result, not an execution failure.

The runtime implementation checkpoint was
`6f49407c3cef96e5753dd26ef81ce9c793116a31`. The reviewed Task 14 close-out and evidence checkpoint
is `5db2705a1cba37ee10d5eb3bd1fa470a5b628e3d`. It was pushed at `2026-07-22T10:02:26Z`; the local
and `origin/agent/research-lab-foundation` SHAs matched exactly. This small evidence-only follow-up
records that observed identity without rewriting the published checkpoint.

## Immutable attempt ledger

Every attempt remains immutable. A later attempt received new programme, snapshot, publication,
normaliser, run, policy, budget, split, regime, and timestamp identities rather than changing an old
receipt.

| Programme | Scope | Terminal receipt | Programme conclusion | Disposition |
|---|---|---|---|---|
| `PG-000001` / `DR-000701` | Obsolete 14-day, 20,160-row attempt | `failed`; receipt `035daaa2e616ff900ca6da2e1d51fe8eb07ea42627104d54264a69680758dbe0` | no vector | Event-publication coverage rejected execution. Preserved as failed. |
| `PG-000002` / `DR-000702` | Obsolete 14-day attempt with excessive motif exposure | `failed`; receipt `af45d41a0d8b483bd10c95b0dc8b93bad0687b95a1ed9b10599019812e1b1a2a` | `inconclusive`; vector `850317ea28d448235d6f04f92531c89ffd94be091ded89850166c4e510268676` | Motif work cap rejected execution. Preserved as failed. |
| `PG-000003` / `DR-000703` | Bounded 16-hour pilot | `rejected`; receipt `28ea9c2ed9c2a82953e1b8883b3ae48af2238d6e8f0dffa87ec1dfdd2193b90a` | no vector | Detector was rejected, then programme publication exposed an incorrect equality check between complete-case work and its larger preregistered reservation. Preserved unchanged. |
| `PG-000004` / `DR-000704` | Fresh bounded 16-hour pilot | `rejected`; receipt `26027e7efc10cd1faa8659230bc8927f539d210eeac423cd1d9696e02fb94366` | `rejected`; vector `bb34e32608c79a5cd74d22abaab3dbc06530deda95f2eff9aceae59bb76664af` | Completed programme evidence. No behaviour advanced. |

The four verified receipts therefore comprise two `failed` and two `rejected` discovery attempts.
There are no completed Phase 5 validation receipts and no strategy receipts.

## Frozen `PG-000004` boundary

- Symbol: `APTUSDT` at `1m`.
- Materialised interval: `2025-02-01T00:00:00Z` to `2025-02-01T16:00:00Z`.
- Discovery: first eight hours; development: following eight hours.
- Holdouts: later temporal interval and `IMXUSDT` identity metadata only.
- Materialised snapshots: 960.
- Maximum input rows per discovery/development partition: 480.
- Motif-window cap: 512.
- Aggregate feature-work cap: 8,628 cells.
- PCA reservation: 2,880 cells.
- Null-control reservation: 1,437 cells.
- Aggregate projection reservation: 4,317 cells.
- Holdout rows accessed: `false`.

The information-cutoff boundary admitted 479 discovery rows and 480 development rows from the 960
snapshots. The complete-case discovery matrix contained 460 rows after a further 19 auditable
warm-up/null exclusions; development contained 480 rows with no complete-case exclusions. Realised
null-control work was therefore
`460 * 3 = 1,380` cells and aggregate projection work was `2,880 + 1,380 = 4,260` cells. Both were
inside their conservative preregistered reservations. Exact arithmetic, budget hashes, and upper
bounds remained fail-closed.

## End-to-end identities

| Artefact | Identity |
|---|---|
| Selected universe | `SU-000704` |
| Preregistration | `PG-000004`; `ecc2807ea6ee97f9ae4a0c9cbec079059d0cf826217603dc1a78bb79da881b2a` |
| Snapshot | `DS-000704`; `eb6c296a92b87ad9071749e18f01699a09ab3d8f71748bf50595f9e6db614905` |
| Feature publication | `FP-000704`; `7002ab50b0274ec139eb6d8bdd85457ba5431ee3c9c3d1c31fd3b7c00e62b7cb` |
| Event publication | `EP-000704`; `8a85487f7985993155efb6b61ff6aed2b6705a2294c0e219786190f81dda7684` |
| Normaliser | `NZ-000704`; `0f5459fa50e2c795e95838f70c07184e9d7e5b25ed8e362a9ea0a8e0e588a352` |
| Trial | `DR-000704`; identity `3460a913d25f2f2f09a653644976dc019dedae1807ede7a83b021352dbea03c2` |
| Reliability vector | `bb34e32608c79a5cd74d22abaab3dbc06530deda95f2eff9aceae59bb76664af` |

Canonical publication and receipt readers reverified checksums and identity bindings. The dashboard
retains `derivation_chain_verified=false`: a browser-safe receipt count is not represented as an
independent proof of arbitrary builder semantics. The programme evidence separately establishes the
bounded, checksum-bound runtime chain and retains the mandatory semantic manual-review limitation.

## Scientific result

`DR-000704` produced 20 outcome-blind motif candidates: 11 passed the motif-specific stability
gate and nine were rejected. The overall detector produced zero frozen behaviours because the
cluster stability policy rejected the detector. Its minimum subsample ARI was `0.154`, nearby
cluster-count ARI was `0.244`, and development asset coverage was zero. Separately, all nine
transition estimates were rejected because their effective supports (`42`, `17`, and `44`) were
below the frozen minimum of 50; several also failed the interval-width rule. Those per-estimate
rejections did not determine the terminal run status. The receipt conclusion is:

> Outcome-blind detector was rejected by the frozen stability policy.

Negative-control and naive-baseline evidence executed inside the trial before terminal rejection.
The immutable `PG-000004` vector nevertheless labels them `unexecuted` because its aggregation code
incorrectly filtered out rejected receipts. A test-first post-run correction now reports verified
execution independently of scientific admissibility; the old vector was not rewritten. No narrative
hypothesis was substituted for the missing frozen behaviour.

The 2025 APTUSDT window was selected using a 2026 freshness/availability audit. It is therefore an
operational pipeline pilot, not a point-in-time survivorship-controlled empirical sample. This is an
additional reason it cannot seed a hypothesis or general recurrence claim.

## Incident and correction record

`PG-000003` proved that complete-case selection can reduce realised projection work below the
reservation frozen over admitted rows. The verifier incorrectly required equality. Test-first
correction made the reservation an upper bound while retaining:

- exact work-budget SHA and frozen limits;
- exact partition projection identity;
- exact `selected rows * components` control arithmetic;
- exact aggregate arithmetic; and
- fail-closed rejection when realised work exceeds either reservation.

Independent specification and code-quality reviews approved this correction before checkpoint
`6f49407c3cef96e5753dd26ef81ce9c793116a31` was pushed. `PG-000003` was not rewritten.

The immutable `PG-000004` preregistration also named `transition_interval_rejection` as though it
were a run-level rule, while the implementation applied the transition policy per estimate. The
post-run default now lists only actual terminal run rules: missingness-policy violation and cluster
stability rejection. The `PG-000004` document remains unchanged and this limitation is explicit.

The first full native-Windows verification then exposed four `MemoryError` failures in
`read_bounded_regular()`: the primitive requested its entire configured maximum in one allocation.
A focused allocation-guard regression reproduced the failure before implementation. The reader now
streams in one-mebibyte chunks, retains the same fail-closed maximum, and also detects growth beyond
the limit while reading. The affected export tests and the complete native-Windows suite passed
after the correction.

## Reviews

| Review | Finding | Disposition |
|---|---|---|
| Implementer self-review | The runtime summary distinguished scientific rejection from execution failure and reported `holdout_rows_accessed=false`. | Accepted. |
| Independent specification review | Reservation semantics and the wholly new `000704` identity set satisfy the Task 14 contract. | Approved; 19 fresh tests passed in the review lane. |
| Independent code-quality review | Exact arithmetic, SHA binding, upper bounds, and immutable attempt separation remain fail-closed. | Approved; 31 fresh tests passed in the review lane. |
| Data/evidence review | Dashboard and status documents must count all four attempts and must not equate receipts with predictive accuracy. | Accepted and implemented in the Task 14 close-out. |
| Adversarial quantitative review | A rejected single-asset, 16-hour pilot cannot support recurrence, accuracy, edge, or cost claims. | Accepted; zero hypotheses advanced. |
| Final independent specification review | Exact PG4 interval, holdout, identity, motif-cap, work-cap and immutable-attempt checks were challenged; stale status prose and evidence dates were checked. | Approved after adding eight hermetic boundary-drift regressions and correcting the evidence date. |
| Final independent code-quality review | Dashboard validation originally under-bound PG4 and incorrectly required the global ledger to remain at exactly four trials. | Approved after exact checkpoint validation, future-validation-ledger compatibility, hermetic tests, and fresh Python/dashboard checks. |
| Independent portability review | Chunked reads were challenged for overflow, exact-limit, zero-limit, negative-limit, descriptor, and link-safety behaviour. | Approved; explicit zero- and negative-limit regressions were added to close the sole non-blocking test gap. |

## Verification record

- Focused Task 14 tests: `192 passed in 64.62s` across artifact I/O, programme CLI/programme,
  reliability, run receipts, canonical export, dashboard evidence, and Phase 4 golden replay.
- Ruff format/lint: `151 files already formatted`; `All checks passed!`.
- Mypy: `Success: no issues found in 81 source files`.
- Full Pytest suite under WSL: `1303 passed, 8 skipped`; 13 PowerShell-runner tests were blocked
  only because the Windows host rejects unsigned scripts reached through the WSL UNC path.
- Complete native-Windows Pytest suite under process-scoped `-ExecutionPolicy Bypass` and a
  disposable Windows virtual environment: `1316 passed, 8 skipped in 255.77s`.
- Package build: source distribution and wheel both built successfully.
- Compose validation: `docker compose config --quiet` passed with non-secret verification-only
  interpolation values; no container or database state was changed.
- Dashboard: `npm ci`, `npm run verify:evidence`, `npm run lint`, `npm run typecheck`, and
  `npm run build` passed. `npm ci` reported four pre-existing audit findings (three moderate, one
  high). `Not-tested:` the dashboard has no configured automated frontend test command.
- `git diff --check`: passed.
- Golden replay: `tests/test_phase4_golden.py` remains in the focused set and generates stable and
  rejected fixtures twice from independent clean roots for byte comparison.
- Cross-platform: the initial native-Windows run found four bounded-read allocation failures;
  allocation-guard TDD plus affected export tests passed (`17 passed`), followed by the complete
  `1316 passed, 8 skipped` native-Windows run above. The WSL UNC execution-policy limitation remains
  environmental and is not described as a passing WSL PowerShell execution.

## Boundary confirmation

- Real outcome-blind discovery: yes, bounded and terminally rejected.
- Final-holdout rows accessed: no.
- Final-holdout outcomes accessed: no.
- Outcomes attached: no.
- Predictive accuracy estimated: no.
- Edge validated or promoted: no.
- Strategy, portfolio, execution, leverage, paper trading, or live trading started: no.
- Generated snapshot, publication, trial, and programme rows committed to Git: no.

## Checkpoint verification

- Task 14 close-out checkpoint: `5db2705a1cba37ee10d5eb3bd1fa470a5b628e3d`.
- Push target: `origin/agent/research-lab-foundation`.
- Push time: `2026-07-22T10:02:26Z`.
- Local SHA after push: `5db2705a1cba37ee10d5eb3bd1fa470a5b628e3d`.
- Remote branch SHA after push: `5db2705a1cba37ee10d5eb3bd1fa470a5b628e3d`.
- Local/remote equality: yes.

## Task 14 decision

Task 14 is empirically complete at an accepted/rejected scientific conclusion. The result is
negative: this frozen detector did not earn advancement. Task 15 must create the separate Phase 5
validation plan before any outcome attachment, and the first Phase 5 implementation remains
baseline-first rather than attempting to rescue this rejected detector.
