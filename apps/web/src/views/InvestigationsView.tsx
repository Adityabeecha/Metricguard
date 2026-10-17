import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  AlertCircle,
  AlertTriangle,
  ArrowLeft,
  ArrowRight,
  Check,
  CheckCircle2,
  Clock,
  Download,
  FileCode2,
  GitCommit,
  GitCompareArrows,
  Play,
  Plus,
  RefreshCw,
  Search,
  ShieldCheck,
  Trash2,
  X,
} from 'lucide-react';
import {
  api,
  ApiError,
  exact,
  versionName,
  type Bootstrap,
  type Context,
  type Evidence,
  type FlatEvidence,
  type InvestigationDraft,
  type InvestigationDraftUpdateInput,
  type InvestigationEditorState,
  type InvestigationDetail,
  type Run,
  type Snapshot,
} from '../api';
import {
  buildComparisonPayload,
  classifyInvestigationError,
  classifyResidual,
  computeSqlDifferences,
  formatInvestigationStatus,
  getSnapshotContractMetadata,
  humanizeMembership,
  type ClassifiedInvestigationError,
} from '../investigationUtils';
import { Shapley } from '../Shapley';

interface InvestigationsViewProps {
  data: Bootstrap;
  refresh: () => Promise<Bootstrap | null>;
  investigationId?: string;
  onSelectInvestigation?: (id: string | null) => void;
  externalRun?: Run | null;
  onClearExternalRun?: () => void;
  initialQuery?: Record<string, string>;
  activeTab?: string;
  onTabChange?: (tab: string) => void;
}

type TabType = 'what_changed' | 'value_bridge' | 'evidence' | 'provenance';
type FilterType = 'all' | 'draft' | 'running' | 'completed' | 'needs_decision';
type DraftWrite = {id: string; workspace: string; patch: InvestigationDraftUpdateInput};

export function defaultContext(
  s?: Snapshot | null,
  data?: { flat_contracts?: any[]; dataset_contracts?: any[] }
): Context {
  const meta = getSnapshotContractMetadata(s, data);
  return {
    start: '2026-08-01',
    end_exclusive: '2026-09-01',
    timezone: s?.timezone || 'Asia/Kolkata',
    currency: meta.unitLabel,
    unit: meta.isFlat ? meta.unitLabel : 'currency',
    grain: s?.grain || 'one_row_per_order_id',
    scope: meta.scope,
    contract_hash: meta.contractHash,
    mapping_hash: s?.mapping_hash ?? null,
  };
}

