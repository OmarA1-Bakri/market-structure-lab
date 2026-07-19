# Feature leakage audit contract v1

## Scope

Task 11 strengthens the Phase 3 feature-publication boundary. It does not run discovery, inspect
holdout rows, validate an edge, or prove that an implementation is leakage-free.

Every registered feature now freezes and hashes its observable dependency contract:

- source field paths;
- causal trailing-window policy and required warm-up observations;
- observable cutoff rule;
- training-partition-only normalization requirement;
- explicit future/outcome prohibition; and
- exact registered builder ID and builder version.

The discovery audit rejects future or outcome sources even when the feature name is innocuous. It
also rejects full-period normalization, global min/max normalization, centered windows,
future-dependent labels, timestamps observable after the declared cutoff, and unregistered builder
identities. Feature publication validates this contract and the independent approval before it
replays any producer output.

`FeatureBuilder.build_batch` calculates snapshots through the audited non-virtual state transition,
not an overridable instance or subclass method, while spooling ordered canonical `FeatureRow`
output to a no-follow replay artifact. The returned frozen envelope
pins builder ID/version, registry and aggregate dependency identity, exact row count, ordered output
checksum, artifact checksum, and explicit row/record caps. `publish_feature_rows` accepts only this
producer-bound envelope; a plain iterable of otherwise valid rows is rejected. Replay rechecks the
artifact, every canonical row, count, and digest before a durable publication can appear.

Issuance is bound to the exact batch object through a weak identity registry that also freezes all
expected metadata. Replacement, shallow/deep copying, manual construction, subclass instances, and
post-issuance artifact substitution are unissued or invalid even when every visible field matches.
Authentication returns the registry's immutable metadata snapshot; replay, bounded no-follow
artifact reads, producer checks, receipts, and manifests use only that snapshot rather than mutable
object attributes. `FeatureBuildBatch.close()` explicitly revokes an instance, and garbage
collection removes weak issuance entries without retaining abandoned batches. Close before replay
lease acquisition fails closed; close after acquisition leaves that active immutable-snapshot replay
safe but prevents every later replay.

## Independent receipt

Feature publications use derived manifest schema 3 and leakage receipt schema 2, including
`leakage-audit.json`. The immutable receipt binds:

- the registry and pre-publication approval checksums;
- the complete publication identity;
- the exact ordered producer-output checksum and row count;
- the independently serialized Parquet-publication content checksum;
- reviewer and review-artifact identity;
- per-field dependency-contract and negative-test evidence checksums; and
- the builder ID/version for every published field.

The receipt has its own logical checksum, while the manifest separately records the receipt file's
byte checksum. Publication verification rejects missing, extra, changed, symlinked, or
path-traversing artifacts through the existing bounded regular-file primitives. The verifier always
loads the canonical on-disk manifest and requires an optional supplied object to match it exactly;
the supplied object can never bypass changed disk bytes. Audit evidence is bounded to 10,000
feature fields and the receipt reader is capped at 4 MiB. Feature-definition field counts and string
lengths, reviewer metadata, negative evidence, producer rows, and producer record bytes have
explicit conservative caps. Receipt size is computed before replay, and the atomic writer rejects
content above 4 MiB before creating or replacing any artifact.

## Residual manual-review boundary

`metadata_proves_no_leakage` is always false and `residual_manual_review_required` is always true.
The receipt proves that declared contracts, reviewed test evidence, publication identity, and
serialized output were checksum-bound together. It cannot prove that source-field declarations are
truthful or that arbitrary builder code used only those fields. An independent reviewer must still
inspect builder implementation and cutoff/dependency negative tests. A ceremonial reviewer string
alone is not sufficient: publication requires the complete checksum-bound approval evidence.

The committed Phase 4 software fixture was migrated because the registry hash intentionally now
includes these causal dependency contracts. Discovery rows, PCA values, cluster assignments,
behaviour definitions, motifs, transition bytes, and statuses did not change; only identities that
correctly include the registry/config provenance changed.
