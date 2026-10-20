import { resultUnit, resultDisplayValue, valueWithUnit } from '../resultSummary';
import { useEffect, useMemo, useState } from 'react';
import {
  AlertTriangle,
  ArrowLeft,
  ArrowRight,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Copy,
  FileText,
  GitCommit,
  GitCompareArrows,
  Layers3,
  Play,
  RefreshCw,
  Search,
  ShieldAlert,
  ShieldCheck,
  Upload,
  X,
} from 'lucide-react';
import {
  api,
  type AffectedMetricDiffItem,
  type Bootstrap,
  type DbtChangeSet,
  type DbtRevisionItem,
  type MetricDiffReport,
  type Run,
} from '../api';
import {
  buildVersionPathSteps,
  classifyDiffOutcome,
  formatMaterialityBadge,
  formatRevisionLabel,
  humanizeRefusalReason,
  sortAffectedMetricsByActionRelevance,
} from '../changesUtils';
import { isDefinedComparison, isExactZero } from '../investigationUtils';

// Preserve the backend's fixed-point string rather than round it through Number.
function formatDelta(value: string | null | undefined, currency = ''): string {
  if (value == null) return 'Unavailable';
  return `${!isExactZero(value) && !value.startsWith('-') && !value.startsWith('+') ? '+' : ''}${valueWithUnit(value, currency)}`;
}

interface ChangesViewProps {
  data: Bootstrap;
  refresh: () => Promise<Bootstrap | null>;
  changeId?: string;
  onSelectChange?: (changeId: string | null) => void;
  onNavigate?: (page: 'dashboard' | 'metrics' | 'data' | 'changes' | 'investigations' | 'settings', tab?: string, query?: Record<string, string>) => void;
  onOpenRun?: (run: Run) => void;
}

