import { groupDigits } from './resultSummary';
export type Version={id:string;definition_id:string;version_number:number;raw_sql:string;bound_sql?:string|null;time_column:string|null;source_time_column?:string|null;mapping_id?:string|null;contract_hash?:string|null;mapping_hash?:string|null;support_state:string;support_reason:string|null;raw_hash:string;ir_hash:string|null;ir:Record<string,unknown>|null;observed_at:string;source_changed_at?:string|null;effective_from?:string|null;change_kind:string};
export type Definition={id:string;name:string;source_label:string;owner_label:string;versions:Version[]};
export type Snapshot={id:string;name:string;row_count:number;manifest_hash:string;source_hash:string;contract_hash?:string|null;mapping_hash?:string|null;currency:string;timezone:string;scope:string;grain:string;cutoff_at:string;schema?:Record<string,string>|null;status:string;created_at:string;preparation_manifest_hash?:string;preparation?:Record<string,unknown>;manifest?:Record<string,unknown>;execution_policy?:string;unit_label?:string|null};
export type SnapshotImport={id:string;name:string;source_hash:string;metadata:Record<string,string>;state:string;attempt:number;created_at:string;completed_at:string|null;snapshot_id:string|null;error:{code:string;message:string}|null};
export type Candidate={left_definition_id:string;right_definition_id:string;left_version_id:string;right_version_id:string;score:number;reasons:string[];cautions:string[];review_status:string;review_id:string|null};
export type Context={start:string;end_exclusive:string;timezone:string;currency:string;unit:string;grain:string;scope:string;contract_hash?:string|null;mapping_hash?:string|null};
export type Evidence={order_id:string;baseline_included:boolean;target_included:boolean;baseline_contribution:string|null;target_contribution:string|null;delta:string|null;baseline_null_term?:boolean;target_null_term?:boolean;membership?:string;baseline_operands?:Record<string,string|null>;target_operands?:Record<string,string|null>;baseline_expression_value?:string|null;target_expression_value?:string|null;baseline_when_state?:'true'|'false'|'unknown';target_when_state?:'true'|'false'|'unknown';baseline_where_state?:'true'|'false'|'unknown';target_where_state?:'true'|'false'|'unknown';baseline_branch?:'then'|'else';target_branch?:'then'|'else';baseline_case_term?:string|null;target_case_term?:string|null;measure_value?:string|null;predicate_inputs?:Record<string,string|boolean|null>};
export type Result={baseline_value:string|null;target_value:string|null;delta_target_minus_baseline:string|null;absolute_gap:string|null;relative_change_percent_display:string|null;residual:string|null;currency:string;numeric_tolerance:string;method:string;definition_status:string;value_status:string;explanation_status:string;steps:Array<{position:number;rule:string;previous_value:string|null;next_value:string|null;contribution:string|null}>;shapley?:{status:string;method:string;contributions:Array<{rule:string;numerator:string;denominator:string;display_decimal:string}>;gap?:string;evaluated_hybrids?:number;limitation:string};evidence:Evidence[];definition_differences:Array<{rule:string;baseline:string;target:string;changed:boolean}>;provenance:Record<string,unknown>;limitations:string[]};
export type Run={id:string;baseline_version_id:string;target_version_id:string;snapshot_id:string;context:Context;relationship:string;purpose?:string;state:string;created_at:string;result:Result|null;error:{code?:string;message?:string}|null;execution_mode:string};
export type DatasetContract={id:string;version:2;version_number:number;name:string;currency:string;timezone:string;scope:string;grain:string;cutoff_at:string;null_policy:'blank_nullable'|'literal_NULL'|'native_null';row_key:'order_id';entity_key:'customer_id';decimal_scale:2;schema:Record<string,string>;contract_hash:string;created_at:string};
export type ColumnMapping={id:string;contract_id:string;version_number:number;columns:Record<string,string>;contract_hash:string;mapping_hash:string;created_at:string};
export type Investigation={id:string;title:string;decision:string;actor_label:string;run_id:string;snapshot_id:string;baseline_version_id:string;target_version_id:string;context:Context;relationship:string;review_status:string;created_at:string};
export type InvestigationDetail=Investigation&{record_type?:'investigation'|'draft';is_draft?:boolean;snapshot:Snapshot;run:Run;baseline_version:Version;target_version:Version;replay_available:boolean};
export type DatasetPreview={headers:string[];declared_types:Record<string,string|null>;observed_types:Record<string,string>;sample_rows:Array<Record<string,string|null>>;source_hash:string};
export type DatasetPreviewRecord={id:string;state:string;preview?:DatasetPreview|null;error?:{code:string;message:string}|null};
export type DecimalColumn={name:string;precision:number;scale:number;unit_label:string};
export type MeasureExpression={op:'sub';minuend:string;subtrahend:string};
export type FlatContract={id:string;version:3|4|5|6|7;predicate_policy?:'flat-boolean-v1';measure_policy?:'flat-subtraction-v1'|'flat-case-v1';decimal_columns?:DecimalColumn[];measure_expression?:MeasureExpression;dimension_types?:Record<string,'string'|'boolean'|'date'|'timestamp'>;nullable_categories?:string[];version_number:number;name:string;row_key:string;measure:string;precision?:number;scale:number;categories:string[];measure_meaning:string;unit_label:string;values_comparable:boolean;scope:'entire_supplied_snapshot';null_policy:'blank_nullable'|'literal_NULL'|'native_null';contract_hash:string};
export type EvidenceSummary={total:number;changed:number;blank_amount_rows:number;top_changes:Array<Evidence&{row_key?:string;fact_key?:string}>};
export type BreakdownGroup={value:string|boolean|null;records:number;changed_records:number;current:string;proposed:string;change:string};
export type EvidenceBreakdown={available:boolean;columns:string[];reason?:string;column?:string;total_change?:string;groups?:BreakdownGroup[];other_groups?:number;other_change?:string};
export type FlatEvidence=Evidence&{row_key?:string;measure?:string|null;categories?:Record<string,string|null>;source_payment_ids?:string[]};
export type RelationalContract = {
  id:string; version:1; version_number:number; mode:'relational_snapshot'; policy:'relational-snapshot-v1';
  name:string; contract_hash:string;
  fact:{table:string; key:string; grain:string; categories:string[]};
  dimension:{table:string; key:string; grain:string; categories:string[]};
  relationship:{fact_foreign_key:string; dimension_key:string; cardinality:'many_to_one'; missing_dimension_policy:'preserve_and_report'};
  measure:{column:string; precision:number; scale:number}; measure_meaning:string; unit_label:string;
  values_comparable:true; scope:'entire_supplied_snapshot'; null_policy:'blank_nullable'|'literal_NULL';
};
export type RelationalContext={mode:'relational_snapshot'; scope:'entire_supplied_snapshot'; contract_hash:string; unit_label:string; values_comparable:true};
export type JoinProof={fact_rows:number; dimension_rows:number; fact_key_unique:boolean; dimension_key_unique:boolean;
  matched_fact_rows:number; unmatched_fact_rows:number; null_foreign_key_rows:number; missing_nonnull_foreign_key_rows:number;
  left_join_rows:number; max_dimension_matches_per_fact:number; fanout_rows:number; grain_preserved:boolean};
