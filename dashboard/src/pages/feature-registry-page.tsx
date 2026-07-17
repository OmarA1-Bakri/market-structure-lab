import { useMemo, useState } from "react"
import {
  ArrowRightIcon,
  ClockCounterClockwiseIcon,
  MagnifyingGlassIcon,
  ShieldCheckIcon,
} from "@phosphor-icons/react"
import { motion } from "motion/react"

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { featureDefinitions } from "@/data/lab-data"
import { cn } from "@/lib/utils"

type FamilyFilter = "all" | "auction" | "sequence"

const flow = [
  {
    label: "Candle close",
    detail: "exclusive UTC cutoff",
  },
  {
    label: "Auction snapshot",
    detail: "deterministic state",
  },
  {
    label: "Feature builder",
    detail: "≤ 21 trailing rows",
  },
  {
    label: "Event evidence",
    detail: "half-open interval",
  },
  {
    label: "Discovery matrix",
    detail: "no future outcomes",
  },
]

export function FeatureRegistryPage() {
  const [query, setQuery] = useState("")
  const [family, setFamily] = useState<FamilyFilter>("all")

  const filtered = useMemo(() => {
    const normalized = query.trim().toLowerCase()
    return featureDefinitions.filter(
      (feature) =>
        (family === "all" || feature.family === family) &&
        (!normalized ||
          feature.name.toLowerCase().includes(normalized) ||
          feature.definition.toLowerCase().includes(normalized)),
    )
  }, [family, query])

  return (
    <div className="space-y-9">
      <section className="grid gap-7 border-b border-border pb-8 xl:grid-cols-[minmax(0,1.2fr)_minmax(20rem,0.8fr)]">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <Badge
              variant="outline"
              className="rounded-md border-primary/25 bg-primary/8 font-mono text-[0.65rem] text-primary"
            >
              FS-000001
            </Badge>
            <span className="font-mono text-[0.65rem] text-muted-foreground">
              26 causal feature definitions
            </span>
          </div>
          <h1 className="mt-5 text-4xl font-semibold tracking-[-0.05em] md:text-5xl">
            A feature contract that carries its information boundary.
          </h1>
          <p className="mt-4 max-w-2xl text-base leading-relaxed text-muted-foreground">
            Sixteen auction-informed features and ten minimally assumptive sequence
            features record their units, prior history, missing policy, version, and
            leakage classification before discovery sees them.
          </p>
        </div>

        <Alert className="border-primary/18 bg-primary/[0.045]">
          <ShieldCheckIcon className="text-primary" />
          <AlertTitle>Outcome-blind by construction</AlertTitle>
          <AlertDescription className="leading-relaxed text-muted-foreground">
            Future returns, MFE, MAE, target hits, profitability, and future
            volatility labels are excluded from feature and event publications used
            for discovery.
          </AlertDescription>
        </Alert>
      </section>

      <section className="rounded-[1.7rem] border border-border bg-card/42 p-5 panel-edge md:p-7">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
          <div>
            <p className="text-[0.66rem] font-medium tracking-[0.18em] text-muted-foreground uppercase">
              Causal handoff
            </p>
            <h2 className="mt-2 text-xl font-semibold tracking-tight">
              Every downstream row inherits the cutoff.
            </h2>
          </div>
          <p className="max-w-xl text-sm leading-relaxed text-muted-foreground">
            Segment, gap, session, and window resets clear trailing history. Missing
            values remain null; the builder never interpolates or forward-fills.
          </p>
        </div>

        <div className="mt-7 grid gap-3 md:grid-cols-5">
          {flow.map((item, index) => (
            <div key={item.label} className="relative">
              <motion.div
                initial={{ opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{
                  type: "spring",
                  stiffness: 150,
                  damping: 22,
                  delay: index * 0.06,
                }}
                className="h-full rounded-xl border border-border bg-background/38 p-4"
              >
                <span className="font-mono text-[0.62rem] text-primary">
                  0{index + 1}
                </span>
                <p className="mt-5 text-sm font-medium">{item.label}</p>
                <p className="mt-1 font-mono text-[0.62rem] text-muted-foreground">
                  {item.detail}
                </p>
              </motion.div>
              {index < flow.length - 1 ? (
                <ArrowRightIcon
                  size={14}
                  className="absolute top-1/2 -right-2.5 z-10 hidden -translate-y-1/2 rounded-full bg-card text-muted-foreground md:block"
                />
              ) : null}
            </div>
          ))}
        </div>
      </section>

      <section className="overflow-hidden rounded-[1.7rem] border border-border bg-card/42 panel-edge">
        <div className="flex flex-col gap-4 border-b border-border p-4 md:flex-row md:items-center md:justify-between md:p-5">
          <div>
            <h2 className="text-lg font-semibold tracking-tight">Registry browser</h2>
            <p className="mt-1 text-xs text-muted-foreground">
              {filtered.length} registered definitions
            </p>
          </div>
          <div className="flex w-full flex-col gap-3 sm:flex-row md:w-auto">
            <div className="relative min-w-0 sm:w-72">
              <MagnifyingGlassIcon
                size={15}
                className="pointer-events-none absolute top-1/2 left-3 -translate-y-1/2 text-muted-foreground"
              />
              <Input
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="Search definitions"
                aria-label="Search feature definitions"
                className="h-9 bg-background/50 pl-9 font-mono text-xs"
              />
            </div>
            <div className="flex gap-1 rounded-xl border border-border bg-background/45 p-1">
              {(["all", "auction", "sequence"] as const).map((item) => (
                <Button
                  key={item}
                  type="button"
                  variant="ghost"
                  size="sm"
                  onClick={() => setFamily(item)}
                  className={cn(
                    "rounded-lg px-3 text-xs capitalize",
                    family === item && "bg-primary/9 text-primary hover:bg-primary/12",
                  )}
                >
                  {item}
                </Button>
              ))}
            </div>
          </div>
        </div>

        {filtered.length ? (
          <div className="max-h-[40rem] overflow-auto">
            <Table>
              <TableHeader className="sticky top-0 z-10 bg-card/96 backdrop-blur">
                <TableRow>
                  <TableHead className="min-w-56">Feature</TableHead>
                  <TableHead>Family</TableHead>
                  <TableHead>History</TableHead>
                  <TableHead>Leakage class</TableHead>
                  <TableHead className="min-w-80">Definition</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {filtered.map((feature) => (
                  <motion.tr
                    layout
                    key={feature.name}
                    className="border-b border-border align-top transition-colors hover:bg-muted/25"
                  >
                    <TableCell>
                      <p className="font-mono text-xs font-medium text-foreground">
                        {feature.name}
                      </p>
                      <p className="mt-1 font-mono text-[0.62rem] text-muted-foreground">
                        {feature.units}
                      </p>
                    </TableCell>
                    <TableCell>
                      <Badge
                        variant="outline"
                        className={cn(
                          "rounded-md font-mono text-[0.62rem]",
                          feature.family === "auction"
                            ? "border-primary/20 bg-primary/7 text-primary"
                            : "border-border bg-muted/45 text-muted-foreground",
                        )}
                      >
                        {feature.family}
                      </Badge>
                    </TableCell>
                    <TableCell>
                      <span className="inline-flex items-center gap-1.5 font-mono text-xs text-foreground/75">
                        <ClockCounterClockwiseIcon size={13} />
                        {feature.prior}
                      </span>
                    </TableCell>
                    <TableCell>
                      <span className="font-mono text-[0.65rem] text-muted-foreground">
                        {feature.leakage}
                      </span>
                    </TableCell>
                    <TableCell className="text-xs leading-relaxed text-muted-foreground">
                      {feature.definition}
                    </TableCell>
                  </motion.tr>
                ))}
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
              <p className="mt-3 text-sm font-medium">No definitions match</p>
              <p className="mt-1 text-xs text-muted-foreground">
                Try another family or a broader search.
              </p>
            </div>
          </div>
        )}
      </section>
    </div>
  )
}
