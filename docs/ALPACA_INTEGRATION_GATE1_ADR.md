# ADR: Alpaca Crypto Integration Gate 1

- **Status:** Deferred and blocked at Gate 1; design retained for a later approved phase
- **Decision date:** 2026-07-30
- **Repository commit inspected:** `255aa04ef11e6bcc25028e3c7178f065712c1ba5`
- **Scope:** Crypto market data and paper execution only
- **Canonical data promoted:** No
- **Live trading enabled:** No

## Context

The requested work assumed:

1. an existing Kraken provider path;
2. provider-selectable data, research, dashboard, and API boundaries;
3. shared strategy, risk, order-intent, and execution infrastructure; and
4. an adapter-sized change that could add Alpaca without changing canonical data.

Those assumptions do not match the inspected repository.

The current source boundary is a bounded one-minute candle protocol:

```text
MarketDataSource
  -> execute_work_unit()
  -> Binance-specific ordered reconciliation
  -> append-only reconciliation evidence
  -> optional canonical replacement publication
```

`BinanceSpotSource` is the only concrete provider adapter. There is no Kraken or
Alpaca implementation. The dashboard is a static read-only evidence console, not
a market-data service, and the repository has no shared order, account, risk,
portfolio, or execution boundary.

The requested prompt requires work to stop when the current provider boundary
cannot preserve venue/provenance safely. That condition is proven here.
Additive provider-specific staging would not itself modify canonical data, but
the requested canonical promotion cannot safely reuse the existing keys or
globally active reconciliation view and therefore needs a separately approved
design.

## Decision

Stop before Alpaca adapter, migration, dashboard, canonical-promotion, or paper
execution implementation.

Retain this document as the Stage 1 architecture decision and safe alternative.
Stage 2 and Stage 3 remain closed until:

1. the project phase that owns the work is explicitly approved;
2. a provider-neutral staging and provenance boundary is implemented and
   independently verified;
3. a direct Kraken path exists or the comparison is accurately described as an
   Alpaca-gateway location comparison;
4. canonical promotion cannot erase or collide provider and venue identity; and
5. Phase 6 strategy/risk boundaries exist before any Phase 7 paper adapter.

The existing canonical candle data, restored dump, supplements, reconciliation
publications, and research inputs remain unchanged.

## Evidence

### Existing source and reconciliation boundaries

- `src/market_structure_lab/data/sources/base.py`
  - `MarketDataSource` exposes only bounded `FetchBatch` candle iteration.
  - `SourceKline` is a one-minute OHLCV row.
  - `SourceProvenance` is batch-level and does not give every downstream row a
    durable provider, gateway, feed location, venue, event type, receipt time,
    or raw-message identity.
- `src/market_structure_lab/data/sources/binance.py`
  - `BinanceSpotSource` is the only concrete source implementation.
- `src/market_structure_lab/data/reconciliation/orchestrator.py`
  - `execute_work_unit()` accepts the nominal protocol but passes rows into
    Binance-named comparison fields and classifications.
- `src/market_structure_lab/data/reconciliation/models.py`
  - correction and fill states are Binance-specific.
- `src/market_structure_lab/data/migrations/0002_candle_reconciliation.sql`
  - persisted reconciliation identities and allowed classifications are
    Binance-specific.

### Canonical and application boundaries

- `src/market_structure_lab/data/canonical.py`
  - the canonical frame is keyed by symbol, timeframe, and timestamp and
    exposes OHLCV values without provider/venue selection.
- `src/market_structure_lab/data/loader.py`
  - canonical reads have no provider, gateway, location, venue, or validation
    status selector.
- `dashboard/src/data/lab-evidence.tsx`
  - the dashboard loads a checked static evidence artifact.
- `dashboard/src/components/app-shell.tsx`
  - the application explicitly presents a read-only evidence console with no
    live trading.
- No `backtest/`, `portfolio/`, or execution/order package currently implements
  the shared boundary assumed by the request.

### Phase boundary

`docs/PRD.md` assigns strategy construction and backtesting to Phase 6 and
live-data, paper/shadow order ledgers, execution adapters, risk controls, and
kill switches to Phase 7. The repository instructions prohibit exchange
integration before its approved phase. The completed Task 9 lifecycle is
development validation infrastructure, not a validated strategy or execution
authorization.

## Official Alpaca constraints that the future design must preserve

Official Alpaca documentation currently states:

- crypto WebSocket data uses
  `wss://stream.data.alpaca.markets/v1beta3/crypto/{loc}`;
- documented streams include trades, quotes, order books, minute bars, daily
  bars, and updated bars;
