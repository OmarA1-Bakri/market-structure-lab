import { useEffect, useMemo, useState } from "react"
import {
  ArrowCounterClockwiseIcon,
  CheckCircleIcon,
  PauseIcon,
  PlayIcon,
} from "@phosphor-icons/react"
import { AnimatePresence, motion, useReducedMotion } from "motion/react"

import { MetricBlock } from "@/components/metric-block"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Slider } from "@/components/ui/slider"
import {
  auctionFrames,
  verification,
} from "@/data/lab-data"
import { cn } from "@/lib/utils"

const chartWidth = 640
const chartHeight = 280
const padding = { top: 24, right: 26, bottom: 34, left: 42 }

function priceY(value: number) {
  const min = 99.5
  const max = 103.5
  return (
    padding.top +
    ((max - value) / (max - min)) *
      (chartHeight - padding.top - padding.bottom)
  )
}

function priceX(index: number) {
  return (
    padding.left +
    (index / (auctionFrames.length - 1)) *
      (chartWidth - padding.left - padding.right)
  )
}

function ReplayChart({ frameIndex }: { frameIndex: number }) {
  const reduceMotion = useReducedMotion()
  const visible = auctionFrames.slice(0, frameIndex + 1)
  const pricePath = visible
    .map(
      (frame, index) =>
        `${index === 0 ? "M" : "L"} ${priceX(index)} ${priceY(frame.price)}`,
    )
    .join(" ")

  return (
    <svg
      viewBox={`0 0 ${chartWidth} ${chartHeight}`}
      role="img"
      aria-label="Deterministic rolling three-bar auction replay chart"
      className="h-auto w-full"
    >
      {[100, 101, 102, 103].map((value) => (
        <g key={value}>
          <line
            x1={padding.left}
            y1={priceY(value)}
            x2={chartWidth - padding.right}
            y2={priceY(value)}
            stroke="currentColor"
            className="text-border"
            strokeDasharray="3 7"
          />
          <text
            x={padding.left - 10}
            y={priceY(value) + 3}
            textAnchor="end"
            className="fill-muted-foreground font-mono text-[9px]"
          >
            {value}
          </text>
        </g>
      ))}

      {auctionFrames.map((frame, index) => (
        <text
          key={frame.minute}
          x={priceX(index)}
          y={chartHeight - 12}
          textAnchor="middle"
          className="fill-muted-foreground font-mono text-[9px]"
        >
          00:0{frame.minute}
        </text>
      ))}

      <motion.path
        d={pricePath}
        fill="none"
        stroke="currentColor"
        strokeWidth="2.2"
        strokeLinecap="round"
        strokeLinejoin="round"
        className="text-primary"
        initial={reduceMotion ? false : { pathLength: 0, opacity: 0 }}
        animate={{ pathLength: 1, opacity: 1 }}
        transition={{ type: "spring", stiffness: 90, damping: 20 }}
      />

      {visible.map((frame, index) => (
        <g key={frame.minute}>
          <motion.line
            x1={priceX(index)}
            x2={priceX(index)}
            y1={priceY(frame.vah)}
            y2={priceY(frame.val)}
            stroke="currentColor"
            strokeWidth="8"
            strokeLinecap="round"
            className="text-foreground/12"
            initial={reduceMotion ? false : { scaleY: 0 }}
            animate={{ scaleY: 1 }}
            style={{ transformOrigin: `${priceX(index)}px ${priceY(frame.val)}px` }}
          />
          <motion.circle
            cx={priceX(index)}
            cy={priceY(frame.price)}
            r={index === frameIndex ? 6 : 3.5}
            className={index === frameIndex ? "fill-primary" : "fill-foreground/65"}
            initial={reduceMotion ? false : { scale: 0 }}
            animate={{ scale: 1 }}
            transition={{ type: "spring", stiffness: 220, damping: 18 }}
          />
          <motion.circle
            cx={priceX(index)}
            cy={priceY(frame.vwap)}
            r="2.5"
            className="fill-background stroke-foreground/70"
            strokeWidth="1.5"
            initial={reduceMotion ? false : { opacity: 0 }}
            animate={{ opacity: 1 }}
          />
        </g>
      ))}

      <g transform={`translate(${chartWidth - 190} 18)`}>
        <circle cx="0" cy="0" r="3" className="fill-primary" />
        <text x="10" y="3" className="fill-muted-foreground font-mono text-[9px]">
          close
        </text>
        <circle cx="63" cy="0" r="2.5" className="fill-background stroke-foreground/70" />
        <text x="73" y="3" className="fill-muted-foreground font-mono text-[9px]">
          VWAP
        </text>
      </g>
    </svg>
  )
}