export function InvestigationsView({
  data,
  refresh,
  investigationId,
  onSelectInvestigation,
  externalRun,
  onClearExternalRun,
  initialQuery,
}: InvestigationsViewProps) {
  const workspaceId = data?.project?.id || 'local-retail';

  // Landing view state
  const [filter, setFilter] = useState<FilterType>('all');
  const [searchQuery, setSearchQuery] = useState('');
  const [serverDrafts, setServerDrafts] = useState<InvestigationDraft[]>(data.investigation_drafts || []);
  const [loadingDrafts, setLoadingDrafts] = useState(false);

  // Studio / Detail state
  const [currentDraft, setCurrentDraft] = useState<InvestigationDraft | null>(null);
  const [draftTitle, setDraftTitle] = useState('');
  const [draftNotes, setDraftNotes] = useState('');
  const [autosaveStatus, setAutosaveStatus] = useState<'saved' | 'saving' | 'unsaved' | 'failed' | 'conflict'>('saved');
  const autosaveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const currentDraftRef = useRef<InvestigationDraft | null>(null);
  const draftTokensRef = useRef(new Map<string, string>());
  const saveChainRef = useRef<Promise<void>>(Promise.resolve());
  const pendingSaveRef = useRef<DraftWrite | null>(null);
  const failedSavesRef = useRef(new Map<string, DraftWrite & {error: unknown; conflict: boolean}>());
  const [loadingStudio, setLoadingStudio] = useState(false);
  const [savedCase, setSavedCase] = useState<InvestigationDetail | null>(null);
  const [resultStale, setResultStale] = useState(false);
  const [runSql, setRunSql] = useState<{runId: string; baseline: string; target: string} | null>(null);
  const [caseDecision, setCaseDecision] = useState('');
  const [caseActor, setCaseActor] = useState('Analyst');
  const [savingCase, setSavingCase] = useState(false);
  const [replayingCase, setReplayingCase] = useState(false);
  useEffect(() => {
    currentDraftRef.current = currentDraft;
    if (currentDraft) draftTokensRef.current.set(currentDraft.id, currentDraft.version_token);
  }, [currentDraft]);

  // Studio inputs
  const defs = data.definitions || [];
  const allVersions = useMemo(
    () =>
      defs.flatMap((d) =>
        d.versions.map((v) => ({
          definition: d,
          version: v,
          displayName: `${d.name} · v${v.version_number}`,
        }))
      ),
    [defs]
  );

  const [baselineVersionId, setBaselineVersionId] = useState('');
  const [targetVersionId, setTargetVersionId] = useState('');
  const [snapshotId, setSnapshotId] = useState(data.snapshots[0]?.id || '');
  const [metricId, setMetricId] = useState<string | null>(null);
  const [relationship, setRelationship] = useState('candidate');

  // Ad-hoc SQL mode toggle
  const [isAdHocSql, setIsAdHocSql] = useState(false);
  const [adHocBaselineName, setAdHocBaselineName] = useState('Baseline calculation');
  const [adHocTargetName, setAdHocTargetName] = useState('Target calculation');
  const [adHocBaselineSql, setAdHocBaselineSql] = useState('SELECT SUM(amount) FROM snapshot');
  const [adHocTargetSql, setAdHocTargetSql] = useState('SELECT SUM(amount) FROM snapshot WHERE status = \'completed\'');
  const editorState = (): InvestigationEditorState => ({mode: isAdHocSql ? 'adhoc' : 'existing', baseline_name: adHocBaselineName,
    target_name: adHocTargetName, baseline_sql: adHocBaselineSql, target_sql: adHocTargetSql});
  const hydrateEditor = (state: InvestigationEditorState) => {
    setIsAdHocSql(state.mode === 'adhoc'); setAdHocBaselineName(state.baseline_name); setAdHocTargetName(state.target_name);
    setAdHocBaselineSql(state.baseline_sql); setAdHocTargetSql(state.target_sql);
  };
  const pinnedEditor = (left?: {raw_sql: string}, right?: {raw_sql: string}): InvestigationEditorState => ({mode: 'existing',
    baseline_name: 'Baseline calculation', target_name: 'Target calculation', baseline_sql: left?.raw_sql || '', target_sql: right?.raw_sql || ''});

  const configurationKey = () => JSON.stringify(isAdHocSql ? {snapshotId, mode: 'adhoc', baseline: adHocBaselineSql, target: adHocTargetSql} : {snapshotId, mode: 'existing', baseline: baselineVersionId, target: targetVersionId});
  const configurationRef = useRef(''); configurationRef.current = configurationKey();
  const studioRouteRef = useRef('');
  const studioGenerationRef = useRef(0);
  const studioRoute = `${workspaceId}:${investigationId || ''}`;
  if (studioRouteRef.current !== studioRoute) {
    studioRouteRef.current = studioRoute; studioGenerationRef.current += 1;
  }
  const studioIdentityRef = useRef(''); studioIdentityRef.current = `${studioRoute}:${studioGenerationRef.current}`;

  // Results & execution
  const [activeRun, setActiveRun] = useState<Run | null>(externalRun || null);
  const [activeTab, setActiveTab] = useState<TabType>('what_changed');
  const [evidenceRows, setEvidenceRows] = useState<Array<Evidence | FlatEvidence>>([]);
  const [evidenceTotal, setEvidenceTotal] = useState(0);
  const [evidenceOffset, setEvidenceOffset] = useState(0);
  const [sortImpact, setSortImpact] = useState(false);
  const [isExecuting, setIsExecuting] = useState(false);
  const [refusalError, setRefusalError] = useState<ClassifiedInvestigationError | null>(null);
  const [operationalError, setOperationalError] = useState<ClassifiedInvestigationError | null>(null);
  const [notice, setNotice] = useState('');

  // Modals & decision actions
  const [showApproveModal, setShowApproveModal] = useState(false);
  const [approveRationale, setApproveRationale] = useState('');
  const [approverActor, setApproverActor] = useState('Engineering Lead');
  const [approving, setApproving] = useState(false);
  const [showTrackMetricModal, setShowTrackMetricModal] = useState(false);
  const [trackMetricName, setTrackMetricName] = useState('');
  const [trackMetricImportance, setTrackMetricImportance] = useState<'critical' | 'important' | 'standard'>('standard');
  const [trackingMetric, setTrackingMetric] = useState(false);

  // Escape key handler for dialogs
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        if (showApproveModal && !approving) setShowApproveModal(false);
        if (showTrackMetricModal && !trackingMetric) setShowTrackMetricModal(false);
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [showApproveModal, showTrackMetricModal, approving, trackingMetric]);

  // Sync server drafts when bootstrap updates
  useEffect(() => {
    if (data.investigation_drafts) {
      setServerDrafts(data.investigation_drafts);
    }
  }, [data.investigation_drafts]);

  // Load server drafts list on mount or refresh
  const reloadDrafts = async () => {
    setLoadingDrafts(true);
    try {
      const res = await api.listInvestigationDrafts(workspaceId);
      setServerDrafts(res.items);
    } catch {
      // Ignore background fetch error
    } finally {
      setLoadingDrafts(false);
    }
  };

  // Keep snapshotId valid when workspace snapshots update
  useEffect(() => {
    if (data.snapshots.length > 0) {
      if (!snapshotId || !data.snapshots.some((s) => s.id === snapshotId)) {
        setSnapshotId(data.snapshots[0].id);
      }
    } else {
      setSnapshotId('');
    }
  }, [data.snapshots, snapshotId]);

  // Reset workspace-bound investigation studio state on workspace change
  useEffect(() => {
    setCurrentDraft(null);
    setActiveRun(null);
    setEvidenceRows([]);
    setRefusalError(null);
    setOperationalError(null);
    setNotice('');
  }, [workspaceId]);

  // Handle external run from Changes / Dashboard
  useEffect(() => {
    if (externalRun) {
      setActiveRun(externalRun);
      setBaselineVersionId(externalRun.baseline_version_id);
      setTargetVersionId(externalRun.target_version_id);
      setSnapshotId(externalRun.snapshot_id);
      setRelationship(externalRun.relationship || 'candidate');
      setDraftTitle(`Investigation: Run ${externalRun.id.slice(0, 8)}`);
      setActiveTab('what_changed');
      if (onClearExternalRun) onClearExternalRun();
    }
  }, [externalRun]);

  // Load draft or investigation when investigationId prop changes
  useEffect(() => {
    let cancelled = false;
    flushAutosave();
    setSavedCase(null); setCurrentDraft(null); currentDraftRef.current = null;
    setActiveRun(null); setEvidenceRows([]); setEvidenceOffset(0); setEvidenceTotal(0);
    setResultStale(false); setRunSql(null); setRefusalError(null); setOperationalError(null);
    setDraftNotes(''); setCaseDecision(''); setCaseActor('Analyst'); setNotice('');
    setMetricId(null); setRelationship('candidate');
    setIsExecuting(false); setSavingCase(false); setReplayingCase(false);
    setApproving(false); setTrackingMetric(false); setShowApproveModal(false); setShowTrackMetricModal(false);
    setLoadingStudio(Boolean(investigationId));
    if (!investigationId) {
      setCurrentDraft(null);
      return;
    }

    if (investigationId === 'new') {
      // Pick initial snapshot: query param or first snapshot in workspace
      const initialSnapId = initialQuery?.snapshotId || data.snapshots[0]?.id || '';
      const initialSnap = data.snapshots.find((s) => s.id === initialSnapId);
      const meta = getSnapshotContractMetadata(initialSnap, data);

      const leftId = initialQuery?.left || allVersions[0]?.version.id || '';
      const rightId = initialQuery?.right || allVersions[1]?.version.id || leftId;
      const initialMetric = initialQuery?.metricId || null;
      const initialRel = initialQuery?.rel || 'candidate';
      const initialContract = data.flat_contracts?.find(c => c.contract_hash === meta.contractHash);
      const example = initialContract?.version === 7 && meta.measure
        ? `SELECT SUM(CASE WHEN ${meta.measure} IS NOT NULL THEN ${meta.measure} ELSE 0 END)\nFROM snapshot`
        : meta.measure ? `SELECT SUM(${meta.measure})\nFROM snapshot` : '';
      const initialEditor: InvestigationEditorState = {mode: initialContract ? 'adhoc' : 'existing', baseline_name: 'Baseline calculation',
        target_name: 'Target calculation', baseline_sql: example, target_sql: example};
      hydrateEditor(initialEditor);

      setBaselineVersionId(leftId);
      setTargetVersionId(rightId);
      setSnapshotId(initialSnapId);
      setMetricId(initialMetric);
      setRelationship(initialRel);
      setDraftTitle('Untitled investigation');
      setDraftNotes('');
      setActiveRun(null);
      setEvidenceRows([]);
      setEvidenceTotal(0);
      setEvidenceOffset(0);
      setRefusalError(null);
      setOperationalError(null);
      setNotice('');
      setAutosaveStatus('saved');

      // Create draft on server immediately to get stable ID
      api
        .createInvestigationDraft(workspaceId, {
          title: 'Untitled investigation',
          baseline_version_id: leftId,
          target_version_id: rightId,
          snapshot_id: initialSnapId,
          metric_id: initialMetric,
          status: 'draft',
          editor_state: initialEditor,
        })
        .then((draft) => {
          if (cancelled) return;
          currentDraftRef.current = draft; draftTokensRef.current.set(draft.id, draft.version_token);
          setCurrentDraft(draft); setLoadingStudio(false);
          if (onSelectInvestigation) {
            onSelectInvestigation(draft.id);
          }
        })
        .catch((err: unknown) => {
          if (cancelled) return;
          setAutosaveStatus('failed'); setLoadingStudio(false);
          setOperationalError(classifyInvestigationError(err));
        });
      return () => { cancelled = true; flushAutosave(); };
    }

    // Load existing draft or run/investigation by ID
    const loadItem = async () => {
      try {
        // Try draft first
        await saveChainRef.current;
        const stored = await api.getInvestigationDraft(workspaceId, investigationId);
        if (cancelled) return;
        const failed = failedSavesRef.current.get(stored.id);
        const draft = failed ? {...stored, ...failed.patch,
          version_token: draftTokensRef.current.get(stored.id) || stored.version_token} as InvestigationDraft : stored;
        setCurrentDraft(draft);
        setDraftTitle(draft.title);
        setDraftNotes(draft.notes || '');
        setBaselineVersionId(draft.baseline_version_id || '');
        setTargetVersionId(draft.target_version_id || '');
        setSnapshotId(draft.snapshot_id || ''); setMetricId(draft.metric_id || null);
        hydrateEditor(draft.editor_state || pinnedEditor(allVersions.find(v => v.version.id === draft.baseline_version_id)?.version,
          allVersions.find(v => v.version.id === draft.target_version_id)?.version));
        setAutosaveStatus(failed ? failed.conflict ? 'conflict' : 'failed' : 'saved');

        if (draft.comparison_run_id) {
          try {
            const run = await api.run(draft.comparison_run_id);
            if (cancelled) return;
            setActiveRun(run);
            if (draft.editor_state?.mode === 'adhoc') {
              const l = allVersions.find(v => v.version.id === run.baseline_version_id)?.version;
              const r = allVersions.find(v => v.version.id === run.target_version_id)?.version;
              setResultStale(!l || !r || draft.editor_state.baseline_sql !== l.raw_sql || draft.editor_state.target_sql !== r.raw_sql || draft.snapshot_id !== run.snapshot_id);
            } else setResultStale(run.snapshot_id !== draft.snapshot_id || run.baseline_version_id !== draft.baseline_version_id || run.target_version_id !== draft.target_version_id);
            if (run.state === 'failed' && run.error) {
              const classified = classifyInvestigationError(run.error);
              if (classified.isRefusal) {
                setRefusalError(classified);
              } else {
                setOperationalError(classified);
              }
            }
          } catch (err: unknown) {
            if (!cancelled) setOperationalError(classifyInvestigationError(err));
          }
        } else if (draft.status === 'refused' || draft.refusal_reason) {
          setActiveRun(null);
          setRefusalError({
            isRefusal: true,
            code: 'REFUSAL',
            title: 'Analytical Refusal',
            message: draft.refusal_reason || 'This metric change was refused by policy as an unsupported construct.',
            guidance:
              (draft.diagnostics?.guidance as string) ||
              (draft.diagnostics?.reason as string) ||
              'Modify the SQL predicate to use supported safe constructs.',
          });
        }
      } catch {
        if (cancelled) return;
        // Fallback: Try general investigation or run
        try {
          const detail = await api.investigation(investigationId);
          if (cancelled) return;
          setSavedCase(detail); setCaseDecision(detail.decision); setCaseActor(detail.actor_label);
          setDraftNotes(detail.decision || '');
          hydrateEditor(pinnedEditor(detail.baseline_version, detail.target_version));
          setDraftTitle(detail.title || `Investigation ${detail.id.slice(0, 8)}`);
          setBaselineVersionId(detail.baseline_version_id);
          setTargetVersionId(detail.target_version_id);
          setSnapshotId(detail.snapshot_id);
          setRelationship(detail.relationship || 'candidate');
          setMetricId((detail as unknown as { metric_id?: string }).metric_id || null);
          setActiveRun(detail.run);
        } catch {
          if (cancelled) return;
          // Check if it's a direct run ID
          try {
            const run = await api.run(investigationId);
            if (cancelled) return;
            hydrateEditor(pinnedEditor(allVersions.find(v => v.version.id === run.baseline_version_id)?.version,
              allVersions.find(v => v.version.id === run.target_version_id)?.version));
            setActiveRun(run);
            setBaselineVersionId(run.baseline_version_id);
            setTargetVersionId(run.target_version_id);
            setSnapshotId(run.snapshot_id);
            setRelationship(run.relationship || 'candidate');
            setDraftTitle(`Investigation: Run ${run.id.slice(0, 8)}`);
          } catch (err: unknown) {
            if (!cancelled) setOperationalError(classifyInvestigationError(err));
          }
        }
      }
    };

    void loadItem().finally(() => {if (!cancelled) setLoadingStudio(false);});
    return () => { cancelled = true; flushAutosave(); };
  }, [investigationId, workspaceId]);

  // Serialize draft writes and flush captured edits before navigation/draft switches.
  const writeDraft = (pending: DraftWrite, retry = false) => {
    const write = saveChainRef.current.then(async () => {
      try {
        const failed = failedSavesRef.current.get(pending.id);
        if (failed) {
          pending = {...pending, patch: {...failed.patch, ...pending.patch}};
          if (!retry || failed.conflict) throw failed.error;
        }
        const updated = await api.updateInvestigationDraft(pending.workspace, pending.id, {
          ...pending.patch, version_token: draftTokensRef.current.get(pending.id),
        });
        draftTokensRef.current.set(updated.id, updated.version_token);
        failedSavesRef.current.delete(updated.id);
        if (currentDraftRef.current?.id === pending.id) {
          currentDraftRef.current = updated; setCurrentDraft(updated); setAutosaveStatus(pendingSaveRef.current ? 'unsaved' : 'saved');
        }
      } catch (err: unknown) {
        const conflict = err instanceof ApiError && err.code === 'STALE_DRAFT';
        failedSavesRef.current.set(pending.id, {...pending, error: err, conflict});
        if (currentDraftRef.current?.id === pending.id) {
          setAutosaveStatus(conflict ? 'conflict' : 'failed');
        }
        throw err;
      }
    });
    saveChainRef.current = write.then(() => undefined, () => undefined);
    return write;
  };
  const flushAutosave = () => {
    if (autosaveTimerRef.current) clearTimeout(autosaveTimerRef.current);
    const pending = pendingSaveRef.current;
    if (!pending) return;
    pendingSaveRef.current = null;
    setAutosaveStatus('saving');
    void writeDraft(pending).catch(() => undefined);
  };
  const queueAutosave = (overrides: Partial<InvestigationDraftUpdateInput>) => {
    const draft = currentDraftRef.current;
    if (!draft) return;
    setAutosaveStatus('unsaved');
    const prior = pendingSaveRef.current?.id === draft.id ? pendingSaveRef.current.patch : {};
    pendingSaveRef.current = {id: draft.id, workspace: workspaceId, patch: {...prior, ...overrides}};
    if (autosaveTimerRef.current) clearTimeout(autosaveTimerRef.current);
    autosaveTimerRef.current = setTimeout(flushAutosave, 800);
  };
  const editEditor = (patch: Partial<InvestigationEditorState>) => {
    const state = {...editorState(), ...patch}; hydrateEditor(state);
    if (activeRun) setResultStale(true);
    queueAutosave({editor_state: state});
  };
  const handleSnapshotChange = (newSnapId: string) => {
    setSnapshotId(newSnapId);
    setActiveRun(null);
    setEvidenceRows([]);
    setEvidenceTotal(0);
    setEvidenceOffset(0);
    setRefusalError(null);
    setOperationalError(null);
    setNotice('');

    // Changing context must never replace an analyst's existing SQL.
    // The supported-syntax example below is available to copy explicitly.

    queueAutosave({
      snapshot_id: newSnapId,
      comparison_run_id: null,
    });
  };

  const handleTitleChange = (val: string) => {
    setDraftTitle(val);
    queueAutosave({title: val});
  };

  const handleNotesChange = (val: string) => {
    setDraftNotes(val);
    queueAutosave({notes: val});
  };
  const retryDraftSave = async () => {
    if (!currentDraft) return;
    const submittedStudio = studioIdentityRef.current;
    flushAutosave(); await saveChainRef.current;
    if (studioIdentityRef.current !== submittedStudio) return;
    const failed = failedSavesRef.current.get(currentDraft.id);
    if (!failed || failed.conflict) return;
    setAutosaveStatus('saving');
    await writeDraft(failed, true).catch(() => undefined);
  };
  const retryDraftCreation = async () => {
    const submittedStudio = studioIdentityRef.current;
    setLoadingStudio(true);
    try {
      const draft = await api.createInvestigationDraft(workspaceId, {
        title: draftTitle || 'Untitled investigation', notes: draftNotes,
        baseline_version_id: baselineVersionId || null, target_version_id: targetVersionId || null,
        snapshot_id: snapshotId || null, metric_id: metricId, status: 'draft', editor_state: editorState(),
      });
      if (studioIdentityRef.current !== submittedStudio) return;
      currentDraftRef.current = draft; draftTokensRef.current.set(draft.id, draft.version_token);
      setCurrentDraft(draft); setAutosaveStatus('saved'); setOperationalError(null);
      onSelectInvestigation?.(draft.id);
    } catch (err: unknown) {
      if (studioIdentityRef.current === submittedStudio) {
        setAutosaveStatus('failed'); setOperationalError(classifyInvestigationError(err));
      }
    } finally {
      if (studioIdentityRef.current === submittedStudio) setLoadingStudio(false);
    }
  };
  const appendDraftNote = async (note: string, submittedStudio: string) => {
    flushAutosave(); await saveChainRef.current;
    if (studioIdentityRef.current !== submittedStudio) return;
    const draft = currentDraftRef.current;
    if (!draft) throw new Error('Open a saved draft before recording an investigation decision.');
    const notes = `${draft.notes ? draft.notes + '\n' : ''}${note}`;
    await writeDraft({id: draft.id, workspace: workspaceId, patch: {status: 'completed', notes}});
    if (studioIdentityRef.current === submittedStudio) setDraftNotes(notes);
  };

  // Poll active run if running
  useEffect(() => {
    if (!activeRun || !['queued', 'running', 'pending'].includes(activeRun.state)) return;
    let cancelled = false;
    const timer = setInterval(async () => {
      try {
        const next = await api.run(activeRun.id);
        if (cancelled) return;
        setActiveRun(next);
        if (!['queued', 'running', 'pending'].includes(next.state)) {
          void refresh();
          if (next.state === 'failed') {
            const classified = classifyInvestigationError(
              next.error || { code: 'RUNNER_FAILED', message: 'Isolated execution did not complete.' }
            );
            if (classified.isRefusal) {
              setRefusalError(classified);
              setOperationalError(null);
            } else {
              setOperationalError(classified);
              setRefusalError(null);
            }
            setNotice('');
          } else if (next.state === 'succeeded') {
            setNotice('');
          }
          if (currentDraft && currentDraftRef.current?.id === currentDraft.id) {
            queueAutosave({status: ['succeeded', 'partial'].includes(next.state) ? 'completed' : 'draft', comparison_run_id: next.id});
            flushAutosave();
          }
        }
      } catch (err: unknown) {
        if (!cancelled) setOperationalError(classifyInvestigationError(err));
      }
    }, 1500);
    return () => { cancelled = true; clearInterval(timer); };
  }, [activeRun?.id, activeRun?.state, currentDraft, workspaceId, refresh, draftTitle, draftNotes]);

  // Load paginated evidence rows when evidence tab is selected
  useEffect(() => {
    if (!activeRun?.result || activeTab !== 'evidence') return;
    let cancelled = false;
    const isFlat = (activeRun.context as { mode?: string })?.mode === 'flat_snapshot';
    const fetchEvidence = isFlat
      ? api.flatEvidence(activeRun.id, evidenceOffset, 100)
      : api.evidence(activeRun.id, evidenceOffset, 100);

    fetchEvidence
      .then((res) => {
        if (!cancelled) {
          setEvidenceRows(res.items);
          setEvidenceTotal(res.total);
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setOperationalError(classifyInvestigationError(err));
        }
      });

    return () => {
      cancelled = true;
    };
  }, [activeRun?.id, activeRun?.result, activeTab, evidenceOffset]);

  // Execute Comparison Run
  const handleRunComparison = async () => {
    const submittedConfig = configurationRef.current, submittedStudio = studioIdentityRef.current;
    setIsExecuting(true);
    setRefusalError(null);
    setOperationalError(null);
    setNotice('');

    try {
      flushAutosave(); await saveChainRef.current;
      if (currentDraft && failedSavesRef.current.has(currentDraft.id)) {
        throw new Error('Save the pending draft edits before starting a comparison. Retry a failed save or resolve the version conflict.');
      }
      if (studioIdentityRef.current !== submittedStudio) return;
      let bVerId = baselineVersionId;
      let tVerId = targetVersionId;

      // Handle Ad-hoc SQL registration if in ad-hoc mode
      const snap = data.snapshots.find((s) => s.id === snapshotId);
      if (isAdHocSql) {
        const contract =
          (snap?.contract_hash
            ? data.flat_contracts?.find((c) => c.contract_hash === snap.contract_hash)
            : null);
        if (!contract) {
          throw new Error('A registered contract or dataset schema is required to evaluate ad-hoc SQL.');
        }
        const bDef = await api.post<{ versions: Array<{ id: string; support_state: string; support_reason?: string }> }>(
          '/flat/definitions',
          { name: adHocBaselineName, raw_sql: adHocBaselineSql, contract_id: contract.id }
        );
        const tDef = await api.post<{ versions: Array<{ id: string; support_state: string; support_reason?: string }> }>(
          '/flat/definitions',
          { name: adHocTargetName, raw_sql: adHocTargetSql, contract_id: contract.id }
        );
        if (studioIdentityRef.current !== submittedStudio) return;
        const bVer = bDef.versions.at(-1);
        const tVer = tDef.versions.at(-1);
        if (!bVer || bVer.support_state !== 'executable') {
          setRefusalError(classifyInvestigationError({
            code: 'UNSUPPORTED_SQL',
            message: bVer?.support_reason || 'Baseline query definition could not be evaluated by the reconciliation engine.',
          }));
          setIsExecuting(false);
          return;
        }
        if (!tVer || tVer.support_state !== 'executable') {
          setRefusalError(classifyInvestigationError({
            code: 'UNSUPPORTED_SQL',
            message: tVer?.support_reason || 'Target query definition could not be evaluated by the reconciliation engine.',
          }));
          setIsExecuting(false);
          return;
        }
        bVerId = bVer.id;
        tVerId = tVer.id;
        setBaselineVersionId(bVerId);
        setTargetVersionId(tVerId);
      }

      const bVer = allVersions.find((v) => v.version.id === bVerId)?.version;
      const tVer = allVersions.find((v) => v.version.id === tVerId)?.version;

      const { endpoint, body } = buildComparisonPayload({
        isAdHocSql,
        baselineVersionId: bVerId,
        targetVersionId: tVerId,
        snapshotId,
        relationship,
        snapshot: snap,
        data,
        baselineVersion: bVer as any,
        targetVersion: tVer as any,
      });

      const run = await api.post<Run>(endpoint, body, { 'Idempotency-Key': crypto.randomUUID() });

      if (studioIdentityRef.current !== submittedStudio) return;
      setActiveRun(run); setResultStale(configurationRef.current !== submittedConfig);
      setRunSql({runId: run.id, baseline: isAdHocSql ? adHocBaselineSql : bVer?.raw_sql || '', target: isAdHocSql ? adHocTargetSql : tVer?.raw_sql || ''});
      setEvidenceRows([]); setEvidenceTotal(0); setEvidenceOffset(0);
      setNotice('Comparison successfully initiated.');

      // Update draft with run ID and status
      if (currentDraft) {
        // Flush newer analyst edits first. The run receipt updates only its
        // identity; it must never replace SQL typed while registration waited.
        flushAutosave();
        const patch: InvestigationDraftUpdateInput = {
          comparison_run_id: run.id,
          status: ['succeeded', 'partial'].includes(run.state) ? 'completed' : ['queued', 'running', 'pending'].includes(run.state) ? 'running' : 'draft',
          ...(configurationRef.current === submittedConfig ? {
            baseline_version_id: bVerId, target_version_id: tVerId, snapshot_id: snapshotId,
          } : {}),
        };
        await writeDraft({workspace: workspaceId, id: currentDraft.id, patch});
      }
    } catch (err: unknown) {
      if (studioIdentityRef.current !== submittedStudio) return;
      const classified = classifyInvestigationError(err);
      if (classified.isRefusal) {
        setRefusalError(classified);
        setOperationalError(null);
      } else {
        setOperationalError(classified);
        setRefusalError(null);
      }
    } finally {
      if (studioIdentityRef.current === submittedStudio) setIsExecuting(false);
    }
  };

  const handleSaveCase = async () => {
    if (!activeRun?.result || !['succeeded', 'partial'].includes(activeRun.state) || resultStale || !caseDecision.trim() || !caseActor.trim()) return;
    const submittedStudio = studioIdentityRef.current;
    setSavingCase(true);
    try {
      flushAutosave(); await saveChainRef.current;
      if (studioIdentityRef.current !== submittedStudio) return;
      if (currentDraft && failedSavesRef.current.has(currentDraft.id)) {
        throw new Error('Save the pending draft edits before saving a pinned case.');
      }
      const saved = await api.saveInvestigation({run_id: activeRun.id, title: draftTitle || 'Investigation', decision: caseDecision, actor_label: caseActor});
      const detail = await api.investigation(saved.id);
      if (studioIdentityRef.current !== submittedStudio) return;
      setSavedCase(detail); await refresh();
      if (studioIdentityRef.current !== submittedStudio) return;
      onSelectInvestigation?.(saved.id);
      setNotice('Saved case with pinned snapshot, definitions, context and result.');
    } catch (err: unknown) {if (studioIdentityRef.current === submittedStudio) setOperationalError(classifyInvestigationError(err));}
    finally {if (studioIdentityRef.current === submittedStudio) setSavingCase(false);}
  };
  const handleReplayCase = async () => {
    if (!savedCase?.replay_available) return;
    const submittedStudio = studioIdentityRef.current;
    setReplayingCase(true);
    try {
      const run = await api.replayInvestigation(savedCase.id);
      if (studioIdentityRef.current !== submittedStudio) return;
      setActiveRun(run); setEvidenceRows([]); setEvidenceTotal(0); setEvidenceOffset(0);
      setNotice('Replay queued using the saved case inputs. Editor changes are not included.');
    } catch (err: unknown) {if (studioIdentityRef.current === submittedStudio) setOperationalError(classifyInvestigationError(err));}
    finally {if (studioIdentityRef.current === submittedStudio) setReplayingCase(false);}
  };

  // Candidate Approval Handler
  const handleApproveCandidate = async () => {
    if (!metricId || !targetVersionId || !approveRationale.trim()) return;
    const submittedStudio = studioIdentityRef.current;
    setApproving(true);
    try {
      await api.approveMetricVersion(
        workspaceId,
        metricId,
        targetVersionId,
        activeRun?.id || undefined,
        undefined
      );
      if (studioIdentityRef.current !== submittedStudio) return;
      setNotice('Candidate definition successfully approved as the active baseline for this metric.');
      setShowApproveModal(false);
      await refresh();
      await appendDraftNote(`Approved by ${approverActor}: ${approveRationale}`, submittedStudio);
    } catch (err: unknown) {
      if (studioIdentityRef.current === submittedStudio) setOperationalError(classifyInvestigationError(err));
    } finally {
      if (studioIdentityRef.current === submittedStudio) setApproving(false);
    }
  };

  // Track as Metric Handler (for Ad-hoc comparisons)
  const handleTrackAsMetric = async () => {
    if (!trackMetricName.trim() || !targetVersionId) return;
    const submittedStudio = studioIdentityRef.current;
    setTrackingMetric(true);
    try {
      const targetDef = allVersions.find((v) => v.version.id === targetVersionId)?.definition;
      if (!targetDef) throw new Error('Reload the registered target definition before tracking this comparison as a metric.');
      const newMetric = await api.createMetric(workspaceId, trackMetricName, trackMetricImportance);
      await api.bindMetricDefinition(workspaceId, newMetric.id, targetDef.id, 'active', targetVersionId);
      if (studioIdentityRef.current !== submittedStudio) return;
      setMetricId(newMetric.id);
      setNotice(`Metric "${newMetric.display_name}" created and tracked.`);
      setShowTrackMetricModal(false);
      await refresh();
      if (studioIdentityRef.current !== submittedStudio) return;
      if (currentDraft) {
        flushAutosave();
        await writeDraft({id: currentDraft.id, workspace: workspaceId, patch: {metric_id: newMetric.id}});
      }
    } catch (err: unknown) {
      if (studioIdentityRef.current === submittedStudio) setOperationalError(classifyInvestigationError(err));
    } finally {
      if (studioIdentityRef.current === submittedStudio) setTrackingMetric(false);
    }
  };

  // Draft Deletion
  const handleDeleteDraft = async (draftIdToDelete: string, e: React.MouseEvent) => {
    e.stopPropagation();
    if (!window.confirm('Delete this investigation draft?')) return;
    try {
      if (pendingSaveRef.current?.id === draftIdToDelete) {
        if (autosaveTimerRef.current) clearTimeout(autosaveTimerRef.current);
        pendingSaveRef.current = null;
      }
      await saveChainRef.current;
      await api.deleteInvestigationDraft(workspaceId, draftIdToDelete);
      failedSavesRef.current.delete(draftIdToDelete); draftTokensRef.current.delete(draftIdToDelete);
      setServerDrafts((prev) => prev.filter((d) => d.id !== draftIdToDelete));
      if (currentDraft?.id === draftIdToDelete) {
        if (onSelectInvestigation) onSelectInvestigation(null);
      }
      setNotice('Draft deleted.');
    } catch (err: unknown) {
      setOperationalError(classifyInvestigationError(err));
    }
  };

  // Context resolution
  const activeMetric = metricId ? data.metrics?.find((m) => m.id === metricId) : null;
  const baselineDefObj = allVersions.find((v) => v.version.id === baselineVersionId);
  const targetDefObj = allVersions.find((v) => v.version.id === targetVersionId);
  const selectedSnapshot = data.snapshots.find((s) => s.id === snapshotId);

  const selectedContract = data.flat_contracts?.find(c => c.contract_hash === selectedSnapshot?.contract_hash);
  const resultSnapshot = activeRun ? (savedCase?.snapshot.id === activeRun.snapshot_id ? savedCase.snapshot : data.snapshots.find(s => s.id === activeRun.snapshot_id)) : selectedSnapshot;
  const resultContractHash = (activeRun?.context as {contract_hash?: string} | undefined)?.contract_hash || resultSnapshot?.contract_hash;
  const resultContract = data.flat_contracts?.find(c => c.contract_hash === resultContractHash);
  const usesBooleanTree = (resultContract?.version ?? selectedContract?.version ?? 0) >= 5;
  const usesCase = activeRun ? resultContract?.version === 7 || activeRun.result?.provenance?.measure_policy === 'flat-case-v1' : selectedContract?.version === 7;
  const runBaseline = savedCase && savedCase.baseline_version.id === activeRun?.baseline_version_id ? savedCase.baseline_version : allVersions.find(v => v.version.id === activeRun?.baseline_version_id)?.version;
  const runTarget = savedCase && savedCase.target_version.id === activeRun?.target_version_id ? savedCase.target_version : allVersions.find(v => v.version.id === activeRun?.target_version_id)?.version;
  const baselineSqlText = activeRun ? runBaseline?.raw_sql || (runSql?.runId === activeRun.id ? runSql.baseline : '') : isAdHocSql ? adHocBaselineSql : baselineDefObj?.version.raw_sql || '';
  const targetSqlText = activeRun ? runTarget?.raw_sql || (runSql?.runId === activeRun.id ? runSql.target : '') : isAdHocSql ? adHocTargetSql : targetDefObj?.version.raw_sql || '';
  const baselineResultLabel = activeRun ? versionName(defs, activeRun.baseline_version_id) : baselineDefObj?.displayName || 'Baseline';
  const targetResultLabel = activeRun ? versionName(defs, activeRun.target_version_id) : targetDefObj?.displayName || 'Target';
  const sqlDiff = useMemo(
    () => usesBooleanTree ? {
      hasDifferences: baselineSqlText.trim() !== targetSqlText.trim(),
      summary: usesCase
        ? 'Compare both complete CASE definitions. Row contributions explain changes in WHEN branches and outer WHERE inclusion together; they are not separate causal attributions.'
        : activeRun?.result?.definition_status === 'same_supported_representation'
        ? 'Both definitions have the same canonical filter tree on this snapshot.'
        : 'Compare the complete filter expressions below. Row evidence explains the total eligibility change; individual boolean branches are not attributed separately.',
      addedFilters: [],
      removedFilters: [],
    } : computeSqlDifferences(baselineSqlText, targetSqlText),
    [baselineSqlText, targetSqlText, usesBooleanTree, usesCase, activeRun?.result?.definition_status]
  );

  // Evidence sorting
  const sortedEvidenceRows = useMemo(() => {
    if (!sortImpact) return evidenceRows;
    return [...evidenceRows].sort((a, b) => {
      const magnitude = (value: string | null) => {
        const [whole = '0', fraction = ''] = (value || '0').replace(/^[-+]/, '').split('.');
        return {whole: whole.replace(/^0+(?=\d)/, ''), fraction: fraction.replace(/0+$/, '')};
      };
      const left = magnitude(a.delta), right = magnitude(b.delta);
      if (left.whole.length !== right.whole.length) return right.whole.length - left.whole.length;
      if (left.whole !== right.whole) return left.whole < right.whole ? 1 : -1;
      const scale = Math.max(left.fraction.length, right.fraction.length);
      const l = left.fraction.padEnd(scale, '0'), r = right.fraction.padEnd(scale, '0');
      return l === r ? 0 : l < r ? 1 : -1;
    });
  }, [evidenceRows, sortImpact]);

  const hasOperands = useMemo(() => {
    return sortedEvidenceRows.some(
      (r) =>
        r.baseline_operands != null ||
        r.target_operands != null ||
        r.baseline_expression_value != null ||
        r.target_expression_value != null ||
        r.baseline_null_term != null ||
        r.target_null_term != null
    );
  }, [sortedEvidenceRows]);

  // Combined Landing List (Drafts + Saved Investigations + Completed Runs)
  const landingItems = useMemo(() => {
    const list: Array<{
      id: string;
      kind: 'draft' | 'saved' | 'run';
      title: string;
      metricName?: string;
      importance?: string;
      leftLabel: string;
      rightLabel: string;
      snapshotName: string;
      status: string;
      deltaDisplay?: string;
      updatedAt: string;
      hasDecision: boolean;
      originalDraft?: InvestigationDraft;
    }> = [];

    // 1. Server Drafts
    serverDrafts.forEach((d) => {
      const met = d.metric_id ? data.metrics?.find((m) => m.id === d.metric_id) : null;
      const bV = allVersions.find((v) => v.version.id === d.baseline_version_id)?.displayName || 'Baseline';
      const tV = allVersions.find((v) => v.version.id === d.target_version_id)?.displayName || 'Target';
      const sN = data.snapshots.find((s) => s.id === d.snapshot_id)?.name || 'Snapshot';
      list.push({
        id: d.id,
        kind: 'draft',
        title: d.title || 'Untitled draft',
        metricName: met?.display_name,
        importance: met?.importance,
        leftLabel: bV,
        rightLabel: tV,
        snapshotName: sN,
        status: d.status || 'draft',
        updatedAt: d.updated_at || d.created_at,
        hasDecision: d.status === 'completed',
        originalDraft: d,
      });
    });

    // 2. Saved Investigations
    (data.investigations || []).forEach((inv) => {
      if (list.some((item) => item.id === inv.id)) return;
      const bV = versionName(defs, inv.baseline_version_id);
      const tV = versionName(defs, inv.target_version_id);
      const sN = data.snapshots.find((s) => s.id === inv.snapshot_id)?.name || 'Snapshot';
      list.push({
        id: inv.id,
        kind: 'saved',
        title: inv.title || 'Saved investigation',
        leftLabel: bV,
        rightLabel: tV,
        snapshotName: sN,
        status: 'completed',
        updatedAt: inv.created_at,
        hasDecision: Boolean(inv.decision),
      });
    });

    return list;
  }, [serverDrafts, data.investigations, data.metrics, data.snapshots, allVersions, defs]);

  // Filtered Landing List
  const filteredLandingItems = useMemo(() => {
    return landingItems.filter((item) => {
      // Status filter
      if (filter === 'draft' && item.status !== 'draft') return false;
      if (filter === 'running' && item.status !== 'running') return false;
      if (filter === 'completed' && item.status !== 'completed') return false;
      if (filter === 'needs_decision' && (item.status === 'completed' || !item.metricName)) return false;

      // Search filter
      if (searchQuery.trim()) {
        const q = searchQuery.toLowerCase();
        const matchesTitle = item.title.toLowerCase().includes(q);
        const matchesMetric = item.metricName?.toLowerCase().includes(q);
        const matchesSnap = item.snapshotName.toLowerCase().includes(q);
        const matchesVersions = item.leftLabel.toLowerCase().includes(q) || item.rightLabel.toLowerCase().includes(q);
        if (!matchesTitle && !matchesMetric && !matchesSnap && !matchesVersions) return false;
      }

      return true;
    });
  }, [landingItems, filter, searchQuery]);

  // --------------------------------------------------------------------------
  // RENDER: LANDING VIEW (05.1)
  // --------------------------------------------------------------------------
  if (!investigationId) {
    return (
      <div className="view-container">
        {/* Top Header */}
        <div className="section-header-row" style={{ marginBottom: 20 }}>
          <div>
            <div className="eyebrow">RECONCILIATION STUDIO · INVESTIGATIONS <span className="eyebrow-line" /></div>
            <h1 style={{ fontSize: 24, fontWeight: 700, margin: '4px 0', color: '#101828' }}>Investigations</h1>
            <p style={{ color: '#475467', fontSize: 14, margin: 0 }}>
              Analyze discrepancies across immutable snapshots, bridge metric drift, and record governance decisions.
            </p>
          </div>
          <div style={{ display: 'flex', gap: 10 }}>
            <button className="button secondary" onClick={() => void reloadDrafts()} disabled={loadingDrafts}>
              <RefreshCw size={15} className={loadingDrafts ? 'spinning' : ''} /> Refresh
            </button>
            <button
              className="button primary"
              onClick={() => {
                if (onSelectInvestigation) onSelectInvestigation('new');
              }}
            >
              <Plus size={16} /> New investigation
            </button>
          </div>
        </div>

        {/* Notice & Error alerts */}
        {notice && (
          <div className="alert success" role="status" style={{ marginBottom: 16 }}>
            <span>{notice}</span>
            <button onClick={() => setNotice('')} aria-label="Dismiss notice"><X size={15} /></button>
          </div>
        )}
        {operationalError && (
          <div className="alert error" role="alert" style={{ marginBottom: 16 }}>
            <AlertCircle size={16} />
            <div style={{ flex: 1 }}>
              <strong>{operationalError.title}:</strong> {operationalError.message}
              {operationalError.guidance && (
                <div style={{ fontSize: 12, marginTop: 4 }}>{operationalError.guidance}</div>
              )}
            </div>
            <button onClick={() => setOperationalError(null)} aria-label="Dismiss error"><X size={15} /></button>
          </div>
        )}

        {/* Filter Strip */}
        <div className="inv-filter-strip">
          <div className="inv-filter-group">
            <button
              className={`inv-filter-pill ${filter === 'all' ? 'active' : ''}`}
              onClick={() => setFilter('all')}
            >
              All ({landingItems.length})
            </button>
            <button
              className={`inv-filter-pill ${filter === 'draft' ? 'active' : ''}`}
              onClick={() => setFilter('draft')}
            >
              Drafts ({landingItems.filter((i) => i.status === 'draft').length})
            </button>
            <button
              className={`inv-filter-pill ${filter === 'running' ? 'active' : ''}`}
              onClick={() => setFilter('running')}
            >
              Running ({landingItems.filter((i) => i.status === 'running').length})
            </button>
            <button
              className={`inv-filter-pill ${filter === 'completed' ? 'active' : ''}`}
              onClick={() => setFilter('completed')}
            >
              Completed ({landingItems.filter((i) => i.status === 'completed').length})
            </button>
            <button
              className={`inv-filter-pill ${filter === 'needs_decision' ? 'active' : ''}`}
              onClick={() => setFilter('needs_decision')}
            >
              Needs decision ({landingItems.filter((i) => i.status !== 'completed' && i.metricName).length})
            </button>
          </div>

          <div style={{ position: 'relative' }}>
            <Search size={15} style={{ position: 'absolute', left: 12, top: 11, color: '#98A2B3' }} />
            <input
              type="text"
              className="inv-search-input"
              style={{ paddingLeft: 34 }}
              placeholder="Search investigations, metrics, snapshots…"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
            />
          </div>
        </div>

        {/* Landing Table */}
        {filteredLandingItems.length === 0 ? (
          <div className="empty-panel" style={{ padding: 48, textAlign: 'center', background: '#FFFFFF', border: '1px solid #E4E7EC', borderRadius: 8 }}>
            <GitCompareArrows size={36} style={{ color: '#245BFF', margin: '0 auto 12px' }} />
            <h3 style={{ fontSize: 16, fontWeight: 700, color: '#101828', marginBottom: 6 }}>
              {searchQuery ? 'No matching investigations found' : 'No investigations analyzed yet'}
            </h3>
            <p style={{ color: '#667085', fontSize: 13, maxWidth: 440, margin: '0 auto 20px' }}>
              Compare two metric definitions against a fixed reference snapshot to understand exact value movement and record governance decisions.
            </p>
            <button
              className="button primary"
              onClick={() => {
                if (onSelectInvestigation) onSelectInvestigation('new');
              }}
            >
              <Plus size={16} /> Start new investigation
            </button>
          </div>
        ) : (
          <div className="table-scroll">
            <table className="inv-landing-table">
              <thead>
                <tr>
                  <th>Investigation & Metric</th>
                  <th>Versions Compared</th>
                  <th>Snapshot</th>
                  <th>Status</th>
                  <th>Last Updated</th>
                  <th style={{ textAlign: 'right' }}>Actions</th>
                </tr>
              </thead>
              <tbody>
                {filteredLandingItems.map((item) => {
                  const statusBadge = formatInvestigationStatus(item.status, Boolean(item.metricName), item.hasDecision);
                  return (
                    <tr
                      key={item.id}
                      style={{ cursor: 'pointer' }}
                      onClick={() => {
                        if (onSelectInvestigation) onSelectInvestigation(item.id);
                      }}
                    >
                      <td>
                        <div style={{ fontWeight: 600, color: '#101828', marginBottom: 2 }}>{item.title}</div>
                        {item.metricName ? (
                          <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, color: '#667085' }}>
                            <FileCode2 size={13} style={{ color: '#245BFF' }} />
                            <span>{item.metricName}</span>
                            {item.importance && (
                              <span className={`importance-tag ${item.importance}`} style={{ fontSize: 12, padding: '1px 5px' }}>
                                {item.importance}
                              </span>
                            )}
                          </div>
                        ) : (
                          <span style={{ fontSize: 12, color: '#98A2B3' }}>Ad-hoc comparison</span>
                        )}
                      </td>
                      <td>
                        <div style={{ fontSize: 12, color: '#344054', display: 'flex', alignItems: 'center', gap: 4 }}>
                          <span className="mono-cell" style={{ fontSize: 12 }}>{item.leftLabel}</span>
                          <ArrowRight size={12} style={{ color: '#98A2B3' }} />
                          <span className="mono-cell" style={{ fontSize: 12, fontWeight: 600 }}>{item.rightLabel}</span>
                        </div>
                      </td>
                      <td>
                        <div style={{ fontSize: 12, color: '#475467' }}>{item.snapshotName}</div>
                      </td>
                      <td>
                        <span className={statusBadge.className}>{statusBadge.label}</span>
                      </td>
                      <td>
                        <span style={{ fontSize: 12, color: '#667085' }}>
                          {item.updatedAt ? new Date(item.updatedAt).toLocaleDateString() : '—'}
                        </span>
                      </td>
                      <td style={{ textAlign: 'right' }}>
                        <div style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
                          <button
                            className="button secondary small"
                            onClick={(e) => {
                              e.stopPropagation();
                              if (onSelectInvestigation) onSelectInvestigation(item.id);
                            }}
                          >
                            Open <ArrowRight size={12} />
                          </button>
                          {item.kind === 'draft' && item.originalDraft && (
                            <button
                              className="button secondary small icon-only"
                              title="Delete draft"
                              onClick={(e) => handleDeleteDraft(item.originalDraft!.id, e)}
                            >
                              <Trash2 size={13} style={{ color: '#D92D20' }} />
                            </button>
                          )}
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    );
  }

  // --------------------------------------------------------------------------
  // RENDER: STUDIO / WORKSPACE VIEW (05.2 - 05.22)
  // --------------------------------------------------------------------------
  if (loadingStudio) return <div className="view-container" role="status">Loading investigation…</div>;
  const runResult = activeRun?.result;
  const definedComparison = !!runResult && runResult.baseline_value != null && runResult.target_value != null && runResult.delta_target_minus_baseline != null;
  const isMaterial = definedComparison && /[1-9]/.test(runResult!.delta_target_minus_baseline!);
  const currencyUnit = activeRun ? (activeRun.context as {unit_label?: string}).unit_label || activeRun.result?.currency || activeRun.context.currency || '' : getSnapshotContractMetadata(selectedSnapshot, data).unitLabel || selectedSnapshot?.currency || '';

  return (
    <div className="view-container">
      {/* Studio Header (05.7, 05.16) */}
      <div className="inv-header">
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, width: '100%' }}>
          <button
            className="button secondary icon-only"
            title="Back to investigations"
            onClick={() => {
              if (onSelectInvestigation) onSelectInvestigation(null);
            }}
          >
            <ArrowLeft size={16} />
          </button>
          <input
            type="text"
            className="inv-title-input"
            value={draftTitle}
            onChange={(e) => handleTitleChange(e.target.value)}
            placeholder="Investigation title…"
          />
        </div>

        <div className="inv-header-actions">
          {/* Autosave status pill (Strictly zero-green: Blue for saved, Red for failed) */}
          <span className={`autosave-indicator ${autosaveStatus}`}>
            {autosaveStatus === 'saving' && <RefreshCw size={12} className="spinning" />}
            {autosaveStatus === 'saved' && <Check size={12} />}
            {autosaveStatus === 'unsaved' && <Clock size={12} />}
            {autosaveStatus === 'failed' && <AlertCircle size={12} />}
            {autosaveStatus === 'conflict' && <AlertTriangle size={12} />}
            <span>
              {autosaveStatus === 'saving' && 'Saving…'}
              {autosaveStatus === 'saved' && (currentDraft ? 'Draft saved' : savedCase ? 'Pinned case' : investigationId === 'new' ? 'Draft not saved' : 'Run view')}
              {autosaveStatus === 'unsaved' && 'Unsaved changes'}
              {autosaveStatus === 'failed' && 'Save failed'}
              {autosaveStatus === 'conflict' && 'Version conflict'}
            </span>
          </span>
          {autosaveStatus === 'failed' && currentDraft && <button className="button secondary small" onClick={() => void retryDraftSave()}>Retry save</button>}
          {investigationId === 'new' && !currentDraft && <button className="button secondary small" onClick={() => void retryDraftCreation()}>Retry creating draft</button>}
          {autosaveStatus === 'conflict' && <span role="status">Edits retained locally. Copy them before reloading to review the newer draft.</span>}

          <button
            className="button primary"
            onClick={handleRunComparison}
            disabled={isExecuting || (investigationId === 'new' && !currentDraft) || (!isAdHocSql && (!baselineVersionId || !targetVersionId || !snapshotId))}
            aria-describedby="studio-run-blocker"
          >
            <Play size={14} /> {isExecuting ? 'Running…' : activeRun ? 'Rerun' : 'Run comparison'}
          </button>
          {!isExecuting && (
            <span id="studio-run-blocker" className="disabled-reason">
              {investigationId === 'new' && !currentDraft ? 'The draft has not been created yet; use “Retry creating draft”.'
                : !isAdHocSql && !snapshotId ? 'Choose a dataset.'
                : !isAdHocSql && (!baselineVersionId || !targetVersionId) ? 'Choose both the current and the proposed definition.' : ''}
            </span>
          )}
        </div>
      </div>

      {/* Context Strip (05.7) */}
      <div className="inv-context-strip" style={{ marginBottom: 20 }}>
        <div className="inv-context-item">
          <span className="inv-context-label">METRIC:</span>
          {activeMetric ? (
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontWeight: 600, color: '#101828' }}>
              <FileCode2 size={14} style={{ color: '#245BFF' }} />
              {activeMetric.display_name}
              <span className={`importance-tag ${activeMetric.importance}`} style={{ fontSize: 12, padding: '1px 5px' }}>
                {activeMetric.importance}
              </span>
            </span>
          ) : (
            <span style={{ color: '#667085', fontWeight: 500 }}>Ad-hoc Comparison</span>
          )}
        </div>

        <div className="inv-context-item">
          <span className="inv-context-label">COMPARING:</span>
          <span className="inv-context-value">
            {activeRun ? baselineResultLabel : baselineDefObj?.displayName || 'Baseline'} → {activeRun ? targetResultLabel : targetDefObj?.displayName || 'Target'}
          </span>
        </div>

        <div className="inv-context-item">
          <span className="inv-context-label">SNAPSHOT:</span>
          <span className="inv-context-value">
            {(activeRun ? resultSnapshot : selectedSnapshot) ? `${(activeRun ? resultSnapshot : selectedSnapshot)?.name} (${(activeRun ? resultSnapshot : selectedSnapshot)?.row_count} rows)` : 'None selected'}
          </span>
        </div>

        <div className="inv-context-item">
          <span className="inv-context-label">STATUS:</span>
          {activeRun ? (
            <span className={`status-pill ${activeRun.state === 'succeeded' ? 'completed' : activeRun.state}`}>
              {activeRun.state.toUpperCase()}
            </span>
          ) : (
            <span className="status-pill draft">DRAFT</span>
          )}
        </div>

        {currentDraft?.origin === 'ci' && (
          <div className="inv-context-item">
            <span className="inv-context-label">ORIGIN:</span>
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontWeight: 600, color: '#245BFF' }}>
              <GitCommit size={14} />
              CI / PR Gate {currentDraft.ci_run_id ? `(${currentDraft.ci_run_id.slice(0, 8)})` : ''}
            </span>
          </div>
        )}
      </div>

      {currentDraft?.origin === 'ci' && activeRun?.result && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '10px 14px', background: '#F8F9FC', border: '1px solid #D0D5DD', borderRadius: 6, marginBottom: 16, fontSize: 13, color: '#344054' }}>
          <ShieldCheck size={16} style={{ color: '#245BFF', flexShrink: 0 }} />
          <span>
            <strong>Deterministic CI Result Loaded:</strong> Reusing analysis from CI Run <code style={{ fontSize: 12 }}>{currentDraft.ci_run_id?.slice(0, 8) || activeRun.id.slice(0, 8)}</code>. Evidence and waterfall metrics are fully preserved with zero recomputation.
          </span>
        </div>
      )}

      {/* Infrastructure / Runtime Error Alert (Deep Red) */}
      {operationalError && (
        <div className="alert error" role="alert" style={{ marginBottom: 18 }}>
          <AlertCircle size={16} />
          <div style={{ flex: 1 }}>
            <strong>{operationalError.title}:</strong> {operationalError.message}
            {operationalError.guidance && (
              <div style={{ fontSize: 12, marginTop: 4 }}>{operationalError.guidance}</div>
            )}
            {operationalError.code && (
              <div style={{ fontSize: 12, opacity: 0.75, marginTop: 4 }}>Code: {operationalError.code}</div>
            )}
          </div>
          <button onClick={() => setOperationalError(null)} aria-label="Dismiss error"><X size={15} /></button>
        </div>
      )}

      {/* Query Refusal / Contract Notice (Amber) */}
      {refusalError && (
        <div className="inv-refusal-card" style={{ marginBottom: 18 }}>
          <div className="inv-refusal-title" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <AlertTriangle size={17} />
            <span style={{ fontWeight: 600 }}>{refusalError.title}</span>
            {refusalError.code && (
              <span className="badge warn" style={{ marginLeft: 'auto', fontSize: 12 }}>{refusalError.code}</span>
            )}
          </div>
          <div className="inv-refusal-reason" style={{ marginTop: 6, fontSize: 13, lineHeight: 1.5, color: '#B45309' }}>
            {refusalError.message}
          </div>
          {refusalError.guidance && (
            <div style={{ marginTop: 8, fontSize: 12, color: '#92400E', background: '#FEF3C7', padding: '6px 10px', borderRadius: 4, border: '1px solid #FDE68A' }}>
              <strong>Helpful guidance:</strong> {selectedContract?.version === 7 ? 'Use exactly SUM(CASE WHEN <supported predicate> THEN the registered measure ELSE 0 END), with an optional supported WHERE.' : refusalError.guidance}
            </div>
          )}
        </div>
      )}

      {/* Success Notice Banner (Blue) */}
      {notice && (
        <div className="alert success" role="status" style={{ marginBottom: 18 }}>
          <CheckCircle2 size={16} />
          <span>{notice}</span>
          <button onClick={() => setNotice('')} aria-label="Dismiss notice"><X size={15} /></button>
        </div>
      )}

      {/* Comparison Setup Panel (05.8) - Collapsible if results exist */}
      <div className="panel" style={{ marginBottom: 24 }}>
        <div className="panel-header" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <div>
            <h2 style={{ fontSize: 15, fontWeight: 700, margin: 0, color: '#101828' }}>Comparison Configuration</h2>
            <p style={{ margin: '2px 0 0', color: '#667085', fontSize: 13 }}>
              Select two definitions and an immutable snapshot to execute deterministic reconciliation.
            </p>
          </div>
          <button
            className="button secondary small"
            onClick={() => editEditor({mode: isAdHocSql ? 'existing' : 'adhoc'})}
          >
            {isAdHocSql ? 'Switch to Definition Catalog' : 'Write Ad-hoc SQL'}
          </button>
        </div>

        <div style={{ padding: '16px 20px' }}>
          {isAdHocSql ? (
            <div>
              <div style={{ marginBottom: 16, maxWidth: 360 }}>
                <label style={{ fontSize: 12, fontWeight: 600, color: '#344054', display: 'block', marginBottom: 4 }}>
                  Reference Snapshot
                </label>
                <select
                  value={snapshotId}
                  onChange={(e) => handleSnapshotChange(e.target.value)}
                  style={{ width: '100%', padding: '7px 10px', borderRadius: 6, border: '1px solid #D0D5DD', fontSize: 13 }}
                >
                  <option value="">Choose snapshot…</option>
                  {data.snapshots.map((s) => (
                    <option key={s.id} value={s.id}>
                      {s.name} ({s.row_count} rows · {getSnapshotContractMetadata(s, data).unitLabel || s.currency || 'records'})
                    </option>
                  ))}
                </select>
              </div>
              <div className="v2-grid two" style={{ gap: 16 }}>
                <div>
                  <label style={{ fontSize: 12, fontWeight: 600, color: '#344054', display: 'block', marginBottom: 4 }}>
                    Baseline Query Name
                  </label>
                  <input
                    type="text"
                    value={adHocBaselineName}
                    onChange={(e) => editEditor({baseline_name: e.target.value})}
                    style={{ width: '100%', padding: '6px 10px', borderRadius: 6, border: '1px solid #D0D5DD', fontSize: 13, marginBottom: 8 }}
                  />
                  <label style={{ fontSize: 12, fontWeight: 600, color: '#344054', display: 'block', marginBottom: 4 }}>
                    Baseline SQL
                  </label>
                  <textarea
                    rows={4}
                    className="code-textarea"
                    value={adHocBaselineSql}
                    onChange={(e) => editEditor({baseline_sql: e.target.value})}
                    style={{ width: '100%', fontFamily: 'monospace', fontSize: 12, padding: 8, borderRadius: 6, border: '1px solid #D0D5DD' }}
                  />
                </div>
                <div>
                  <label style={{ fontSize: 12, fontWeight: 600, color: '#344054', display: 'block', marginBottom: 4 }}>
                    Target Query Name
                  </label>
                  <input
                    type="text"
                    value={adHocTargetName}
                    onChange={(e) => editEditor({target_name: e.target.value})}
                    style={{ width: '100%', padding: '6px 10px', borderRadius: 6, border: '1px solid #D0D5DD', fontSize: 13, marginBottom: 8 }}
                  />
                  <label style={{ fontSize: 12, fontWeight: 600, color: '#344054', display: 'block', marginBottom: 4 }}>
                    Target SQL
                  </label>
                  <textarea
                    rows={4}
                    className="code-textarea"
                    value={adHocTargetSql}
                    onChange={(e) => editEditor({target_sql: e.target.value})}
                    style={{ width: '100%', fontFamily: 'monospace', fontSize: 12, padding: 8, borderRadius: 6, border: '1px solid #D0D5DD' }}
                  />
                </div>
              </div>
              <div style={{
                marginTop: 10,
                padding: '8px 12px',
                background: '#F8FAFC',
                border: '1px solid #E2E8F0',
                borderRadius: 6,
                fontSize: 12,
                color: '#475569',
                display: 'flex',
                alignItems: 'center',
                gap: 8,
                flexWrap: 'wrap'
              }}>
                <span style={{ fontWeight: 600, color: '#245BFF' }}>Supported SQL:</span>
                <span>Queries must evaluate <code>{selectedContract?.version === 7 ? 'SELECT SUM(CASE WHEN <predicate> THEN <measure> ELSE 0 END) FROM snapshot [WHERE …]' : 'SELECT SUM(<measure>) FROM snapshot [WHERE …]'}</code></span>
                {selectedContract?.version === 7 && <span>Editable example: <code>{`SELECT SUM(CASE WHEN ${selectedContract?.measure} IS NOT NULL THEN ${selectedContract?.measure} ELSE 0 END) FROM snapshot`}</code>. This does not infer a business definition. WHEN and WHERE together allow 24 leaves, 96 nodes; SQL AST limit 250.</span>}
                <span style={{ color: '#64748B' }}>·</span>
                <span>Supported filters: <code>=</code>, <code>!=</code>, <code>IN</code>, <code>IS NULL</code>, <code>IS NOT NULL</code>, <code>&gt;</code>, <code>&lt;</code>, <code>&gt;=</code>, <code>&lt;=</code>, <code>BETWEEN</code></span>
                <span>{usesBooleanTree
                  ? 'Compound filters: AND / OR / NOT, up to 16 predicate leaves, 8 levels and 63 tree nodes. SQL NULL semantics apply.'
                  : 'Combine filters with AND. Compound OR / NOT requires a new snapshot with compound filters enabled.'}</span>
              </div>
            </div>
          ) : (
            <div className="form-grid three" style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 16 }}>
              <div>
                <label style={{ fontSize: 12, fontWeight: 600, color: '#344054', display: 'block', marginBottom: 4 }}>
                  Baseline Definition
                </label>
                <select
                  value={baselineVersionId}
                  onChange={(e) => {setBaselineVersionId(e.target.value); if (activeRun) setResultStale(true); queueAutosave({baseline_version_id: e.target.value});}}
                  style={{ width: '100%', padding: '7px 10px', borderRadius: 6, border: '1px solid #D0D5DD', fontSize: 13 }}
                >
                  <option value="">Choose baseline version…</option>
                  {allVersions.map((v) => (
                    <option key={v.version.id} value={v.version.id}>
                      {v.displayName}
                    </option>
                  ))}
                </select>
              </div>

              <div>
                <label style={{ fontSize: 12, fontWeight: 600, color: '#344054', display: 'block', marginBottom: 4 }}>
                  Target Definition
                </label>
                <select
                  value={targetVersionId}
                  onChange={(e) => {setTargetVersionId(e.target.value); if (activeRun) setResultStale(true); queueAutosave({target_version_id: e.target.value});}}
                  style={{ width: '100%', padding: '7px 10px', borderRadius: 6, border: '1px solid #D0D5DD', fontSize: 13 }}
                >
                  <option value="">Choose target version…</option>
                  {allVersions.map((v) => (
                    <option key={v.version.id} value={v.version.id}>
                      {v.displayName}
                    </option>
                  ))}
                </select>
              </div>

              <div>
                <label style={{ fontSize: 12, fontWeight: 600, color: '#344054', display: 'block', marginBottom: 4 }}>
                  Reference Snapshot
                </label>
                <select
                  value={snapshotId}
                  onChange={(e) => handleSnapshotChange(e.target.value)}
                  style={{ width: '100%', padding: '7px 10px', borderRadius: 6, border: '1px solid #D0D5DD', fontSize: 13 }}
                >
                  <option value="">Choose snapshot…</option>
                  {data.snapshots.map((s) => (
                    <option key={s.id} value={s.id}>
                      {s.name} ({s.row_count} rows · {getSnapshotContractMetadata(s, data).unitLabel || s.currency || 'records'})
                    </option>
                  ))}
                </select>
              </div>
            </div>
          )}
        </div>
      </div>

      {activeRun && resultStale && <div className="alert warn" role="status" style={{marginBottom: 16}}>
        The displayed result belongs to run {activeRun.id}. Editor inputs have changed; run a new comparison to evaluate them.
      </div>}
      {activeRun?.result && <div className="panel" style={{padding: 16, marginBottom: 20}}>
        <h3 style={{fontSize: 14, margin: '0 0 8px'}}>Pinned case</h3>
        {savedCase ? <>
          <p style={{fontSize: 13}}>Saved decision: {savedCase.decision} · Recorded by {savedCase.actor_label}</p>
          <p className="mono-cell" style={{fontSize: 12}}>Case {savedCase.id} · Snapshot {savedCase.snapshot_id}</p>
          <button className="button secondary" onClick={handleReplayCase} disabled={!savedCase.replay_available || replayingCase || ['queued', 'running'].includes(activeRun.state)}>
            <RefreshCw size={14}/>{replayingCase ? 'Queuing replay…' : 'Replay pinned case'}
          </button>
          {!savedCase.replay_available && <p style={{fontSize: 12}}>The pinned snapshot is unavailable for replay.</p>}
        </> : <>
          <label style={{display: 'block', fontSize: 12}}>Decision / rationale
            <textarea value={caseDecision} maxLength={2000} onChange={e => setCaseDecision(e.target.value)} rows={2} style={{width: '100%'}}/>
          </label>
          <label style={{fontSize: 12}}>Recorded by <input value={caseActor} maxLength={160} onChange={e => setCaseActor(e.target.value)}/></label>
          <button className="button secondary" style={{marginLeft: 12}} onClick={handleSaveCase} disabled={savingCase || !['succeeded', 'partial'].includes(activeRun.state) || resultStale || !caseDecision.trim() || !caseActor.trim()}>
            {savingCase ? 'Saving case…' : 'Save pinned case'}
          </button>
        </>}
      </div>}
      {runResult && runResult.limitations.length > 0 && <div className="panel" style={{padding: 16, marginBottom: 16}}>
        <h3 style={{fontSize: 14, margin: '0 0 6px'}}>Result limitations</h3>
        <ul style={{margin: 0, paddingLeft: 18, fontSize: 13}}>{runResult.limitations.map((item, i) => <li key={i}>{item}</li>)}</ul>
      </div>}
      {/* KPI Result Strip (05.7) */}
      {runResult && (
        <div className="inv-kpi-grid">
          <div className="inv-kpi-card">
            <div className="inv-kpi-label">BASELINE RESULT</div>
            <div className="inv-kpi-val">
              {exact(runResult.baseline_value)} {currencyUnit}
            </div>
            <div className="inv-kpi-sub">
              {baselineResultLabel}
            </div>
          </div>

          <div className="inv-kpi-card">
            <div className="inv-kpi-label">TARGET RESULT</div>
            <div className="inv-kpi-val">
              {exact(runResult.target_value)} {currencyUnit}
            </div>
            <div className="inv-kpi-sub">
              {targetResultLabel}
            </div>
          </div>

          <div className="inv-kpi-card">
            <div className="inv-kpi-label">REVISION IMPACT (TARGET − BASELINE)</div>
            <div className={`inv-kpi-val ${isMaterial ? 'delta-material' : 'delta-normal'}`}>
              {runResult.delta_target_minus_baseline != null && /[1-9]/.test(runResult.delta_target_minus_baseline) && !runResult.delta_target_minus_baseline.startsWith('-') ? '+' : ''}
              {exact(runResult.delta_target_minus_baseline)} {currencyUnit}
            </div>
            <div className="inv-kpi-sub">
              {!definedComparison ? 'Gap unavailable: undefined SUM' : isMaterial ? 'Nonzero difference' : 'Defined zero difference'}
            </div>
          </div>
        </div>
      )}

      {/* 4 Standard Result Tabs (05.8) */}
      {runResult && (
        <div style={{ marginTop: 24 }}>
          <div className="inv-nav-tabs">
            <button
              className={`inv-tab-btn ${activeTab === 'what_changed' ? 'active' : ''}`}
              onClick={() => setActiveTab('what_changed')}
            >
              What changed
            </button>
            <button
              className={`inv-tab-btn ${activeTab === 'value_bridge' ? 'active' : ''}`}
              onClick={() => setActiveTab('value_bridge')}
            >
              Value bridge
            </button>
            <button
              className={`inv-tab-btn ${activeTab === 'evidence' ? 'active' : ''}`}
              onClick={() => setActiveTab('evidence')}
            >
              Evidence ({evidenceTotal || 0} rows)
            </button>
            <button
              className={`inv-tab-btn ${activeTab === 'provenance' ? 'active' : ''}`}
              onClick={() => setActiveTab('provenance')}
            >
              Provenance
            </button>
          </div>

          {/* TAB 1: WHAT CHANGED */}
          {activeTab === 'what_changed' && (
            <div>
              <div className="sql-diff-summary-banner">
                <FileCode2 size={16} />
                <span><strong>{usesCase ? 'Definition Analysis:' : 'Filter Analysis:'}</strong> {sqlDiff.summary}</span>
              </div>

              {sqlDiff.addedFilters.length > 0 && (
                <div style={{ marginBottom: 12, padding: '10px 14px', background: '#F9FAFB', border: '1px solid #E4E7EC', borderRadius: 6 }}>
                  <strong style={{ fontSize: 12, color: '#344054' }}>Constraints added by Target:</strong>
                  <ul style={{ margin: '4px 0 0 16px', fontSize: 12, color: '#475467' }}>
                    {sqlDiff.addedFilters.map((f, idx) => (
                      <li key={idx}><code style={{ color: '#1E46C9' }}>{f}</code></li>
                    ))}
                  </ul>
                </div>
              )}

              <div className="sql-diff-grid">
                <div className="sql-diff-col">
                  <div className="sql-diff-header">
                    <span>BASELINE: {baselineResultLabel}</span>
                    <span style={{ fontSize: 12, color: '#667085' }}>SQL AST</span>
                  </div>
                  <pre className="sql-diff-body">{baselineSqlText || '/* No SQL available */'}</pre>
                </div>

                <div className="sql-diff-col">
                  <div className="sql-diff-header">
                    <span>TARGET: {targetResultLabel}</span>
                    <span style={{ fontSize: 12, color: '#245BFF' }}>REVISED SQL</span>
                  </div>
                  <pre className="sql-diff-body">{targetSqlText || '/* No SQL available */'}</pre>
                </div>
              </div>
            </div>
          )}

          {/* TAB 2: VALUE BRIDGE */}
          {activeTab === 'value_bridge' && (
            <div>
              <div className="panel" style={{ marginBottom: 20 }}>
                <div className="panel-header">
                  <h2 style={{ fontSize: 15, fontWeight: 700, margin: 0 }}>Value Decomposition Waterfall</h2>
                  <p style={{ margin: '2px 0 0', color: '#667085', fontSize: 13 }}>
                    {usesCase ? 'One whole-definition contribution combines CASE branch selection and outer WHERE changes. No individual predicate or causal attribution is claimed.' : 'Step-by-step contribution of filter and formula modifications to the final delta.'}
                  </p>
                </div>
                <div style={{ padding: '20px' }}>
                  <div style={{ display: 'grid', gridTemplateColumns: 'minmax(180px, 1fr) auto minmax(180px, 1fr) auto minmax(180px, 1fr)', alignItems: 'center', gap: 12 }}>
                    <div style={{ padding: '14px 18px', background: '#F8F9FC', border: '1px solid #E4E7EC', borderRadius: 6 }}>
                      <div style={{ fontSize: 12, color: '#667085', fontWeight: 600, letterSpacing: '0.3px' }}>STARTING BASELINE</div>
                      <div style={{ fontSize: 18, fontWeight: 700, color: '#101828', fontVariantNumeric: 'tabular-nums', marginTop: 4 }}>
                        {exact(runResult.baseline_value)} {currencyUnit}
                      </div>
                    </div>
                    <ArrowRight size={18} style={{ color: '#98A2B3', flexShrink: 0 }} />
                    <div style={{ padding: '14px 18px', background: isMaterial ? '#FEF3F2' : '#F2F5FF', border: `1px solid ${isMaterial ? '#FECDCA' : '#C9DAFF'}`, borderRadius: 6 }}>
                      <div style={{ fontSize: 12, color: isMaterial ? '#B42318' : '#1E46C9', fontWeight: 600, letterSpacing: '0.3px' }}>{usesCase ? 'WHOLE-DEFINITION DELTA' : 'REVISION DELTA'}</div>
                      <div style={{ fontSize: 18, fontWeight: 700, color: isMaterial ? '#D92D20' : '#245BFF', fontVariantNumeric: 'tabular-nums', marginTop: 4 }}>
                        {exact(runResult.delta_target_minus_baseline)} {currencyUnit}
                      </div>
                    </div>
                    <ArrowRight size={18} style={{ color: '#98A2B3', flexShrink: 0 }} />
                    <div style={{ padding: '14px 18px', background: '#F8F9FC', border: '1px solid #E4E7EC', borderRadius: 6 }}>
                      <div style={{ fontSize: 12, color: '#667085', fontWeight: 600, letterSpacing: '0.3px' }}>ENDING TARGET</div>
                      <div style={{ fontSize: 18, fontWeight: 700, color: '#101828', fontVariantNumeric: 'tabular-nums', marginTop: 4 }}>
                        {exact(runResult.target_value)} {currencyUnit}
                      </div>
                    </div>
                  </div>
                </div>
              </div>

              {/* Shapley Allocations if available */}
              {runResult.shapley && (
                <div style={{ marginBottom: 20 }}>
                  <Shapley value={runResult.shapley} />
                </div>
              )}

              {/* Residual Analysis Banner */}
              <div className="panel" style={{ padding: '14px 18px', background: '#F9FAFB', border: '1px solid #E4E7EC', borderRadius: 8 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                  <ShieldCheck size={18} style={{ color: '#245BFF' }} />
                  <div>
                    <div style={{ fontSize: 13, fontWeight: 700, color: '#101828' }}>
                      Residual Variance Analysis
                    </div>
                    <div style={{ fontSize: 12, color: '#475467' }}>
                      Status: <strong>{runResult.residual == null ? 'Residual not applicable: undefined endpoint' : classifyResidual(runResult.residual, currencyUnit)}</strong>. {runResult.residual != null && 'Exact row-contribution reconciliation applies to this result.'}
                    </div>
                  </div>
                </div>
              </div>
            </div>
          )}

          {/* TAB 3: EVIDENCE */}
          {activeTab === 'evidence' && (
            <div>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                  <span style={{ fontSize: 13, color: '#475467' }}>
                    Showing rows {evidenceOffset + 1}–{Math.min(evidenceOffset + 100, evidenceTotal)} of {evidenceTotal}
                  </span>
                  <label className="checkbox-field-standard">
                    <input
                      type="checkbox"
                      checked={sortImpact}
                      onChange={(e) => setSortImpact(e.target.checked)}
                    />
                    <span>Sort by absolute impact</span>
                  </label>
                </div>

                <div style={{ display: 'flex', gap: 8 }}>
                  <button type="button" className="button secondary small" onClick={() => void api.download(`/comparisons/${encodeURIComponent(activeRun.id)}/export?format=json`, `comparison-${activeRun.id}.json`).catch(e => window.alert(e instanceof Error ? e.message : String(e)))}>
                    <Download size={13} /> JSON (first 100)
                  </button>
                  <button type="button" className="button secondary small" onClick={() => void api.download(`/comparisons/${encodeURIComponent(activeRun.id)}/export?format=html`, `comparison-${activeRun.id}.html`).catch(e => window.alert(e instanceof Error ? e.message : String(e)))}>
                    <Download size={13} /> HTML (first 100)
                  </button>
                </div>
              </div>

              <div className="table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th>Row Identifier</th>
                      <th>{usesCase ? 'WHERE membership' : 'Membership'}</th>
                      {usesCase ? <><th>Source measure</th><th>Baseline CASE</th><th>Target CASE</th></> : hasOperands && <th>Operands & Expression</th>}
                      <th>Baseline inclusion / contribution</th>
                      <th>Target inclusion / contribution</th>
                      <th>Row Delta ({currencyUnit})</th>
                    </tr>
                  </thead>
                  <tbody>
                    {sortedEvidenceRows.map((row, idx) => {
                      const rowKey = (row as FlatEvidence).row_key || row.order_id || `row-${idx}`;
                      const memLabel = humanizeMembership(
                        row.membership,
                        row.baseline_included,
                        row.target_included
                      );
                      const memClass =
                        memLabel === 'Both'
                          ? 'both'
                          : memLabel === 'Baseline only'
                          ? 'baseline'
                          : memLabel === 'Target only'
                          ? 'target'
                          : 'neither';

                      return (
                        <tr key={`${rowKey}-${idx}`}>
                          <td className="mono-cell">{rowKey}</td>
                          <td>
                            <span className={`evidence-membership-pill ${memClass}`}>
                              {memLabel}
                            </span>
                          </td>
                          {usesCase && <>
                            <td className="mono-cell">{row.measure_value === null ? 'NULL' : row.measure_value ?? 'Unavailable'}</td>
                            {(['baseline', 'target'] as const).map(side => <td key={side} className="mono-cell" style={{fontSize: 12}}>
                              <div>WHERE: {row[`${side}_where_state`]?.toUpperCase() ?? 'Unavailable'}</div>
                              <div>WHEN: {row[`${side}_when_state`]?.toUpperCase() ?? 'Unavailable'}</div>
                              <div>Branch: {row[`${side}_branch`]?.toUpperCase() ?? 'Unavailable'}</div>
                              <div>Term: {row[`${side}_case_term`] === null ? 'NULL' : row[`${side}_case_term`] ?? 'Unavailable'}</div>
                              <div>Included NULL term: {row[`${side}_null_term`] === undefined ? 'Unavailable' : row[`${side}_null_term`] ? 'Yes' : 'No'}</div>
                            </td>)}
                          </>}
                          {!usesCase && hasOperands && (
                            <td className="mono-cell" style={{ fontSize: 12 }}>
                              {(() => {
                                const ops = row.target_operands || row.baseline_operands;
                                const isNullTerm = row.baseline_null_term || row.target_null_term;
                                const exprVal = row.target_expression_value ?? row.baseline_expression_value;
                                if (!ops && exprVal == null) return <span style={{ color: '#98A2B3' }}>—</span>;
                                return (
                                  <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                                    {ops && (
                                      <span style={{ color: '#344054' }}>
                                        {Object.entries(ops)
                                          .map(([k, v]) => `${k}=${v ?? 'NULL'}`)
                                          .join(', ')}
                                      </span>
                                    )}
                                    <span style={{ fontSize: 12, color: isNullTerm ? '#B54708' : '#667085' }}>
                                      expr: {exprVal ?? 'NULL'}
                                      {isNullTerm && ' (NULL term)'}
                                    </span>
                                  </div>
                                );
                              })()}
                            </td>
                          )}
                          <td className="mono-cell">
                            <div>{row.baseline_included ? 'Included' : 'Excluded'}</div>
                            {row.baseline_contribution ?? 'Unavailable'}
                          </td>
                          <td className="mono-cell">
                            <div>{row.target_included ? 'Included' : 'Excluded'}</div>
                            {row.target_contribution ?? '—'}
                          </td>
                          <td className="mono-cell" style={{ fontWeight: 600 }}>
                            {row.delta ?? 'Unavailable'}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>

              {/* Pagination */}
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: 14 }}>
                <span style={{ fontSize: 12, color: '#667085' }}>
                  {evidenceTotal} validated evidence records pinned
                </span>
                <div style={{ display: 'flex', gap: 8 }}>
                  <button
                    className="button secondary small"
                    disabled={evidenceOffset === 0}
                    onClick={() => setEvidenceOffset(Math.max(0, evidenceOffset - 100))}
                  >
                    Previous
                  </button>
                  <button
                    className="button secondary small"
                    disabled={evidenceOffset + 100 >= evidenceTotal}
                    onClick={() => setEvidenceOffset(evidenceOffset + 100)}
                  >
                    Next
                  </button>
                </div>
              </div>
            </div>
          )}

          {/* TAB 4: PROVENANCE */}
          {activeTab === 'provenance' && (
            <div className="panel" style={{ padding: 20 }}>
              <div style={{ marginBottom: 16 }}>
                <h3 style={{ fontSize: 14, fontWeight: 700, margin: '0 0 6px', color: '#101828' }}>
                  Execution Provenance & Determinism
                </h3>
                <p style={{ fontSize: 13, color: '#667085', margin: 0 }}>
                  Immutable evidence hashes, execution parameters, and audit metadata.
                </p>
              </div>

              <div className="form-grid two" style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 20 }}>
                <div>
                  <h4 style={{ fontSize: 12, textTransform: 'uppercase', color: '#667085', margin: '0 0 8px' }}>
                    Snapshot Provenance
                  </h4>
                  <table style={{ width: '100%', fontSize: 12 }}>
                    <tbody>
                      <tr>
                        <td style={{ color: '#667085', padding: '4px 0' }}>Snapshot Name:</td>
                        <td style={{ fontWeight: 600 }}>{resultSnapshot?.name}</td>
                      </tr>
                      <tr>
                        <td style={{ color: '#667085', padding: '4px 0' }}>Row Count:</td>
                        <td className="mono-cell">{resultSnapshot?.row_count} rows</td>
                      </tr>
                      <tr>
                        <td style={{ color: '#667085', padding: '4px 0' }}>Source Hash:</td>
                        <td className="mono-cell" style={{ fontSize: 12 }}>{resultSnapshot?.source_hash || '—'}</td>
                      </tr>
                      <tr>
                        <td style={{ color: '#667085', padding: '4px 0' }}>Manifest Hash:</td>
                        <td className="mono-cell" style={{ fontSize: 12 }}>{resultSnapshot?.manifest_hash || '—'}</td>
                      </tr>
                    </tbody>
                  </table>
                </div>

                <div>
                  <h4 style={{ fontSize: 12, textTransform: 'uppercase', color: '#667085', margin: '0 0 8px' }}>
                    Reconciliation Details
                  </h4>
                  <table style={{ width: '100%', fontSize: 12 }}>
                    <tbody>
                      <tr>
                        <td style={{ color: '#667085', padding: '4px 0' }}>Analysis Run ID:</td>
                        <td className="mono-cell">{activeRun.id}</td>
                      </tr>
                      <tr>
                        <td style={{ color: '#667085', padding: '4px 0' }}>State:</td>
                        <td style={{ fontWeight: 600 }}>{activeRun.state.toUpperCase()}</td>
                      </tr>
                      <tr>
                        <td style={{ color: '#667085', padding: '4px 0' }}>Engine:</td>
                        <td>Deterministic Analytical Engine</td>
                      </tr>
                      <tr>
                        <td style={{ color: '#667085', padding: '4px 0' }}>Analysis Policy:</td>
                        <td className="mono-cell">
                          {(activeRun.context as { mode?: string })?.mode === 'sql_ast_v1'
                            ? 'Standard AST Reconciliation'
                            : (activeRun.context as { mode?: string })?.mode || 'Standard Reconciliation'}
                        </td>
                      </tr>
                    </tbody>
                  </table>
                </div>
              </div>

              <div style={{marginTop: 16, fontSize: 12}}>
                <div>Contract hash: <code>{resultContractHash || 'Unavailable'}</code></div>
                <div>Measure policy: <code>{String(runResult.provenance.measure_policy || resultContract?.measure_policy || 'Pinned legacy policy')}</code></div>
                <div>Baseline IR hash: <code>{runBaseline?.ir_hash || String(runResult.provenance.baseline_ir_hash || 'Unavailable')}</code></div>
                <div>Target IR hash: <code>{runTarget?.ir_hash || String(runResult.provenance.target_ir_hash || 'Unavailable')}</code></div>
              </div>
              {currentDraft?.ci_provenance && (
                <div style={{ marginTop: 20, paddingTop: 16, borderTop: '1px solid #E4E7EC' }}>
                  <h4 style={{ fontSize: 12, textTransform: 'uppercase', color: '#667085', margin: '0 0 8px' }}>
                    CI / PR Gate Provenance
                  </h4>
                  <table style={{ width: '100%', fontSize: 12 }}>
                    <tbody>
                      <tr>
                        <td style={{ color: '#667085', padding: '4px 0', width: 220 }}>CI Run ID:</td>
                        <td className="mono-cell">{currentDraft.ci_run_id || '—'}</td>
                      </tr>
                      <tr>
                        <td style={{ color: '#667085', padding: '4px 0' }}>Base Manifest Hash:</td>
                        <td className="mono-cell" style={{ fontSize: 12 }}>{(currentDraft.ci_provenance.base_manifest_hash as string) || '—'}</td>
                      </tr>
                      <tr>
                        <td style={{ color: '#667085', padding: '4px 0' }}>Target Manifest Hash:</td>
                        <td className="mono-cell" style={{ fontSize: 12 }}>{(currentDraft.ci_provenance.target_manifest_hash as string) || '—'}</td>
                      </tr>
                      <tr>
                        <td style={{ color: '#667085', padding: '4px 0' }}>Trusted Config Hash:</td>
                        <td className="mono-cell" style={{ fontSize: 12 }}>{(currentDraft.ci_provenance.trusted_config_hash as string) || '—'}</td>
                      </tr>
                      <tr>
                        <td style={{ color: '#667085', padding: '4px 0' }}>Policy Source:</td>
                        <td style={{ fontWeight: 600 }}>{(currentDraft.ci_provenance.policy_source as string) || 'Governed Store'}</td>
                      </tr>
                      <tr>
                        <td style={{ color: '#667085', padding: '4px 0' }}>CI Action:</td>
                        <td className="mono-cell">{(currentDraft.ci_provenance.ci_action as string) || 'fail'}</td>
                      </tr>
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          )}

          {/* Contextual Decision Rail (05.15, 05.20, 05.21) */}
          <div className="inv-decision-rail">
            <div className="inv-decision-rail-header">
              <div>
                <div className="inv-decision-rail-title">
                  {metricId ? 'Candidate Governance Decision' : 'Ad-hoc Comparison Decision'}
                </div>
                <div className="inv-decision-rail-subtitle">
                  {metricId
                    ? !definedComparison ? 'Comparison is undefined; no agreement or validation conclusion is available.' : isMaterial
                      ? 'Nonzero difference detected against the pinned baseline. Approval will update the active definition for all downstream consumers.'
                      : 'The evaluated definitions have a defined zero difference.'
                    : 'This comparison was executed ad-hoc without an assigned business Metric.'}
                </div>
              </div>

              {metricId && (
                <span className={`status-pill ${!definedComparison || isMaterial ? 'needs-decision' : 'completed'}`}>
                  {!definedComparison ? 'Undefined comparison' : isMaterial ? 'Candidate Drift Detected' : 'Defined zero difference'}
                </span>
              )}
            </div>

            <div className="inv-decision-actions-row">
              {metricId ? (
                <>
                  <button
                    className="button primary"
                    onClick={() => {
                      setApproveRationale('');
                      setShowApproveModal(true);
                    }}
                  >
                    <Check size={15} /> Approve candidate definition
                  </button>
                  <button
                    className="button secondary"
                    onClick={() => {
                      const submittedStudio = studioIdentityRef.current;
                      void appendDraftNote('Marked as intentional variant.', submittedStudio).then(() => {
                        if (studioIdentityRef.current === submittedStudio) setNotice('Marked as intentional variant.');
                      }).catch((err: unknown) => {
                        if (studioIdentityRef.current === submittedStudio) setOperationalError(classifyInvestigationError(err));
                      });
                    }}
                  >
                    Mark intentional variant
                  </button>
                  <button
                    className="button secondary"
                    onClick={() => setNotice('Investigation retained in active review.')}
                  >
                    Keep investigating
                  </button>
                </>
              ) : (
                <button
                  className="button primary"
                  onClick={() => {
                    setTrackMetricName(draftTitle === 'Untitled investigation' ? 'Revenue' : draftTitle);
                    setShowTrackMetricModal(true);
                  }}
                >
                  <Plus size={15} /> Track this as a Metric
                </button>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Investigation Notes & Rationale */}
      <div className="panel" style={{ marginTop: 24, padding: 20 }}>
        <h3 style={{ fontSize: 14, fontWeight: 700, margin: '0 0 8px', color: '#101828' }}>
          Investigation Notes & Governance Trail
        </h3>
        <textarea
          rows={3}
          value={draftNotes}
          onChange={(e) => handleNotesChange(e.target.value)}
          placeholder="Record notes, hypotheses, or external reconciliation context (automatically saved)…"
          style={{ width: '100%', padding: '8px 12px', borderRadius: 6, border: '1px solid #D0D5DD', fontSize: 13 }}
        />
      </div>

      {/* Modal: Approve Candidate Confirmation (05.20) */}
      {showApproveModal && (
        <div
          className="modal-backdrop"
          onClick={() => !approving && setShowApproveModal(false)}
          role="dialog"
          aria-modal="true"
          aria-labelledby="approve-candidate-title"
        >
          <div
            className="modal-card"
            style={{ maxWidth: '520px' }}
            onClick={(e) => e.stopPropagation()}
          >
            <div className="modal-header">
              <div>
                <h3 id="approve-candidate-title" style={{ margin: 0 }}>
                  Approve Candidate Implementation
                </h3>
                <p className="modal-subtitle">
                  Replace active approved implementation with verified candidate.
                </p>
              </div>
              <button
                type="button"
                className="modal-close-button"
                onClick={() => setShowApproveModal(false)}
                disabled={approving}
                aria-label="Close dialog"
              >
                <X size={16} />
              </button>
            </div>

            <div className="modal-body">
              <p style={{ fontSize: '13px', color: '#475467', lineHeight: 1.5, margin: '0 0 16px' }}>
                Promoting this candidate replaces the currently active approved implementation for{' '}
                <strong>{activeMetric?.display_name || 'this metric'}</strong>. Future CI/CD metric diffs and
                baseline comparisons will use this definition version as the immutable standard.
              </p>

              <div className="form-group">
                <label className="form-label">
                  Decision Rationale <span style={{ color: '#D92D20' }}>*</span>
                </label>
                <textarea
                  required
                  rows={3}
                  className="form-textarea"
                  value={approveRationale}
                  onChange={(e) => setApproveRationale(e.target.value)}
                  placeholder="Explain why this change is approved (e.g. reflects verified business logic update)…"
                  style={{ width: '100%' }}
                />
              </div>

              <div className="form-group" style={{ marginBottom: 0 }}>
                <label className="form-label">
                  Approver Identity
                </label>
                <input
                  type="text"
                  className="form-input"
                  value={approverActor}
                  onChange={(e) => setApproverActor(e.target.value)}
                  style={{ width: '100%' }}
                />
              </div>
            </div>

            <div className="modal-footer">
              <button
                type="button"
                className="button secondary"
                onClick={() => setShowApproveModal(false)}
                disabled={approving}
              >
                Cancel
              </button>
              <button
                type="button"
                className="button primary"
                onClick={handleApproveCandidate}
                disabled={approving || !approveRationale.trim()}
              >
                {approving ? 'Approving…' : 'Confirm Approval'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Modal: Track as Metric (05.4) */}
      {showTrackMetricModal && (
        <div
          className="modal-backdrop"
          onClick={() => !trackingMetric && setShowTrackMetricModal(false)}
          role="dialog"
          aria-modal="true"
          aria-labelledby="track-metric-title"
        >
          <div
            className="modal-card"
            style={{ maxWidth: '480px' }}
            onClick={(e) => e.stopPropagation()}
          >
            <div className="modal-header">
              <div>
                <h3 id="track-metric-title" style={{ margin: 0 }}>
                  Track Comparison as a Metric
                </h3>
                <p className="modal-subtitle">
                  Promote calculation into a formal business Metric with lifecycle governance.
                </p>
              </div>
              <button
                type="button"
                className="modal-close-button"
                onClick={() => setShowTrackMetricModal(false)}
                disabled={trackingMetric}
                aria-label="Close dialog"
              >
                <X size={16} />
              </button>
            </div>

            <div className="modal-body">
              <div className="form-group">
                <label className="form-label">
                  Metric Display Name <span style={{ color: '#D92D20' }}>*</span>
                </label>
                <input
                  type="text"
                  className="form-input"
                  value={trackMetricName}
                  onChange={(e) => setTrackMetricName(e.target.value)}
                  placeholder="e.g. Total Revenue"
                  style={{ width: '100%' }}
                  required
                />
              </div>

              <div className="form-group" style={{ marginBottom: 0 }}>
                <label className="form-label">
                  Business Importance
                </label>
                <select
                  className="form-select"
                  value={trackMetricImportance}
                  onChange={(e) => setTrackMetricImportance(e.target.value as 'critical' | 'important' | 'standard')}
                  style={{ width: '100%' }}
                >
                  <option value="critical">Critical (Blocks deployment on unapproved drift)</option>
                  <option value="important">Important (Warning on unapproved drift)</option>
                  <option value="standard">Standard (Informational drift)</option>
                </select>
              </div>
            </div>

            <div className="modal-footer">
              <button
                type="button"
                className="button secondary"
                onClick={() => setShowTrackMetricModal(false)}
                disabled={trackingMetric}
              >
                Cancel
              </button>
              <button
                type="button"
                className="button primary"
                onClick={handleTrackAsMetric}
                disabled={trackingMetric || !trackMetricName.trim()}
              >
                {trackingMetric ? 'Creating…' : 'Create & Track Metric'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