export type RelationalSnapshot=Snapshot & {source_hashes:Record<'fact'|'dimension',string>; join_proof:JoinProof; null_semantics:string};
export type RelationalEvidence=Evidence & {fact_key:string; foreign_key:string|null; dimension_key:string|null; dimension_matched:boolean;
  join_membership:'matched'|'null_foreign_key'|'missing_dimension'; baseline_join_included:boolean; target_join_included:boolean;
  fact_values:Record<string,string|null>; dimension_values:Record<string,string|null>; measure_value:string|null};
export type RelationalRun=Omit<Run,'context'> & {context:RelationalContext};
export type RelationalCase=Omit<InvestigationDetail,'context'|'run'|'snapshot'> & {context:RelationalContext; run:RelationalRun; snapshot:RelationalSnapshot};
export type RelationalBootstrap=Bootstrap & {relational_contracts?:RelationalContract[]};
export type RelationalPredicate={table:'fact'|'dimension';column:string;op:'eq'|'in'|'is_null'|'is_not_null';values:string[]};
export type RelationalRowOutcome='included_value'|'included_null_measure'|'excluded_join'|'excluded_filter_false'|'excluded_filter_unknown';
export type RelationalRowExplanation={fact_key:string;join_membership:RelationalEvidence['join_membership'];
  baseline:RelationalSideExplanation;target:RelationalSideExplanation};
export type RelationalSideExplanation={join_kind:'inner'|'left';joined:boolean;where_state:'true'|'false'|'unknown'|'not_evaluated';
  outcome:RelationalRowOutcome;predicates:Array<RelationalPredicate & {field:string;observed:string|null;state:'true'|'false'|'unknown'}>};
export type JoinEvidenceSummary={schema_version:1;policy:'relational-join-evidence-v1';summary_hash:string;
  pins:{execution_policy:'relational_snapshot_v1';contract_version:1;contract_hash:string;snapshot_manifest_hash:string;
    database_hash:string;source_hashes:Record<'fact'|'dimension',string>;baseline_ir_hash:string;target_ir_hash:string;
    evidence_sha256:string;relational_binding:string;context_hash:string};context:RelationalContext;
  contract:Pick<RelationalContract,'fact'|'dimension'|'relationship'|'measure'|'measure_meaning'|'unit_label'|'scope'|'null_policy'>;
  definitions:Record<'baseline'|'target',{join:'inner'|'left';predicates:RelationalPredicate[]}>;
  join_proof:JoinProof;fact_rows:number;relationship_counts:Record<RelationalEvidence['join_membership'],number>;
  membership_counts:Record<'included_in_both'|'baseline_only'|'target_only'|'included_in_neither',number>;
  side_counts:Record<'baseline'|'target',Record<RelationalRowOutcome,number>>;changed_inclusion_rows:number;nonzero_contribution_rows:number;
  reconciliation:{status:'reconciled'|'not_applicable';baseline_value:string|null;target_value:string|null;
    delta_target_minus_baseline:string|null;residual:string|null;baseline_bookkeeping_sum:string;target_bookkeeping_sum:string;row_delta_sum:string};
  null_semantics:string;limitations:string[]};
export type JoinEvidencePage={run_id:string;snapshot_id:string;baseline_version_id:string;target_version_id:string;offset:number;limit:number;
  items:RelationalEvidence[];total:number;join_evidence:{summary:JoinEvidenceSummary;rows:RelationalRowExplanation[]}};
