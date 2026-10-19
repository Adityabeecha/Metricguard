from datetime import date
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Discriminator, Field, Tag, field_validator, model_validator


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid')


class DefinitionInput(Input):
    name: str = Field(default='Imported metric', min_length=1, max_length=160)
    source_label: str = Field(default='SQL import', min_length=1, max_length=160)
    owner_label: str = Field(default='Local analyst', min_length=1, max_length=160)
    raw_sql: str = Field(min_length=1, max_length=50000)
    time_column: str | None = Field(default=None, max_length=128)
    mapping_id: str | None = None
    source_changed_at: date | None = None
    effective_from: date | None = None


class Context(Input):
    start: date
    end_exclusive: date
    timezone: str = Field(min_length=1, max_length=64)
    currency: str = Field(min_length=1, max_length=8)
    unit: Literal['currency', 'count']
    grain: str = Field(min_length=1, max_length=100)
    scope: str = Field(min_length=1, max_length=200)
    contract_hash: str | None = Field(default=None, pattern=r'^[0-9a-f]{64}$')
    mapping_hash: str | None = Field(default=None, pattern=r'^[0-9a-f]{64}$')


class ComparisonInput(Input):
    baseline_version_id: str
    target_version_id: str
    snapshot_id: str
    context: Context
    relationship: Literal['candidate', 'expected_agreement', 'intentional_variant', 'unrelated'] = 'candidate'


class PairReview(Input):
    left_definition_id: str
    right_definition_id: str
    relation: Literal['expected_agreement', 'intentional_variant', 'unrelated', 'candidate']
    reason: str = Field(min_length=1, max_length=2000)
    actor_label: str = Field(min_length=1, max_length=160)


class BaselineInput(Input):
    version_id: str
    scope: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=1, max_length=2000)
    actor_label: str = Field(min_length=1, max_length=160)
    effective_from: date


class DependencyInput(Input):
    definition_id: str
    label: str = Field(min_length=1, max_length=160)
    kind: str = Field(default='dashboard', min_length=1, max_length=80)


class DatasetContractInput(Input):
    version: Literal[2]
    name: str = Field(min_length=1, max_length=160)
    currency: str = Field(min_length=3, max_length=8)
    timezone: str = Field(min_length=1, max_length=64)
    scope: str = Field(min_length=1, max_length=200)
    grain: str = Field(min_length=1, max_length=100)
    cutoff_at: date
    null_policy: Literal['blank_nullable', 'literal_NULL', 'native_null']
    row_key: Literal['order_id']
    entity_key: Literal['customer_id']
    decimal_scale: Literal[2]
    schema_: dict[str, str] = Field(alias='schema')


class ColumnMappingInput(Input):
    contract_id: str
    columns: dict[str, str]


class DecimalColumnInput(Input):
    name: str = Field(min_length=1, max_length=128)
    precision: int = Field(ge=1, le=38)
    scale: int = Field(ge=0, le=18)
    unit_label: str = Field(min_length=1, max_length=80)


class MeasureExpressionInput(Input):
    op: Literal['sub']
    minuend: str = Field(min_length=1, max_length=128)
    subtrahend: str = Field(min_length=1, max_length=128)


