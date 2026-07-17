import { useState } from "react"
import {
  BrainIcon,
  CheckCircleIcon,
  FlaskIcon,
  LockKeyIcon,
  WarningCircleIcon,
} from "@phosphor-icons/react"
import { AnimatePresence, motion } from "motion/react"

import { MetricBlock } from "@/components/metric-block"
import { ScatterPlot } from "@/components/visuals"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Progress } from "@/components/ui/progress"
import { discoveryFixture } from "@/data/lab-data"
import { cn } from "@/lib/utils"

type RunKey = "stable" | "rejected"

export function DiscoveryPage() {
  const [runKey, setRunKey] = useState<RunKey>("stable")
  const run = discoveryFixture[runKey]
  const rejected = runKey === "rejected"

  return (
    <div className="space-y-9">
      <section className="grid gap-7 border-b border-border pb-8 xl:grid-cols-[minmax(0,1.2fr)_minmax(20rem,0.8fr)]">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <Badge
              variant="outline"
              className="rounded-md border-primary/25 bg-primary/8 font-mono text-[0.65rem] text-primary"
            >
              GOLDEN SOFTWARE REPLAY
            </Badge>
            <span className="font-mono text-[0.65rem] text-muted-foreground">
              no outcomes · holdout metadata sealed
            </span>
          </div>
          <h1 className="mt-5 text-4xl font-semibold tracking-[-0.05em] md:text-5xl">
            Discovery is allowed to find structure, not declare an edge.
          </h1>
          <p className="mt-4 max-w-2xl text-base leading-relaxed text-muted-foreground">
            The inspectable baseline uses canonical PCA, seeded K-means, multi-axis
            stability, bounded motifs, and boundary-aware dwell-compressed
            transitions. Unstable definitions remain visible as rejected runs.
          </p>
        </div>

        <Alert className="border-border bg-card/40">
          <WarningCircleIcon className="text-primary" />
          <AlertTitle>Fixture, not market evidence</AlertTitle>
          <AlertDescription className="leading-relaxed text-muted-foreground">
            The two behaviours below come from a synthetic verification fixture with
            unusually strong separation. They do not establish recurrence,
            profitability, causation, or a tradeable edge.
          </AlertDescription>
        </Alert>
      </section>

      <section className="flex flex-col gap-5 lg:flex-row lg:items-center lg:justify-between">
        <div className="relative flex w-fit gap-1 rounded-xl border border-border bg-card/45 p-1">
          {(["stable", "rejected"] as const).map((item) => (
            <Button
              key={item}
              type="button"
              variant="ghost"
              onClick={() => setRunKey(item)}
              className={cn(
                "relative min-w-36 rounded-lg text-xs",
                runKey === item
                  ? "text-foreground hover:bg-transparent"
                  : "text-muted-foreground",
              )}
            >
              {runKey === item ? (
                <motion.span
                  layoutId="run-selector"
                  className="absolute inset-0 rounded-lg border border-primary/15 bg-primary/8"
                  transition={{ type: "spring", stiffness: 250, damping: 25 }}
                />
              ) : null}
              <span className="relative">
                {item === "stable" ? "Completed run" : "Rejected run"}
              </span>
            </Button>
          ))}
        </div>
        <div className="flex flex-wrap items-center gap-3 font-mono text-[0.65rem] text-muted-foreground">
          <span>{run.runId}</span>
          <span className="h-3 w-px bg-border" />
          <span>{run.status}</span>
          <span className="h-3 w-px bg-border" />
          <span>{run.manifest.slice(0, 12)}…</span>
        </div>
      </section>

      <AnimatePresence mode="wait">
        <motion.div
          key={runKey}
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: -7 }}
          transition={{ type: "spring", stiffness: 170, damping: 24 }}
          className="space-y-7"
        >
          <section className="grid gap-7 xl:grid-cols-[minmax(0,1.1fr)_minmax(20rem,0.9fr)]">
            <div className="rounded-[1.7rem] border border-border bg-card/42 p-5 panel-edge md:p-7">
              <div className="flex flex-wrap items-start justify-between gap-4">
                <div>
                  <p className="text-[0.66rem] font-medium tracking-[0.18em] text-muted-foreground uppercase">
                    Canonical projection
                  </p>
                  <h2 className="mt-2 text-xl font-semibold tracking-tight">
                    PCA · one component · two clusters
                  </h2>
                </div>
                <Badge
                  variant="outline"
                  className={cn(
                    "rounded-md font-mono text-[0.62rem]",
                    rejected
                      ? "border-destructive/25 bg-destructive/8 text-destructive"
                      : "border-primary/20 bg-primary/7 text-primary",
                  )}
                >
                  {rejected ? "rejected_unstable" : "completed"}
                </Badge>
              </div>
              <div className="mt-5 rounded-xl border border-border bg-background/34 p-3">
                <ScatterPlot rejected={rejected} />
              </div>
              <div className="mt-5 grid gap-4 border-t border-border pt-5 sm:grid-cols-3">
                <div>
                  <p className="font-mono text-xl">
                    {discoveryFixture.rows.discovery}
                  </p>
                  <p className="mt-1 text-xs text-muted-foreground">
                    discovery rows
                  </p>
                </div>
                <div>
                  <p className="font-mono text-xl">
                    {discoveryFixture.rows.development}
                  </p>
                  <p className="mt-1 text-xs text-muted-foreground">
                    development rows
                  </p>
                </div>
                <div>
                  <p className="font-mono text-xl">2</p>
                  <p className="mt-1 text-xs text-muted-foreground">
                    fixture assets
                  </p>
                </div>
              </div>
            </div>

            <div className="rounded-[1.7rem] border border-border bg-card/42 p-5 panel-edge md:p-7">
              <p className="text-[0.66rem] font-medium tracking-[0.18em] text-muted-foreground uppercase">
                Run evidence
              </p>
              <div className="mt-6 grid grid-cols-2 gap-5">
                <MetricBlock
                  label="Behaviours"
                  value={run.behaviours}
                  detail={rejected ? "definition not frozen" : "content-addressed"}
                />
                <MetricBlock
                  label="Motifs"
                  value={run.motifs}
                  detail="bounded per boundary"
                />
                <MetricBlock
                  label="Transitions"
                  value={run.transitions}
                  detail="dwell-compressed rows"
                />
                <MetricBlock
                  label="Holdout rows"
                  value="0"
                  detail="metadata only"
                />
              </div>

              <div className="mt-7 border-t border-border pt-5">
                <p className="text-xs font-medium">Frozen manifest identity</p>
                <p className="mt-2 break-all font-mono text-[0.62rem] leading-relaxed text-muted-foreground">
                  {run.manifest}
                </p>
              </div>
            </div>
          </section>

          {rejected ? (
            <section className="grid min-h-72 place-items-center rounded-[1.7rem] border border-dashed border-destructive/25 bg-destructive/[0.025] p-8 text-center">
              <div className="max-w-md">
                <FlaskIcon
                  size={34}
                  className="mx-auto text-destructive/75"
                />
                <h2 className="mt-4 text-xl font-semibold tracking-tight">
                  No behaviours were frozen.
                </h2>
                <p className="mt-3 text-sm leading-relaxed text-muted-foreground">
                  The stricter stability policy rejected this detector definition.
                  The run and its evidence remain retained so failed research is not
                  lost or silently re-explored.
                </p>
              </div>
            </section>
          ) : (
            <section className="grid gap-5 lg:grid-cols-2">
              {discoveryFixture.behaviours.map((behaviour, index) => (
                <motion.article
                  key={behaviour.id}
                  initial={{ opacity: 0, y: 12 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{
                    type: "spring",
                    stiffness: 150,
                    damping: 23,
                    delay: index * 0.08,
                  }}
                  className="rounded-[1.7rem] border border-border bg-card/42 p-5 panel-edge md:p-6"
                >
                  <div className="flex items-start justify-between gap-4">
                    <div>
                      <p className="font-mono text-[0.62rem] text-primary">
                        {behaviour.id}
                      </p>
                      <h2 className="mt-2 text-xl font-semibold tracking-tight">
                        {behaviour.name}
                      </h2>
                    </div>
                    <Badge
                      variant="outline"
                      className="rounded-md border-primary/20 bg-primary/7 font-mono text-[0.62rem] text-primary"
                    >
                      frozen
                    </Badge>
                  </div>

                  <div className="mt-6 grid grid-cols-2 gap-5 border-y border-border py-5">
                    <div>
                      <p className="text-[0.65rem] text-muted-foreground">
                        auction location
                      </p>
                      <p className="mt-1 font-mono text-xl">
                        {behaviour.centroid[0].toFixed(2)}
                      </p>
                    </div>
                    <div>
                      <p className="text-[0.65rem] text-muted-foreground">
                        volume change
                      </p>
                      <p className="mt-1 font-mono text-xl">
                        {behaviour.centroid[1].toFixed(3)}
                      </p>
                    </div>
                  </div>

                  <div className="mt-5 space-y-4">
                    <div>
                      <div className="flex justify-between text-xs">
                        <span className="text-muted-foreground">Seed ARI</span>
                        <span className="font-mono">
                          {behaviour.seedAri.toFixed(2)}
                        </span>
                      </div>
                      <Progress value={behaviour.seedAri * 100} className="mt-2 h-1.5" />
                    </div>
                    <div>
                      <div className="flex justify-between text-xs">
                        <span className="text-muted-foreground">
                          Parameter perturbation ARI
                        </span>
                        <span className="font-mono">
                          {behaviour.perturbationAri.toFixed(3)}
                        </span>
                      </div>
                      <Progress
                        value={behaviour.perturbationAri * 100}
                        className="mt-2 h-1.5"
                      />
                    </div>
                  </div>

                  <div className="mt-6 rounded-xl border border-border bg-background/35 p-4">
                    <div className="flex gap-2.5">
                      <BrainIcon size={17} className="mt-0.5 shrink-0 text-primary" />
                      <div>
                        <p className="text-xs font-medium">
                          Proposed Phase 5 test
                        </p>
                        <p className="mt-2 text-xs leading-relaxed text-muted-foreground">
                          {behaviour.hypothesis}
                        </p>
                      </div>
                    </div>
                  </div>

                  <p className="mt-4 text-[0.68rem] leading-relaxed text-muted-foreground">
                    {behaviour.caution}
                  </p>
                </motion.article>
              ))}
            </section>
          )}
        </motion.div>
      </AnimatePresence>

      <section className="relative overflow-hidden rounded-[1.7rem] border border-primary/18 bg-primary/[0.045] p-6 panel-edge md:p-8">
        <div className="absolute right-8 bottom-[-2rem] text-primary/[0.045]">
          <LockKeyIcon size={180} weight="fill" />
        </div>
        <div className="relative max-w-3xl">
          <div className="flex items-center gap-2 text-primary">
            <CheckCircleIcon size={18} weight="fill" />
            <span className="font-mono text-[0.66rem] tracking-[0.16em] uppercase">
              Phase 5 gate
            </span>
          </div>
          <h2 className="mt-4 text-2xl font-semibold tracking-[-0.035em]">
            Freeze the detector, split policy, horizons, metrics, and promotion
            criteria before outcomes are attached.
          </h2>
          <p className="mt-3 max-w-2xl text-sm leading-relaxed text-muted-foreground">
            Validation must use untouched chronological data, purging and embargo
            where labels overlap, serial-dependence-aware uncertainty,
            multiple-testing control, negative controls, and cost stress. Until
            then, these remain behaviours and candidate hypotheses only.
          </p>
        </div>
      </section>
    </div>
  )
}
