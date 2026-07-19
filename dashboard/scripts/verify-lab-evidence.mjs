import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";

const path = process.argv[2]
  ? pathToFileURL(resolve(process.argv[2]))
  : new URL("../public/data/lab-evidence-v1.json", import.meta.url);
const fail = (message) => {
  throw new Error(`invalid lab evidence: ${message}`);
};
const object = (value) =>
  value && typeof value === "object" && !Array.isArray(value);
const count = (value) => Number.isInteger(value) && value >= 0;
const sha = (value) =>
  typeof value === "string" && /^[0-9a-f]{64}$/.test(value);
const counts = (value) => object(value) && Object.values(value).every(count);
const exactKeys = (value, keys) =>
  object(value) &&
  Object.keys(value).length === keys.length &&
  keys.every((key) => Object.hasOwn(value, key));
const local = (value) =>
  typeof value === "string" && value.length > 0 && !/[\\/]/.test(value);
const timestamp = (value) =>
  typeof value === "string" &&
  /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/.test(value) &&
  !Number.isNaN(new Date(value).valueOf()) &&
  new Date(value).toISOString().replace(".000Z", "Z") === value;
const utcTimestamp = (value) =>
  typeof value === "string" &&
  /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$/.test(value) &&
  !Number.isNaN(new Date(value).valueOf());
const symbolStatuses = new Set([
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
]);
const compatibilityStates = new Set([
  "pending",
  "compatible",
  "source_conflict",
  "source_unavailable",
]);
const transitionAlgorithmVersion = "boundary-aware-dwell-transitions-v3";

let evidence;
try {
  evidence = JSON.parse(readFileSync(path, "utf8"));
} catch (error) {
  fail(`unreadable JSON: ${error.message}`);
}
if (
  !object(evidence) ||
  evidence.schema_version !== 1 ||
  evidence.connection_mode !== "deployment_snapshot" ||
  !timestamp(evidence.generated_at)
)
  fail("root contract");
const freshness = evidence.freshness;
if (
  !object(freshness) ||
  !timestamp(freshness.evidence_timestamp) ||
  !local(freshness.plan_filename) ||
  !local(freshness.report_filename) ||
  !sha(freshness.plan_sha256) ||
  !sha(freshness.report_sha256) ||
  !sha(freshness.dump_sha256)
)
  fail("freshness identities");
if (
  ![
    freshness.before_missing_minutes,
    freshness.recovered_minutes,
    freshness.remaining_missing_minutes,
    freshness.inserted_rows,
  ].every(count) ||
  freshness.before_missing_minutes !==
    freshness.recovered_minutes + freshness.remaining_missing_minutes ||
  freshness.coverage_conserved !== true ||
  !counts(freshness.status_counts)
)
  fail("freshness conservation");
if (
  !Array.isArray(freshness.symbols) ||
  freshness.symbols.some(
    (item) =>
      !object(item) ||
      typeof item.symbol !== "string" ||
      item.timeframe !== "1m" ||
      !symbolStatuses.has(item.status) ||
      !compatibilityStates.has(item.compatibility) ||
      !count(item.before_missing_minutes) ||
      !count(item.recovered_minutes) ||
      !count(item.remaining_missing_minutes) ||
      !count(item.inserted_rows) ||
      item.before_missing_minutes !==
        item.recovered_minutes + item.remaining_missing_minutes ||
      typeof item.current_through_cutoff !== "boolean" ||
      typeof item.reason !== "string",
  )
)
  fail("freshness symbols");
const reconciliation = evidence.reconciliation;
for (const key of ["bounded_audit", "full_history"]) {
  const run = reconciliation?.[key];
  const validPromotion =
    (run?.promotion_receipt_available === false &&
      run?.promoted_verified_intervals === 0) ||
    (run?.promotion_receipt_available === true &&
      count(run?.promoted_verified_intervals) &&
      run.promoted_verified_intervals > 0 &&
      utcTimestamp(run?.promoted_at) &&
      sha(run?.promotion_receipt_content_sha256) &&
      sha(run?.promotion_coverage_logical_sha256) &&
      sha(run?.replacement_logical_sha256) &&
      sha(run?.canonical_logical_sha256));
  if (
    !object(run) ||
    typeof run.run_id !== "string" ||
    typeof run.cutoff !== "string" ||
    typeof run.algorithm_version !== "string" ||
    typeof run.mapping_version !== "string" ||
    !sha(run.run_manifest_sha256) ||
    !sha(run.verified_manifest_set_sha256) ||
    !count(run.expected_work_units) ||
    !count(run.verified_work_units) ||
    run.verified_work_units > run.expected_work_units ||
    !count(run.audited_keys) ||
    !count(run.replacement_rows) ||
    !counts(run.work_unit_status_counts) ||
    !counts(run.classification_counts) ||
    !counts(run.differing_field_counts) ||
    typeof run.scope_label !== "string" ||
    typeof run.completion_state !== "string" ||
    !validPromotion ||
    typeof run.research_eligibility !== "string"
  )
    fail(`reconciliation ${key}`);
}
if (
  reconciliation.bounded_audit.completion_state !== "bounded_audit_complete" ||
  reconciliation.bounded_audit.verified_work_units !==
    reconciliation.bounded_audit.expected_work_units
)
  fail("bounded completion");