export type Workspace={id:string;name:string;role?:'owner'|'analyst'|'reader'|null};
export type MetricBinding={id:string;project_id:string;metric_id:string;definition_id:string;status:'active'|'candidate'|'retired';approved_version_id?:string|null;activated_at?:string|null;retired_at?:string|null;created_at:string;data?:Record<string,unknown>};
export type MetricStateTransition={id:string;project_id:string;metric_id:string;transition_kind:string;definition_id:string;approved_version_id?:string|null;previous_definition_id?:string|null;previous_approved_version_id?:string|null;comparison_run_id?:string|null;review_decision_id?:string|null;actor_id?:string|null;metadata:Record<string,unknown>;created_at:string};
export type ExternalMetricReference={id:string;project_id:string;metric_id:string;provider:string;external_id:string;created_at:string;data?:Record<string,unknown>};
export type MetricItem={id:string;project_id:string;display_name:string;importance:'critical'|'important'|'standard'|'unclassified';created_at:string;active_binding?:{definition_id:string;approved_version_id?:string;status:string}|null;data?:Record<string,unknown>};
export type MetricContractExpectationsV1 = {
  schema_version: 1;
  owner_label: string;
  meaning: string;
  unit_label: string;
  grain: string;
  expected_dimensions: Array<{ name: string; type: 'string' | 'boolean' | 'date' | 'timestamp' | 'decimal' }>;
  source_expectations: { mode: 'retail' | 'flat_snapshot'; source_label: string | null; contract_hash: string | null };
  materiality_policy: { absolute_threshold: string | null; relative_threshold: string | null };
};
export type RelationalGovernanceExpectations = {
  schema_version: 2;
  owner_label: string;
  meaning: string;
  unit_label: string;
  fact: { table: string; key: string; grain: string };
  dimension: { table: string; key: string; grain: string };
  relationship: { fact_foreign_key: string; dimension_key: string; cardinality: 'many_to_one';
    allowed_join_kinds: Array<'inner' | 'left'>; missing_dimension_policy: 'preserve_and_report' };
  expected_fields: Array<{ table: string; column: string; type: 'string' | 'boolean' | 'date' | 'timestamp' | 'decimal' }>;
  source_expectations: { mode: 'relational_snapshot'; execution_policy: 'relational_snapshot_v1'; source_label: string | null; contract_hash: string | null };
  materiality_policy: { absolute_threshold: string | null; relative_threshold: string | null };
};
export type MetricContractExpectations = MetricContractExpectationsV1 | RelationalGovernanceExpectations;
type MetricContractRevisionBase = {
  id: string; project_id: string; metric_id: string; version_number: number; contract_hash: string;
  metric_identity: { display_name: string; importance: MetricItem['importance'] };
  approved_definition: { definition_id: string; version_id: string; raw_hash: string | null; ir_hash: string | null; contract_hash: string | null };
  created_at: string; created_by: string;
  status?: 'proposed' | 'active' | 'superseded';
};
export type MetricContractRevision =
  | (MetricContractRevisionBase & { policy: 'metric-governance-v1'; schema_version: 1; expectations: MetricContractExpectationsV1 })
  | (MetricContractRevisionBase & { policy: 'metric-governance-v2'; schema_version: 2; expectations: RelationalGovernanceExpectations });
export type MetricContractCheckRow = { field: string; expected: unknown; observed: unknown; status: 'match' | 'mismatch' | 'unverified'; note: string; category?: string };
type MetricContractCheckBase = {
  id: string; contract_revision_id: string; contract_hash: string; snapshot_id: string;
  snapshot_manifest_hash: string | null; definition_version_id: string; report_hash: string;
  definition_raw_hash?: string | null; definition_ir_hash?: string | null;
  contract_status?: 'active' | 'historical_or_proposed'; created_by?: string | null;
  status: 'mismatch' | 'inconclusive' | 'metadata_match'; created_at: string;
  checks: MetricContractCheckRow[];
  limitations: string[];
};
export type MetricContractCheckV1 = MetricContractCheckBase & { policy: 'metric-governance-v1';
  declarations?: { owner_label: string; meaning: string; grain: string; materiality_policy: MetricContractExpectations['materiality_policy'] } };
export type MetricContractCheckV2 = MetricContractCheckBase & {
  policy: 'metric-governance-v2'; schema_version: 2; contract_version_number: number;
  definition_id: string; definition_version_number: number | null;
  definition_contract_id: string | null; definition_contract_hash: string | null; definition_execution_policy: string | null;
  snapshot_contract_hash: string | null; snapshot_execution_policy: string | null; approved_baseline_version_id: string;
  observed: { snapshot_contract_hash: string | null; definition_contract_hash: string | null; definition_join_kind: 'inner' | 'left' | null;
    relationship: Record<string, unknown> | null; fact: { table: string; key: string; grain: string } | null;
    dimension: { table: string; key: string; grain: string } | null; join_proof: Record<string, number | boolean> | null };
  assurance: Record<string, { status: string; note: string; source_hashes?: Record<string, string> | null }>;
  declarations: { owner_label: string; meaning: string; unit_label: string; fact_grain: string; dimension_grain: string;
    materiality_policy: MetricContractExpectations['materiality_policy'] };
};
export type MetricContractCheck = MetricContractCheckV1 | MetricContractCheckV2;
export type MetricContractHistory = {
  items: MetricContractRevision[]; active_contract: MetricContractRevision | null;
  version_token: string | null; next_offset: number | null;
  approvals: Array<{ id: string; contract_revision_id: string; previous_contract_id: string | null; approved_by: string; reason: string; created_at: string }>;
  recent_checks: MetricContractCheck[];
  capabilities: { can_propose: boolean; can_check: boolean; can_approve: boolean };
};
export type DbtRevisionItem = {
  id: string;
  project_id: string;
  manifest_hash: string;
  source_revision: { git_sha?: string; branch?: string; dirty?: boolean; [k: string]: unknown };
  invocation_id?: string | null;
  dbt_version?: string | null;
  generated_at?: string | null;
  created_at: string;
  data?: Record<string, unknown>;
};

export type MetricImpactSingle = {
  execution_status: 'executed' | 'refused';
  support_state?: string;
  refusal_reason?: string | null;
  diagnostics?: Record<string, unknown>;
  baseline_version_id?: string | null;
  target_version_id?: string | null;
  baseline_value: string | null;
  target_value: string | null;
  exact_delta: string | null;
  absolute_delta: string | null;
  relative_change_percent: string | null;
  residual?: string | null;
  run_id?: string | null;
  materiality?: { is_material?: boolean; exceeded?: boolean; reason?: string; details?: Record<string, unknown> } | null;
  evidence_summary?: Record<string, unknown> | null;
};

