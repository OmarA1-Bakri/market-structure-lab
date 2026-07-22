# Task 15 — Market Structure Lab Phase 5 Validation Plan

## Status and authorisation

This is the separate Task 15 validation-plan artefact. It defines an authorised validation boundary;
it does not claim that validation has occurred. Task 14 closed at implementation/evidence checkpoint
`5db2705a1cba37ee10d5eb3bd1fa470a5b628e3d`, with evidence follow-up
`3482882c864f1471ec1dd682631544d0c404c542`; both were pushed and remotely verified before this
document was created. The user's 2026-07-22 master prompt explicitly authorises publication of this
plan and immediate execution of a separately tracked bounded Phase 5 implementation plan. Phase 6,
strategy construction, portfolio, paper/live trading, exchange integration, and leverage remain
unauthorised.

No new dependency is authorised for the bounded implementation. Use the Python standard library,
Polars, SciPy, and existing canonical JSON, hashing, bounded-I/O, and immutable-publication
primitives. A third-party backtesting or execution framework is explicitly rejected for this phase.

## Programme goal

Determine, rather than assume, whether frozen recurring behaviours and simple OHLCV-observable candidate families contain reproducible cost-adjusted information after causal timing, serial dependence, multiplicity, robustness, and untouched-data controls.

## Required sequence

1. Use verified Task 14 close-out checkpoint `5db2705a1cba37ee10d5eb3bd1fa470a5b628e3d`
   as the immutable upstream research baseline; do not rewrite `PG-000001` through `PG-000004`.
2. Review and publish this Task 15 plan and its test specification as a distinct checkpoint.
3. Create and publish a separate bounded Phase 5 implementation plan before source-code changes.
4. Implement the bounded Phase 5 stack test-first.
5. Demonstrate one complete synthetic no-edge lifecycle through a thin vertical orchestrator over
   small typed functions; extract shared primitives only after their contracts are proven, without
   creating a monolith to refactor later.
6. Run non-final development validation strictly in order A -> B -> G -> E -> D.
7. Kill, reject, or retain candidates using frozen code-defined rules.
8. Open a programme-scoped final-holdout batch only if every frozen eligibility rule passes before
   row iteration; otherwise record that the final holdout was not opened.
9. Stop before Phase 6 or any trading activity.

## Research population and source aggregation

- Primary source: canonical one-minute OHLCV.
- Primary validation bars: deterministic complete 1h and 4h bars.
- Optional 15m: only for D or G when an outcome-blind detector event-count/dependence report proves the 1h/4h event unit cannot meet the frozen support design. Outcome variance, return magnitude, and final-holdout data cannot influence this gate. A changed timeframe creates a new preregistration/programme identity.
- Primary universe: compatible longer-history symbols proven by source/publication evidence.
- BTCUSDT and ETHUSDT remain excluded while provenance conflicts are unresolved.
- Eligible and excluded symbols and reasons are frozen before outcomes are attached.
- One-minute rows are never treated as independent validation samples.

All Phase 5 identities, seeds, ordering keys, and deterministic scalars use
`hash_json(domain, fields)`: SHA-256 over UTF-8 schema-versioned canonical JSON containing separate
`domain`, `schema_version`, and named `fields` keys, with sorted keys, no insignificant whitespace,
UTC timestamps in canonical ISO-8601 form, and finite decimal values serialised by the repository's
canonical numeric policy. Raw concatenation of variable-length fields is prohibited. Every
aggregate-bar publication is immutable and binds its parent snapshot SHA, symbol, target interval,
half-open interval boundaries, ordered canonical-row identities/counts/digest, segment/continuity
identity, configuration SHA, and artefact SHA. A
canonical-row identity is `hash_json("canonical-row-identity-v1", {parent_partition_sha256,
symbol, source_timeframe, timestamp, segment_id, canonical_ohlcv})`; it does not assume an
unavailable database row ID. Streaming/chunked and single-batch aggregation must replay
byte-identically. Partial, gapped, duplicate, reordered, mixed-symbol, mixed-timeframe,
mixed-segment, or cross-boundary input rejects without filling.

## Exact causal clock

- A candle timestamp denotes `bar_open`.
- A bar covers `[bar_open, bar_close)`.
- A signal using that completed bar has `information_cutoff = bar_close`.
- Ordinary legal entry is the same instant as the next contiguous bar's `bar_open`; no signal-bar fill is permitted.
- Family D requires its separately completed reclaim-confirmation bar, then entry at the following contiguous bar open.
- Outcome and path intervals are `[entry, exit)` and include the entry minute but exclude the exit instant.
- Missing or non-contiguous entry, path, or exit data rejects outcome attachment; it is never filled or shortened silently.

## Candidate families and order

### A — conventional comparators

