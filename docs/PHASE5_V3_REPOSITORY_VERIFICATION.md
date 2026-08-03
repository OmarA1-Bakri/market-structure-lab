# Phase 5-v3 repository verification

## Result

The exact source checkpoint `057a4a7293a0793f2bf2db98de1eb860a9cb3ddc` on
`agent/research-lab-foundation` passed the deterministic repository verification gate. The
machine-readable publication is in `docs/evidence/phase5_v3_repository_verification/`; its summary
identity is `5ce012e50e01f52f757d9f39f2d9357380b3fd84424f76237133b1f023aacaee`.

This engineering result does not change the scientific terminal state. Phase 5-v3 remains a
truthful `missing_prerequisites` closure with zero scientifically evaluated candidates. No
successor was selected, the final holdout remains unopened, and Phases 6 and 7 remain closed.

## Deterministic test inventory and sharding

- Python: CPython 3.13.13; per-shard interpreter and OS identities are recorded in the shard
  publications.
- Lock SHA-256: `a16b349b97b4c265129df59092f69981d9150a131772b8ed9f9ebda28dcaf44b`.
- Collected tests: 2,172.
- Collection identity: `17f59f9087af668c1cc19f9956f7acbe46724960a8e14c54d17e5d0a35558f85`.
- Frozen manifest identity:
  `ee06cf0ce639a45df7bef56e9689e3fbf8f34b38833fb6214d87ca513fb0ebfa`.
- Frozen manifest file SHA-256:
  `8d47e23426f4e62175d07e1dae62318df5328f5dead679a7d66b51a610758172`.
- Eight deterministic, non-overlapping non-Windows shards: 2,159 tests.
- One native Windows PowerShell shard: all 13 runner tests passed on Windows 11 under CPython
  3.13.13.
- Reconciled result: 2,153 passed, 19 skipped, 0 failed, and every collected node represented
  exactly once.
- Reconciliation identity:
  `6a79fbd48e105456048228cd16907773365600cb201beaff43378ccfec20d87a`.
- Reconciliation file SHA-256:
  `5faaf81e8cf4b230c1a0d06490fd0b408a89173fb5b5bc651331d85001851367`.

The manifest records the collection command and exact node inventory. Each shard publication
records its command, platform, start/end timestamps, exit code, stdout/stderr hashes, and exact
per-node status. Reconciliation rehashed the collection and shard evidence before accepting the
complete inventory.

The summary also binds the controlling audit identity `e43d329a...`, no-successor identity
`661093d5...`, closure `22dd6454...`, and the exact closure publication, configuration, programme
tree, programme receipt, and empty-batch identities. The engineering publication therefore cannot
detach its final-access zeros or terminal decision from the authenticated scientific records.

## Superseded verification attempts

No failed verification attempt was hidden or relabelled.

1. The initial WSL-on-DrvFS shard run was terminalized as abandoned after bounded evidence showed
   unsuitable throughput. Its receipt is preserved as `superseded-drvfs-attempt.json`.
2. The first trusted-filesystem run at `329b36304fd21aeacfdf13732fd8fd47a53ccb94`
   exposed two real gate defects: failure-receipt environment capture was sensitive to a mocked
   subprocess, and the isolated dashboard test lacked its locked Node installation. The defects
   were corrected, independently reviewed, and the complete inventory was recollected and rerun at
   `057a4a7` with `npm ci` completed before testing.
3. A first native-Windows launch encountered the repository's WSL-only `.venv` link. That launch
   was preserved as failed; the final Windows run used a separate locked Windows environment and
   passed all 13 assigned tests. The WSL link was restored unchanged.

## Additional gates

| Gate | Result |
|---|---|
| Exact Phase 5-v3 parent-tree integration | 5 passed; log SHA-256 `99c2644b3ad19aa64513b829eec974a9773c174ad1a3b113cf18d0b246292fb9` |
| `uv lock --check` | passed |
| Ruff lint | passed |
| Ruff formatting | 246 files already formatted |
| Mypy | 124 source files, no issues |
| Pyright | 0 errors |
| `git diff --check` | passed; only existing line-ending warnings |
| Locked package build on trusted filesystem | wheel and sdist passed |
| Compose configuration | passed with non-secret verification-only required values |
| Dashboard evidence verifier | 25 symbols and 26 features verified |
| Dashboard lint/typecheck/build | passed; 5,091 modules transformed |

Build identities are recorded in `summary.json`. `npm audit` remains a non-scientific dependency
risk: 4 transitive findings (2 moderate, 2 high), with no safe non-force lock-preserving repair
available during this run. It does not invalidate the completed dashboard build, but it remains an
explicit maintenance risk.

## Closure and readiness conclusion

The closure verifier reopened the complete 1,104-slot VP/VR parent tree and authenticated the
closure, publication, configuration, programme tree, programme receipt, empty batch, final marker,
and zero final access. Its exact scientific funnel remains:

```text
1,104 frozen
-> 1,104 terminal
-> 1,104 failed / not_evaluated
-> 0 scientifically evaluated
-> 0 inconclusive
-> 0 rejected
-> 0 supported_development
-> 0 final-eligible
-> 0 validated
-> 0 promoted
```

The cost-policy over-constraint is corrected under classification A and independently approved.
The successor gate still fails because no trusted development-source authority is admitted and the
source-bound complete 1,104-slot production roster is not executable. Aggregate publication is
local work only after source admission; Family B historical precision remains family-local and
would use effective `p=1` if unavailable. Therefore `no_ready_successor` is the only truthful
terminal state, not hypothesis rejection or a mixed result.
