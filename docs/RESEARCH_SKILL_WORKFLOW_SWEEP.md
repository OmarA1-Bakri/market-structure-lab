# Research Skill and Workflow Sweep

## Document control

- **Assessment date:** 2026-07-22
- **Purpose:** Identify agent skills and workflows that can improve the design, implementation,
  review, and validation of Market Structure Lab experiments without bypassing the repository's
  phase gates or treating generic trading guidance as research evidence.
- **Decision:** Use the installed research and verification skills now; defer strategy/backtesting
  skills until the approved Phase 5/6 boundary; install no additional skill or dependency under this
  sweep.

## Task-router classification

- **Primary domains:** quantitative research, time-series statistics, market structure, experiment
  design, data quality, machine learning evaluation, backtesting methodology, and research
  reproducibility.
- **Supporting domains:** PostgreSQL/data engineering, dashboard evidence, testing, code review,
  Git/GitHub checkpointing, and AI-output evaluation.
- **Complexity:** very high and stage-dependent. The work is a research program with irreversible
  evidence boundaries, not one modeling task.

The documented local Library catalog path was unavailable in this environment. The sweep therefore
cross-referenced the active skill registry, inspected the installed skill instructions, searched the
Skills CLI catalog, and checked repository/install quality for the strongest external candidates.

## Primary installed skills

| Skill/workflow | Use | Activation boundary | Restrictions |
|---|---|---|---|
| `quantitative-research` | Adversarial review of backtest bias, walk-forward design, costs, factor exposure, regime robustness, and ML overfit | Phase 4 policy review and Phase 5 plan/review | Its example thresholds and claimed success rates are not project evidence. All research thresholds require repo-specific preregistration and power/effective-sample justification. |
| `data-analytics:analyze-data-quality` | Profile completeness, uniqueness, validity, era/symbol missingness, distribution drift, and experiment-input fitness | Phase 0 evidence, snapshot admission, every real experiment | Must use canonical data APIs and inspectable artifacts; no ad hoc research SQL. |
| `data-analytics:validate-data` | Independently recompute headline metrics and check claims, denominators, exclusions, visuals, and conclusions | Every evidence report, dashboard refresh, discovery bundle, and validation report | A polished report cannot compensate for unverified source data or methodology. |
| `data-analytics:jupyter-notebooks` | Reproducible exploratory analysis and charts | Approved real discovery/validation work | Canonical algorithms remain in `market_structure_lab`; notebooks may not become the authoritative implementation. |
| `llm-evaluation` | Evaluate AI interpretation consistency, evidence grounding, contradiction retention, and reviewer agreement | AI interpretation after frozen discovery evidence exists | It cannot validate a behaviour, edge, outcome, or promotion decision. |
| `plan` / `ralplan` | Consensus planning and adversarial review for phase changes, validation policy, and irreversible data decisions | Before Phase 4 hardening, Phase 5, promotion, and strategy construction | Planning does not authorize execution across a phase gate. |
| `superpowers:test-driven-development` | Red-green-regression implementation discipline | Every behavior change or bug fix | No production change before a failing test demonstrates the required behavior. |
| `superpowers:systematic-debugging` | Root-cause incidents and statistical/software regressions | Runtime failures, drift, discrepancies, and test failures | Diagnose before changing evidence or golden artifacts. |
| `superpowers:verification-before-completion` | Fresh command evidence before completion, commit, push, or phase claims | Every task and phase gate | Previous results or subagent assertions are insufficient. |
| `autoresearch-goal` | Durable professor/critic loop over a preregistered empirical research mission | Only after Phase 4 hardening approval and a frozen validator rubric | Every attempt must enter the immutable trial ledger; it may not tune against final holdout outcomes. |

## Supporting installed skills

| Skill/workflow | Appropriate use | Why it is secondary |
|---|---|---|
| `autoresearch` | Validator-gated bounded literature or empirical deliverables | Use only when the validator and completion artifact are fixed first; ordinary planning uses `best-practice-research`. |
| `best-practice-research` | Current official/upstream statistical or library behavior before planning | Read-only input to planning, not an implementation or research authority. |
| `ml-pipeline-workflow` | Checklist for versioning, modularity, validation, and drift | Its Airflow/Kubeflow/MLflow/deployment patterns conflict with the lightweight current phase and are explicitly deferred. |
| `data-pipeline` | Bounded ingestion/publication review | Existing canonical Polars/PostgreSQL/Parquet pipeline remains authoritative; no replacement platform is justified. |
| `code-review`, `security-review` | Independent software and secret-safety review | They validate implementation quality, not financial validity. |

## External candidates

No external skill is installed by this sweep.

### Recommended for later review

1. **`wshobson/agents@backtesting-frameworks`** — strongest external candidate for the later
   Phase 5/6 validation and backtest plan. The catalog reports 12.9K installs, a 38K-star source
   repository, security-audit passes, point-in-time data, walk-forward analysis, costs, and
   out-of-sample controls. Review its exact instructions before installation and subordinate it to
   `docs/PRD.md`.
   - Catalog: <https://www.skills.sh/wshobson/agents/backtesting-frameworks>
   - Source: <https://github.com/wshobson/agents>
   - Candidate command: `npx skills add wshobson/agents@backtesting-frameworks -g -y`

2. **Selected `agiprolabs/claude-trading-skills` skills** — possible supplemental checklists for
   `walk-forward-validation`, `slippage-modeling`, `market-microstructure-traditional`, and
   `regime-detection`. The collection is crypto-focused and has 228 GitHub stars, but individual
   skills have much lower installation counts than the backtesting candidate. Inspect each file and
   reject any dependency, API, outcome, or execution assumption that conflicts with this repository.
   - Source: <https://github.com/agiprolabs/claude-trading-skills>
   - Candidate command example:
     `npx skills add agiprolabs/claude-trading-skills@walk-forward-validation -g -y`

