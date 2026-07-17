# Market Structure Lab Dashboard

A deployable, read-only research console for the verified Phase 0–4 Market Structure Lab
implementation.

The dashboard deliberately does not expose P&L, signals, positions, strategies, portfolio controls,
or live trading. Real market discovery publication is the next active gate; untouched validation
and all trading phases remain sealed.

## Included views

- **Overview** — phase gates, canonical data totals, freshness coverage, and verification evidence.
- **Data health** — searchable and filterable 25-symbol freshness ledger.
- **Auction replay** — exact `auction-rolling-3-v1` deterministic golden fixture.
- **Feature registry** — searchable `FS-000001` definitions and causal cutoff flow.
- **Discovery lab** — `DR-000601` completed and `DR-000602` rejected golden software replays.
- **Artifact inspector** — local-only JSON parsing and SHA-256 calculation for supported evidence
  files.

The embedded figures come from verified repository evidence dated `2026-07-16`. Discovery content
is prominently labelled as a software fixture, not empirical market evidence.

## Local development

Requirements:

- Node.js 22.12 or newer
- npm

```powershell
cd dashboard
npm ci
npm run dev
```

Open `http://localhost:5173`.

## Verification

```powershell
npm run typecheck
npm run lint
npm run build
```

The production build is written to `dashboard/dist/`.

## Deploy

The app is a static Vite build and can be hosted by Vercel, Netlify, Cloudflare Pages, or any static
web server.

For Vercel, set the project root directory to `dashboard`. The checked-in `vercel.json` uses:

- build command: `npm run build`
- output directory: `dist`

No environment variables, database credentials, or backend connection are required.

## Data integration boundary

`data/exports/` is intentionally gitignored and is unavailable to a hosted static site. The
dashboard fetches a compact checksum-bearing reconciliation contract from
`/data/reconciliation-status.json`, ships the remaining verified evidence snapshot, and provides a
local browser artifact inspector. The reconciliation contract records the promoted run, cutoff,
classification totals, and canonical logical hash. It is refreshed deliberately when a new run is
promoted and the dashboard is deployed.

A future continuously updated integration should use a separately approved authenticated,
read-only artifact API.
The browser must never connect directly to PostgreSQL or receive credential-bearing connection
URLs. Any API must preserve manifest identities, UTC cutoffs, checksums, holdout boundaries, and
the distinction between behaviour, hypothesis, validation, edge, and strategy.