Completed-bar moving-average trend, Donchian breakout, ATR breakout, and time-series momentum on 1h and 4h. A evidence is produced first and each outer fold chooses its comparator only within inner training/calibration folds. A's negative expectancy does not prevent B/G/E/D from using that frozen comparator; only causal, data-quality, support, or publication invalidity blocks dependent work.

### B — value migration and acceptance

Test only as incremental lift over the fold-specific A comparator. Publish baseline-only, structure-only, and combined evidence. The incremental estimand uses the common baseline opportunity population and reports the augmented selected subset, its complement, a selection-rate-matched placebo, and paired/block-bootstrap lift. Kill B when net incremental development lift does not survive costs, correction, robustness, and the matched placebo.

### G — compression followed by expansion

Use deterministic completed-bar compression and expansion definitions. Compare against matched ATR and Donchian controls. Permit 15m only through the outcome-blind gate above.

### E — volume-confirmed breakout

Layer one frozen current-volume-above-prior-24h-median filter over a price-only breakout. Use the
same common-opportunity/subset/complement/selection-rate-matched-placebo design as B. The continuous
volume/median ratio is diagnostic only. Any additional threshold or formulation requires a new VP,
preregistration, and complete search count.

### D — failed-break reclaim reversal

Require a previously frozen reference level, causal breach, completed close back inside or failed acceptance, a separate completed reclaim confirmation, and following-bar entry. Compare with failed Donchian and the exact prior-week Donchian pseudo-level control defined below. Never describe results as liquidity or order-flow evidence.

## Candidate and trial contracts

Every candidate manifest binds:

- stable H/behaviour origin and VR-family identity; a real Task 14 behaviour link is used only when one exists;
- explicit `human_origin` for allowed H candidates when Task 14 yields no relevant behaviour; no invented DR/B link;
- family, comparator role, directions, timeframes, horizons, selectors, and exact bounded grid;
- symbol universe and aggregate-publication identities;
- completed-bar formula, parameters, feature interval, information cutoff, legal entry, label interval, and outcome path convention;
- continuity, resampling, missingness, event-compression, purge, embargo, development folds, asset holdouts, and final-holdout metadata;
- costs, controls, placebos, trial-family count, correction family, primary estimand, test sidedness, alpha, and confidence level;
- effective support, interval-width, decision, rejection/inconclusive, and kill rules;
- code commit, lockfile, dataset/snapshot/publication, seed, configuration, and work-budget identities.

Use a dedicated `ValidationExperimentConfig` and `VR` receipt. Existing discovery `ExperimentConfig` requires feature-publication/registry/normaliser identities and cannot truthfully represent pure aggregate-bar A candidates. Reuse only generic canonical JSON, atomic-write, hashing, bounded-read, and terminal-ledger primitives.

Execution status (`completed`, `failed`, `abandoned`) is orthogonal to scientific decision (`not_evaluated`, `rejected`, `inconclusive`, `supported_development`, `validated`, `promoted`). A completed computation may be scientifically rejected; a failed or abandoned computation must be `not_evaluated`.

Receipt ownership is explicit. One immutable `VP-######` programme root owns the programme manifest,
the exact 1,104-evaluation ledger, aggregate publications, family correction sets, and the single
programme-scoped final-holdout access state. Each core, primary, baseline, control, perturbation,
robustness, exposure, or capacity evaluation has its own immutable `VR-######` receipt under that
root, binds its candidate/evaluation role, and terminates independently. Candidate summaries may
reference many VR receipts but cannot own or create a separate final-access state. Retry never
overwrites either level.

## Exact initial detector specification and justification

These constants are engineering comparators chosen before outcomes from the repository's 24/7
civil-day scale and documented roughly two-year primary-data horizon. They are not claimed financial
thresholds. Independent quantitative review must confirm before the tracked Task 15 freeze that none
was selected from outcome evidence. Family order is immutable: A first, then B, G, E, and D; B, G,
E, and D are subordinate to their declared simple comparators and cannot bypass baseline evidence.

Common rules: for target-bar duration `Delta`, every hour window uses `n_H=H/Delta` target bars and rejects when non-integral. Thus `(n8,n24,n72)=(8,24,72)` at 1h and `(2,6,18)` at 4h. Crypto is a continuous UTC market with no artificial session reset; source segment/gap/symbol/timeframe changes reset all state. Lookbacks use completed bars only. Strict inequalities emit no signal on ties. A candidate emits only on transition from false/other-direction to true; repeated state is suppressed. No same candidate/symbol/direction event may overlap its frozen outcome interval. Warm-up or missing references yield no event, never imputation. Long/short definitions are symmetric.