class FlatContractInput(Input):
    version: Literal[3, 4, 5, 6, 7, 8]
    mode: Literal['flat_snapshot']
    name: str = Field(min_length=1, max_length=160)
    row_key: str
    measure: str | None = None
    precision: int | None = Field(default=None, ge=1, le=38)
    scale: int = Field(ge=0, le=18)
    categories: list[str] = Field(max_length=8)
    predicate_policy: Literal['flat-boolean-v1'] | None = None
    measure_policy: Literal['flat-subtraction-v1', 'flat-case-v1', 'flat-multi-measure-v1'] | None = None
    decimal_columns: list[DecimalColumnInput] | None = None
    measure_expression: MeasureExpressionInput | None = None
    dimension_types: dict[str, str] | None = None
    nullable_categories: list[str] | None = None
    measure_meaning: str = Field(min_length=1, max_length=300)
    unit_label: str = Field(min_length=1, max_length=80)
    values_comparable: Literal[True]
    scope: Literal['entire_supplied_snapshot']
    null_policy: Literal['blank_nullable', 'literal_NULL', 'native_null']

    def to_contract_payload(self) -> dict:
        raw = self.model_dump(mode='json')
        v3_fields = {
            'version', 'mode', 'name', 'row_key', 'measure', 'precision', 'scale',
            'categories', 'measure_meaning', 'unit_label', 'values_comparable',
            'scope', 'null_policy'
        }
        v4_fields = v3_fields | {'dimension_types', 'nullable_categories'}
        v5_fields = v4_fields | {'predicate_policy'}
        v6_fields = {
            'version', 'mode', 'name', 'row_key', 'scale', 'categories',
            'dimension_types', 'nullable_categories', 'measure_meaning',
            'unit_label', 'values_comparable', 'scope', 'null_policy',
            'measure_policy', 'predicate_policy', 'decimal_columns',
            'measure_expression'
        }
        allowable_by_version = {
            3: v3_fields,
            4: v4_fields,
            5: v5_fields,
            6: v6_fields,
            7: v5_fields | {'measure_policy'},
            8: v6_fields - {'measure_expression'},
        }
        allowable = allowable_by_version[self.version]
        payload = {}
        for key, value in raw.items():
            if key in self.model_fields_set:
                payload[key] = value
            elif key in allowable and value is not None:
                payload[key] = value
            elif key in allowable and key in ('nullable_categories', 'predicate_policy', 'measure_policy'):
                pass
            elif key in allowable:
                payload[key] = value
        return payload


class FlatDefinitionInput(Input):
    name: str = Field(min_length=1, max_length=160)
    raw_sql: str = Field(min_length=1, max_length=50000)
    contract_id: str


class FlatMatchCountInput(Input):
    raw_sql: str = Field(min_length=1, max_length=50000)


class FlatContext(Input):
    mode: Literal['flat_snapshot']
    scope: Literal['entire_supplied_snapshot']
    contract_hash: str = Field(pattern=r'^[0-9a-f]{64}$')
    unit_label: str = Field(min_length=1, max_length=80)
    values_comparable: Literal[True]


class FlatComparisonInput(Input):
    baseline_version_id: str
    target_version_id: str
    snapshot_id: str
    context: FlatContext
    relationship: Literal['candidate', 'expected_agreement', 'intentional_variant', 'unrelated'] = 'candidate'


class FlatSnapshotChangeInput(Input):
    """One flat definition evaluated on a baseline and a target load of the same dataset contract."""
    version_id: str
    baseline_snapshot_id: str
    target_snapshot_id: str
    context: FlatContext


class RelationalTableInput(Input):
    table: str
    key: str
    grain: str = Field(min_length=1, max_length=300)
    categories: list[str] = Field(max_length=8)


class RelationalMeasureInput(Input):
    column: str
    precision: int = Field(strict=True, ge=1, le=38)
    scale: int = Field(strict=True, ge=0, le=18)


class RelationalRelationshipInput(Input):
    fact_foreign_key: str
    dimension_key: str
    cardinality: Literal['many_to_one']
    missing_dimension_policy: Literal['preserve_and_report']


class RelationalContractInput(Input):
    version: int = Field(strict=True, ge=1, le=1)
    mode: Literal['relational_snapshot']
    policy: Literal['relational-snapshot-v1']
    name: str = Field(min_length=1, max_length=160)
    fact: RelationalTableInput
    dimension: RelationalTableInput
    relationship: RelationalRelationshipInput
    measure: RelationalMeasureInput
    measure_meaning: str = Field(min_length=1, max_length=300)
    unit_label: str = Field(min_length=1, max_length=80)
    values_comparable: Literal[True]
    scope: Literal['entire_supplied_snapshot']
    null_policy: Literal['blank_nullable', 'literal_NULL']

    @field_validator('values_comparable', mode='before')
    @classmethod
    def explicit_comparable(cls, value):
        if value is not True:
            raise ValueError('Explicit boolean true confirmation is required.')
        return value


class RelationalLookupInput(Input):
    role: str = Field(min_length=1, max_length=64)
    table: str
    key: str
    grain: str = Field(min_length=1, max_length=300)
    categories: list[str] = Field(max_length=8)
    fact_foreign_key: str


