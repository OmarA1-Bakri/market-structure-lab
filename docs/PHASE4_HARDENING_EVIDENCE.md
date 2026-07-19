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

Execution stops here for explicit approval before Phase C. No database, dump, protected raw
dataset, research snapshot, real `DR-*` run, or final holdout was read or mutated for this gate. The
dashboard generator read the committed reconciliation and freshness publications without mutation.
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
agree that Phase 0 and deterministic Phase 4 hardening are complete and that explicit approval is
required before Phase C real outcome-blind discovery. The canonical progress snapshot lists Tasks
7–12 complete and Tasks 13–14 approval-gated. The public evidence was regenerated twice at the same
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

## Defects found by the gate

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

## Independent clean-root replay

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

Current committed software-fixture identities are:

- fixture file: `2fa71a218c6066a165c222a7c1d0386cb9bb3cb6e02cd309d9156871bcb0ed07`;
- stable manifest: `eb09fe6c3773bb7454626701495b5c0674beb09d22ac9fbe729da08faf4b872b`;
- rejected manifest: `700f2a95b5ac01efc9ce582c826022071ed9f27517553e849e0c867bd6e918f0`;
- interpretation input: `9a695a20ca0e7757de142f4c6508c2955e9d1849a36dcce331559d9f34d65cec`;
- interpretation response: `d7a965d9b8c40c4af760e665c88d84f26001f79c5df515f7587f47556acfa1ec`;
- interpretation publication manifest:
  `b10edc9c5d99ed28468bbd97cdac9f0dba60a98a49e44b4bc408046b0a385119`;
- generated dashboard evidence:
  `6c88747b4580d106c93ecdff96f7e29264cd619b8605b45eff946dbc340b25e4`.

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
are truthful. Human code review remains mandatory.

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
4. A new checksum-bound research snapshot and feature publication must be frozen after explicit
   Phase C approval. Existing old snapshots cannot be relabelled as eligible.
5. The final holdout remains untouched and unavailable to discovery. Phase 5 requires a separate
   approved validation plan.
6. Eight database integration profiles remained skipped because no disposable PostgreSQL URL was
   supplied. Phase B changed no database code and the complete non-database Windows suite passed.
7. The Task 12 remote checkpoint is complete. Real empirical discovery and every later research
   gate remain separately approval-gated.

## Gate checklist and conclusion

- [x] All Task 7–11 targeted tests passed.
- [x] The complete repository suite passed on Windows under a process-only policy bypass.
- [x] Stable, rejected, and interpretation publications replayed byte-identically from independent
      clean roots.
- [x] Boundedness, holdout non-access, provenance, trial accounting, sequence boundaries,
      descriptive terminology, and no-edge claims were manually reviewed.
- [x] Documentation and dashboard evidence use the committed fixture identities.
- [x] No Phase C work, market run, holdout access, database mutation, or strategy work occurred.

**Conclusion:** Phase 4 deterministic software hardening is accepted. Empirical discovery readiness
is not implied, and a real discovery run is not authorized. Stop before Phase C and wait for explicit
user approval.
