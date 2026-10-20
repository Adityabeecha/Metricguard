import { resultUnit, resultDisplayValue, valueWithUnit } from './resultSummary';
import {
  AlertCircle,
  ArrowRight,
  CheckCircle2,
  Database,
  FileCode2,
  GitCompareArrows,
  Plus,
  RefreshCw,
  ShieldAlert,
  ShieldCheck,
} from 'lucide-react';
import {
  latest,
  versionName,
  type Bootstrap,
  type Candidate,
  type Run,
} from './api';
import { type Page } from './router';
import { isDefinedComparison, isExactZero } from './investigationUtils';

interface DashboardProps {
  data: Bootstrap;
  refresh: () => Promise<Bootstrap | null>;
  onNavigate: (page: Page, tab?: string, query?: Record<string, string>) => void;
  onOpenRun?: (run: Run) => void;
  onSelectCandidate?: (candidate: Candidate) => void;
}

interface PriorityItem {
  id: string;
  kind: 'material_drift' | 'candidate_review' | 'refusal' | 'open_run' | 'saved_case';
  badgeLabel: string;
  badgeTone: 'critical' | 'attention' | 'neutral' | 'warn';
  title: string;
  subtitle: string;
  deltaDisplay?: string | null;
  currency?: string;
  actionLabel: string;
  onAction: () => void;
  secondaryActionLabel?: string;
  onSecondaryAction?: () => void;
}

