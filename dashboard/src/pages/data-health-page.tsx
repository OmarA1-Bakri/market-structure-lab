import { useMemo, useState } from "react";
import {
  FunnelSimpleIcon,
  MagnifyingGlassIcon,
  WarningOctagonIcon,
} from "@phosphor-icons/react";
import { motion } from "motion/react";

import { MetricBlock } from "@/components/metric-block";
import { ReconciliationStatusPanel } from "@/components/reconciliation-status-panel";
import { StatusBadge } from "@/components/status-badge";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Progress } from "@/components/ui/progress";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useLabEvidence, type SymbolState } from "@/data/lab-evidence";
import { formatCompact, formatNumber } from "@/data/lab-data";
import { cn } from "@/lib/utils";

type Filter = "all" | SymbolState;
const filters: Array<{ value: Filter; label: string }> = [
  { value: "all", label: "All markets" },
  { value: "recovered", label: "Recovered" },
  { value: "partially_recovered", label: "Partial" },
  { value: "provider_absent", label: "Provider absent" },
  { value: "source_conflict", label: "Source conflict" },
];

export function DataHealthPage() {
  const evidence = useLabEvidence();
  const [filter, setFilter] = useState<Filter>("all");
  const [query, setQuery] = useState("");
  const symbols = useMemo(
    () => (evidence.state === "ready" ? evidence.data.freshness.symbols : []),
    [evidence],
  );
  const filtered = useMemo(() => {
    const normalized = query.trim().toUpperCase();
    return symbols.filter(
      (item) =>
        (filter === "all" || item.status === filter) &&
        (!normalized || item.symbol.includes(normalized)),
    );
  }, [filter, query, symbols]);
  if (evidence.state !== "ready")
    return (
      <div className="m-8 rounded-2xl border border-border p-8 font-mono text-sm">
        {evidence.state === "loading"
          ? "Loading verified evidence…"
          : `Verified evidence unavailable: ${evidence.message}`}
      </div>
    );
  const { data } = evidence;
  const freshness = data.freshness;
  const totalPlanned =
    freshness.recovered_minutes + freshness.remaining_missing_minutes;
  const recoveryShare =
    totalPlanned === 0 ? 1 : freshness.recovered_minutes / totalPlanned;
  const currentMarkets = symbols.filter(
    (item) => item.current_through_cutoff,
  ).length;

  return (
    <div className="space-y-9">
      <section className="grid gap-7 border-b border-border pb-8 xl:grid-cols-[minmax(0,1.2fr)_minmax(20rem,0.8fr)]">
        <div>
          <p className="font-mono text-[0.68rem] tracking-[0.16em] text-primary uppercase">
            verified latest terminal report
          </p>
          <h1 className="mt-4 text-4xl font-semibold tracking-[-0.05em] md:text-5xl">
            Data health, with the missing parts left visible.
          </h1>
          <p className="mt-4 max-w-2xl text-base leading-relaxed text-muted-foreground">
            The deployment snapshot is generated from a linked plan and report
            whose hashes and cutoff agree. Provider absence and source
            disagreement remain explicit downstream boundaries.
          </p>
        </div>
        <Alert className="border-primary/20 bg-primary/[0.055] text-foreground">
          <AlertTitle>Coverage arithmetic verified</AlertTitle>
          <AlertDescription className="leading-relaxed text-muted-foreground">
            {formatNumber(freshness.before_missing_minutes)} before minus{" "}
            {formatNumber(freshness.recovered_minutes)} recovered equals{" "}
            {formatNumber(freshness.remaining_missing_minutes)} remaining.
          </AlertDescription>
        </Alert>
      </section>
      <ReconciliationStatusPanel
        evidence={data.reconciliation}
        generatedAt={data.generated_at}
      />
      <section className="grid gap-5 sm:grid-cols-2 xl:grid-cols-4">
        <MetricBlock
          label="Latest inserted rows"
          value={formatNumber(freshness.inserted_rows)}
          detail="Append-only freshness run"
        />
        <MetricBlock
          label="Recovered minutes"
          value={formatCompact(freshness.recovered_minutes)}
          detail="At the frozen cutoff"
        />
        <MetricBlock
          label="Remaining minutes"
          value={formatCompact(freshness.remaining_missing_minutes)}
          detail="Not hidden or interpolated"
        />
        <MetricBlock
          label="Current markets"
          value={`${currentMarkets} / ${symbols.length}`}
          detail="Current through this cutoff only"
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
                {filtered.length} of {symbols.length} symbols · cutoff{" "}
                {freshness.evidence_timestamp}
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
          <div className="max-h-[38rem] overflow-auto">
            <Table>
              <TableHeader className="sticky top-0 z-10 bg-card/96 backdrop-blur">
                <TableRow>
                  <TableHead>Market</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead className="text-right">Recovered</TableHead>
                  <TableHead className="text-right">Remaining</TableHead>
                  <TableHead className="min-w-40">Recovery share</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {filtered.map((item) => {
                  const share =
                    item.before_missing_minutes === 0
                      ? 1
                      : item.recovered_minutes / item.before_missing_minutes;
                  return (
                    <motion.tr
                      layout
                      key={item.symbol}
                      className="border-b border-border"
                    >
                      <TableCell>
                        <span className="font-mono text-xs font-medium">
                          {item.symbol}
                        </span>
                      </TableCell>
                      <TableCell>
                        <StatusBadge status={item.status} />
                      </TableCell>
                      <TableCell className="text-right font-mono text-xs">
                        {formatNumber(item.recovered_minutes)}
                      </TableCell>
                      <TableCell className="text-right font-mono text-xs">
                        {formatNumber(item.remaining_missing_minutes)}
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
                  );
                })}
              </TableBody>
            </Table>
          </div>
        </div>
        <aside className="space-y-5">
          <div className="rounded-[1.6rem] border border-border bg-card/42 p-5 panel-edge">
            <p className="text-[0.66rem] font-medium tracking-[0.18em] text-muted-foreground uppercase">
              Latest recovery share
            </p>
            <p className="mt-5 font-mono text-4xl">
              {(recoveryShare * 100).toFixed(3)}%
            </p>
            <Progress value={recoveryShare * 100} className="mt-4 h-2" />
            <p className="mt-4 text-xs leading-relaxed text-muted-foreground">
              This is recovery within the latest planned missing-minute set, not
              overall temporal coverage.
            </p>
          </div>
          <Alert className="border-destructive/18 bg-destructive/[0.045]">
            <WarningOctagonIcon className="text-destructive" />
            <AlertTitle>Source conflict is fail-closed</AlertTitle>
            <AlertDescription className="leading-relaxed text-muted-foreground">
              {freshness.status_counts.source_conflict ?? 0} symbols remain
              quarantined in this report.
            </AlertDescription>
          </Alert>
        </aside>
      </section>
    </div>
  );
}
