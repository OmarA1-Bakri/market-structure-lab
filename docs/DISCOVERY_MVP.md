# Outcome-Blind Discovery MVP

Phase 4 freezes recurring market-structure behaviours before any future outcome is attached. It is
an inspectable discovery baseline, not an edge validator, strategy engine, or trading system.

## Pipeline and access boundary

The implemented path is:

```text
Frozen Phase 3 FeatureRow records
  -> chronological discovery/development guards
  -> bounded numeric feature matrix
  -> inspectable PCA
  -> deterministic seeded K-means
  -> multi-axis stability decision
  -> bounded motifs and boundary-aware transitions
  -> frozen BehaviourEvidencePack records
  -> optional provenance-complete AI interpretation
```

Discovery, development, and final-holdout metadata are frozen together. Only discovery rows may fit
the PCA and clusters; development rows are used only for stability evidence. The discovery input
factory rejects holdout partitions before iterating their rows. `run_discovery` receives no holdout
row argument, and interpretation evidence contains no holdout content.

Dataset, configuration, profile, window-policy, feature-set, and registry identities must remain
constant within and across discovery/development inputs. The final holdout is reserved for Phase 5
and cannot be used for feature selection, cluster selection, threshold tuning, interpretation,
parameter changes, or strategy construction.

## Algorithms and fixed caps

- `build_feature_matrix` admits only registered numeric discovery-safe features and fails when its
  explicit row cap is exceeded. It never fills a missing value.
- PCA is an inspectable deterministic projection. Component signs and ordering are canonical.
- K-means uses explicit seeds, canonical centroid/label ordering, bounded iterations, and a frozen
  tolerance. Empty clusters and invalid dimensions fail closed.
- Stability records seeded reruns, deterministic 75% subsamples, adjacent development-period
  Jensen-Shannon distance, per-cluster asset coverage, and nearby cluster-count perturbations.
  Policy thresholds are frozen before evaluation. An unstable definition is retained as
  `rejected_unstable` and produces no frozen behaviours.
- Motif search is bounded per symbol/timeframe/segment. The Task 5 orchestration uses a maximum
  four-observation window, exclusion zone two, and top-three results. It searches the first selected
  discovery feature and does not cross a hard boundary.
- Transition evidence uses event/cluster observations with dwell compression and refuses symbol,
  timeframe, segment, material-gap, session, or non-contiguous crossings. The run baseline uses
  horizon one, 100 block-bootstrap iterations, block length two, and 95% intervals.

These values are pinned in each `config.json`; changing one changes the run identity.

## Frozen behaviour and AI evidence

A stable behaviour pins its complete cluster definition, representative event IDs, feature
centroid and distributions, stability evidence, frequency, median duration, asset/regime coverage,
neutral description, and `frozen` status. Its `B-*` identifier is derived from that full payload.

AI receives only `BehaviourEvidencePack` records: frozen behaviour summaries, neighbouring
behaviour IDs, and transition summaries. It does not receive unrestricted source rows, outcomes, or
holdout data. An `AIInterpretation` must:

- use the closed observable-language neutral-name grammar;
- label the proposed mechanism explicitly as an inference;
- state a falsifiable hypothesis and spuriousness alternatives;
- preserve the frozen detector fields;
- propose a horizon and metrics without claiming they passed;
- record provider, model, prompt hash, response hash, temperature, and UTC generation time.

AI is interpretive, not authoritative. It cannot validate an edge, claim profitability, identify
participants from OHLCV, change the detector, or override promotion criteria.

## Run artifacts

`run_discovery` publishes a `DR-*` directory atomically through a sibling staging directory:

```text
config.json
projection.json
clustering.json
stability.json
behaviours.json
motifs.json
transitions.json
metrics.json
summary.md
manifest.json
```

JSON is canonical and newline-terminated. `manifest.json` pins run/config/input identity, status,
behaviour IDs, transition rows, and every artifact SHA-256. Repeating identical content returns the
verified existing run without rewriting it. Stale stages, changed identity/content, missing files,
extra caller identities, or checksum disagreement fail closed.

Interpretations publish separately under `DR-*/interpretations/` with evidence, interpretation, and
manifest hashes. The supplied run object must be byte-identical to the verified run manifest, and
its full frozen-behaviour payload must match `behaviours.json`.

Generated `DR-*` directories are run artifacts and are not committed.

## Golden replay

The committed multi-symbol fixture is
`tests/fixtures/phase4/discovery_run_v1.json`. It contains 16 discovery rows, eight development
rows, separately frozen holdout metadata, explicit caps/seeds, and no outcome fields or holdout
rows.

```bash
uv run pytest -q tests/test_phase4_golden.py
```

The stable `DR-000601` fixture freezes two behaviours and has manifest SHA-256
`86d754f5be1c3d0b5e2a9d403a7af9c814231f7679421a560f14dc1bbc65b8f2`. The unstable
`DR-000602` fixture is deterministically retained with no behaviours and manifest SHA-256
`cbadd46a15144ce89424b40c0d84687ce99764725b323d4f2f9dbb3b66f9e500`.

The parent-model interpretation prompt input is frozen at
`tests/fixtures/phase4/discovery_interpretation_input_v1.json`, SHA-256
`dd306e3eea72684c28d3855e924785c805d1c7b0e8d8961736eab9c5fca526e5`. It is derived through the
real evidence-pack API.

The session model's exact response and supplied provenance are stored separately at
`tests/fixtures/phase4/discovery_interpretation_response_v1.json`, SHA-256
`d3a180687ff3e630b559f17c2433359bc86d76e301c585e84da6a0a77453ab06`. The test constructs real
`AIInterpretation` records from those bytes and publishes them through
`publish_ai_interpretations`. The interpretation manifest SHA-256 is
`b0370ffee5a3d64ae57fd3e4af798ec98fe00daee70dcb07a2ed08d1b545c53c`. These are candidate
interpretations and proposed Phase 5 tests, not validation results.

## Interpretation of transitions

Transition rows estimate observed conditional frequencies such as
`P(next cluster | current cluster)` after boundary checks and dwell compression. They are
Markov-like summaries only. Phase 4 does not establish the Markov property, stationarity, causal
mechanisms, or independent samples. Support counts and block-bootstrap intervals must accompany
every probability, and low-support rows are descriptive evidence rather than significant signals.

## Approximation limits and Phase 5 boundary

The input features ultimately inherit the OHLCV representation limits documented in
`market_structure.md`: volume-at-price is approximate, and OHLCV does not reveal aggressor side,
order-book depth, queue position, participant identity, open interest, funding, or liquidations.
The golden fixture proves deterministic software replay; it is not empirical evidence that either
frozen fixture behaviour exists or is profitable in live cryptocurrency data.

Phase 5 may attach predefined outcomes only after the candidate detector, split policy, horizons,
metrics, and promotion criteria are frozen. It must use purging/embargo where labels overlap,
chronological walk-forward evaluation, asset holdouts where feasible, serial-dependence-aware
uncertainty, multiple-testing controls, negative controls, and an untouched final holdout.
Behaviour, interpretation, and hypothesis remain distinct from a validated edge.
