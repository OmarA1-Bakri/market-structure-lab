import { useState } from "react";
import {
  FlaskIcon,
  LockKeyIcon,
  WarningCircleIcon,
} from "@phosphor-icons/react";

import { MetricBlock } from "@/components/metric-block";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useLabEvidence } from "@/data/lab-evidence";
import { cn } from "@/lib/utils";

type RunKey = "stable" | "rejected";

export function DiscoveryPage() {
  const evidence = useLabEvidence();
  const [runKey, setRunKey] = useState<RunKey>("stable");
  if (evidence.state !== "ready") {
    return (
      <div className="m-8 rounded-2xl border border-border p-8 font-mono text-sm">
        {evidence.state === "loading"
          ? "Loading fixture contract…"
          : `Evidence unavailable: ${evidence.message}`}
      </div>
    );
  }
  const replay = evidence.data.software_replay;
  const task14 = evidence.data.experiment_accuracy.task14_programme_checkpoint;
  const run = replay.runs[runKey];
  const metrics = run.metrics as {
    behaviours: number;
    motifs: number;
    transitions: number;
    discovery_rows: number;
    development_rows: number;
  };

  return (
    <div className="space-y-9">
      <section className="grid gap-7 border-b border-border pb-8 xl:grid-cols-[minmax(0,1.2fr)_minmax(20rem,0.8fr)]">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <Badge
              variant="outline"
              className="rounded-md border-primary/25 bg-primary/8 font-mono text-[0.65rem] text-primary"
            >
              SYNTHETIC GOLDEN FIXTURE
            </Badge>
            <span className="font-mono text-[0.65rem] text-muted-foreground">
              no verification receipt published
            </span>
          </div>
          <h1 className="mt-5 text-4xl font-semibold tracking-[-0.05em] md:text-5xl">
            Discovery software evidence is not market evidence.
          </h1>
          <p className="mt-4 max-w-2xl text-base leading-relaxed text-muted-foreground">
            The exact fixture, interpretation input, response, run manifests,
            metrics, and behaviour identities are checksum-linked in the
            deployment contract. No fixture is counted as a real experiment.
          </p>
        </div>
        <Alert className="border-border bg-card/40">
          <WarningCircleIcon className="text-primary" />
          <AlertTitle>Accuracy remains not estimable</AlertTitle>
          <AlertDescription className="leading-relaxed text-muted-foreground">
            The contract supplies{" "}
            {
              evidence.data.experiment_accuracy.verified_real_trial_artifacts
                .total
            }{" "}
            verified real-trial artifacts and reports the trial ledger as{" "}
            {evidence.data.experiment_accuracy.trial_ledger_status.replace(
              "_",
              " ",
            )}
            . By mode: {Object.entries(
              evidence.data.experiment_accuracy.verified_real_trial_artifacts
                .by_mode,
            )
              .map(([mode, count]) => `${mode} ${count}`)
              .join(", ")}. Terminal statuses: {Object.entries(
              evidence.data.experiment_accuracy.verified_real_trial_artifacts
                .by_status,
            )
              .map(([status, count]) => `${status} ${count}`)
              .join(", ")}. Receipt hashes are verified; the asserted derivation
            chain is not yet verified.
          </AlertDescription>
        </Alert>
      </section>

      {task14 ? (
        <section className="rounded-[1.7rem] border border-primary/18 bg-primary/[0.045] p-6">
          <div className="flex flex-wrap items-center justify-between gap-4">
            <div>
              <Badge
                variant="outline"
                className="rounded-md border-primary/25 bg-primary/8 font-mono text-[0.65rem] text-primary"
              >
                OUTCOME-BLIND REAL PILOT
              </Badge>
              <h2 className="mt-3 text-xl font-semibold">
                Task 14 programme checkpoint
              </h2>
              <p className="mt-2 max-w-2xl text-sm text-muted-foreground">
                {task14.program_id} / {task14.run_id} closed as{" "}
                {task14.programme_conclusion}; the detector status was{" "}
                {task14.scientific_status}. This is rejection evidence, not a
                predictive validation result.
              </p>
            </div>
            <div className="font-mono text-[0.65rem] text-muted-foreground">
              <span className="block">
                receipt · {task14.trial_receipt_sha256.slice(0, 12)}…
              </span>
              <span className="mt-1 block">
                holdout rows accessed · {String(task14.holdout_rows_accessed)}
              </span>
            </div>
          </div>
        </section>
      ) : null}

      <section className="flex flex-col gap-5 lg:flex-row lg:items-center lg:justify-between">
        <div className="flex w-fit gap-1 rounded-xl border border-border bg-card/45 p-1">
          {(["stable", "rejected"] as const).map((key) => (
            <Button
              key={key}
              variant="ghost"
              onClick={() => setRunKey(key)}
              className={cn(
                "min-w-36 rounded-lg text-xs",
                runKey === key &&
                  "border border-primary/15 bg-primary/8 text-primary",
              )}
            >
              {key} fixture
            </Button>
          ))}
        </div>
        <div className="font-mono text-[0.65rem] text-muted-foreground">
          <span>
            {run.run_id} · {run.status} · {run.manifest_sha256.slice(0, 12)}…
          </span>
          <span className="mt-1 block text-primary/80">
            transition model · {run.transition_algorithm_version}
          </span>
        </div>
      </section>

      <section className="grid gap-5 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-5">
        <MetricBlock
          label="Behaviours"
          value={metrics.behaviours}
          detail="fixture count"
        />
        <MetricBlock
          label="Motifs"
          value={metrics.motifs}
          detail="fixture count"
        />
        <MetricBlock
          label="Transitions"
          value={metrics.transitions}
          detail="fixture count"
        />
        <MetricBlock
          label="Discovery rows"
          value={metrics.discovery_rows}
          detail="synthetic rows"
        />
        <MetricBlock
          label="Development rows"
          value={metrics.development_rows}
          detail="synthetic rows"
        />
      </section>

      {runKey === "stable" ? (
        <section className="grid gap-5 lg:grid-cols-2">
          {replay.behaviours.map((behaviour) => (
            <article
              key={behaviour.behaviour_id}
              className="rounded-[1.7rem] border border-border bg-card/42 p-6 panel-edge"
            >
              <p className="font-mono text-[0.62rem] text-primary">
                {behaviour.behaviour_id}
              </p>
              <h2 className="mt-2 text-xl font-semibold">
                {behaviour.neutral_name}
              </h2>
              <div className="mt-5 grid grid-cols-2 gap-4 border-y border-border py-4">
                <MetricBlock
                  label="Frequency"
                  value={behaviour.frequency}
                  detail="fixture events"
                />
                <MetricBlock
                  label="Centroid"
                  value={behaviour.centroid
                    .map((value) => value.toFixed(3))
                    .join(" / ")}
                  detail={behaviour.asset_coverage.join(", ")}
                />
              </div>
              <p className="mt-5 text-xs leading-relaxed text-muted-foreground">
                {behaviour.falsifiable_hypothesis}
              </p>
              <ul className="mt-4 list-disc space-y-1 pl-4 text-xs text-muted-foreground">
                {behaviour.cautions.map((caution) => (
                  <li key={caution}>{caution}</li>
                ))}
              </ul>
            </article>
          ))}
        </section>
      ) : (
        <section className="grid min-h-64 place-items-center rounded-[1.7rem] border border-dashed border-border p-8 text-center">
          <div>
            <FlaskIcon size={32} className="mx-auto text-muted-foreground" />
            <p className="mt-3 font-medium">Rejected fixture run retained</p>
            <p className="mt-2 text-xs text-muted-foreground">
              No behaviours were frozen under the stricter fixture policy.
            </p>
          </div>
        </section>
      )}

      <section className="rounded-[1.7rem] border border-primary/18 bg-primary/[0.045] p-6">
        <div className="flex gap-3">
          <LockKeyIcon size={20} className="text-primary" />
          <div>
            <p className="font-medium">
              {task14
                ? "Task 14 closed; validation not yet executed"
                : "Task 14 remains pending"}
            </p>
            <p className="mt-2 text-sm text-muted-foreground">
              {task14
                ? "The separate Task 15 plan is the next gate. No outcomes, accuracy, edge, strategy, or profitability claim is present here."
                : "Linked fixture hashes prove contract identity only; they do not prove replay execution, market recurrence, prediction, or profitability."}
            </p>
          </div>
        </div>
      </section>
    </div>
  );
}
