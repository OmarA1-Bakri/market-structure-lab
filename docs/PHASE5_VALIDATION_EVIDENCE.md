# Phase 5 validation evidence

## Scope and terminal conclusion

Phase 5 implemented and verified the deterministic validation transaction, but the first bounded
real development programme could not lawfully enter outcome evaluation. The repository has enough
compatible non-conflict coverage metadata for the source-universe gate, but it does not have the
required verified aggregate publications, event-level cost evidence, or independently bound
profile/source-price-precision evidence.

The preregistered programme therefore terminated as:

```text
execution_status = failed
scientific_decision = not_evaluated
reason_code = missing_prerequisites
final_holdout_access_count = 0
```

This is a truthful terminal preflight result. It is not an inconclusive or rejected backtest, a
validated behaviour, a promoted edge, or authorization to begin Phase 6.

## Frozen authority and execution order

The relevant remotely verified checkpoints are:

| Authority | Git SHA / identity |
|---|---|
| Task 14 close-out | `5db2705a1cba37ee10d5eb3bd1fa470a5b628e3d` |
| Task 14 evidence | `3482882c864f1471ec1dd682631544d0c404c542` |
| Task 15 plan | `807ac28ac5616fb837c1ccea1e2bc47572ae3984` |
| Task 15 evidence | `afce8e889aa645cd9e48e90631698b87866f287f` |
| Bounded implementation plan | `e655152ab729b3e1e530f1bc044febca3509a6fd` |
| Bounded implementation evidence | `6da307a0d4756c61ddcabdf01f00d828afb55d7e` |
| Checkpointed plan document SHA-256 | `d3520669352f0d85a27569edeefcfe84ff785e1f928ae0cc41edf91915c16111` |
| Task 10 hardened implementation source | `8b0cfc7e68ac3e7f14be0ca61122f23ec4a211f4` |
| Superseding exact Task 10 preregistration | `a21952b61b94e8a6823273a114b83f87f1dd54d5` |

The implementation source commit was pushed and live-remote verified before the preregistration was
created. The preregistration was then committed, pushed, and live-remote verified before execution.
At execution, local `HEAD` equalled the remote branch; all required lineage commits and the frozen
source checkpoint were ancestors of that remote SHA; tracked code was clean; no controlled
untracked file existed; and `src/`, `pyproject.toml`, and `uv.lock` were unchanged after the source
checkpoint.

The frozen files are:

- `configs/phase5/phase5-validation-programme-v1.json`;
- `configs/phase5/phase5-validation-source-preflight-v1.json`;
- `configs/phase5/phase5-validation-bindings-v1.json`.

Their principal identities are:

| Item | Identity |
|---|---|
| Programme | `VP-0d65fef04ca44dfc5ba7c7705e0be197480d7456b51178702a0011a18ab4487d` |
| Programme config SHA-256 | `f98bd334bd8e360d274ad4aad6df0f3336cf6acc9f5c94c7328cab68866f1561` |
| Exact 1,104-slot roster SHA-256 | `6fd1f69553048541716d5f28f405308fc9e9246cc305940d298a41608f237ac4` |
| Source-preflight manifest SHA-256 | `80730e2523be9c2bf8463716d806164889df2693007316e20127bccb3f19e5d7` |
| Binding bundle SHA-256 | `4b06188311325042f85b0d67ae6038159db5acb041492cf83dc4e4464c4e984e` |
| Lockfile SHA-256 | `a16b349b97b4c265129df59092f69981d9150a131772b8ed9f9ebda28dcaf44b` |

The config contains the complete ordered 1,104-entry roster and exact work budget. The adjacent
binding bundle freezes explicit unavailable-state declarations rather than inventing aggregate,
cost, split, profile, or price-precision evidence. The hardened CLI recomputes all five binding
domain hashes and the bundle hash, binds the source manifest to that bundle, binds the config
dataset identity to the source manifest, and rejects any tracked preregistration bytes that differ
from the live remote commit.

