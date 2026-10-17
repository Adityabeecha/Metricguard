export interface SqlDiffSummary {
  hasDifferences: boolean;
  summary: string;
  addedFilters: string[];
  removedFilters: string[];
}

export function humanizeMembership(
  membership?: string | null,
  baselineIncluded?: boolean,
  targetIncluded?: boolean
): string {
  if (membership) {
    const norm = membership.toLowerCase().replace(/[^a-z_]/g, '');
    if (norm.includes('neither') || norm.includes('excluded')) return 'Neither';
    if (norm.includes('both') || norm === 'in_both') return 'Both';
    if (norm.includes('baseline')) return 'Baseline only';
    if (norm.includes('target')) return 'Target only';
  }
  if (baselineIncluded && targetIncluded) return 'Both';
  if (baselineIncluded && !targetIncluded) return 'Baseline only';
  if (!baselineIncluded && targetIncluded) return 'Target only';
  return 'Neither';
}

export function computeSqlDifferences(baselineSql: string, targetSql: string): SqlDiffSummary {
  const normBase = (baselineSql || '').trim();
  const normTarget = (targetSql || '').trim();

  if (normBase === normTarget) {
    return {
      hasDifferences: false,
      summary: 'SQL definitions are identical across revisions.',
      addedFilters: [],
      removedFilters: [],
    };
  }

  // Extract simple WHERE clauses
  const extractWhere = (sql: string): string[] => {
    const match = sql.match(/WHERE\s+([\s\S]+?)(?:GROUP\s+BY|ORDER\s+BY|LIMIT|$)/i);
    if (!match) return [];
    return match[1]
      .split(/\s+AND\s+/i)
      .map((s) => s.trim().replace(/^\(+|\)+$/g, ''))
      .filter(Boolean);
  };

  const baseFilters = extractWhere(normBase);
  const targetFilters = extractWhere(normTarget);

  const addedFilters = targetFilters.filter(
    (tf) => !baseFilters.some((bf) => bf.toLowerCase() === tf.toLowerCase())
  );
  const removedFilters = baseFilters.filter(
    (bf) => !targetFilters.some((tf) => tf.toLowerCase() === bf.toLowerCase())
  );

  let summary = 'Target definition introduces query changes.';
  if (addedFilters.length > 0 && removedFilters.length === 0) {
    summary = `Target adds filter constraint: ${addedFilters.join(' AND ')}`;
  } else if (removedFilters.length > 0 && addedFilters.length === 0) {
    summary = `Target removes filter constraint: ${removedFilters.join(' AND ')}`;
  } else if (addedFilters.length > 0 && removedFilters.length > 0) {
    summary = `Target modifies filter logic (${addedFilters.length} added, ${removedFilters.length} removed).`;
  }

  return {
    hasDifferences: true,
    summary,
    addedFilters,
    removedFilters,
  };
}

export function isExactZero(value: string | null | undefined): boolean {
  return typeof value === 'string' && /^[+-]?0+(?:\.0+)?$/.test(value);
}

export function isDefinedComparison(result: { baseline_value?: string | null; target_value?: string | null; delta_target_minus_baseline?: string | null } | null | undefined): boolean {
  return Boolean(result && result.baseline_value != null && result.target_value != null && result.delta_target_minus_baseline != null);
}

export function classifyResidual(residual: string | null | undefined, currency: string = ''): string {
  if (residual == null || residual === '') {
    return 'Residual not applicable';
  }
  if (isExactZero(residual)) {
    return 'No unexplained residual';
  }
  const prefix = !currency ? '' : currency === 'INR' ? '₹' : `${currency} `;
  return `${prefix}${residual}`;
}

export interface InvestigationStatusBadge {
  label: string;
  className: string;
  tone: 'blue' | 'amber' | 'red';
}

export function formatInvestigationStatus(
  status: string,
  isGovernedCandidate: boolean = false,
  hasDecisionRecorded: boolean = false
): InvestigationStatusBadge {
  const norm = (status || '').toLowerCase();
  if (norm === 'draft') {
    return { label: 'Draft', className: 'status-pill draft', tone: 'blue' };
  }
  if (norm === 'running' || norm === 'queued' || norm === 'pending') {
    return { label: 'Running', className: 'status-pill running', tone: 'blue' };
  }
  if (norm === 'needs_decision' || (isGovernedCandidate && !hasDecisionRecorded)) {
    return { label: 'Needs decision', className: 'status-pill needs-decision', tone: 'amber' };
  }
  if (norm === 'completed' || norm === 'saved') {
    return { label: 'Completed', className: 'status-pill completed', tone: 'blue' };
  }
  return { label: status || 'Draft', className: 'status-pill draft', tone: 'blue' };
}

export function detectExecutionPath(
  baselineVersion?: { execution_policy?: string } | null,
  targetVersion?: { execution_policy?: string } | null,
  snapshot?: { execution_policy?: string } | null
): 'flat' | 'standard' {
  if (
    baselineVersion?.execution_policy === 'flat_snapshot_v1' ||
    targetVersion?.execution_policy === 'flat_snapshot_v1' ||
    snapshot?.execution_policy === 'flat_snapshot_v1'
  ) {
    return 'flat';
  }
  return 'standard';
}