export type AffectedMetricDiffItem = {
  metric_id: string;
  display_name: string;
  importance: 'critical' | 'important' | 'standard' | 'unclassified';
  comparison_type: 'active' | 'candidate_vs_active';
  active_definition_id?: string | null;
  candidate_definition_id?: string | null;
  has_approved_baseline: boolean;
  approved_version_id?: string | null;
  baseline_revision_version_id?: string | null;
  target_version_id?: string | null;
  reference_snapshot_id: string;
  snapshot_hash?: string;
  comparison_run_id?: string | null;
  baseline_value?: string | null;
  target_value?: string | null;
  exact_delta?: string | null;
  absolute_delta?: string | null;
  relative_change_percent?: string | null;
  residual?: string | null;
  materiality?: { is_material?: boolean; exceeded?: boolean; reason?: string; details?: Record<string, unknown> } | null;
  support_state: string;
  execution_status: 'executed' | 'refused';
  refusal_reason?: string | null;
  diagnostics?: Record<string, unknown>;
  primary_impact?: MetricImpactSingle | null;
  approved_baseline_impact?: MetricImpactSingle | null;
  incremental_impact?: MetricImpactSingle | null;
  impact_id?: string;
  investigation_promotable?: boolean;
  evidence_reference?: { run_id: string; evidence_summary?: unknown } | null;
};

export type MetricDiffCoverage = {
  structural: {
    changed_resources_count: number;
    declared_metric_linked_resources_count: number;
    undeclared_changed_resources_count: number;
    supported_executable_resources_count: number;
    refused_resources_count: number;
    refusal_reasons: Record<string, number>;
  };
  declared_metrics: {
    total_affected: number;
    analyzable: number;
    refused: number;
    coverage_pct: number;
  };
  importance_weighted: Record<string, {
    affected: number;
    analyzable: number;
    refused: number;
    coverage_pct: number | null;
  }>;
  observable_structural_signals: {
    exposure_referenced_changed_resources: { count: number; resource_ids: string[] };
    explicit_dbt_metric_references: { count: number; resource_ids: string[] };
    other_observable_metadata: { tagged_changed_resources: { count: number; resource_ids: string[] } };
  };
};

export type MetricDiffRefusal = {
  metric_id: string | null;
  metric_display_name?: string | null;
  resource_id: string;
  support_state: string;
  exact_diagnostic: string;
  diagnostics?: Record<string, unknown>;
  refusal_stage: string;
};

export type DbtChangeSetResource = {
  unique_id: string;
  resource_type: string;
  name: string;
  change_category: 'modified' | 'added' | 'removed' | 'unchanged';
  change_dimensions: string[];
  summary_label: string;
  old_definition_id?: string | null;
  new_definition_id?: string | null;
  old_definition_version_id?: string | null;
  new_definition_version_id?: string | null;
  old_support_state?: string;
  new_support_state?: string;
  old_diagnostics?: string[];
  new_diagnostics?: string[];
  metric?: { id: string; display_name: string; importance: string } | null;
};

export type DbtChangeSet = {
  schema_version: 'metricguard_change_set_v1';
  project_id: string;
  baseline_revision_id: string;
  target_revision_id: string;
  counts: {
    total_resources: number;
    changed_count: number;
    unchanged_count: number;
    added_count: number;
    removed_count: number;
  };
  resources: DbtChangeSetResource[];
};

export type MetricDiffReport = {
  schema_version: 'metricguard_metric_diff_v1';
  id: string;
  project_id: string;
  baseline_revision_id: string;
  target_revision_id: string;
  baseline_manifest_hash: string;
  target_manifest_hash: string;
  snapshot_id: string;
  snapshot_hash: string;
  source_metadata: {
    baseline: { git_sha?: string; branch?: string; dirty?: boolean; [k: string]: unknown };
    target: { git_sha?: string; branch?: string; dirty?: boolean; [k: string]: unknown };
  };
  change_set_id: string;
  change_set_hash: string;
  impact_report_id: string;
  mode: 'advisory' | 'enforcing';
  overall_outcome: 'completed' | 'completed_with_material_drift' | 'completed_with_refusals' | 'completed_with_material_drift_and_refusals' | 'operational_failure';
  exit_code: number;
  counts: {
    total_changed_resources: number;
    unchanged_resources: number;
    total_declared_metrics_affected: number;
    successfully_analyzed_metrics: number;
    refused_metrics: number;
    undeclared_changed_resources: number;
    material_metrics: number;
  };
  coverage: MetricDiffCoverage;
  refusals: MetricDiffRefusal[];
  metrics: AffectedMetricDiffItem[];
  candidate_metrics?: AffectedMetricDiffItem[];
  undeclared_resources: Array<{ unique_id: string; resource_type?: string; change_category?: string; reason?: string; diagnostics?: unknown }>;
  request_hash?: string;
  created_at: string;
  data?: Record<string, unknown>;
};

export type MetricDiffItem = MetricDiffReport;

export type DbtMetricDiffInput = {
  baseline_revision_id: string;
  target_revision_id: string;
  snapshot_id: string;
  mode?: 'advisory' | 'enforcing';
  context?: Record<string, unknown> | null;
  materiality_policy?: {
    absolute_threshold?: string | number | null;
    relative_threshold?: number | null;
  } | null;
};

export type MetricImpactInvestigationInput = {
  metric_id: string;
  title?: string;
  decision?: string;
  actor_label?: string;
};

export type InvestigationDraft = {
  id: string;
  project_id: string;
  title: string;
  metric_id?: string | null;
  baseline_definition_id?: string | null;
  baseline_version_id?: string | null;
  target_definition_id?: string | null;
  target_version_id?: string | null;
  snapshot_id?: string | null;
  comparison_run_id?: string | null;
  status: 'draft' | 'running' | 'completed' | 'needs_decision' | 'refused';
  current_step?: string | null;
  origin?: string | null;
  ci_run_id?: string | null;
  ci_provenance?: Record<string, unknown> | null;
  refusal_reason?: string | null;
  diagnostics?: Record<string, unknown> | null;
  notes?: string | null;
  rationale?: string | null;
  decision_draft?: Record<string, unknown> | null;
  editor_state?: InvestigationEditorState | null;
  version_token: string;
  created_by?: string | null;
  created_at: string;
  updated_at: string;
};

export type InvestigationEditorState = {
  mode: 'existing' | 'adhoc';
  baseline_name: string;
  target_name: string;
  baseline_sql: string;
  target_sql: string;
};

