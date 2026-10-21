import { useState, useEffect, useMemo } from 'react';
import { calculationPage, splitCalculations } from '../guidedWorkflow';
import { matchingMetricLoads, memberName, metricDifference, metricSupport, retainedCalculations, sqlDefinitionRequest, type MetricMember } from '../metricWorkflow';
import { resultDisplayValue, resultUnit, valueWithUnit } from '../resultSummary';
import {
  ArrowLeft,
  CheckCircle2,
  FileCode2,
  GitCommit,
  Layers,
  Plus,
  Search,
  ShieldCheck,
  TrendingUp,
  X,
} from 'lucide-react';
import {
  api,
  latest,
  type Bootstrap,
  type RelationalBootstrap,
  type Definition,
  type MetricBinding,
  type MetricItem,
  type MetricStateTransition,
  type Run,
  type Version,
  type DefinitionUsage,
} from '../api';
import { isDefinedComparison, isExactZero } from '../investigationUtils';
import { MetricContractsPanel } from './MetricContractsPanel';
import { SqlEditor } from './SqlEditor';
import { datasetTableName, flatSqlColumns } from '../comparisonBuilder';

interface MetricsViewProps {
  data: Bootstrap;
  refresh: () => Promise<Bootstrap | null>;
  activeTab?: string;
  metricId?: string;
  onTabChange?: (tab: string) => void;
  onSelectMetric?: (metricId: string | null) => void;
  onComparePair?: (leftVersionId: string, rightVersionId: string, rel: string, snapshotId?: string) => void;
  onStartReplay?: (defId: string, oldVerId: string) => void;
  onOpenRun?: (run: Run) => void;
}

