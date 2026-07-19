# Phase 0 Remediation Evidence

## Decision

**Status on 2026-07-18:** RR-000008 is complete, explicitly approved, atomically promoted, and
post-promotion verified. The active reconciled view is bound to the checksum-pinned RR-000008
candidate. Phase 0 and the original deterministic Phase 4 Task 12 checkpoint are complete. Task 13
is the authorized non-empirical residual-risk audit; real discovery, validation, strategy, and
trading remain separately approval-gated and have not started.

The approved promotion appended one immutable promotion row and 285 verified-coverage rows. No
command changed the immutable dump, reset PostgreSQL, dropped durable schemas, interpolated
unavailable minutes, or crossed into a later research phase.

## Frozen identities

| Identity | Value |
|---|---|
| Branch | `agent/research-lab-foundation` |
| Handover Git checkpoint | `b5a76527ab47e28d18303dfa803c92008c6d690a` |
| RR-000008 run manifest logical SHA-256 | `1eaa2909e0f564490ebf65fe35027e588d2b46b135c52f1ed9d16f38fbbd2629` |
| RR-000008 run file SHA-256 | `2bc951e30007f9d3897c7de54e8ffd8bc73193e377149e60f6416ed87839b7ca` |
| Frozen runner commit | `52eb43276c10e9479d03b5469d4912046728b739` |
| Frozen `uv.lock` SHA-256 | `a16b349b97b4c265129df59092f69981d9150a131772b8ed9f9ebda28dcaf44b` |
| Frozen Windows Python SHA-256 | `e6c07188984d41b80f822dcdaeaceeb217cdf977f18f79c421a3a53edba895c3` |
| Immutable dump SHA-256, freshly rechecked | `1b6bcb39af41048b53729e9b094f0229163eb6ff6af9563adb666c96f5fd4da4` |
| Promoted Phase 0 implementation checkpoint | `388bf19089b6f1011879f65eec546e383b5884b5` |

The frozen worktree was clean before recovery. The supervisor preserved these identities, selected
only missing work-unit IDs, and recorded `promotion_attempted=false`.

## Incident closure

Recovery stopped fail-closed with 263 units remaining because a per-work-unit replacement count
could not use an exact lookup index on the approximately 32-million-row replacement ledger. The
repair was test-first:

1. a regression demonstrated the missing schema artifact;
2. migration `0003_reconciliation_work_unit_lookup.sql` added the exact
   `(run_id, work_unit_id)` index without changing existing rows;
3. an isolated PostgreSQL `EXPLAIN` verified an index-only plan with no sequential, gather, or
   gather-merge node;
4. the migration was applied to the live database only after RR workers were stopped and through
   the existing publication-lock domain;
5. the frozen runner resumed only the remaining 263 IDs in bounded chunks of 100, 100, and 63.

Checkpoint `b53239552b6de3f1b44c1a9e9258d38537d0d889` contains the index repair and was pushed before
recovery resumed.

The first complete preflight then failed closed because its verifier incorrectly required equal
full-row hashes for exact matches. Rows can be exact on every comparable field while hashes differ
when the dump legitimately lacks optional `quote_volume` or `trades` fields. A new regression
captured that contract. The verifier now requires both hashes and no differing comparable fields;
it does not require hash equality. A bounded vector scan over all `68,474,492` ledger rows then
found zero invalid classifications. Checkpoint `781a61df3da4f985706509cc6f2f75793d4e90a0` contains the
repair and was pushed before the final preflight.

## Completed RR-000008 ledger

| Measure | Verified value |
|---|---:|
| Planned work units | 1,583 |
| Filesystem manifests | 1,583 |
| PostgreSQL work units | 1,583 |
| Completed units | 1,359 |
| Source-unavailable units | 224 |
| Audited keys | 68,474,492 |
| Replacement rows | 31,894,927 |
| Work-unit ID set SHA-256 | `4d98af4a826be79f69d728743b1c2348f70cf0005bef6f60a9ecd8d7ee3b39a9` |
| RR-000008 promotion rows | 1 |

