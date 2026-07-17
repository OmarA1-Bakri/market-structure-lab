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
- **Discovery lab** — checksum-linked synthetic stable/rejected fixture contracts, never counted as
  real experiments.
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

The generator also boundedly scans `data/exports/trials/` through the canonical receipt verifier.
It reports exact counts by experiment mode and terminal status only after every receipt, artifact
hash, path, and file set verifies. Synthetic golden fixtures are outside that ledger and are never
counted. The current checked snapshot truthfully reports an implemented-but-empty ledger, zero real
trials, and accuracy not estimable. Receipt identities are asserted and byte-verified; the complete
snapshot-to-feature-to-normalizer derivation chain is not yet verified.
That proof belongs to a later, post-Phase-0 Phase 4 hardening gate.
