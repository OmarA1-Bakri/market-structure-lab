# Outcome-Blind Discovery MVP

Phase 4 freezes recurring market-structure behaviours before any future outcome is attached. It is
an inspectable discovery baseline, not an edge validator, strategy engine, or trading system.

The original Task 12 software gate is remotely complete at baseline
`3cf3721f1ec694b61ec1da78d0b7be24fea93caf`. The separate Task 13 residual-risk audit is complete
and remotely checkpointed at `59a9814a8e2a609bf1ea0bd5d0320ed0f7b33ee4`. The first real
outcome-blind run is Task 14; it is now authorized, while holdout rows, outcomes, validation, and
strategy work remain inaccessible and unstarted.

## Pipeline and access boundary

The implemented path is:

```text
Frozen Phase 3 FeatureRow records
  -> verified snapshot/publication/registry/normalizer provenance
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

Before matrix construction, factory-created `DiscoveryProvenance` authenticates the concrete snapshot
manifest, derived feature publication, registry/dependency contract, fitted normalizer, selected
feature order, split, clean code commit, and lockfile. Changed artefacts, rows, feature order,
partitions, commit state, or hashes fail before PCA or clustering reads discovery values. This is a
verified software boundary; no real source-backed discovery publication has yet exercised it.

Before feature rows can be published for discovery, each registered field must carry the causal
dependency contract and independent checksum-bound leakage evidence documented in
`docs/benchmarks/feature-leakage-audit-contract-v1.md`. The receipt binds actual serialized output
to the reviewed contract through a bounded producer envelope emitted by `FeatureBuilder`; plain row
iterables are not publishable. The receipt explicitly retains residual manual review because
metadata and output binding are not proof that arbitrary builder code is leakage-free.

## Algorithms and fixed caps

- `build_feature_matrix` admits only registered numeric discovery-safe features and fails when its
  explicit row cap is exceeded. It never fills a missing value.
- PCA is an inspectable deterministic projection. Component signs and ordering are canonical.
- K-means uses explicit seeds, canonical centroid/label ordering, bounded iterations, and a frozen
  tolerance. Empty clusters and invalid dimensions fail closed.
- Cluster stability records seeded reruns, deterministic unstratified subsamples, nearby
  cluster-count perturbations, asset coverage, and adjacent development-period frequency,
  centroid, within-cluster scale, assignment-margin, and per-cluster event-support evidence.
  Policies are frozen before evaluation. An unstable definition is retained as
  `rejected_unstable` and produces no frozen behaviours.
- Motif search consumes bounded multivariate contiguous sequences. It splits or rejects removed/null
  rows, timestamp gaps, session/symbol/timeframe/segment changes, duplicate or out-of-order rows,
  and non-contiguous information cutoffs. Separate motif evidence covers seed/tie rules,
  deterministic subsamples, nearby windows/exclusion zones/distances, assets, adjacent periods, and
  frozen outcome-blind regimes. Rejected motifs remain recorded and cannot support recurrence.
- Transition evidence uses event/cluster observations with dwell compression and refuses symbol,
  timeframe, segment, material-gap, session, or non-contiguous crossings. Horizon, bootstrap
  iterations/seed, confidence, block rule/value, effective support, interval-width action, and
  nearby block sensitivity are validated frozen run inputs.

These values are pinned in each `config.json`; changing one changes the run identity. Golden
fixture values demonstrate software pass/reject plumbing only and are not research thresholds.

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
behaviour IDs, the complete versioned transition matrix, and every artifact SHA-256. Repeating
identical content returns the verified existing run without rewriting it. Stale stages, changed
identity/content, missing files, extra caller identities, or checksum disagreement fail closed.

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
`404da0b473a6b3d065c9ce009b37cb89d91e1394c28382b64854901e1295d27e`. The unstable
`DR-000602` fixture is deterministically retained with no behaviours and manifest SHA-256
`4ca6d4b6b3ca8e89b56c853f1b4235338ee5fa92d9b536e9cb3e07057ed371e9`.

The parent-model interpretation prompt input is frozen at
`tests/fixtures/phase4/discovery_interpretation_input_v1.json`, SHA-256
`9f6b8bb56143b1ce6298d5e409fff41d50bbd1bb10cd5901bf0fbcf455d3aae6`. It is derived through the
real evidence-pack API.

The session model's exact response and supplied provenance are stored separately at
`tests/fixtures/phase4/discovery_interpretation_response_v1.json`, SHA-256
`ca49bc5572d22042723891f3e1047e6812f8e4595f6ce9e04fae793e32eb4577`. The test constructs real
`AIInterpretation` records from those bytes and publishes them through
`publish_ai_interpretations`. The interpretation manifest SHA-256 is
`57959a105814d8e090f5353cb571c292cf3c8a4dc69806ba20a8a8381439937f`. These are candidate
interpretations and proposed Phase 5 tests, not validation results.

## Interpretation of transitions

Transition rows estimate observed conditional frequencies such as
`P(next cluster | current cluster)` after boundary checks and dwell compression. They are
Markov-like summaries only. Phase 4 does not establish the Markov property, stationarity, causal
mechanisms, or independent samples. Support counts and block-bootstrap intervals must accompany
every probability. Low-support, wide-interval, or block-sensitive estimates are rejected or marked
descriptive-only by the frozen policy; no transition estimate is a significance, edge, or promotion
claim. The serialized algorithm identity is `boundary-aware-dwell-transitions-v3`; discovery
manifests and AI evidence packs retain the full uncertainty policy, dependence diagnostics, nearby
block-length sensitivity, and complete matrix so the evidence contract survives replay.

Before any estimate Cartesian product or bootstrap call, the estimator computes conservative draw
work as `transitions * bootstrap_iterations * (1 + actual sensitivity block count)` using checked
integer bounds. Work above 10,000,000 transition draws is rejected. This CPU guard is independent
of the existing 100,000 serialized-estimate cap and 1,000,000 stored bootstrap-probability cap.

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

## Phase 4 hardening stop gate

The original Task 7–12 software-hardening gate and the authorized Task 13 adversarial closure are
recorded in [`PHASE4_HARDENING_EVIDENCE.md`](PHASE4_HARDENING_EVIDENCE.md). Together they verify and
independently challenge deterministic provenance, structural stability, motif boundaries, frozen
transition uncertainty, semantic leakage receipts, bounded work, holdout non-access, trial
accounting, and byte-identical golden publication. The result remains synthetic software evidence
only. Task 14 real discovery is authorized only after the final Task 13 evidence checkpoint is
pushed and verified remotely. Outcome attachment, predictive validation, and strategy work remain
blocked behind their later gates.
