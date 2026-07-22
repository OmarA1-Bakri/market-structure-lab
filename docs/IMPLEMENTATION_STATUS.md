# Implementation Status

Evidence date: 2026-07-22

## Scope

This execution implements the approved data-truth foundation, early missing-candle recovery,
canonical snapshot boundary, deterministic auction representation, Phase 3 feature/event datasets,
and the Phase 4 outcome-blind discovery MVP. It does not begin Phase 5 outcome attachment,
statistical edge validation, strategy construction, exchange integration, or live execution.

## Foundation delivered

- The installable package now lives under `src/market_structure_lab/`; imports, scripts, tests, and
  Hatch packaging use `market_structure_lab.*`.
- Frozen typed database and source-mapping configuration validates identifiers, ports, timestamp
  units, and constructs credential-safe SQLAlchemy URLs.
- `msl-db-inspect` performs real schema discovery and deterministic quick/full data-quality
  inspection. JSON and human output redact database credentials.
- Placeholder operational scripts were removed. Remaining scripts are thin package entry points.
- The reviewed mapping is `market_data.candles`, epoch milliseconds, `interval` as timeframe,
  source OHLCV plus optional quote volume/trades, mapping version `market-data-candles-v1`.
- Canonical reads use ordered server-side batches. Snapshot export is atomic, UTC-date partitioned,
  checksum-manifested, and derives exact unresolved-gap segment boundaries from the post-recovery
  canonical series.

## Immutable restore evidence

- Dump path: `data/dumps/callscore.dump`
- Bytes: `889,379,858`
- SHA-256: `1B6BCB39AF41048B53729E9B094F0229163EB6FF6AF9563ADB666C96F5FD4DA4`
- Fresh isolated restore source rows: `35,748,117`
- Fresh isolated restore symbols: `25`
- Restored source relations: `market_data.candles`, `market_data.candles_id_seq`,
  `market_data.ticks`, and `market_data.ticks_id_seq`
- Callscore-derived candle columns and Callscore creator/application relations: absent
- The successful isolated restore volume was preserved and restarted for integration verification;
  `data/postgres` was neither deleted nor reinitialized.

The restore initially exposed two fail-fast script defects: PostgreSQL had already moved owned
sequences with their tables, and a shell-style comment appeared inside a SQL heredoc. Both defects
have regression coverage, and a subsequent fresh isolated restore completed successfully.

## Source inspection baseline

Full exact inspection of the immutable source reported:

- `35,748,117` candles, `25` symbols, UTC range 2017-11-25 through 2026-07-14;
- zero duplicate candle keys, invalid OHLC rows, and required-field nulls;
- `19,192` internal gap runs containing `23,372,460` missing minute slots;
- largest internal gap: `2,554,080` minutes;
- optional quote-volume/trade fields null on `17,040,419` rows;
- `market_data.ticks`: zero rows.

The frozen baseline recovery manifest hash is
`befbf514ead328e14320cd6097ce28663c652bc9136115d344fa6714023d826d`.

## Binance Spot compatibility gate

The validated manifest hash is
`482f3a09a4e24a62b0ad92f5bb90028eead67fe961eb1d3320dd38d13fd105d2`.
Every symbol was checked independently using deterministic distributed and major-gap-boundary
overlaps. Sixteen symbols passed and nine failed closed:

- compatible: ADA, ALGO, APT, BNB, DOGE, DOT, FTM, IMX, INJ, LINK, NEAR, TAO, THETA, XLM, XRP,
  ZEC;
- source conflict: AR, AVAX, BTC, ETH, FET, PENDLE, RENDER, SOL, SUI.

The conflict group covers `9,194` gaps and `6,863,688` missing minutes. No supplement is admitted
for those symbols. Their evidence records the exact sampled timestamp and differing fields rather
than inferring a venue.

## Verification evidence

The pre-existing root `.venv` contains a stale Windows symlink. It was preserved rather than
rewritten; verification used the ignored `.venv-ralph` environment via
`UV_PROJECT_ENVIRONMENT=.venv-ralph`. This implementation introduced no new dependency; the lock
now matches the existing `pyproject.toml` requirements.

