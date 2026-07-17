import {
  createContext,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";
import { isTrialCounts, type TrialCounts } from "@/data/trial-counts";

export const TRANSITION_ALGORITHM_VERSION =
  "boundary-aware-dwell-transitions-v2" as const;

export type SymbolState =
  | "up_to_date"
  | "recovered"
  | "partially_recovered"
  | "provider_absent"
  | "non_trading"
  | "provenance_pending"
  | "source_conflict"
  | "source_unavailable"
  | "fetch_failed"
  | "unresolved";

export interface SymbolHealth {
  symbol: string;
  timeframe: "1m";
  status: SymbolState;
  compatibility:
    | "pending"
    | "compatible"
    | "source_conflict"
    | "source_unavailable";
  before_missing_minutes: number;
  recovered_minutes: number;
  remaining_missing_minutes: number;
  inserted_rows: number;
  current_through_cutoff: boolean;
  reason: string;
}

export interface ReconciliationEvidence {
  run_id: string;
  cutoff: string;
  algorithm_version: string;
  mapping_version: string;
  run_manifest_sha256: string;
  verified_manifest_set_sha256: string;
  expected_work_units: number;
  verified_work_units: number;
  audited_keys: number;
  replacement_rows: number;
  work_unit_status_counts: Record<string, number>;
  classification_counts: Record<string, number>;
  differing_field_counts: Record<string, number>;
  scope_label: string;
  completion_state: string;
  promotion_receipt_available: false;
  promoted_verified_intervals: 0;
  research_eligibility: string;
}

export interface LabEvidence {
  schema_version: 1;
  generated_at: string;
  connection_mode: "deployment_snapshot";
  freshness: {
    evidence_timestamp: string;
    plan_filename: string;
    plan_sha256: string;
    report_filename: string;
    report_sha256: string;
    dump_sha256: string;
    before_missing_minutes: number;
    recovered_minutes: number;
    remaining_missing_minutes: number;
    inserted_rows: number;
    coverage_conserved: true;
    status_counts: Record<string, number>;
    symbols: SymbolHealth[];
  };
  reconciliation: {
    bounded_audit: ReconciliationEvidence;
    full_history: ReconciliationEvidence;
  };
  software_replay: {
    status: "fixture_contract_present";
    fixture_kind: "synthetic_golden";
    fixture_path: string;
    fixture_sha256: string;
    verification_receipt_available: false;
    claim: string;
    interpretation_input_sha256: string;
    interpretation_response_sha256: string;
    runs: Record<
      "stable" | "rejected",
      {
        run_id: string;
        status: "completed" | "rejected_unstable";
        manifest_sha256: string;
        transition_algorithm_version: typeof TRANSITION_ALGORITHM_VERSION;
        behaviour_ids: string[];
        metrics: Record<string, unknown>;
      }
    >;
    behaviours: Array<{
      behaviour_id: string;
      neutral_name: string;
      centroid: number[];
      frequency: number;
      asset_coverage: string[];
      seed_ari: number[];
      parameter_perturbation_ari: number[];
      falsifiable_hypothesis: string;
      cautions: string[];
    }>;
  };
  feature_registry: {
    feature_set_id: string;
    registry_id: string;
    registry_sha256: string;
    definitions: Array<{
      name: string;
      definition: string;
      family: "auction" | "sequence";
      value_kind: "float" | "integer" | "category";
      units: string;
      required_prior_observations: number;
      missing_policy: "null" | "error";
      version: string;
      leakage_class: "AT_CUTOFF" | "TRAILING_ONLY" | "TRAIN_FITTED";
      allowed_categories: string[];
    }>;
  };
  experiment_accuracy: {
    status: "not_estimable";
    verified_real_trial_artifacts: TrialCounts;
    trial_ledger_status: "implemented_empty" | "implemented_with_receipts";
    trial_receipt_schema: "trial-receipt-v2";
    fixture_trials_counted_as_real: false;
    derivation_chain_verified: false;
    scope: string;
    claim: string;
  };
  phase_gate: {
    active_phase: string;
    status: "remediation_in_progress";
    next_required_evidence: string;
  };
}

export type LabEvidenceState =
  | { state: "loading" }
  | { state: "ready"; data: LabEvidence }
  | { state: "error"; message: string };

const LabEvidenceContext = createContext<LabEvidenceState | null>(null);

function isObject(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function isSha256(value: unknown): value is string {
  return typeof value === "string" && /^[0-9a-f]{64}$/.test(value);
}

function isNonNegative(value: unknown): value is number {
  return typeof value === "number" && Number.isInteger(value) && value >= 0;
}

function isCountMap(value: unknown): value is Record<string, number> {
  return isObject(value) && Object.values(value).every(isNonNegative);
}

function isLocalFilename(value: unknown): value is string {
  return (
    typeof value === "string" &&
    value.length > 0 &&
    !value.includes("/") &&
    !value.includes("\\")
  );
}

function isCanonicalUtcTimestamp(value: unknown): value is string {
  if (
    typeof value !== "string" ||
    !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/.test(value)
  )
    return false;
  const parsed = new Date(value);
  return (
    !Number.isNaN(parsed.valueOf()) &&
    parsed.toISOString().replace(".000Z", "Z") === value
  );
}

function hasReplayMetrics(value: unknown): value is Record<string, number> {
  return (
    isObject(value) &&
    [
      "behaviours",
      "motifs",
      "transitions",
      "discovery_rows",
      "development_rows",
    ].every((key) => isNonNegative(value[key]))
  );
}

function isSymbol(value: unknown): value is SymbolHealth {
  if (!isObject(value)) return false;
  return (
    typeof value.symbol === "string" &&
    value.timeframe === "1m" &&
    [
      "up_to_date",
      "recovered",
      "partially_recovered",
      "provider_absent",
      "non_trading",
      "provenance_pending",
      "source_conflict",
      "source_unavailable",
      "fetch_failed",
      "unresolved",
    ].includes(String(value.status)) &&
    ["pending", "compatible", "source_conflict", "source_unavailable"].includes(
      String(value.compatibility),
    ) &&
    isNonNegative(value.before_missing_minutes) &&
    isNonNegative(value.recovered_minutes) &&
    isNonNegative(value.remaining_missing_minutes) &&
    isNonNegative(value.inserted_rows) &&
    typeof value.current_through_cutoff === "boolean" &&
    typeof value.reason === "string" &&
    value.before_missing_minutes ===
      value.recovered_minutes + value.remaining_missing_minutes
  );
}

function isReconciliation(value: unknown): value is ReconciliationEvidence {
  if (!isObject(value)) return false;
  return (
    typeof value.run_id === "string" &&
    typeof value.cutoff === "string" &&
    typeof value.algorithm_version === "string" &&
    typeof value.mapping_version === "string" &&
    isSha256(value.run_manifest_sha256) &&
    isSha256(value.verified_manifest_set_sha256) &&
    isNonNegative(value.expected_work_units) &&
    isNonNegative(value.verified_work_units) &&
    isNonNegative(value.audited_keys) &&
    isNonNegative(value.replacement_rows) &&
    isCountMap(value.work_unit_status_counts) &&
    isCountMap(value.classification_counts) &&
    isCountMap(value.differing_field_counts) &&
    value.expected_work_units >= value.verified_work_units &&
    typeof value.scope_label === "string" &&
    typeof value.completion_state === "string" &&
    value.promotion_receipt_available === false &&
    value.promoted_verified_intervals === 0 &&
    typeof value.research_eligibility === "string"
  );
}

function isFeatureRegistry(
  value: unknown,
): value is LabEvidence["feature_registry"] {
  if (!isObject(value) || !Array.isArray(value.definitions)) return false;
  const names = new Set<string>();
  const valid = value.definitions.every((definition) => {
    if (
      !isObject(definition) ||
      typeof definition.name !== "string" ||
      names.has(definition.name)
    )
      return false;
    names.add(definition.name);
    return (
      typeof definition.definition === "string" &&
      ["auction", "sequence"].includes(String(definition.family)) &&
      ["float", "integer", "category"].includes(
        String(definition.value_kind),
      ) &&
      typeof definition.units === "string" &&
      isNonNegative(definition.required_prior_observations) &&
      ["null", "error"].includes(String(definition.missing_policy)) &&
      typeof definition.version === "string" &&
      ["AT_CUTOFF", "TRAILING_ONLY", "TRAIN_FITTED"].includes(
        String(definition.leakage_class),
      ) &&
      Array.isArray(definition.allowed_categories) &&
      definition.allowed_categories.every((item) => typeof item === "string")
    );
  });
  return (
    valid &&
    value.definitions.length > 0 &&
    /^FS-\d{6}$/.test(String(value.feature_set_id)) &&
    /^FR-[0-9A-F]{12}$/.test(String(value.registry_id)) &&
    isSha256(value.registry_sha256)
  );
}

function isReplay(value: unknown): value is LabEvidence["software_replay"] {
  if (
    !isObject(value) ||
    !isObject(value.runs) ||
    !Array.isArray(value.behaviours)
  )
    return false;
  const stable = value.runs.stable;
  const rejected = value.runs.rejected;
  if (!isObject(stable) || !isObject(rejected)) return false;
  const runValid = (run: Record<string, unknown>, status: string) =>
    typeof run.run_id === "string" &&
    run.status === status &&
    isSha256(run.manifest_sha256) &&
    run.transition_algorithm_version === TRANSITION_ALGORITHM_VERSION &&
    Array.isArray(run.behaviour_ids) &&
    run.behaviour_ids.every((id) => typeof id === "string") &&
    hasReplayMetrics(run.metrics);
  if (
    !runValid(stable, "completed") ||
    !runValid(rejected, "rejected_unstable")
  )
    return false;
  const ids: string[] = [];
  const behavioursValid = value.behaviours.every((item) => {
    if (!isObject(item) || typeof item.behaviour_id !== "string") return false;
    ids.push(item.behaviour_id);
    return (
      typeof item.neutral_name === "string" &&
      Array.isArray(item.centroid) &&
      item.centroid.every((n) => typeof n === "number" && Number.isFinite(n)) &&
      isNonNegative(item.frequency) &&
      Array.isArray(item.asset_coverage) &&
      item.asset_coverage.every((s) => typeof s === "string") &&
      Array.isArray(item.seed_ari) &&
      item.seed_ari.every((n) => typeof n === "number") &&
      Array.isArray(item.parameter_perturbation_ari) &&
      item.parameter_perturbation_ari.every((n) => typeof n === "number") &&
      typeof item.falsifiable_hypothesis === "string" &&
      Array.isArray(item.cautions) &&
      item.cautions.every((s) => typeof s === "string")
    );
  });
  return (
    value.status === "fixture_contract_present" &&
    value.fixture_kind === "synthetic_golden" &&
    typeof value.fixture_path === "string" &&
    typeof value.claim === "string" &&
    isSha256(value.fixture_sha256) &&
    isSha256(value.interpretation_input_sha256) &&
    isSha256(value.interpretation_response_sha256) &&
    value.verification_receipt_available === false &&
    behavioursValid &&
    JSON.stringify([...ids].sort()) ===
      JSON.stringify([...(stable.behaviour_ids as string[])].sort())
  );
}

// This runtime validator intentionally shares the evidence-contract module.
// eslint-disable-next-line react-refresh/only-export-components
export function isLabEvidence(value: unknown): value is LabEvidence {
  if (
    !isObject(value) ||
    !isObject(value.freshness) ||
    !isObject(value.reconciliation)
  ) {
    return false;
  }
  const freshness = value.freshness;
  const reconciliation = value.reconciliation;
  const replay = value.software_replay;
  const accuracy = value.experiment_accuracy;
  const phase = value.phase_gate;
  const registry = value.feature_registry;
  return (
    value.schema_version === 1 &&
    value.connection_mode === "deployment_snapshot" &&
    isCanonicalUtcTimestamp(value.generated_at) &&
    isCanonicalUtcTimestamp(freshness.evidence_timestamp) &&
    isLocalFilename(freshness.plan_filename) &&
    isLocalFilename(freshness.report_filename) &&
    isSha256(freshness.plan_sha256) &&
    isSha256(freshness.report_sha256) &&
    isSha256(freshness.dump_sha256) &&
    isNonNegative(freshness.before_missing_minutes) &&
    isNonNegative(freshness.recovered_minutes) &&
    isNonNegative(freshness.remaining_missing_minutes) &&
    isNonNegative(freshness.inserted_rows) &&
    freshness.before_missing_minutes ===
      freshness.recovered_minutes + freshness.remaining_missing_minutes &&
    freshness.coverage_conserved === true &&
    isCountMap(freshness.status_counts) &&
    Array.isArray(freshness.symbols) &&
    freshness.symbols.every(isSymbol) &&
    isReconciliation(reconciliation.bounded_audit) &&
    isReconciliation(reconciliation.full_history) &&
    reconciliation.bounded_audit.scope_label === "bounded audited keys" &&
    reconciliation.bounded_audit.completion_state ===
      "bounded_audit_complete" &&
    reconciliation.bounded_audit.verified_work_units ===
      reconciliation.bounded_audit.expected_work_units &&
    ((reconciliation.full_history.scope_label ===
      "partial full-history audit progress" &&
      reconciliation.full_history.completion_state === "partial" &&
      reconciliation.full_history.verified_work_units <
        reconciliation.full_history.expected_work_units &&
      reconciliation.full_history.research_eligibility ===
        "blocked_until_complete_and_deliberately_promoted") ||
      (reconciliation.full_history.scope_label ===
        "full-history audit complete; unpromoted" &&
        reconciliation.full_history.completion_state ===
          "complete_unpromoted" &&
        reconciliation.full_history.verified_work_units ===
          reconciliation.full_history.expected_work_units &&
        reconciliation.full_history.research_eligibility ===
          "blocked_pending_deliberate_promotion_receipt")) &&
    isReplay(replay) &&
    isFeatureRegistry(registry) &&
    isObject(accuracy) &&
    accuracy.status === "not_estimable" &&
    isTrialCounts(accuracy.verified_real_trial_artifacts) &&
    accuracy.trial_ledger_status ===
      (accuracy.verified_real_trial_artifacts.total === 0
        ? "implemented_empty"
        : "implemented_with_receipts") &&
    accuracy.trial_receipt_schema === "trial-receipt-v2" &&
    accuracy.fixture_trials_counted_as_real === false &&
    accuracy.derivation_chain_verified === false &&
    typeof accuracy.scope === "string" &&
    typeof accuracy.claim === "string" &&
    isObject(phase) &&
    phase.status === "remediation_in_progress" &&
    phase.active_phase === "Phase 0: trustworthy foundation" &&
    typeof phase.next_required_evidence === "string" &&
    phase.next_required_evidence.length > 0
  );
}

export function LabEvidenceProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<LabEvidenceState>({ state: "loading" });

  useEffect(() => {
    const controller = new AbortController();
    async function load() {
      try {
        const response = await fetch("/data/lab-evidence-v1.json", {
          cache: "no-store",
          signal: controller.signal,
        });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const payload: unknown = await response.json();
        if (!isLabEvidence(payload))
          throw new Error("invalid lab evidence contract");
        setStatus({ state: "ready", data: payload });
      } catch (error) {
        if (controller.signal.aborted) return;
        setStatus({
          state: "error",
          message:
            error instanceof Error ? error.message : "unknown evidence error",
        });
      }
    }
    void load();
    return () => controller.abort();
  }, []);

  return <LabEvidenceContext value={status}>{children}</LabEvidenceContext>;
}

// Provider and hook stay together so consumers cannot import a different context.
// eslint-disable-next-line react-refresh/only-export-components
export function useLabEvidence(): LabEvidenceState {
  const value = useContext(LabEvidenceContext);
  if (value === null)
    throw new Error("useLabEvidence requires LabEvidenceProvider");
  return value;
}