- **A moving-average crossover:** one `(fast, slow)=(24h,72h)` pair. At completed bar `t`, compute simple means over the converted `n_fast`/`n_slow` bars including `close_t`; long when `SMA_fast(t) > SMA_slow(t)`, short when `<`, and emit only when the non-zero sign differs from `t-1`.
- **A Donchian:** lookbacks 24h and 72h. Boundaries use the converted `n_L` highs/lows from bars `t-n_L..t-1` and exclude `t`; long when `close_t > upper`, short when `close_t < lower`; re-arm after a close returns inside/inclusive of the channel.
- **A ATR breakout:** define `ATR24_prior(t)` as the arithmetic mean of true ranges for completed bars `t-n24..t-1`, excluding expansion bar `t`. Long when `close_t > close_(t-1) + ATR24_prior(t)`, short when `< close_(t-1) - ATR24_prior(t)`. The one-ATR unit avoids an outcome-tuned multiplier grid.
- **A TSMOM:** lookbacks 24h and 72h. Long when `close_t > close_(t-L)`, short when `<`; emit on sign transition.
- **B value migration/acceptance:** rolling 24h and 72h one-minute profiles use `uniform-touched-v1`, 70% value area, rolling window, and a per-symbol fixed bin step taken only from verified source price-precision/config metadata and bound in profile identity. At A signal bar `t`, compare profiles frozen at `t-2` and `t-1`; long structure requires POC and value-midpoint indices increase strictly and completed closes at `t-1` and `t` lie inclusively inside the `t-1` migrated `[VAL,VAH]`; short is symmetric. Thus the complete B state exists at the A cutoff and baseline/combined entries both use open `t+1`. Missing references yield no event.
- **G compression/expansion:** bars `t-3,t-2,t-1` must each be completed and have `ATR8(j)/ATR24(j) < 1.0`; each arithmetic ATR ends at `j` and contains converted `n8`/`n24` bars. Expansion bar `t` follows them; emit one event per run when `TR_t > ATR24_prior(t)` and `close_t` strictly breaks the Donchian24 boundary from bars `t-n24..t-1`. The ATR-only control uses every otherwise legal bar with `TR_t > ATR24_prior(t)`, assigns direction from the strict sign of `close_t-close_(t-1)`, and omits compression and Donchian conditions. The Donchian-only control uses every otherwise legal strict Donchian24 close break and omits compression and ATR conditions. Both enter at the same next-bar clock and are SHA-selected within symbol/fold/week/direction/horizon strata to the candidate count; shortage is inconclusive.
- **E volume confirmation:** opportunity population is A Donchian 24h/72h breakouts. Selected events require current completed-bar volume strictly greater than the median of prior 24h bars, excluding current. The continuous volume/median ratio is diagnostic, not a second initial threshold.
- **D failed-break reclaim:** reference is the prior 24h or 72h Donchian boundary. Upward failure: `high_t > upper` and `close_t <= upper`; next completed close strictly below the frozen upper; enter following open short. Downward failure is symmetric. Allow one event per frozen level/run. The failed-Donchian control uses the same breach and failed close at `t`, omits only the confirmation condition on `t+1`, still waits for completed bar `t+1`, and enters at open `t+2` so its outcome clock matches the candidate. The pseudo-level control substitutes the same symbol/timeframe/lookback/direction Donchian boundary frozen at exactly `t-7 days`, then applies the identical breach, confirmation, entry, and horizon rules at the current clock. Missing or cross-segment prior-week history is unavailable; controls are SHA-selected within symbol/fold/week/direction/horizon strata to the candidate count, and shortage is inconclusive.

Ordinary entry price is the next contiguous aggregate-bar open. D enters one bar later. An N-bar
holding exits at the open exactly N aggregate bars after entry. Delayed-entry sensitivity enters one
additional contiguous bar later and holds N bars from that entry. Signed return is side-adjusted
entry-to-exit return. On the complete one-minute `[entry,exit)` path, gross excursion diagnostics are
entry-notional-normalised and exclude fees: long
`MFE=max(high/P_entry-1)`, long `MAE=min(low/P_entry-1)`, short
`MFE=max(1-low/P_entry)`, and short `MAE=min(1-high/P_entry)`. The entry minute is included and the
exit instant is excluded. Missing path minutes reject attachment.

The initial candidate-specific outcome grid is fixed before attachment:

| Family | Primary timeframes | Frozen outcome horizons | Path evidence |
|---|---|---|---|
| A | 1h and 4h | signed 24h net return | complete path plus MFE/MAE |
| B | 1h and 4h | paired signed 24h incremental net return on the A opportunity population | identical entry/path convention to A |
| G | 1h and 4h | signed 8h and 24h net return | complete path plus MFE/MAE |
| E | 1h and 4h | paired signed 24h incremental net return on the price-breakout opportunity population | identical entry/path convention to its price-only control |
| D | 1h and 4h | signed 8h and 24h net return after the separate confirmation bar | complete path plus MFE/MAE |