The foundation and recovery verification produced:

- `uv sync --locked` and `uv lock --check`: passed;
- unit/regression suite: `108 passed` in the final recovery evidence pass;
- Ruff: passed;
- Mypy across `src`: passed with no issues in 33 source files;
- wheel and source distribution: built successfully;
- isolated installed-wheel import: passed with runtime dependencies installed;
- `docker compose config --quiet`: passed;
- `bash -n docker/postgres/init/restore_dump.sh`: passed;
- archive TOC/allowlist inspection: passed;
- fresh isolated PostgreSQL restore and exact relation/source-row assertions: passed;
- `git diff --check`: passed.

## Completed recovery evidence

- Run ID: `381be98e-51fb-4a07-9ad6-a0fdd44305a7`
- Terminal gaps: `19,192 / 19,192`
- Added unique supplements: `16,480,681`
- Immutable source rows before/after: `35,748,117 / 35,748,117`
- Remaining missing minutes: `6,891,779`
- Failed batches: `0`
- Supplements on source-conflict symbols: `0`
- Logical supplement hash:
  `e79c147039249362feba0ef153ea7417bcabab7a3e0c0c670e414f91f2adf6e1`
- Reapplying the identical validated manifest: `0` inserted rows, all `19,192` gaps complete,
  identical logical hash.
- Durable batch ledger: `28,765` completed bounded checkpoints. Interrupted gaps resume after the
  greatest completed batch boundary and retain prior authoritative-empty evidence.
- Publication is globally serialized with a PostgreSQL advisory lock; the validated-key unique
  index is present and the real supplement table has zero duplicate canonical keys.

The resolution ledger contains 9,869 fully recovered gaps, 117 partially recovered gaps, 12
authoritative provider-absent gaps, and 9,194 source-conflict gaps. Coverage conservation is exact:
`23,372,460 - 16,480,681 = 6,891,779` missing minutes after recovery.

## Canonical snapshot proof

A real bounded export used `batch_size=257` for XRPUSDT on 2018-05-05. The canonical view returned
exactly 1,440 ordered minutes: one immutable dump row and 1,439 validated supplements. Atomic
publication produced one UTC-date Parquet partition with SHA-256
`225096cddf0dd2081c8a51ba9f9ea575aea814d08d497288f87975536f813b32` and snapshot hash
`5c98cb44467f02d20529c40489f2758b20c83347a56ab951e4879879465e66f6`.
Repeating the export returned the existing verified snapshot with the same row count and hash.

The exact post-recovery boundary query was also run against the complete XRPUSDT canonical series.
It returned `27` disjoint remaining gaps totaling exactly `5,902` minutes, with a largest gap of
`600` minutes and UTC-aware bounds. The original ledger reports `26` non-full ranges because one
partially recovered original range split into multiple exact remaining holes.

## Phase 2: auction representation

Phase 2 implements the deterministic auction-representation boundary described in PRD sections 16
and 17. It does not begin feature/event dataset production, behaviour discovery, statistical edge
validation, strategy construction, exchange integration, or live execution.

Delivered representation:

- versioned integer bin definitions for exchange ticks, fixed steps, constant-percentage/log price,
  frozen target counts, and frozen volatility-scaled steps;
- explicit, versioned OHLCV allocation approximations for uniform touched-bin, typical price, and
  triangular-to-close volume, plus reconstruction from supplied real lower-timeframe candles only;
- deterministic integer-bin POC, contiguous POC-outward value area, VWAP, immutable profiles, and
  additive/removable rolling accumulation;
- required bounded window policies for rolling bar count, rolling duration, fixed half-open UTC
  ranges, and UTC day/week/month sessions;
- ordered symbol/timeframe stream checks, hard segment resets, explicit reject-or-reset gap policy,
  and no transition or persistence across invalid boundaries;
- immutable versioned auction snapshots containing active timestamps, profile state, location,
  node zones and persistence, value migration, and stable structural events;
- canonical snapshot serialization and deterministic ordered replay hashes;
- golden profile/replay fixtures and rolling incremental-versus-full-recomputation coverage.

