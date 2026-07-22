# Phase 5 source price-precision authority gap

## Current state

The canonical snapshot publication schema does not bind either:

- a source price-precision metadata artifact digest; or
- an independently fixed authority checkpoint for that artifact.

A hash placed in a newly constructed `ValidationProgrammeConfig` is not an
independent authority because the same caller can choose both the metadata and
the expected hash.

## Fail-closed behaviour

Family B (`value_migration_acceptance`) profile construction and candidate
definition issuance are unavailable until the missing publication binding is
implemented. The system raises `SourcePricePrecisionUnavailable`; it does not
invent a tick size, infer one from validation data, or accept caller-attested
metadata.

The pure, integer-bin family B formula remains unit tested below the issuance
boundary. Passing that formula does not issue a signal or establish metadata
authority.

## Required future prerequisite

The source-mapping publication process must emit canonical precision metadata,
and the immutable snapshot identity must bind both the exact artifact digest
and an independently tracked checkpoint. The verifier must then read the bound
bytes from that publication rather than accepting bytes or receipts supplied by
the validation-programme caller.
