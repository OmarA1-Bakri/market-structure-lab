import { useRef, useState } from "react"
import type { ChangeEvent, DragEvent } from "react"
import {
  BracketsCurlyIcon,
  CheckCircleIcon,
  FileCodeIcon,
  ShieldWarningIcon,
  UploadSimpleIcon,
  XCircleIcon,
} from "@phosphor-icons/react"
import { AnimatePresence, motion } from "motion/react"

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Skeleton } from "@/components/ui/skeleton"
import { cn } from "@/lib/utils"

interface ArtifactMetric {
  label: string
  value: string
}

interface ArtifactSummary {
  kind: string
  title: string
  detail: string
  metrics: ArtifactMetric[]
  caution?: string
}

interface LoadedArtifact {
  filename: string
  bytes: number
  sha256: string
  pretty: string
  summary: ArtifactSummary
}

function record(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null
}

function numberValue(value: unknown) {
  return typeof value === "number" && Number.isFinite(value) ? value : null
}

function stringValue(value: unknown) {
  return typeof value === "string" ? value : null
}

function arrayValue(value: unknown) {
  return Array.isArray(value) ? value : null
}

function formatted(value: unknown) {
  const number = numberValue(value)
  if (number !== null) return new Intl.NumberFormat("en").format(number)
  return stringValue(value) ?? "—"
}

function summarizeArtifact(parsed: unknown): ArtifactSummary {
  const root = record(parsed)
  if (!root) {
    return {
      kind: "JSON document",
      title: "Unstructured JSON value",
      detail: "The file parsed successfully but does not use an object envelope.",
      metrics: [],
    }
  }

  const report = record(root.report)
  if (report && arrayValue(report.symbols)) {
    return {
      kind: "Freshness report",
      title: `Candle freshness · ${formatted(report.as_of)}`,
      detail:
        "Terminal per-symbol coverage evidence from a frozen freshness plan.",
      metrics: [
        { label: "Recovered minutes", value: formatted(report.recovered_minutes) },
        { label: "Remaining minutes", value: formatted(report.after_missing_minutes) },
        { label: "Inserted rows", value: formatted(report.inserted_rows) },
        {
          label: "Symbols",
          value: String(arrayValue(report.symbols)?.length ?? 0),
        },
      ],
      caution:
        "Live freshness is data maintenance. It does not imply live trading or a validated edge.",
    }
  }

  const identity = record(root.identity)
  const partitions = arrayValue(root.partitions)
  if (identity && partitions) {
    return {
      kind: "Snapshot manifest",
      title: formatted(identity.dataset_version),
      detail:
        "Immutable, checksum-manifested canonical dataset publication.",
      metrics: [
        { label: "Rows", value: formatted(root.row_count) },
        { label: "Partitions", value: String(partitions.length) },
        { label: "Minimum UTC", value: formatted(root.min_timestamp) },
        { label: "Maximum UTC", value: formatted(root.max_timestamp) },
      ],
    }
  }

  const correctness = record(root.correctness)
  const workload = record(root.workload)
  if (correctness && workload) {
    const results = record(root.results)
    const summary = record(results?.summary)
    return {
      kind: "Auction benchmark",
      title: `${formatted(workload.symbol)} · ${formatted(workload.count)} candles`,
      detail:
        "Observed deterministic replay benchmark with separate correctness evidence.",
      metrics: [
        {
          label: "Throughput / second",
          value: formatted(summary?.throughput_candles_per_second_median),
        },
        {
          label: "Peak traced MiB",
          value: formatted(summary?.peak_tracemalloc_mebibytes),
        },
        {
          label: "Active window",
          value: formatted(summary?.active_window_max),
        },
        {
          label: "Fixture seed",
          value: formatted(workload.seed),
        },
      ],
      caution:
        "Observed timing is not an acceptance threshold; canonical hashes and equivalence checks prove correctness.",
    }
  }

  if (numberValue(root.candidate_count) !== null && arrayValue(root.tables)) {
    return {
      kind: "Database inspection",
      title: `${formatted(root.database)} · ${formatted(root.mode)} inspection`,
      detail:
        "Read-only schema and source-quality inspection. Artifact timestamps may predate current supplement totals.",
      metrics: [
        { label: "Tables", value: formatted(root.table_count) },
        { label: "OHLCV candidates", value: formatted(root.candidate_count) },
        {
          label: "Schemas",
          value: String(arrayValue(root.schemas)?.length ?? 0),
        },
        { label: "Database", value: formatted(root.database) },
      ],
      caution:
        "Connection strings must remain redacted. Never expose database credentials to a deployed browser.",
    }
  }

  const runs = record(root.runs)
  if (runs && arrayValue(root.discovery_rows) && arrayValue(root.development_rows)) {
    const stable = record(runs.stable)
    const expected = record(stable?.expected)
    return {
      kind: "Discovery fixture",
      title: formatted(stable?.run_id),
      detail:
        "Committed synthetic fixture contract; not real-market experiment evidence.",
      metrics: [
        {
          label: "Discovery rows",
          value: String(arrayValue(root.discovery_rows)?.length ?? 0),
        },
        {
          label: "Development rows",
          value: String(arrayValue(root.development_rows)?.length ?? 0),
        },
        {
          label: "Behaviours",
          value: String(arrayValue(expected?.behaviour_ids)?.length ?? 0),
        },
        {
          label: "Features",
          value: String(arrayValue(root.feature_names)?.length ?? 0),
        },
      ],
      caution:
        "Golden fixture output is not empirical market evidence, validation, or profitability.",
    }
  }

  return {
    kind: "JSON artifact",
    title: stringValue(root.schema_version) ?? "Recognized JSON object",
    detail:
      "The document parsed successfully. No Market Structure Lab schema was recognized.",
    metrics: [
      { label: "Top-level fields", value: String(Object.keys(root).length) },
    ],
  }
}