The supervisor completion receipt is
`data/exports/reconciliation/recovery/RR-000008/supervisor-complete-20260718T120644.653202Z.json`,
SHA-256 `0f8f53b48c783eefada04566757d9d3ad878991bcc965b151363137830df15`. Its planned,
filesystem, and database work-unit sets all equal 1,583 and share the frozen work-unit ID hash.

### Classification inventory

| Classification | Rows |
|---|---:|
| `exact_match` | 35,738,056 |
| `binance_fill` | 31,884,870 |
| `binance_correction` | 10,057 |
| `source_unavailable` | 841,509 |

Comparable-field differences are: close 8,827; high 4,112; low 4,185; open 120;
quote-volume 42; trades 36; and volume 10,051. Counts may overlap because one corrected row can
differ in multiple fields.

## Read-only promotion preflight

The ignored local artifact `.omx/rr8-promotion-preflight.json`, SHA-256
`e9e634353e4a2c605561be9d14c28e448d8c5ebcfc52776dbc4bd927c0a5eb0a`, records
`applied=false`. It verified:

- all 1,583 terminal work units and their exact frozen ID set;
- 31,894,927 replacements with candidate and database logical SHA-256
  `2f45102a45b261b99c38484e0cf10df71121237320deb27c11877de2aa1b9182`;
- 285 candidate verified-coverage intervals;
- 261 residual-unavailable intervals containing 841,509 minutes;
- zero run, work-unit, or replacement rows to insert;
- one future promotion row and 285 future coverage rows to insert only if explicitly applied;
- active run before apply `RR-000002` and prospective active run `RR-000008`.

### Residual unavailable coverage

| Symbol | Intervals | Minutes |
|---|---:|---:|
| ADAUSDT | 24 | 4,720 |
| ALGOUSDT | 6 | 994 |
| ARUSDT | 1 | 80 |
| AVAXUSDT | 8 | 1,133 |
| BNBUSDT | 24 | 4,720 |
| BTCUSDT | 30 | 9,266 |
| DOGEUSDT | 19 | 3,069 |
| DOTUSDT | 10 | 1,424 |
| ETHUSDT | 24 | 4,720 |
| FETUSDT | 22 | 4,090 |
| FTMUSDT | 1 | 791,063 |
| INJUSDT | 10 | 1,423 |
| LINKUSDT | 22 | 4,090 |
| NEARUSDT | 10 | 1,424 |
| SOLUSDT | 11 | 1,425 |
| XLMUSDT | 6 | 993 |
| XRPUSDT | 27 | 5,881 |
| ZECUSDT | 6 | 994 |
| **Total** | **261** | **841,509** |

These ranges remain explicit unavailable evidence. They were not interpolated, filled, or hidden.

## Dashboard evidence

`dashboard/public/data/lab-evidence-v1.json`, regenerated at `2026-07-19T14:36:43Z`, has SHA-256
`6c88747b4580d106c93ecdff96f7e29264cd619b8605b45eff946dbc340b25e4`. It is generated from
the checksum-verified promotion receipt and reports:

- `complete_promoted` and `full-history reconciliation promoted`;
- 1,583 expected and 1,583 verified work units;
- 68,474,492 audited keys and 31,894,927 replacements;
- 285 promoted verified intervals and the immutable promotion receipt identities;
- reconciliation provenance established, while snapshot eligibility remains blocked until a new
  immutable research snapshot is explicitly frozen;
- zero real trials and accuracy `not_estimable`; and
- Phase 0 plus deterministic Phase 4 hardening complete, awaiting explicit approval before Phase C
  real outcome-blind discovery.

The dashboard does not convert software completeness into market evidence or an edge claim.

## Verification record

