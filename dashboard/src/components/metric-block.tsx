import type { ReactNode } from "react"

import { cn } from "@/lib/utils"

interface MetricBlockProps {
  label: string
  value: ReactNode
  detail?: ReactNode
  className?: string
}

export function MetricBlock({
  label,
  value,
  detail,
  className,
}: MetricBlockProps) {
  return (
    <div className={cn("min-w-0 border-t border-border pt-4", className)}>
      <p className="text-[0.68rem] font-medium tracking-[0.18em] text-muted-foreground uppercase">
        {label}
      </p>
      <div className="mt-2 font-mono text-2xl leading-none tracking-[-0.04em] text-foreground md:text-3xl">
        {value}
      </div>
      {detail ? (
        <div className="mt-2 text-xs leading-relaxed text-muted-foreground">
          {detail}
        </div>
      ) : null}
    </div>
  )
}