Research limitations remain explicit: all candle-based allocation models are approximations, exact
trade-price allocation is unavailable, and no candle is interpolated, forward-filled, or fabricated.
Session boundaries are UTC conventions, not exchange closure claims. The simple location and event
vocabulary is representation evidence, not a behaviour, hypothesis, validated edge, or strategy.

### Phase 2 verification evidence

The existing root `.venv` remained untouched after Windows could not remove its stale `lib64`
symlink. Phase 2 verification used the isolated temporary uv environment
`C:\Users\albak\AppData\Local\Temp\msl-phase2-venv`; the built wheel was installed into a second
fresh temporary environment.

- Unit/regression suite: `343 passed in 7.85s` in the final pass.
- Ruff: all repository checks passed.
- Mypy: no issues in `40` source files.
- `uv lock --check`, `uv sync --locked`, wheel build, and source-distribution build: passed.
- Isolated installed-wheel auction update and canonical snapshot hash: passed; smoke hash
  `0f48041af27caea054fe569ff2c4ab4fcf2aa3f9bc850708b9737b664b811922`.
- Golden rolling replay: whole-stream SHA-256
  `6ce85846132da118bc5cd0d73c1e37eea13a0cd3b55eaa170f336944b38a79b1`, with each intermediate
  snapshot hash independently pinned in the fixture.
- Rolling incremental/full equivalence: unit coverage across uniform and typical-price allocation,
  windows of 1, 2, and 60 bars over 512 snapshots each; the benchmark independently compared 512
  snapshots at absolute tolerance `1e-12`.
- Benchmark: [`docs/benchmarks/phase2-auction-baseline.json`](benchmarks/phase2-auction-baseline.json)
  records 100,000 deterministic candles, seed `20260716`, input SHA-256
  `3a8127988b56f0b1a2e873143aac22c58284e651b4b1fe66bd24b021dfeda6d4`, canonical snapshot-stream
  SHA-256 `d879aa0010852ed9c6ca24ee1d5d217458459dffcc7091833ec5219d302a0e3f`, 523.43 candles/second,
  1,440 maximum active candles, and 1.03 MiB peak traced engine memory. No machine-specific timing
  threshold is asserted.
- Independent architect review: approved after `237` targeted tests and explicit reruns of the
  decimal-boundary, empty-profile, fixed-exclusion, session-reset, and golden-hash counterexamples.
- `docker compose config --quiet`, restore-script shell syntax, `git diff --check`, and immutable
  dump SHA-256 verification: passed.

The Phase 2 exit gate is satisfied.

## Phase 3: feature and event datasets

Phase 3 implements the outcome-blind evidence boundary described by the approved Phase 3 PRD and
test specification. It consumes Phase 2 snapshots and does not attach forward outcomes or begin
behaviour discovery.

Delivered evidence contracts:

- `FS-000001`, a canonical hashed registry containing 16 auction-informed and 10 minimally
  assumptive sequence features with explicit units, history, missing-value, version, category, and
  leakage metadata;
- a causal builder bounded to 21 trailing snapshots, exact exclusive-close information cutoffs,
  hard resets at segment/gap/session/window boundaries, and fail-closed upstream identity checks;
- exact training-only median/IQR normalisation using bounded on-disk sorted runs and bounded merge
  buffers, with immutable artifact hashes and no validation/holdout fitting;
- half-open discovery events with mandatory registry validation, provenance-rich stable IDs,
  feature-vector hashes, causal prior-history triggers, and source-open versus observable-trigger
  timestamp separation;
- fixed, exploratory rolling, UTC session, causal change-point, expansion, value/POC, and prior-zone
  node event construction without boundary crossing;
- exact streaming overlap accounting whose memory is proportional to active concurrency rather than
  the total event collection;
- bounded deterministic Parquet publication with pinned upstream/code/lock identities, registered
  schemas, checksums, missing/leakage/overlap evidence, atomic `_SUCCESS`, tamper detection, and
  content-idempotent repeats.

The public formulas, null rules, event semantics, and operations contract are documented in
[`FEATURE_EVENT_DATASETS.md`](FEATURE_EVENT_DATASETS.md). Derived publication APIs live at
`market_structure_lab.data.derived`; keeping them out of the canonical data package root prevents
fresh-install circular imports.

