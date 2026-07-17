import { DatabaseIcon, WarningCircleIcon } from "@phosphor-icons/react";

import { Badge } from "@/components/ui/badge";
import { Progress } from "@/components/ui/progress";
import type { LabEvidence } from "@/data/lab-evidence";
import { formatNumber } from "@/data/lab-data";

export function ReconciliationStatusPanel({
  evidence,
  generatedAt,
}: {
  evidence: LabEvidence["reconciliation"];
  generatedAt: string;
}) {
  const bounded = evidence.bounded_audit;
  const history = evidence.full_history;
  const progress = history.verified_work_units / history.expected_work_units;
  const historyComplete = history.completion_state === "complete_unpromoted";
  return (
    <div className="rounded-[1.6rem] border border-primary/20 bg-primary/[0.045] p-5 panel-edge md:p-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <Badge className="rounded-md bg-primary/12 font-mono text-[0.62rem] text-primary">
              VERIFIED DEPLOYMENT SNAPSHOT
            </Badge>
            <span className="font-mono text-[0.65rem] text-muted-foreground">
              {bounded.run_id}
            </span>
          </div>
          <h2 className="mt-3 text-xl font-semibold tracking-tight">
            Bounded Binance row audit
          </h2>
          <p className="mt-2 max-w-2xl text-sm leading-relaxed text-muted-foreground">
            Checksum-verified work-unit evidence. No browser database
            connection, promotion receipt, or full-history eligibility claim.
          </p>
        </div>
        <DatabaseIcon size={20} className="text-primary" />
      </div>
      <div className="mt-5 grid gap-4 border-t border-primary/15 pt-5 sm:grid-cols-2 xl:grid-cols-4">
        <Metric value={bounded.audited_keys} label="audited minute keys" />
        <Metric
          value={bounded.classification_counts.binance_correction ?? 0}
          label="Binance corrections"
        />
        <Metric
          value={bounded.classification_counts.binance_fill ?? 0}
          label="Binance fills"
        />
        <Metric
          value={bounded.verified_work_units}
          label={`verified work units / ${bounded.expected_work_units}`}
        />
      </div>
      <div className="mt-5 border-t border-primary/15 pt-4">
        <div className="flex items-center justify-between gap-3 text-[0.68rem] text-muted-foreground">
          <span>
            {historyComplete
              ? "RR-000008 complete audit; unpromoted"
              : "RR-000008 partial audit progress"}
          </span>
          <span className="font-mono">
            {history.verified_work_units}/{history.expected_work_units} (
            {(progress * 100).toFixed(1)}%)
          </span>
        </div>
        <Progress value={progress * 100} className="mt-2 h-1.5" />
        <div className="mt-4 flex gap-2 text-xs text-muted-foreground">
          <WarningCircleIcon size={16} className="shrink-0 text-primary" />
          <span>
            {historyComplete
              ? "Audit completion does not imply promotion or research eligibility."
              : "Partial evidence is not promotable."}{" "}
            Promoted verified intervals reported:{" "}
            {history.promoted_verified_intervals}.
          </span>
        </div>
      </div>
      <p className="mt-4 break-all font-mono text-[0.62rem] text-muted-foreground">
        run manifest {bounded.run_manifest_sha256}
      </p>
      <div className="mt-5 grid gap-4 border-t border-primary/15 pt-5 lg:grid-cols-2">
        {[bounded, history].map((run) => (
          <div
            key={run.run_id}
            className="rounded-xl border border-border bg-background/35 p-4"
          >
            <div className="flex items-center justify-between gap-3">
              <p className="font-mono text-xs text-foreground">{run.run_id}</p>
              <Badge variant="outline" className="font-mono text-[0.6rem]">
                {run.completion_state}
              </Badge>
            </div>
            <dl className="mt-4 grid grid-cols-2 gap-3 text-xs">
              <div>
                <dt className="text-muted-foreground">cutoff</dt>
                <dd className="mt-1 font-mono">{run.cutoff}</dd>
              </div>
              <div>
                <dt className="text-muted-foreground">algorithm</dt>
                <dd className="mt-1 font-mono">{run.algorithm_version}</dd>
              </div>
              <div>
                <dt className="text-muted-foreground">audited keys</dt>
                <dd className="mt-1 font-mono">
                  {formatNumber(run.audited_keys)}
                </dd>
              </div>
              <div>
                <dt className="text-muted-foreground">work units</dt>
                <dd className="mt-1 font-mono">
                  {run.verified_work_units}/{run.expected_work_units}
                </dd>
              </div>
            </dl>
            <p className="mt-3 break-all font-mono text-[0.58rem] text-muted-foreground">
              manifest {run.run_manifest_sha256}
            </p>
            {run.run_id === "RR-000008" ? (
              <p className="mt-3 text-[0.65rem] text-muted-foreground">
                {historyComplete
                  ? "Complete audit, still unpromoted"
                  : "Partial, nonpromotable audit"}{" "}
                snapshot published at {generatedAt}.
              </p>
            ) : null}
          </div>
        ))}
      </div>
    </div>
  );
}

function Metric({ value, label }: { value: number; label: string }) {
  return (
    <div>
      <p className="font-mono text-2xl tracking-[-0.05em]">
        {formatNumber(value)}
      </p>
      <p className="mt-1 text-xs text-muted-foreground">{label}</p>
    </div>
  );
}