export function MetricsView({
  data,
  refresh,
  activeTab = 'implementations',
  metricId,
  onTabChange,
  onSelectMetric,
  onComparePair,
  onOpenRun,
}: MetricsViewProps) {
  // Navigation / Selection
  const [selectedMetricId, setSelectedMetricId] = useState<string | null>(metricId || null);
  const [detailTab, setDetailTab] = useState<'implementations' | 'versions' | 'impact' | 'decisions' | 'contracts'>(
    (activeTab as 'implementations' | 'versions' | 'impact' | 'decisions' | 'contracts') || 'implementations'
  );
  useEffect(() => {
    if (['implementations', 'versions', 'impact', 'decisions', 'contracts'].includes(activeTab)) {
      setDetailTab(activeTab as typeof detailTab);
    }
  }, [activeTab]);

  // Search & Filter Toolbar
  const [searchQuery, setSearchQuery] = useState('');
  const [showCalculations, setShowCalculations] = useState(false);
  const [calculationQuery, setCalculationQuery] = useState('');
  const [calculationPageIndex, setCalculationPageIndex] = useState(0);
  const [calculationGroup, setCalculationGroup] = useState<'imported' | 'comparisons'>('imported');
  const [showAllCalculations, setShowAllCalculations] = useState(false);
  const [definitionUsage, setDefinitionUsage] = useState<DefinitionUsage[]>([]);
  const [usageError, setUsageError] = useState('');
  const [calculationEdit, setCalculationEdit] = useState<Definition | null>(null);
  const [calculationName, setCalculationName] = useState('');
  const [calculationDelete, setCalculationDelete] = useState<Definition | null>(null);
  const [importanceFilter, setImportanceFilter] = useState<'all' | 'critical' | 'important' | 'standard' | 'unclassified'>('all');
  const [supportFilter, setSupportFilter] = useState<'all' | 'analyzable' | 'limited' | 'unbound'>('all');

  // Modals
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [newMetricName, setNewMetricName] = useState('');
  const [newMetricImportance, setNewMetricImportance] = useState<'critical' | 'important' | 'standard' | 'unclassified'>('standard');
  const [newMetricAttachDefId, setNewMetricAttachDefId] = useState('');

  const [showAddImplModal, setShowAddImplModal] = useState(false);
  const [implChoice, setImplChoice] = useState<'existing' | 'new_sql'>('existing');
  const [implExistingDefId, setImplExistingDefId] = useState('');
  const [implSqlName, setImplSqlName] = useState('');
  const [implRawSql, setImplRawSql] = useState('');
  const [implContractId, setImplContractId] = useState('');
  const [implStatus, setImplStatus] = useState<'candidate' | 'active'>('candidate');

  const [promoteCandidateTarget, setPromoteCandidateTarget] = useState<{
    metricId: string;
    definitionId: string;
    versionId: string;
    defName: string;
  } | null>(null);

  // Async detail data for active metric
  const [bindings, setBindings] = useState<MetricBinding[]>([]);
  const [transitions, setTransitions] = useState<MetricStateTransition[]>([]);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [members, setMembers] = useState<MetricMember[]>([]);
  const [comparison, setComparison] = useState<{ left: Version; right: Version; relationship: string } | null>(null);
  const [comparisonSnapshotId, setComparisonSnapshotId] = useState('');

  // Status & Feedback
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState('');
  const [error, setError] = useState('');

  const metrics = data.metrics || [];
  const definitions = data.definitions || [];
  const runs = data.runs || [];
  const baselines = data.baselines || [];
  const metricDiffs = data.metric_diffs || [];
  const projectId = data.project?.id || 'local-retail';
  const availableFlatContracts = (data.flat_contracts || []).filter(c =>
    data.snapshots.some(s => s.contract_hash === c.contract_hash && s.status === 'validated'));

  useEffect(() => {
    let current = true;
    setDefinitionUsage([]); setUsageError(''); setMembers([]);
    api.definitionUsage(projectId).then(result => { if (current) setDefinitionUsage(result.items); })
      .catch(() => { if (current) setUsageError('Saved calculation usage could not be loaded. Show all calculations to browse them.'); });
    api.get<MetricMember[]>(`/projects/${encodeURIComponent(projectId)}/members`)
      .then(result => { if (current) setMembers(result); }).catch(() => {});
    return () => { current = false; };
  }, [projectId, data.definitions, data.metrics, data.investigations]);

  // Keep internal selected metric in sync with prop
  useEffect(() => {
    if (metricId) {
      setSelectedMetricId(metricId);
    }
  }, [metricId]);

  // Reset local state when workspace changes
  useEffect(() => {
    setSelectedMetricId(metricId || null);
    setShowCreateModal(false);
    setShowAddImplModal(false);
    setPromoteCandidateTarget(null);
    setComparison(null);
    setCalculationEdit(null); setCalculationDelete(null); setShowAllCalculations(false);
    setImplContractId('');
    setBindings([]);
    setTransitions([]);
    setNotice('');
    setError('');
  }, [projectId]);

  // Escape key handler for modals
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !busy) {
        if (showCreateModal) setShowCreateModal(false);
        if (showAddImplModal) setShowAddImplModal(false);
        if (promoteCandidateTarget) setPromoteCandidateTarget(null);
        setComparison(null); setCalculationEdit(null); setCalculationDelete(null);
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [showCreateModal, showAddImplModal, promoteCandidateTarget, busy]);

  // Load bindings & transitions when a metric is selected
  useEffect(() => {
    if (!selectedMetricId) {
      setBindings([]);
      setTransitions([]);
      return;
    }

    let active = true;
    setLoadingDetail(true);

    Promise.all([
      api.listMetricBindings(projectId, selectedMetricId).catch(() => ({ items: [] })),
      api.listMetricTransitions(projectId, selectedMetricId).catch(() => ({ items: [] })),
    ])
      .then(([bindingsRes, transitionsRes]) => {
        if (!active) return;
        setBindings(bindingsRes.items || []);
        setTransitions(transitionsRes.items || []);
      })
      .finally(() => {
        if (active) setLoadingDetail(false);
      });

    return () => {
      active = false;
    };
  }, [selectedMetricId, projectId]);

  // Derived: Bound definition IDs across all metrics
  const boundDefinitionIds = useMemo(() => {
    const ids = new Set<string>();
    metrics.forEach((m) => {
      if (m.active_binding?.definition_id) {
        ids.add(m.active_binding.definition_id);
      }
    });
    bindings.forEach((b) => {
      ids.add(b.definition_id);
    });
    return ids;
  }, [metrics, bindings]);

  // Unassigned definitions (definitions not bound to any metric)
  const unassignedDefinitions = useMemo(() => {
    return definitions.filter((d) => !boundDefinitionIds.has(d.id));
  }, [definitions, boundDefinitionIds]);
  // Guided comparisons save one calculation per new formula; keep those apart so imported queries stay findable.
  const visibleCalculations = retainedCalculations(definitions, definitionUsage, showAllCalculations);
  const calculationGroups = splitCalculations(visibleCalculations);
  const attachGroups = splitCalculations(unassignedDefinitions);
  const shownGroup = calculationGroup === 'imported' && !calculationGroups.imported.length ? 'comparisons' : calculationGroup;
  const calculations = calculationPage(shownGroup === 'imported' ? calculationGroups.imported : calculationGroups.fromComparisons,
    calculationQuery, calculationPageIndex);
  const definitionOptions = (versionsLabel: boolean) => ([
    ['Imported queries', attachGroups.imported], ['Saved from guided comparisons', attachGroups.fromComparisons],
  ] as const).filter(([, items]) => items.length).map(([label, items]) => (
    <optgroup key={label} label={label}>
      {items.map((d) => (
        <option key={d.id} value={d.id}>
          {d.name} ({d.source_label}){versionsLabel ? ` · ${d.versions.length} versions` : ''}
        </option>
      ))}
    </optgroup>
  ));

  // Selected Metric Object
  const selectedMetric = useMemo(() => {
    if (!selectedMetricId) return null;
    return metrics.find((m) => m.id === selectedMetricId) || null;
  }, [metrics, selectedMetricId]);

  const getMetricSupportState = (metric: MetricItem) => metricSupport(metric, definitions, data.snapshots, bindings);

  const startComparison = (left: Version, right: Version, relationship: string) => {
    const loads = matchingMetricLoads(left, data.snapshots, right);
    if (!loads.length) { setError('These calculations need supported SQL linked to the same available dataset.'); return; }
    setComparisonSnapshotId(loads.length === 1 ? loads[0].id : '');
    setComparison({ left, right, relationship });
  };

  const comparisonDialog = comparison && (
    <div className="modal-backdrop" role="dialog" aria-modal="true" aria-labelledby="metric-comparison-title" onClick={() => setComparison(null)}>
      <form className="modal-card" onClick={e => e.stopPropagation()} onSubmit={e => {
        e.preventDefault();
        const load = matchingMetricLoads(comparison.left, data.snapshots, comparison.right).find(s => s.id === comparisonSnapshotId);
        if (!load) { setError('Choose an available data load for these calculations.'); return; }
        onComparePair?.(comparison.left.id, comparison.right.id, comparison.relationship, load.id);
        setComparison(null);
      }}>
        <div className="modal-header"><h3 id="metric-comparison-title">Choose data to compare</h3>
          <button type="button" className="modal-close-button" aria-label="Close dialog" onClick={() => setComparison(null)}><X size={18} /></button></div>
        <div className="modal-body">
          <label className="form-label" htmlFor="metric-comparison-load">Data load</label>
          <select id="metric-comparison-load" className="form-select" required value={comparisonSnapshotId} onChange={e => setComparisonSnapshotId(e.target.value)}>
            <option value="">Choose a data load…</option>
            {matchingMetricLoads(comparison.left, data.snapshots, comparison.right).map(s => <option key={s.id} value={s.id}>{s.name} · {s.created_at.slice(0, 10)} · {s.row_count.toLocaleString()} records</option>)}
          </select>
        </div>
        <div className="modal-footer"><button type="button" className="button secondary" onClick={() => setComparison(null)}>Cancel</button>
          <button className="button primary" disabled={!comparisonSnapshotId}>Compare</button></div>
      </form>
    </div>
  );

  // Helper to get latest drift state for a metric
  const getMetricDriftState = (metric: MetricItem) => {
    if (!metric.active_binding?.definition_id) return null;
    const def = definitions.find((d) => d.id === metric.active_binding?.definition_id);
    if (!def) return null;
    const approvedVerId = metric.active_binding.approved_version_id;

    // Look for runs comparing approved version against candidate/other versions
    const relevantRun = runs.find(
      (r) =>
        r.state === 'succeeded' &&
        r.result &&
        (r.baseline_version_id === approvedVerId || r.target_version_id === approvedVerId)
    );

    if (!relevantRun || !relevantRun.result) return null;

    const res = relevantRun.result;
    const defined = isDefinedComparison(res);
    const isZero = defined && isExactZero(res.delta_target_minus_baseline);
    return {
      isZero,
      defined,
      delta: metricDifference(res),
      relativePercent: res.relative_change_percent_display,
      runId: relevantRun.id,
      snapshotId: relevantRun.snapshot_id,
    };
  };

  // Filtered metrics
  const filteredMetrics = useMemo(() => {
    return metrics.filter((m) => {
      const matchSearch =
        !searchQuery ||
        m.display_name.toLowerCase().includes(searchQuery.toLowerCase()) ||
        m.id.toLowerCase().includes(searchQuery.toLowerCase());

      const matchImportance =
        importanceFilter === 'all' || m.importance === importanceFilter;

      const support = getMetricSupportState(m);
      const matchSupport =
        supportFilter === 'all' || support.state === supportFilter;

      return matchSearch && matchImportance && matchSupport;
    });
  }, [metrics, searchQuery, importanceFilter, supportFilter, definitions, data.snapshots, bindings]);

  // Handlers
  const handleSelectMetric = (mId: string | null) => {
    setSelectedMetricId(mId);
    if (onSelectMetric) onSelectMetric(mId);
  };

  const handleDetailTabClick = (tab: 'implementations' | 'versions' | 'impact' | 'decisions' | 'contracts') => {
    setDetailTab(tab);
    if (onTabChange) onTabChange(tab);
  };

  const handleCreateMetric = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newMetricName.trim()) return;

    setBusy(true);
    setError('');
    try {
      const created = await api.createMetric(projectId, newMetricName.trim(), newMetricImportance);
      if (newMetricAttachDefId) {
        // Attach as candidate; never implicitly promote or approve without explicit lifecycle transition
        await api.bindMetricDefinition(projectId, created.id, newMetricAttachDefId, 'candidate');
      }
      await refresh();
      setShowCreateModal(false);
      setNewMetricName('');
      setNewMetricAttachDefId('');
      setNotice(`Metric "${created.display_name}" created successfully.`);
      handleSelectMetric(created.id);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const handleAddImplementation = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!selectedMetric) return;

    setBusy(true);
    setError('');
    try {
      let targetDefId = implExistingDefId;
      if (implChoice === 'new_sql') {
        const request = sqlDefinitionRequest(implSqlName || `${selectedMetric.display_name} Query`, implRawSql,
          implContractId, availableFlatContracts, data.snapshots);
        const createdDef = await api.post<Definition>(request.endpoint, request.body);
        targetDefId = createdDef.id;
      }

      if (!targetDefId) throw new Error('Please select or create a query definition.');

      if (implStatus === 'active') {
        await api.promoteMetricDefinition(projectId, selectedMetric.id, targetDefId);
      } else {
        await api.bindMetricDefinition(projectId, selectedMetric.id, targetDefId, 'candidate');
      }

      await refresh();
      // Reload bindings
      const updatedBindings = await api.listMetricBindings(projectId, selectedMetric.id);
      setBindings(updatedBindings.items || []);

      setShowAddImplModal(false);
      setImplSqlName('');
      setImplRawSql('');
      setImplExistingDefId('');
      setNotice('Implementation added successfully.');
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const handlePromoteCandidate = async () => {
    if (!promoteCandidateTarget) return;
    setBusy(true);
    setError('');
    try {
      await api.promoteMetricDefinition(
        projectId,
        promoteCandidateTarget.metricId,
        promoteCandidateTarget.definitionId,
        promoteCandidateTarget.versionId
      );
      await refresh();
      const updatedBindings = await api.listMetricBindings(projectId, promoteCandidateTarget.metricId);
      setBindings(updatedBindings.items || []);
      const updatedTransitions = await api.listMetricTransitions(projectId, promoteCandidateTarget.metricId);
      setTransitions(updatedTransitions.items || []);

      setPromoteCandidateTarget(null);
      setNotice(`Promoted ${promoteCandidateTarget.defName} to active implementation.`);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const handleRenameCalculation = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!calculationEdit || !calculationName.trim()) return;
    setBusy(true); setError('');
    try {
      await api.renameDefinition(calculationEdit.id, calculationName.trim());
      await refresh(); setCalculationEdit(null); setNotice('Calculation renamed.');
    } catch (err) { setError(err instanceof Error ? err.message : String(err)); }
    finally { setBusy(false); }
  };

  const handleDeleteCalculation = async () => {
    if (!calculationDelete) return;
    setBusy(true); setError('');
    try {
      await api.deleteDefinition(calculationDelete.id);
      await refresh(); setCalculationDelete(null); setNotice('Unused calculation deleted.');
    } catch (err) { setError(err instanceof Error ? err.message : String(err)); }
    finally { setBusy(false); }
  };

  // =========================================================================
  // VIEW 1: METRIC DETAIL VIEW (when selectedMetricId is active)
  // =========================================================================
  if (selectedMetric) {
    const supportInfo = getMetricSupportState(selectedMetric);
    const activeDef = supportInfo.definition;
    const activeVersion = supportInfo.version;

    // Derived active and candidate bindings
    const activeBinding =
      bindings.find((b) => b.status === 'active') ||
      (selectedMetric.active_binding?.definition_id
        ? {
            id: 'active-bound',
            project_id: projectId,
            metric_id: selectedMetric.id,
            definition_id: selectedMetric.active_binding.definition_id,
            status: 'active' as const,
            approved_version_id: selectedMetric.active_binding.approved_version_id,
            created_at: selectedMetric.created_at,
          }
        : null);

    const candidateBindings = bindings.filter((b) => b.status === 'candidate');
    const retiredBindings = bindings.filter((b) => b.status === 'retired');

    // All versions across bound definitions for version history tab
    const boundDefs = definitions.filter(
      (d) =>
        d.id === activeBinding?.definition_id ||
        candidateBindings.some((c) => c.definition_id === d.id) ||
        retiredBindings.some((r) => r.definition_id === d.id)
    );

    const versionHistoryList = boundDefs.flatMap((d) =>
      d.versions.map((v) => {
        const isApproved = v.id === activeBinding?.approved_version_id;
        const isCandidate =
          candidateBindings.some((c) => c.definition_id === d.id) ||
          (d.id === activeBinding?.definition_id &&
            !isApproved &&
            v.version_number > (activeVersion?.version_number || 0));
        return {
          definition: d,
          version: v,
          isApproved,
          status: isApproved ? 'approved' : isCandidate ? 'candidate' : 'superseded',
        };
      })
    ).sort((a, b) => b.version.version_number - a.version.version_number);

    // Candidate drift runs
    const candidateRuns = runs.filter(
      (r) =>
        r.state === 'succeeded' &&
        r.result &&
        (r.baseline_version_id === activeBinding?.approved_version_id ||
          boundDefs.some((d) => d.versions.some((v) => v.id === r.target_version_id)))
    );

    return (
      <div className="metric-detail-view">
        {comparisonDialog}
        {/* Topbar navigation */}
        <div className="metric-detail-topbar">
          <button
            type="button"
            className="back-button-link"
            onClick={() => handleSelectMetric(null)}
          >
            <ArrowLeft size={16} />
            <span>Back to Metrics catalog</span>
          </button>
        </div>

        {notice && (
          <div className="alert success" role="status">
            <span>{notice}</span>
            <button onClick={() => setNotice('')} aria-label="Dismiss notice">×</button>
          </div>
        )}
        {error && (
          <div className="alert error" role="alert">
            <span>{error}</span>
            <button onClick={() => setError('')} aria-label="Dismiss error">×</button>
          </div>
        )}

        {/* Metric Detail Header Card */}
        <div className="metric-detail-header-card">
          <div className="metric-detail-title-group">
            <div className="metric-detail-badges-row">
              <span className={`importance-badge ${selectedMetric.importance}`}>
                {selectedMetric.importance}
              </span>
              <span className={`status-pill ${supportInfo.isAnalyzable ? 'ready' : 'warn'}`}>
                <span className="status-dot" />
                {supportInfo.label}
              </span>
              <span className="source-badge">Metric ID: {selectedMetric.id}</span>
            </div>
            <h1>{selectedMetric.display_name}</h1>
            <p className="muted-text">{supportInfo.reason}</p>
            <div className="metric-detail-active-binding-text">
              {activeDef && activeVersion ? (
                <>
                  <ShieldCheck size={16} color="var(--link)" />
                  <span>
                    Approved calculation: <strong>{activeDef.name}</strong> ·{' '}
                    <span className="mono-cell">v{activeVersion.version_number}</span>{' '}
                    (Approved baseline)
                  </span>
                </>
              ) : (
                <span className="muted-text">Add a calculation linked to a dataset to get started.</span>
              )}
            </div>
          </div>

          <div className="metric-row-actions">
            <button
              type="button"
              className="button primary"
              onClick={() => {
                setImplExistingDefId(unassignedDefinitions[0]?.id || '');
                setShowAddImplModal(true);
              }}
            >
              <Plus size={15} />
              <span>Add implementation</span>
            </button>
            {supportInfo.isAnalyzable && activeVersion && onComparePair && (
              <button
                type="button"
                className="button secondary"
                onClick={() => {
                  const candidateVer = versionHistoryList.find((vh) => vh.status === 'candidate' &&
                    ['executable', 'supported'].includes(vh.version.support_state) && matchingMetricLoads(activeVersion, data.snapshots, vh.version).length > 0)?.version;
                  if (candidateVer) {
                    startComparison(activeVersion, candidateVer, 'candidate_review');
                  } else {
                    startComparison(activeVersion, activeVersion, 'baseline_audit');
                  }
                }}
              >
                <Layers size={15} />
                <span>Investigate in studio</span>
              </button>
            )}
          </div>
        </div>

        {/* Navigation Tabs */}
        <div className="section-nav-tabs">
          <button
            type="button"
            className={`section-tab-button ${detailTab === 'implementations' ? 'active' : ''}`}
            onClick={() => handleDetailTabClick('implementations')}
          >
            Implementations ({(activeBinding ? 1 : 0) + candidateBindings.length + retiredBindings.length})
          </button>
          <button
            type="button"
            className={`section-tab-button ${detailTab === 'versions' ? 'active' : ''}`}
            onClick={() => handleDetailTabClick('versions')}
          >
            Version history ({versionHistoryList.length})
          </button>
          <button
            type="button"
            className={`section-tab-button ${detailTab === 'impact' ? 'active' : ''}`}
            onClick={() => handleDetailTabClick('impact')}
          >
            Proposed changes and their impact ({candidateRuns.length})
          </button>
          <button
            type="button"
            className={`section-tab-button ${detailTab === 'decisions' ? 'active' : ''}`}
            onClick={() => handleDetailTabClick('decisions')}
          >
            Decisions & changes ({transitions.length + baselines.length})
          </button>
          <button type="button" className={`section-tab-button ${detailTab === 'contracts' ? 'active' : ''}`}
            onClick={() => handleDetailTabClick('contracts')}>Checks</button>
        </div>

        {detailTab === 'contracts' && <MetricContractsPanel key={`${projectId}:${selectedMetric.id}`}
          projectId={projectId} metric={selectedMetric} definitions={definitions} bindings={bindings} snapshots={data.snapshots || []}
          relationalContracts={(data as RelationalBootstrap).relational_contracts || []} />}

        {/* Tab 1: Implementations */}
        {detailTab === 'implementations' && (
          <div className="implementations-list">
            {/* Active Implementation */}
            <div className="implementation-group-title">Active Implementation</div>
            {activeDef && activeVersion ? (
              <div className="implementation-card active">
                <div className="implementation-card-header">
                  <div className="implementation-card-title-line">
                    <span className="status-pill ready">
                      <span className="status-dot" />
                      ACTIVE
                    </span>
                    <span className="implementation-name">{activeDef.name}</span>
                    <span className="source-badge">{activeDef.source_label}</span>
                  </div>
                  <div className="implementation-card-actions">
                    <span className="badge font-semibold" style={{ background: 'var(--tint)', color: 'var(--link)' }}>
                      Approved baseline v{activeVersion.version_number}
                    </span>
                  </div>
                </div>

                <div className="implementation-card-meta-grid">
                  <div className="implementation-meta-item">
                    <span className="meta-label">Bound Version</span>
                    <span className="meta-value mono">v{activeVersion.version_number}</span>
                  </div>
                  <div className="implementation-meta-item">
                    <span className="meta-label">Next step</span>
                    <span className="meta-value font-semibold" style={{ color: supportInfo.isAnalyzable ? 'var(--link)' : 'var(--warn)' }}>
                      {supportInfo.label}
                    </span>
                  </div>
                  <div className="implementation-meta-item">
                    <span className="meta-label">Observed Date</span>
                    <span className="meta-value">{activeVersion.observed_at ? activeVersion.observed_at.slice(0, 10) : 'Recent'}</span>
                  </div>
                  <div className="implementation-meta-item">
                    <span className="meta-label">Change Kind</span>
                    <span className="meta-value">{activeVersion.change_kind || 'Production query'}</span>
                  </div>
                </div>

                {activeVersion.raw_sql && (
                  <div className="sql-preview-box">
                    <pre>{activeVersion.raw_sql}</pre>
                  </div>
                )}
              </div>
            ) : (
              <div className="empty-card" style={{ padding: '24px', textAlign: 'center', background: 'var(--panel)', border: '1px dashed var(--line)', borderRadius: '8px' }}>
                <p style={{ margin: '0 0 12px', color: 'var(--muted)' }}>No active implementation currently bound to this metric.</p>
                <button
                  type="button"
                  className="button secondary"
                  onClick={() => setShowAddImplModal(true)}
                >
                  <Plus size={14} /> Attach implementation
                </button>
              </div>
            )}

            {/* Candidate Implementations */}
            <div className="implementation-group-title">Candidate Implementations ({candidateBindings.length})</div>
            {candidateBindings.length > 0 ? (
              candidateBindings.map((c) => {
                const candDef = definitions.find((d) => d.id === c.definition_id);
                const candVer = candDef?.versions.find((v) => v.id === c.approved_version_id) || (candDef ? latest(candDef) : null);
                if (!candDef || !candVer) return null;

                return (
                  <div key={c.id} className="implementation-card candidate">
                    <div className="implementation-card-header">
                      <div className="implementation-card-title-line">
                        <span className="status-pill warn">
                          <span className="status-dot" />
                          CANDIDATE
                        </span>
                        <span className="implementation-name">{candDef.name}</span>
                        <span className="source-badge">{candDef.source_label}</span>
                      </div>
                      <div className="implementation-card-actions">
                        {supportInfo.isAnalyzable && activeVersion && onComparePair && (
                          <button
                            type="button"
                            className="button secondary"
                            disabled={!matchingMetricLoads(activeVersion, data.snapshots, candVer).length || !['executable', 'supported'].includes(candVer.support_state)}
                            onClick={() => startComparison(activeVersion, candVer, 'candidate_review')}
                          >
                            Compare with active
                          </button>
                        )}
                        <button
                          type="button"
                          className="button primary"
                          onClick={() =>
                            setPromoteCandidateTarget({
                              metricId: selectedMetric.id,
                              definitionId: candDef.id,
                              versionId: candVer.id,
                              defName: candDef.name,
                            })
                          }
                        >
                          Promote to active
                        </button>
                      </div>
                    </div>

                    <div className="implementation-card-meta-grid">
                      <div className="implementation-meta-item">
                        <span className="meta-label">Target Version</span>
                        <span className="meta-value mono">v{candVer.version_number}</span>
                      </div>
                      <div className="implementation-meta-item">
                        <span className="meta-label">Next step</span>
                        <span className="meta-value">
                          {!candVer.contract_hash ? 'Not linked to a dataset — add the SQL again for a dataset' : matchingMetricLoads(candVer, data.snapshots).length > 0 && ['executable', 'supported'].includes(candVer.support_state) ? 'Ready to compare' : 'Add SQL for a dataset'}
                        </span>
                      </div>
                      <div className="implementation-meta-item">
                        <span className="meta-label">Bound At</span>
                        <span className="meta-value">{c.created_at ? c.created_at.slice(0, 10) : 'Recent'}</span>
                      </div>
                      <div className="implementation-meta-item">
                        <span className="meta-label">Rationale</span>
                        <span className="meta-value">Candidate evaluation</span>
                      </div>
                    </div>

                    {candVer.raw_sql && (
                      <div className="sql-preview-box">
                        <pre>{candVer.raw_sql}</pre>
                      </div>
                    )}
                  </div>
                );
              })
            ) : (
              <div style={{ padding: '16px', background: 'var(--panel)', border: '1px solid var(--line)', borderRadius: '6px', color: 'var(--muted)', fontSize: '13px' }}>
                No candidate implementations currently pending review.
              </div>
            )}

            {/* Retired Implementations */}
            {retiredBindings.length > 0 && (
              <>
                <div className="implementation-group-title">Retired Implementations ({retiredBindings.length})</div>
                {retiredBindings.map((r) => {
                  const retDef = definitions.find((d) => d.id === r.definition_id);
                  return (
                    <div key={r.id} className="implementation-card" style={{ opacity: 0.7 }}>
                      <div className="implementation-card-header">
                        <div className="implementation-card-title-line">
                          <span className="status-pill neutral">RETIRED</span>
                          <span className="implementation-name">{retDef?.name || r.definition_id}</span>
                        </div>
                        <span className="muted-text font-small">
                          Retired at: {r.retired_at ? r.retired_at.slice(0, 10) : 'Recorded'}
                        </span>
                      </div>
                    </div>
                  );
                })}
              </>
            )}
          </div>
        )}

        {/* Tab 2: Version History */}
        {detailTab === 'versions' && (
          <div className="implementations-list">
            <div className="unassigned-header">
              <h3>Version Timeline</h3>
              <p>Previous calculations and the versions approved for this metric.</p>
            </div>

            {versionHistoryList.length > 0 ? (
              versionHistoryList.map(({ definition: def, version: ver, isApproved, status }) => (
                <div key={ver.id} className={`implementation-card ${isApproved ? 'active' : ''}`}>
                  <div className="implementation-card-header">
                    <div className="implementation-card-title-line">
                      <span className="mono-cell font-semibold" style={{ fontSize: '15px' }}>
                        v{ver.version_number}
                      </span>
                      {isApproved ? (
                        <span className="status-pill ready">
                          <CheckCircle2 size={13} />
                          Approved baseline
                        </span>
                      ) : status === 'candidate' ? (
                        <span className="status-pill warn">Candidate</span>
                      ) : (
                        <span className="status-pill neutral">Superseded</span>
                      )}
                      <span className="source-badge">{def.name}</span>
                    </div>

                    <div className="implementation-card-actions">
                      {activeVersion && activeVersion.id !== ver.id && onComparePair && matchingMetricLoads(activeVersion, data.snapshots, ver).length > 0 && ['executable', 'supported'].includes(ver.support_state) && (
                        <button
                          type="button"
                          className="button secondary"
                          onClick={() => startComparison(activeVersion, ver, 'version_comparison')}
                        >
                          Compare with active
                        </button>
                      )}
                    </div>
                  </div>

                  <div className="implementation-card-meta-grid">
                    <div className="implementation-meta-item">
                      <span className="meta-label">Observed Timestamp</span>
                      <span className="meta-value">{ver.observed_at ? ver.observed_at.slice(0, 19).replace('T', ' ') : 'Recent'}</span>
                    </div>
                    <div className="implementation-meta-item">
                      <span className="meta-label">Next step</span>
                      <span className="meta-value">
                        {!ver.contract_hash ? 'Not linked to a dataset — add the SQL again for a dataset' : matchingMetricLoads(ver, data.snapshots).length > 0 && ['executable', 'supported'].includes(ver.support_state) ? 'Ready to compare' : 'Add SQL for a dataset'}
                      </span>
                    </div>
                    <div className="implementation-meta-item">
                      <span className="meta-label">Time Column</span>
                      <span className="meta-value mono">{ver.time_column || 'None specified'}</span>
                    </div>
                    <div className="implementation-meta-item">
                      <span className="meta-label">Source Hash</span>
                      <span className="meta-value mono">{ver.raw_hash.slice(0, 10)}…</span>
                    </div>
                  </div>

                  {ver.raw_sql && (
                    <div className="sql-preview-box">
                      <pre>{ver.raw_sql}</pre>
                    </div>
                  )}
                </div>
              ))
            ) : (
              <div className="empty-card" style={{ padding: '24px', textAlign: 'center', background: 'var(--panel)' }}>
                <p className="muted-text">No recorded versions found for this metric.</p>
              </div>
            )}
          </div>
        )}

        {/* Tab 3: Difference from approved */}
        {detailTab === 'impact' && (
          <div className="drift-impact-container">
            <div className="unassigned-header">
              <h3>Difference from approved</h3>
              <p>How proposed calculations compare with the approved calculation on the same data.</p>
            </div>

            {candidateRuns.length > 0 ? (
              candidateRuns.map((r) => {
                const res = r.result;
                if (!res) return null;
                const defined = isDefinedComparison(res);
                const isZero = defined && isExactZero(res.delta_target_minus_baseline);

                return (
                  <div key={r.id} className="drift-summary-card">
                    <div className="implementation-card-header">
                      <div>
                        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '4px' }}>
                          <span className={`status-pill ${isZero ? 'ready' : 'neutral'}`}>
                            {!defined ? 'Difference unavailable' : isZero ? 'Same result as approved' : 'Difference from approved'}
                          </span>
                          <span className="source-badge">Snapshot: {r.snapshot_id}</span>
                        </div>
                        <div className="muted-text font-small">Evaluated on {r.created_at ? r.created_at.slice(0, 19).replace('T', ' ') : 'Recent'}</div>
                      </div>

                      {onOpenRun && (
                        <button
                          type="button"
                          className="button primary"
                          onClick={() => onOpenRun(r)}
                        >
                          Open comparison
                        </button>
                      )}
                    </div>

                    <div className="drift-comparison-grid">
                      <div className="drift-value-box">
                        <span className="box-label">Approved value</span>
                        <span className="box-num">{valueWithUnit(resultDisplayValue(res, res.baseline_value), resultUnit(res))}</span>
                      </div>
                      <div className="drift-value-box target-box">
                        <span className="box-label">Proposed value</span>
                        <span className="box-num">{valueWithUnit(resultDisplayValue(res, res.target_value), resultUnit(res))}</span>
                      </div>
                      <div className={`drift-value-box ${isZero ? 'delta-zero' : ''}`}>
                        <span className="box-label">Difference from approved</span>
                        <span className="box-num">{metricDifference(res)}</span>
                      </div>
                      <div className="drift-value-box">
                        <span className="box-label">Percentage change</span>
                        <span className="box-num">{res.relative_change_percent_display ?? 'Unavailable'}</span>
                      </div>
                    </div>

                    <div style={{ fontSize: '13px', color: 'var(--text-2)' }}>
                      Open the comparison to see which records explain this difference.
                    </div>
                  </div>
                );
              })
            ) : (
              <div className="empty-card" style={{ padding: '36px', textAlign: 'center', background: 'var(--panel)', border: '1px solid var(--line)', borderRadius: '8px' }}>
                <TrendingUp size={32} color="var(--link)" style={{ margin: '0 auto 12px' }} />
                <h4 style={{ margin: '0 0 6px', fontSize: '16px', color: 'var(--ink)' }}>No comparisons recorded yet</h4>
                <p style={{ margin: '0 0 16px', color: 'var(--muted)', fontSize: '13px', maxWidth: '420px', marginLeft: 'auto', marginRight: 'auto' }}>
                  Compare a proposed calculation with the approved calculation using the same data load.
                </p>
                {supportInfo.isAnalyzable && activeVersion && onComparePair && (
                  <button
                    type="button"
                    className="button secondary"
                    onClick={() => {
                      const cand = versionHistoryList.find((v) => v.status === 'candidate' &&
                        ['executable', 'supported'].includes(v.version.support_state) && matchingMetricLoads(activeVersion, data.snapshots, v.version).length > 0);
                      if (cand) {
                        startComparison(activeVersion, cand.version, 'candidate_impact');
                      } else {
                        startComparison(activeVersion, activeVersion, 'baseline_audit');
                      }
                    }}
                  >
                    Compare calculations
                  </button>
                )}
              </div>
            )}
          </div>
        )}

        {/* Tab 4: Decisions & Changes */}
        {detailTab === 'decisions' && (
          <div className="decision-timeline">
            <div className="unassigned-header">
              <h3>Approval history</h3>
              <p>Historical promotions, version approvals, and change records for {selectedMetric.display_name}.</p>
            </div>

            {loadingDetail && (
              <div style={{ padding: '12px', color: 'var(--muted)', fontSize: '13px' }}>
                Loading transition history…
              </div>
            )}

            {transitions.length > 0 ? (
              transitions.map((t) => (
                <div key={t.id} className="timeline-node">
                  <div className="timeline-node-icon">
                    <GitCommit size={16} />
                  </div>
                  <div className="timeline-node-content">
                    <div className="timeline-node-title">
                      {t.transition_kind.replace(/_/g, ' ').toUpperCase()}
                    </div>
                    <div className="timeline-node-meta">
                      <span>Actor: {memberName(t.actor_id, members)}</span>
                      <span>·</span>
                      <span>Recorded: {t.created_at ? t.created_at.slice(0, 19).replace('T', ' ') : 'Recent'}</span>
                    </div>
                    <div className="timeline-node-details">
                      {t.previous_approved_version_id && (
                        <span>
                          Prior version: <span className="mono-cell">{t.previous_approved_version_id.slice(0, 8)}</span> →{' '}
                        </span>
                      )}
                      <span>
                        Target approved version: <span className="mono-cell font-semibold">{t.approved_version_id?.slice(0, 8) || 'Active'}</span>
                      </span>
                    </div>
                  </div>
                </div>
              ))
            ) : !loadingDetail ? (
              <div style={{ padding: '20px', background: 'var(--panel)', border: '1px solid var(--line)', borderRadius: '6px', fontSize: '13px', color: 'var(--muted)' }}>
                No state transition events recorded for this metric yet.
              </div>
            ) : null}

            {metricDiffs.length > 0 && (
              <div style={{ marginTop: '20px' }}>
                <div className="implementation-group-title">Related CI Metric Diffs ({metricDiffs.length})</div>
                {metricDiffs.map((md) => (
                  <div key={md.id} className="implementation-card" style={{ padding: '12px 16px' }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                      <span className="mono-cell font-semibold" style={{ fontSize: '13px' }}>Diff ID: {md.id.slice(0, 12)}…</span>
                      <span className="source-badge">Snapshot: {md.snapshot_id}</span>
                    </div>
                    <div className="muted-text font-small">
                      Revision: <span className="mono-cell">{md.baseline_revision_id}</span> → <span className="mono-cell">{md.target_revision_id}</span>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {/* Add Implementation Modal */}
        {showAddImplModal && (
          <div
            className="modal-backdrop"
            role="dialog"
            aria-modal="true"
            aria-labelledby="add-impl-title"
            onClick={() => !busy && setShowAddImplModal(false)}
          >
            <div
              className="modal-card"
              style={{ maxWidth: '580px' }}
              onClick={(e) => e.stopPropagation()}
            >
              <div className="modal-header">
                <div>
                  <h3 id="add-impl-title" style={{ margin: 0 }}>Add implementation to {selectedMetric.display_name}</h3>
                  <p className="modal-subtitle">Bind a query definition as a candidate or active baseline.</p>
                </div>
                <button
                  type="button"
                  className="modal-close-button"
                  onClick={() => setShowAddImplModal(false)}
                  aria-label="Close dialog"
                >
                  <X size={18} />
                </button>
              </div>

              <form onSubmit={handleAddImplementation} style={{ display: 'flex', flexDirection: 'column', flex: '1 1 auto', minHeight: 0, margin: 0 }}>
                <div className="modal-body" style={{ padding: '24px', display: 'flex', flexDirection: 'column', gap: '20px' }}>
                  <div className="form-group" style={{ marginBottom: 0 }}>
                    <label className="form-label" style={{ fontSize: '13px', fontWeight: 600, color: 'var(--text-2)', marginBottom: 6 }}>
                      Definition source
                    </label>
                    <div style={{ display: 'flex', gap: '16px', marginTop: '6px' }}>
                      <label style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '13px', cursor: 'pointer' }}>
                        <input
                          type="radio"
                          name="implChoice"
                          value="existing"
                          checked={implChoice === 'existing'}
                          onChange={() => setImplChoice('existing')}
                        />
                        <span>Select existing definition</span>
                      </label>
                      <label style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '13px', cursor: 'pointer' }}>
                        <input
                          type="radio"
                          name="implChoice"
                          value="new_sql"
                          checked={implChoice === 'new_sql'}
                          onChange={() => setImplChoice('new_sql')}
                        />
                        <span>Import new SQL query</span>
                      </label>
                    </div>
                  </div>

                  {implChoice === 'existing' ? (
                    <div className="form-group" style={{ marginBottom: 0 }}>
                      <label className="form-label" htmlFor="impl-existing-def" style={{ fontSize: '13px', fontWeight: 600, color: 'var(--text-2)', marginBottom: 6 }}>
                        Choose definition
                      </label>
                      {unassignedDefinitions.length > 0 ? (
                        <select
                          id="impl-existing-def"
                          className="form-select"
                          value={implExistingDefId}
                          onChange={(e) => setImplExistingDefId(e.target.value)}
                          required
                          style={{ width: '100%' }}
                        >
                          <option value="">Select an unassigned definition…</option>
                          {definitionOptions(true)}
                        </select>
                      ) : (
                        <p className="muted-text font-small" style={{ margin: '4px 0 0', color: 'var(--muted)' }}>
                          No unassigned definitions currently available. Choose "Import new SQL query" instead.
                        </p>
                      )}
                    </div>
                  ) : (
                    <>
                      <div className="form-group" style={{ marginBottom: 0 }}>
                        <label className="form-label" htmlFor="impl-dataset">Dataset</label>
                        <select id="impl-dataset" className="form-select" value={implContractId} onChange={e => setImplContractId(e.target.value)} required>
                          <option value="">Choose a dataset…</option>
                          {availableFlatContracts.map(c => <option key={c.id} value={c.id}>{c.name} · {c.unit_label}</option>)}
                        </select>
                        {!availableFlatContracts.length && <p className="disabled-reason">Add a dataset in Data Sources before adding SQL.</p>}
                        <p className="muted-text font-small">Use the table name <code>snapshot</code> in your SQL.</p>
                      </div>
                      <div className="form-group" style={{ marginBottom: 0 }}>
                        <label className="form-label" htmlFor="impl-sql-name" style={{ fontSize: '13px', fontWeight: 600, color: 'var(--text-2)', marginBottom: 6 }}>
                          Definition name
                        </label>
                        <input
                          id="impl-sql-name"
                          type="text"
                          className="form-input"
                          style={{ width: '100%' }}
                          placeholder="e.g. Orders Total SQL Query"
                          value={implSqlName}
                          onChange={(e) => setImplSqlName(e.target.value)}
                          required
                        />
                      </div>
                      <div className="form-group" style={{ marginBottom: 0 }}>
                        <label className="form-label" htmlFor="impl-raw-sql" style={{ fontSize: '13px', fontWeight: 600, color: 'var(--text-2)', marginBottom: 6 }}>
                          Raw SQL query
                        </label>
                        {(() => {
                          const picked = availableFlatContracts.find(c => c.id === implContractId);
                          const table = picked ? datasetTableName(picked) : '';
                          return <SqlEditor id="impl-raw-sql" rows={5} value={implRawSql} onChange={setImplRawSql}
                            columns={picked ? flatSqlColumns(picked) : []} tables={picked ? ['snapshot', ...(table ? [table] : [])] : []}
                            placeholder={`SELECT SUM(amount) FROM ${table || 'snapshot'} WHERE status = 'completed'`} />;
                        })()}
                      </div>
                    </>
                  )}

                  <div className="form-group" style={{ marginBottom: 0 }}>
                    <label className="form-label" htmlFor="impl-status" style={{ fontSize: '13px', fontWeight: 600, color: 'var(--text-2)', marginBottom: 6 }}>
                      Binding role
                    </label>
                    <select
                      id="impl-status"
                      className="form-select"
                      style={{ width: '100%' }}
                      value={implStatus}
                      onChange={(e) => setImplStatus(e.target.value as 'candidate' | 'active')}
                    >
                      <option value="candidate">Candidate (Recommended for verification)</option>
                      <option value="active">Active (Immediately replaces active baseline)</option>
                    </select>
                  </div>
                </div>

                <div className="modal-footer" style={{ padding: '16px 24px', display: 'flex', justifyContent: 'flex-end', gap: '10px', background: 'var(--panel-2)', borderTop: '1px solid var(--line)' }}>
                  <button
                    type="button"
                    className="button secondary"
                    onClick={() => setShowAddImplModal(false)}
                  >
                    Cancel
                  </button>
                  <button type="submit" className="button primary" disabled={busy || (implChoice === 'new_sql' && !implContractId)}>
                    {busy ? 'Adding…' : 'Add implementation'}
                  </button>
                </div>
              </form>
            </div>
          </div>
        )}

        {/* Promote Candidate Modal */}
        {promoteCandidateTarget && (
          <div
            className="modal-backdrop"
            role="dialog"
            aria-modal="true"
            aria-labelledby="promote-modal-title"
            onClick={() => !busy && setPromoteCandidateTarget(null)}
          >
            <div
              className="modal-card"
              style={{ maxWidth: '480px' }}
              onClick={(e) => e.stopPropagation()}
            >
              <div className="modal-header">
                <div>
                  <h3 id="promote-modal-title" style={{ margin: 0 }}>Promote Candidate to Active</h3>
                  <p className="modal-subtitle">Replace current approved baseline with verified candidate.</p>
                </div>
                <button
                  type="button"
                  className="icon-button"
                  onClick={() => setPromoteCandidateTarget(null)}
                >
                  <X size={18} />
                </button>
              </div>

              <div style={{ padding: '16px 20px', fontSize: '13px', color: 'var(--text-2)' }}>
                <p style={{ margin: '0 0 12px' }}>
                  Are you sure you want to promote <strong>{promoteCandidateTarget.defName}</strong> as the active implementation for this metric?
                </p>
                <div style={{ background: 'var(--panel-2)', padding: '10px 14px', borderRadius: '6px', border: '1px solid var(--line)' }}>
                  <div>Target version: <span className="mono-cell font-semibold">{promoteCandidateTarget.versionId.slice(0, 10)}…</span></div>
                  <div style={{ marginTop: '4px', color: 'var(--muted)', fontSize: '12px' }}>
                    A frozen state transition record will be generated automatically.
                  </div>
                </div>
              </div>

              <div className="modal-actions" style={{ padding: '12px 20px', background: 'var(--panel-2)', borderTop: '1px solid var(--line)' }}>
                <button
                  type="button"
                  className="button secondary"
                  onClick={() => setPromoteCandidateTarget(null)}
                >
                  Cancel
                </button>
                <button
                  type="button"
                  className="button primary"
                  disabled={busy}
                  onClick={handlePromoteCandidate}
                >
                  {busy ? 'Promoting…' : 'Confirm promotion'}
                </button>
              </div>
            </div>
          </div>
        )}
      </div>
    );
  }

  // =========================================================================
  // VIEW 2: METRIC CATALOG & INVENTORY (when no metricId is selected)
  // =========================================================================
  return (
    <div className="metrics-page-container">
      {comparisonDialog}
      {/* Page Heading */}
      <div className="page-heading">
        <div>
          <h1>Metrics</h1>
          <p>Business metrics, their approved calculations and baselines.</p>
        </div>
        <div style={{ display: 'flex', gap: '10px' }}>
          <button
            type="button"
            className="button primary"
            onClick={() => setShowCreateModal(true)}
          >
            <Plus size={16} />
            <span>Create metric</span>
          </button>
        </div>
      </div>

      {notice && (
        <div className="alert success" role="status">
          <span>{notice}</span>
          <button onClick={() => setNotice('')} aria-label="Dismiss notice">×</button>
        </div>
      )}
      {error && (
        <div className="alert error" role="alert">
          <span>{error}</span>
          <button onClick={() => setError('')} aria-label="Dismiss error">×</button>
        </div>
      )}

      {/* Search & Filter Toolbar */}
      <div className="data-sources-toolbar">
        <div className="search-box">
          <Search size={16} className="search-icon" />
          <input
            type="text"
            placeholder="Filter metrics by name or ID…"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="search-input"
          />
        </div>

        <div className="filter-group">
          <span className="filter-label">Importance:</span>
          <div className="type-filter-pills">
            {(['all', 'critical', 'important', 'standard', 'unclassified'] as const).map((imp) => (
              <button
                key={imp}
                type="button"
                className={`filter-pill ${importanceFilter === imp ? 'active' : ''}`}
                onClick={() => setImportanceFilter(imp)}
              >
                {imp.charAt(0).toUpperCase() + imp.slice(1)}
              </button>
            ))}
          </div>
        </div>

        <div className="filter-group">
          <span className="filter-label">Support:</span>
          <div className="type-filter-pills">
            {(['all', 'analyzable', 'limited', 'unbound'] as const).map((sup) => (
              <button
                key={sup}
                type="button"
                className={`filter-pill ${supportFilter === sup ? 'active' : ''}`}
                onClick={() => setSupportFilter(sup)}
              >
                {sup === 'all' ? 'All' : sup === 'analyzable' ? 'Ready to compare' : sup === 'limited' ? 'Add SQL for a dataset' : 'Waiting for a calculation'}
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* Metrics Inventory List */}
      <div className="metrics-inventory-section">
        {filteredMetrics.length > 0 ? (
          filteredMetrics.map((m) => {
            const support = getMetricSupportState(m);
            const drift = getMetricDriftState(m);

            return (
              <div
                key={m.id}
                className="metric-card-row"
                onClick={() => handleSelectMetric(m.id)}
              >
                <div className="metric-row-main">
                  <div className="metric-row-title-line">
                    <span className="metric-row-name">{m.display_name}</span>
                    <span className={`importance-badge ${m.importance}`}>{m.importance}</span>
                    <span className="metric-row-id">{m.id}</span>
                  </div>

                  <div className="metric-row-meta">
                    <span className="metric-row-meta-item">
                      <FileCode2 size={14} color="var(--muted)" />
                      {support.version && support.definition ? `Approved calculation: ${support.definition.name}` : 'No approved calculation'}
                    </span>
                    <span className="muted-text">{support.reason}</span>
                    {support.version && (
                      <span className="metric-row-meta-item">
                        <CheckCircle2 size={14} color="var(--link)" />
                        Approved v{support.version.version_number}
                      </span>
                    )}
                  </div>
                </div>

                <div className="metric-row-indicators">
                  <span className={`status-pill ${support.isAnalyzable ? 'ready' : support.state === 'unbound' ? 'neutral' : 'warn'}`}>
                    <span className="status-dot" />
                    {support.label}
                  </span>

                  {drift && (
                    <span className={`status-pill ${drift.isZero ? 'ready' : 'neutral'}`}>
                      {!drift.defined ? 'Difference unavailable' : drift.isZero ? 'Same result as approved' : <>Difference from approved: <span className="mono-cell">{drift.delta}</span></>}
                    </span>
                  )}
                </div>

                <div className="metric-row-actions" onClick={(e) => e.stopPropagation()}>
                  <button
                    type="button"
                    className="button secondary"
                    onClick={() => handleSelectMetric(m.id)}
                  >
                    View detail
                  </button>
                  {support.isAnalyzable && support.version && onComparePair && (
                    <button
                      type="button"
                      className="button secondary icon-only"
                      title="Investigate metric"
                      onClick={() => startComparison(support.version!, support.version!, 'audit')}
                    >
                      <Layers size={14} />
                    </button>
                  )}
                </div>
              </div>
            );
          })
        ) : (
          <div className="empty-card" style={{ padding: '36px 20px', textAlign: 'center', background: 'var(--panel)', border: '1px solid var(--line)', borderRadius: '8px' }}>
            <Layers size={32} color="var(--link)" style={{ margin: '0 auto 12px' }} />
            <h4 style={{ margin: '0 0 6px', fontSize: '16px', color: 'var(--ink)' }}>No declared metrics match filters</h4>
            <p style={{ margin: '0 0 16px', color: 'var(--muted)', fontSize: '13px' }}>
              Declare business metrics with explicit importance levels and assign query definitions below.
            </p>
            <button
              type="button"
              className="button primary"
              onClick={() => setShowCreateModal(true)}
            >
              <Plus size={15} /> Create metric
            </button>
          </div>
        )}
      </div>

      {/* UNASSIGNED DEFINITIONS SECTION */}
      <div className="unassigned-section">
        <div className="unassigned-header">
          <h3>Saved calculations ({visibleCalculations.length})</h3>
          <p>Calculations used by a saved case or metric. Show all to find other calculations.</p>
          <button type="button" className="button secondary" aria-pressed={showAllCalculations} onClick={() => { setShowAllCalculations(v => !v); setCalculationPageIndex(0); setShowCalculations(true); }}>{showAllCalculations ? 'Used by cases or metrics' : `Show all (${definitions.length})`}</button>
          {usageError && <p role="alert" className="disabled-reason">{usageError}</p>}
          <p className="muted-text font-small">{calculationGroups.imported.length} imported · {calculationGroups.fromComparisons.length} saved from guided comparisons</p>
          <button type="button" className="button secondary" aria-expanded={showCalculations} aria-controls="saved-calculations" onClick={() => setShowCalculations(v => !v)}>
            {showCalculations ? 'Hide saved calculations' : 'Browse saved calculations'}
          </button>
        </div>

        {showCalculations && <div id="saved-calculations">
          <div className="report-actions" role="group" aria-label="Calculation source" style={{ marginBottom: 12 }}>
            {([['imported', `Imported queries (${calculationGroups.imported.length})`], ['comparisons', `From guided comparisons (${calculationGroups.fromComparisons.length})`]] as const).map(([group, label]) => (
              <button key={group} type="button" className={`button ${shownGroup === group ? 'primary' : 'secondary'}`} aria-pressed={shownGroup === group}
                onClick={() => { setCalculationGroup(group); setCalculationPageIndex(0); }}>{label}</button>
            ))}
          </div>
          <label className="form-label" htmlFor="calculation-search">Find a saved calculation</label>
          <input id="calculation-search" className="form-input" type="search" placeholder="Search name, source or ID" value={calculationQuery}
            onChange={e => { setCalculationQuery(e.target.value); setCalculationPageIndex(0); }} />
          <p role="status">{calculations.total} matching calculations · Page {calculations.current + 1} of {calculations.pages}</p>
        {calculations.total > 0 ? (
          <div className="unassigned-card-grid">
            {calculations.items.map((d) => {
              const latestVer = latest(d);
              const isAnalyzable = !!latestVer && ['executable', 'supported'].includes(latestVer.support_state) && matchingMetricLoads(latestVer, data.snapshots).length > 0;
              const usage = definitionUsage.find(u => u.definition_id === d.id);

              return (
                <div key={d.id} className="unassigned-card">
                  <div className="unassigned-card-title">
                    <span>{d.name}</span>
                    <span className={`status-pill ${isAnalyzable ? 'ready' : 'warn'}`}>
                      {isAnalyzable ? 'Ready to compare' : 'Add SQL for a dataset'}
                    </span>
                  </div>

                  <div className="unassigned-card-meta">
                    <span>Source: {d.source_label}</span>
                    <span>·</span>
                    <span>{d.versions.length} versions</span>
                  </div>

                  {!latestVer?.contract_hash && <p className="disabled-reason">Not linked to a dataset — add the SQL again for a dataset</p>}
                  {!!usage?.used_by.length && <p className="muted-text font-small">Used by {usage.used_by.map(u => u.name).join(', ')}</p>}
                  <div className="unassigned-card-actions">
                    <button type="button" className="button secondary font-small" onClick={() => { setCalculationEdit(d); setCalculationName(d.name); }}>Rename</button>
                    <button type="button" className="button secondary font-small" disabled={busy || !usage?.can_delete}
                      aria-describedby={`calculation-delete-hint-${d.id}`} onClick={() => setCalculationDelete(d)}>Delete</button>
                    <button
                      type="button"
                      className="button primary font-small"
                      onClick={() => {
                        setNewMetricName(d.name.replace(/_/g, ' '));
                        setNewMetricAttachDefId(d.id);
                        setShowCreateModal(true);
                      }}
                    >
                      Create Metric
                    </button>
                    {onComparePair && latestVer && matchingMetricLoads(latestVer, data.snapshots).length > 0 && isAnalyzable && (
                      <button
                        type="button"
                        className="button secondary font-small"
                        onClick={() => startComparison(latestVer, latestVer, 'unassigned_audit')}
                      >
                        Compare
                      </button>
                    )}
                  </div>
                  <p id={`calculation-delete-hint-${d.id}`} className="muted-text font-small">
                    {!usage ? 'Checking whether this calculation is used…' : usage.can_delete ? 'Unused; safe to delete.' : `Kept for ${usage.used_by.map(u => u.name).join(', ') || 'an existing comparison'}.`}
                  </p>
                </div>
              );
            })}
          </div>
        ) : (
          <div style={{ padding: '16px', background: 'var(--panel)', border: '1px solid var(--line)', borderRadius: '6px', color: 'var(--muted)', fontSize: '13px' }}>
            {!visibleCalculations.length ? 'No calculations used by saved cases or metrics yet. Choose Show all to browse other calculations.'
              : calculationQuery.trim() ? 'No saved calculations match this search. Clear the search to browse all calculations.'
              : 'No calculations in this group. Switch groups to see the others.'}
          </div>
        )}
        {calculations.pages > 1 && <div className="report-actions" style={{ marginTop: 12 }}>
          <button className="button secondary" disabled={calculations.current === 0} onClick={() => setCalculationPageIndex(calculations.current - 1)}>Previous calculations</button>
          <button className="button secondary" disabled={calculations.current + 1 >= calculations.pages} onClick={() => setCalculationPageIndex(calculations.current + 1)}>Next calculations</button>
        </div>}
        </div>}
      </div>

      {calculationEdit && <div className="modal-backdrop" role="dialog" aria-modal="true" aria-labelledby="rename-calculation-title" onClick={() => !busy && setCalculationEdit(null)}>
        <form className="modal-card" onClick={e => e.stopPropagation()} onSubmit={handleRenameCalculation}>
          <div className="modal-header"><h3 id="rename-calculation-title">Rename calculation</h3>
            <button type="button" className="modal-close-button" aria-label="Close dialog" disabled={busy} onClick={() => setCalculationEdit(null)}><X size={18} /></button></div>
          <div className="modal-body"><label className="form-label" htmlFor="calculation-name">Name</label>
            <input id="calculation-name" className="form-input" autoFocus required maxLength={160} value={calculationName} onChange={e => setCalculationName(e.target.value)} />
            {error && <p role="alert" className="disabled-reason">{error}</p>}</div>
          <div className="modal-footer"><button type="button" className="button secondary" disabled={busy} onClick={() => setCalculationEdit(null)}>Cancel</button>
            <button className="button primary" disabled={busy || !calculationName.trim()}>{busy ? 'Saving…' : 'Save name'}</button></div>
        </form>
      </div>}
      {calculationDelete && <div className="modal-backdrop" role="dialog" aria-modal="true" aria-labelledby="delete-calculation-title" onClick={() => !busy && setCalculationDelete(null)}>
        <div className="modal-card" onClick={e => e.stopPropagation()}>
          <div className="modal-header"><h3 id="delete-calculation-title">Delete unused calculation?</h3>
            <button type="button" className="modal-close-button" aria-label="Close dialog" disabled={busy} onClick={() => setCalculationDelete(null)}><X size={18} /></button></div>
          <div className="modal-body"><p>Delete “{calculationDelete.name}” and its versions?</p>
            {error && <p role="alert" className="disabled-reason">{error}</p>}</div>
          <div className="modal-footer"><button type="button" className="button secondary" disabled={busy} onClick={() => setCalculationDelete(null)}>Cancel</button>
            <button type="button" className="button primary" disabled={busy} onClick={handleDeleteCalculation}>{busy ? 'Deleting…' : 'Delete calculation'}</button></div>
        </div>
      </div>}

      {/* Create Metric Modal */}
      {showCreateModal && (
        <div
          className="modal-backdrop"
          role="dialog"
          aria-modal="true"
          aria-labelledby="create-metric-title"
          onClick={() => !busy && setShowCreateModal(false)}
        >
            <div
              className="modal-card"
              style={{ maxWidth: '520px' }}
              onClick={(e) => e.stopPropagation()}
            >
              <div className="modal-header">
                <div>
                  <h3 id="create-metric-title" style={{ margin: 0 }}>Create Declared Metric</h3>
                  <p className="modal-subtitle">Define business metric identity, importance tier, and binding.</p>
                </div>
                <button
                  type="button"
                  className="modal-close-button"
                  onClick={() => setShowCreateModal(false)}
                  aria-label="Close dialog"
                >
                  <X size={18} />
                </button>
              </div>

              <form onSubmit={handleCreateMetric} style={{ display: 'flex', flexDirection: 'column', flex: '1 1 auto', minHeight: 0, margin: 0 }}>
                <div className="modal-body" style={{ padding: '24px', display: 'flex', flexDirection: 'column', gap: '20px' }}>
                  <div className="form-group" style={{ marginBottom: 0 }}>
                    <label className="form-label" htmlFor="create-metric-name" style={{ fontSize: '13px', fontWeight: 600, color: 'var(--text-2)', marginBottom: 6 }}>
                      Metric name
                    </label>
                    <input
                      id="create-metric-name"
                      type="text"
                      className="form-input"
                      style={{ width: '100%' }}
                      placeholder="e.g. Net Revenue or Total Order Volume"
                      value={newMetricName}
                      onChange={(e) => setNewMetricName(e.target.value)}
                      required
                      autoFocus
                    />
                  </div>

                  <div className="form-group" style={{ marginBottom: 0 }}>
                    <label className="form-label" htmlFor="create-metric-importance" style={{ fontSize: '13px', fontWeight: 600, color: 'var(--text-2)', marginBottom: 6 }}>
                      Importance
                    </label>
                    <select
                      id="create-metric-importance"
                      className="form-select"
                      style={{ width: '100%' }}
                      value={newMetricImportance}
                      onChange={(e) => setNewMetricImportance(e.target.value as any)}
                    >
                      <option value="critical">Critical (Highest visibility, red badge on material drift)</option>
                      <option value="important">Important (High visibility, standard governance)</option>
                      <option value="standard">Standard (Default analytical governance)</option>
                      <option value="unclassified">Unclassified</option>
                    </select>
                  </div>

                  <div className="form-group" style={{ marginBottom: 0 }}>
                    <label className="form-label" htmlFor="create-metric-def" style={{ fontSize: '13px', fontWeight: 600, color: 'var(--text-2)', marginBottom: 6 }}>
                      Initial implementation (optional)
                    </label>
                    <select
                      id="create-metric-def"
                      className="form-select"
                      style={{ width: '100%' }}
                      value={newMetricAttachDefId}
                      onChange={(e) => setNewMetricAttachDefId(e.target.value)}
                    >
                      <option value="">None (Leave unbound for now)</option>
                      {definitionOptions(false)}
                    </select>
                    <p style={{ margin: '6px 0 0', fontSize: '12px', color: 'var(--muted)' }}>
                      You can attach or change implementations at any time from the metric detail view.
                    </p>
                  </div>
                </div>

                <div className="modal-footer" style={{ padding: '16px 24px', display: 'flex', justifyContent: 'flex-end', gap: '10px', background: 'var(--panel-2)', borderTop: '1px solid var(--line)' }}>
                  <button
                    type="button"
                    className="button secondary"
                    onClick={() => setShowCreateModal(false)}
                  >
                    Cancel
                  </button>
                  <button type="submit" className="button primary" disabled={busy}>
                    {busy ? 'Creating…' : 'Create metric'}
                  </button>
                </div>
              </form>
            </div>
        </div>
      )}
    </div>
  );
}
