# Crypto Data Dump Viability

Assessment date: 2026-07-16

## Verdict

The dump is **conditionally viable for cryptocurrency market-structure research**. Candle rows are
unique, minute-aligned, numerically valid, and externally spot-checked against Binance. The dataset
is not a continuous 18-symbol, two-year panel: it contains 25 symbols with materially different
date ranges and gaps. Canonical datasets must select contiguous per-symbol windows and must never
bridge gaps or symbol boundaries.

## Archive identity

- Path: `data/dumps/callscore.dump`
- Format: PostgreSQL custom archive, gzip-compressed
- Size: `889,379,858` bytes
- SHA-256: `1B6BCB39AF41048B53729E9B094F0229163EB6FF6AF9563ADB666C96F5FD4DA4`
- Source PostgreSQL version: `16.14`
- Archive entries: `563`
- Archive timestamp: `2026-07-14 05:56:31`

The source archive also contains unrelated Callscore application data. The crypto-only restore
manifest admits only `candles` and the empty `ticks` market schema, then moves them to
`market_data`. The source archive itself remains immutable.

## Candle profile and quality

- Rows: `35,748,117`
- Symbols: `25`
- Timeframe: `1m` only
- Global UTC range: `2017-11-25` through `2026-07-14`
- Duplicate `(symbol, interval, open_time)` keys: `0`
- Missing required candle fields: `0`
- Malformed or non-finite numeric values: `0`
- Invalid OHLC relationships: `0`
- Negative numeric values: `0`
- Off-minute-grid timestamps: `0`
- Zero-volume rows: `501,913` (`1.404%`)

The dump's physical row order is not chronological. Loaders must explicitly order by
`symbol, interval, open_time`.

`quote_volume` and `trades` are absent in `17,040,419` rows (`47.668%`) and are optional only.
Callscore-derived `regime`, `confidence`, `returns`, `volatility`, and `volume_ratio` fields are not
research inputs and are removed from the restored copy. The `ticks` table contains zero rows, so
the dump provides no trade-level enrichment.

Three sampled BTCUSDT minutes matched Binance exactly for timestamp, OHLCV, quote volume, and trade
count. This supports Binance provenance, but venue/source identity must remain explicit metadata
rather than an inferred fact.

## Coverage by symbol

Coverage is the share of present one-minute rows between each symbol's first and last timestamp.
Gap runs are exact contiguous missing-minute runs.

| Symbol | Rows | UTC range | Coverage | Gap runs | Largest gap |
|---|---:|---|---:|---:|---:|
| ADAUSDT | 2,099,741 | 2018-07-09 to 2026-07-12 | 49.85% | 1,674 | 139,530 min |
| ALGOUSDT | 49,910 | 2021-03-01 to 2021-04-04 | 99.82% | 1 | 90 min |
| APTUSDT | 4 | 2024-10-22 to 2025-01-20 | 0.003% | 3 | 86,399 min |
| ARUSDT | 2,106,144 | 2022-06-01 to 2026-07-13 | 97.27% | 964 | 19,175 min |
| AVAXUSDT | 2,097,675 | 2020-12-24 to 2026-07-14 | 71.83% | 1,248 | 58,991 min |
| BNBUSDT | 2,099,158 | 2018-07-06 to 2026-07-14 | 49.75% | 1,091 | 907,063 min |
| BTCUSDT | 2,805,027 | 2017-11-25 to 2026-07-14 | 61.78% | 2,446 | 54,719 min |
| DOGEUSDT | 2,097,274 | 2019-07-08 to 2026-07-13 | 56.86% | 1,268 | 86,399 min |
| DOTUSDT | 2,099,558 | 2020-08-20 to 2026-07-14 | 67.72% | 1,476 | 10,079 min |
| ETHUSDT | 2,807,766 | 2018-07-06 to 2026-07-14 | 66.54% | 1,357 | 149,190 min |
| FETUSDT | 2,106,147 | 2019-03-09 to 2026-07-07 | 54.63% | 980 | 1,033,456 min |
| FTMUSDT | 4 | 2024-06-22 to 2024-09-20 | 0.003% | 3 | 86,399 min |
| IMXUSDT | 4 | 2023-12-12 to 2024-03-11 | 0.003% | 3 | 86,399 min |
| INJUSDT | 2,105,868 | 2020-11-02 to 2026-07-13 | 70.31% | 1,015 | 86,399 min |
| LINKUSDT | 2,362,242 | 2019-01-16 to 2026-07-13 | 59.97% | 944 | 10,987 min |
| NEARUSDT | 1,143,927 | 2020-10-15 to 2026-07-14 | 37.88% | 768 | 17,279 min |
| PENDLEUSDT | 1,534,370 | 2023-07-03 to 2026-07-13 | 96.38% | 391 | 57,141 min |
| RENDERUSDT | 974,864 | 2024-07-26 to 2026-07-11 | 94.64% | 14 | 9,971 min |
| SOLUSDT | 2,149,698 | 2020-09-14 to 2026-07-14 | 70.14% | 1,304 | 130,876 min |
| SUIUSDT | 1,622,157 | 2023-05-03 to 2026-07-13 | 96.57% | 490 | 10,079 min |
| TAOUSDT | 1,127,276 | 2024-04-11 to 2026-07-13 | 95.10% | 17 | 8,639 min |
| THETAUSDT | 4 | 2024-11-23 to 2025-02-21 | 0.003% | 3 | 86,399 min |
| XLMUSDT | 211,656 | 2021-03-01 to 2026-06-04 | 7.65% | 4 | 2,554,080 min |
| XRPUSDT | 2,097,733 | 2018-05-05 to 2026-07-14 | 48.69% | 1,727 | 19,204 min |
| ZECUSDT | 49,910 | 2021-03-01 to 2021-04-04 | 99.82% | 1 | 90 min |