The optional 15m G/D branch is excluded from the initial grid and can be opened only by the
outcome-blind event-count/dependence gate above under a new programme identity. A horizon that is not
an integral number of target bars rejects at preregistration.

Initial core execution slots: A `24`; B `32` (baseline/structure/combined/placebo for eight specifications); G `24` (candidate/ATR/Donchian for eight); E `24` (price/filtered/placebo for eight); D `48` (candidate/failed-Donchian/pseudo-level for sixteen), total `152`. Primary inferential slots are A `24`, B `8`, G `8`, E `8`, D `16`, total `64`. Base/stress/delay/robustness are diagnostic evaluations, not additional selectable hypotheses. A 15m branch or grid change creates a new programme and count.

For each A primary slot, compare the candidate's base-cost weekly-mean net-return vector with all
three count/calendar/direction/horizon-matched baseline vectors: naive zero/no trade, unconditional,
and persistence. The A primary decision is one worst-control intersection-union statistic:
`primary_p=max(two_sided_p_vs_naive, p_vs_unconditional, p_vs_persistence)` and
`primary_lower_bound=min(95pct_lower_bound_vs_naive, lower_vs_unconditional,
lower_vs_persistence)`. Other A detector specifications remain separate corrected primary slots;
they are not silently selected as controls. A is rejected when this corrected worst-control effect
or any common cost/robustness gate fails, and is inconclusive when a matched control lacks support.

For B/E, build paired vectors over every baseline opportunity at the same legal entry/horizon: baseline vector contains its trade return; augmented vector contains the selected trade return or exact zero with zero trade cost when unselected; complement and a seeded selection-rate/calendar-block/direction-matched placebo vector are retained. The primary lift is the worst-control intersection-union statistic versus baseline and placebo: `primary_p=max(two_sided_p_each_control)` and `primary_lower_bound=min(two_sided_95pct_lower_bound_each_control)`. G/D use the same single-slot intersection-union rule against both declared controls on frequency/calendar/direction/horizon-matched opportunities. All component contrasts remain published. This reconciles multi-control evidence with the 64 primary slots.

## Outcomes, folds, and leakage controls

- Outcomes attach only to frozen signal/event IDs and exact aggregate/source publication identity.
- Return horizons and MFE/MAE paths are candidate-specific and fixed before attachment.
- Purging is interval based at every inner and outer validation boundary: remove a training or
  calibration event whenever its feature or label interval intersects any test event's protected
  feature/label interval.
- For each candidate and every inner or outer test interval `[T_start,T_end)`, embargo is the exact
  candidate outcome horizon `[T_end,T_end+horizon)`. Any event whose feature or label interval
  intersects that embargo is excluded from subsequent expanding training/calibration as well as the
  current boundary calculation. The embargo definition and excluded identities are bound to the
  fold identity. Width slogans cannot substitute for interval-overlap tests.
- Chronological nested walk-forward uses outer development test folds and inner training/calibration folds. Outer outcomes never select a comparator or parameter.
- The selector identity and complete A grid are subordinate inputs to B/G/E/D identities.
- Asset holdouts remain unchanged after preregistration.

The real-run universe is selected from promoted RR snapshot coverage metadata only: exclude
BTCUSDT/ETHUSDT, every unresolved source-conflict or incompatible mapping, and every symbol whose
verified metadata does not span the common interval. Require at least five eligible symbols and a
common inward-rounded UTC interval of at least 730 complete calendar days; otherwise reject before
outcomes. Trim only earliest excess whole days so the common timestamp grid divides into six equal
chronological blocks. Blocks 0–4 are development; block 5 is untouched temporal holdout. Outer folds
are expanding: train blocks `0..k-1`, test block `k`, for `k=1..4`. For each target timeframe, derive
one common ordered timestamp grid before attaching symbols. For an outer-training interval, let `n`
be the number of timestamps on that grid, never the number of pooled symbol rows; let
`q=floor(n/4)` and `r=n-4q`. Exclude and identity-bind the earliest `r` timestamps, then form four
consecutive `q`-timestamp subblocks and apply the identical time boundaries to every eligible
symbol. Use subblock 0 as initial inner training and subblocks 1–3 as expanding inner validation
tests. Adding or reordering otherwise eligible symbols cannot change a time boundary. This formula
applies independently at 1h and 4h, with the inner/outer purge and embargo rules above applied at
every test boundary.

Asset holdout membership is outcome-blind: let `N_symbols` be the frozen eligible-symbol count, sort
eligible symbols by
`hash_json("asset-holdout-order-v1", {programme_id, symbol})` and reserve the final
`ceil(N_symbols/5)` symbols, at least one, from every development outcome. The components are
disjoint and
identity-bound: temporal holdout = development symbols over block 5; asset holdout = reserved
symbols over blocks 0–4; combined = their union. Reserved symbols over block 5 are explicitly unused
and never iterated. Gaps remain explicit segment boundaries and produce no filled bars/events.

