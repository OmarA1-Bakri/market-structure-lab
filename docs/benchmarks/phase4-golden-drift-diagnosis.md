# Phase 4 golden replay drift diagnosis

## Scope

This diagnosis covers only the Phase 4 golden discovery replay on commit
`371b6792c9e081a610f7156adf0f7a49c88102c4`. It does not inspect holdout outcomes, change
discovery semantics, or start a research run.

## Reproduction

The same checkout and frozen inputs produced different results in the repository's Linux and
Windows Python environments:

```bash
.venv-ralph/bin/python -m pytest tests/test_phase4_golden.py -vv
.venv-codex/Scripts/python.exe -m pytest tests/test_phase4_golden.py -vv
```

Before the repair, Linux failed all three tests while Windows passed all three. The first changed
numeric value was PCA component `/components/0/1`:

| Environment | Value |
| --- | ---: |
| Linux / SciPy SVD | `0.09950371902099896` |
| Windows / SciPy SVD | `0.09950371902099893` |

No assignments, feature centroids, distributions, stability metrics, motifs, transitions, or run
status changed. On Linux the last-bit difference changed `projection.json` from the frozen
`e9785313168caeff659231ada06c3ef7d0c732cd3bcc13cc03cd4e0b3f891b43` identity to
`8e568a92533f0aaf1f206d419935141eb0c535c95df8cbe625b9a837c04596e4`. Exact serialization then
propagated through clustering and the documented behaviour-definition payload, producing behaviour
IDs `B-4C8331FC502E0C59` and `B-FD3F930F93E8CD8E` instead of the Windows fixture IDs
`B-AFFBC6438DE4E321` and `B-CC5D0EE76718D3E7`. The manifest and interpretation provenance hashes
therefore also changed.

Git history did not identify a behaviour-changing commit between the two results: both came from
the same source checkout. Commit `41bbb8c` introduced the golden completion replay, but the failure
was numerical backend nondeterminism exposed by exact float hashing, not a later algorithm or schema
change.

## Root cause

`scipy.linalg.svd(..., lapack_driver="gesvd")` gives mathematically equivalent results across the
two LAPACK builds, but IEEE-754 last bits are not a cross-backend identity boundary. `fit_pca`
canonicalized component signs only and retained every binary64 digit. `pca_projection_sha256` and
`_cluster_definition_sha256` correctly hashed their exact payloads, so the unstable SVD digits were
incorrectly promoted into durable artifact and behaviour identity.

The semantic checks were not wrong: supplied PCA evidence still must match the deterministic fit,
scores still must match the stored means and components, and stale interpretation IDs are still
rejected. The missing invariant was a platform-neutral numeric representation before PCA evidence
entered identity-bearing artifacts.

## Precision policy and repair

The repaired contract is explicitly versioned as **`deterministic-pca-v2`**. All identity-bearing
PCA floats are canonicalized at the `fit_pca` output boundary to **14 significant decimal digits**,
with signed zero normalized to `0.0`. This covers means, oriented components, explained-variance
ratios, and scores. Fourteen digits is the highest tested precision that maps the observed Linux and
Windows component values to the same decimal value; 15 significant digits preserves their
difference. Significant digits are used instead of fixed decimal places so the policy is
scale-aware.

Component sign orientation uses the canonical values, not raw backend values. Before choosing the
largest-absolute-loading pivot, loadings at or below `64 * binary64_epsilon * max_abs_loading` are
set to zero and every remaining loading is reduced to the 14-significant-digit representation. The
oriented component is canonicalized again. This scale-relative threshold prevents meaningless
near-zero noise and ULP-sized pivot ties from changing the component sign while retaining loadings
that are numerically distinguishable at the frozen precision.

`deterministic-pca-v2` also fails closed when the selected basis is not uniquely identifiable.
Adjacent selected singular values, plus the selected/unselected boundary when present, must have a
relative gap greater than `1e-12` using the larger absolute singular value as scale. Exact repeated
and near-repeated subspaces are rejected rather than allowing a LAPACK backend to choose an
arbitrary orthonormal basis. Repeated singular values wholly below the unselected boundary do not
affect the published projection and are therefore outside this check.

The selected component count must also be no greater than the numerical rank at that same relative
tolerance. This accounts for zero singular dimensions omitted by economy SVD on wide matrices and
rejects arbitrary selected null-space vectors even when the preceding nonzero singular value is
well separated.

SVD still runs on the unrounded centered input. Scores are recomputed from the canonical stored
means and components, preserving the public projection definition. Validation was not weakened:
non-finite and dimension checks remain, supplied scores remain checked against the frozen
projection, and the complete `PCAProjection` must still equal a fresh canonical deterministic fit.
The version is serialized in `projection.json`, included in `pca_projection_sha256` and behaviour
cluster identity, and must exactly equal `deterministic-pca-v2`; legacy or substituted versions are
rejected before hashing or clustering.

The regression test replaces the SVD result with a copy whose affected loading is moved by two
representable last-bit steps. It proves that both the full `PCAProjection` artifact and
`pca_projection_sha256` identity remain equal. Additional regressions cover opposite-sign pivot
ties, scale-relative near-zero loading noise, singular-value/variance ULP changes, and exact or
near-repeated selected and boundary subspaces.

## Fixture migration

The golden files were regenerated only after the regression was red and the canonical boundary made
it green:

- `discovery_run_v1.json` now records the canonical projection, clustering, behaviour, manifest,
  and interpretation-publication identities.
- `discovery_interpretation_input_v1.json` was regenerated from the repaired run's exact evidence.
  Versioned ID sorting places the high-state evidence before the low-state evidence, and canonical
  score ties swap the first two representative event IDs in each behaviour. The representative
  sets and all feature/distribution/stability/transition evidence are unchanged.
- `discovery_interpretation_response_v1.json` retains every interpretation field verbatim except
  the two behaviour IDs required to reference the repaired frozen behaviours. The low-state text
  maps to `B-D6E06239FB9EC1EF`; the high-state text maps to `B-CB3E7F47FE808F3C`.

The versioned canonical behaviour IDs are now `B-D6E06239FB9EC1EF` (low state) and
`B-CB3E7F47FE808F3C` (high state) on both platforms.

## Rejected alternative

A blind update to the Linux-generated hashes was rejected. It would merely choose one LAPACK
backend's last bits as canonical, leave Windows replay broken, and allow future backend upgrades to
rename behaviours without any semantic change. Relaxing hash or equality checks was also rejected
because it would weaken stale or forged evidence detection.

## Verification commands

```bash
# Linux
.venv-ralph/bin/python -m pytest tests/test_discovery_clustering.py tests/test_discovery_runs.py \
  tests/test_behaviour_catalogue.py tests/test_discovery_evidence.py tests/test_phase4_golden.py -q

# Windows from WSL
.venv-codex/Scripts/python.exe -m pytest tests/test_discovery_clustering.py tests/test_discovery_runs.py \
  tests/test_behaviour_catalogue.py tests/test_discovery_evidence.py tests/test_phase4_golden.py -q

.venv-ralph/bin/python -m ruff check src/market_structure_lab/discovery/pca.py \
  tests/test_discovery_clustering.py tests/test_phase4_golden.py
git diff --check
```
