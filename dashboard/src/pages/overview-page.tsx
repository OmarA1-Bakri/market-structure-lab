import {
  ArrowRightIcon,
  CheckCircleIcon,
  FingerprintIcon,
  LockKeyIcon,
  ShieldCheckIcon,
  WarningCircleIcon,
} from "@phosphor-icons/react"
import { motion, useReducedMotion } from "motion/react"

import { MetricBlock } from "@/components/metric-block"
import { CoverageArc, MiniBars } from "@/components/visuals"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  evidenceTimestamp,
  formatCompact,
  formatNumber,
  headlineMetrics,
  phases,
  symbolHealth,
  verification,
  type PageId,
} from "@/data/lab-data"
import { cn } from "@/lib/utils"

const reveal = {
  hidden: { opacity: 0, y: 14 },
  visible: { opacity: 1, y: 0 },
}

export function OverviewPage({
  onNavigate,
}: {
  onNavigate: (page: PageId) => void
}) {
  const reduceMotion = useReducedMotion()
  const counts = symbolHealth.reduce<Record<string, number>>((accumulator, item) => {
    accumulator[item.status] = (accumulator[item.status] ?? 0) + 1
    return accumulator
  }, {})

  return (
    <motion.div
      initial={reduceMotion ? false : "hidden"}
      animate="visible"
      variants={{
        visible: {
          transition: { staggerChildren: reduceMotion ? 0 : 0.07 },
        },
      }}
      className="space-y-10"
    >
      <motion.section
        variants={reveal}
        className="grid items-start gap-8 border-b border-border pb-9 xl:grid-cols-[minmax(0,1.45fr)_minmax(22rem,0.75fr)]"
      >
        <div className="max-w-3xl">
          <div className="flex flex-wrap items-center gap-2">
            <Badge
              variant="outline"
              className="rounded-md border-primary/25 bg-primary/8 font-mono text-[0.65rem] text-primary"
            >
              PHASE 04 SOFTWARE GATE PASSED
            </Badge>
            <span className="font-mono text-[0.65rem] text-muted-foreground">
              evidence {evidenceTimestamp}
            </span>
          </div>
          <h1 className="mt-6 max-w-3xl text-4xl leading-[0.98] font-semibold tracking-[-0.055em] text-balance md:text-6xl">
            A research console built around what the evidence can prove.
          </h1>
          <p className="mt-5 max-w-2xl text-base leading-relaxed text-muted-foreground md:text-lg">
            Deterministic market representation, bounded data maintenance, causal
            features, and outcome-blind discovery are operational. Real market
            experiments remain the next deliberate handoff; validation and trading
            surfaces stay sealed.
          </p>
          <div className="mt-7 flex flex-wrap gap-3">
            <Button
              size="lg"
              onClick={() => onNavigate("data")}
              className="rounded-xl px-4"
            >
              Inspect data health
              <ArrowRightIcon size={16} data-icon="inline-end" />
            </Button>
            <Button
              variant="outline"
              size="lg"
              onClick={() => onNavigate("artifacts")}
              className="rounded-xl border-border bg-background/45 px-4"
            >
              Open an artifact
            </Button>
          </div>
        </div>

        <div className="relative overflow-hidden rounded-[1.8rem] border border-border bg-card/58 p-5 panel-edge md:p-6">
          <div className="absolute inset-0 hairline-grid opacity-45" />
          <div className="relative">
            <div className="flex items-center justify-between">
              <div>
                <p className="text-[0.66rem] font-medium tracking-[0.18em] text-muted-foreground uppercase">
                  Active boundary
                </p>
                <p className="mt-2 text-lg font-semibold tracking-tight">
                  Real market discovery
                </p>
              </div>
              <div className="grid size-10 place-items-center rounded-xl border border-primary/20 bg-primary/8 text-primary">
                <FingerprintIcon size={20} />
              </div>
            </div>
            <div className="mt-8 flex gap-2">
              {[0, 1, 2, 3, 4].map((item) => (
                <span
                  key={item}
                  className="h-1 flex-1 rounded-full bg-primary"
                />
              ))}
              <span className="h-1 flex-1 rounded-full bg-primary/35" />
              <span className="h-1 flex-1 rounded-full bg-foreground/10" />
              <span className="h-1 flex-1 rounded-full bg-foreground/10" />
            </div>
            <div className="mt-5 grid grid-cols-[auto_1fr] gap-x-4 gap-y-4">
              <CheckCircleIcon size={18} weight="fill" className="text-primary" />
              <div>
                <p className="text-sm font-medium">Software replay verified</p>
                <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
                  Frozen fixture runs reproduce byte-identically with sealed holdout
                  metadata.
                </p>
              </div>
              <WarningCircleIcon size={18} className="text-muted-foreground" />
              <div>
                <p className="text-sm font-medium">Market publication pending</p>
                <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
                  Freeze a deliberate candle snapshot, then publish Phase 3 feature
                  and event evidence.
                </p>
              </div>
              <LockKeyIcon size={18} className="text-muted-foreground" />
              <div>
                <p className="text-sm font-medium">Phase 5 remains untouched</p>
                <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
                  No future outcomes, performance claims, or validation metrics are
                  shown.
                </p>
              </div>
            </div>
          </div>
        </div>
      </motion.section>

      <motion.section variants={reveal}>
        <div className="grid gap-5 sm:grid-cols-2 xl:grid-cols-4">
          <MetricBlock
            label="Canonical candles"
            value={formatCompact(headlineMetrics.canonicalRows)}
            detail={`${formatNumber(headlineMetrics.immutableRows)} immutable + ${formatNumber(headlineMetrics.supplements)} validated supplements`}
          />
          <MetricBlock
            label="Tracked markets"
            value={headlineMetrics.symbols}
            detail="One-minute OHLCV across the reviewed universe"
          />
          <MetricBlock
            label="Feature contract"
            value="FS-000001"
            detail="16 auction-informed + 10 sequence features"
          />
          <MetricBlock
            label="Regression proof"
            value={verification.tests}
            detail={`plus ${verification.postgresProfiles} disposable PostgreSQL profiles`}
          />
        </div>
      </motion.section>

      <motion.section
        variants={reveal}
        className="grid gap-8 xl:grid-cols-[minmax(0,1.2fr)_minmax(20rem,0.8fr)]"
      >
        <div className="rounded-[1.8rem] border border-border bg-card/46 p-5 panel-edge md:p-7">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div>
              <p className="text-[0.66rem] font-medium tracking-[0.18em] text-muted-foreground uppercase">
                Latest freshness run
              </p>
              <h2 className="mt-2 text-2xl font-semibold tracking-[-0.035em]">
                Coverage conserved, gaps still explicit.
              </h2>
              <p className="mt-2 max-w-xl text-sm leading-relaxed text-muted-foreground">
                The run recovered 8.57 million planned minutes without modifying
                immutable source rows. Remaining coverage limitations are preserved
                as evidence rather than hidden.
              </p>
            </div>
            <CoverageArc
              recovered={headlineMetrics.recoveredLatest}
              remaining={headlineMetrics.remainingLatest}
            />
          </div>

          <div className="mt-7 grid gap-4 border-t border-border pt-5 sm:grid-cols-2 lg:grid-cols-4">
            {[
              ["Recovered", counts.recovered ?? 0, "current through cutoff"],
              ["Partial", counts.partially_recovered ?? 0, "bounded residual gaps"],
              ["Provider absent", counts.provider_absent ?? 0, "terminal source evidence"],
              ["Source conflict", counts.source_conflict ?? 0, "dump remains authoritative"],
            ].map(([label, count, detail]) => (
              <div key={String(label)}>
                <p className="font-mono text-2xl tracking-[-0.05em]">{count}</p>
                <p className="mt-1 text-xs font-medium">{label}</p>
                <p className="mt-1 text-[0.68rem] text-muted-foreground">{detail}</p>
              </div>
            ))}
          </div>
        </div>

        <div className="rounded-[1.8rem] border border-border bg-card/46 p-5 panel-edge md:p-7">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-[0.66rem] font-medium tracking-[0.18em] text-muted-foreground uppercase">
                Auction benchmark
              </p>
              <h2 className="mt-2 text-xl font-semibold tracking-tight">
                Bounded replay profile
              </h2>
            </div>
            <ShieldCheckIcon size={24} className="text-primary" />
          </div>
          <MiniBars
            values={[540, 612, 590, 641, 677, 704, 692, 730]}
            className="mt-7"
          />
          <div className="mt-6 grid grid-cols-2 gap-5">
            <div>
              <p className="font-mono text-2xl tracking-[-0.05em]">
                {verification.benchmarkThroughput.toFixed(2)}
              </p>
              <p className="mt-1 text-xs text-muted-foreground">candles / second</p>
            </div>
            <div>
              <p className="font-mono text-2xl tracking-[-0.05em]">
                {verification.peakMemoryMib.toFixed(3)}
              </p>
              <p className="mt-1 text-xs text-muted-foreground">MiB peak traced</p>
            </div>
          </div>
          <p className="mt-6 border-t border-border pt-4 text-[0.7rem] leading-relaxed text-muted-foreground">
            Observed benchmark only. Correctness is pinned by deterministic hashes
            and {verification.equivalenceChecks} incremental/full comparisons at{" "}
            {verification.absoluteTolerance.toExponential()} tolerance.
          </p>
        </div>
      </motion.section>

      <motion.section variants={reveal} className="border-t border-border pt-8">
        <div className="flex flex-col gap-4 md:flex-row md:items-end md:justify-between">
          <div>
            <p className="text-[0.66rem] font-medium tracking-[0.18em] text-muted-foreground uppercase">
              Canonical progression
            </p>
            <h2 className="mt-2 text-2xl font-semibold tracking-[-0.035em]">
              Phase gates stay visible.
            </h2>
          </div>
          <p className="max-w-md text-sm leading-relaxed text-muted-foreground">
            A completed behaviour detector is not a hypothesis, a hypothesis is not
            a validated edge, and a fixture replay is not market evidence.
          </p>
        </div>

        <div className="mt-7 divide-y divide-border border-y border-border">
          {phases.map((phase) => (
            <div
              key={phase.label}
              className="grid grid-cols-[3.5rem_minmax(0,1fr)_auto] items-center gap-3 py-3.5"
            >
              <span className="font-mono text-xs text-muted-foreground">
                {phase.label}
              </span>
              <span
                className={cn(
                  "text-sm",
                  phase.state === "locked" && "text-muted-foreground",
                )}
              >
                {phase.name}
              </span>
              <span
                className={cn(
                  "font-mono text-[0.62rem] tracking-[0.12em] uppercase",
                  phase.state === "complete" && "text-primary",
                  phase.state === "next" && "text-foreground",
                  phase.state === "locked" && "text-muted-foreground",
                )}
              >
                {phase.state}
              </span>
            </div>
          ))}
        </div>
      </motion.section>
    </motion.div>
  )
}