### Phase 3 verification evidence

The stale root `.venv` remained untouched. Phase 3 verification used the ignored
`.venv-codex` environment through `UV_PROJECT_ENVIRONMENT=.venv-codex`; builds and installed-wheel
checks used isolated directories below the operating-system temporary directory.

- Post-deslop unit/regression suite: `473 passed in 15.74s`.
- Ruff: all repository checks passed.
- Ruff formatter: all `20` Phase 3-touched Python files already formatted after the scoped cleanup.
- Mypy: no issues in `53` source files.
- `uv lock --check` resolved the pinned `120` packages without a change; `uv sync --locked` passed.
- Post-deslop wheel and source distribution built successfully; a fresh installed-wheel smoke
  printed `FS-000001 26 change_point True True DerivedPublicationIdentity`, including both
  documented event callables.
- Feature and event publication tests include fresh-interpreter import, deterministic batching,
  empty publication, idempotent replay, stale/tampered output, schema leakage, and overlap evidence.
- Docker daemon `29.4.2` was reachable and `docker compose config --quiet` passed.
- `bash -n docker/postgres/init/restore_dump.sh` and `pg_restore --list` passed.
- The dump SHA-256 was rechecked as
  `1B6BCB39AF41048B53729E9B094F0229163EB6FF6AF9563ADB666C96F5FD4DA4`.
- Read-only queries against preserved isolated PostgreSQL containers reconfirmed `35,748,117`
  immutable source candles, `16,480,681` validated supplements, `19,192` terminal gap resolutions,
  `28,765` completed recovery batches, zero Callscore application tables outside the approved
  schemas, and zero forbidden Callscore-derived candle columns.
- The exact 100,000-candle Phase 2 benchmark regression wrote only to a temporary file. Its schema
  and complete correctness block match the pinned baseline: input SHA-256
  `3a8127988b56f0b1a2e873143aac22c58284e651b4b1fe66bd24b021dfeda6d4`, snapshot-stream SHA-256
  `d879aa0010852ed9c6ca24ee1d5d217458459dffcc7091833ec5219d302a0e3f`, `512` incremental/full
  comparisons passed at absolute tolerance `1e-12`, repetitions were identical, and maximum active
  candles remained `1,440`. Timing is not an acceptance threshold.
- Independent architect review: approved after `135` focused tests and adversarial checks of
  punctuation-hidden future metadata, identity drift, cutoffs, boundary resets, bounded
  normalisation, event identity, structural observability, overlap, publication, package imports,
  and Phase 4 exclusion.
- Mandatory anti-slop pass: the architect found no blocking overengineering; scoped Ruff formatting
  changed only `12` Phase 3-owned files, removed irregular indentation and formatting drift, and
  retained `130` focused passing tests before the complete post-cleanup gate above.
- Final `git diff --check`: passed. Only preserved user/runtime `.codacy`, `.coverage`, and `.vscode`
  artifacts remain untracked and were not staged.

The Phase 3 exit gate is satisfied with zero known implementation errors.

## Phase 4: outcome-blind discovery MVP

Phase 4 consumes only frozen Phase 3 feature evidence and keeps the final holdout inaccessible.
Delivered contracts include:

- canonical chronological discovery/development/final-holdout and optional asset-holdout metadata,
  with holdout iterables rejected before access;
- bounded discovery-safe numeric matrices, inspectable deterministic PCA, and seeded canonical
  K-means;
- stability across seeds, deterministic subsamples, adjacent development periods, assets, and
  nearby cluster counts, with unstable definitions retained as `rejected_unstable`;
- bounded per-boundary motifs and event-level dwell-compressed transition evidence that never
  crosses symbol, timeframe, segment, gap, session, or non-contiguous boundaries;
- content-addressed frozen behaviours with complete detector, representative, distribution,
  stability, frequency/duration, and coverage evidence;
- frozen AI evidence packs and provenance-complete non-authoritative interpretation records;
- atomic, canonical, checksum-pinned, idempotent `DR-*` and interpretation publication.

