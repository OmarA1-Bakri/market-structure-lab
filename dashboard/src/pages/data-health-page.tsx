import { useMemo, useState } from "react"
import {
  CheckCircleIcon,
  FunnelSimpleIcon,
  MagnifyingGlassIcon,
  WarningOctagonIcon,
} from "@phosphor-icons/react"
import { motion } from "motion/react"

import { MetricBlock } from "@/components/metric-block"
import { ReconciliationStatusPanel } from "@/components/reconciliation-status-panel"
import { StatusBadge } from "@/components/status-badge"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Progress } from "@/components/ui/progress"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import {
  evidenceTimestamp,
  formatCompact,
  formatNumber,
  headlineMetrics,
  symbolHealth,
  type SymbolState,
} from "@/data/lab-data"
import { cn } from "@/lib/utils"

type Filter = "all" | SymbolState

const filters: Array<{ value: Filter; label: string }> = [
  { value: "all", label: "All markets" },
  { value: "recovered", label: "Recovered" },
  { value: "partially_recovered", label: "Partial" },
  { value: "provider_absent", label: "Provider absent" },
  { value: "source_conflict", label: "Source conflict" },
]

export function DataHealthPage() {
  const [filter, setFilter] = useState<Filter>("all")
  const [query, setQuery] = useState("")

  const filtered = useMemo(() => {
    const normalized = query.trim().toUpperCase()
    return symbolHealth.filter(
      (item) =>
        (filter === "all" || item.status === filter) &&
        (!normalized || item.symbol.includes(normalized)),
    )
  }, [filter, query])

  const totalPlanned =
    headlineMetrics.recoveredLatest + headlineMetrics.remainingLatest
  const recoveryShare = headlineMetrics.recoveredLatest / totalPlanned

  return (
    <div className="space-y-9">
      <section className="grid gap-7 border-b border-border pb-8 xl:grid-cols-[minmax(0,1.2fr)_minmax(20rem,0.8fr)]">
        <div>
          <p className="font-mono text-[0.68rem] tracking-[0.16em] text-primary uppercase">
            latest terminal report
          </p>
          <h1 className="mt-4 text-4xl font-semibold tracking-[-0.05em] md:text-5xl">
            Data health, with the missing parts left visible.
          </h1>
          <p className="mt-4 max-w-2xl text-base leading-relaxed text-muted-foreground">
            The immutable dump remains authoritative. Compatible observations may
            fill absent keys, while provider absence and source disagreement remain
            explicit hard boundaries for downstream research.
          </p>
        </div>

        <Alert className="border-primary/20 bg-primary/[0.055] text-foreground">
          <CheckCircleIcon className="text-primary" />
          <AlertTitle>Coverage conservation passed</AlertTitle>
          <AlertDescription className="leading-relaxed text-muted-foreground">
            {formatNumber(totalPlanned)} planned minutes minus{" "}
            {formatNumber(headlineMetrics.recoveredLatest)} recovered equals{" "}
            {formatNumber(headlineMetrics.remainingLatest)} remaining. The terminal
            health exit is intentionally non-zero because coverage is trustworthy
            but incomplete.
          </AlertDescription>
        </Alert>
      </section>

      <ReconciliationStatusPanel />

      <section className="grid gap-5 sm:grid-cols-2 xl:grid-cols-4">
        <MetricBlock
          label="Immutable source"
          value={formatCompact(headlineMetrics.immutableRows)}
          detail="2017-11-25 through 2026-07-14 UTC"
        />
        <MetricBlock
          label="Validated supplements"
          value={formatCompact(headlineMetrics.supplements)}
          detail="Append-only; dump collisions excluded"
        />
        <MetricBlock
          label="Source gap runs"
          value={formatNumber(headlineMetrics.sourceGapRuns)}
          detail={`${formatCompact(headlineMetrics.sourceMissingMinutes)} missing minute slots at baseline`}
        />
        <MetricBlock
          label="Quality failures"
          value="0"
          detail="Duplicate keys, invalid OHLC, required-field nulls"
        />
      </section>

      <section className="grid gap-7 xl:grid-cols-[minmax(0,1fr)_22rem]">
        <div className="min-w-0 rounded-[1.6rem] border border-border bg-card/42 panel-edge">
          <div className="flex flex-col gap-4 border-b border-border p-4 md:flex-row md:items-center md:justify-between md:p-5">
            <div>
              <h2 className="text-lg font-semibold tracking-tight">
                Symbol coverage ledger
              </h2>
              <p className="mt-1 text-xs text-muted-foreground">
                {filtered.length} of {symbolHealth.length} symbols · cutoff{" "}
                {evidenceTimestamp}
              </p>
            </div>
            <div className="relative w-full md:w-64">
              <MagnifyingGlassIcon
                size={15}
                className="pointer-events-none absolute top-1/2 left-3 -translate-y-1/2 text-muted-foreground"
              />
              <Input
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="Filter symbol"
                aria-label="Filter symbols"
                className="h-9 bg-background/50 pl-9 font-mono text-xs"
              />
            </div>
          </div>

          <div className="flex gap-2 overflow-x-auto border-b border-border p-3 md:px-5">
            <FunnelSimpleIcon
              size={16}
              className="mt-1.5 shrink-0 text-muted-foreground"
            />
            {filters.map((item) => (
              <Button
                key={item.value}
                type="button"
                variant={filter === item.value ? "secondary" : "ghost"}
                size="sm"
                onClick={() => setFilter(item.value)}
                className={cn(
                  "shrink-0 rounded-lg text-xs",
                  filter === item.value &&
                    "border border-primary/15 bg-primary/8 text-primary",
                )}
              >
                {item.label}
              </Button>
            ))}
          </div>

          {filtered.length ? (
            <div className="max-h-[38rem] overflow-auto">
              <Table>
                <TableHeader className="sticky top-0 z-10 bg-card/96 backdrop-blur">
                  <TableRow>
                    <TableHead>Market</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead className="text-right">Recovered</TableHead>
                    <TableHead className="text-right">Remaining</TableHead>
                    <TableHead className="min-w-40">Coverage</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {filtered.map((item) => {
                    const share =
                      item.before === 0 ? 1 : item.recovered / item.before
                    return (
                      <motion.tr
                        layout
                        key={item.symbol}
                        className="border-b border-border transition-colors hover:bg-muted/25"
                      >
                        <TableCell>
                          <div className="flex items-center gap-2.5">
                            <span
                              className={cn(
                                "size-1.5 rounded-full",
                                item.current
                                  ? "bg-primary"
                                  : item.status === "source_conflict"
                                    ? "bg-destructive"
                                    : "bg-muted-foreground/55",
                              )}
                            />
                            <span className="font-mono text-xs font-medium">
                              {item.symbol}
                            </span>
                          </div>
                        </TableCell>
                        <TableCell>
                          <StatusBadge status={item.status} />
                        </TableCell>
                        <TableCell className="text-right font-mono text-xs text-foreground/80">
                          {formatNumber(item.recovered)}
                        </TableCell>
                        <TableCell className="text-right font-mono text-xs text-foreground/80">
                          {formatNumber(item.remaining)}
                        </TableCell>
                        <TableCell>
                          <div className="flex items-center gap-3">
                            <Progress
                              value={share * 100}
                              className="h-1.5 bg-muted"
                            />
                            <span className="w-10 text-right font-mono text-[0.65rem] text-muted-foreground">
                              {(share * 100).toFixed(1)}%
                            </span>
                          </div>
                        </TableCell>
                      </motion.tr>
                    )
                  })}
                </TableBody>
              </Table>
            </div>
          ) : (
            <div className="grid min-h-64 place-items-center px-6 text-center">
              <div>
                <MagnifyingGlassIcon
                  size={28}
                  className="mx-auto text-muted-foreground"
                />
                <p className="mt-3 text-sm font-medium">No markets match</p>
                <p className="mt-1 text-xs text-muted-foreground">
                  Clear the search or select another status.
                </p>
              </div>
            </div>
          )}
        </div>

        <aside className="space-y-5">
          <div className="rounded-[1.6rem] border border-border bg-card/42 p-5 panel-edge">
            <p className="text-[0.66rem] font-medium tracking-[0.18em] text-muted-foreground uppercase">
              Latest recovery
            </p>
            <div className="mt-5">
              <div className="flex items-end justify-between">
                <p className="font-mono text-4xl tracking-[-0.06em]">
                  {(recoveryShare * 100).toFixed(1)}%
                </p>
                <p className="font-mono text-[0.65rem] text-muted-foreground">
                  planned minutes
                </p>
              </div>
              <Progress value={recoveryShare * 100} className="mt-4 h-2" />
            </div>
            <dl className="mt-6 space-y-3 border-t border-border pt-5 text-xs">
              <div className="flex justify-between gap-4">
                <dt className="text-muted-foreground">Recovered</dt>
                <dd className="font-mono">
                  {formatNumber(headlineMetrics.recoveredLatest)}
                </dd>
              </div>
              <div className="flex justify-between gap-4">
                <dt className="text-muted-foreground">Remaining</dt>
                <dd className="font-mono">
                  {formatNumber(headlineMetrics.remainingLatest)}
                </dd>
              </div>
              <div className="flex justify-between gap-4">
                <dt className="text-muted-foreground">Failed batches</dt>
                <dd className="font-mono">0</dd>
              </div>
              <div className="flex justify-between gap-4">
                <dt className="text-muted-foreground">Current markets</dt>
                <dd className="font-mono">4 / 25</dd>
              </div>
            </dl>
          </div>

          <Alert className="border-destructive/18 bg-destructive/[0.045]">
            <WarningOctagonIcon className="text-destructive" />
            <AlertTitle>Source conflict is fail-closed</AlertTitle>
            <AlertDescription className="leading-relaxed text-muted-foreground">
              A candidate provider disagrees with immutable dump observations for
              nine symbols. No supplement is admitted until new provenance evidence
              resolves the conflict.
            </AlertDescription>
          </Alert>

          <div className="border-t border-border pt-5">
            <p className="text-[0.66rem] font-medium tracking-[0.18em] text-muted-foreground uppercase">
              Research boundary
            </p>
            <p className="mt-3 text-sm leading-relaxed text-muted-foreground">
              Missing minutes become explicit segment boundaries. Auction state,
              features, events, and transitions may not cross them.
            </p>
          </div>
        </aside>
      </section>
    </div>
  )
}