export interface SnapshotContractMetadata {
  unitLabel: string;
  scope: string;
  contractHash: string | null;
  measure?: string;
  isFlat: boolean;
}

export function getSnapshotContractMetadata(
  snapshot?: any | null,
  data?: { flat_contracts?: any[]; dataset_contracts?: any[] }
): SnapshotContractMetadata {
  if (!snapshot) {
    return {
      unitLabel: '',
      scope: 'entire_supplied_snapshot',
      contractHash: null,
      isFlat: false,
    };
  }

  const flatContract = data?.flat_contracts?.find(
    (c: any) =>
      c.contract_hash === snapshot.contract_hash ||
      (snapshot.manifest as any)?.contract?.contract_hash === c.contract_hash
  );

  const datasetContract = data?.dataset_contracts?.find(
    (c: any) => c.contract_hash === snapshot.contract_hash
  );

  const isFlat =
    snapshot.execution_policy === 'flat_snapshot_v1' ||
    Boolean(flatContract) ||
    Boolean(snapshot.preparation);

  // Derive unit label strictly from frozen metadata - NEVER fallback to INR
  const unitLabel =
    snapshot.unit_label ||
    (snapshot.manifest as any)?.unit_label ||
    (snapshot.manifest as any)?.contract?.unit_label ||
    flatContract?.unit_label ||
    snapshot.currency ||
    datasetContract?.currency ||
    '';

  const scope =
    snapshot.scope ||
    flatContract?.scope ||
    (snapshot.manifest as any)?.scope ||
    (snapshot.manifest as any)?.contract?.scope ||
    datasetContract?.scope ||
    'entire_supplied_snapshot';

  const contractHash =
    snapshot.contract_hash ||
    flatContract?.contract_hash ||
    datasetContract?.contract_hash ||
    null;

  const contractObj = flatContract || (snapshot?.manifest as any)?.contract;
  const measure =
    contractObj?.version === 6 && contractObj?.measure_expression
      ? `${contractObj.measure_expression.minuend} - ${contractObj.measure_expression.subtrahend}`
      : (contractObj?.measure || null);

  return {
    unitLabel,
    scope,
    contractHash,
    measure,
    isFlat,
  };
}

export function buildComparisonPayload(params: {
  isAdHocSql: boolean;
  baselineVersionId: string;
  targetVersionId: string;
  snapshotId: string;
  relationship: string;
  snapshot?: any | null;
  data?: { flat_contracts?: any[]; dataset_contracts?: any[] };
  baselineVersion?: { execution_policy?: string } | null;
  targetVersion?: { execution_policy?: string } | null;
}) {
  const {
    isAdHocSql,
    baselineVersionId,
    targetVersionId,
    snapshotId,
    relationship,
    snapshot,
    data,
    baselineVersion,
    targetVersion,
  } = params;

  const path = isAdHocSql
    ? 'flat'
    : detectExecutionPath(baselineVersion, targetVersion, snapshot);

  const meta = getSnapshotContractMetadata(snapshot, data);

  if (path === 'flat') {
    return {
      endpoint: '/flat/comparisons',
      body: {
        baseline_version_id: baselineVersionId,
        target_version_id: targetVersionId,
        snapshot_id: snapshotId,
        relationship,
        context: {
          mode: 'flat_snapshot',
          scope: meta.scope,
          contract_hash: meta.contractHash,
          unit_label: meta.unitLabel,
          values_comparable: true,
        },
      },
    };
  }

  return {
    endpoint: '/comparisons',
    body: {
      baseline_version_id: baselineVersionId,
      target_version_id: targetVersionId,
      snapshot_id: snapshotId,
      relationship,
      context: {
        start: '2026-08-01',
        end_exclusive: '2026-09-01',
        timezone: snapshot?.timezone || 'Asia/Kolkata',
        currency: meta.unitLabel,
        unit: meta.isFlat ? meta.unitLabel : 'currency',
        grain: snapshot?.grain || 'one_row_per_order_id',
        scope: meta.scope,
        contract_hash: meta.contractHash,
        mapping_hash: snapshot?.mapping_hash ?? null,
      },
    },
  };
}

export interface ClassifiedInvestigationError {
  isRefusal: boolean;
  title: string;
  message: string;
  guidance?: string;
  code?: string;
}