The committed golden fixture uses two symbols, 16 discovery rows, eight development rows, and
separately frozen holdout metadata with no holdout rows or outcomes. Stable `DR-000601` replays
byte-identically with two behaviours and manifest SHA-256
`404da0b473a6b3d065c9ce009b37cb89d91e1394c28382b64854901e1295d27e`. Unstable
`DR-000602` replays byte-identically with no behaviours and manifest SHA-256
`4ca6d4b6b3ca8e89b56c853f1b4235338ee5fa92d9b536e9cb3e07057ed371e9`.

The exact frozen interpretation prompt input SHA-256 is
`9f6b8bb56143b1ce6298d5e409fff41d50bbd1bb10cd5901bf0fbcf455d3aae6`; the parent session-model
response SHA-256 is `ca49bc5572d22042723891f3e1047e6812f8e4595f6ce9e04fae793e32eb4577`.
Real publication produced interpretation manifest SHA-256
`57959a105814d8e090f5353cb571c292cf3c8a4dc69806ba20a8a8381439937f`. The response proposes
falsifiable Phase 5 tests and explicitly records fixture/transition/asset/OHLCV limitations. It does
not claim a validated edge.

Algorithms, caps, replay instructions, artifact formats, Markov-like transition caveats, AI
non-authority, and the untouched Phase 5 boundary are documented in
[`DISCOVERY_MVP.md`](DISCOVERY_MVP.md).

Task 6 verification completed:

- golden stable/rejected/interpretation publication: `3 passed`;
- discovery regression including the golden fixture: `186 passed in 16.56s`;
- Ruff and formatter: passed for the Task 6 Python test;
- Mypy: no issues in the 13 discovery/experiment source files;
- `git diff --check`: passed;
- immutable dump, interpretation input, and interpretation response hashes matched their pinned
  values.

Repository-wide integrating verification completed after the live freshness work:

- unit/regression suite: `711 passed, 3 skipped in 97.30s`; the three skips are explicit PostgreSQL
  integration profiles;
- the skipped profiles then passed against an isolated disposable database: `3 passed`;
- Ruff and Ruff formatting: all checks passed, `120` Python files formatted;
- Mypy: no issues in `68` source files;
- `uv lock --check`, `uv sync --locked`, source distribution, and wheel build: passed;
- a fresh Python 3.13 wheel install imported the package, CLI, and discovery API successfully;
- `docker compose config --quiet`, restore-script shell syntax, archive TOC inspection, and
  immutable dump SHA-256 verification: passed;
- durable PostgreSQL verification found zero public application tables, zero forbidden
  Callscore-derived candle columns, zero ticks, and zero supplements on conflict symbols;
- the exact 100,000-candle benchmark matched both pinned correctness hashes, passed `512`
  incremental/full comparisons at `1e-12`, retained a maximum active window of `1,440`, used
  `1.0248` MiB peak traced memory, and replayed at `730.15` candles/second. Timing is not an
  acceptance threshold.

The Phase 4 deterministic software-hardening gate passes. Tasks 7–11 bind concrete
snapshot-to-discovery provenance, reject cluster structural drift, make motifs multivariate and
boundary-safe with separate stability evidence, freeze dependence-aware transition uncertainty,
and publish checksum-bound semantic-leakage receipts with an explicit manual-review limitation.
Task 12 verified those contracts without running market discovery or opening the final holdout, and
Task 13 independently challenged and remotely checkpointed them. Task 14 then retained four real
attempts and completed bounded `PG-000004` with a rejected detector, zero behaviours, and no
holdout-row or outcome access. This remains neither a prediction, validation, strategy, nor edge
gate. Task 15 is authorized next but must publish its separate Phase 5 plan before outcome code.

## RR-000008 full-history reconciliation: complete and promoted

The frozen `row-reconciliation-v3` run is terminal across all `1,583` planned monthly work units.
Filesystem manifests and the PostgreSQL ledger agree on the complete work-unit ID set, whose
SHA-256 is `4d98af4a826be79f69d728743b1c2348f70cf0005bef6f60a9ecd8d7ee3b39a9`.
The final status distribution is `1,359` completed and `224` source-unavailable units. The run
audited `68,474,492` keys and retained `31,894,927` replacement rows:

- `35,738,056` exact matches;
- `31,884,870` Binance fills;
- `10,057` Binance corrections;
- `841,509` source-unavailable keys.

The recovery completed from its frozen commit, lockfile, Python executable, and clean worktree.
The runner selected only the missing frozen IDs; it never replanned the run and never requested
promotion. The completion receipt is
`data/exports/reconciliation/recovery/RR-000008/supervisor-complete-20260718T120644.653202Z.json`,
SHA-256 `0f8f53b48c783eefada04566757d9d3ad878991bcc965b151363137830df15`.

The incident that stopped the final 263 units was resolved with an append-only schema migration
adding the exact `(run_id, work_unit_id)` replacement lookup index. Disposable PostgreSQL
verification proves the count path uses that index without a sequential or parallel scan. A
separate verifier correction now treats an exact match according to comparable candle fields:
both row hashes must exist and no comparable fields may differ, but the hashes may differ when the
dump legitimately lacks optional fields.

The bounded read-only promotion preflight passed before the approved apply. Its local evidence
artifact SHA-256 is `e9e634353e4a2c605561be9d14c28e448d8c5ebcfc52776dbc4bd927c0a5eb0a`.
It verified the `31,894,927` replacement rows at logical SHA-256
`2f45102a45b261b99c38484e0cf10df71121237320deb27c11877de2aa1b9182`, prepared `285`
eligible coverage intervals, and retained `261` explicit residual-unavailable intervals containing
`841,509` minutes.

After explicit approval, RR-000008 was atomically promoted at `2026-07-18T16:47:13.507011Z`.
The active reconciled run is now RR-000008 with exactly one immutable promotion row and `285`
non-overlapping coverage rows. Its canonical logical SHA-256 is
`8dd1af045a92d53b7a8e898764e9f9a90bc456373c16b7c9ac2670ee2c47608c`. The checksum-verified
receipt is `data/exports/reconciliation/promotions/run_id=RR-000008/receipt.json`, content SHA-256
`19315f0c52c6d3e9a91a5127921ce349fe50e60944c0ccc1a209b5637b3b9cf6`. Exact replay retained the
same promotion row, timestamp, hashes, coverage, and receipt bytes.

The reconciled view now publishes `35,738,056` verified dump matches, `31,884,870` Binance fills,
and `10,057` Binance corrections, for `67,632,983` canonical rows. Zero replacements lie outside
verified coverage. The `841,509` unavailable minutes remain absent and explicit.

The dashboard evidence snapshot labels RR-000008 `complete_promoted`, reports all `1,583 / 1,583`
verified work units and `285` promoted intervals. Task 14 subsequently froze its separate bounded
snapshot and retained four real discovery receipts. None attaches outcomes, so predictive and
cost-adjusted accuracy remain `not_estimable`.

Detailed identities, residual coverage, verification commands, and the approval boundary are in
[`PHASE0_REMEDIATION_EVIDENCE.md`](PHASE0_REMEDIATION_EVIDENCE.md).

## Daily candle freshness implementation

The approved freshness prerequisite is implemented without changing the immutable dump or existing
research snapshots. `msl-sync-candles` supports frozen planning, dry-run/apply, checksum-bearing
reports and health, an explicit idempotent `bootstrap`, plus a separate `snapshot` handoff.
Bootstrap verifies the immutable dump, reviewed compatibility evidence, restored row identity, and
mapping before applying the append-only recovery migration; daily jobs never initialize storage.
The snapshot handoff checks the report,
compatibility artifact, dump identity, current logical supplement hash, and publication policy; it
then streams bounded canonical batches through the existing atomic partitioned exporter.

The default snapshot policy requires every symbol to be `up_to_date` or `recovered`. An explicit
`allow_provenance_blocked` policy can freeze uneven research coverage only when every exception is
`provenance_pending`, `source_conflict`, or `source_unavailable`; it does not admit partial,
provider-absent, failed, or unresolved recovery. The nine existing source conflicts therefore keep
full-universe scheduler health non-zero and are recorded in any explicitly permitted snapshot.

