import { CircleNotchIcon } from "@phosphor-icons/react"

import { Badge } from "@/components/ui/badge"
import type { SymbolState } from "@/data/lab-data"
import { cn } from "@/lib/utils"

const labels: Record<SymbolState, string> = {
  recovered: "Recovered",
  partially_recovered: "Partial",
  provider_absent: "Provider absent",
  source_conflict: "Source conflict",
}

const styles: Record<SymbolState, string> = {
  recovered:
    "border-primary/25 bg-primary/10 text-primary",
  partially_recovered:
    "border-foreground/15 bg-foreground/5 text-foreground/78",
  provider_absent:
    "border-border bg-muted/65 text-muted-foreground",
  source_conflict:
    "border-destructive/30 bg-destructive/10 text-destructive",
}

export function StatusBadge({
  status,
  className,
}: {
  status: SymbolState
  className?: string
}) {
  return (
    <Badge
      variant="outline"
      className={cn("gap-1.5 rounded-md font-mono text-[0.66rem]", styles[status], className)}
    >
      <CircleNotchIcon
        weight={status === "recovered" ? "fill" : "regular"}
        className="size-2.5"
      />
      {labels[status]}
    </Badge>
  )
}
