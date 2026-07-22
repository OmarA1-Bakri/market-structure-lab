# Market Structure Lab Dashboard

A deployable, read-only research console for Market Structure Lab evidence. The current repository
gate is **Phase 0 trustworthy-foundation remediation**; later research and trading phases remain
locked.

The dashboard deliberately does not expose P&L, signals, positions, strategies, portfolio controls,
or live trading.

## Included views

- **Overview** — current phase gate, checksum-linked freshness, and scoped experiment readiness.
- **Data health** — searchable symbol freshness and bounded/partial reconciliation evidence.
- **Auction replay** — illustrative rolling-window fixture; no verification receipt is published.
- **Feature registry** — generated `FS-000001` definitions and registry identity.
- **Discovery lab** — the checksum-linked Task 14 `PG-000004` outcome-blind programme checkpoint
  alongside synthetic stable/rejected fixture contracts that are never counted as real experiments.
- **Artifact inspector** — local-only JSON parsing and SHA-256 calculation.

## Local development

Requirements: Node.js 22.12 or newer and npm.

```powershell
cd dashboard
npm ci
npm run dev
```

Open `http://localhost:5173`.

## Verification

```powershell
npm run verify:evidence
npm run typecheck
npm run lint
npm run build
```

`npm run build` runs the dependency-free evidence verifier before TypeScript and Vite. The production
build is written to `dashboard/dist/`.

## Deploy

The app is a static Vite build. For Vercel, set the project root to `dashboard`; `vercel.json` uses
`npm run build` and publishes `dist`. No environment variables, database credentials, or backend
connection are required.

## Evidence publication boundary

`data/exports/` is intentionally gitignored and unavailable to a hosted static site. The browser
fetches one checked deployment artifact, `/data/lab-evidence-v1.json`, and never connects directly
to PostgreSQL.

Generate the public artifact locally from verified repository evidence:

```bash
uv run python scripts/generate_dashboard_evidence.py \
  --generated-at 2026-07-17T00:00:00Z
```

The explicit `generated_at` is publication time. `freshness.evidence_timestamp` is the verified data
cutoff. Deployments cannot regenerate from ignored/private inputs, so the prebuild verifier rejects
malformed or internally inconsistent checked JSON instead. Refresh the artifact deliberately after
source evidence changes; never expose credentials or infer promotion from partial reconciliation.

The generator boundedly scans both flat and one-level grouped directories under
`data/exports/trials/` through the canonical receipt verifier. It reports exact counts by experiment
mode and terminal status only after every receipt, artifact hash, path, and file set verifies.
Synthetic golden fixtures are outside that ledger and are never counted. The current checked
snapshot retains all four Task 14 discovery attempts: two failed and two rejected. It additionally
verifies the `PG-000004` preregistration, non-holdout snapshot boundary, terminal `DR-000704`
receipt, and reliability-vector linkage. Accuracy remains not estimable, outcomes remain unattached,
and `derivation_chain_verified` deliberately remains `false`; this dashboard checkpoint does not
claim that every upstream derived-publication byte has been independently replayed.