Across the full per-symbol envelopes, `35,748,117` of `59,120,577` expected minute slots are
present: `60.47%` coverage, `23,372,460` missing minutes, and `19,192` gap runs.

APT, FTM, IMX, and THETA are unusable four-row fragments. ALGO and ZEC are short samples, and XLM
is highly fragmented. Other symbols remain usable only after contiguous-window selection.

## Required safeguards

- Use `market_data.candles` as the authoritative current source; recompute higher timeframes and all
  research features deterministically.
- Map `open_time` from epoch milliseconds to UTC and `interval` to canonical `timeframe`.
- Preserve the dump hash, source row ID, mapping version, and dataset version in snapshot manifests.
- Split every sequence at material gaps; never create profiles or transitions across gaps or symbols.
- Preserve but flag zero-volume rows; do not impute large gaps.
- Treat OHLCV-derived volume-at-price as an approximation.
- Do not use the final research holdout to select symbols, contiguous ranges, or quality thresholds.

## Recovery provenance gate

The early data-completion stage used a frozen recovery cutoff of 2026-07-14 and independently
compared every symbol with Binance Spot. Samples cover each symbol's beginning, middle, end, and
the boundaries of its largest gaps. Large sources require at least 100 exact normalized overlaps;
four-row fragments require all four and remain labelled limited evidence.

The compatibility result is deliberately mixed:

- compatible: ADAUSDT, ALGOUSDT, APTUSDT, BNBUSDT, DOGEUSDT, DOTUSDT, FTMUSDT,
  IMXUSDT, INJUSDT, LINKUSDT, NEARUSDT, TAOUSDT, THETAUSDT, XLMUSDT, XRPUSDT,
  ZECUSDT;
- source conflict: ARUSDT, AVAXUSDT, BTCUSDT, ETHUSDT, FETUSDT, PENDLEUSDT,
  RENDERUSDT, SOLUSDT, SUIUSDT.

The incompatible group contains `9,194` original gaps and `6,863,688` missing minutes. Those
symbols are quarantined from recovery. The exact conflicting samples are:

| Symbol | UTC sample | Differing fields |
|---|---|---|
| ARUSDT | 2026-06-03 07:38 | high, close, volume |
| AVAXUSDT | 2026-06-03 07:38 | volume |
| BTCUSDT | 2026-07-14 04:46 | low, close, volume |
| ETHUSDT | 2026-07-14 04:46 | low, close, volume |
| FETUSDT | 2026-06-03 07:38 | high, close, volume |
| PENDLEUSDT | 2026-06-03 07:38 | high, close, volume |
| RENDERUSDT | 2026-06-03 07:38 | high, low, close, volume |
| SOLUSDT | 2026-07-14 04:46 | low, close, volume |
| SUIUSDT | 2026-06-03 07:38 | high, close, volume |

This supersedes the earlier inference from three matching BTCUSDT samples: the dump is not treated
as uniformly Binance Spot. Existing dump rows remain authoritative, and no candidate venue is
assigned to the conflicting symbols without further evidence.

The baseline manifest SHA-256 is
`befbf514ead328e14320cd6097ce28663c652bc9136115d344fa6714023d826d`; the validated manifest
SHA-256 is `482f3a09a4e24a62b0ad92f5bb90028eead67fe961eb1d3320dd38d13fd105d2`.

## Post-recovery coverage

The completed frozen-manifest run added `16,480,681` unique, validated observations without
changing the `35,748,117` immutable dump rows. Combined canonical rows are `52,228,798` of
`59,120,577` minute slots (`88.3428%` coverage). Recovery filled `70.5132%` of the original
missing slots; `6,891,779` minutes remain absent and are not imputed.

Every one of the `19,192` original gaps has one terminal ledger row:

| Resolution | Gaps | Expected minutes | Recovered minutes |
|---|---:|---:|---:|
| recovered | 9,869 | 11,576,803 | 11,576,803 |
| partially recovered | 117 | 4,930,706 | 4,903,878 |
| provider absent | 12 | 1,263 | 0 |
| source conflict | 9,194 | 6,863,688 | 0 |

There were no failed recovery batches and no supplements for any source-conflict symbol. The
largest remaining gap is the quarantined `1,033,456`-minute FETUSDT range. The recovery ledger
references `20,521` distinct source payload checksums; its logical supplement hash is
`e79c147039249362feba0ef153ea7417bcabab7a3e0c0c670e414f91f2adf6e1`.
The batch ledger contains `28,765` completed bounded checkpoints. Recovery resumes from the last
completed boundary, publication is guarded by a database advisory lock, and a partial unique index
enforces one validated supplement per canonical key; the populated table has zero duplicate keys.