The earlier `edc99d5` / `7286651` preregistration produced a preserved `VP-1adc...` receipt tree.
Independent review then found that the CLI did not yet derive eligibility from the verified
reconciliation and compatibility artifacts, recompute the binding bundle, or prove exact
live-remote preregistration bytes. That tree is superseded audit evidence, not the authoritative
Task 10 result. The `VP-0d65...` programme documented here is the hardened replacement.

## Real source-universe metadata

Only the already promoted reconciliation receipt and the validated compatibility manifest were
read:

```text
data/exports/reconciliation/promotions/run_id=RR-000008/receipt.json
data/exports/manifests/recovery-callscore-20260714-validated.json
```

Its recorded facts are:

| Field | Value |
|---|---|
| Raw receipt SHA-256 | `cb399ac01d0beeea4666da071d9186e9a144f3f7fe5e4d1bf760696c631e8529` |
| Compatibility manifest raw SHA-256 | `482f3a09a4e24a62b0ad92f5bb90028eead67fe961eb1d3320dd38d13fd105d2` |
| Recorded content SHA-256 | `19315f0c52c6d3e9a91a5127921ce349fe50e60944c0ccc1a209b5637b3b9cf6` |
| Manifest SHA-256 | `1eaa2909e0f564490ebf65fe35027e588d2b46b135c52f1ed9d16f38fbbd2629` |
| Coverage logical SHA-256 | `e4b427b3589402809e519afe72f3ff47fee8912906b4fe606eb4201ad5334b3f` |
| Coverage intervals | `285` |

Thirteen compatible non-BTC/ETH symbols have at least 730 complete contiguous days:

```text
ADAUSDT, ALGOUSDT, BNBUSDT, DOGEUSDT, DOTUSDT, IMXUSDT, INJUSDT,
LINKUSDT, NEARUSDT, TAOUSDT, XLMUSDT, XRPUSDT, ZECUSDT
```

The latest shared verified-minute interval is
`[2024-04-11T12:00:00Z, 2026-07-16T11:23:00Z)`. It contains an available inward-rounded 825-day
calendar window. No split or fold was frozen or opened during this preflight.

The following source-conflict/incompatible symbols remained excluded:

```text
ARUSDT, AVAXUSDT, BTCUSDT, ETHUSDT, FETUSDT, PENDLEUSDT,
RENDERUSDT, SOLUSDT, SUIUSDT
```

`APTUSDT`, `FTMUSDT`, and `THETAUSDT` are compatible but have fewer than 730 contiguous days in the
promoted coverage metadata and did not enter the eligible set.

## Missing prerequisites

The preflight froze these exact blockers:

1. `aggregate_publications`: no verified real 1h/4h aggregate publication bundle exists for the
   eligible common grid;
2. `event_level_cost_evidence`: no frozen event-level fee, spread, slippage, funding, latency, fill,
   missed-fill, turnover, or capacity publication exists;
3. `profile_price_precision_evidence`: canonical snapshot publication still does not independently
   bind source price precision, so family B remains unavailable.

No source was substituted. In particular, BTCUSDT and ETHUSDT were not used to pad coverage, a
zero-cost policy was not supplied, a tick size was not inferred from validation data, and no caller
attestation was converted into a verified capability.

The immutable `programme-evidence.json` seals these exact blocker names in its preflight context,
together with the source-manifest identity and final-holdout access count zero.

Because primitive inputs were unavailable:

- no candidate outcome was attached;
- no event was selected or excluded by a fold;
- no purge or embargo interval was calculated;
- no control, bootstrap, confidence interval, p-value, MDE, cost, capacity, robustness, or exposure
  statistic was produced;
- A, B, G, E, and D all remained `not_evaluated`;
- no final temporal or asset-holdout row was opened.

## Immutable receipt evidence

The CLI returned its documented safe terminal exit code `2` and published one complete immutable
ledger under the ignored local runtime root:

```text
data/exports/validation-programmes/
  programme_id=VP-0d65fef04ca44dfc5ba7c7705e0be197480d7456b51178702a0011a18ab4487d/
```

Verified receipt evidence:

| Field | Value |
|---|---|
| Programme receipts | `1` |
| Evaluation receipts | `1,104` |
| Ledger entries | `1,104` |
| Unique terminal pair | `failed / not_evaluated` |
| Programme receipt file SHA-256 | `32525932c42bc618b84256df19445ead5e36d7fc67312a836887a68c713ca915` |
| Programme receipt canonical identity | `2b1629261a8b2559daff4fe6fc6a9c31f48ae6432fdc114f3ecb5020ab3d8ed7` |
| Complete tree file count | `3,315` |
| Complete tree byte count | `2,708,226` |
| Complete tree SHA-256 | `5707cfd5aa1dba2e7900d45e3f2723c6edf10e94cbd9423868c0fcfc0293ded8` |
| Final-holdout access count | `0` |

The programme evidence binds the exact source-preflight manifest and records
`missing_prerequisites`. Every roster slot has exactly one attempt-001 VR receipt. The output root
did not exist before publication and was committed by one staging-root rename; no partial final
tree was exposed.

## Deterministic replay

The exact hardened preregistration was run a second time into
`/tmp/msl-phase5-validation-replay-v2`. Both runs returned exit code `2`. A path-ordered comparison
of every relative file name and every byte produced:

```text
real tree SHA-256   = 5707cfd5aa1dba2e7900d45e3f2723c6edf10e94cbd9423868c0fcfc0293ded8
replay tree SHA-256 = 5707cfd5aa1dba2e7900d45e3f2723c6edf10e94cbd9423868c0fcfc0293ded8
result              = byte-identical
```

The public receipt verifiers accepted both programme receipts and all 1,104 replay evaluation
receipts against the exact frozen config. Every evaluation and every programme-ledger entry had
the unique terminal pair `failed/not_evaluated`. The runtime receipt trees remain ignored and are
not committed.

## Ready-path limitation

The hardened CLI is the canonical verified terminal-preflight route for current real artifacts.
The public `run_validation_programme` function is the typed ready-path implementation boundary,
The original Task 10 close-out did not contain a canonical loader that could assemble the complete
real typed primitive-source bundle, so that immutable programme correctly stopped before outcome
evaluation. No readiness was fabricated and its receipts remain unchanged.

Subsequent V2 enabling work now includes an explicit-path source-bundle loader, bounded VS-0001
event/outcome population, publication-rooted base/delay candidate inputs, and a factory-issued
publication-rooted unconditional/persistence control-input authority. Donor clocks are selected
without using outcome prices, candidate and donor horizon overlaps are excluded, retained parents
replay exactly, and shortages remain explicit. The bounded real synthetic slice has an admissible
unconditional donor but no admissible persistence donor under the frozen different-clock rule.
Together with still-incomplete promotion-grade numeric cost evidence, that shortage keeps exact
Gate D inference unavailable. These V2 additions do not rewrite the Task 10 receipt tree, access the
final holdout, validate a behaviour, promote an edge, or authorize Phase 6.

## Verification and phase gate

Targeted Task 10 plus adjacent Phase 5 model, receipt, and lifecycle verification passed `183`
tests after hardening. The final Task 11 verification results were:

| Check | Result |
|---|---|
| `uv sync --locked` | passed; 120 packages resolved and 117 checked |
| `uv run ruff format --check .` | passed; 183 files already formatted |
| `uv run ruff check .` | passed |
| `uv run mypy src` | passed; 98 source files |
| `uv run pytest -q` | 1,748 passed, 8 skipped, 13 host-policy failures |
| `uv build` | passed from an exact `git archive HEAD` Linux-filesystem mirror |
| `docker compose config --quiet` | passed with validation-only password placeholders |
| `git diff --check` | passed |
| dashboard `npm ci` | passed; 505 packages installed; 4 existing audit findings |
| dashboard evidence verification | passed; 25 symbols and 26 features |
| dashboard lint / typecheck / build | passed / passed / passed |
| dashboard tests | not configured in `package.json` |
| independent code-quality review | approved; no critical, high, or medium issue remains |
| independent quantitative/provenance review | approved |

The 13 Python-suite failures are all in
`tests/test_daily_candle_refresh_runner.py`: the Windows PowerShell host rejects unsigned temporary
test scripts reached through `\\wsl.localhost`, before the runner code executes. The remaining
1,748 tests still ran and passed. Native Windows execution was not available from this session, so
that already documented host-specific gap remains explicit.