const history = reconciliation.full_history;
const partialHistory =
  history.completion_state === "partial" &&
  history.verified_work_units < history.expected_work_units &&
  history.scope_label === "partial full-history audit progress" &&
  history.research_eligibility ===
    "blocked_until_complete_and_deliberately_promoted";
const completeUnpromoted =
  history.completion_state === "complete_unpromoted" &&
  history.verified_work_units === history.expected_work_units &&
  history.scope_label === "full-history audit complete; unpromoted" &&
  history.research_eligibility ===
    "blocked_pending_deliberate_promotion_receipt";
const completePromoted =
  history.completion_state === "complete_promoted" &&
  history.verified_work_units === history.expected_work_units &&
  history.scope_label === "full-history reconciliation promoted" &&
  history.research_eligibility ===
    "reconciliation_provenance_established_snapshot_not_frozen";
if (!partialHistory && !completeUnpromoted && !completePromoted)
  fail("full-history semantics");
const accuracy = evidence.experiment_accuracy;
const trials = accuracy?.verified_real_trial_artifacts;
const modeKeys = ["discovery", "hypothesis", "validation", "strategy"];
const statusKeys = ["failed", "inconclusive", "abandoned", "rejected", "completed"];
const byMode = trials?.by_mode;
const byStatus = trials?.by_status;
const matrix = trials?.by_mode_and_status;
const validMatrix =
  exactKeys(matrix, modeKeys) &&
  modeKeys.every(
    (mode) =>
      exactKeys(matrix[mode], statusKeys) &&
      counts(matrix[mode]) &&
      statusKeys.reduce((sum, status) => sum + matrix[mode][status], 0) ===
        byMode?.[mode],
  ) &&
  statusKeys.every(
    (status) =>
      modeKeys.reduce((sum, mode) => sum + matrix[mode][status], 0) ===
      byStatus?.[status],
  );
if (
  !object(accuracy) ||
  accuracy.status !== "not_estimable" ||
  accuracy.trial_receipt_schema !== "trial-receipt-v2" ||
  accuracy.derivation_chain_verified !== false ||
  accuracy.fixture_trials_counted_as_real !== false ||
  !exactKeys(trials, ["by_mode", "by_status", "by_mode_and_status", "total"]) ||
  !exactKeys(byMode, modeKeys) ||
  !counts(byMode) ||
  !exactKeys(byStatus, statusKeys) ||
  !counts(byStatus) ||
  !count(trials.total) ||
  trials.total !== modeKeys.reduce((sum, mode) => sum + byMode[mode], 0) ||
  trials.total !== statusKeys.reduce((sum, status) => sum + byStatus[status], 0) ||
  !validMatrix ||
  accuracy.trial_ledger_status !==
    (trials.total === 0 ? "implemented_empty" : "implemented_with_receipts")
)
  fail("trial ledger contract");
const replay = evidence.software_replay;
const fixtureProducer = replay?.fixture_producer;
if (
  !object(replay) ||
  replay.status !== "fixture_contract_present" ||
  replay.fixture_kind !== "synthetic_golden" ||
  typeof replay.fixture_path !== "string" ||
  typeof replay.claim !== "string" ||
  replay.verification_receipt_available !== false ||
  !sha(replay.fixture_sha256) ||
  replay.fixture_schema_version !== "phase4-discovery-fixture-v4" ||
  !exactKeys(fixtureProducer, ["builder_id", "builder_version", "input_schema"]) ||
  fixtureProducer.builder_id !==
    "phase4_fixture_producer.Phase4FixtureFeatureProducer" ||
  fixtureProducer.builder_version !== "phase4-fixture-producer-v2" ||
  fixtureProducer.input_schema !== "phase4-fixture-source-v2" ||
  !sha(replay.fixture_registry_sha256) ||
  !sha(replay.interpretation_input_sha256) ||
  !sha(replay.interpretation_response_sha256) ||
  !sha(replay.interpretation_publication_manifest_sha256) ||
  !object(replay.runs) ||
  !Array.isArray(replay.behaviours)
)
  fail("fixture contract");