| Check | Result |
|---|---|
| Migration test-first regression | RED on the missing file, then 7 migration tests passed |
| New lookup index against disposable PostgreSQL | 2 passed, 9 deselected; exact metadata and index-only plan verified |
| Promotion receipt, bounded hash/view, publication, preflight, and dashboard regression surface | 58 passed |
| Dashboard evidence contract | Passed against the promoted receipt-bound snapshot |
| Dashboard TypeScript check, ESLint, and production build | Passed |
| Full Windows-native Python suite | 908 passed, 8 isolated-PostgreSQL profiles skipped |
| All PostgreSQL integration profiles against a disposable PostgreSQL 17 database | 8 passed |
| Ruff format and lint | 143 files formatted; all checks passed |
| Mypy | No issues in 78 source files |
| `uv sync --locked` and `uv lock --check` | Passed |
| Source distribution and wheel build | Passed |
| Docker Compose configuration with non-secret validation values | Passed |
| `git diff --check` | Passed |
| Immutable dump hash | Matched the pinned SHA-256 |
| GitHub checkpoint | Local and `origin/agent/research-lab-foundation` matched `388bf19089b6f1011879f65eec546e383b5884b5` |

The Linux-host full suite reached 891 passes and 7 skips; its 13 remaining cases were the
Windows-task-runner tests rejected when Linux pytest created scripts under a WSL UNC temporary
path. Those same 13 cases passed under the Windows-native Python environment, and the complete
Windows-native suite passed. No product assertion failed on its supported host path.

The PostgreSQL integration fixtures use the canonical view's documented 2018 lower boundary. All
eight isolated profiles passed together against disposable PostgreSQL 17, including the new
bounded-view query-plan regression.

## Approved promotion and post-promotion invariants

The explicitly approved promotion committed at `2026-07-18T16:47:13.507011Z`. The immutable
receipt is
`data/exports/reconciliation/promotions/run_id=RR-000008/receipt.json`, with content SHA-256
`19315f0c52c6d3e9a91a5127921ce349fe50e60944c0ccc1a209b5637b3b9cf6` and file SHA-256
`cb399ac01d0beeea4666da071d9186e9a144f3f7fe5e4d1bf760696c631e8529`. It binds:

- manifest SHA-256 `1eaa2909e0f564490ebf65fe35027e588d2b46b135c52f1ed9d16f38fbbd2629`;
- replacement logical SHA-256
  `2f45102a45b261b99c38484e0cf10df71121237320deb27c11877de2aa1b9182`;
- canonical logical SHA-256
  `8dd1af045a92d53b7a8e898764e9f9a90bc456373c16b7c9ac2670ee2c47608c`;
- exactly 285 non-overlapping verified-coverage intervals.

Post-promotion database checks found exactly one RR-000008 promotion row, exactly 285 coverage
rows, zero coverage self-overlaps, zero replacements outside verified coverage, and RR-000008 as
the active run. The reconciled public surface contains `35,738,056` verified dump matches,
`31,884,870` Binance fills, and `10,057` Binance corrections: `67,632,983` unique canonical rows.
The `841,509` source-unavailable minutes are absent rather than fabricated. Reapplying the exact
promotion candidate returned the original hashes and timestamp, retained one promotion row and 285
coverage rows, and left the receipt byte-identical.

## Approval boundary and remaining risks

Phase 0 is closed at the promoted reconciliation boundary. Deterministic Phase 4 hardening is also
complete through its original Task 12 remote checkpoint. The next permitted non-empirical
operation is the separately authorized independent residual-risk audit; any real outcome-blind
discovery or market experiment remains separately approval-gated.

Independent of promotion, `841,509` minutes remain explicitly unavailable, nine venue-conflict
symbols remain quarantined by the earlier compatibility review, and there are still zero real
research trials. Detector stability, untouched predictive validity, realistic cost-adjusted
expectancy, and experiment-program yield therefore remain unmeasured and approval-gated.