Compatible-symbol results are:

| Symbol | Added rows | Remaining minutes | Post-recovery coverage | Non-full gaps |
|---|---:|---:|---:|---:|
| ADAUSDT | 2,107,519 | 4,741 | 99.8874% | 20 |
| ALGOUSDT | 0 | 90 | 99.8200% | 1 |
| APTUSDT | 129,597 | 0 | 100.0000% | 0 |
| BNBUSDT | 2,115,302 | 4,741 | 99.8876% | 10 |
| DOGEUSDT | 1,588,330 | 3,090 | 99.9162% | 17 |
| DOTUSDT | 999,524 | 1,445 | 99.9534% | 10 |
| FTMUSDT | 129,597 | 0 | 100.0000% | 0 |
| IMXUSDT | 129,597 | 0 | 100.0000% | 0 |
| INJUSDT | 887,661 | 1,445 | 99.9518% | 9 |
| LINKUSDT | 1,572,718 | 4,110 | 99.8957% | 21 |
| NEARUSDT | 1,874,548 | 1,444 | 99.9522% | 10 |
| TAOUSDT | 58,061 | 0 | 100.0000% | 0 |
| THETAUSDT | 129,597 | 0 | 100.0000% | 0 |
| XLMUSDT | 2,553,610 | 993 | 99.9641% | 4 |
| XRPUSDT | 2,205,020 | 5,902 | 99.8630% | 26 |
| ZECUSDT | 0 | 90 | 99.8200% | 1 |

ALGOUSDT and ZECUSDT retained their original 90-minute gaps because the authoritative provider had
no observations for those ranges. APTUSDT, FTMUSDT, IMXUSDT, and THETAUSDT now have continuous
internal envelopes, but those envelopes remain short and must not be mistaken for long-history
assets. Source-conflict symbols retain their original coverage pending provenance resolution.

Downstream sequence boundaries are derived from the combined canonical series rather than from the
broader original gap ledger. On XRPUSDT this produces `27` exact remaining holes totaling the same
`5,902` absent minutes (largest `600` minutes); the count differs from the `26` non-full original
ranges because partial recovery can split one original range into multiple holes. No boundary is
filled, interpolated, or fabricated.

## Downstream feature and event safeguards

Phase 3 does not change source or supplemental candle storage. It consumes only versioned Phase 2
snapshots and carries the dump-derived dataset identity, auction configuration, profile definition,
window policy, symbol, timeframe, and exact canonical segment into every feature row and event.
Identity drift, unexplained gaps, duplicates, or out-of-order observations fail closed; rolling
history is cleared at every approved boundary.

Feature information cutoffs are the exclusive close of the real source candle. Structural event
metadata records both the source candle-open timestamp and the later observable trigger timestamp.
There is no interpolation, forward fill, arbitrary epsilon, synthetic candle, or future/outcome
field. The registered discovery leakage audit rejects returns or labels that depend on a future
observation.

Training normalisation uses only a caller-frozen training partition and exact bounded-memory sorted
runs. Feature/event Parquet outputs are derived artifacts stored separately from immutable source
and supplemental candles. Their manifests pin upstream identities, registered schemas, code and
lockfile hashes, partition checksums, missing-value counts, and overlap evidence; repeating an
identical publication is content-idempotent, while disagreement or tampering fails closed.

Read-only verification after Phase 3 reconfirmed the source dump SHA-256 above, `35,748,117`
immutable source rows, `16,480,681` validated supplements, all `19,192` original gaps terminally
classified, no supplements on source-conflict symbols, and no restored Callscore application data
or derived Callscore candle fields.

## Daily freshness readiness

The daily updater now freezes an explicit last-closed-minute cutoff, reuses the append-only recovery
ledger, and writes checksum-bearing plans and terminal reports outside Git. A same-cutoff retry is
idempotent; a later cutoff plans only actual canonical holes; blocked sources are never fetched.
Daily operation updates the dump-preferred PostgreSQL canonical view. Full immutable Parquet exports
are created only when a research run deliberately declares a new dataset version.

The operator must run the explicit idempotent `msl-sync-candles bootstrap` command once after a
deliberate crypto-only restore. It verifies the immutable dump and reviewed source identity before
applying the append-only migration. Scheduled daily runs start at `plan` and never initialize
`data/postgres` implicitly.

The compatibility gate has not changed: the validated artifact remains
`482f3a09a4e24a62b0ad92f5bb90028eead67fe961eb1d3320dd38d13fd105d2`, and the nine conflicts listed
above keep the complete 25-symbol universe unhealthy. This is an explicit provenance limitation,
not an operational failure and not permission to fill or replace those candles. No live network
freshness run or scheduled automation has yet been executed. A disposable PostgreSQL integration
profile verified migration reapplication, dump preference, append-only supplements, same/later
cutoffs, blocked-source no-fetch, advisory locking, and report conservation without touching
`data/postgres`.
