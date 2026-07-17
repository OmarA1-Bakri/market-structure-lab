import {
  CheckCircleIcon,
  DatabaseIcon,
  WarningCircleIcon,
} from "@phosphor-icons/react"

import { Badge } from "@/components/ui/badge"
import { Progress } from "@/components/ui/progress"
import { formatNumber } from "@/data/lab-data"
import { useReconciliationStatus } from "@/data/reconciliation-status"

function shortHash(value: string) {
  return `${value.slice(0, 10)}…${value.slice(-8)}`
}

export function ReconciliationStatusPanel() {
  const status = useReconciliationStatus()

  if (status.state === "loading") {
    return (
      <div className="rounded-[1.6rem] border border-border bg-card/42 p-5 panel-edge">
        <p className="font-mono text-xs text-muted-foreground">
          Loading promoted reconciliation evidence…
        </p>
      </div>
    )
  }

  if (status.state === "error") {
    return (
      <div className="rounded-[1.6rem] border border-destructive/30 bg-destructive/5 p-5 panel-edge">
        <div className="flex items-center gap-2 text-destructive">
          <WarningCircleIcon size={18} />
          <p className="text-sm font-medium">Evidence endpoint unavailable</p>
        </div>
        <p className="mt-2 font-mono text-xs text-muted-foreground">
          {status.message}
        </p>
      </div>
    )
  }

  const { data } = status
  const coverage = data.verified_symbols / data.work_units

  return (
    <div className="rounded-[1.6rem] border border-primary/20 bg-primary/[0.045] p-5 panel-edge md:p-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <Badge className="rounded-md bg-primary/12 font-mono text-[0.62rem] text-primary">
              DATA SNAPSHOT ONLINE
            </Badge>
            <span className="font-mono text-[0.65rem] text-muted-foreground">
              {data.run_id}
            </span>
          </div>
          <h2 className="mt-3 text-xl font-semibold tracking-tight">
            Promoted Binance row reconciliation
          </h2>
          <p className="mt-2 max-w-2xl text-sm leading-relaxed text-muted-foreground">
            The deployed console fetched a checksum-bearing, read-only evidence
            artifact. This is a deployment snapshot—not a browser connection to
            PostgreSQL and not a live trading feed.
          </p>
        </div>
        <div className="grid size-10 place-items-center rounded-xl border border-primary/20 bg-background/50 text-primary">
          <DatabaseIcon size={20} />
        </div>
      </div>

      <div className="mt-5 grid gap-4 border-t border-primary/15 pt-5 sm:grid-cols-2 xl:grid-cols-4">
        <div>
          <p className="font-mono text-2xl tracking-[-0.05em]">
            {formatNumber(data.row_count)}
          </p>
          <p className="mt-1 text-xs text-muted-foreground">audited minute keys</p>
        </div>
        <div>
          <p className="font-mono text-2xl tracking-[-0.05em]">
            {formatNumber(data.classification_counts.binance_correction)}
          </p>
          <p className="mt-1 text-xs text-muted-foreground">Binance corrections</p>
        </div>
        <div>
          <p className="font-mono text-2xl tracking-[-0.05em]">
            {formatNumber(data.classification_counts.binance_fill)}
          </p>
          <p className="mt-1 text-xs text-muted-foreground">Binance fills</p>
        </div>
        <div>
          <div className="flex items-center gap-2">
            <CheckCircleIcon size={17} weight="fill" className="text-primary" />
            <p className="font-mono text-2xl tracking-[-0.05em]">
              {data.verified_symbols}/{data.work_units}
            </p>
          </div>
          <p className="mt-1 text-xs text-muted-foreground">verified symbols</p>
        </div>
      </div>

      <div className="mt-5 grid gap-4 border-t border-primary/15 pt-4 lg:grid-cols-[minmax(0,1fr)_auto] lg:items-center">
        <div>
          <div className="flex items-center justify-between gap-3 text-[0.68rem] text-muted-foreground">
            <span>Promotable symbol coverage</span>
            <span className="font-mono">{(coverage * 100).toFixed(1)}%</span>
          </div>
          <Progress value={coverage * 100} className="mt-2 h-1.5" />
        </div>
        <div className="font-mono text-[0.62rem] leading-relaxed text-muted-foreground lg:text-right">
          <p>cutoff {data.cutoff}</p>
          <p title={data.canonical_logical_sha256}>
            canonical {shortHash(data.canonical_logical_sha256)}
          </p>
        </div>
      </div>
    </div>
  )
}
