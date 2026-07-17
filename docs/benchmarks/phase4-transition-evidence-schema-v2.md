# Phase 4 transition-evidence schema v2 migration

## Decision

Phase 4 transition evidence is versioned as `boundary-aware-dwell-transitions-v2`. Discovery run
manifests use `discovery-run-manifest-v2`, interpretation manifests use
`interpretation-manifest-v2`, and frozen interpretation inputs use
`phase4-interpretation-input-v2`. Existing fixture filenames remain stable for downstream path
compatibility, while their embedded schema identities advance to v2.

The v1 manifest and evidence-pack path serialized only transition rows. That discarded raw-row,
dwell-run, contiguous-sequence, and boundary-break evidence even though `transitions.json` contained
it. V2 carries the complete `ClusterTransitionMatrix` through run publication, replay, evidence
packs, and interpretation publication. Supplied interpretation evidence must exactly match the
verified run matrix.

## Reviewed fixture migration

The synthetic fixture was regenerated through `run_discovery` and
`publish_ai_interpretations`; no market experiment, holdout access, or data mutation occurred.

| Identity | Before | V2 |
|---|---|---|
| Stable run manifest | `925764c238b16df0a5435320adb9f8f3ac59434758aacf8025636a42e50bfaf3` | `c06ddc20b8e606cb62aefa50a68a2624594abef06f8f760c6adc9dabc2dbdbc2` |
| Rejected run manifest | `e16a7985f5d015b9931935dd4f171fa365458639df209e4cf92555272dbe7950` | `0a06164ce7dbe603c4c11fc9ee62812bf1bdb044ec16a78a6a0dcb24671268d7` |
| `transitions.json` | `507d9fb64ce4b6a615647e3bb14e1ba1c185285fed77199e709bb39784ff71bc` | `4586a21d8ff6e594531ab6a7c6275f4ea7a5205990fdf0e7c4d48048e54a3661` |
| Interpretation input | `e31c32765d67f84f7a411d9309f636716a2d071a6dc10f7e54f8cf2c32064ac5` | `3eac8e08aafce10c987298f1365d6570865329184e229477b95fd58656929873` |
| Evidence artifact | `4ddd6d84860047caf1f47058f38170cb9d082995ea9d62fd6de467864b2cdb1c` | `6d1f6ee93ad15dc45c69c0a8fd5ba2d5af7b44efe125adbdc8dc8cb5d488e110` |
| Interpretation artifact | `a1a78434a82dac56acca707be4123b6dd169b97347b135a6a1afb7bee54d4129` | `a6a759ef2f21d3507a273b11120e5ab01bb57c5907946e5a4aede6a031f961ef` |
| Interpretation manifest | `58d33fc7818b01edfd459daaf188f4fa7d079279cb7d9bc8ec481ea1ce77ffc2` | `c2f1249ed0a3558f07e33ca11ebdb007c97a98975d682ca8a2bd576e8add6a60` |

`transitions.json` changed only by the explicit transition algorithm identity, serialized effective
support, and boundary/compression evidence. Run manifests changed schema and replaced row-only
copies with the complete matrix. Interpretation evidence changed schema and now carries that same
matrix. `interpretations.json` changed only because its recorded prompt SHA-256 now identifies the
v2 input.

The PCA projection remains byte-identical with algorithm identity `deterministic-pca-v2`; clustering,
behaviour IDs, behaviour artifacts, stability, motifs, metrics, and run identity inputs are
unchanged. The stored model response is also byte-identical: its SHA-256 remains
`b13972cac7c31f6f48118dc22fd384bacff81935ce6770a75ce6ad2e344f6c04`.
The dashboard's source-backed `software_replay` snapshot was refreshed to the v2 fixture and now
publishes the transition algorithm identity alongside the linked fixture/input/response hashes.

## Rejected alternative

Updating only the expected hashes was rejected. Without the schema and algorithm version bump,
downstream consumers could not distinguish a reviewed evidence-contract migration from accidental
golden drift, and interpretation packs could still omit the boundary proof required to interpret
effective support.
