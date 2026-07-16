# Implementation Status

Evidence date: 2026-07-16

## Scope

This execution implements the approved data-truth foundation through the early missing-candle
recovery and canonical snapshot boundary. It does not begin clustering, AI hypothesis generation,
strategy construction, exchange integration, or live execution.

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

The current implementation verification has produced:

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

The Phase 2 exit gate is satisfied. Phase 3 feature/event datasets remain intentionally unstarted
pending explicit approval under the PRD phase gate.