class RelationalStarContractInput(Input):
    """Contract v2: one fact table and one to four lookup tables, each on its own foreign key."""
    version: int = Field(strict=True, ge=2, le=2)
    mode: Literal['relational_snapshot']
    policy: Literal['relational-star-v1']
    name: str = Field(min_length=1, max_length=160)
    fact: RelationalTableInput
    dimensions: list[RelationalLookupInput] = Field(min_length=1, max_length=4)
    measure: RelationalMeasureInput
    measure_meaning: str = Field(min_length=1, max_length=300)
    unit_label: str = Field(min_length=1, max_length=80)
    values_comparable: Literal[True]
    scope: Literal['entire_supplied_snapshot']
    null_policy: Literal['blank_nullable', 'literal_NULL']

    @field_validator('values_comparable', mode='before')
    @classmethod
    def explicit_comparable(cls, value):
        if value is not True:
            raise ValueError('Explicit boolean true confirmation is required.')
        return value


class RelationalContext(FlatContext):
    mode: Literal['relational_snapshot']

    @field_validator('values_comparable', mode='before')
    @classmethod
    def explicit_comparable(cls, value):
        if value is not True:
            raise ValueError('Explicit boolean true confirmation is required.')
        return value


class RelationalComparisonInput(FlatComparisonInput):
    context: RelationalContext


class InvestigationInput(Input):
    title: str = Field(min_length=1, max_length=160)
    run_id: str
    snapshot_id: str | None = None
    decision: str = Field(min_length=1, max_length=2000)
    actor_label: str = Field(min_length=1, max_length=160)


class UserCreateInput(Input):
    name: str = Field(min_length=1, max_length=160)
    email: str = Field(min_length=3, max_length=254)


class ProjectCreateInput(Input):
    name: str = Field(min_length=1, max_length=160)
    id: str | None = Field(default=None, max_length=64, pattern=r'^[a-z0-9_-]+$')


class ProjectMemberInput(Input):
    user_id: str
    role: Literal['owner', 'analyst', 'reader']


class ReviewRequestInput(Input):
    definition_id: str | None = None
    baseline_version_id: str | None = None
    target_version_id: str | None = None
    run_id: str | None = None
    rationale: str = Field(min_length=1, max_length=2000)
    is_intentional_variant: bool = False
    variant_rationale: str | None = Field(default=None, max_length=2000)
    effective_time: str | None = Field(default=None, max_length=64)


class DecisionInput(Input):
    review_request_id: str | None = None
    supersedes_version_id: str | None = None
    approved_version_id: str
    decision: Literal['approved', 'rejected', 'superseded', 'variant_accepted']
    rationale: str = Field(min_length=1, max_length=2000)


class MonitoringPolicyInput(Input):
    name: str = Field(min_length=1, max_length=160)
    baseline_version_id: str
    target_version_id: str
    snapshot_id: str | None = None
    schedule_type: Literal['event_triggered', 'scheduled'] = 'event_triggered'
    absolute_threshold: str | None = None
    relative_threshold: float | None = None
    suppress_intentional_variants: bool = True
    enabled: bool = True


class AlertAcknowledgeInput(Input):
    note: str = Field(default='', max_length=1000)


class ProjectQuotaInput(Input):
    max_snapshots: int | None = None
    max_storage_bytes: int | None = None


class PostgresExtractionInput(Input):
    source_uri: str = Field(min_length=1, max_length=2000)
    schema_name: str = Field(default='public', min_length=1, max_length=128)
    table_name: str = Field(min_length=1, max_length=128)
    columns: list[str] | dict[str, str] | None = None
    contract_id: str
    mapping_id: str | None = None
    cutoff_column: str = Field(min_length=1, max_length=128)
    cutoff_value: str = Field(min_length=1, max_length=64)
    extraction_id: str | None = Field(default=None, max_length=64)
    snapshot_name: str | None = Field(default=None, max_length=160)
    row_limit: int | None = Field(default=1_000_000, ge=1, le=10_000_000)
    sample_rows: int | None = Field(default=None, ge=1, le=100_000)


class MetricCreateInput(Input):
    display_name: str = Field(min_length=1, max_length=160)
    importance: Literal['critical', 'important', 'standard', 'unclassified'] = 'unclassified'
    id: str | None = Field(default=None, max_length=128)


class MetricUpdateInput(Input):
    display_name: str | None = Field(default=None, min_length=1, max_length=160)
    importance: Literal['critical', 'important', 'standard', 'unclassified'] | None = None