export type InvestigationDraftCreateInput = {
  editor_state?: InvestigationEditorState | null;
  title?: string;
  metric_id?: string | null;
  baseline_definition_id?: string | null;
  baseline_version_id?: string | null;
  target_definition_id?: string | null;
  target_version_id?: string | null;
  snapshot_id?: string | null;
  comparison_run_id?: string | null;
  status?: 'draft' | 'running' | 'completed' | 'needs_decision';
  current_step?: string | null;
  notes?: string | null;
  rationale?: string | null;
  decision_draft?: Record<string, unknown> | null;
};

export type InvestigationDraftUpdateInput = {
  editor_state?: InvestigationEditorState | null;
  title?: string | null;
  metric_id?: string | null;
  baseline_definition_id?: string | null;
  baseline_version_id?: string | null;
  target_definition_id?: string | null;
  target_version_id?: string | null;
  snapshot_id?: string | null;
  comparison_run_id?: string | null;
  status?: 'draft' | 'running' | 'completed' | 'needs_decision' | null;
  current_step?: string | null;
  notes?: string | null;
  rationale?: string | null;
  decision_draft?: Record<string, unknown> | null;
  version_token?: string | null;
};

export type Bootstrap={project:{id:string;name:string};mode:string;runner_available:boolean;definitions:Definition[];snapshots:Snapshot[];imports:SnapshotImport[];metadata_imports:import('./MetadataImports').MetadataArtifact[];candidates:Candidate[];runs:Run[];baselines:Array<Record<string,unknown>>;dependencies:Array<Record<string,unknown>>;reviews:Array<Record<string,unknown>>;dataset_contracts:DatasetContract[];flat_contracts?:FlatContract[];relational_contracts?:RelationalContract[];column_mappings:ColumnMapping[];investigations:Investigation[];investigation_drafts?:InvestigationDraft[];metrics?:MetricItem[];metric_diffs?:MetricDiffReport[]};
export class ApiError extends Error {code:string;constructor(code:string,message:string){super(message);this.code=code}}
let activeWorkspaceId = '';

export function setActiveWorkspace(workspaceId: string) {
  activeWorkspaceId = workspaceId;
}

export function getActiveWorkspace(): string {
  return activeWorkspaceId;
}

// Credential is held in memory only: never persisted, placed in URLs, logged or echoed in errors.
let apiCredential: string | null = null;
let identityEpoch = 0;
const identityListeners = new Set<() => void>();

export function setApiCredential(key: string | null) {
  apiCredential = key && key.trim() ? key.trim() : null;
  identityEpoch += 1;
  identityListeners.forEach(listener => listener());
}
// A server-side browser session (HttpOnly cookie) can also identify the user.
// The key itself is never stored; only this flag is kept, in memory.
let sessionSignedIn = false;
export function setSessionSignedIn(value: boolean) {
  if (sessionSignedIn === value) return;
  sessionSignedIn = value;
  identityEpoch += 1;
  identityListeners.forEach(listener => listener());
}
export function hasApiCredential() { return apiCredential !== null || sessionSignedIn; }
export function getIdentityEpoch() { return identityEpoch; }
export function onIdentityChange(listener: () => void) {
  identityListeners.add(listener);
  return () => { identityListeners.delete(listener); };
}

function headerRecord(headers?: HeadersInit): Record<string, string> {
  const out: Record<string, string> = {};
  if (!headers) return out;
  if (typeof (headers as Headers).forEach === 'function' && !Array.isArray(headers)) {
    (headers as Headers).forEach((v, k) => { out[k] = v; });
  } else if (Array.isArray(headers)) {
    for (const [k, v] of headers) out[k] = v;
  } else {
    Object.assign(out, headers);
  }
  return out;
}

/** Same-origin MetricGuard API fetch. `path` is relative to /api/v1 and must start with '/'. */
export async function apiFetch(path: string, init?: RequestInit): Promise<Response> {
  if (!path.startsWith('/') || path.startsWith('//')) throw new ApiError('INVALID_PATH', 'Only same-origin API paths are allowed.');
  const reqHeaders = headerRecord(init?.headers);
  const isGlobal = path === '/workspaces' || path === '/health' || path.startsWith('/evaluation');
  const has = (name: string) => Object.keys(reqHeaders).some(k => k.toLowerCase() === name);
  if (!isGlobal && activeWorkspaceId && !has('x-project-id')) reqHeaders['X-Project-Id'] = activeWorkspaceId;
  if (apiCredential && !has('x-api-key') && !has('authorization')) reqHeaders['X-API-Key'] = apiCredential;
  // Lets the API tell this app's requests from forged cross-site ones when a session cookie is used.
  reqHeaders['X-MetricGuard-Client'] = 'web';
  const epoch = identityEpoch;
  const response = await fetch(`/api/v1${path}`, { ...init, headers: reqHeaders });
  if (epoch !== identityEpoch) {
    throw new ApiError('IDENTITY_CHANGED', 'The connected identity changed while this request was in flight; its response was discarded.');
  }
  return response;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await apiFetch(path, init);
  if (!response.ok) {
    let body: { error?: { code?: string; message?: string } } = {};
    try {
      body = await response.json();
    } catch {}
    throw new ApiError(
      body.error?.code || `HTTP_${response.status}`,
      body.error?.message || `Request failed (${response.status}).`
    );
  }
  return response.json();
}

export type ConnectedIdentity = {
  verified: boolean;
  session?: boolean;
  user: { id: string; name: string | null; email: string | null } | null;
  project_id: string | null;
  role: 'owner' | 'analyst' | 'reader' | null;
};