The completed early-recovery state was preserved in the ignored archive
`data/exports/market-structure-recovery-state-20260716.dump`, SHA-256
`989E5792F02E3CE01035D0DF91423D8961835AE130EC10D757C8E5A5A643F35C`, and restored atomically into
the durable database. It retained `16,480,681` supplements, `28,765` completed batches, `19,192`
terminal resolutions, and logical hash
`e79c147039249362feba0ef153ea7417bcabab7a3e0c0c670e414f91f2adf6e1`.

The first durable live plan is
`data/exports/freshness/20260716T112300Z-754d49560efc.plan.json`, cutoff
`2026-07-16T11:23:00Z`, SHA-256
`754d49560efccbe6ac40d23b5b190fa6c791c352951da35ac1c6aff6e40981d2`. It covered 25 symbols,
`9,388,681` compatible eligible minutes, and `6,910,293` provenance-blocked minutes. Recovery run
`5ba02c00-c622-41fb-b6c5-1ef960825f88` produced:

- `8,567,720` admitted rows in `9,750` completed batches;
- 170 terminal ranges: 13 recovered, 3 partially recovered, and 154 provider absent;
- zero failed batches and zero supplements for source-conflict symbols;
- logical supplement hash
  `cfaba512a869075336f27136a6792cb3d07246c8b4e6656a7c08e52dda59189d`.

The durable database now contains `25,048,401` validated supplements. Immutable source rows remain
`35,748,117`, so the canonical view contains `60,796,518` rows. Report
`data/exports/freshness/20260716T112300Z-754d49560efc-d518e9ca850a.report.json`, SHA-256
`d518e9ca850a2c06338da84f2ef921c8efe2ed3f50c52f5a37375ff54678d210`, conserved coverage:
`16,298,974` before minus `8,567,720` recovered equals `7,731,254` remaining. Of those,
`6,910,293` are reviewed source conflicts and `820,961` are explicit compatible-source terminal
gaps. Health returns exit `2` by design.

The identical 170-gap recovery manifest was replayed after publication. It returned the same run
ID and logical hash, inserted zero rows, and left validated supplements unchanged at `25,048,401`.

The PostgreSQL freshness planner uses bounded aggregate summaries plus lag-only narrow key scans.
Transaction-local controls retain index-only merge/anti-join plans after the supplement table grew
past 25 million rows; the prior sequential/hash/sort spill path is regression-tested on disposable
PostgreSQL.

The Windows task `Market Structure Lab - Daily Candle Refresh` is registered and ready. It runs at
07:15 local time, starts after missed triggers, ignores overlapping instances, and invokes
PowerShell 7 with `scripts/run_daily_candle_refresh.ps1`. The runner requires existing durable
storage, preserves pending manifests across retryable failures, and never bootstraps, snapshots,
deletes volumes, or reinitializes PostgreSQL.

Exact operator commands, paths, status meanings, resumption procedure, compatibility hash, and the
deliberate snapshot policy are documented in
[`DAILY_CANDLE_FRESHNESS.md`](DAILY_CANDLE_FRESHNESS.md).

## Phase 4 hardening: Task 14 real pilot complete and rejected

The deterministic software gate is now joined by one terminal real, source-backed outcome-blind
programme. Four immutable Task 14 attempts are counted: `PG-000001` and `PG-000002` failed under
their obsolete oversized scopes, `PG-000003` produced a rejected detector receipt before exposing
an overly strict programme-vector reservation check, and fresh bounded `PG-000004` completed with
`DR-000704` and a scientifically `rejected` reliability vector.

- `msl-run-discovery-program` froze promotion-bound universe selection, exact gap and snapshot
  identities, the search grid, programme and run budgets, Phase 3 publications, discovery-only
  normalisation, terminal receipts, and a bounded reliability vector.
- The bounded programme used 960 APTUSDT snapshots across eight discovery and eight development
  hours, a 512-window motif cap, and 8,628 aggregate feature cells. The later temporal interval and
  IMXUSDT remained metadata-only holdouts.
- `DR-000704` retained 460 complete-case discovery rows and 480 development rows, executed without
  holdout-row access, and was rejected by the frozen stability policy. No behaviour or hypothesis
  advanced.