class MetricContractDimension(Input):
    name: str = Field(pattern=r'^[A-Za-z_][A-Za-z0-9_]{0,127}$')
    type: Literal['string', 'boolean', 'date', 'timestamp', 'decimal']


class MetricContractSource(Input):
    mode: Literal['retail', 'flat_snapshot']
    source_label: str | None = Field(default=None, min_length=1, max_length=160)
    contract_hash: str | None = Field(default=None, pattern=r'^[0-9a-f]{64}$')


class MetricContractMateriality(Input):
    absolute_threshold: str | None = Field(default=None, max_length=128)
    relative_threshold: str | None = Field(default=None, max_length=128)

    @field_validator('absolute_threshold', 'relative_threshold')
    @classmethod
    def exact_nonnegative_threshold(cls, value):
        if value is not None:
            from packages.metricguard_core.numeric import parse_fixed_point
            coefficient, _ = parse_fixed_point(value)
            if coefficient < 0:
                raise ValueError('Materiality thresholds must be nonnegative exact decimals')
        return value


class MetricContractExpectations(Input):
    schema_version: Literal[1] = 1
    owner_label: str = Field(min_length=1, max_length=160)
    meaning: str = Field(min_length=1, max_length=600)
    unit_label: str = Field(min_length=1, max_length=200)
    grain: str = Field(min_length=1, max_length=200)
    expected_dimensions: list[MetricContractDimension] = Field(default_factory=list, max_length=24)
    source_expectations: MetricContractSource
    materiality_policy: MetricContractMateriality = Field(default_factory=MetricContractMateriality)

    @field_validator('schema_version', mode='before')
    @classmethod
    def exact_version(cls, value):
        if type(value) is not int or value != 1:
            raise ValueError('Only integer schema version 1 is supported')
        return value

    @field_validator('owner_label', 'meaning', 'unit_label', 'grain')
    @classmethod
    def declared_text(cls, value):
        if not value.strip():
            raise ValueError('A nonblank declaration is required')
        return value.strip()

    @field_validator('expected_dimensions')
    @classmethod
    def distinct_dimensions(cls, value):
        if len({d.name for d in value}) != len(value):
            raise ValueError('Expected dimension names must be unique')
        return sorted(value, key=lambda d: d.name)


# ---------------------------------------------------------------------------
# metric-governance-v2 (schema_version=2): relational_snapshot_v1 expectations.
# Governance metadata only; it never alters relational analytical contracts.
# ---------------------------------------------------------------------------
_RELATIONAL_IDENT = r'^[a-z_][a-z0-9_]{0,63}$'


def _declared(value):
    if not value.strip():
        raise ValueError('A nonblank declaration is required')
    return value.strip()


class RelationalGovernanceTable(Input):
    table: str = Field(pattern=_RELATIONAL_IDENT)
    key: str = Field(pattern=_RELATIONAL_IDENT)
    grain: str = Field(min_length=1, max_length=300)

    @field_validator('grain')
    @classmethod
    def declared_grain(cls, value):
        return _declared(value)


class RelationalGovernanceRelationship(Input):
    fact_foreign_key: str = Field(pattern=_RELATIONAL_IDENT)
    dimension_key: str = Field(pattern=_RELATIONAL_IDENT)
    cardinality: Literal['many_to_one']
    allowed_join_kinds: list[Literal['inner', 'left']] = Field(min_length=1, max_length=2)
    missing_dimension_policy: Literal['preserve_and_report']

    @field_validator('allowed_join_kinds')
    @classmethod
    def canonical_join_kinds(cls, value):
        if len(set(value)) != len(value):
            raise ValueError('Allowed join kinds must be distinct')
        return sorted(value)


class RelationalGovernanceField(Input):
    table: str = Field(pattern=_RELATIONAL_IDENT)
    column: str = Field(pattern=_RELATIONAL_IDENT)
    type: Literal['string', 'boolean', 'date', 'timestamp', 'decimal']


class RelationalGovernanceSource(Input):
    mode: Literal['relational_snapshot']
    execution_policy: Literal['relational_snapshot_v1']
    source_label: str | None = Field(default=None, min_length=1, max_length=160)
    contract_hash: str | None = Field(default=None, pattern=r'^[0-9a-f]{64}$')