export function Dashboard({
  data,
  refresh,
  onNavigate,
  onOpenRun,
  onSelectCandidate,
}: DashboardProps) {
  const definitions = data.definitions || [];
  const snapshots = data.snapshots || [];
  const runs = data.runs || [];
  const candidates = data.candidates || [];
  const reviews = data.reviews || [];
  const baselines = data.baselines || [];
  const investigations = data.investigations || [];

  const trackedMetrics = data.metrics || [];
  const executableCount = definitions.filter(
    (d) => latest(d)?.support_state === 'executable'
  ).length;

  // Build priority queue items from real workspace state
  const priorityItems: PriorityItem[] = [];

  // 1. Comparisons with a difference that nobody has explained yet. Runs already
  // saved in an investigation, replays, and older repeats of the same comparison
  // are not "needing review": the saved case (listed below) carries the decision.
  const savedRunIds = new Set(investigations.map((inv) => inv.run_id));
  const savedPairs = new Set(investigations.map((inv) => `${inv.baseline_version_id}|${inv.target_version_id}|${inv.snapshot_id}`));
  const purposeOf = (r: Run) => r.purpose ?? (r as unknown as { request?: { purpose?: string } }).request?.purpose;
  const seenPairs = new Set<string>();
  const diffRuns = [...runs]
    .sort((a, b) => (b.created_at || '').localeCompare(a.created_at || ''))
    .filter((r) => {
      const res = r.result;
      if (!res) return false;
      if (savedRunIds.has(r.id) || /replay/.test(purposeOf(r) || '')) return false;
      const pair = `${r.baseline_version_id}|${r.target_version_id}|${r.snapshot_id}`;
      if (savedPairs.has(pair) || seenPairs.has(pair)) return false;
      seenPairs.add(pair);
      const delta = res.delta_target_minus_baseline;
      return !isDefinedComparison(res) || !isExactZero(delta);
    });

  diffRuns.slice(0, 3).forEach((r) => {
    const res = r.result;
    const delta = res?.delta_target_minus_baseline;
    const defined = isDefinedComparison(res);
    const baseName = versionName(definitions, r.baseline_version_id);
    const targetName = versionName(definitions, r.target_version_id);
    const snap = snapshots.find((s) => s.id === r.snapshot_id);

    priorityItems.push({
      id: `run-${r.id}`,
      kind: 'material_drift',
      badgeLabel: defined ? 'Not yet explained' : 'Total undefined',
      badgeTone: 'attention',
      title: `${baseName} → ${targetName}`,
      subtitle: `${snap?.name || 'Fixed snapshot'} · ${res?.explanation_status?.replaceAll('_', ' ') || 'reconciliation complete'}`,
      deltaDisplay: resultDisplayValue(res, delta),
      currency: resultUnit(res, (snap as any)?.unit_label || snap?.currency || ''),
      actionLabel: 'Review and save',
      onAction: () => {
        if (onOpenRun) onOpenRun(r);
        else onNavigate('investigations');
      },
    });
  });

  // 2. Identify candidate pairs requiring review
  const unreviewedCandidates = candidates.filter(
    (c) => c.review_status === 'candidate' || !c.review_status
  );

  unreviewedCandidates.slice(0, 3).forEach((c, idx) => {
    const leftDef = definitions.find((d) => d.id === c.left_definition_id);
    const rightDef = definitions.find((d) => d.id === c.right_definition_id);
    const title = `${leftDef?.name || 'Left definition'} ↔ ${rightDef?.name || 'Right definition'}`;

    priorityItems.push({
      id: `cand-${idx}-${c.left_definition_id}`,
      kind: 'candidate_review',
      badgeLabel: 'Candidate review',
      badgeTone: 'attention',
      title,
      subtitle: `${c.score} heuristic shared points · ${c.reasons.slice(0, 2).join(' · ')}`,
      actionLabel: 'Compare pair',
      onAction: () => {
        if (onSelectCandidate) onSelectCandidate(c);
        else onNavigate('investigations');
      },
      secondaryActionLabel: 'Review',
      onSecondaryAction: () => onNavigate('investigations', 'evidence'),
    });
  });

  // 3. Saved investigation cases requiring formal decision
  investigations.slice(0, 2).forEach((inv) => {
    priorityItems.push({
      id: `inv-${inv.id}`,
      kind: 'saved_case',
      badgeLabel: 'Saved investigation',
      badgeTone: 'neutral',
      title: inv.title || 'Recorded investigation',
      subtitle: `Recorded by ${inv.actor_label || 'Analyst'} · ${new Date(inv.created_at).toLocaleDateString()}`,
      actionLabel: 'View case',
      onAction: () => onNavigate('investigations', undefined, { investigationId: inv.id }),
    });
  });

  // Top attention item for the hero block
  const topAttentionItem = priorityItems[0] || null;
  const remainingItems = topAttentionItem ? priorityItems.slice(1) : [];

  return (
    <div className="dashboard-container">
      {/* A. Page Header */}
      <div className="page-heading">
        <div>
          <div className="eyebrow">
            WORKSPACE PULSE <span className="eyebrow-line" />
          </div>
          <h1>Dashboard</h1>
          <p>Your priority queue for metric changes, investigations, and approvals.</p>
        </div>
        <div className="heading-actions">
          <button
            className="button secondary"
            onClick={() => void refresh()}
            title="Refresh workspace status"
          >
            <RefreshCw size={15} /> Refresh
          </button>
          <button
            className="button secondary"
            onClick={() => onNavigate('investigations')}
          >
            Open saved investigation
          </button>
          <button
            className="button primary"
            onClick={() => onNavigate('start')}
          >
            <Plus size={16} /> Start a comparison
          </button>
        </div>
      </div>

      {/* Empty State 1: No data sources */}
      {snapshots.length === 0 && (
        <section className="dashboard-empty-card">
          <div className="empty-badge-icon">
            <Database size={24} />
          </div>
          <h2>Start with your data</h2>
          <p>
            MetricGuard compares metric logic against frozen evidence. Upload a CSV or
            Parquet file to create a validated snapshot and begin reconciliation.
          </p>
          <button
            className="button primary"
            onClick={() => onNavigate('data')}
          >
            <Database size={16} /> Add dataset <ArrowRight size={16} />
          </button>
        </section>
      )}

      {/* Empty State 2: Snapshots exist, but no definitions */}
      {snapshots.length > 0 && definitions.length === 0 && (
        <section className="dashboard-empty-card">
          <div className="empty-badge-icon">
            <FileCode2 size={24} />
          </div>
          <h2>Define your first metric</h2>
          <p>
            You have {snapshots.length} validated dataset snapshot{snapshots.length > 1 ? 's' : ''} ready.
            Register your baseline and target SQL queries to begin comparing logic.
          </p>
          <div className="heading-actions">
            <button
              className="button primary"
              onClick={() => onNavigate('metrics')}
            >
              <Plus size={16} /> Add definition
            </button>
            <button
              className="button secondary"
              onClick={() => onNavigate('start')}
            >
              Start a comparison <ArrowRight size={15} />
            </button>
          </div>
        </section>
      )}

      {/* Populated States */}
      {snapshots.length > 0 && definitions.length > 0 && (
        <>
          {/* B. Highest-Priority Review Block */}
          {topAttentionItem ? (
            <section className="hero-attention-card">
              <div className="hero-attention-header">
                <div className="hero-status-row">
                  <span className={`badge ${topAttentionItem.badgeTone}`}>
                    {topAttentionItem.badgeTone === 'critical' ? (
                      <ShieldAlert size={14} />
                    ) : (
                      <AlertCircle size={14} />
                    )}
                    {topAttentionItem.badgeLabel}
                  </span>
                  <span className="hero-timestamp">{topAttentionItem.kind === 'saved_case' ? 'Saved' : 'Requires review'}</span>
                </div>
                <h2>{topAttentionItem.kind === 'saved_case' ? topAttentionItem.title : `${topAttentionItem.title} needs an explanation`}</h2>
                <p className="hero-subtitle">{topAttentionItem.subtitle}</p>
              </div>

              {topAttentionItem.deltaDisplay && (
                <div className="hero-drift-kpis">
                  <div className="drift-stat">
                    <span className="stat-label">CHANGE (PROPOSED − CURRENT)</span>
                    <strong className="stat-value error-text">
                      {valueWithUnit(topAttentionItem.deltaDisplay, topAttentionItem.currency || '')}
                    </strong>
                    <small>Measured on the same frozen dataset; a negative change is not automatically a loss</small>
                  </div>
                  <div className="drift-stat">
                    <span className="stat-label">DECISION STATE</span>
                    <strong className="stat-value attention-text">Not saved yet</strong>
                    <small>Save an explanation to record what this difference means</small>
                  </div>
                </div>
              )}

              <div className="hero-attention-actions">
                <button
                  className="button primary"
                  onClick={topAttentionItem.onAction}
                >
                  {topAttentionItem.actionLabel} <ArrowRight size={16} />
                </button>
                {topAttentionItem.onSecondaryAction && (
                  <button
                    className="button secondary"
                    onClick={topAttentionItem.onSecondaryAction}
                  >
                    {topAttentionItem.secondaryActionLabel || 'Review'}
                  </button>
                )}
              </div>
            </section>
          ) : (
            /* Empty State 3: Definitions and data exist, but clean queue */
            <section className="dashboard-clean-card">
              <div className="clean-badge-icon">
                <CheckCircle2 size={24} />
              </div>
              <h2>No metric changes need attention</h2>
              <p>
                All metric definitions match their approved baselines and no investigations are
                currently pending.
              </p>
              <div className="heading-actions">
                <button
                  className="button primary"
                  onClick={() => onNavigate('start')}
                >
                  <Plus size={16} /> Start a comparison
                </button>
                <button
                  className="button secondary"
                  onClick={() => onNavigate('metrics')}
                >
                  View all metrics
                </button>
              </div>
            </section>
          )}

          {/* C. Priority Queue */}
          {remainingItems.length > 0 && (
            <section className="panel queue-panel">
              <div className="panel-header">
                <div>
                  <h2>Priority queue</h2>
                  <p>Open items awaiting analyst review or decision</p>
                </div>
                <span className="count-pill">
                  {remainingItems.length} item{remainingItems.length !== 1 ? 's' : ''}
                </span>
              </div>
              <div className="queue-list">
                {remainingItems.map((item) => (
                  <div className="queue-row" key={item.id}>
                    <div className="queue-row-main">
                      <div className="queue-row-top">
                        <span className={`badge ${item.badgeTone}`}>{item.badgeLabel}</span>
                        <strong>{item.title}</strong>
                      </div>
                      <p className="queue-row-sub">{item.subtitle}</p>
                    </div>
                    {item.deltaDisplay && (
                      <div className="queue-row-metric">
                        <span>DELTA</span>
                        <strong>{valueWithUnit(item.deltaDisplay, item.currency || '')}</strong>
                      </div>
                    )}
                    <div className="queue-row-actions">
                      <button
                        className="button secondary small"
                        onClick={item.onAction}
                      >
                        {item.actionLabel}
                      </button>
                      {item.onSecondaryAction && (
                        <button
                          className="button secondary small"
                          onClick={item.onSecondaryAction}
                        >
                          {item.secondaryActionLabel}
                        </button>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            </section>
          )}

          {/* D. Workspace Context (Compact) */}
          <section className="panel context-panel">
            <div className="panel-header">
              <h2>Workspace context</h2>
              <p>Current analytical surface in this workspace</p>
            </div>
            <div className="context-metrics-grid">
              <button type="button" className="context-card cursor-pointer" onClick={() => onNavigate('metrics')}>
                <div className="context-card-top">
                  <span>TRACKED METRICS</span>
                  <ShieldCheck size={16} />
                </div>
                <strong>{trackedMetrics.length}</strong>
                <small>Business metrics with an owner and importance</small>
              </button>

              <button type="button" className="context-card cursor-pointer" onClick={() => onNavigate('metrics')}>
                <div className="context-card-top">
                  <span>CALCULATION DEFINITIONS</span>
                  <FileCode2 size={16} />
                </div>
                <strong>{definitions.length}</strong>
                <small>{executableCount} of {definitions.length} can run · {reviews.length} reviewed pairs · {baselines.length} baselines</small>
              </button>

              <button type="button" className="context-card cursor-pointer" onClick={() => onNavigate('investigations')}>
                <div className="context-card-top">
                  <span>SAVED INVESTIGATIONS</span>
                  <GitCompareArrows size={16} />
                </div>
                <strong>{investigations.length}</strong>
                <small>{runs.length} comparison run{runs.length === 1 ? '' : 's'} in total</small>
              </button>

              <button type="button" className="context-card cursor-pointer" onClick={() => onNavigate('data')}>
                <div className="context-card-top">
                  <span>DATASETS</span>
                  <Database size={16} />
                </div>
                <strong>{snapshots.length}</strong>
                <small>Frozen, validated data snapshots</small>
              </button>
            </div>
          </section>
        </>
      )}
    </div>
  );
}