- a message may contain multiple data points;
- event timestamps can carry nanosecond precision;
- late trades can revise the preceding minute through `updatedBars`;
- a zero-volume bar can use quote midpoint prices;
- order-book messages can be incremental or full reset messages;
- historical bars are paginated and capped at 10,000 data points per page;
- paper and live trading use different hosts and credentials; and
- Alpaca warns that paper fills and liquidity assumptions are not proof of live
  execution quality.

The current documentation also exposes an important identity risk. One current
page maps locations such as `us`, `us-1`, and `eu-1` to Alpaca or Kraken feeds
and says providers may change, while an older versioned page says the endpoint
no longer distributes other providers. A future contract must bind the exact
API gateway, feed location, venue claim, source revision, and retrieval time
rather than treating `provider="alpaca"` as sufficient.

Official references:

- [Real-time crypto data](https://docs.alpaca.markets/us/docs/real-time-crypto-pricing-data)
- [Versioned real-time crypto data](https://docs.alpaca.markets/us/v1.1/docs/real-time-crypto-pricing-data)
- [Historical crypto bars](https://docs.alpaca.markets/us/reference/cryptobars-1)
- [Authentication and host separation](https://docs.alpaca.markets/us/v1.4.2/docs/authentication)
- [Paper trading](https://docs.alpaca.markets/us/v1.4.2/docs/paper-trading)

The two real-time pages were retrieved on 2026-07-30. Their response-byte
SHA-256 values were respectively
`bee67e7cab5efaccdff3d33b1224609195ed60d9a6001b0fdcee18514f4ff0c8` and
`814f0ca97e5565c3f5595c3b3a578de710dcd9db21d73761f0a2b6386b3b4c74`.
These hashes record the reviewed mutable documentation snapshots; they are not
provider-data identities.

## Required provider-neutral contract

This is a design contract, not an implemented schema.

### Source identity

Every immutable source envelope must bind:

```text
source_provider
api_gateway
feed_location
market_venue_claim
venue_claim_source
venue_resolution_status
source_revision
event_type
provider_symbol
instrument_mapping_version
event_timestamp_text
event_timestamp_ns
received_at_utc
receipt_timestamp_utc
payload_sha256
raw_receipt_id
replay_version
validation_status
```

`market_venue_claim` is nullable. `venue_resolution_status` explicitly permits
`unknown` and `not_applicable`; an aggregated or mutable feed must not receive a
fabricated venue. Gateway and feed location remain separate from the venue
claim. Execution venue belongs only to later order and fill records. An Alpaca
API response carrying a Kraken-labeled feed must not be relabeled as an
independent direct Kraken observation.

### Timestamp policy

- Preserve the original RFC-3339 timestamp text and parsed UTC nanoseconds.
- Keep event time, receive time, receipt time, and publication time separate.
- Define deterministic minute-window semantics before deriving bars.
- Translate Alpaca's inclusive historical `start` and `end` parameters into the
  internal half-open `[start, end)` contract by requesting a covering range and
  always filtering locally to `start <= event_time < end`.
- Test equality at both boundaries across pagination and retry so adjacent
  requests cannot duplicate an end-boundary observation.
- Treat `updatedBars` as a versioned correction, never an in-place mutation.
- Record reconnect watermarks, detected gaps, duplicate messages, and
  out-of-order messages.
- A daily bar remains provisional until a frozen finality policy is satisfied.

### Symbol and quantity policy

- Use one versioned provider-neutral instrument identifier.
- Preserve the provider symbol (`BTC/USD`, `BTCUSDT`, or venue-specific form).
- Bind base/quote assets, price scale, quantity scale, tick size, lot size, and
  asset-discovery receipt.
- Do not compare base volume, quote volume, trade count, VWAP, or quote-midpoint
  bars as equivalent without an explicit semantic mapping.

## Required staging and promotion boundary

A future migration must create provider-specific append-only staging and
quarantine storage. It must not reuse the current canonical supplement or
reconciliation keys.

Preserve the existing one-minute `MarketDataSource` contract for its current
Binance reconciliation use. Trades, quotes, books, revisions, and streaming
messages require a separate bounded raw-event contract, such as
`RawMarketEventSource` yielding `ProviderEventBatch`. An explicit derived-candle
adapter may later consume verified raw events; the raw-event contract must not
be forced into `SourceKline`.

Raw transport observations must be keyed without deduplicating identical
messages:

```text
(raw_receipt_id, transport_session_id, frame_ordinal, item_ordinal)
```

Provider sequence or trade ID, event timestamp, provider symbol, and payload
digest are attributes and comparison keys, not fallback uniqueness.

Each immutable raw receipt binds:

- a sanitized request or subscription identity;
- endpoint, feed location, and source revision;
- pagination token chain for REST;
- HTTP status, request ID, and retained non-secret rate-limit headers;
- connection/transport session identity;
- exact response or frame bytes and encoding;
- receipt timestamp; and
- payload checksum.

The append-only lifecycle is:

```text
received
  -> staged
  -> validated | quarantined
  -> reconciled
  -> promotion_candidate
  -> promoted | rejected
```

Corrections create new versions and receipts; they never update accepted
evidence in place. Quarantine is an immutable terminal evidence state, not only
a filesystem location.

A later additive schema should use provider-qualified surfaces such as:

```text
provider_ingest_runs
raw_payload_receipts
staged_market_events
quarantined_market_events
provider_reconciliation_runs
provider_reconciliation_results
provider_promotion_candidates
provider_promotion_receipts
```

These surfaces must not alter or reuse the current recovery supplements,
Binance reconciliation tables, or globally active reconciliation view.

Promotion remains a separate immutable decision that references:

- the exact staged rows and raw receipts;
- symbol and timestamp policy versions;
- completeness, duplicate, ordering, and gap evidence;
- comparison population and source identities;
- semantic compatibility, including quote-midpoint-bar status;
- a human-readable conclusion; and
- an explicit accepted, rejected, quarantined, or inconclusive state.

No staged row is visible through canonical research loaders or the current
dashboard by default. A future provider-selectable repository may expose
staging only through an explicit non-canonical query mode.

Gate 2 may create only an immutable promotion candidate. Canonical availability
requires a separate, explicitly approved, provider/venue-qualified append-only
promotion. It must not silently switch the current globally active
reconciliation. Canonical research loaders remain canonical-only by default.

## Required deterministic comparison

For matched instruments and half-open windows, the future comparison must
report:

- expected and observed intervals;
- coverage and missing intervals;
- duplicates and out-of-order observations;
- off-grid rows and timestamp-alignment differences;
- OHLC and volume deltas with units;
- zero-volume quote-midpoint bars;
- late-bar revisions;
- gateway, feed location, and venue provenance; and
- raw receipt and replay identities.

A comparison through two Alpaca feed locations is not described as an
independent Alpaca-versus-Kraken provider comparison unless one side is a direct
Kraken source with independent transport and receipts.

Reserve direct `source_provider="kraken"` for an independent Kraken transport
and receipt chain. An Alpaca gateway location advertising a Kraken-derived feed
remains an Alpaca-gateway observation with its advertised feed identity.

## Gate 2 acceptance criteria for a later phase

Before any promotion candidate can pass Gate 2:

- every parsed event traces to one immutable raw receipt;
- replay from those receipts is byte-identical and deterministic;
- REST pages and WebSocket batches are bounded before materialization;
- no duplicate staged key is accepted;
- every gap, off-grid row, out-of-order event, and late revision is classified;
- timestamp, symbol, precision, and quantity policies are frozen and versioned;
- quote-midpoint bars are separately classified;
- comparison thresholds are frozen before comparison evidence is read;
- every result preserves provider, gateway, feed location, source revision,
  venue claim source, and venue resolution status;
- the current canonical loaders and dashboard expose zero staging rows;
- the promotion candidate has an immutable, verified receipt; and
- no automatic canonical promotion occurs.

## Deferred execution boundary

Paper execution is not part of the approved implementation.

Before a future Alpaca paper adapter is considered, the repository must have a
provider-neutral boundary for:

```text
StrategyDecision
RiskDecision
OrderIntent
SubmissionAttempt
BrokerOrder
OrderStateTransition
TradeUpdate
KillSwitchState
```

The future Alpaca implementation must:

- allowlist only `paper-api.alpaca.markets`;
- reject the live trading host at startup;
- require both paper mode and a separate execution-enable flag;
- authenticate and read back the expected paper environment before enabling;
- use durable idempotency keys and retain Alpaca request IDs;
- default to network-denied and submission-disabled;
- redact credentials from logs, errors, receipts, and fixtures; and
- keep the kill switch independent of strategy code.

Paper fills remain operational evidence only. They do not prove profitability,
liquidity, slippage, capacity, or live deployability.

## Proposed file-level plan for a later approved phase

No files below are created by this decision. These paths are design candidates,
not authorization to scaffold packages before Gate 1 is reopened.

Reuse existing verified behaviors where their contracts remain valid:

- bounded batches and explicit source errors from `data/sources/base.py`;
- checksum-pinned cache and quarantine behavior from `data/sources/binance.py`;
- frozen run/work-unit identities from `data/reconciliation/manifests.py`;
- bounded ordered comparison from `data/reconciliation/compare.py`;
- advisory-lock and idempotent publication preflight from
  `data/reconciliation/repository.py`; and
- immutable receipt publication from `data/reconciliation/receipts.py`.

Reuse behavior, not Binance-specific names, classifications, schemas, or
provider assumptions.

### Provider staging and comparison

- `src/market_structure_lab/data/sources/events.py`
  - bounded raw-event protocol and immutable event envelope.
- `src/market_structure_lab/data/sources/alpaca.py`
  - fixture-first REST and WebSocket parsing behind injected transports.
- `src/market_structure_lab/data/provider_staging.py`
  - append-only staging/quarantine repository.
- `src/market_structure_lab/data/provider_comparison.py`
  - deterministic provider-qualified comparison.
- `src/market_structure_lab/data/migrations/0004_provider_staging.sql`
  - additive provider-qualified ingest, quarantine, comparison, and candidate
    receipts; no current canonical view change. The number is provisional and
    must be replaced by the next unused migration number at implementation
    time.
- `tests/test_provider_event_contract.py`
- `tests/test_alpaca_crypto_source.py`
- `tests/test_provider_staging.py`
- `tests/test_provider_comparison.py`

### Later application and execution phases

- Provider selection may be added to existing research/dashboard evidence
  surfaces only after verified repository queries exist.
- Phase 6 must first establish strategy, risk, fill, and ledger contracts.
- A separately approved Phase 7 may then add
  `src/market_structure_lab/execution/base.py`,
  `src/market_structure_lab/execution/alpaca_paper.py`,
  an additive execution-ledger migration, and fixture-based execution tests.

## Gate-specific verification requirements

### Gate 1: design readiness

- explicit approval of the owning project phase;
- approved raw receipt, event identity, timestamp, symbol, quantity, venue
  claim, staging, promotion-candidate, and rollback contracts;
- no reuse of current recovery/reconciliation keys or global views; and
- an independently reviewed file/migration/test plan.

### Gate 2: fixture-only data integrity

- REST pagination, response-size ceilings, rate limits, and retry exhaustion;
- inclusive API bounds filtered to internal half-open windows;
- batched WebSocket arrays, reconnects, slow-consumer failure, and gap
  detection;
- nanosecond timestamps, identical duplicates, out-of-order events, and
  off-grid bars;
- full and incremental order-book messages;
- late and revised minute bars;
- zero-volume quote-midpoint bars;
- ambiguous symbols, mapping-version changes, and precision changes;
- provider/location/venue-claim substitution and same-time multi-venue events;
- raw-receipt replay and payload mutation;
- staging isolation from canonical loaders and research;
- partial publication failure and deterministic retry; and
- immutable promotion-candidate receipts with no automatic promotion.

### Gate 3: separately approved paper execution

- missing credentials and complete secret redaction;
- live-host substitution and live-mode startup rejection;
- paper-account readback;
- duplicate order intents and ambiguous-timeout reconciliation;
- valid and invalid order-state transitions;
- default-disabled execution; and
- persistent kill-switch behavior.

All automated tests use fixtures. No credentialed call, order submission,
canonical mutation, or dump access is required before the separately approved
paper-execution verification.

## Rollback

This decision changes documentation only. Rollback is deletion of this file.
There is no schema rollback, data rollback, credential rotation, order
cancellation, or canonical-data repair because none of those actions occurred.

For a future staging release, rollback means disabling its feature flags and
query paths while retaining append-only ingest, quarantine, comparison, and
receipt evidence. Future rollback must not drop evidence or rewrite existing
canonical tables or views.

Any future promotion reversal is an append-only superseding or deactivation
receipt with an explicit pointer to the previous publication. It is never a row
deletion, evidence rewrite, or implicit "highest promotion ID wins" rule.

## Gate result

**Gate 1: STOP**

- Existing provider boundary preserves bounded batch provenance but not the
  required row-level gateway/feed/venue identity end to end.
- Existing canonical and application interfaces are not provider-selectable.
- No existing Kraken path is available for the requested comparison.
- No shared execution boundary exists.
- Stage 2 and Stage 3 would skip required project phases and invent
  architecture outside the approved boundary.

**Data promoted:** No.

**Canonical data modified:** No.

**Credentials read or stored:** No.

**Orders submitted:** No.

**Live trading enabled:** No.