### Already installed; do not duplicate

`omer-metin/skills-for-antigravity@quantitative-research` is already available as
`quantitative-research`. The catalog reports 2.2K installs and 112 GitHub stars. Its useful content
is the adversarial checklist; generic thresholds, personality claims, and anecdotal success rates
must not enter frozen experiment policy without independent justification.

### Rejected or deferred

| Candidate class | Decision | Reason |
|---|---|---|
| Low-install generic quant/finance skills | Reject | Weak source reputation or insufficient adoption; they add noise rather than a defensible method. |
| `longbridge-market-microstructure` | Defer | Provider/platform orientation and a 43-star repository; current OHLCV data cannot support order-book or trade-direction claims. |
| Generic causal-inference prompts | Defer | The immediate objective is robust descriptive discovery and predictive validation, not unsupported causal attribution. |
| Deep-learning/RL trading skills | Reject for current roadmap | High overfit risk, no demonstrated need, and incompatible with deterministic-before-probabilistic staging. |
| MLflow, W&B, Airflow, Kubeflow, feature-store workflows | Reject for current roadmap | Prohibited or unjustified platform complexity; the repository already has immutable local artifacts and trial receipts. |
| Portfolio optimization and live execution skills | Defer beyond validated edge | Portfolio and execution work begins only after untouched cost-adjusted edge validation. |
| Public-equity research/modeling skills | Reject for this program | Fundamental-equity workflows do not address cryptocurrency auction reconstruction from OHLCV. |

## Phase-specific workflow

### Phase 0 — trustworthy foundation

1. `superpowers:systematic-debugging` for RR incidents.
2. `superpowers:test-driven-development` for every repair.
3. `data-analytics:analyze-data-quality` for filesystem/database reconciliation and snapshot fitness.
4. `data-analytics:validate-data` for dashboard and evidence-report recomputation.
5. `superpowers:verification-before-completion` before each GitHub checkpoint.

### Phase 4 hardening — outcome-blind discovery reliability

1. `ralplan` for the frozen provenance, stability, transition, and leakage policy.
2. `quantitative-research` as an adversarial methodology reviewer, not a threshold source.
3. TDD for provenance forgery, motif-boundary, instability, low-support, and leakage regressions.
4. `llm-evaluation` only for the non-authoritative interpretation layer.
5. Independent `validate-data` review of every published reliability-vector claim.

### Task 14 first real discovery

1. Freeze the mission, search space, negative controls, trial budget, and validator.
2. Use `autoresearch-goal` only after that freeze, Task 13 completion, and final Phase B approval.
3. Record every attempted configuration in `trial-receipt-v2`.
4. Stop after outcome-blind behaviour publication; do not attach forward outcomes.

#### Task 14 execution record

- TDD, systematic debugging, and verification-before-completion instructions governed the
  `PG-000003` incident and immutable `PG-000004` retry.
- Independent specification, code-quality, data/evidence, and adversarial quantitative lanes read
  the exact runtime artefacts. The quantitative lane confirmed cluster rejection, low effective
  transition support, zero behaviours, and the ex-post availability-selection limitation.
- No generic external threshold was imported. No additional package, service, or skill was
  installed.
- The named external `data-analytics:validate-data` and `quantitative-research` runtimes were not
  separately invoked as executable skills in this session; equivalent installed-role review lanes
  were used and are named accurately in the Task 14 evidence. This is not represented as external
  skill execution.

### Task 15 Phase 5 validation planning and later backtesting

1. Separate tracked plan and test-spec artefacts were created before outcomes:
   `docs/superpowers/plans/2026-07-22-phase5-validation.md` and
   `docs/superpowers/plans/2026-07-22-phase5-validation-test-spec.md`.
2. Repository-capability and adversarial quantitative review lanes challenged causal timing,
   multi-asset folds, purge/embargo, costs, controls, multiplicity, robustness, final access, and
   immutable receipt ownership. Confirmed gaps were repaired before checkpointing.
3. No external threshold was imported. The initial 4,096-draw bound, family error allocation,
   candidate grid, horizons, and work counts are repository-specific preregistration choices and are
   falsifiable rather than claimed as financial truths.
4. The optional `backtesting-frameworks` skill was not installed or adopted. No new dependency is
   authorised; the required causal/provenance semantics are narrower than a general backtesting
   framework.
5. The resulting order is A -> B -> G -> E -> D. Naive, unconditional, persistence, and simple
   trend/breakout controls precede any higher-capacity model; no higher-capacity ML is in this phase.
6. The named external `quantitative-research` runtime was not separately invoked as an executable
   skill; an equivalent adversarial quantitative review lane reviewed the exact plan and is recorded
   accurately rather than misrepresented as external skill execution.

## Skill-governance rules

1. `docs/PRD.md`, `AGENTS.md`, frozen experiment policy, and reviewed code outrank skill guidance.
2. Installing a skill does not authorize adding its libraries or services to `pyproject.toml`.
3. External numeric thresholds are hypotheses or examples, never project defaults.
4. Record skill name, source commit/version, role, and any material guidance in experiment metadata
   when it influences a real experiment.
5. A skill may propose a behaviour or validation design; deterministic code and predefined gates
   decide status.
6. No skill may inspect the final holdout early, suppress failed trials, infer participant identity
   from OHLCV, or promote an edge.
7. Re-run this sweep before Task 15 Phase 5 planning because external skill quality and installed
   versions can change.