export function classifyInvestigationError(err: unknown, measurePolicy?: string): ClassifiedInvestigationError {
  if (!err) {
    return {
      isRefusal: false,
      title: 'Processing unavailable',
      message: 'An unknown issue occurred.',
    };
  }

  let code = '';
  let rawMsg = '';

  if (typeof err === 'object' && err !== null) {
    if ('code' in err && typeof (err as any).code === 'string') {
      code = (err as any).code.toUpperCase();
    }
    if ('message' in err && typeof (err as any).message === 'string') {
      rawMsg = (err as any).message;
    }
  }

  if (!rawMsg && err instanceof Error) {
    rawMsg = err.message;
  }
  if (!rawMsg && typeof err === 'string') {
    rawMsg = err;
  }

  // Extract code from message if embedded (e.g. "[UNSUPPORTED_SQL] ..." or "422 UNSUPPORTED_SQL")
  if (!code && rawMsg) {
    const match = rawMsg.match(
      /\b(UNSUPPORTED_SQL|UNSUPPORTED_CONSTRUCT|UNSUPPORTED_FORMAT|NOT_COMPARABLE|PERIOD_CONFLICT|CONTEXT_REQUIRED|POLICY_MISMATCH|INVALID_CONTRACT|INVALID_SQL|PARSE_ERROR|DEMO_NOT_READY|ISOLATION_REQUIRED|VALIDATION_ERROR|RUNNER_FAILED)\b/i
    );
    if (match) {
      code = match[1].toUpperCase();
    }
  }

  const lowerMsg = (rawMsg || '').toLowerCase();

  // 1. UNSUPPORTED_SQL & Query grammar limitations
  if (
    code === 'UNSUPPORTED_SQL' ||
    code === 'UNSUPPORTED_CONSTRUCT' ||
    lowerMsg.includes('unsupported_sql') ||
    lowerMsg.includes('only categorical') ||
    lowerMsg.includes('from snapshot is required') ||
    lowerMsg.includes('only sum') ||
    lowerMsg.includes('join structure not supported') ||
    lowerMsg.includes('common table expressions') ||
    lowerMsg.includes('syntax or operations outside')
  ) {
    let guidance = 'Supported filters: =, !=, <>, IN, >, <, >=, <=, BETWEEN, IS NULL, IS NOT NULL, boolean predicates.';
    let message = rawMsg || 'This query uses syntax MetricGuard does not currently support.';

    if (measurePolicy === 'flat-case-v1' || lowerMsg.includes('case') || lowerMsg.includes('when') || lowerMsg.includes('else')) {
      guidance = 'CASE mode supports SUM(CASE WHEN <bounded predicate> THEN <registered measure> ELSE 0 END) FROM snapshot, with optional supported WHERE. Exactly one WHEN branch; no nested CASE or arithmetic branches.';
    } else if (lowerMsg.includes('from snapshot')) {
      message = rawMsg || 'Queries must select directly FROM snapshot without joins, subqueries, or table aliases.';
      guidance = 'Use: FROM snapshot WHERE ...';
    } else if (lowerMsg.includes('only sum')) {
      message = rawMsg || 'Queries must evaluate the calculation registered by the frozen snapshot contract.';
      guidance = 'Use the frozen snapshot\'s declared calculation inside SUM(...), directly FROM snapshot.';
    }

    return {
      isRefusal: true,
      title: 'Query not supported',
      message,
      guidance,
      code: code || 'UNSUPPORTED_SQL',
    };
  }

  // 2. NOT_COMPARABLE
  if (
    code === 'NOT_COMPARABLE' ||
    code === 'POLICY_MISMATCH' ||
    lowerMsg.includes('not comparable') ||
    lowerMsg.includes('differs from the frozen') ||
    lowerMsg.includes('contract changed') ||
    lowerMsg.includes('same declared measure')
  ) {
    return {
      isRefusal: true,
      title: 'Comparison not available',
      message: 'The selected definitions do not match the frozen snapshot contract.',
      guidance: rawMsg && rawMsg !== 'The selected definitions do not match the frozen snapshot contract.' ? rawMsg : undefined,
      code: code || 'NOT_COMPARABLE',
    };
  }

  // 3. Validation errors
  if (
    code === 'VALIDATION_ERROR' ||
    code === 'INVALID_CONTRACT' ||
    code === 'ROW_KEY_NOT_UNIQUE' ||
    code === 'NULL_IN_ROW_KEY' ||
    code === 'PERIOD_CONFLICT' ||
    lowerMsg.includes('validation error') ||
    lowerMsg.includes('period conflict') ||
    lowerMsg.includes('contract fields')
  ) {
    return {
      isRefusal: true,
      title: "Couldn't validate snapshot",
      message: rawMsg || 'The dataset or definition failed contract validation.',
      code: code || 'VALIDATION_ERROR',
    };
  }

  // 4. Runner Failure
  if (
    code === 'RUNNER_FAILED' ||
    lowerMsg.includes('runner_failed') ||
    lowerMsg.includes('isolated execution did not complete')
  ) {
    return {
      isRefusal: false,
      title: 'Isolated runner execution failed',
      message: rawMsg || 'The isolated reconciliation runner exited unexpectedly.',
      guidance: 'Ensure the runner Docker image (metricguard-runner:1.0) is built and the Docker daemon is running.',
      code: 'RUNNER_FAILED',
    };
  }

  // 5. Processing / Infrastructure / Runtime failure
  return {
    isRefusal: false,
    title: 'Processing unavailable',
    message: rawMsg || 'The reconciliation engine is temporarily unavailable. Check system status in Settings.',
    code: code || 'PROCESSING_UNAVAILABLE',
  };
}