## Statistical policy

- Primary estimand: mean net signed event return for directional families; B/E primary incremental estimand is paired mean net return lift on the frozen common opportunity population.
- Tests are two-sided with programme alpha `0.05` and 95% confidence intervals. Allocate `0.01` to each of A/B/G/E/D; apply Holm within each family's frozen primary hypothesis slots. This Bonferroni family allocation controls programme FWER without result-driven family selection.
- Every primary slot exists before execution. An unevaluable, failed, or inconclusive slot receives conservative `p=1` for Holm while retaining its actual execution/scientific status and search count. Comparator/control outputs are diagnostic unless their paired lift is the declared primary B/G/E/D hypothesis; diagnostic p-values cannot promote.
- Confidence intervals use deterministic synchronised calendar-block bootstrap across crypto assets so a shared market shock is not sampled independently per asset.
- Effective support is the number of non-overlapping synchronised calendar bootstrap blocks containing an admissible event. Let `sigma_block` be the inner-training weekly-block standard deviation frozen by the bootstrap policy. Define scalar `MDE` as the maximum finite positive base round-trip cost, already normalised to entry notional, across every admissible inner-training opportunity for that slot/fold. A missing/invalid preregistered cost policy or non-positive/non-finite configured cost fails preflight with execution `failed` and scientific decision `not_evaluated`; missing event-level cost coverage after a valid policy yields execution `completed` and scientific decision `inconclusive`. Identity-bind the opportunity set, maximum value, and all tied row identities. For final eligibility freeze the analogous maximum over all development opportunities before access. Let `alpha_power=0.01/m_family` for the frozen number of primary family slots, and `z` the standard-normal quantile. Required support is `ceil(((z_(1-alpha_power/2)+z_0.80)*sigma_block/MDE)^2)`. Observed support below it or 95% interval full width above two base round-trip costs is `inconclusive`, never threshold-relaxed.
- Negative controls include legal-block label shuffles, causal time shifts, random features, and family-specific placebos/pseudo-levels.
- PBO is not an initial decision statistic because the programme uses chronological nested walk-forward rather than combinatorial path selection. Deflated Sharpe is not applicable to the primary mean-event-return estimand; if a later authorised decision uses Sharpe or combinatorial selection, applicability must be re-planned and identity-bound.
- Linear/tree baselines are not applicable because no high-capacity ML model is in this phase. The required capacity hierarchy is naive/unconditional, persistence where meaningful, and simple trend/breakout comparators.
- The bounded 152-core-slot grid is the initial specification curve. Publish every per-fold estimate, direction, and degradation from inner calibration through outer development; pooled values cannot hide worsening or single-fold evidence.
- Discrimination/calibration metrics are not applicable to the continuous signed-return estimand. They require a separately frozen binary/probabilistic outcome.

## Exact resampling, baselines, controls, and evaluation accounting

Bootstrap blocks are consecutive half-open UTC weeks `[Monday 00:00, next Monday 00:00)`. An admissible week is wholly inside the relevant unpurged fold, has complete source/publication coverage, and contains at least one value for every vector in the paired contrast. Within each week compute the unweighted mean across its admissible event values; the primary estimate is the unweighted mean of admissible weekly means, so weeks—not dense events—are the inferential units. The same week indices are used across assets.

For each fold/purpose, seed is the first unsigned 64 bits of
`hash_json("validation-seed-v1", {programme_id, candidate_id, fold_id, purpose})`. With `W`
admissible weeks, each of exactly 4,096 draws samples `W` week means with replacement and averages
them. Sort draws ascending; use zero-based indices `102` and `3993` as the non-interpolated 2.5% and
97.5% bounds (`ceil(p*4096)-1`). Two-sided p-value is
`min(1,2*min((1+n_le_zero)/4097,(1+n_ge_zero)/4097))`. `sigma_block` is the sample SD of those same
observed weekly means and effective support is `W`, making the power formula estimator-consistent.
Fewer than two weeks, empty/zero-variance weeks, or non-finite values are inconclusive. Outer-fold
weeks/support/sigma come only from inner training; final eligibility freezes them from development
before access. Final outcomes never redefine blocks/support/MDE/power. `MDE<=0` rejects
configuration.

Every primary slot has three baseline ledger entries: (1) naive zero/no-trade vector on identical
opportunities; (2) unconditional direction-matched non-overlapping horizon entries chosen within
symbol/fold/direction/week by smallest
`hash_json("unconditional-control-order-v1", {candidate_id, row_identity})` to the primary event
count; (3) persistence direction from the strict sign of the immediately prior completed-bar close
return at the same legal opportunity. Matching shortage is inconclusive and never borrows across
strata.

