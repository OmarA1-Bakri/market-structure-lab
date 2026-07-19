# Phase 4 transition-evidence schema v3 migration

## Decision

Phase 4 transition evidence is versioned as `boundary-aware-dwell-transitions-v3`. Discovery run
manifests remain `discovery-run-manifest-v2`, interpretation manifests remain
`interpretation-manifest-v2`, and frozen interpretation inputs remain
`phase4-interpretation-input-v2`; those envelopes did not change. Existing fixture filenames remain
stable for downstream path compatibility.

V3 removes the public compatibility path that could synthesize an uncertainty policy or dependence
diagnostics. `estimate_cluster_transitions` now requires one complete `TransitionUncertaintyPolicy`,
and the matrix requires the exact policy and measured diagnostics. The frozen configuration,
identity, transition artifact, manifest, and interpretation evidence all retain that contract.
Superseded v2 transition matrices are rejected rather than silently upgraded.

## Reviewed fixture migration

The synthetic fixture was regenerated through the same frozen policy contract used by production
run orchestration. No market experiment, holdout access, database access, or data mutation occurred.

| Identity | V2 | V3 |
|---|---|---|
| Stable run manifest | `6555a50e8d508d3221db02058b6161fd2da796eefaae6b9e3f0ff5993f521141` | `a0d5293393309cb4071fef710e1a0b79bb7274e0fc61216f628ec18225ad6481` |
| Rejected run manifest | `fc801c43f859712aef567fceef744711ae07e757dacddbf834a5dd5022218ac6` | `03b0fc8daa7738814000f3564da48417646992a5bc2b192009aebec637f882dc` |
| `transitions.json` | `1968115eb5022968bfa32f81512bb036840f68c5ec9671f4a55566358dd47eea` | `0331b791fd66a1cbbdc9252d15d05ee7dd79632d5dfcbacbe4fe223c025d44e9` |
| Interpretation input | `ade2ce543b2c65cd4787f1ea68a9c18a836932f6d4b67c7ae09fc394c2113e6c` | `daf4b8451cd6777db0f99d1afbe4283a62188b8f21b6a2bd8fff5932a7ad91f4` |
| Evidence artifact | `74785e8ba5fe65cebd1eb53bdf877e1c4d3ba15aac949b4bfaa1733d8d016d01` | `f34ee6acd0c8315ecc82dffed9e8c545238dacdbcc8f755bf675fcd77bbed53c` |
| Interpretation artifact | `fcd64266d9de112fe8faea43c8ea642d1761a2f336a78f75de939cbfe0e6c312` | `17d604c52fb0c903a4b5c2f444bd0a5c0f00a7fb437777fcd5ac346b460743f7` |
| Interpretation manifest | `60908ce4258bb1f46ac534351e9dee43f16a838487ef026d38753e3c7689c611` | `4bba9523095c11d29188edab1b4b34b91b89ab3c6dcd6255a66e1ff970914550` |

The root-cause transition diff was limited to the v3 algorithm identity and replacement of the old
compatibility-generated fixture policy identity with the explicit `synthetic_golden` policy. The
configured numerical policy, dependence diagnostics, counts, probabilities, intervals, evidence
statuses, and sensitivity values remained unchanged. Config, metrics, summary, manifest, evidence,
and publication hashes changed as deterministic consequences of serializing the policy in config,
reporting conditional-recurrence statuses, and linking the v3 matrix.

Two independent clean runs of
`test_phase4_golden_stable_and_rejected_runs_replay_byte_identically` passed. Each run performs two
stable and two rejected replays and asserts byte-identical bundles. The PCA projection, clustering,
behaviour IDs, stability, motifs, and stored model response remained unchanged.

## Interpretation boundary

V3 outputs are conditional recurrence estimates only. Low-support estimates are rejected;
wide-interval estimates follow the frozen report-or-reject policy; block-length-sensitive estimates
are rejected; and unavailable sensitivity or zero destination counts remain visibly
descriptive-only. None of these statuses is statistical significance, a validated edge, or a
promotion decision.

The estimator also rejects more than 10,000,000 total transition draws across the primary and
actual sensitivity bootstraps before materializing estimate pairs or calling the bootstrap. This
CPU-work cap is checked separately from serialized-output and stored-probability bounds.
