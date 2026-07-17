import { CircleNotchIcon } from "@phosphor-icons/react";

import { Badge } from "@/components/ui/badge";
import type { SymbolState } from "@/data/lab-evidence";
import { cn } from "@/lib/utils";

const labels: Record<SymbolState, string> = {
  up_to_date: "Up to date",
  recovered: "Recovered",
  partially_recovered: "Partial",
  provider_absent: "Provider absent",
  source_conflict: "Source conflict",
  non_trading: "Non-trading",
  provenance_pending: "Provenance pending",
  source_unavailable: "Source unavailable",
  fetch_failed: "Fetch failed",
  unresolved: "Unresolved",
};

const styles: Record<SymbolState, string> = {
  up_to_date: "border-primary/25 bg-primary/10 text-primary",
  recovered: "border-primary/25 bg-primary/10 text-primary",
  partially_recovered:
    "border-foreground/15 bg-foreground/5 text-foreground/78",
  provider_absent: "border-border bg-muted/65 text-muted-foreground",
  source_conflict: "border-destructive/30 bg-destructive/10 text-destructive",
  non_trading: "border-border bg-muted/65 text-muted-foreground",
  provenance_pending: "border-border bg-muted/65 text-muted-foreground",
  source_unavailable: "border-border bg-muted/65 text-muted-foreground",
  fetch_failed: "border-destructive/30 bg-destructive/10 text-destructive",
  unresolved: "border-destructive/30 bg-destructive/10 text-destructive",
};

export function StatusBadge({
  status,
  className,
}: {
  status: SymbolState;
  className?: string;
}) {
  return (
    <Badge
      variant="outline"
      className={cn(
        "gap-1.5 rounded-md font-mono text-[0.66rem]",
        styles[status],
        className,
      )}
    >
      <CircleNotchIcon
        weight={
          status === "recovered" || status === "up_to_date" ? "fill" : "regular"
        }
        className="size-2.5"
      />
      {labels[status]}
    </Badge>
  );
}
