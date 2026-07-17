import {
  ArrowRightIcon,
  FingerprintIcon,
  LockKeyIcon,
  WarningCircleIcon,
} from "@phosphor-icons/react";
import { motion, useReducedMotion } from "motion/react";

import { MetricBlock } from "@/components/metric-block";
import { CoverageArc } from "@/components/visuals";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useLabEvidence } from "@/data/lab-evidence";
import { formatCompact, formatNumber, type PageId } from "@/data/lab-data";
import { cn } from "@/lib/utils";

const reveal = { hidden: { opacity: 0, y: 14 }, visible: { opacity: 1, y: 0 } };
const phases = [
  { label: "00", name: "Trustworthy foundation remediation", state: "active" },
  { label: "01", name: "Canonical data truth", state: "locked" },
  {
    label: "02",
    name: "Deterministic auction representation",
    state: "locked",
  },
  { label: "03", name: "Versioned features & events", state: "locked" },
  { label: "04", name: "Outcome-blind real-market discovery", state: "locked" },
  { label: "05", name: "Untouched validation", state: "locked" },
] as const;

export function OverviewPage({
  onNavigate,
}: {
  onNavigate: (page: PageId) => void;
}) {
  const reduceMotion = useReducedMotion();
  const evidence = useLabEvidence();
  if (evidence.state !== "ready") {
    return (
      <EvidenceUnavailable
        state={evidence.state}
        message={evidence.state === "error" ? evidence.message : undefined}
      />
    );
  }
  const { data } = evidence;
  const freshness = data.freshness;
  const history = data.reconciliation.full_history;
  const bounded = data.reconciliation.bounded_audit;
  const accuracy = data.experiment_accuracy;
  const trialCounts = accuracy.verified_real_trial_artifacts;
  const modeCounts = Object.entries(
    data.experiment_accuracy.verified_real_trial_artifacts.by_mode,
  )
    .map(([mode, count]) => `${mode} ${count}`)
    .join(", ");

  return (
    <motion.div
      initial={reduceMotion ? false : "hidden"}
      animate="visible"
      variants={{
        visible: { transition: { staggerChildren: reduceMotion ? 0 : 0.07 } },
      }}
      className="space-y-10"
    >
      <motion.section
        variants={reveal}
        className="grid items-start gap-8 border-b border-border pb-9 xl:grid-cols-[minmax(0,1.45fr)_minmax(22rem,0.75fr)]"
      >
        <div className="max-w-3xl">
          <div className="flex flex-wrap items-center gap-2">
            <Badge
              variant="outline"
              className="rounded-md border-primary/25 bg-primary/8 font-mono text-[0.65rem] text-primary"
            >
              PHASE 0 REMEDIATION IN PROGRESS
            </Badge>
            <span className="font-mono text-[0.65rem] text-muted-foreground">
              evidence cutoff {freshness.evidence_timestamp}
            </span>
          </div>
          <h1 className="mt-6 max-w-3xl text-4xl leading-[0.98] font-semibold tracking-[-0.055em] text-balance md:text-6xl">
            A research console built around what the evidence can prove.
          </h1>
          <p className="mt-5 max-w-2xl text-base leading-relaxed text-muted-foreground md:text-lg">
            {history.completion_state === "partial"
              ? "Data recovery and full-history reconciliation remain incomplete."
              : "Full-history reconciliation is complete but unpromoted."} {accuracy.claim}
          </p>
          <div className="mt-7 flex flex-wrap gap-3">
            <Button
              size="lg"
              onClick={() => onNavigate("data")}
              className="rounded-xl px-4"
            >
              Inspect verified data evidence{" "}
              <ArrowRightIcon size={16} data-icon="inline-end" />
            </Button>
            <Button
              variant="outline"
              size="lg"
              onClick={() => onNavigate("discovery")}
              className="rounded-xl border-border bg-background/45 px-4"
            >
              Review experiment readiness
            </Button>
          </div>
        </div>

        <div className="relative overflow-hidden rounded-[1.8rem] border border-border bg-card/58 p-5 panel-edge md:p-6">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-[0.66rem] font-medium tracking-[0.18em] text-muted-foreground uppercase">
                Current evidence boundary
              </p>
              <p className="mt-2 text-lg font-semibold tracking-tight">
                {data.experiment_accuracy.status.replace("_", " ")}
              </p>
            </div>
            <FingerprintIcon size={22} className="text-primary" />
          </div>
          <div className="mt-7 space-y-5">
            <div className="flex gap-3">
              <WarningCircleIcon
                size={18}
                className="mt-0.5 shrink-0 text-primary"
              />
              <div>
                <p className="text-sm font-medium">
                  Synthetic fixture contract present
                </p>
                <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
                  No replay receipt is published in this contract; the fixture
                  is not market evidence.
                </p>
              </div>
            </div>
            <div className="flex gap-3">
              <WarningCircleIcon
                size={18}
                className="mt-0.5 shrink-0 text-primary"
              />
              <div>
                <p className="text-sm font-medium">
                  {history.completion_state === "partial"
                    ? "RR-000008 is partial"
                    : "RR-000008 audit is complete but unpromoted"}
                </p>
                <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
                  {history.verified_work_units} of {history.expected_work_units}{" "}
                  frozen work units verify in this publication snapshot. No
                  promotion receipt is supplied.
                </p>
              </div>
            </div>
            <div className="flex gap-3">
              <LockKeyIcon
                size={18}
                className="mt-0.5 shrink-0 text-muted-foreground"
              />
              <div>
                <p className="text-sm font-medium">Validation remains sealed</p>
                <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
                  Ledger status: {accuracy.trial_ledger_status.replaceAll("_", " ")}.
                  Verified real receipts: {trialCounts.total}. By mode: {modeCounts}.
                  Receipt integrity is checked; derivation-chain verification is{" "}
                  {accuracy.derivation_chain_verified ? "complete" : "not yet complete"}.
                </p>
              </div>
            </div>
          </div>
        </div>
      </motion.section>

      <motion.section
        variants={reveal}
        className="grid gap-5 sm:grid-cols-2 xl:grid-cols-4"
      >
        <MetricBlock
          label="Missing before run"
          value={formatCompact(freshness.before_missing_minutes)}
          detail="Frozen latest freshness plan"
        />
        <MetricBlock
          label="Recovered in run"
          value={formatNumber(freshness.recovered_minutes)}
          detail={`${formatNumber(freshness.inserted_rows)} inserted rows`}
        />
        <MetricBlock
          label="Still missing"
          value={formatCompact(freshness.remaining_missing_minutes)}
          detail={`${freshness.symbols.length} symbol reports`}
        />
        <MetricBlock
          label="Verified real-trial artifacts"
          value={trialCounts.total}
          detail={accuracy.claim}
        />
      </motion.section>

      <motion.section
        variants={reveal}
        className="grid gap-7 xl:grid-cols-[minmax(0,1.1fr)_minmax(20rem,0.9fr)]"
      >
        <div className="rounded-[1.8rem] border border-border bg-card/46 p-5 panel-edge md:p-7">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div>
              <p className="text-[0.66rem] font-medium tracking-[0.18em] text-muted-foreground uppercase">
                Latest freshness report
              </p>
              <h2 className="mt-2 text-2xl font-semibold tracking-[-0.035em]">
                Coverage conserved; limitations explicit.
              </h2>
            </div>
            <CoverageArc
              recovered={freshness.recovered_minutes}
              remaining={freshness.remaining_missing_minutes}
            />
          </div>
          <p className="mt-5 max-w-2xl text-sm leading-relaxed text-muted-foreground">
            The pointer, plan, and report hashes verify together. This is
            evidence freshness at the stated cutoff, not a claim that every
            market is current.
          </p>
          <p className="mt-5 break-all border-t border-border pt-4 font-mono text-[0.65rem] text-muted-foreground">
            report {freshness.report_sha256}
          </p>
        </div>
        <div className="rounded-[1.8rem] border border-border bg-card/46 p-5 panel-edge md:p-7">
          <p className="text-[0.66rem] font-medium tracking-[0.18em] text-muted-foreground uppercase">
            Bounded reconciliation evidence
          </p>
          <p className="mt-3 font-mono text-3xl">
            {formatNumber(bounded.audited_keys)}
          </p>
          <p className="mt-1 text-xs text-muted-foreground">
            audited minute keys across {bounded.verified_work_units} verified
            work units
          </p>
          <p className="mt-5 text-sm leading-relaxed text-muted-foreground">
            No immutable promotion receipt is available to this contract. It
            therefore reports zero promoted verified intervals and does not
            infer full-history eligibility.
          </p>
        </div>
      </motion.section>

      <motion.section variants={reveal} className="border-t border-border pt-8">
        <div>
          <p className="text-[0.66rem] font-medium tracking-[0.18em] text-muted-foreground uppercase">
            Canonical progression
          </p>
          <h2 className="mt-2 text-2xl font-semibold tracking-[-0.035em]">
            Phase gates stay visible.
          </h2>
        </div>
        <div className="mt-7 divide-y divide-border border-y border-border">
          {phases.map((phase) => (
            <div
              key={phase.label}
              className="grid grid-cols-[3.5rem_minmax(0,1fr)_auto] items-center gap-3 py-3.5"
            >
              <span className="font-mono text-xs text-muted-foreground">
                {phase.label}
              </span>
              <span
                className={cn(
                  "text-sm",
                  phase.state === "locked" && "text-muted-foreground",
                )}
              >
                {phase.name}
              </span>
              <span
                className={cn(
                  "font-mono text-[0.62rem] uppercase",
                  phase.state === "active"
                    ? "text-primary"
                    : "text-muted-foreground",
                )}
              >
                {phase.state}
              </span>
            </div>
          ))}
        </div>
      </motion.section>
    </motion.div>
  );
}

function EvidenceUnavailable({
  state,
  message,
}: {
  state: "loading" | "error";
  message?: string;
}) {
  return (
    <div className="m-8 rounded-[1.6rem] border border-border bg-card/42 p-8">
      <p className="font-mono text-sm">
        {state === "loading"
          ? "Loading verified evidence…"
          : "Verified evidence unavailable"}
      </p>
      {message ? (
        <p className="mt-2 text-xs text-destructive">{message}</p>
      ) : null}
    </div>
  );
}