async function sha256(bytes: ArrayBuffer) {
  const digest = await crypto.subtle.digest("SHA-256", bytes)
  return Array.from(new Uint8Array(digest))
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("")
}

export function ArtifactInspectorPage() {
  const inputRef = useRef<HTMLInputElement>(null)
  const [dragging, setDragging] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [artifact, setArtifact] = useState<LoadedArtifact | null>(null)

  const loadFile = async (file: File | undefined) => {
    if (!file) return
    if (!file.name.toLowerCase().endsWith(".json")) {
      setArtifact(null)
      setError("Choose a JSON artifact. Parquet and dump files are not read in the browser.")
      return
    }
    if (file.size > 8 * 1024 * 1024) {
      setArtifact(null)
      setError("The inspector accepts JSON files up to 8 MiB.")
      return
    }

    setLoading(true)
    setError(null)
    try {
      const bytes = await file.arrayBuffer()
      const text = new TextDecoder().decode(bytes)
      const parsed: unknown = JSON.parse(text)
      const pretty = JSON.stringify(parsed, null, 2)
      setArtifact({
        filename: file.name,
        bytes: file.size,
        sha256: await sha256(bytes),
        pretty,
        summary: summarizeArtifact(parsed),
      })
    } catch (caught) {
      setArtifact(null)
      setError(
        caught instanceof Error
          ? `The artifact could not be read: ${caught.message}`
          : "The artifact could not be read.",
      )
    } finally {
      setLoading(false)
      if (inputRef.current) inputRef.current.value = ""
    }
  }

  const onChange = (event: ChangeEvent<HTMLInputElement>) => {
    void loadFile(event.target.files?.[0])
  }

  const onDrop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault()
    setDragging(false)
    void loadFile(event.dataTransfer.files?.[0])
  }

  return (
    <div className="space-y-9">
      <section className="grid gap-7 border-b border-border pb-8 xl:grid-cols-[minmax(0,1.2fr)_minmax(20rem,0.8fr)]">
        <div>
          <Badge
            variant="outline"
            className="rounded-md border-primary/25 bg-primary/8 font-mono text-[0.65rem] text-primary"
          >
            LOCAL · READ ONLY
          </Badge>
          <h1 className="mt-5 text-4xl font-semibold tracking-[-0.05em] md:text-5xl">
            Inspect evidence without sending it anywhere.
          </h1>
          <p className="mt-4 max-w-2xl text-base leading-relaxed text-muted-foreground">
            Open a Market Structure Lab JSON manifest, report, benchmark, inspection,
            or discovery fixture. Parsing and SHA-256 calculation happen in your
            browser; the file is not uploaded.
          </p>
        </div>

        <Alert className="border-border bg-card/40">
          <ShieldWarningIcon className="text-primary" />
          <AlertTitle>Deployment-safe boundary</AlertTitle>
          <AlertDescription className="leading-relaxed text-muted-foreground">
            A deployed dashboard cannot read gitignored local exports or connect
            directly to PostgreSQL. This inspector is the safe bridge until an
            authenticated artifact API is deliberately approved.
          </AlertDescription>
        </Alert>
      </section>

      <section
        onDragEnter={(event) => {
          event.preventDefault()
          setDragging(true)
        }}
        onDragOver={(event) => event.preventDefault()}
        onDragLeave={(event) => {
          if (event.currentTarget === event.target) setDragging(false)
        }}
        onDrop={onDrop}
        className={cn(
          "relative grid min-h-64 place-items-center overflow-hidden rounded-[1.8rem] border border-dashed p-7 text-center transition-colors",
          dragging
            ? "border-primary/65 bg-primary/[0.075]"
            : "border-border bg-card/32 hover:border-primary/30",
        )}
      >
        <div className="absolute inset-0 hairline-grid opacity-35" />
        <div className="relative max-w-lg">
          <div className="mx-auto grid size-14 place-items-center rounded-2xl border border-primary/20 bg-primary/8 text-primary panel-edge">
            <UploadSimpleIcon size={25} />
          </div>
          <h2 className="mt-5 text-xl font-semibold tracking-tight">
            Drop a JSON artifact here
          </h2>
          <p className="mt-2 text-sm leading-relaxed text-muted-foreground">
            Freshness report, snapshot manifest, database inspection, auction
            benchmark, or Phase 4 fixture. Maximum file size: 8 MiB.
          </p>
          <Button
            type="button"
            className="mt-5"
            onClick={() => inputRef.current?.click()}
          >
            Choose JSON file
          </Button>
          <input
            ref={inputRef}
            type="file"
            accept="application/json,.json"
            onChange={onChange}
            className="sr-only"
          />
        </div>
      </section>

      <AnimatePresence mode="wait">
        {loading ? (
          <motion.section
            key="loading"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="grid gap-6 rounded-[1.7rem] border border-border bg-card/42 p-5 panel-edge lg:grid-cols-[minmax(0,0.7fr)_minmax(0,1.3fr)] md:p-7"
          >
            <div className="space-y-4">
              <Skeleton className="h-5 w-28" />
              <Skeleton className="h-9 w-2/3" />
              <Skeleton className="h-16 w-full" />
              <div className="grid grid-cols-2 gap-3">
                <Skeleton className="h-20" />
                <Skeleton className="h-20" />
              </div>
            </div>
            <Skeleton className="min-h-72" />
          </motion.section>
        ) : null}

        {!loading && error ? (
          <motion.section
            key="error"
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0 }}
          >
            <Alert className="border-destructive/25 bg-destructive/[0.045]">
              <XCircleIcon className="text-destructive" />
              <AlertTitle>Artifact rejected</AlertTitle>
              <AlertDescription className="text-muted-foreground">
                {error}
              </AlertDescription>
            </Alert>
          </motion.section>
        ) : null}

        {!loading && artifact ? (
          <motion.section
            key={artifact.sha256}
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -7 }}
            transition={{ type: "spring", stiffness: 160, damping: 23 }}
            className="grid gap-6 lg:grid-cols-[minmax(0,0.72fr)_minmax(0,1.28fr)]"
          >
            <div className="space-y-5">
              <div className="rounded-[1.7rem] border border-border bg-card/42 p-5 panel-edge md:p-6">
                <div className="flex items-start justify-between gap-4">
                  <div>
                    <p className="font-mono text-[0.64rem] text-primary">
                      {artifact.summary.kind}
                    </p>
                    <h2 className="mt-2 text-xl font-semibold tracking-tight">
                      {artifact.summary.title}
                    </h2>
                  </div>
                  <CheckCircleIcon size={22} weight="fill" className="text-primary" />
                </div>
                <p className="mt-3 text-sm leading-relaxed text-muted-foreground">
                  {artifact.summary.detail}
                </p>
                <div className="mt-6 grid grid-cols-2 gap-4 border-t border-border pt-5">
                  {artifact.summary.metrics.map((metric) => (
                    <div key={metric.label}>
                      <p className="font-mono text-lg tracking-[-0.04em]">
                        {metric.value}
                      </p>
                      <p className="mt-1 text-[0.65rem] text-muted-foreground">
                        {metric.label}
                      </p>
                    </div>
                  ))}
                </div>
                {artifact.summary.caution ? (
                  <p className="mt-5 border-t border-border pt-4 text-[0.68rem] leading-relaxed text-muted-foreground">
                    {artifact.summary.caution}
                  </p>
                ) : null}
              </div>

              <div className="rounded-[1.7rem] border border-border bg-card/42 p-5 panel-edge">
                <div className="flex items-center gap-2">
                  <BracketsCurlyIcon size={17} className="text-primary" />
                  <p className="truncate text-sm font-medium">{artifact.filename}</p>
                </div>
                <dl className="mt-4 space-y-3 border-t border-border pt-4 text-xs">
                  <div className="flex justify-between gap-4">
                    <dt className="text-muted-foreground">Bytes</dt>
                    <dd className="font-mono">
                      {new Intl.NumberFormat("en").format(artifact.bytes)}
                    </dd>
                  </div>
                  <div>
                    <dt className="text-muted-foreground">Computed SHA-256</dt>
                    <dd className="mt-2 break-all font-mono text-[0.61rem] leading-relaxed">
                      {artifact.sha256}
                    </dd>
                  </div>
                </dl>
              </div>
            </div>

            <div className="min-w-0 overflow-hidden rounded-[1.7rem] border border-border bg-[oklch(0.115_0.006_150)] panel-edge">
              <div className="flex items-center justify-between border-b border-border px-5 py-4">
                <div className="flex items-center gap-2">
                  <FileCodeIcon size={17} className="text-primary" />
                  <span className="text-sm font-medium">Canonical JSON view</span>
                </div>
                <span className="font-mono text-[0.62rem] text-muted-foreground">
                  parsed locally
                </span>
              </div>
              <ScrollArea className="h-[38rem]">
                <pre className="min-w-max p-5 font-mono text-[0.68rem] leading-relaxed text-foreground/75">
                  {artifact.pretty}
                </pre>
              </ScrollArea>
            </div>
          </motion.section>
        ) : null}
      </AnimatePresence>

      {!loading && !artifact && !error ? (
        <section className="border-t border-border pt-8 text-center">
          <FileCodeIcon size={26} className="mx-auto text-muted-foreground" />
          <p className="mt-3 text-sm font-medium">No artifact loaded</p>
          <p className="mt-1 text-xs text-muted-foreground">
            The summary, computed hash, and canonical JSON view will appear here.
          </p>
        </section>
      ) : null}
    </div>
  )
}