export const api = {
  download: async (path:string, filename:string) => {
    const response = await apiFetch(path);
    if (!response.ok) {
      const body = await response.json().catch(()=>({}));
      throw new ApiError(body.error?.code || `HTTP_${response.status}`,body.error?.message || 'Export failed.');
    }
    const blob = await response.blob(); const url = URL.createObjectURL(blob);
    const link = document.createElement('a');link.href=url;link.download=filename;link.click();
    setTimeout(()=>URL.revokeObjectURL(url),1000);
  },
  startSession: () => request<{ signed_in: boolean; expires_in_seconds: number }>('/auth/session', { method: 'POST' }),
  endSession: () => request<{ signed_in: boolean }>('/auth/session', { method: 'DELETE' }),
  identity: (projectId?: string) =>
    request<ConnectedIdentity>(projectId ? `/auth/identity?project_id=${encodeURIComponent(projectId)}` : '/auth/identity'),
  setActiveWorkspace,
  getActiveWorkspace,
  bootstrap: (projectId?: string) => {
    const pid = projectId || activeWorkspaceId;
    return request<Bootstrap>(pid ? `/bootstrap?project_id=${encodeURIComponent(pid)}` : '/bootstrap');
  },
  // Only workspaces the caller can open: verified memberships, or the local fallback's workspaces.
  listWorkspaces: () => request<Workspace[]>('/projects'),
  createWorkspace: (name: string, id?: string) =>
    request<Workspace>('/workspaces', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        name,
        id: id || name.toLowerCase().replace(/[^a-z0-9_-]/g, '-').replace(/-+/g, '-').replace(/^-|-$/g, ''),
      }),
    }),
  createFlatContract: (contract: Record<string, unknown>, projectId?: string) => {
    const pid = projectId || activeWorkspaceId;
    return request<FlatContract>(pid ? `/flat/contracts?project_id=${encodeURIComponent(pid)}` : '/flat/contracts', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...(pid ? { 'X-Project-Id': pid } : {}) },
      body: JSON.stringify(contract),
    });
  },
  post: <T>(path: string, body: unknown, headers?: Record<string, string>) =>
    request<T>(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...headers },
      body: JSON.stringify(body),
    }),
  createMetric: (
    pid: string | undefined,
    displayName: string,
    importance: 'critical' | 'important' | 'standard' | 'unclassified' = 'unclassified',
    id?: string
  ) => {
    const targetPid = pid || activeWorkspaceId;
    return request<MetricItem>(`/projects/${encodeURIComponent(targetPid)}/metrics`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ display_name: displayName, importance, id: id || undefined }),
    });
  },
  getMetric: (pid: string | undefined, metricId: string) => {
    const targetPid = pid || activeWorkspaceId;
    return request<MetricItem>(`/projects/${encodeURIComponent(targetPid)}/metrics/${encodeURIComponent(metricId)}`);
  },
  listMetricContracts: (pid: string, metricId: string, offset = 0) =>
    request<MetricContractHistory>(`/projects/${encodeURIComponent(pid)}/metrics/${encodeURIComponent(metricId)}/contracts?offset=${offset}`),
  proposeMetricContract: (pid: string, metricId: string, expectations: MetricContractExpectations, token: string | null, approvedVersionId: string) =>
    request<MetricContractRevision & { version_token: string }>(`/projects/${encodeURIComponent(pid)}/metrics/${encodeURIComponent(metricId)}/contracts`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ expectations, expected_version_token: token, expected_approved_version_id: approvedVersionId }),
    }),
  approveMetricContract: (pid: string, metricId: string, revisionId: string, token: string, reason: string) =>
    request<{ active_contract: MetricContractRevision; version_token: string }>(`/projects/${encodeURIComponent(pid)}/metrics/${encodeURIComponent(metricId)}/contracts/${encodeURIComponent(revisionId)}/approve`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ expected_version_token: token, reason }),
    }),
  checkMetricContract: (pid: string, metricId: string, snapshotId: string, revisionId: string, versionId?: string) =>
    request<MetricContractCheck>(`/projects/${encodeURIComponent(pid)}/metrics/${encodeURIComponent(metricId)}/contracts/checks`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ snapshot_id: snapshotId, contract_revision_id: revisionId, definition_version_id: versionId || null }),
    }),
  getMetricContractCheck: (pid: string, metricId: string, checkId: string) =>
    request<MetricContractCheck>(`/projects/${encodeURIComponent(pid)}/metrics/${encodeURIComponent(metricId)}/contracts/checks/${encodeURIComponent(checkId)}`),
  listMetricBindings: (pid: string | undefined, metricId: string) => {
    const targetPid = pid || activeWorkspaceId;
    return request<{ items: MetricBinding[] }>(
      `/projects/${encodeURIComponent(targetPid)}/metrics/${encodeURIComponent(metricId)}/bindings`
    );
  },
  bindMetricDefinition: (
    pid: string | undefined,
    metricId: string,
    definitionId: string,
    status: 'active' | 'candidate' | 'retired' = 'candidate',
    approvedVersionId?: string
  ) => {
    const targetPid = pid || activeWorkspaceId;
    return request<MetricBinding>(
      `/projects/${encodeURIComponent(targetPid)}/metrics/${encodeURIComponent(metricId)}/bindings`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ definition_id: definitionId, status, approved_version_id: approvedVersionId || null }),
      }
    );
  },
  promoteMetricDefinition: (
    pid: string | undefined,
    metricId: string,
    definitionId: string,
    approvedVersionId?: string,
    comparisonRunId?: string,
    reviewDecisionId?: string
  ) => {
    const targetPid = pid || activeWorkspaceId;
    return request<{ metric: MetricItem; binding: MetricBinding; transition: MetricStateTransition }>(
      `/projects/${encodeURIComponent(targetPid)}/metrics/${encodeURIComponent(metricId)}/promote`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          definition_id: definitionId,
          approved_version_id: approvedVersionId || null,
          comparison_run_id: comparisonRunId || null,
          review_decision_id: reviewDecisionId || null,
        }),
      }
    );
  },
  approveMetricVersion: (
    pid: string | undefined,
    metricId: string,
    approvedVersionId: string,
    comparisonRunId?: string,
    reviewDecisionId?: string
  ) => {
    const targetPid = pid || activeWorkspaceId;
    return request<{ metric: MetricItem; binding: MetricBinding; transition: MetricStateTransition }>(
      `/projects/${encodeURIComponent(targetPid)}/metrics/${encodeURIComponent(metricId)}/approve-version`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          approved_version_id: approvedVersionId,
          comparison_run_id: comparisonRunId || null,
          review_decision_id: reviewDecisionId || null,
        }),
      }
    );
  },
  listMetricTransitions: (pid: string | undefined, metricId: string) => {
    const targetPid = pid || activeWorkspaceId;
    return request<{ items: MetricStateTransition[] }>(
      `/projects/${encodeURIComponent(targetPid)}/metrics/${encodeURIComponent(metricId)}/transitions`
    );
  },
  upload: (form: FormData, projectId?: string) => {
    const pid = projectId || activeWorkspaceId;
    if (pid && !form.has('project_id')) form.append('project_id', pid);
    return request<Snapshot | SnapshotImport>(pid ? `/snapshots?project_id=${encodeURIComponent(pid)}` : '/snapshots', {
      method: 'POST',
      body: form,
      headers: pid ? { 'X-Project-Id': pid } : {},
    });
  },
  uploadMapped: (form: FormData, projectId?: string) => {
    const pid = projectId || activeWorkspaceId;
    if (pid && !form.has('project_id')) form.append('project_id', pid);
    return request<Snapshot | SnapshotImport>(
      pid ? `/mapped-snapshots?project_id=${encodeURIComponent(pid)}` : '/mapped-snapshots',
      {
        method: 'POST',
        body: form,
        headers: pid ? { 'X-Project-Id': pid } : {},
      }
    );
  },
  uploadFlat: (form: FormData, projectId?: string) => {
    const pid = projectId || activeWorkspaceId;
    if (pid && !form.has('project_id')) form.append('project_id', pid);
    return request<Snapshot | SnapshotImport>(
      pid ? `/flat/snapshots?project_id=${encodeURIComponent(pid)}` : '/flat/snapshots',
      {
        method: 'POST',
        body: form,
        headers: pid ? { 'X-Project-Id': pid } : {},
      }
    );
  },
  uploadRelational: (form: FormData) => request<RelationalSnapshot | SnapshotImport>('/relational/snapshots', {method:'POST', body:form}),
  relationalRun: (id:string) => request<RelationalRun>(`/comparisons/${encodeURIComponent(id)}`),
  relationalCase: (id:string) => request<RelationalCase>(`/investigations/${encodeURIComponent(id)}`),
  relationalEvidence: (id:string, offset:number) => request<{items:RelationalEvidence[];total:number}>(`/comparisons/${encodeURIComponent(id)}/evidence?offset=${offset}&limit=100`),
  relationalJoinEvidence: (id:string, offset:number) => request<JoinEvidencePage>(`/comparisons/${encodeURIComponent(id)}/join-evidence?offset=${offset}&limit=100`),
  snapshotImport: (id: string) => request<SnapshotImport>(`/snapshot-imports/${encodeURIComponent(id)}`),
  previewDataset: (form: FormData, projectId?: string) => {
    const pid = projectId || activeWorkspaceId;
    if (pid && !form.has('project_id')) form.append('project_id', pid);
    return request<DatasetPreviewRecord>(
      pid ? `/dataset-previews?project_id=${encodeURIComponent(pid)}` : '/dataset-previews',
      {
        method: 'POST',
        body: form,
        headers: pid ? { 'X-Project-Id': pid } : {},
      }
    );
  },
  datasetPreview: (id: string) => request<DatasetPreviewRecord>(`/dataset-previews/${encodeURIComponent(id)}`),
  investigation: (id: string) => request<InvestigationDetail>(`/investigations/${encodeURIComponent(id)}`),
  saveInvestigation: (body: {run_id: string; title: string; decision: string; actor_label: string}) =>
    request<Investigation>('/investigations', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)}),
  replayInvestigation: (id: string) => request<Run>(`/investigations/${encodeURIComponent(id)}/replays`, {
    method: 'POST', headers: {'Content-Type': 'application/json', 'Idempotency-Key': crypto.randomUUID()}, body: '{}',
  }),
  run: (id: string) => request<Run>(`/comparisons/${encodeURIComponent(id)}`),
  evidence: (id: string, offset: number, limit: number) =>
    request<{ items: Evidence[]; total: number }>(
      `/comparisons/${encodeURIComponent(id)}/evidence?offset=${offset}&limit=${limit}`
    ),
  evidenceSummary: (id: string) => request<EvidenceSummary>(`/comparisons/${encodeURIComponent(id)}/evidence-summary`),
  evidenceBreakdown: (id: string, column?: string) =>
    request<EvidenceBreakdown>(`/comparisons/${encodeURIComponent(id)}/evidence-breakdown${column ? `?column=${encodeURIComponent(column)}` : ''}`),
  evidenceView: (id: string, view: { offset: number; limit: number; changedOnly?: boolean; q?: string; order?: 'file' | 'impact'; withContext?: boolean }) => {
    const params = new URLSearchParams({ offset: String(view.offset), limit: String(view.limit) });
    if (view.withContext) params.set('with_context', 'true');
    if (view.changedOnly) params.set('changed_only', 'true');
    if (view.q?.trim()) params.set('q', view.q.trim());
    if (view.order === 'impact') params.set('order', 'impact');
    return request<{ items: FlatEvidence[]; total: number }>(`/comparisons/${encodeURIComponent(id)}/evidence?${params}`);
  },
  flatEvidence: (id: string, offset: number, limit: number) =>
    request<{ items: FlatEvidence[]; total: number }>(
      `/comparisons/${encodeURIComponent(id)}/evidence?offset=${offset}&limit=${limit}`
    ),
  executeMetricDiff: (pid: string | undefined, body: DbtMetricDiffInput) => {
    const targetPid = pid || activeWorkspaceId;
    return request<MetricDiffReport>(`/projects/${encodeURIComponent(targetPid)}/dbt/metric-diffs`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
  },
  getMetricDiffReport: (pid: string | undefined, diffId: string, format?: string) => {
    const targetPid = pid || activeWorkspaceId;
    return request<MetricDiffReport>(
      `/projects/${encodeURIComponent(targetPid)}/dbt/metric-diffs/${encodeURIComponent(diffId)}${
        format ? `?format=${encodeURIComponent(format)}` : ''
      }`
    );
  },
  getMetricDiffMarkdown: async (pid: string | undefined, diffId: string): Promise<string> => {
    const targetPid = pid || activeWorkspaceId;
    const res = await apiFetch(
      `/projects/${encodeURIComponent(targetPid)}/dbt/metric-diffs/${encodeURIComponent(diffId)}?format=markdown`,
      {
        headers: targetPid ? { 'X-Project-Id': targetPid } : {},
      }
    );
    if (!res.ok) {
      throw new ApiError(`HTTP_${res.status}`, `Failed to fetch Markdown report (${res.status})`);
    }
    return res.text();
  },
  listMetricDiffReports: (pid: string | undefined) => {
    const targetPid = pid || activeWorkspaceId;
    return request<{ items: MetricDiffReport[] }>(`/projects/${encodeURIComponent(targetPid)}/dbt/metric-diffs`);
  },
  listDbtRevisions: (pid: string | undefined) => {
    const targetPid = pid || activeWorkspaceId;
    return request<{ items: DbtRevisionItem[] }>(`/projects/${encodeURIComponent(targetPid)}/dbt/revisions`);
  },
  getDbtChangeSet: (pid: string | undefined, baselineRevisionId: string, targetRevisionId: string) => {
    const targetPid = pid || activeWorkspaceId;
    return request<DbtChangeSet>(
      `/projects/${encodeURIComponent(targetPid)}/dbt/change-sets?baseline_revision_id=${encodeURIComponent(
        baselineRevisionId
      )}&target_revision_id=${encodeURIComponent(targetRevisionId)}`
    );
  },
  promoteMetricImpactToInvestigation: (
    pid: string | undefined,
    impactId: string,
    body: MetricImpactInvestigationInput
  ) => {
    const targetPid = pid || activeWorkspaceId;
    return request<Investigation>(
      `/projects/${encodeURIComponent(targetPid)}/dbt/metric-impacts/${encodeURIComponent(impactId)}/investigations`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      }
    );
  },
  importDbtRevision: (pid: string | undefined, form: FormData) => {
    const targetPid = pid || activeWorkspaceId;
    return request<DbtRevisionItem>(`/projects/${encodeURIComponent(targetPid)}/dbt/imports`, {
      method: 'POST',
      body: form,
    });
  },
  uploadMetadata: (kind: 'dbt' | 'tmdl', file: File, projectId?: string) => {
    const pid = projectId || activeWorkspaceId;
    const form = new FormData();
    form.append('file', file);
    if (pid) form.append('project_id', pid);
    return request<import('./MetadataImports').MetadataArtifact>(`/metadata-imports/${kind}`, {
      method: 'POST',
      body: form,
      headers: pid ? { 'X-Project-Id': pid } : {},
    });
  },
  listInvestigationDrafts: (pid?: string) => {
    const targetPid = pid || activeWorkspaceId;
    return request<{ items: InvestigationDraft[] }>(
      `/projects/${encodeURIComponent(targetPid)}/investigation-drafts`
    );
  },
  createInvestigationDraft: (pid: string | undefined, body: InvestigationDraftCreateInput) => {
    const targetPid = pid || activeWorkspaceId;
    return request<InvestigationDraft>(`/projects/${encodeURIComponent(targetPid)}/investigation-drafts`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
  },
  getInvestigationDraft: (pid: string | undefined, draftId: string) => {
    const targetPid = pid || activeWorkspaceId;
    return request<InvestigationDraft>(
      `/projects/${encodeURIComponent(targetPid)}/investigation-drafts/${encodeURIComponent(draftId)}`
    );
  },
  updateInvestigationDraft: (
    pid: string | undefined,
    draftId: string,
    body: InvestigationDraftUpdateInput
  ) => {
    const targetPid = pid || activeWorkspaceId;
    return request<InvestigationDraft>(
      `/projects/${encodeURIComponent(targetPid)}/investigation-drafts/${encodeURIComponent(draftId)}`,
      {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      }
    );
  },
  deleteInvestigationDraft: (pid: string | undefined, draftId: string) => {
    const targetPid = pid || activeWorkspaceId;
    return request<{ deleted: boolean; id: string }>(
      `/projects/${encodeURIComponent(targetPid)}/investigation-drafts/${encodeURIComponent(draftId)}`,
      {
        method: 'DELETE',
      }
    );
  },
};
export const latest=(definition:Definition)=>definition.versions.at(-1);
export const versionName=(definitions:Definition[],id:string)=>{const definition=definitions.find(d=>d.versions.some(v=>v.id===id));const version=definition?.versions.find(v=>v.id===id);return definition&&version?`${definition.name} · v${version.version_number}`:id};
export const exact = (value: string | null | undefined, currency?: string | null) =>
  value == null ? 'Unavailable' : currency ? `${currency} ${groupDigits(value)}` : groupDigits(value);
export const compact = (value: string | null | undefined, currency?: string | null) => {
  if (value == null) return 'Unavailable';
  const n = Number(value);
  if (!Number.isFinite(n)) return exact(value, currency);
  const f = (v: number) =>
    new Intl.NumberFormat(currency === 'INR' ? 'en-IN' : 'en-US', { maximumFractionDigits: 2 }).format(v);
  if (currency === 'INR' && Math.abs(n) >= 1e7) return `₹${f(n / 1e7)} cr`;
  if (currency === 'INR' && Math.abs(n) >= 1e5) return `₹${f(n / 1e5)} L`;
  if (currency === 'INR') return `₹${f(n)}`;
  if (currency) return `${currency} ${f(n)}`;
  return f(n);
};