function VolumeProfile({
  frameIndex,
}: {
  frameIndex: number
}) {
  const frame = auctionFrames[frameIndex]
  const max = Math.max(...frame.bins.map((item) => item.volume), 1)
  return (
    <div className="space-y-3">
      {[103, 102, 101, 100].map((index) => {
        const bin = frame.bins.find((item) => item.index === index)
        const volume = bin?.volume ?? 0
        return (
          <div key={index} className="grid grid-cols-[2rem_1fr_3rem] items-center gap-3">
            <span className="text-right font-mono text-[0.68rem] text-muted-foreground">
              {index}
            </span>
            <div className="h-6 overflow-hidden rounded-md bg-muted/55">
              <motion.div
                layout
                animate={{ scaleX: volume / max }}
                transition={{ type: "spring", stiffness: 150, damping: 24 }}
                className={cn(
                  "h-full origin-left",
                  index === frame.poc ? "bg-primary" : "bg-foreground/18",
                )}
                style={{ willChange: "transform" }}
              />
            </div>
            <span className="font-mono text-[0.68rem] text-foreground/75">
              {volume}
            </span>
          </div>
        )
      })}
    </div>
  )
}

export function AuctionReplayPage() {
  const [frameIndex, setFrameIndex] = useState(0)
  const [playing, setPlaying] = useState(false)

  useEffect(() => {
    if (!playing) return
    const interval = window.setInterval(() => {
      setFrameIndex((current) => {
        if (current >= auctionFrames.length - 1) {
          setPlaying(false)
          return current
        }
        return current + 1
      })
    }, 1100)
    return () => window.clearInterval(interval)
  }, [playing])

  const frame = auctionFrames[frameIndex]
  const activeLabel = useMemo(
    () => frame.active.map((minute) => `00:0${minute}`).join(" · "),
    [frame.active],
  )

  const reset = () => {
    setPlaying(false)
    setFrameIndex(0)
  }

  return (
    <div className="space-y-9">
      <section className="grid gap-7 border-b border-border pb-8 xl:grid-cols-[minmax(0,1.25fr)_minmax(18rem,0.75fr)]">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <Badge
              variant="outline"
              className="rounded-md border-primary/25 bg-primary/8 font-mono text-[0.65rem] text-primary"
            >
              GOLDEN FIXTURE
            </Badge>
            <span className="font-mono text-[0.65rem] text-muted-foreground">
              auction-rolling-3-v1
            </span>
          </div>
          <h1 className="mt-5 text-4xl font-semibold tracking-[-0.05em] md:text-5xl">
            Replay the deterministic auction boundary.
          </h1>
          <p className="mt-4 max-w-2xl text-base leading-relaxed text-muted-foreground">
            Four exact fixture candles enter a three-bar rolling window. Volume is
            held under integer bin indices, the oldest contribution is removed, and
            every snapshot is pinned by a canonical hash.
          </p>
        </div>

        <Alert className="border-border bg-card/40">
          <CheckCircleIcon className="text-primary" />
          <AlertTitle>Software verification fixture</AlertTitle>
          <AlertDescription className="leading-relaxed text-muted-foreground">
            This replay proves deterministic engine behaviour. It is not a market
            signal, empirical behaviour, validated edge, or strategy.
          </AlertDescription>
        </Alert>
      </section>

      <section className="grid gap-7 xl:grid-cols-[minmax(0,1.45fr)_minmax(20rem,0.55fr)]">
        <div className="overflow-hidden rounded-[1.7rem] border border-border bg-card/42 panel-edge">
          <div className="flex flex-wrap items-center justify-between gap-4 border-b border-border px-5 py-4">
            <div>
              <p className="text-sm font-semibold">BTCUSDT · 1m · rolling 3 bars</p>
              <p className="mt-1 font-mono text-[0.62rem] text-muted-foreground">
                fixed step 1.0 · uniform touched-bin allocation · value area 0.70
              </p>
            </div>
            <div className="flex items-center gap-2">
              <Button
                type="button"
                variant="outline"
                size="icon"
                onClick={reset}
                aria-label="Reset replay"
              >
                <ArrowCounterClockwiseIcon size={16} />
              </Button>
              <Button
                type="button"
                onClick={() => {
                  if (frameIndex === auctionFrames.length - 1) setFrameIndex(0)
                  setPlaying((current) => !current)
                }}
                className="min-w-24"
              >
                {playing ? <PauseIcon size={15} /> : <PlayIcon size={15} />}
                {playing ? "Pause" : "Replay"}
              </Button>
            </div>
          </div>

          <div className="p-3 md:p-5">
            <ReplayChart frameIndex={frameIndex} />
          </div>

          <div className="border-t border-border px-5 py-5">
            <div className="flex items-center gap-4">
              <span className="w-11 font-mono text-xs text-muted-foreground">
                {frameIndex + 1}/4
              </span>
              <Slider
                min={0}
                max={auctionFrames.length - 1}
                step={1}
                value={[frameIndex]}
                onValueChange={(value) => {
                  setPlaying(false)
                  setFrameIndex(value[0] ?? 0)
                }}
                aria-label="Replay snapshot"
              />
            </div>
          </div>
        </div>

        <div className="space-y-5">
          <div className="rounded-[1.7rem] border border-border bg-card/42 p-5 panel-edge">
            <div className="flex items-start justify-between gap-4">
              <div>
                <p className="text-[0.66rem] font-medium tracking-[0.18em] text-muted-foreground uppercase">
                  Snapshot 0{frameIndex + 1}
                </p>
                <AnimatePresence mode="wait">
                  <motion.p
                    key={frame.location}
                    initial={{ opacity: 0, y: 5 }}
                    animate={{ opacity: 1, y: 0 }}
                    exit={{ opacity: 0, y: -5 }}
                    className="mt-2 text-xl font-semibold tracking-tight"
                  >
                    {frame.location.replaceAll("_", " ")}
                  </motion.p>
                </AnimatePresence>
              </div>
              <span className="rounded-lg border border-primary/20 bg-primary/8 px-2.5 py-1 font-mono text-xs text-primary">
                00:0{frame.minute}
              </span>
            </div>

            <dl className="mt-6 grid grid-cols-2 gap-x-5 gap-y-4 border-t border-border pt-5 text-xs">
              {[
                ["Close", frame.price.toFixed(2)],
                ["VWAP", frame.vwap.toFixed(4)],
                ["POC index", frame.poc],
                ["Value area", `${frame.val} — ${frame.vah}`],
                ["Candle volume", frame.volume],
                ["Active bars", frame.active.length],
              ].map(([label, value]) => (
                <div key={String(label)}>
                  <dt className="text-muted-foreground">{label}</dt>
                  <dd className="mt-1 font-mono text-sm">{value}</dd>
                </div>
              ))}
            </dl>
            <p className="mt-5 border-t border-border pt-4 font-mono text-[0.62rem] text-muted-foreground">
              active minutes · {activeLabel}
            </p>
          </div>

          <div className="rounded-[1.7rem] border border-border bg-card/42 p-5 panel-edge">
            <p className="text-[0.66rem] font-medium tracking-[0.18em] text-muted-foreground uppercase">
              Integer-bin profile
            </p>
            <div className="mt-5">
              <VolumeProfile frameIndex={frameIndex} />
            </div>
            <div className="mt-5 flex items-center gap-5 border-t border-border pt-4 font-mono text-[0.62rem] text-muted-foreground">
              <span className="flex items-center gap-1.5">
                <span className="size-2 rounded-sm bg-primary" /> POC
              </span>
              <span className="flex items-center gap-1.5">
                <span className="size-2 rounded-sm bg-foreground/18" /> other bins
              </span>
            </div>
          </div>
        </div>
      </section>

      <section className="grid gap-5 border-t border-border pt-8 sm:grid-cols-2 xl:grid-cols-4">
        <MetricBlock
          label="Benchmark candles"
          value={verification.benchmarkCandles.toLocaleString()}
          detail="Deterministic synthetic workload"
        />
        <MetricBlock
          label="Observed throughput"
          value={verification.benchmarkThroughput.toFixed(2)}
          detail="Candles per second; not an acceptance threshold"
        />
        <MetricBlock
          label="Peak traced memory"
          value={`${verification.peakMemoryMib.toFixed(3)} MiB`}
          detail="Separate 5,000-candle measurement pass"
        />
        <MetricBlock
          label="Equivalence checks"
          value={verification.equivalenceChecks}
          detail={`Incremental vs full recomputation at ${verification.absoluteTolerance.toExponential()}`}
        />
      </section>
    </div>
  )
}
