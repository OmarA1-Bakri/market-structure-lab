# Phase 5 Bootstrap/Holm Specification Amendment

**Amendment ID:** `MSL-P5-SR-001`  
**Status:** approved specification correction  
**Effective for:** new Phase 5 development computations created after this amendment  
**Historical evidence:** immutable; existing 4,096-draw attempts and receipts remain valid records of their original contract and must not be rewritten or mixed with amended programme identities

## Reason for amendment

The frozen Phase 5 contract combined:

- 4,096 bootstrap draws;
- the two-sided add-one p-value `2 * min((1+n_le)/(B+1), (1+n_ge)/(B+1))`;
- family-local Holm correction at alpha `0.01`; and
- 24 primary hypotheses in family A.

That combination makes every family A rejection mathematically impossible. With `B=4,096`, the smallest attainable p-value is `2/4,097`, so the smallest attainable first-step Holm-adjusted p-value is:

```text
24 * 2 / 4,097 = 0.011715889675372224 > 0.01
```

This contradicts the accepted known-effect lifecycle requirement. It is a specification defect, not a negative scientific result.

Two independent adversarial reviews reproduced the result algebraically and through the repository's actual Holm implementation. They rejected changes to the add-one formula, two-sided test, family allocation, roster, or Holm method as larger scientific changes.

## Review provenance

The correction was independently reviewed on 2026-08-01 before production migration:

- `phase5_bootstrap_holm_spec_review` — specification reviewer; approved the contradiction proof,
  rejected the 4,096-draw contract as capable of supporting family A, and recommended 4,800 draws;
- `phase5_bootstrap_holm_adversarial_review` — adversarial quantitative reviewer; independently
  reproduced family-by-family attainability, rejected proxy alternatives, and recommended 4,800 draws.

Both reviews required the amendment to be identity-bound, executable, bounded, and non-retroactive.

## Corrected frozen contract

New amended Phase 5 computations must use:

- exactly **4,800** SHA-seeded weekly-block bootstrap draws;
- add-one denominator **4,801**;
- non-interpolated 95% order-statistic indices **119** and **4,679**;
- maximum bootstrap cell ceiling **307,200** (`4,800 * 64`);
- the existing two-sided p-value, family-local alpha `0.01`, Holm procedure, primary counts, MDE, support, interval-width, control, cost, robustness, exposure, and capacity rules unchanged.

The mathematical minimum is 4,799 draws, which lands exactly on the A-family alpha boundary. The approved 4,800-draw count provides a strict representable margin while increasing bounded work by only 17.2 percent. It restores logical attainability; it does not confer empirical support.

## Identity and evidence rules

New V2 configurations bind the following canonical policy identity:

```text
policy ID:     MSL-P5-SR-001
hash domain:   phase5-bootstrap-holm-specification-amendment-v1
content hash:  72c42270e237403a2c098f4599e6e09ff032d15009d64dd525dda987a8fff2c3
```

The canonical content preimage is the immutable mapping exported as
`PHASE5_BOOTSTRAP_HOLM_AMENDMENT_PAYLOAD` in `research/validation_v2_models.py`. It contains the
amendment ID, schema, 4,800 draws, 4,801 denominator, indices 119/4,679, 307,200 cells, 64 blocks,
family alpha 0.01, primary counts, two-sided add-one method, family-local Holm method, and
unevaluable-primary effective p-value 1. The content hash is domain-separated canonical JSON, not
the raw Markdown file hash.

- The amended draw count and ceilings must change the work-budget hash and every programme/config identity that binds it.
- The amendment ID and amendment content hash must be bound into new V2 policy identities before amended results can become receipt authority.
- A 4,096-draw result cannot be relabelled as an amended result.
- Old receipts remain verifiable under their original identity but cannot be mixed into an amended programme batch.
- No raw primary p-value can determine `rejected` or `supported_development` before complete family Holm correction and every mandatory gate.
- Unevaluable primary hypotheses continue to enter Holm as effective `p=1`.

## Required executable gates

1. Prove the legacy 4,096-draw floor cannot reject family A.
2. Prove 4,798 draws remain insufficient.
3. Prove 4,800 draws make an A-family first-step Holm rejection attainable.
4. Lock draw count, denominator, CI indices, draw ceiling, and cell ceiling.
5. Preserve deterministic replay and over-budget rejection before iteration/allocation.
6. Prove all family sizes A=24, B=8, G=8, E=8, and D=16 remain correctly corrected.
7. Prove amended and historical programme identities cannot be mixed.

## Superseded clauses

This amendment supersedes only the exact 4,096-draw numerical clauses in:

- `2026-07-22-phase5-validation.md`;
- `2026-07-22-phase5-validation-test-spec.md`; and
- `2026-07-22-phase5-bounded-implementation.md`.

All other scientific and engineering requirements in those plans remain in force.