- `PG-000003` was not changed after its programme-vector verification incident. The corrected
  reservation semantics and every active attempt identity were checkpointed at
  `6f49407c3cef96e5753dd26ef81ce9c793116a31` before `PG-000004` ran.
- Exact identities, incident history, reviews, and verification are recorded in
  [`TASK14_OUTCOME_BLIND_DISCOVERY_EVIDENCE.md`](TASK14_OUTCOME_BLIND_DISCOVERY_EVIDENCE.md).
- The reviewed Task 14 close-out checkpoint
  `5db2705a1cba37ee10d5eb3bd1fa470a5b628e3d` was pushed and the remote branch resolved to the exact
  local SHA before Task 15 planning began.

The Phase 4 deterministic contracts remain in force: provenance is checked before matrix
construction; cluster, motif, missingness, transition, work-budget, and semantic-leakage evidence is
frozen; transitions remain descriptive and dependence-aware; and manual semantic review remains
mandatory.

Task 13 used completed and remotely verified baseline
`3cf3721f1ec694b61ec1da78d0b7be24fea93caf` and is remotely checkpointed at
`59a9814a8e2a609bf1ea0bd5d0320ed0f7b33ee4`. It did not run discovery or inspect outcomes. Task 14
then consumed its separate authorization under the same outcome-blind boundaries.

The ignored runtime ledger now contains four checksum-verified real discovery receipts: two failed
and two rejected. This is not a validated signal, predictive estimate, or edge. Task 15 is the next
gate and must publish the separate Phase 5 validation plan before any outcome attachment.

## Task 15: separate Phase 5 validation plan

Task 15 is defined by two first-class tracked artefacts:

- [`2026-07-22-phase5-validation.md`](superpowers/plans/2026-07-22-phase5-validation.md);
- [`2026-07-22-phase5-validation-test-spec.md`](superpowers/plans/2026-07-22-phase5-validation-test-spec.md).

They freeze the baseline-first order A -> B -> G -> E -> D, completed-bar information cutoffs,
next-contiguous-bar entry, candidate-specific horizons, nested purged/embargoed walk-forward folds,
unchanged temporal and asset holdouts, one programme-scoped final-access attempt, weekly block
bootstrap, Holm correction, costs, negative controls, robustness, immutable receipts, and bounded
work. BTCUSDT, ETHUSDT, and unresolved source-conflict symbols remain excluded. Task 14 produced no
advancing behaviour, so the plan records the five families as preregistered human-origin candidates
and does not invent discovery lineage.

This planning checkpoint does not attach outcomes or claim support, validation, promotion, or an
edge. Source implementation begins only after the Task 15 plan/test-spec commit is pushed and its
remote SHA is verified, using a separate bounded implementation plan.

The independently reviewed Task 15 plan/test-spec checkpoint
`807ac28ac5616fb837c1ccea1e2bc47572ae3984` was pushed at `2026-07-22T10:24:29Z`; the remote branch
resolved to the exact local SHA. No outcome code preceded that checkpoint.

## Phase 5 bounded implementation plan

The separately tracked
[`2026-07-22-phase5-bounded-implementation.md`](superpowers/plans/2026-07-22-phase5-bounded-implementation.md)
turns the Task 15 contract into eleven TDD work units. A repository capability map and independent
specification review approved the revised file boundaries, exact 1,104-slot roster, complete work
budgets, causal clocks, sealed final-access mechanism, development-only real-run boundary, and
verification/review chain. The plan creates no outcome evidence and makes no validation claim.

Source implementation remains blocked until this bounded plan has its own pushed, remotely verified
checkpoint. The first bounded real programme remains development-only with zero final-holdout
access; synthetic tests will prove the one-time access state without touching real final rows.

That planning checkpoint is `e655152ab729b3e1e530f1bc044febca3509a6fd`, pushed and remotely
verified at `2026-07-22T10:36:29Z`. The checkpointed plan bytes have SHA-256
`d3520669352f0d85a27569edeefcfe84ff785e1f928ae0cc41edf91915c16111`. Phase 5 source TDD may now
begin against those exact identities.