The first in-place `uv build` attempt was stopped after more than 15 minutes blocked in WSL
`p9_client_rpc` with no I/O progress. The exact tracked `HEAD` was then exported with `git archive`
to a Linux-filesystem temporary directory, where `uv build` produced both the sdist and wheel
successfully. No generated build artifact is part of this close-out commit.

Dashboard `npm ci` reported two moderate and two high existing audit findings. This Phase 5
Python/documentation close-out changed no dashboard dependency file and did not run an automatic
dependency rewrite.

Phase 5 cannot claim candidate support, validation, or promotion. The correct scientific conclusion
is `not_evaluated`. Phase 6 remains scientifically closed because no validated edge exists.

## 2026-08-02 terminal decision publication

The hardened receipt tree was reopened from its original bytes after the subsequent V2 enabling
work. Every VP/VR receipt was revalidated against the exact frozen config and canonical 1,104-slot
roster. The terminal publisher then issued a separate immutable decision under:

```text
data/exports/phase5-terminal/terminal_state=no-validated-edge-v3/
```

The new publication supersedes the earlier absence of an explicit empty-batch marker; it does not
rewrite the historical VP/VR tree. Its authenticated facts are:

| Field | Value |
|---|---|
| Closure identity | `22dd64540283402275324dfd6c8849fb89d4500b7ed2b8a9de06f78c70852f0b` |
| Publication file SHA-256 | `e3d322d0d3ccbbea3f85babb558e2db0592c22159f121fd747e7abe8932bd62b` |
| Original programme-tree identity | `85063429a5e644922d33041b314d43b3936624874fbc4fc0f50933c05b107f1d` |
| Parent programme receipt | `2b1629261a8b2559daff4fe6fc6a9c31f48ae6432fdc114f3ecb5020ab3d8ed7` |
| Terminal implementation identity | `4fe428169d706b85f02ad891fe40c663e7e864478e43494adf60fcd3dd5941df` |
| Empty eligible-batch identity | `25eb650e11480bedf66bf64c74a8f3542aae8a552f53fc3a52e36abef1ce904f` |
| Planned / terminal slots | `1,104 / 1,104` |
| Primary Holm entries | `64`; every unevaluable primary has effective `p=1` |
| Eligible development candidates | `0` |
| Final marker | `final_holdout_not_opened_empty_batch` |
| Final access attempts / rows / records | `0 / 0 / 0` |
| Phase 6 / Phase 7 | `scientifically_gated_no_validated_edge` |

The exact authenticated unavailable parents remain `aggregate_publications`,
`event_level_cost_evidence`, and `profile_price_precision_evidence`. Current source archives do not
contain truthful historical paid fees, bid/ask spread, execution slippage, fill probability,
latency, missed fills, or capacity. No zero-cost substitution, OHLCV microstructure inference, or
current-only metadata was used. This is a truthful Terminal A result: no candidate was evaluated,
validated, or promoted, and the final holdout was not opened.

The V2 legal-control path was also tightened without changing the scientific conclusion. Donor
horizons are now disjoint across unconditional and persistence roles, all members of a control
stratum must bind the same aggregate-series identity, and candidate-side direction diagnostics are
not misreported as donor shortages. Exact persistence-donor shortage remains terminal evidence only
when the complete bounded donor search finds no legal different-clock, non-overlapping donor.

Earlier same-session closures at `terminal_state=no-validated-edge` and
`terminal_state=no-validated-edge-v2` are preserved as superseded audit evidence. Independent
security review found that the first closure's generic V2 disk-reader path could accept
self-hashed fixture provenance and that its parent generation was not rechecked after commit. The
V2 path was removed. The authoritative `-v3` closure accepts only the exact frozen legacy programme,
config bytes, and parent receipt; verifies every slot-evidence artifact; proves a stable tree before,
during, and after publication; binds the exact implementation-source bytes; and uses the repository's
write-through, no-replace publication primitive on native Windows without invoking POSIX directory
fsync.