class RelationalMetricContractExpectations(Input):
    schema_version: Literal[2]
    owner_label: str = Field(min_length=1, max_length=160)
    meaning: str = Field(min_length=1, max_length=600)
    unit_label: str = Field(min_length=1, max_length=200)
    fact: RelationalGovernanceTable
    dimension: RelationalGovernanceTable
    relationship: RelationalGovernanceRelationship
    expected_fields: list[RelationalGovernanceField] = Field(default_factory=list, max_length=16)
    source_expectations: RelationalGovernanceSource
    materiality_policy: MetricContractMateriality = Field(default_factory=MetricContractMateriality)

    @field_validator('schema_version', mode='before')
    @classmethod
    def exact_version(cls, value):
        if type(value) is not int or value != 2:
            raise ValueError('Relational governance requires integer schema version 2')
        return value

    @field_validator('owner_label', 'meaning', 'unit_label')
    @classmethod
    def declared_text(cls, value):
        return _declared(value)

    @field_validator('expected_fields')
    @classmethod
    def canonical_fields(cls, value):
        if len({(f.table, f.column) for f in value}) != len(value):
            raise ValueError('Expected qualified fields must be unique')
        return sorted(value, key=lambda f: (f.table, f.column))

    @model_validator(mode='after')
    def unambiguous_references(self):
        if self.fact.table == self.dimension.table:
            raise ValueError('Fact and dimension tables must be distinct so qualified references are unambiguous')
        if self.relationship.dimension_key != self.dimension.key:
            raise ValueError('The relationship must target the declared dimension key')
        keys = {self.fact.table: {self.fact.key, self.relationship.fact_foreign_key},
                self.dimension.table: {self.dimension.key}}
        for item in self.expected_fields:
            if item.table not in keys:
                raise ValueError(f'Qualified field {item.table}.{item.column} must name the declared fact or dimension table')
            if item.column in keys[item.table]:
                raise ValueError(f'{item.table}.{item.column} is a key; expected fields must be categorical')
        return self


def _governance_schema(value):
    version = value.get('schema_version', 1) if isinstance(value, dict) else getattr(value, 'schema_version', 1)
    return 'v2' if type(version) is int and version == 2 else 'v1'


GovernanceExpectations = Annotated[
    Union[Annotated[MetricContractExpectations, Tag('v1')], Annotated[RelationalMetricContractExpectations, Tag('v2')]],
    Discriminator(_governance_schema)]


class MetricContractProposalInput(Input):
    expectations: GovernanceExpectations
    expected_version_token: str | None = Field(max_length=64)
    expected_approved_version_id: str = Field(min_length=1, max_length=128)


class MetricContractApprovalInput(Input):
    expected_version_token: str = Field(min_length=1, max_length=64)
    reason: str = Field(min_length=1, max_length=1000)


class MetricContractCheckInput(Input):
    snapshot_id: str = Field(min_length=1, max_length=128)
    definition_version_id: str | None = Field(default=None, min_length=1, max_length=128)
    contract_revision_id: str | None = Field(default=None, min_length=1, max_length=128)


class ExternalMetricReferenceInput(Input):
    provider: str = Field(default='dbt', min_length=1, max_length=64)
    external_id: str = Field(min_length=1, max_length=256)


class MetricDefinitionBindingInput(Input):
    definition_id: str = Field(min_length=1, max_length=128)
    status: Literal['active', 'candidate', 'retired'] = 'candidate'
    approved_version_id: str | None = Field(default=None, max_length=128)


class MetricPromotionInput(Input):
    definition_id: str = Field(min_length=1, max_length=128)
    approved_version_id: str | None = Field(default=None, max_length=128)
    comparison_run_id: str | None = Field(default=None, max_length=128)
    review_decision_id: str | None = Field(default=None, max_length=128)


class MetricVersionApprovalInput(Input):
    approved_version_id: str = Field(min_length=1, max_length=128)
    comparison_run_id: str | None = Field(default=None, max_length=128)
    review_decision_id: str | None = Field(default=None, max_length=128)


class DbtChangeSetInput(Input):
    baseline_revision_id: str = Field(min_length=1, max_length=128)
    target_revision_id: str = Field(min_length=1, max_length=128)


class DbtMetricImpactInput(Input):
    baseline_revision_id: str = Field(min_length=1, max_length=128)
    target_revision_id: str = Field(min_length=1, max_length=128)
    snapshot_id: str = Field(min_length=1, max_length=128)
    context: dict | None = None
    materiality_policy: dict | None = None


