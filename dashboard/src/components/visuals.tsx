import { motion, useReducedMotion } from "motion/react"

import { cn } from "@/lib/utils"

export function CoverageArc({
  recovered,
  remaining,
}: {
  recovered: number
  remaining: number
}) {
  const reduceMotion = useReducedMotion()
  const total = recovered + remaining
  const share = total === 0 ? 0 : recovered / total
  const radius = 72
  const circumference = 2 * Math.PI * radius
  const dash = circumference * share

  return (
    <div className="relative size-44">
      <svg viewBox="0 0 180 180" className="-rotate-90">
        <circle
          cx="90"
          cy="90"
          r={radius}
          fill="none"
          stroke="currentColor"
          strokeWidth="10"
          className="text-muted"
        />
        <motion.circle
          cx="90"
          cy="90"
          r={radius}
          fill="none"
          stroke="currentColor"
          strokeWidth="10"
          strokeLinecap="round"
          className="text-primary"
          initial={reduceMotion ? false : { strokeDasharray: `0 ${circumference}` }}
          animate={{ strokeDasharray: `${dash} ${circumference - dash}` }}
          transition={{ type: "spring", stiffness: 70, damping: 18 }}
        />
      </svg>
      <div className="absolute inset-0 grid place-items-center text-center">
        <div>
          <p className="font-mono text-3xl tracking-[-0.06em]">
            {(share * 100).toFixed(1)}%
          </p>
          <p className="mt-1 text-[0.62rem] tracking-[0.16em] text-muted-foreground uppercase">
            recovered
          </p>
        </div>
      </div>
    </div>
  )
}

export function MiniBars({
  values,
  active = values.length - 1,
  className,
}: {
  values: number[]
  active?: number
  className?: string
}) {
  const max = Math.max(...values, 1)
  return (
    <div className={cn("flex h-16 items-end gap-1", className)}>
      {values.map((value, index) => (
        <motion.span
          key={`${index}-${value}`}
          initial={{ scaleY: 0 }}
          animate={{ scaleY: 1 }}
          transition={{
            type: "spring",
            stiffness: 130,
            damping: 20,
            delay: index * 0.025,
          }}
          className={cn(
            "block min-w-1 flex-1 origin-bottom rounded-sm",
            index === active ? "bg-primary" : "bg-foreground/12",
          )}
          style={{ height: `${Math.max(8, (value / max) * 100)}%` }}
        />
      ))}
    </div>
  )
}

export function ScatterPlot({ rejected = false }: { rejected?: boolean }) {
  const reduceMotion = useReducedMotion()
  const left = [
    [36, 76],
    [48, 66],
    [58, 82],
    [68, 61],
    [79, 72],
    [88, 55],
    [98, 69],
    [108, 48],
  ]
  const right = [
    [202, 152],
    [212, 137],
    [225, 160],
    [236, 131],
    [246, 148],
    [257, 122],
    [269, 142],
    [280, 111],
  ]

  return (
    <svg
      viewBox="0 0 320 210"
      role="img"
      aria-label="Canonical PCA fixture scatter plot"
      className="h-auto w-full overflow-visible"
    >
      {[40, 80, 120, 160].map((y) => (
        <line
          key={y}
          x1="18"
          y1={y}
          x2="302"
          y2={y}
          stroke="currentColor"
          className="text-border"
          strokeDasharray="2 5"
        />
      ))}
      <line x1="18" y1="180" x2="302" y2="180" stroke="currentColor" className="text-border" />
      <line x1="18" y1="20" x2="18" y2="180" stroke="currentColor" className="text-border" />
      {[...left, ...right].map(([x, y], index) => (
        <motion.circle
          key={`${x}-${y}`}
          cx={x}
          cy={y}
          r={rejected ? 4 : index < left.length ? 4.5 : 5.5}
          className={
            rejected
              ? "fill-muted-foreground/45"
              : index < left.length
                ? "fill-foreground/55"
                : "fill-primary"
          }
          initial={reduceMotion ? false : { opacity: 0, scale: 0 }}
          animate={{ opacity: 1, scale: 1 }}
          transition={{
            type: "spring",
            stiffness: 180,
            damping: 18,
            delay: index * 0.03,
          }}
        />
      ))}
      <text x="160" y="204" textAnchor="middle" className="fill-muted-foreground font-mono text-[9px]">
        canonical PC1
      </text>
    </svg>
  )
}