Every primary slot has three negative-control entries. (1) Label shuffle: define the donor pool as
every complete legal-entry opportunity in the frozen symbol/direction/UTC-week stratum,
Fisher-Yates-permute its outcome labels with the slot seed, then read the permuted labels at the
unchanged candidate event IDs; insufficient donors is inconclusive. (2) Causal time shift: move each
frozen signal/event forward exactly one UTC week. The shifted feature interval, signal, legal entry,
and full label path must remain wholly inside the identical
programme/publication/symbol/timeframe/segment/fold/non-final-component identities; any inner/outer
fold, asset component, temporal block, gap, or final-holdout crossing is dropped and shortage is
inconclusive. The original signal is therefore known before the placebo event without crossing an
evidence boundary. (3) Random-feature check: assign each legal opportunity a scalar
`u=uint64(hash_json("random-feature-scalar-v1", {seed, row_identity, purpose: "feature"}))/2^64`,
select the lowest `u` values to match exact symbol/fold/week counts, and assign directions by a
separately domain-tagged hash order to match long/short counts; neither selection nor direction
reads outcomes. B/E matched placebo uses an independent domain-tagged scalar with the same matching
rule. All attempts are immutable.

Counts frozen before iteration: 152 core executions; 64 primary hypotheses; 192 naive/unconditional/persistence baselines; 192 shuffled/time-shift/random negative controls; 256 fixed robustness evaluations (doubled cost, one-bar delay, ex-strongest asset, ex-strongest UTC year for each primary); 184 one-at-a-time lookback perturbations; 64 exposure residuals; 64 capacity diagnostics; total `1,104` ledgered evaluations. Perturbations are `n_L-1` and `n_L+1` target bars around the timeframe-converted values, one parameter at a time, with identity-bound originals: A MA fast/slow (16 evaluations), A Donchian (16), A ATR (8), A TSMOM (16), B profile lookback (16), G ATR-short/ATR-long/Donchian (48), E Donchian/volume-median (32), D level lookback (32). Compression-run length, B acceptance count, D confirmation count, inequality units, directions, horizons, and costs are fixed contract axes, not tunable parameters. Every evaluation counts in `ValidationWorkBudget`; only the 64 primary slots enter Holm, while named controls/robustness are mandatory Boolean gates.

## Costs, exposures, capacity, and robustness

The initial programme validates spot-cash execution only, matching the verified Binance Spot source
compatibility boundary. Funding is identity-bound as `not_applicable_spot_v1`; it is not silently
zero. A perpetual/funding-bearing instrument requires a new programme and cost policy. For side `s`
in `{+1,-1}`, one unit entered at `P_entry` has entry-notional-normalised
`gross=s*(P_exit/P_entry-1)`. The source cost artefact stores non-negative execution-notional rates
for each leg: `fee_rate`, `half_spread_rate`, and `slippage_rate`, plus `fill_probability` in
`(0,1]`, all with units, venue/symbol/time/effective-date provenance. Let `R=P_exit/P_entry`. Entry
leg cost is the sum of entry rates. Exit leg cost is `R * sum(exit rates)` for both long and short
because exit execution notional is `P_exit` per unit. For a separately authorised funding-bearing
policy, funding cost would be
`sum(abs(funding_rate_k) * mark_price_k/P_entry)` over sourced funding instants in `[entry,exit)`;
the current spot policy forbids such rows. Thus
`conditional_filled_net = gross - entry_cost - exit_cost - funding_cost`, all on entry-notional
denominator, and `expected_net=fill_probability*conditional_filled_net`; a missed fill is exactly
zero with no cost. Base uses sourced rates. Doubled stress doubles every applicable rate before the
same normalisation and sets fill probability to its square. Delay uses the separately legal path.
No random fill simulation occurs. A missing, unit-incompatible, non-finite, or non-positive
preregistered cost policy fails preflight as execution `failed` / scientific `not_evaluated` before
outcomes. Missing event-level coverage under a valid policy is execution `completed` / scientific
`inconclusive`, never a zero-cost default.

Report opportunity count, trades, turnover, filled-notional expectation, and volume-relative capacity/market-impact diagnostics. Capacity absence does not block `supported_development` or `validated`; it sets separate `promotion_eligibility=inconclusive_capacity` and makes `promoted` unavailable without changing the validation decision.

Exposure diagnostics use observable OHLCV-derived equal-weight market return, market-trend state,
realised-volatility rank, volume/turnover rank as a liquidity proxy, asset, UTC year, fold, and frozen
regime labels. They never imply order-flow, institutional, whale, participant, or causal liquidity
information unavailable from OHLCV.