const stableIds = replay.runs.stable?.behaviour_ids;
if (
  !Array.isArray(stableIds) ||
  stableIds.some((item) => typeof item !== "string") ||
  replay.behaviours.some(
    (item) =>
      !object(item) ||
      typeof item.behaviour_id !== "string" ||
      typeof item.neutral_name !== "string" ||
      !Array.isArray(item.centroid) ||
      item.centroid.some(
        (value) => typeof value !== "number" || !Number.isFinite(value),
      ) ||
      !count(item.frequency) ||
      !Array.isArray(item.asset_coverage) ||
      item.asset_coverage.some((value) => typeof value !== "string") ||
      !Array.isArray(item.seed_ari) ||
      item.seed_ari.some((value) => typeof value !== "number") ||
      !Array.isArray(item.parameter_perturbation_ari) ||
      item.parameter_perturbation_ari.some(
        (value) => typeof value !== "number",
      ) ||
      typeof item.falsifiable_hypothesis !== "string" ||
      !Array.isArray(item.cautions) ||
      item.cautions.some((value) => typeof value !== "string"),
  ) ||
  JSON.stringify([...stableIds].sort()) !==
    JSON.stringify(replay.behaviours.map((item) => item.behaviour_id).sort())
)
  fail("fixture behaviour linkage");
for (const [key, status] of [
  ["stable", "completed"],
  ["rejected", "rejected_unstable"],
]) {
  const run = replay.runs[key];
  if (
    !object(run) ||
    typeof run.run_id !== "string" ||
    run.status !== status ||
    !sha(run.manifest_sha256) ||
    !sha(run.identity_sha256) ||
    !sha(run.config_sha256) ||
    !exactKeys(run.artifact_sha256, [
      "behaviours.json",
      "clustering.json",
      "config.json",
      "metrics.json",
      "missingness.json",
      "motifs.json",
      "projection.json",
      "stability.json",
      "summary.md",
      "transitions.json",
    ]) ||
    !Object.values(run.artifact_sha256).every(sha) ||
    run.transition_algorithm_version !== transitionAlgorithmVersion ||
    !Array.isArray(run.behaviour_ids) ||
    run.behaviour_ids.some((value) => typeof value !== "string") ||
    !object(run.metrics) ||
    ![
      "behaviours",
      "motif_candidates",
      "motifs_published",
      "motifs_rejected",
      "transitions",
      "conditional_recurrence_estimates",
      "conditional_recurrence_rejected",
      "conditional_recurrence_descriptive_only",
      "discovery_rows",
      "development_rows",
    ].every((metric) => count(run.metrics[metric]))
  )
    fail(
      run?.transition_algorithm_version !== transitionAlgorithmVersion
        ? "transition algorithm version"
        : "fixture replay metrics",
    );
}
const registry = evidence.feature_registry;
if (
  !object(registry) ||
  !/^FS-\d{6}$/.test(registry.feature_set_id) ||
  !/^FR-[0-9A-F]{12}$/.test(registry.registry_id) ||
  !sha(registry.registry_sha256) ||
  !Array.isArray(registry.definitions) ||
  registry.definitions.length === 0
)
  fail("feature registry");
const featureNames = new Set();
for (const item of registry.definitions)
  if (
    !object(item) ||
    typeof item.name !== "string" ||
    featureNames.has(item.name) ||
    typeof item.definition !== "string" ||
    !["auction", "sequence"].includes(item.family) ||
    typeof item.units !== "string" ||
    !count(item.required_prior_observations) ||
    typeof item.version !== "string" ||
    !["null", "error"].includes(item.missing_policy) ||
    !["float", "integer", "category"].includes(item.value_kind) ||
    !["AT_CUTOFF", "TRAILING_ONLY", "TRAIN_FITTED"].includes(
      item.leakage_class,
    ) ||
    !Array.isArray(item.source_fields) ||
    item.source_fields.length === 0 ||
    item.source_fields.some((value) => typeof value !== "string") ||
    typeof item.trailing_window !== "string" ||
    ![
      "at_information_cutoff",
      "trailing_through_information_cutoff",
    ].includes(item.observable_cutoff_rule) ||
    !["not_required", "training_partition_fitted"].includes(
      item.normalization_requirement,
    ) ||
    item.future_outcome_prohibited !== true ||
    typeof item.builder_id !== "string" ||
    typeof item.builder_version !== "string" ||
    !Array.isArray(item.allowed_categories) ||
    item.allowed_categories.some((value) => typeof value !== "string")
  )
    fail("feature definition");
  else featureNames.add(item.name);
const phase = evidence.phase_gate;
if (
  !object(phase) ||
  !(
    (phase.status === "remediation_in_progress" &&
      phase.active_phase === "Phase 0: trustworthy foundation") ||
    (phase.status === "complete_task14_authorized" &&
      phase.active_phase ===
        "Phase B Task 13 deterministic hardening: complete" &&
      phase.next_required_evidence ===
        "run authorized Task 14 outcome-blind discovery after verified Task 13 remote checkpoint")
  ) ||
  typeof phase.next_required_evidence !== "string" ||
  phase.next_required_evidence.length === 0
)
  fail("phase gate");
console.log(
  `verified lab-evidence-v1.json (${freshness.symbols.length} symbols, ${registry.definitions.length} features)`,
);