export function ChangesView({
  data,
  refresh,
  changeId,
  onSelectChange,
  onNavigate,
  onOpenRun,
}: ChangesViewProps) {
  const workspaceId = data.project.id;
  const rawDiffs = data.metric_diffs || [];
  const snapshots = data.snapshots || [];
  const runs = data.runs || [];

  // Local state
  const [searchQuery, setSearchQuery] = useState('');
  const [outcomeFilter, setOutcomeFilter] = useState<'all' | 'analyzed' | 'material' | 'refused'>('all');
  const [metricFilter, setMetricFilter] = useState<'all' | 'material' | 'refused' | 'within_threshold'>('all');

  // Active diff report detail
  const [activeDiff, setActiveDiff] = useState<MetricDiffReport | null>(null);
  const [diffRuns, setDiffRuns] = useState<Record<string, Run>>({});
  useEffect(() => {
    let active = true;
    setDiffRuns({});
    const ids = new Set((activeDiff?.metrics || []).flatMap(m => [
      m.comparison_run_id, m.primary_impact?.run_id,
      m.approved_baseline_impact?.run_id, m.incremental_impact?.run_id,
    ]).filter((id): id is string => Boolean(id)));
    // Bootstrap contains recent runs only. Older diff reports need their frozen
    // results too; never substitute the snapshot's amount unit for missing data.
    void (async () => {
      for (const id of ids) {
        if (!active) return;
        if (runs.some(r => r.id === id)) continue;
        try {
          const run = await api.run(id);
          if (active) setDiffRuns(previous => ({ ...previous, [id]: run }));
        } catch { /* Leave the unit unspecified when the saved run is unavailable. */ }
      }
    })();
    return () => { active = false; };
  }, [activeDiff, data.runs, workspaceId]);
  const [loadingDiff, setLoadingDiff] = useState(false);
  const [diffLoadError, setDiffLoadError] = useState('');

  // Associated change set for resource-level detail
  const [changeSet, setChangeSet] = useState<DbtChangeSet | null>(null);
  const [loadingChangeSet, setLoadingChangeSet] = useState(false);
  const [showResourcesPanel, setShowResourcesPanel] = useState(false);

  // Markdown PR modal
  const [showMarkdownModal, setShowMarkdownModal] = useState(false);
  const [markdownContent, setMarkdownContent] = useState('');
  const [loadingMarkdown, setLoadingMarkdown] = useState(false);
  const [copiedMarkdown, setCopiedMarkdown] = useState(false);

  // Run Metric Diff modal state
  const [showRunModal, setShowRunModal] = useState(false);
  const [revisions, setRevisions] = useState<DbtRevisionItem[]>([]);
  const [loadingRevisions, setLoadingRevisions] = useState(false);
  const [runBaselineId, setRunBaselineId] = useState('');
  const [runTargetId, setRunTargetId] = useState('');
  const [runSnapshotId, setRunSnapshotId] = useState(snapshots[0]?.id || '');
  const [runMode, setRunMode] = useState<'advisory' | 'enforcing'>('advisory');
  const [showAdvancedThresholds, setShowAdvancedThresholds] = useState(false);
  const [runAbsThreshold, setRunAbsThreshold] = useState('');
  const [runRelThreshold, setRunRelThreshold] = useState('');
  const [runError, setRunError] = useState('');
  const [isExecutingDiff, setIsExecutingDiff] = useState(false);

  // Expanded technical refusal IDs
  const [expandedRefusalIds, setExpandedRefusalIds] = useState<Record<string, boolean>>({});

  // Escape key handler for dialogs
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        if (showMarkdownModal) setShowMarkdownModal(false);
        if (showRunModal && !isExecutingDiff) setShowRunModal(false);
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [showMarkdownModal, showRunModal, isExecutingDiff]);

  // Reset local state when workspace changes
  useEffect(() => {
    setActiveDiff(null);
    setChangeSet(null);
    setShowMarkdownModal(false);
    setShowRunModal(false);
    setRunError('');
    setDiffLoadError('');
  }, [workspaceId]);

  // Sync activeDiff with changeId prop
  useEffect(() => {
    if (!changeId) {
      setActiveDiff(null);
      setChangeSet(null);
      return;
    }

    const found = rawDiffs.find((d) => d.id === changeId);
    if (found) {
      setActiveDiff(found);
      loadChangeSetForDiff(found);
    } else {
      // Fetch directly from API
      setLoadingDiff(true);
      setDiffLoadError('');
      api
        .getMetricDiffReport(workspaceId, changeId)
        .then((report) => {
          setActiveDiff(report);
          loadChangeSetForDiff(report);
        })
        .catch((err) => {
          setDiffLoadError(err instanceof Error ? err.message : String(err));
        })
        .finally(() => {
          setLoadingDiff(false);
        });
    }
  }, [changeId, rawDiffs, workspaceId]);

  const loadChangeSetForDiff = (diff: MetricDiffReport) => {
    if (!diff.baseline_revision_id || !diff.target_revision_id) return;
    setLoadingChangeSet(true);
    api
      .getDbtChangeSet(workspaceId, diff.baseline_revision_id, diff.target_revision_id)
      .then((cs) => {
        setChangeSet(cs);
      })
      .catch(() => {
        // Change set optional for secondary resources
      })
      .finally(() => {
        setLoadingChangeSet(false);
      });
  };

  // Open Run Metric Diff dialog & fetch workspace dbt revisions
  const handleOpenRunModal = async () => {
    setShowRunModal(true);
    setRunError('');
    setLoadingRevisions(true);
    try {
      const res = await api.listDbtRevisions(workspaceId);
      const items = res.items || [];
      setRevisions(items);
      if (items.length >= 2) {
        setRunBaselineId(items[1].id);
        setRunTargetId(items[0].id);
      } else if (items.length === 1) {
        setRunBaselineId(items[0].id);
      }
      if (snapshots.length > 0 && !runSnapshotId) {
        setRunSnapshotId(snapshots[0].id);
      }
    } catch (err) {
      setRunError(err instanceof Error ? err.message : 'Failed to fetch revisions');
    } finally {
      setLoadingRevisions(false);
    }
  };

  // Submit Run Metric Diff
  const handleExecuteDiff = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!runBaselineId || !runTargetId || !runSnapshotId) {
      setRunError('Please select baseline revision, target revision, and reference snapshot.');
      return;
    }
    if (runBaselineId === runTargetId) {
      setRunError('Baseline revision and target revision must be different.');
      return;
    }

    setIsExecutingDiff(true);
    setRunError('');

    try {
      const policy: { absolute_threshold?: number; relative_threshold?: number } = {};
      if (runAbsThreshold.trim()) {
        const parsed = Number(runAbsThreshold);
        if (Number.isFinite(parsed)) policy.absolute_threshold = parsed;
      }
      if (runRelThreshold.trim()) {
        const parsed = Number(runRelThreshold) / 100; // e.g. 5% -> 0.05
        if (Number.isFinite(parsed)) policy.relative_threshold = parsed;
      }

      const report = await api.executeMetricDiff(workspaceId, {
        baseline_revision_id: runBaselineId,
        target_revision_id: runTargetId,
        snapshot_id: runSnapshotId,
        mode: runMode,
        materiality_policy: Object.keys(policy).length ? policy : undefined,
      });

      setShowRunModal(false);
      await refresh();
      if (onSelectChange) {
        onSelectChange(report.id);
      } else {
        setActiveDiff(report);
      }
    } catch (err) {
      setRunError(err instanceof Error ? err.message : String(err));
    } finally {
      setIsExecutingDiff(false);
    }
  };

  // Open Markdown PR summary modal
  const handleOpenMarkdownModal = async () => {
    if (!activeDiff) return;
    setShowMarkdownModal(true);
    setCopiedMarkdown(false);
    setLoadingMarkdown(true);
    try {
      const md = await api.getMetricDiffMarkdown(workspaceId, activeDiff.id);
      setMarkdownContent(md);
    } catch {
      setMarkdownContent('Markdown summary could not be retrieved from the server.');
    } finally {
      setLoadingMarkdown(false);
    }
  };

  const handleCopyMarkdown = () => {
    navigator.clipboard.writeText(markdownContent).then(() => {
      setCopiedMarkdown(true);
      setTimeout(() => setCopiedMarkdown(false), 2500);
    });
  };

  // Open investigation from affected metric
  const handleOpenInvestigation = async (m: AffectedMetricDiffItem) => {
    if (!m.comparison_run_id) {
      if (onNavigate) {
        onNavigate('investigations', 'compare', {
          left: m.approved_version_id || m.baseline_revision_version_id || '',
          right: m.target_version_id || '',
        });
      }
      return;
    }

    // 1. Try finding in existing runs
    const existingRun = runs.find((r) => r.id === m.comparison_run_id);
    if (existingRun && onOpenRun) {
      onOpenRun(existingRun);
      return;
    }

    // 2. Fetch run directly from API
    try {
      const fetchedRun = await api.run(m.comparison_run_id);
      if (onOpenRun) {
        onOpenRun(fetchedRun);
      }
    } catch {
      if (onNavigate) {
        onNavigate('investigations', 'compare');
      }
    }
  };

  // Filtered diffs for landing list
  const filteredDiffs = useMemo(() => {
    return rawDiffs.filter((d) => {
      const badge = classifyDiffOutcome(d);
      if (outcomeFilter === 'analyzed' && badge.label !== 'Analyzed') return false;
      if (outcomeFilter === 'material' && d.counts.material_metrics === 0) return false;
      if (outcomeFilter === 'refused' && d.counts.refused_metrics === 0) return false;

      if (searchQuery.trim()) {
        const q = searchQuery.toLowerCase();
        const baseRev = formatRevisionLabel(d.baseline_revision_id, d.source_metadata?.baseline).toLowerCase();
        const targetRev = formatRevisionLabel(d.target_revision_id, d.source_metadata?.target).toLowerCase();
        const snap = snapshots.find((s) => s.id === d.snapshot_id)?.name.toLowerCase() || '';
        return (
          baseRev.includes(q) ||
          targetRev.includes(q) ||
          snap.includes(q) ||
          badge.label.toLowerCase().includes(q)
        );
      }
      return true;
    });
  }, [rawDiffs, outcomeFilter, searchQuery, snapshots]);

  // Sorted and filtered affected metrics for active diff detail
  const sortedMetrics = useMemo(() => {
    if (!activeDiff) return [];
    const all = activeDiff.metrics || [];
    const filtered = all.filter((m) => {
      if (metricFilter === 'material') return Boolean(m.materiality?.is_material ?? m.materiality?.exceeded);
      if (metricFilter === 'refused') return m.execution_status === 'refused';
      if (metricFilter === 'within_threshold') {
        const isMat = Boolean(m.materiality?.is_material ?? m.materiality?.exceeded);
        return m.execution_status === 'executed' && m.primary_impact?.exact_delta != null && !isMat;
      }
      return true;
    });
    return sortAffectedMetricsByActionRelevance(filtered);
  }, [activeDiff, metricFilter]);

  // =========================================================================
  // DETAIL SCREEN (when activeDiff is selected)
  // =========================================================================
  if (loadingDiff) {
    return (
      <div className="panel" style={{ padding: '60px 20px', textAlign: 'center' }}>
        <RefreshCw className="spinning" size={28} color="var(--link)" />
        <h3 style={{ marginTop: '12px', fontSize: '16px', fontWeight: 700 }}>Loading change analysis</h3>
        <p style={{ color: 'var(--muted)', fontSize: '13px' }}>Retrieving revision diff and evidence metrics…</p>
      </div>
    );
  }

  if (diffLoadError) {
    return (
      <div className="panel" style={{ padding: '30px 20px' }}>
        <div className="changes-edge-banner warning-amber" role="alert" style={{ marginBottom: '16px' }}>
          <AlertTriangle size={18} color="var(--warn)" />
          <div>
            <strong>Failed to load change report</strong>
            <div>{diffLoadError}</div>
          </div>
        </div>
        <button
          className="button secondary small"
          onClick={() => {
            if (onSelectChange) onSelectChange(null);
            else setActiveDiff(null);
          }}
        >
          <ArrowLeft size={13} /> Back to Changes history
        </button>
      </div>
    );
  }

  if (activeDiff) {
    const outcome = classifyDiffOutcome(activeDiff);
    const snap = snapshots.find((s) => s.id === activeDiff.snapshot_id);
    const snapName = snap?.name || `Snapshot ${activeDiff.snapshot_id.slice(0, 10)}`;
    const baseLabel = formatRevisionLabel(activeDiff.baseline_revision_id, activeDiff.source_metadata?.baseline);
    const targetLabel = formatRevisionLabel(activeDiff.target_revision_id, activeDiff.source_metadata?.target);
    const declaredUnit = snap?.unit_label || snap?.currency || '';

    const hasRefusals = activeDiff.counts.refused_metrics > 0;
    const hasMaterial = activeDiff.counts.material_metrics > 0;
    const allRefused = activeDiff.counts.total_declared_metrics_affected > 0 && activeDiff.counts.successfully_analyzed_metrics === 0;

    return (
      <div className="changes-workbench-view">
        {/* Navigation & Breadcrumb */}
        <div style={{ marginBottom: '14px' }}>
          <button
            className="button secondary small"
            onClick={() => {
              if (onSelectChange) onSelectChange(null);
              else setActiveDiff(null);
            }}
          >
            <ArrowLeft size={13} /> Back to Changes history
          </button>
        </div>

        {/* Change Summary Header (04.3) */}
        <header className="changes-header-strip">
          <div className="changes-header-left">
            <span className="changes-provider-badge">dbt</span>
            <div className="changes-revision-path">
              <span>{baseLabel}</span>
              <span className="rev-arrow">
                <ArrowRight size={14} />
              </span>
              <span>{targetLabel}</span>
            </div>
            <div className="changes-snapshot-pill">
              <Layers3 size={13} color="var(--link)" />
              <span>Snapshot: <strong>{snapName}</strong></span>
              {snap?.row_count && <small style={{ color: 'var(--muted)' }}>({snap.row_count.toLocaleString('en-US')} rows)</small>}
            </div>
          </div>

          <div className="changes-header-stats">
            <div className="stat-item">
              <strong>{activeDiff.counts.total_changed_resources}</strong>
              <span>resources changed</span>
            </div>
            <div className="stat-item">
              <strong>{activeDiff.counts.total_declared_metrics_affected}</strong>
              <span>metrics affected</span>
            </div>
            <div className={`stat-item ${hasMaterial ? 'material-highlight' : ''}`}>
              <strong>{activeDiff.counts.material_metrics}</strong>
              <span>material drift</span>
            </div>
            <div className={`stat-item ${hasRefusals ? 'refusal-highlight' : ''}`}>
              <strong>{activeDiff.counts.refused_metrics}</strong>
              <span>refused</span>
            </div>
          </div>

          <div className="changes-header-right">
            <span className={`badge ${outcome.className}`}>
              {outcome.label}
            </span>
            <span className="badge neutral">
              {activeDiff.mode === 'enforcing' ? `Enforcing (Exit ${activeDiff.exit_code})` : 'Advisory'}
            </span>
            <button
              className="button secondary small"
              onClick={handleOpenMarkdownModal}
              title="View formatted Markdown report"
            >
              <FileText size={13} /> PR Summary
            </button>
          </div>
        </header>

        {/* Edge banner for CI exit code precedence */}
        {activeDiff.exit_code === 2 && (
          <div className="changes-edge-banner warning-amber" role="alert">
            <AlertTriangle size={18} color="var(--warn)" style={{ flexShrink: 0, marginTop: '1px' }} />
            <div>
              <strong>CI Exit Code 2 (Refusal Precedence)</strong>
              <div>
                MetricGuard refused quantitative analysis on one or more declared metrics. In MetricGuard, analytical refusal
                strictly takes precedence over passing checks and material drift to prevent unanalyzed changes from silently shipping.
              </div>
            </div>
          </div>
        )}

        {/* Edge banner when all affected metrics are refused */}
        {allRefused && (
          <div className="changes-edge-banner warning-amber" role="alert">
            <ShieldAlert size={18} color="var(--warn)" style={{ flexShrink: 0, marginTop: '1px' }} />
            <div>
              <strong>All affected metrics were refused deterministic evaluation</strong>
              <div>
                None of the affected business metrics met IR single-table criteria. Review the specific refusal reasons below
                (e.g. CTEs or JOIN operations) to adjust the model SQL or definition bindings.
              </div>
            </div>
          </div>
        )}

        {/* Edge banner when zero drift across all declared metrics */}
        {!hasMaterial && !hasRefusals && activeDiff.counts.total_declared_metrics_affected > 0 && (
          <div className="changes-edge-banner info-blue" role="status">
            <CheckCircle2 size={18} color="var(--link)" style={{ flexShrink: 0, marginTop: '1px' }} />
            <div>
              <strong>Analyzed with verified exact agreement</strong>
              <div>
                All {activeDiff.counts.total_declared_metrics_affected} affected declared metrics produced identical analytical totals
                on frozen reference snapshot {snapName}.
              </div>
            </div>
          </div>
        )}

        {/* Main Section: Affected Business Metrics (04.4 - 04.8) */}
        <section style={{ marginBottom: '24px' }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '14px', flexWrap: 'wrap', gap: '10px' }}>
            <div>
              <h2 style={{ margin: 0, fontSize: '18px', fontWeight: 800, color: 'var(--ink)' }}>
                Affected business metrics ({activeDiff.counts.total_declared_metrics_affected})
              </h2>
              <p style={{ margin: '2px 0 0', fontSize: '13px', color: 'var(--muted)' }}>
                Evaluated against reference snapshot <strong>{snapName}</strong> holding underlying customer transactions constant.
              </p>
            </div>

            {/* Quick filter pills */}
            {activeDiff.counts.total_declared_metrics_affected > 1 && (
              <div className="tab-pills">
                <button
                  className={`tab-pill ${metricFilter === 'all' ? 'active' : ''}`}
                  onClick={() => setMetricFilter('all')}
                >
                  All ({activeDiff.counts.total_declared_metrics_affected})
                </button>
                {activeDiff.counts.material_metrics > 0 && (
                  <button
                    className={`tab-pill ${metricFilter === 'material' ? 'active' : ''}`}
                    onClick={() => setMetricFilter('material')}
                  >
                    Material ({activeDiff.counts.material_metrics})
                  </button>
                )}
                {activeDiff.counts.refused_metrics > 0 && (
                  <button
                    className={`tab-pill ${metricFilter === 'refused' ? 'active' : ''}`}
                    onClick={() => setMetricFilter('refused')}
                  >
                    Refused ({activeDiff.counts.refused_metrics})
                  </button>
                )}
                <button
                  className={`tab-pill ${metricFilter === 'within_threshold' ? 'active' : ''}`}
                  onClick={() => setMetricFilter('within_threshold')}
                >
                  Within threshold
                </button>
              </div>
            )}
          </div>

          {/* Affected Metric Cards */}
          {sortedMetrics.map((m) => {
            const isRefused = m.execution_status === 'refused';
            const isMaterial = Boolean(m.materiality?.is_material ?? m.materiality?.exceeded);
            const matBadge = m.execution_status === 'executed' && m.primary_impact?.exact_delta == null
              ? { className: 'warning', label: 'Gap unavailable' }
              : formatMaterialityBadge(m);
            const steps = buildVersionPathSteps(m);

            // Approved-baseline drift (V_approved -> V_target)
            const approvedImpact = m.approved_baseline_impact || (m.has_approved_baseline ? m.primary_impact : null);
            const approvedDelta = approvedImpact?.exact_delta;
            const approvedPct = approvedImpact?.relative_change_percent;

            // Incremental revision impact (V_base -> V_target)
            const incrementalImpact = m.incremental_impact || m.primary_impact;
            const incrementalDelta = incrementalImpact?.exact_delta;
            const incrementalPct = incrementalImpact?.relative_change_percent;
            const metricRunId = approvedImpact?.run_id || incrementalImpact?.run_id || m.comparison_run_id;
            const metricResult = runs.find(r => r.id === metricRunId)?.result || (metricRunId ? diffRuns[metricRunId]?.result : null);
            const currency = metricResult ? resultUnit(metricResult, declaredUnit) : '';
            const metricValue = (value: string | null | undefined) => resultDisplayValue(metricResult, value);

            const isExpandedRefusal = Boolean(expandedRefusalIds[m.metric_id]);

            return (
              <article
                className={`changes-metric-card ${isMaterial ? 'is-material' : ''} ${isRefused ? 'is-refused' : ''}`}
                key={m.metric_id}
              >
                {/* Metric Header */}
                <div className="changes-metric-header">
                  <div className="metric-title-group">
                    <h3>{m.display_name}</h3>
                    {/* Importance badge - explicit only, red for critical, blue for important */}
                    <span className={`badge ${m.importance === 'critical' ? 'critical' : m.importance === 'important' ? 'attention' : 'neutral'}`}>
                      {m.importance.toUpperCase()}
                    </span>
                    {/* Analysis State Pill */}
                    <span className={`badge ${isRefused ? 'warning' : 'attention'}`}>
                      {isRefused ? 'Refused' : 'Analyzed'}
                    </span>
                    {/* Materiality Pill */}
                    {!isRefused && (
                      <span className={`badge ${matBadge.className}`}>
                        {matBadge.label}
                      </span>
                    )}
                  </div>

                  {/* Direct Actions */}
                  <div className="changes-metric-actions">
                    {(m.candidate_definition_id || m.comparison_type === 'candidate_vs_active') && onNavigate && (
                      <button
                        className="button secondary small"
                        onClick={() => onNavigate('metrics', 'implementations', { metricId: m.metric_id })}
                      >
                        Review candidate
                      </button>
                    )}
                    {!isRefused && (
                      <button
                        className="button primary small"
                        onClick={() => handleOpenInvestigation(m)}
                      >
                        Open investigation <ArrowRight size={13} />
                      </button>
                    )}
                  </div>
                </div>

                {/* Version Path Visualization (04.5) */}
                <div className="version-path-container">
                  {steps.map((step, idx) => (
                    <div key={idx} style={{ display: 'contents' }}>
                      <div className="version-path-step">
                        <div className={`step-indicator role-${step.role}`}>
                          {step.role === 'approved' ? 'A' : step.role === 'baseline' ? 'B' : 'T'}
                        </div>
                        <div className="step-info">
                          <span className="step-label">{step.label}</span>
                          <span className={`step-value ${!step.isAvailable ? 'unavailable' : ''}`}>
                            {step.isAvailable && step.value != null
                              ? valueWithUnit(metricValue(step.value), metricResult?.ratio?.unit === 'share' ? '%' : currency)
                              : !step.isAvailable
                              ? 'No approved version'
                              : 'Unavailable'}
                          </span>
                        </div>
                      </div>

                      {/* Connector to next step */}
                      {idx < steps.length - 1 && (
                        <div className="version-path-connector">
                          <span className="connector-badge">
                            {idx === 0 && m.has_approved_baseline && approvedDelta
                              ? formatDelta(metricValue(approvedDelta), currency)
                              : idx > 0 && incrementalDelta
                              ? formatDelta(metricValue(incrementalDelta), currency)
                              : '→'}
                          </span>
                        </div>
                      )}
                    </div>
                  ))}
                </div>

                {/* Business Impact Panel (04.6 & 04.20) */}
                {!isRefused ? (
                  <div className="business-impact-grid">
                    {/* Box 1: Approved-Baseline Drift */}
                    <div
                      className={`business-impact-box ${
                        !m.has_approved_baseline
                          ? 'box-unavailable'
                          : isMaterial
                          ? 'box-material'
                          : 'box-analyzed'
                      }`}
                    >
                      <span className="impact-box-label">Approved-baseline drift</span>
                      <div className="impact-delta-row">
                        <span className="impact-delta-val">
                          {m.has_approved_baseline && approvedDelta != null
                            ? formatDelta(metricValue(approvedDelta), currency)
                            : 'Not available'}
                        </span>
                        {m.has_approved_baseline && approvedPct && (
                          <span className={`impact-pct-badge ${isMaterial ? 'material' : 'neutral'}`}>
                            {Number(approvedPct) > 0 ? '+' : ''}
                            {approvedPct}%
                          </span>
                        )}
                      </div>
                      <span className="impact-box-sub">
                        {m.has_approved_baseline && m.approved_version_id
                          ? `from approved version ${m.approved_version_id.slice(0, 10)}`
                          : 'This metric has no approved version.'}
                      </span>
                      <div className="impact-box-footer">
                        <ShieldCheck size={13} color="var(--link)" />
                        <span>
                          {m.has_approved_baseline
                            ? approvedDelta == null
                              ? 'Materiality unavailable (undefined SUM)'
                              : isMaterial
                              ? 'Materiality: Threshold exceeded'
                              : 'Materiality: Within acceptable threshold'
                            : 'Drift amnesia protection active (baseline unapproved)'}
                        </span>
                      </div>
                    </div>

                    {/* Box 2: Incremental Revision Impact */}
                    <div className="business-impact-box box-analyzed">
                      <span className="impact-box-label">This revision impact</span>
                      <div className="impact-delta-row">
                        <span className="impact-delta-val">
                          {incrementalDelta != null
                            ? formatDelta(metricValue(incrementalDelta), currency)
                            : 'Unavailable'}
                        </span>
                        {incrementalPct && (
                          <span className="impact-pct-badge neutral">
                            {Number(incrementalPct) > 0 ? '+' : ''}
                            {incrementalPct}%
                          </span>
                        )}
                      </div>
                      <span className="impact-box-sub">
                        introduced by this revision relative to baseline
                      </span>
                      <div className="impact-box-footer">
                        <GitCommit size={13} color="var(--link)" />
                        <span>Exact decimal difference evaluated without live DB mutations</span>
                      </div>
                    </div>
                  </div>
                ) : (
                  /* Refusal Details (04.8) */
                  <div className="refusal-box">
                    <div className="refusal-header">
                      <AlertTriangle size={16} />
                      <span>Quantitative Analysis Refused (Amber Warning)</span>
                    </div>
                    <div className="refusal-message">
                      {humanizeRefusalReason(m.refusal_reason)}
                    </div>
                    <button
                      className="refusal-tech-toggle"
                      onClick={() =>
                        setExpandedRefusalIds((prev) => ({
                          ...prev,
                          [m.metric_id]: !prev[m.metric_id],
                        }))
                      }
                    >
                      {isExpandedRefusal ? 'Hide technical diagnostics' : 'View reason & diagnostics'}
                    </button>
                    {isExpandedRefusal && (
                      <div className="refusal-tech-details">
                        <div><strong>Support State:</strong> {m.support_state}</div>
                        <div><strong>Raw Diagnostic:</strong> {m.refusal_reason || 'None'}</div>
                        {m.target_version_id && <div><strong>Target Version ID:</strong> {m.target_version_id}</div>}
                        {m.diagnostics && (
                          <div style={{ marginTop: '4px' }}>
                            <strong>Diagnostics payload:</strong>
                            <pre style={{ margin: '4px 0 0' }}>{JSON.stringify(m.diagnostics, null, 2)}</pre>
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                )}
              </article>
            );
          })}

          {sortedMetrics.length === 0 && (
            <div className="small-empty">
              No declared business metrics match the active filter.
            </div>
          )}
        </section>

        {/* Coverage Section (04.9) */}
        {activeDiff.coverage && (
          <section className="coverage-section">
            <div>
              <h3 style={{ margin: 0, fontSize: '16px', fontWeight: 800, color: 'var(--ink)' }}>
                Coverage Analysis
              </h3>
              <p style={{ margin: '2px 0 0', fontSize: '13px', color: 'var(--muted)' }}>
                How much of this revision did MetricGuard actually analyze against frozen evidence?
              </p>
            </div>

            <div className="coverage-views-grid">
              {/* 1. Declared Metric Coverage */}
              <div className="coverage-card">
                <span className="coverage-card-title">
                  <span>Declared Metric Coverage</span>
                  <strong>{activeDiff.coverage.declared_metrics.coverage_pct}%</strong>
                </span>
                <div className="coverage-progress-bar">
                  <div
                    className="progress-segment-analyzed"
                    style={{
                      width: `${activeDiff.coverage.declared_metrics.coverage_pct}%`,
                    }}
                  />
                  <div
                    className="progress-segment-refused"
                    style={{
                      width: `${100 - activeDiff.coverage.declared_metrics.coverage_pct}%`,
                    }}
                  />
                </div>
                <div style={{ fontSize: '12px', color: 'var(--text-2)' }}>
                  <strong>{activeDiff.coverage.declared_metrics.analyzable}</strong> of{' '}
                  <strong>{activeDiff.coverage.declared_metrics.total_affected}</strong> affected business metrics analyzable.
                  {activeDiff.coverage.declared_metrics.refused > 0 && (
                    <span style={{ color: 'var(--warn)', display: 'block', marginTop: '2px' }}>
                      ({activeDiff.coverage.declared_metrics.refused} refused)
                    </span>
                  )}
                </div>
              </div>

              {/* 2. Structural & Resource Coverage */}
              <div className="coverage-card">
                <span className="coverage-card-title">
                  <span>Resource Breakdown</span>
                  <span className="badge neutral">dbt DAG</span>
                </span>
                <div className="coverage-tier-row">
                  <span>Changed resources</span>
                  <strong>{activeDiff.coverage.structural.changed_resources_count}</strong>
                </div>
                <div className="coverage-tier-row">
                  <span>Declared-linked resources</span>
                  <strong>{activeDiff.coverage.structural.declared_metric_linked_resources_count}</strong>
                </div>
                <div className="coverage-tier-row">
                  <span>Undeclared changed resources</span>
                  <strong>{activeDiff.coverage.structural.undeclared_changed_resources_count}</strong>
                </div>
                <div className="coverage-tier-row">
                  <span>Supported executable</span>
                  <strong style={{ color: 'var(--link)' }}>
                    {activeDiff.coverage.structural.supported_executable_resources_count}
                  </strong>
                </div>
              </div>

              {/* 3. Importance-Weighted Coverage */}
              <div className="coverage-card">
                <span className="coverage-card-title">
                  <span>Importance Tiers</span>
                  <span className="badge neutral">Explicit Only</span>
                </span>
                {['critical', 'important', 'standard', 'unclassified'].map((tier) => {
                  const item = activeDiff.coverage.importance_weighted?.[tier];
                  if (!item) return null;
                  return (
                    <div className="coverage-tier-row" key={tier}>
                      <span style={{ textTransform: 'capitalize' }}>
                        {tier === 'critical' ? (
                          <strong style={{ color: 'var(--danger)' }}>Critical</strong>
                        ) : tier === 'important' ? (
                          <strong style={{ color: 'var(--link)' }}>Important</strong>
                        ) : (
                          tier
                        )}
                      </span>
                      <span>
                        {item.affected > 0 ? (
                          <strong>
                            {item.analyzable}/{item.affected} ({item.coverage_pct}%)
                          </strong>
                        ) : (
                          <span style={{ color: 'var(--faint)' }}>0 affected</span>
                        )}
                      </span>
                    </div>
                  );
                })}
              </div>

              {/* 4. Observable Structural Signals */}
              <div className="coverage-card">
                <span className="coverage-card-title">
                  <span>Structural Signals</span>
                  <span className="badge neutral">Non-Metric</span>
                </span>
                <div className="coverage-signals-list">
                  <div>
                    <strong>{activeDiff.coverage.observable_structural_signals.exposure_referenced_changed_resources.count}</strong> exposure-referenced models
                  </div>
                  <div>
                    <strong>{activeDiff.coverage.observable_structural_signals.explicit_dbt_metric_references.count}</strong> dbt metric/semantic nodes
                  </div>
                  <div>
                    <strong>{activeDiff.coverage.observable_structural_signals.other_observable_metadata.tagged_changed_resources.count}</strong> tagged resources
                  </div>
                </div>
              </div>
            </div>
          </section>
        )}

        {/* Changed Resources Section (04.10) */}
        <section className="changed-resources-panel">
          <div
            className="changed-resources-header"
            onClick={() => setShowResourcesPanel((p) => !p)}
          >
            <h3>
              Changed dbt resources ({changeSet?.resources?.length || activeDiff.counts.total_changed_resources})
            </h3>
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
              <span className="badge neutral">Secondary Detail</span>
              {showResourcesPanel ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
            </div>
          </div>

          {showResourcesPanel && (
            <div>
              {loadingChangeSet ? (
                <div style={{ padding: '20px', textAlign: 'center', color: 'var(--muted)' }}>
                  Loading changed resource details…
                </div>
              ) : changeSet && changeSet.resources.length > 0 ? (
                <table className="changed-resources-table">
                  <thead>
                    <tr>
                      <th>Resource Unique ID</th>
                      <th>Category</th>
                      <th>Linked Business Metric</th>
                      <th>Support State</th>
                      <th>Diagnostics / Details</th>
                    </tr>
                  </thead>
                  <tbody>
                    {changeSet.resources.map((res) => {
                      const isExe = res.new_support_state === 'executable';
                      return (
                        <tr key={res.unique_id}>
                          <td>
                            <strong>{res.name}</strong>
                            <div style={{ fontSize: '12px', color: 'var(--muted)' }}>{res.unique_id}</div>
                          </td>
                          <td>
                            <span className="badge neutral">{res.change_category}</span>
                          </td>
                          <td>
                            {res.metric ? (
                              <span className="badge attention">
                                {res.metric.display_name}
                              </span>
                            ) : (
                              <span style={{ color: 'var(--faint)', fontSize: '12px' }}>Undeclared</span>
                            )}
                          </td>
                          <td>
                            <span className={`badge ${isExe ? 'attention' : 'warning'}`}>
                              {res.new_support_state || 'unknown'}
                            </span>
                          </td>
                          <td style={{ fontSize: '12px', color: 'var(--muted)' }}>
                            {res.new_diagnostics?.[0] || res.summary_label || 'None'}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              ) : (
                <div style={{ padding: '20px', textAlign: 'center', color: 'var(--muted)' }}>
                  {activeDiff.undeclared_resources?.length
                    ? `${activeDiff.undeclared_resources.length} undeclared resources changed.`
                    : 'All changed resources map to analyzed business metrics.'}
                </div>
              )}
            </div>
          )}
        </section>

        {/* Markdown PR Summary Modal */}
        {showMarkdownModal && (
          <div
            className="modal-backdrop"
            onClick={() => setShowMarkdownModal(false)}
            role="dialog"
            aria-modal="true"
            aria-labelledby="pr-summary-title"
          >
            <div
              className="modal-card modal-large"
              style={{ maxWidth: '780px' }}
              onClick={(e) => e.stopPropagation()}
            >
              <div className="modal-header">
                <div>
                  <h3 id="pr-summary-title" style={{ margin: 0 }}>
                    Pull Request Summary Report
                  </h3>
                  <p className="modal-subtitle">
                    Automated reconciliation report formatted for pull request comments.
                  </p>
                </div>
                <button
                  type="button"
                  className="modal-close-button"
                  onClick={() => setShowMarkdownModal(false)}
                  aria-label="Close dialog"
                >
                  <X size={16} />
                </button>
              </div>

              <div className="modal-body">
                {loadingMarkdown ? (
                  <div style={{ padding: '30px', textAlign: 'center' }}>
                    <RefreshCw className="spinning" size={24} color="var(--link)" />
                    <p style={{ marginTop: '8px', color: 'var(--muted)', fontSize: '13px' }}>Generating Markdown report…</p>
                  </div>
                ) : (
                  <pre className="markdown-preview-box" style={{ margin: 0 }}>{markdownContent}</pre>
                )}
              </div>

              <div className="modal-footer" style={{ justifyContent: 'space-between' }}>
                <button
                  type="button"
                  className="button secondary"
                  onClick={handleCopyMarkdown}
                  disabled={loadingMarkdown || !markdownContent}
                >
                  <Copy size={14} /> {copiedMarkdown ? 'Copied to clipboard!' : 'Copy Markdown'}
                </button>
                <button
                  type="button"
                  className="button primary"
                  onClick={() => setShowMarkdownModal(false)}
                >
                  Close
                </button>
              </div>
            </div>
          </div>
        )}
      </div>
    );
  }

  // =========================================================================
  // LANDING SCREEN (when no activeDiff is selected)
  // =========================================================================
  return (
    <div>
      {/* Page Heading (04.1) */}
      <div className="page-heading">
        <div>
          <h1>Changes</h1>
          <p>See which metrics a definition or code change moves, before it ships.</p>
        </div>
        <div className="heading-actions">
          <button className="button primary" onClick={handleOpenRunModal}>
            <Play size={15} /> Run metric diff
          </button>
          <button className="button secondary" onClick={() => void refresh()}>
            <RefreshCw size={15} /> Refresh
          </button>
        </div>
      </div>

      {/* Search and Filters Toolbar */}
      {rawDiffs.length > 0 && (
        <div className="catalog-toolbar" style={{ marginBottom: '16px' }}>
          <div className="search-field">
            <Search size={15} color="var(--muted)" />
            <input
              type="text"
              placeholder="Filter by commit SHA, branch, snapshot, or outcome…"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
            />
            {searchQuery && (
              <button className="search-clear" onClick={() => setSearchQuery('')}>
                <X size={13} />
              </button>
            )}
          </div>

          <div className="filter-group">
            <span className="filter-label">Outcome:</span>
            <div className="type-filter-pills">
              <button
                type="button"
                className={`filter-pill ${outcomeFilter === 'all' ? 'active' : ''}`}
                onClick={() => setOutcomeFilter('all')}
              >
                All ({rawDiffs.length})
              </button>
              <button
                type="button"
                className={`filter-pill ${outcomeFilter === 'analyzed' ? 'active' : ''}`}
                onClick={() => setOutcomeFilter('analyzed')}
              >
                Analyzed
              </button>
              <button
                type="button"
                className={`filter-pill ${outcomeFilter === 'material' ? 'active' : ''}`}
                onClick={() => setOutcomeFilter('material')}
              >
                Material drift
              </button>
              <button
                type="button"
                className={`filter-pill ${outcomeFilter === 'refused' ? 'active' : ''}`}
                onClick={() => setOutcomeFilter('refused')}
              >
                Refused
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Main Content */}
      {rawDiffs.length === 0 ? (
        <section className="dashboard-empty-card" style={{ maxWidth: '620px', margin: '32px auto' }}>
          <div className="empty-badge-icon">
            <GitCompareArrows size={26} />
          </div>
          <h2>No metric changes checked yet</h2>
          <p>
            This page shows how a change to your metric definitions moves the numbers, before you ship it.
            There are two ways to start:
          </p>
          <ol className="changes-start">
            <li><b>Compare two versions of a saved metric.</b> Save a case as a metric, then compare its versions from <a href={`/w/${encodeURIComponent(data.project.id)}/metrics`}>Metrics</a>. No other tools needed.</li>
            <li><b>Check a dbt or Power BI change.</b> Import two revisions in <a href={`/w/${encodeURIComponent(data.project.id)}/settings?tab=integrations`}>Settings → Integrations</a>, then run the check here.</li>
          </ol>
          <button className="button primary" onClick={handleOpenRunModal}>
            <Play size={15} /> Check a dbt or Power BI change
          </button>
        </section>
      ) : (
        <section className="panel" style={{ marginBottom: '24px' }}>
          <div className="panel-header">
            <h2>Recent analyzed changes</h2>
            <p>How each definition change moved the metric, checked on frozen data.</p>
          </div>

          {filteredDiffs.length > 0 ? (
            <div className="queue-list">
              {filteredDiffs.map((diff) => {
                const outcome = classifyDiffOutcome(diff);
                const snap = snapshots.find((s) => s.id === diff.snapshot_id);
                const snapLabel = snap?.name || `Snapshot ${diff.snapshot_id.slice(0, 10)}`;
                const baseLabel = formatRevisionLabel(diff.baseline_revision_id, diff.source_metadata?.baseline);
                const targetLabel = formatRevisionLabel(diff.target_revision_id, diff.source_metadata?.target);

                return (
                  <div className="queue-row" key={diff.id}>
                    <div className="queue-row-main">
                      <div className="queue-row-top" style={{ gap: '10px' }}>
                        <span className="changes-provider-badge">dbt</span>
                        <span className={`badge ${outcome.className}`}>
                          {outcome.label}
                        </span>
                        <strong>
                          {baseLabel} → {targetLabel}
                        </strong>
                      </div>
                      <p className="queue-row-sub">
                        Snapshot: <strong>{snapLabel}</strong> · Created {new Date(diff.created_at).toLocaleString()} · Mode: {diff.mode}
                      </p>
                    </div>

                    <div className="queue-row-metric" style={{ minWidth: '220px', textAlign: 'left' }}>
                      <div style={{ display: 'flex', gap: '12px', fontSize: '12px' }}>
                        <span><strong>{diff.counts.total_declared_metrics_affected}</strong> affected</span>
                        <span style={{ color: diff.counts.material_metrics > 0 ? 'var(--danger)' : 'var(--text-2)' }}>
                          <strong>{diff.counts.material_metrics}</strong> material
                        </span>
                        <span style={{ color: diff.counts.refused_metrics > 0 ? 'var(--warn)' : 'var(--text-2)' }}>
                          <strong>{diff.counts.refused_metrics}</strong> refused
                        </span>
                      </div>
                    </div>

                    <div className="queue-row-actions">
                      <button
                        className="button secondary small"
                        onClick={() => {
                          if (onSelectChange) onSelectChange(diff.id);
                          else setActiveDiff(diff);
                        }}
                      >
                        Review impact <ArrowRight size={13} />
                      </button>
                    </div>
                  </div>
                );
              })}
            </div>
          ) : (
            <div className="empty-state" style={{ padding: '36px 20px', textAlign: 'center' }}>
              <p style={{ margin: 0, color: 'var(--muted)', fontSize: '13px' }}>
                No analyzed changes match the selected outcome filter or search query.
              </p>
            </div>
          )}
        </section>
      )}

      {/* Direct Comparison Reconciliation History */}
      {runs.length > 0 && (
        <section className="panel">
          <div className="panel-header">
            <h2>Individual reconciliation runs ({runs.length})</h2>
            <p>Direct comparisons and CI runs recorded in this workspace.</p>
          </div>
          <div className="queue-list">
            {runs.slice(0, 15).map((r) => {
              const res = r.result;
              const delta = res?.delta_target_minus_baseline;
              const defined = isDefinedComparison(res);
              const hasDelta = defined && !isExactZero(delta);
              const snap = snapshots.find((s) => s.id === r.snapshot_id);

              return (
                <div className="queue-row" key={r.id}>
                  <div className="queue-row-main">
                    <div className="queue-row-top">
                      <span className={`badge ${hasDelta ? 'critical' : 'attention'}`}>
                        {!res ? r.state : !defined ? 'Gap unavailable (undefined SUM)' : hasDelta ? 'Differs' : 'Agreement'}
                      </span>
                      <strong>
                        Run {r.id.slice(0, 8)}…
                      </strong>
                    </div>
                    <p className="queue-row-sub">
                      {snap?.name || 'Snapshot'} · {new Date(r.created_at).toLocaleString()} · {r.relationship}
                    </p>
                    {r.state === 'failed' && (
                      <p className="queue-row-sub disabled-reason">
                        Why it failed: {r.error?.message || 'no reason was recorded'}{r.error?.code ? ` (${r.error.code})` : ''}
                      </p>
                    )}
                  </div>
                  {res && (
                    <div className="queue-row-metric">
                      <span>DELTA</span>
                      <strong style={{ color: hasDelta ? 'var(--danger)' : 'var(--link)' }}>
                        {defined ? formatDelta(resultDisplayValue(res, delta), resultUnit(res, snap?.unit_label || snap?.currency || '')) : 'Unavailable'}
                      </strong>
                    </div>
                  )}
                  <div className="queue-row-actions">
                    {onOpenRun && (
                      <button
                        className="button secondary small"
                        onClick={() => onOpenRun(r)}
                      >
                        Investigate <ArrowRight size={13} />
                      </button>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        </section>
      )}

      {/* Run Metric Diff Modal (04.2) */}
      {showRunModal && (
        <div
          className="modal-backdrop"
          onClick={() => !isExecutingDiff && setShowRunModal(false)}
          role="dialog"
          aria-modal="true"
          aria-labelledby="run-diff-title"
        >
          <div
            className="modal-card modal-large"
            style={{ maxWidth: '620px' }}
            onClick={(e) => e.stopPropagation()}
          >
            <div className="modal-header">
              <div>
                <h3 id="run-diff-title" style={{ margin: 0 }}>
                  Run Metric Diff
                </h3>
                <p className="modal-subtitle">
                  Analyze declared business metric impact between two revisions against a frozen snapshot.
                </p>
              </div>
              <button
                type="button"
                className="modal-close-button"
                onClick={() => setShowRunModal(false)}
                disabled={isExecutingDiff}
                aria-label="Close dialog"
              >
                <X size={16} />
              </button>
            </div>

            {loadingRevisions ? (
              <div className="modal-body" style={{ padding: '36px', textAlign: 'center' }}>
                <RefreshCw className="spinning" size={24} color="var(--link)" />
                <p style={{ marginTop: '8px', color: 'var(--muted)', fontSize: '13px' }}>Loading workspace revisions…</p>
              </div>
            ) : revisions.length < 2 ? (
              <div className="modal-body" style={{ padding: '20px' }}>
                <div className="changes-edge-banner warning-amber">
                  <AlertTriangle size={18} color="var(--warn)" style={{ flexShrink: 0 }} />
                  <div>
                    <strong>At least two revisions are required</strong>
                    <div style={{ marginTop: '4px', fontSize: '12px' }}>
                      MetricGuard needs a baseline revision and a target revision to compute change sets. Currently, this workspace has{' '}
                      <strong>{revisions.length}</strong> imported revision(s).
                    </div>
                  </div>
                </div>
                <div style={{ marginTop: '16px', display: 'flex', gap: '10px', justifyContent: 'flex-end' }}>
                  {onNavigate && (
                    <button
                      type="button"
                      className="button primary"
                      onClick={() => {
                        setShowRunModal(false);
                        onNavigate('settings', 'integrations');
                      }}
                    >
                      <Upload size={14} /> Go to Settings → Integrations
                    </button>
                  )}
                  <button
                    type="button"
                    className="button secondary"
                    onClick={() => setShowRunModal(false)}
                  >
                    Close
                  </button>
                </div>
              </div>
            ) : (
              <form onSubmit={handleExecuteDiff} style={{ display: 'flex', flexDirection: 'column', flex: '1 1 auto', minHeight: 0, overflow: 'hidden' }}>
                <div className="modal-body">
                  {runError && (
                    <div className="changes-edge-banner warning-amber" role="alert" style={{ marginBottom: '14px' }}>
                      <AlertTriangle size={16} color="var(--warn)" />
                      <span>{runError}</span>
                    </div>
                  )}

                  <div className="form-group">
                    <label className="form-label">
                      BASELINE REVISION
                    </label>
                    <select
                      className="form-select"
                      value={runBaselineId}
                      onChange={(e) => setRunBaselineId(e.target.value)}
                      disabled={isExecutingDiff}
                      required
                    >
                      {revisions.map((rev) => (
                        <option key={rev.id} value={rev.id}>
                          {formatRevisionLabel(rev.id, rev.source_revision)} · {new Date(rev.created_at).toLocaleDateString()}
                        </option>
                      ))}
                    </select>
                    <small className="field-hint">
                      The stable base branch or prior deployment release.
                    </small>
                  </div>

                  <div className="form-group">
                    <label className="form-label">
                      TARGET REVISION (Candidate)
                    </label>
                    <select
                      className="form-select"
                      value={runTargetId}
                      onChange={(e) => setRunTargetId(e.target.value)}
                      disabled={isExecutingDiff}
                      required
                    >
                      {revisions.map((rev) => (
                        <option key={rev.id} value={rev.id}>
                          {formatRevisionLabel(rev.id, rev.source_revision)} · {new Date(rev.created_at).toLocaleDateString()}
                        </option>
                      ))}
                    </select>
                    <small className="field-hint">
                      The proposed pull request or candidate revision to evaluate.
                    </small>
                  </div>

                  <div className="form-group">
                    <label className="form-label">
                      REFERENCE DATA SNAPSHOT
                    </label>
                    <select
                      className="form-select"
                      value={runSnapshotId}
                      onChange={(e) => setRunSnapshotId(e.target.value)}
                      disabled={isExecutingDiff}
                      required
                    >
                      {snapshots.map((s) => (
                        <option key={s.id} value={s.id}>
                          {s.name} ({s.row_count.toLocaleString('en-US')} rows · {s.unit_label || s.currency || 'records'})
                        </option>
                      ))}
                    </select>
                    <small className="field-hint">
                      Single frozen file holding test data strictly constant across runs.
                    </small>
                  </div>

                  <div className="form-group">
                    <label className="form-label">
                      RECONCILIATION POLICY
                    </label>
                    <div style={{ display: 'flex', gap: '16px' }}>
                      <label style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '13px', cursor: 'pointer' }}>
                        <input
                          type="radio"
                          name="mode"
                          value="advisory"
                          checked={runMode === 'advisory'}
                          onChange={() => setRunMode('advisory')}
                          disabled={isExecutingDiff}
                        />
                        <span>Advisory (informational)</span>
                      </label>
                      <label style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '13px', cursor: 'pointer' }}>
                        <input
                          type="radio"
                          name="mode"
                          value="enforcing"
                          checked={runMode === 'enforcing'}
                          onChange={() => setRunMode('enforcing')}
                          disabled={isExecutingDiff}
                        />
                        <span>Enforcing (strict threshold check)</span>
                      </label>
                    </div>
                  </div>

                  {/* Collapsible Materiality Threshold Policy */}
                  <div>
                    <button
                      type="button"
                      className="refusal-tech-toggle"
                      onClick={() => setShowAdvancedThresholds((p) => !p)}
                    >
                      {showAdvancedThresholds ? 'Hide materiality thresholds' : 'Configure materiality thresholds (optional)'}
                    </button>

                    {showAdvancedThresholds && (
                      <div style={{ marginTop: '10px', background: 'var(--panel-2)', padding: '12px', borderRadius: '6px', border: '1px solid var(--line)' }}>
                        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '10px' }}>
                          <div>
                            <label style={{ display: 'block', fontSize: '12px', fontWeight: 700, color: 'var(--text-2)', marginBottom: '4px' }}>
                              ABSOLUTE THRESHOLD (₹)
                            </label>
                            <input
                              type="number"
                              step="any"
                              className="form-input"
                              placeholder="e.g. 50.00"
                              value={runAbsThreshold}
                              onChange={(e) => setRunAbsThreshold(e.target.value)}
                              disabled={isExecutingDiff}
                            />
                          </div>
                          <div>
                            <label style={{ display: 'block', fontSize: '12px', fontWeight: 700, color: 'var(--text-2)', marginBottom: '4px' }}>
                              RELATIVE THRESHOLD (%)
                            </label>
                            <input
                              type="number"
                              step="any"
                              className="form-input"
                              placeholder="e.g. 5.0"
                              value={runRelThreshold}
                              onChange={(e) => setRunRelThreshold(e.target.value)}
                              disabled={isExecutingDiff}
                            />
                          </div>
                        </div>
                      </div>
                    )}
                  </div>
                </div>

                <div className="modal-footer">
                  <button
                    type="button"
                    className="button secondary"
                    onClick={() => setShowRunModal(false)}
                    disabled={isExecutingDiff}
                  >
                    Cancel
                  </button>
                  <button
                    type="submit"
                    className="button primary"
                    disabled={isExecutingDiff}
                  >
                    {isExecutingDiff ? (
                      <>
                        <RefreshCw className="spinning" size={14} /> Analyzing changes…
                      </>
                    ) : (
                      <>
                        <Play size={14} /> Analyze changes
                      </>
                    )}
                  </button>
                </div>
              </form>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