At each event clock, the equal-weight market-return factor is the mean completed-bar return of every
eligible non-target asset with a complete contemporaneous bar; fewer than three non-target assets is
inconclusive. The market-trend factor is the strict sign of the equal-weight 72h TSMOM of those same
non-target assets, not the fold-selected candidate A rule. An exact zero/tie has no trend regime;
the event remains in primary evidence but is unavailable for the regime diagnostic and therefore
reduces regime support rather than being mapped to a fifth state. Realised-volatility and turnover ranks
place the target value against the empirical distribution of non-target assets using only completed
prior-24h observations. Continuous factors are scaled on inner training only. Regime labels are the
four outcome-blind combinations of market-trend sign and non-target equal-weight prior-24h realised
volatility above/below its inner-training median; the median, eligible assets, source rows, and label
identity are frozen before outer application. Final outcomes never fit a factor or regime.

The inner-trained OLS residual lower-bound gate is mandatory. Regime evidence is a separate ordered
robustness gate: a regime is supported with at least two admissible UTC weeks. First, if fewer than
two regimes are supported, the result is `inconclusive`. Otherwise, if fewer than two supported
regimes have positive point estimates or any supported regime has a 95% upper bound below zero, the
result is `rejected`. Otherwise the regime gate passes and every per-regime estimate remains
published. This prevents a one-regime result from being described as robust without importing a
financial threshold.

Required robustness and exposure predicates are the exact machine-defined rules in development eligibility below. For each asset and UTC calendar year, compute its development-only strength as the primary base-cost net contrast using the same unweighted weekly-mean estimator restricted to that asset/year. Select the largest finite strength; ties break by lexicographically smallest symbol or earliest year. No-event strata rank below finite strata. Identity-bind the score table and exclusion, then rerun after excluding it. Final outcomes never influence the selector. Each candidate binds retirement/invalidation metadata. No third-party backtesting framework is adopted; the first bounded lifecycle is a pure validation transaction whose stable primitives are extracted afterwards.

## Aggregate work budget

A frozen `ValidationWorkBudget` preflights, before row iteration or numerical allocation:

- source one-minute rows and bytes;
- aggregate bars, symbols, and ranges;
- candidates, complete search trials, events, and attached outcomes;
- one-minute path cells;
- outer/inner folds and model/evaluation calls;
- bootstrap draws/cells and synchronised blocks;
- controls, placebos, perturbations, and robustness reruns;
- artefact count and serialised bytes;
- dashboard evidence count/bytes;
- final-holdout candidate count and programme batch size.

Every value capable of changing admissibility, evidence, or results is in the configuration/receipt identity. Pathological but structurally valid configurations reject at deterministic preflight.

## Programme-scoped final holdout

- Programme preflight first proves all 1,104 evaluations are terminally accounted, identities/hashes/reviews pass, and no unresolved programme blocker exists.
- Per-candidate development eligibility then applies the exact conjunctive predicate below.
- The batch contains every and only eligible candidate, canonically ordered by family order A/B/G/E/D then candidate ID; its canonical list hash is receipt-bound. Zero eligible candidates writes `final_holdout_not_opened_empty_batch` and creates no access attempt.
- A hash-bound eligibility receipt binds the whole batch, code/data/lock/config/trials/costs/controls and decisions. State machine is `sealed -> access_attempted -> completed|failed`. An atomic create-if-absent `access_attempted` record occurs before row iteration; concurrency/replay rejects and crash/partial failure consumes the attempt.
- No candidate, threshold, selector, or family may be added after freeze. Final results cannot change the grid or trigger a second batch.

Exact per-candidate development eligibility is conjunctive and evaluated after programme preflight:
execution completed; decision is `supported_development`; its family Holm passes at 0.01; primary
worst-control lower bound, base-cost lower bound, doubled-cost lower bound, one-bar-delay lower bound,
worst named perturbation lower bound, ex-strongest-asset lower bound, and ex-strongest-UTC-year lower
bound all exceed zero; at least three of four outer-fold point estimates and fold 4 are positive; the
regime gate above passes; the residual mean after an inner-trained OLS projection has a 95% lower
bound above zero. Its design uses one intercept, scaled continuous equal-weight non-target market
return, market-trend, realised-volatility-rank, and turnover-rank factors, plus treatment-coded
asset/year indicators with the lexicographically first training asset/year omitted as fixed
references. Scaling/reference/rank are fit on inner training only, unseen outer levels map to
all-zero reference indicators, and any remaining rank deficiency is inconclusive; power/support and
width formulae pass; cost provenance/reviews are complete. Rank-deficient exposure, missing
cost/data, inadequate support, or excessive width is inconclusive; a completed non-positive
robustness/control criterion is rejected. Promotion additionally requires capacity/impact evidence.

## Decision hierarchy and family kill rules