class MetricImpactInvestigationInput(Input):
    metric_id: str = Field(min_length=1, max_length=128)
    title: str | None = Field(default=None, max_length=160)
    decision: str = Field(default='Promoted from CI metric impact evaluation', min_length=1, max_length=2000)
    actor_label: str = Field(default='CI / Analyst', min_length=1, max_length=160)


class DbtMetricDiffInput(Input):
    baseline_revision_id: str = Field(min_length=1, max_length=128)
    target_revision_id: str = Field(min_length=1, max_length=128)
    snapshot_id: str = Field(min_length=1, max_length=128)
    mode: Literal['advisory', 'enforcing'] = 'advisory'
    context: dict | None = None
    materiality_policy: dict | None = None


class InvestigationEditorState(Input):
    """Bounded verbatim editor text, including temporarily unsupported SQL."""
    mode: Literal['existing', 'adhoc']
    baseline_name: str = Field(max_length=160)
    target_name: str = Field(max_length=160)
    baseline_sql: str = Field(max_length=50000)
    target_sql: str = Field(max_length=50000)


class InvestigationDraftCreateInput(Input):
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=160)
    title: str = Field(default='Untitled investigation', min_length=1, max_length=200)
    metric_id: str | None = Field(default=None, max_length=128)
    baseline_definition_id: str | None = Field(default=None, max_length=128)
    baseline_version_id: str | None = Field(default=None, max_length=128)
    target_definition_id: str | None = Field(default=None, max_length=128)
    target_version_id: str | None = Field(default=None, max_length=128)
    snapshot_id: str | None = Field(default=None, max_length=128)
    comparison_run_id: str | None = Field(default=None, max_length=128)
    status: Literal['draft', 'running', 'completed', 'needs_decision'] = 'draft'
    current_step: str | None = Field(default='config', max_length=64)
    notes: str | None = Field(default=None, max_length=5000)
    rationale: str | None = Field(default=None, max_length=5000)
    decision_draft: dict | None = None
    editor_state: InvestigationEditorState | None = None


class InvestigationDraftUpdateInput(Input):
    title: str | None = Field(default=None, max_length=200)
    metric_id: str | None = Field(default=None, max_length=128)
    baseline_definition_id: str | None = Field(default=None, max_length=128)
    baseline_version_id: str | None = Field(default=None, max_length=128)
    target_definition_id: str | None = Field(default=None, max_length=128)
    target_version_id: str | None = Field(default=None, max_length=128)
    snapshot_id: str | None = Field(default=None, max_length=128)
    comparison_run_id: str | None = Field(default=None, max_length=128)
    status: Literal['draft', 'running', 'completed', 'needs_decision'] | None = None
    current_step: str | None = Field(default=None, max_length=64)
    notes: str | None = Field(default=None, max_length=5000)
    rationale: str | None = Field(default=None, max_length=5000)
    decision_draft: dict | None = None
    editor_state: InvestigationEditorState | None = None
    version_token: str | None = Field(default=None, max_length=128)




class WarehouseExtractionInput(Input):
    """Snowflake or BigQuery extraction of one table into a single-table dataset (see warehouse_rest)."""
    contract_id: str
    snapshot_name: str = Field(min_length=1, max_length=160)
    token: str = Field(min_length=8, max_length=8192, repr=False)
    table: str = Field(min_length=1, max_length=128)
    # Snowflake
    account: str | None = Field(default=None, max_length=128)
    database: str | None = Field(default=None, max_length=128)
    schema_name: str | None = Field(default=None, max_length=128)
    warehouse: str | None = Field(default=None, max_length=128)
    role: str | None = Field(default=None, max_length=128)
    token_type: Literal['PROGRAMMATIC_ACCESS_TOKEN', 'OAUTH'] = 'PROGRAMMATIC_ACCESS_TOKEN'
    # BigQuery
    project: str | None = Field(default=None, max_length=64)
    dataset: str | None = Field(default=None, max_length=1024)
    location: str | None = Field(default=None, max_length=32)
    # Both
    sample_rows: int | None = Field(default=None, ge=1, le=5_000_000)
    cutoff_column: str | None = Field(default=None, max_length=128)
    cutoff_type: Literal['date', 'timestamp'] | None = None
    cutoff_value: str | None = Field(default=None, max_length=32)