- `rejected`: frozen rejection/kill rule met.
- `inconclusive`: support, cost evidence, interval precision, data quality, or required controls insufficient. Capacity absence is recorded only in separate promotion eligibility.
- `supported_development`: every non-final gate passes; not an edge.
- `validated`: use final-holdout outcomes only—never pool with development or reselect. Within the frozen batch, apply the same 0.01-per-family Holm rule with unevaluable slots `p=1`. A candidate validates only when temporal and asset-holdout component point estimates are each positive and the combined holdout-only primary worst-control, base-cost, doubled-cost, and one-bar-delay 95% lower bounds exceed zero with adequate frozen-formula support/width. Missing/unreadable/support/cost evidence is `inconclusive`; a completed effect/control/cost/delay failure is `rejected`. Known-pass/known-fail batch fixtures lock this function.
- `promoted`: only after all Phase 5 promotion and capacity/cost gates pass; no strategy is created.

Kill/reject a completed candidate when any machine-defined primary worst-control, base/doubled-cost, delay, adjacent-lookback, ex-asset, ex-year, fold-sign, or exposure-residual rule above is non-positive/fails its corrected test. Apply the relevant controls for A/B/G/E/D; inadequate/missing evidence is inconclusive under the explicit rules, never described as generic collapse or fragility.

| Family | Hardest falsification | Likeliest failure mode | Terminal research condition |
|---|---|---|---|
| A | unconditional, persistence, and the other simple trend/breakout comparators after costs | ordinary market trend/beta disappears after delay, stressed costs, or exposure residualisation | reject the slot when any corrected effect, cost, delay, perturbation, ex-asset/year, fold-sign, or residual-exposure gate fails |
| B | fold-selected A alone plus a selection-rate-matched placebo on the same opportunities | structural selection merely selects an A subset or one asset/year | kill B when paired worst-control incremental lift fails after costs, Holm correction, and robustness |
| G | both ATR and Donchian expansion controls under an intersection-union decision | generic volatility breakout explains the apparent compression narrative | kill G unless it beats both controls and every common robustness gate |
| E | identical price-only breakout plus matched placebo | volume filters only select volatile/high-turnover periods without incremental value | kill E when paired net lift over price-only and placebo fails |
| D | failed-Donchian and prior-week pseudo-level controls | reclaim events are generic failed breaks or random-level coincidences | kill D unless the legally timed candidate beats both controls and every common robustness gate |

Insufficient support, missing cost/path/control evidence, or excessive interval width is
`inconclusive`, not a pass. A line of research terminates rather than expands its grid when it survives
only one asset, one UTC year, one narrow parameter, or unrealistic cost/delay assumptions.

## Immutable artefacts and evidence

- `VP-######` directory: programme preregistration, Task 14 and Task 15 lineage, aggregate
  publication identities, complete evaluation ledger, family correction sets, work budget,
  programme-scoped final-holdout access record, terminal programme receipt, and human summary.
- `VR-######` directory: one immutable candidate/evaluation configuration, folds, attached outcomes,
  metrics, controls, costs, robustness/exposure evidence, terminal execution/scientific receipt, and
  parent VP identity.
- Every attempted configuration is terminally accounted for; retry never overwrites.
- At least one human-origin H -> VP/VR lifecycle is demonstrated if no genuine Task 14 behaviour supports these families. Rejection is valid evidence; no edge link is fabricated.
- Rerun the repository's relevant quantitative-research/reference sweep before tracked Task 15 freeze, using it only to challenge methods; no generic external threshold becomes policy.

## Task 15 review record

- Repository capability audit: **approved after revision**. Repairs froze canonical domain-separated
  identities, exact signed MFE/MAE, VP/VR ownership, exact G/D controls, no-new-dependency scope,
  thin typed orchestration, common-grid folds, A statistics, spot-cost semantics, and target-excluded
  exposures.
- Adversarial quantitative review: **approved after revision**. Repairs froze inner/outer embargo,
  A worst-control inference, D prior-week pseudo-levels, ordered regime status and zero-trend handling,
  cost-status branches, family kills, and final-holdout transaction semantics.
- Documentation/lineage review: Task 14's rejected programme remains immutable; no behaviour lineage
  was fabricated, no outcome was attached, and no validation result is claimed by this plan.

## Task 15 plan acceptance

- Task 14 is remotely complete before outcome attachment.
- Task 15 plan/test spec are reviewed and checkpointed before Phase 5 source code.
- This document remains a plan: it contains no claim that outcomes were attached, a candidate was
  supported, or validation occurred.
- The rules are specific enough for a separate bounded implementation plan and are consistent with
  actual repository capabilities.
- The Task 15 commit is pushed and the remote branch SHA equals local `HEAD`.

## Downstream bounded Phase 5 acceptance

- The causal clock, interval purging, nested selector, costs, multiplicity, budgets, controls, robustness, immutable receipts, and final-holdout batch are machine verified.
- At least one complete bounded VP/VR lifecycle terminates truthfully even if rejected/inconclusive.
- No live/paper trading, history rewrite, or unauthorised final-holdout access occurs.
