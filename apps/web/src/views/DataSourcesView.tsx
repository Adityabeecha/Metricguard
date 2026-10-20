import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  AlertCircle,
  ArrowLeft,
  ArrowRight,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Clock,
  Database,
  FileCode2,
  FileSpreadsheet,
  Layers,
  Plus,
  RefreshCw,
  Search,
  ShieldCheck,
  UploadCloud,
  X,
} from 'lucide-react';
import { api, type Bootstrap, type FlatContract, type Snapshot, type SnapshotImport } from '../api';
import { inferMeaning, parseCsvText, type ParsedCsvPreview } from '../csvInference';
import { DIRECT_TWO_TABLE_LIMIT, PrepareTwoTable } from './PrepareTwoTable';
import { WarehouseSource } from './WarehouseSource';
import { humanizeError, type HumanizedError } from '../errorUtils';
import { ROLE_LABEL, summarizeDataset } from '../datasetSummary';
import { useDialog } from '../useDialog';
import { DEFAULT_COMPOUND_FILTERS, defaultFlatImport, uploadLimitText, previewRowLimitMessage, canConfirmUpload, loadLabelAfterFilePick, inheritedUnit, datasetUsageLabel, type UploadLimits } from '../onboardingPolicy';

interface DataSourcesViewProps {
  data: Bootstrap;
  refresh: () => Promise<Bootstrap | null>;
  /** Open the upload dialog on arrival (used by the guided comparison). */
  openAdd?: boolean;
  /** After a new dataset is created from the guided comparison, return there with it selected. */
  onCreatedReturn?: (snapshotId: string) => void;
}

type OnboardingStep = 'upload' | 'confirm' | 'validating' | 'success' | 'error';
type SourceType = 'file' | 'postgres';

export function DataSourcesView({ data, refresh, openAdd, onCreatedReturn }: DataSourcesViewProps) {
  const [selectedSnapshotId, setSelectedSnapshotId] = useState<string>(
    data.snapshots[0]?.id || ''
  );
  const [searchQuery, setSearchQuery] = useState('');
  const [typeFilter, setTypeFilter] = useState<'all' | 'csv' | 'parquet' | 'postgres'>('all');
  const [showAddModal, setShowAddModal] = useState(false);

  // Detail panel state
  const [detailTab, setDetailTab] = useState<'schema' | 'usage' | 'provenance'>('schema');

  // Add Data Source Flow State
  const [sourceType, setSourceType] = useState<SourceType>('file');
  const [step, setStep] = useState<OnboardingStep>('upload');
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [filePreview, setFilePreview] = useState<ParsedCsvPreview | null>(null);

  // Form Fields (Step 2)
  const [snapshotName, setSnapshotName] = useState('');
  const [confirmedKey, setConfirmedKey] = useState('');
  const [confirmedMeasure, setConfirmedMeasure] = useState('');
  const [confirmedCategories, setConfirmedCategories] = useState<string[]>([]);
  const [confirmedUnit, setConfirmedUnit] = useState('');
  const [unitSource, setUnitSource] = useState<string | undefined>(undefined);
  const [confirmedMeaning, setConfirmedMeaning] = useState('Row amount');
  const [precision, setPrecision] = useState(18);
  const [scale, setScale] = useState(2);
  const [nullPolicy, setNullPolicy] = useState<'blank_nullable' | 'native_null' | 'literal_NULL'>('blank_nullable');
  const [scopeConfirmed, setScopeConfirmed] = useState(false);
  const [comparabilityConfirmed, setComparabilityConfirmed] = useState(false);
  const [preparationFile, setPreparationFile] = useState<File | null>(null);
  const [categoryTypeOverrides, setCategoryTypeOverrides] = useState<Record<string, string>>({});
  const [compoundFilters, setCompoundFilters] = useState(DEFAULT_COMPOUND_FILTERS);
  const [enableSubtraction, setEnableSubtraction] = useState(false);
  const [enableCase, setEnableCase] = useState(false);
  // Several amount columns (contract v8): extra columns and their units, beside the main amount.
  const [enableMulti, setEnableMulti] = useState(false);
  const [extraAmounts, setExtraAmounts] = useState<string[]>([]);
  const [extraUnits, setExtraUnits] = useState<Record<string, string>>({});
  const [minuendColumn, setMinuendColumn] = useState('');
  const [subtrahendColumn, setSubtrahendColumn] = useState('');
  const [showAdvanced, setShowAdvanced] = useState(false);

  // Validation State (Step 3)
  const [validationProgress, setValidationProgress] = useState('');
  const [validationError, setValidationError] = useState<HumanizedError | null>(null);
  const [createdSnapshot, setCreatedSnapshot] = useState<Snapshot | null>(null);
  const [busy, setBusy] = useState(false);
  const [pageNotice, setPageNotice] = useState('');
  // Adding a newer load of an existing dataset: its definition is reused as is.
  const [loadContract, setLoadContract] = useState<FlatContract | null>(null);
  // A new upload whose settings match an existing dataset definition joins it as a new load.
  const [joinExisting, setJoinExisting] = useState(true);
  const [importStarted, setImportStarted] = useState<number | null>(null);
  const [importElapsed, setImportElapsed] = useState(0);
  // The dialog can close while an import keeps running; its result then arrives as a page notice.
  const modalOpen = useRef(false);
  useEffect(() => { modalOpen.current = showAddModal; }, [showAddModal]);
  useEffect(() => {
    if (importStarted === null) { setImportElapsed(0); return; }
    const timer = setInterval(() => setImportElapsed(Math.round((Date.now() - importStarted) / 1000)), 1000);
    return () => clearInterval(timer);
  }, [importStarted]);

  const snapshots = data.snapshots || [];
  const definitions = data.definitions || [];
  const runs = data.runs || [];
  const flatContracts = data.flat_contracts || [];
  const projectId = data.project?.id || 'local-retail';
  const [uploadLimits, setUploadLimits] = useState<UploadLimits | null>(null);
  const [limitsProblem, setLimitsProblem] = useState('');
  useEffect(() => {
    let active = true;
    setUploadLimits(null); setLimitsProblem('');
    api.get<UploadLimits>(`/usage?project_id=${encodeURIComponent(projectId)}`)
      .then(limits => { if (active) setUploadLimits(limits); })
      .catch(error => { if (active) setLimitsProblem(`Upload limits could not be loaded. Reload the page to try again. ${error instanceof Error ? error.message : String(error)}`); });
    return () => { active = false; };
  }, [projectId]);
  const previewProblem = uploadLimits && filePreview
    ? previewRowLimitMessage(uploadLimits, filePreview.totalRowsSampled, filePreview.row_count_exact !== false) : '';
  const uploadProblem = limitsProblem || previewProblem || (step === 'upload' ? validationError?.message || '' : '');
  const uploadReady = canConfirmUpload(Boolean(selectedFile && filePreview && uploadLimits), busy, uploadProblem);


  // Keep selected snapshot valid for current workspace snapshots
  useEffect(() => {
    if (snapshots.length > 0) {
      if (!selectedSnapshotId || !snapshots.some((s) => s.id === selectedSnapshotId)) {
        setSelectedSnapshotId(snapshots[0].id);
      }
    } else {
      setSelectedSnapshotId('');
    }
  }, [snapshots, selectedSnapshotId]);

  // Reset modal and temporary creation state when workspace changes
  useEffect(() => {
    setShowAddModal(false);
    setValidationError(null);
    setCreatedSnapshot(null);
    setPageNotice('');
    setStep('upload');
  }, [projectId]);

  // Focus, Tab containment, Escape and focus return for the add dialog.
  const addDialogRef = useDialog<HTMLDivElement>(showAddModal, () => setShowAddModal(false), !busy);

  const activeSnapshot = useMemo(() => {
    return snapshots.find((s) => s.id === selectedSnapshotId) || snapshots[0] || null;
  }, [snapshots, selectedSnapshotId]);

  // Associated contract for active snapshot
  const activeContract = useMemo(() => {
    if (!activeSnapshot) return null;
    return (
      flatContracts.find((c) => c.contract_hash === activeSnapshot.contract_hash) ||
      null
    );
  }, [activeSnapshot, flatContracts]);
  const keyIssue = confirmedKey ? filePreview?.key_issues?.[confirmedKey] : undefined;
  const multiExtras = enableMulti ? extraAmounts.filter(c => c !== confirmedKey && c !== confirmedMeasure) : [];
  const roleColumns = new Set([confirmedKey, enableSubtraction ? minuendColumn : confirmedMeasure, enableSubtraction ? subtrahendColumn : '', ...multiExtras].filter(Boolean));
  const filterColumns = confirmedCategories.filter(c => !roleColumns.has(c));
  const validateBlocker = !confirmedKey ? 'Choose the column that identifies each row.'
    : keyIssue ? `The row key “${confirmedKey}” ${keyIssue}; choose a column whose values are all different.`
    : roleColumns.size < (enableSubtraction ? 3 : 2 + multiExtras.length) ? 'The row key and the amount must be different columns.'
    : enableMulti && multiExtras.length === 0 ? 'Tick at least one more amount column, or choose a single amount.'
    : enableMulti && multiExtras.length > 7 ? 'Register at most 8 amount columns.'
    : enableMulti && multiExtras.some(c => !(extraUnits[c] ?? confirmedUnit).trim()) ? 'Enter a unit for every amount column.'
    : enableSubtraction && (!minuendColumn || !subtrahendColumn) ? 'Choose both amount columns for the subtraction.'
    : !enableSubtraction && !confirmedMeasure ? 'Choose the amount to total.'
    : !confirmedUnit.trim() ? 'Enter the unit the amount uses.'
    : !Number.isInteger(precision) || precision < 1 || precision > 38 ? 'Decimal precision must be a whole number from 1 to 38.'
    : !Number.isInteger(scale) || scale < 0 || scale > 18 || scale > precision ? 'Decimal places must be a whole number from 0 to 18, no greater than precision.'
    : enableCase && (!scopeConfirmed || !comparabilityConfirmed) ? 'Tick both confirmations for the conditional total.'
    : '';
  /** An existing dataset definition this upload fits exactly, apart from its name and description. */
  const matchingContract = useMemo<FlatContract | null>(() => {
    if (!filePreview || enableSubtraction || enableCase || !confirmedKey || !confirmedMeasure) return null;
    const amounts = [confirmedMeasure, ...multiExtras];
    const nullable = filterColumns.filter(cat => !filePreview.null_counts || filePreview.null_counts[cat] > 0);
    const types = (cat: string) => {
      const obs = filePreview.observed_types[cat];
      return categoryTypeOverrides[cat] || (obs === 'date' || obs === 'boolean' || obs === 'timestamp' ? obs : 'string');
    };
    const same = (a: string[], b: string[]) => a.length === b.length && [...a].sort().join('\u0000') === [...b].sort().join('\u0000');
    return flatContracts.find(c => {
      const version = enableMulti ? 8 : defaultFlatImport(compoundFilters).version;
      if (c.version !== version || c.row_key !== confirmedKey || c.null_policy !== nullPolicy) return false;
      if ((c.unit_label || '').trim() !== confirmedUnit.trim() || c.scale !== scale) return false;
      if (enableMulti) {
        const cols = c.decimal_columns || [];
        if (!same(cols.map(d => d.name), amounts) || cols[0]?.name !== confirmedMeasure) return false;
        if (cols.some(d => d.precision !== precision || (d.unit_label || '').trim() !== (d.name === confirmedMeasure ? confirmedUnit : extraUnits[d.name] ?? confirmedUnit).trim())) return false;
      } else if (c.measure !== confirmedMeasure || c.precision !== precision) return false;
      if (!same(c.categories || [], filterColumns)) return false;
      if (filterColumns.some(cat => (c.dimension_types?.[cat] || 'string') !== types(cat))) return false;
      // The new file may not have blanks where the existing definition forbids them.
      return nullable.every(cat => (c.nullable_categories || []).includes(cat));
    }) || null;
  }, [filePreview, enableSubtraction, enableCase, enableMulti, compoundFilters, confirmedKey, confirmedMeasure, multiExtras.join(','),
      filterColumns.join(','), nullPolicy, confirmedUnit, scale, precision, extraUnits, categoryTypeOverrides, flatContracts]);

  const activeSummary = useMemo(
    () => (activeSnapshot ? summarizeDataset(activeSnapshot, data) : null),
    [activeSnapshot, data]
  );

  // Definitions that run against this snapshot
  const snapshotDefinitions = useMemo(() => {
    if (!activeSnapshot) return [];
    const usedVersionIds = new Set(
      runs
        .filter((r) => r.snapshot_id === activeSnapshot.id)
        .flatMap((r) => [r.baseline_version_id, r.target_version_id])
    );
    return definitions.filter((d) =>
      d.versions.some((v) => usedVersionIds.has(v.id))
    );
  }, [activeSnapshot, runs, definitions]);

  // Runs recorded on this snapshot
  const snapshotRuns = useMemo(() => {
    if (!activeSnapshot) return [];
    return runs.filter((r) => r.snapshot_id === activeSnapshot.id);
  }, [activeSnapshot, runs]);

  // Filtered snapshots list
  const filteredSnapshots = useMemo(() => {
    return snapshots.filter((s) => {
      const matchQuery =
        !searchQuery ||
        s.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
        s.id.toLowerCase().includes(searchQuery.toLowerCase());
      const format = (s.execution_policy || '').includes('flat')
        ? s.name.endsWith('.parquet')
          ? 'parquet'
          : 'csv'
        : 'csv';
      const matchType = typeFilter === 'all' || format === typeFilter;
      return matchQuery && matchType;
    });
  }, [snapshots, searchQuery, typeFilter]);

  // Reset Onboarding Modal
  const openAddModal = () => {
    setSourceType('file');
    setStep('upload');
    setSelectedFile(null);
    setFilePreview(null);
    setConfirmedUnit('');
    setUnitSource(undefined);
    setValidationError(null);
    setCreatedSnapshot(null);
    setCategoryTypeOverrides({});
    setShowAdvanced(false);
    setCompoundFilters(DEFAULT_COMPOUND_FILTERS);
    setSnapshotName('');
    setEnableSubtraction(false);
    setEnableCase(false);
    setEnableMulti(false);
    setExtraAmounts([]);
    setExtraUnits({});
    setScopeConfirmed(false);
    setComparabilityConfirmed(false);
    setLoadContract(null);
    setJoinExisting(true);
    setShowAddModal(true);
  };
  const openAddLoad = (contract: FlatContract) => {
    openAddModal();
    setLoadContract(contract);
  };
  useEffect(() => {
    if (openAdd) openAddModal();
  }, [openAdd]);

  // Handle local File selection
  const handleFilePicked = async (file: File) => {
    setSelectedFile(file);
    setFilePreview(null);
    setExtraAmounts([]); setExtraUnits({}); setMinuendColumn(''); setSubtrahendColumn('');
    setCategoryTypeOverrides({});
    setValidationError(null);
    setSnapshotName(current => loadLabelAfterFilePick(current, file.name, Boolean(loadContract)));

    const isParquet = file.name.toLowerCase().endsWith('.parquet');
    const isExcel = file.name.toLowerCase().endsWith('.xlsx');
    setNullPolicy(isParquet ? 'native_null' : 'blank_nullable');
    if (!uploadLimits) return;
    if (uploadLimits.max_upload_mb != null && file.size > uploadLimits.max_upload_mb * 1024 * 1024) {
      setValidationError({ title: 'Free beta limit', message: `Free beta limit: ${uploadLimits.max_upload_mb.toLocaleString('en-US')} MB per upload (this file has ${file.size.toLocaleString('en-US')} bytes).` });
      return;
    }

    if (isParquet || isExcel) {
      // Parquet and Excel are read inside the isolated runner; wait for its preview.
      setBusy(true);
      setValidationProgress(isExcel ? 'Reading the first worksheet…' : 'Analyzing Parquet schema…');
      try {
        const form = new FormData();
        form.append('file', file);
        form.append('project_id', projectId);
        let record = await api.previewDataset(form, projectId);
        for (let waited = 0; !record.preview && ['queued', 'running'].includes(record.state) && waited < 120; waited++) {
          await new Promise(resolve => setTimeout(resolve, 1000));
          record = await api.datasetPreview(record.id);
        }
        if (!record.preview) {
          throw new Error(record.error?.message || 'The file could not be previewed. Check that the comparison engine is running.');
        }
        if (isExcel && record.preview.csv_head) {
          // The runner converted the sheet exactly; infer columns from its first rows as for a CSV.
          const parsed = { ...parseCsvText(record.preview.csv_head, 25), ...(record.preview.row_count != null ? { totalRowsSampled: record.preview.row_count, row_count_exact: true } : { row_count_exact: false }) };
          setFilePreview(parsed);
          const inf = inferMeaning(parsed.headers, parsed.observed_types, parsed.sample_rows, file.name, parsed.key_issues);
          setConfirmedKey(inf.rowKey);
          setConfirmedMeasure(inf.measure);
          setConfirmedCategories(inf.categories);
          setConfirmedUnit(inf.unit || '');
          setUnitSource(inf.unitSource);
          setConfirmedMeaning(inf.meaning);
          setScale(inf.scale);
          if (record.preview.sheet_name) setValidationProgress(`Using the first worksheet, “${record.preview.sheet_name}”.`);
        } else if (record.preview) {
          const inf = inferMeaning(
            record.preview.headers,
            Object.fromEntries(Object.entries(record.preview.declared_types).map(([k, v]) => [k, ({VARCHAR: 'string', BOOLEAN: 'boolean', DATE: 'date', TIMESTAMP: 'timestamp'} as Record<string, string>)[v || ''] || (v || '').toLowerCase()])),
            record.preview.sample_rows,
            file.name
          );
          setConfirmedKey(inf.rowKey);
          setConfirmedMeasure(inf.measure);
          setConfirmedCategories(inf.categories);
          setConfirmedUnit(inf.unit || '');
          setUnitSource(inf.unitSource);
          setConfirmedMeaning(inf.meaning);
          setScale(inf.scale);
          setFilePreview({
            headers: record.preview.headers,
            declared_types: record.preview.declared_types,
            observed_types: Object.fromEntries(Object.entries(record.preview.declared_types).map(([k, v]) => [k, ({VARCHAR: 'string', BOOLEAN: 'boolean', DATE: 'date', TIMESTAMP: 'timestamp'} as Record<string, string>)[v || ''] || 'string'])),
            sample_rows: record.preview.sample_rows,
            totalRowsSampled: record.preview.row_count ?? record.preview.sample_rows.length,
            row_count_exact: record.preview.row_count != null,
          });
        }
      } catch (err: unknown) {
        setValidationError(humanizeError(err, { filename: file.name }));
      } finally {
        setBusy(false);
      }
    } else {
      // Infer from the complete bounded source; display only a small sample.
      setBusy(true);
      try {
        if (file.size > 512 * 1024 * 1024) throw new Error('Source exceeds 512 MiB');
        // Large extracts: suggest types from the first 2 MiB of complete lines; the
        // isolated import still checks every row against the approved types.
        const large = file.size > 25 * 1024 * 1024;
        let text = large ? await file.slice(0, 2 * 1024 * 1024).text() : await file.text();
        if (large) text = text.slice(0, text.lastIndexOf('\n') + 1);
        const parsed = parseCsvText(text, 25);
        setFilePreview({ ...parsed, row_count_exact: !large });
        const inf = inferMeaning(parsed.headers, parsed.observed_types, parsed.sample_rows, file.name, parsed.key_issues);
        setConfirmedKey(inf.rowKey);
        setConfirmedMeasure(inf.measure);
        setConfirmedCategories(inf.categories);
        setConfirmedUnit(inf.unit || '');
        setUnitSource(inf.unitSource);
        setConfirmedMeaning(inf.meaning);
        setScale(inf.scale);
      } catch (err: unknown) {
        setValidationError(humanizeError(err, { filename: file.name }));
      } finally {
        setBusy(false);
      }
    }
  };

  // Toggle Category selection
  const toggleCategory = (cat: string) => {
    if (confirmedCategories.includes(cat)) {
      setConfirmedCategories(confirmedCategories.filter((c) => c !== cat));
    } else {
      if (confirmedCategories.length >= 8) return;
      setConfirmedCategories([...confirmedCategories, cat]);
    }
  };

  /** Upload the file under a dataset definition and wait for the import, however long it takes. */
  async function uploadAndWait(contractId: string, name: string): Promise<Snapshot> {
    const form = new FormData();
    form.append('file', selectedFile!);
    form.append('name', name);
    form.append('contract_id', contractId);
    form.append('project_id', projectId);
    if (preparationFile) form.append('preparation_manifest', preparationFile);
    const size = selectedFile!.size;
    setValidationProgress(size > 5 * 1024 * 1024 ? `Uploading ${formatBytes(size)}…` : 'Checking rows, column types, and data consistency…');
    setImportStarted(Date.now());
    const result = await api.uploadFlat(form, projectId);
    if ('id' in result && !('state' in result)) return result as Snapshot;  // an identical load already exists
    const queued = result as SnapshotImport;
    let current: SnapshotImport = queued;
    // No fixed time limit: a large file can take minutes, and the import keeps running on the server.
    for (let polls = 0; current.state === 'queued' || current.state === 'running'; polls++) {
      await new Promise(resolve => setTimeout(resolve, polls < 20 ? 1000 : 3000));
      try {
        current = await api.snapshotImport(queued.id);
      } catch {
        const bs = await refresh();
        const found = bs?.imports?.find(i => i.id === queued.id);
        if (found) current = found;
      }
      setValidationProgress(current.state === 'queued' ? 'Waiting for the import engine…' : 'Checking every row, column type and amount…');
    }
    if (current.state === 'failed') throw current.error || new Error('Snapshot validation failed.');
    const updated = await refresh();
    const found = updated?.snapshots?.find(s => s.id === current.snapshot_id);
    return found || ({ id: current.snapshot_id || queued.id, name, row_count: filePreview?.totalRowsSampled || 0,
      source_hash: current.source_hash, created_at: new Date().toISOString() } as unknown as Snapshot);
  }

  function finishImport(snapshot: Snapshot) {
    setCreatedSnapshot(snapshot);
    setSelectedSnapshotId(snapshot.id);
    setStep('success');
    if (!modalOpen.current) setPageNotice(`Dataset “${snapshot.name}” is ready: ${snapshot.row_count.toLocaleString('en-US')} rows validated.`);
  }

  function failImport(err: unknown) {
    const human = humanizeError(err, { key: confirmedKey, measure: confirmedMeasure, filename: selectedFile?.name });
    setValidationError(human);
    setStep('error');
    if (!modalOpen.current) setPageNotice(`The import of “${snapshotName || selectedFile?.name}” failed: ${human.message}`);
  }

  // A newer load of an existing dataset: same definition, so the two loads can be compared.
  const handleAddLoad = async () => {
    if (!selectedFile || !loadContract || !uploadReady) return;
    setStep('validating'); setBusy(true); setValidationError(null);
    try {
      finishImport(await uploadAndWait(loadContract.id, snapshotName.trim() || selectedFile.name));
    } catch (err: unknown) {
      failImport(err);
    } finally {
      setBusy(false); setImportStarted(null);
    }
  };

  // Execute Snapshot Ingestion (Step 3)
  const handleValidateAndCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!selectedFile || !filePreview || previewProblem || !uploadLimits) return;
    if (validateBlocker) {
      setValidationError({ title: 'Check dataset settings', message: validateBlocker });
      return;
    }

    if (enableCase && (!scopeConfirmed || !comparabilityConfirmed)) {
      setValidationError({ title: 'Confirmation required', message: 'Confirm the whole-snapshot scope and that all non-NULL measure values share the declared unit before importing CASE data.' });
      return;
    }

    if (enableSubtraction) {
      if (!minuendColumn || !subtrahendColumn) {
        setValidationError({
          title: 'Selection Required',
          message: 'Please choose both a minuend and a subtrahend column for subtraction.',
        });
        return;
      }
      if (minuendColumn === subtrahendColumn) {
        setValidationError({
          title: 'Distinct Fields Required',
          message: 'The minuend and subtrahend must be different columns.',
        });
        return;
      }
      if (confirmedKey === minuendColumn || confirmedKey === subtrahendColumn) {
        setValidationError({
          title: 'Distinct Fields Required',
          message: 'The row key cannot be used as a subtraction operand.',
        });
        return;
      }
    } else {
      if (!confirmedKey || !confirmedMeasure) {
        setValidationError({
          title: 'Selection Required',
          message: 'Please choose both a row identifier key and an analytical measure column.',
        });
        return;
      }

      if (confirmedKey === confirmedMeasure) {
        setValidationError({
          title: 'Distinct Fields Required',
          message: 'The row key and numeric measure must be different columns.',
        });
        return;
      }
    }

    if (!confirmedUnit.trim()) {
      setValidationError({
        title: 'Unit of measurement required',
        message: 'MetricGuard requires explicit confirmation of the business unit of measurement before creating a data snapshot.',
        suggestion: 'Specify the business unit (e.g. USD, EUR, INR, units, count) in the Unit field.',
      });
      setStep('confirm');
      return;
    }

    setStep('validating');
    setBusy(true);
    setValidationProgress('Preparing your data…');
    setValidationError(null);

    try {
      // 1. Register flat contract with explicit dimension types
      const dimensionTypes: Record<string, string> = {};
      for (const cat of filterColumns) {
        const obs = filePreview?.observed_types[cat];
        const inferred = obs === 'date' ? 'date' : obs === 'boolean' ? 'boolean' : obs === 'timestamp' ? 'timestamp' : 'string';
        dimensionTypes[cat] = categoryTypeOverrides[cat] || inferred;
      }

      const contractPayload = enableMulti ? {
        version: 8,
        measure_policy: 'flat-multi-measure-v1',
        predicate_policy: 'flat-boolean-v1',
        mode: 'flat_snapshot',
        name: snapshotName || selectedFile.name,
        row_key: confirmedKey,
        decimal_columns: [confirmedMeasure, ...multiExtras].map((name, i) => ({
          name, precision, scale, unit_label: i === 0 ? confirmedUnit.trim() : (extraUnits[name] ?? confirmedUnit).trim(),
        })),
        scale,
        categories: filterColumns,
        dimension_types: dimensionTypes,
        nullable_categories: filterColumns.filter(cat => !filePreview?.null_counts || filePreview.null_counts[cat] > 0),
        measure_meaning: confirmedMeaning || `Amounts ${[confirmedMeasure, ...multiExtras].join(', ')}`,
        unit_label: confirmedUnit.trim(),
        values_comparable: true,
        scope: 'entire_supplied_snapshot',
        null_policy: nullPolicy,
      } : enableSubtraction ? {
        version: 6,
        measure_policy: 'flat-subtraction-v1',
        predicate_policy: 'flat-boolean-v1',
        mode: 'flat_snapshot',
        name: snapshotName || selectedFile.name,
        row_key: confirmedKey,
        decimal_columns: [
          {
            name: minuendColumn,
            precision,
            scale,
            unit_label: confirmedUnit.trim(),
          },
          {
            name: subtrahendColumn,
            precision,
            scale,
            unit_label: confirmedUnit.trim(),
          },
        ],
        measure_expression: {
          op: 'sub',
          minuend: minuendColumn,
          subtrahend: subtrahendColumn,
        },
        scale,
        categories: filterColumns,
        dimension_types: dimensionTypes,
        nullable_categories: filterColumns.filter(cat => !filePreview?.null_counts || filePreview.null_counts[cat] > 0),
        measure_meaning: confirmedMeaning || `${minuendColumn} minus ${subtrahendColumn}`,
        unit_label: confirmedUnit.trim(),
        values_comparable: true,
        scope: 'entire_supplied_snapshot',
        null_policy: nullPolicy,
      } : {
        version: enableCase ? 7 : defaultFlatImport(compoundFilters).version,
        ...(enableCase ? { measure_policy: 'flat-case-v1' } : {}),
        ...(compoundFilters || enableCase ? { predicate_policy: 'flat-boolean-v1' } : {}),
        mode: 'flat_snapshot',
        name: snapshotName || selectedFile.name,
        row_key: confirmedKey,
        measure: confirmedMeasure,
        precision,
        scale,
        categories: filterColumns,
        dimension_types: dimensionTypes,
        nullable_categories: filterColumns.filter(cat => !filePreview?.null_counts || filePreview.null_counts[cat] > 0),
        measure_meaning: confirmedMeaning || `Row ${confirmedMeasure}`,
        unit_label: confirmedUnit.trim(),
        values_comparable: true,
        scope: 'entire_supplied_snapshot',
        null_policy: nullPolicy,
      };

      const contract = matchingContract && joinExisting ? matchingContract : await api.createFlatContract(contractPayload, projectId);
      finishImport(await uploadAndWait(contract.id, snapshotName || selectedFile.name));
    } catch (err: unknown) {
      failImport(err);
    } finally {
      setBusy(false);
      setImportStarted(null);
    }
  };

  const handleDone = async () => {
    setShowAddModal(false);
    const updated = await refresh();
    if (createdSnapshot && onCreatedReturn) {
      // Opened from New comparison: go back there with the new dataset selected.
      onCreatedReturn(createdSnapshot.id);
      return;
    }
    if (createdSnapshot) {
      setSelectedSnapshotId(createdSnapshot.id);
      setPageNotice(`Data source "${createdSnapshot.name}" successfully created and ready.`);
    } else if (updated?.snapshots?.length) {
      setSelectedSnapshotId(updated.snapshots[updated.snapshots.length - 1].id);
    }
  };
  const getSourceTypeBadge = (s: Snapshot) => {
    const isParquet = s.name.endsWith('.parquet') || (s.manifest as { format?: string })?.format === 'parquet';
    if (s.execution_policy === 'relational_snapshot_v1') return <span className="source-tag csv">{summarizeDataset(s, data).tables.length || 2} tables</span>;
    if (isParquet) return <span className="source-tag parquet">Parquet</span>;
    if (s.name.toLowerCase().includes('postgres')) return <span className="source-tag postgres">PostgreSQL</span>;
    return <span className="source-tag csv">CSV</span>;
  };

  const getSourceTypeLabel = (s: Snapshot) => {
    const isParquet = s.name.endsWith('.parquet') || (s.manifest as { format?: string })?.format === 'parquet';
    if (s.execution_policy === 'relational_snapshot_v1') return summarizeDataset(s, data).kindLabel;
    if (isParquet) return 'Parquet extract';
    if (s.name.toLowerCase().includes('postgres')) return 'PostgreSQL snapshot';
    return 'CSV extract';
  };

  return (
    <div className="datasources-container">
      {/* Page Heading */}
      <div className="page-heading">
        <div>
          <h1>Datasets</h1>
          <p>Frozen, validated data that comparisons read.</p>
        </div>
        <div className="heading-actions">
          <button className="button secondary" onClick={() => void refresh()} title="Refresh datasets">
            <RefreshCw size={15} /> Refresh
          </button>
          <button className="button primary" onClick={openAddModal}>
            <Plus size={16} /> Add dataset
          </button>
        </div>
      </div>

      {pageNotice && (
        <div className="alert success" role="status">
          <span>{pageNotice}</span>
          <button onClick={() => setPageNotice('')}>
            <X size={15} />
          </button>
        </div>
      )}

      {/* Main Layout: If no snapshots, show empty state */}
      {snapshots.length === 0 ? (
        <section className="dashboard-empty-card" style={{ maxWidth: '680px', margin: '40px auto' }}>
          <div className="empty-badge-icon">
            <Database size={26} />
          </div>
          <h2>No datasets yet</h2>
          <p>
            MetricGuard compares SQL metric logic against immutable evidence snapshots.
            Add a CSV or Parquet extract to validate types, establish your comparison baseline,
            and begin reconciliation.
          </p>
          <button className="button primary" onClick={openAddModal}>
            <Plus size={16} /> Add dataset <ArrowRight size={16} />
          </button>
        </section>
      ) : (
        <div className="datasources-grid-layout">
          {/* Left Column: Source inventory */}
          <div className="sources-list-column">
            {/* Search and filter bar */}
            <div className="sources-toolbar">
              <div className="search-box">
                <Search size={15} />
                <input
                  type="text"
                  placeholder="Filter sources by name or hash…"
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                />
              </div>
              <div className="type-filter-pills">
                <button
                  type="button"
                  className={`filter-pill ${typeFilter === 'all' ? 'active' : ''}`}
                  onClick={() => setTypeFilter('all')}
                >
                  All ({snapshots.length})
                </button>
                <button
                  type="button"
                  className={`filter-pill ${typeFilter === 'csv' ? 'active' : ''}`}
                  onClick={() => setTypeFilter('csv')}
                >
                  CSV
                </button>
                <button
                  type="button"
                  className={`filter-pill ${typeFilter === 'parquet' ? 'active' : ''}`}
                  onClick={() => setTypeFilter('parquet')}
                >
                  Parquet
                </button>
              </div>
            </div>

            {/* Source card list */}
            <div className="sources-scroll-list">
              {filteredSnapshots.map((snap) => {
                const isSelected = snap.id === activeSnapshot?.id;
                const formattedDate = snap.created_at
                  ? new Date(snap.created_at).toLocaleDateString(undefined, {
                      day: 'numeric',
                      month: 'short',
                      year: 'numeric',
                    })
                  : 'Frozen';
                const runCount = runs.filter((r) => r.snapshot_id === snap.id).length;

                return (
                  <div
                    key={snap.id}
                    className={`source-card-item ${isSelected ? 'selected' : ''}`}
                    onClick={() => setSelectedSnapshotId(snap.id)}
                    role="button"
                    tabIndex={0}
                    onKeyDown={(e) => e.key === 'Enter' && setSelectedSnapshotId(snap.id)}
                  >
                    <div className="source-card-top">
                      <div className="source-title-row">
                        <span className="source-icon-wrap">
                          <FileSpreadsheet size={16} />
                        </span>
                        <strong className="source-title">{snap.name}</strong>
                      </div>
                      {getSourceTypeBadge(snap)}
                    </div>

                    <div className="source-card-meta">
                      <span className="meta-stat">
                        <strong>{snap.row_count?.toLocaleString('en-US') || '0'}</strong> rows
                      </span>
                      <span className="meta-sep">·</span>
                      <span className="meta-stat">
                        <Clock size={12} /> {formattedDate}
                      </span>
                      {runCount > 0 && (
                        <>
                          <span className="meta-sep">·</span>
                          <span className="meta-stat">{runCount} run{runCount !== 1 ? 's' : ''}</span>
                        </>
                      )}
                    </div>

                    <div className="source-card-footer">
                      <span className="status-badge-blue">
                        <span className="pulse-dot" /> Snapshot ready
                      </span>
                      <span className="chevron-link">
                        Inspect <ChevronRight size={14} />
                      </span>
                    </div>
                  </div>
                );
              })}

              {filteredSnapshots.length === 0 && (
                <div className="empty-filter-state">
                  <p>No datasets match this filter.</p>
                </div>
              )}
            </div>
          </div>

          {/* Right Column: Active Snapshot Detail Panel */}
          {activeSnapshot && (
            <div className="source-detail-column panel">
              {/* Header */}
              <div className="detail-panel-header">
                <div>
                  <div className="detail-type-row">
                    {getSourceTypeBadge(activeSnapshot)}
                    <span className="source-type-label">{getSourceTypeLabel(activeSnapshot)}</span>
                  </div>
                  <h2>{activeSnapshot.name}</h2>
                  <p className="detail-subtitle">
                    Immutable snapshot · {activeSnapshot.row_count?.toLocaleString('en-US')} verified rows
                  </p>
                </div>
                <div className="detail-header-actions">
                  <div className="detail-status-pill">
                    <ShieldCheck size={16} />
                    <span>Snapshot verified</span>
                  </div>
                  {activeContract && (
                    <button type="button" className="button secondary small" onClick={() => openAddLoad(activeContract)}
                      title="Upload a newer file of this dataset with the same columns, to compare the two loads">
                      <Plus size={14} /> Add a newer load
                    </button>
                  )}
                </div>
              </div>

              {/* Stat Cards Strip */}
              <div className="summary-stat-grid">
                <div className="stat-box">
                  <span className="stat-label">TOTAL ROWS</span>
                  <strong className="stat-num">{activeSnapshot.row_count?.toLocaleString('en-US')}</strong>
                </div>
                <div className="stat-box">
                  <span className="stat-label">{activeSummary?.kind === 'relational' ? 'KEYS (FACT · DIMENSION)' : 'ROW KEY'}</span>
                  <strong className="stat-num mono">{activeSummary?.keyLabel}</strong>
                </div>
                <div className="stat-box">
                  <span className="stat-label">AMOUNT TOTALLED</span>
                  <strong className="stat-num mono">{activeSummary?.measureLabel}</strong>
                </div>
                <div className="stat-box">
                  <span className="stat-label">DECLARED UNIT</span>
                  <strong className="stat-num">{activeSummary?.unitLabel}</strong>
                </div>
              </div>
              {activeSummary && (
                <div className="panel-note">
                  <strong>{activeSummary.kindLabel}</strong>
                  {activeSummary.contractName && <> · contract “{activeSummary.contractName}”</>}
                  {activeSummary.measureMeaning && <> · amount means “{activeSummary.measureMeaning}”</>}
                  {activeSummary.joinLabel && <> · join {activeSummary.joinLabel}</>}
                  {activeSummary.filterColumns.length > 0 && <> · filter columns: {activeSummary.filterColumns.join(', ')}</>}
                  {activeSummary.missing.length > 0 && (
                    <span className="disabled-reason">Missing information: {activeSummary.missing.join(', ')}.</span>
                  )}
                </div>
              )}

              {activeContract?.version === 7 && <p className="panel-note">Conditional SUM: one CASE WHEN branch over <code>{activeContract.measure}</code>, ELSE zero. FALSE and UNKNOWN select zero; TRUE may select a NULL measure. WHERE and WHEN definitions are saved separately from this physical measure contract.</p>}

              {/* Detail Tabs */}
              <div className="section-nav-tabs compact">
                <button
                  type="button"
                  className={`section-tab-button ${detailTab === 'schema' ? 'active' : ''}`}
                  onClick={() => setDetailTab('schema')}
                >
                  Schema & Structure
                </button>
                <button
                  type="button"
                  className={`section-tab-button ${detailTab === 'usage' ? 'active' : ''}`}
                  onClick={() => setDetailTab('usage')}
                >
                  {datasetUsageLabel(snapshotDefinitions.length, snapshotRuns.length)}
                </button>
                <button
                  type="button"
                  className={`section-tab-button ${detailTab === 'provenance' ? 'active' : ''}`}
                  onClick={() => setDetailTab('provenance')}
                >
                  Technical Provenance
                </button>
              </div>

              {/* Tab 1: Schema */}
              {detailTab === 'schema' && (
                <div className="detail-tab-body">
                  <div className="table-scroll">
                    <table className="schema-table">
                      <thead>
                        <tr>
                          <th>COLUMN NAME</th>
                          <th>TYPE</th>
                          <th>ANALYTICAL ROLE</th>
                        </tr>
                      </thead>
                      <tbody>
                        {activeSnapshot.schema &&
                          Object.entries(activeSnapshot.schema).map(([col, typ]) => {
                            const role = activeSummary?.roleOf(col) || 'attribute';
                            const tone = role === 'key' || role === 'dimension_key' || role === 'foreign_key' ? 'key'
                              : role === 'category' ? 'category' : role === 'attribute' ? 'attribute' : 'measure';
                            return (
                              <tr key={col}>
                                <td className="mono-cell font-semibold">{col}</td>
                                <td>
                                  <span className="type-badge">{typ}</span>
                                </td>
                                <td>
                                  <span className={`role-tag ${tone}`}>{ROLE_LABEL[role]}</span>
                                </td>
                              </tr>
                            );
                          })}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}

              {/* Tab 2: Usage */}
              {detailTab === 'usage' && (
                <div className="detail-tab-body">
                  <div className="usage-section">
                    <h3>Saved calculations using this snapshot</h3>
                    {snapshotDefinitions.length === 0 ? (
                      <p className="helper-muted">No saved calculations have executed against this snapshot yet.</p>
                    ) : (
                      <div className="definition-list">
                        {snapshotDefinitions.map((def) => (
                          <div className="definition-row compact" key={def.id}>
                            <FileCode2 size={16} />
                            <div>
                              <strong>{def.name}</strong>
                              <small>{def.versions.length} version{def.versions.length !== 1 ? 's' : ''}</small>
                            </div>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>

                  <div className="usage-section" style={{ marginTop: '20px' }}>
                    <h3>Recorded reconciliation runs</h3>
                    {snapshotRuns.length === 0 ? (
                      <p className="helper-muted">No runs executed against this snapshot yet.</p>
                    ) : (
                      <div className="definition-list">
                        {snapshotRuns.slice(0, 5).map((r) => (
                          <div className="definition-row compact" key={r.id}>
                            <Layers size={16} />
                            <div>
                              <strong>Run {r.id.slice(0, 8)}…</strong>
                              <small>
                                Status: {r.state} · {r.result ? `Delta: ${r.result.delta_target_minus_baseline ?? 'Unavailable (undefined SUM)'}` : 'In progress'}
                              </small>
                            </div>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                </div>
              )}

              {/* Tab 3: Provenance */}
              {detailTab === 'provenance' && (
                <div className="detail-tab-body">
                  <div className="provenance-grid">
                    <div className="prov-item">
                      <span className="prov-label">IMMUTABLE SNAPSHOT ID</span>
                      <code className="prov-val">{activeSnapshot.id}</code>
                    </div>
                    <div className="prov-item">
                      <span className="prov-label">SOURCE SHA-256 HASH</span>
                      <code className="prov-val">{activeSnapshot.source_hash}</code>
                    </div>
                    <div className="prov-item">
                      <span className="prov-label">MANIFEST HASH</span>
                      <code className="prov-val">{activeSnapshot.manifest_hash}</code>
                    </div>
                    {activeSnapshot.contract_hash && (
                      <div className="prov-item">
                        <span className="prov-label">CONTRACT HASH</span>
                        <code className="prov-val">{activeSnapshot.contract_hash}</code>
                      </div>
                    )}
                    <div className="prov-item">
                      <span className="prov-label">WHOLE-SNAPSHOT SCOPE</span>
                      <span className="prov-text">{activeSnapshot.scope || 'synthetic_retail_all_orders'}</span>
                    </div>
                  </div>

                  {activeSnapshot.execution_policy === 'relational_snapshot_v1' && (() => {
                    const relationalContract = (data.relational_contracts || []).find((c) => c.contract_hash === activeSnapshot.contract_hash);
                    if (!relationalContract) return null;
                    return (
                      <details className="prov-details" style={{ marginTop: '16px' }} open={activeSnapshot.row_count > DIRECT_TWO_TABLE_LIMIT}>
                        <summary>Prepare for large comparisons{activeSnapshot.row_count > DIRECT_TWO_TABLE_LIMIT ? ' (needed: over 100,000 facts)' : ''}</summary>
                        <PrepareTwoTable key={activeSnapshot.id} snapshot={activeSnapshot} contract={relationalContract}
                          onPrepared={async (id) => { await refresh(); setSelectedSnapshotId(id); }} />
                      </details>
                    );
                  })()}

                  {activeSnapshot.preparation && (
                    <details className="prov-details" style={{ marginTop: '16px' }}>
                      <summary>External preparation manifest</summary>
                      <pre className="json-view">
                        {JSON.stringify(activeSnapshot.preparation, null, 2)}
                      </pre>
                    </details>
                  )}
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {/* ========================================================================= */}
      {/* UNIFIED ADD DATA SOURCE MODAL (3-STEP GUIDED FLOW)                        */}
      {/* ========================================================================= */}
      {showAddModal && (
        <div className="modal-backdrop" onClick={() => !busy && setShowAddModal(false)}>
          <div
            ref={addDialogRef}
            data-dialog-managed=""
            className="modal-card modal-large"
            role="dialog"
            aria-modal="true"
            aria-labelledby="add-source-title"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="modal-header">
              <div>
                <h2 id="add-source-title">Add Data Source</h2>
                <p className="modal-subtitle">
                  Upload a verified extract to establish an immutable analytical snapshot.
                </p>
              </div>
              <button
                type="button"
                className="modal-close-button"
                onClick={() => setShowAddModal(false)}
                aria-label="Close dialog"
              >
                <X size={18} />
              </button>
            </div>

            {/* Stepper Header (for file uploads) */}
            {sourceType === 'file' && (
              <div className="modal-stepper-header">
                <div className={`step-node ${step === 'upload' ? 'active' : 'completed'}`}>
                  <span className="step-circle">1</span>
                  <span>Upload & preview</span>
                </div>
                <div className="step-line" />
                <div className={`step-node ${step === 'confirm' ? 'active' : ['validating', 'success'].includes(step) ? 'completed' : ''}`}>
                  <span className="step-circle">2</span>
                  <span>Confirm meaning</span>
                </div>
                <div className="step-line" />
                <div className={`step-node ${['validating', 'success', 'error'].includes(step) ? 'active' : ''}`}>
                  <span className="step-circle">3</span>
                  <span>Validate snapshot</span>
                </div>
              </div>
            )}

            {/* Error banner if present */}
            {validationError && step !== 'error' && (
              <div className="alert error" style={{ margin: '14px 20px 0' }} role="alert">
                <strong>{validationError.title}:</strong> {validationError.message}
                {validationError.suggestion && <p style={{ margin: '4px 0 0', fontSize: '12px' }}>{validationError.suggestion}</p>}
              </div>
            )}

            <div className="modal-body">
              {/* STEP 1: UPLOAD */}
              {step === 'upload' && (
                <div className="step-upload-body">
                  {loadContract && (
                    <div className="panel-note">
                      <strong>Newer load of “{loadContract.name}”</strong>
                      <p>The file must have the same columns: row key <code>{loadContract.row_key}</code>, amount{(loadContract.decimal_columns?.length || 0) > 1 ? 's' : ''} <code>{loadContract.decimal_columns?.map(d => d.name).join(', ') || loadContract.measure}</code>{loadContract.categories.length > 0 && <> and filter columns <code>{loadContract.categories.join(', ')}</code></>}. Every row is checked against this definition, and the two loads can then be compared record by record.</p>
                      <label>Label for this load
                        <input value={snapshotName} maxLength={160} onChange={e => setSnapshotName(e.target.value)} placeholder="e.g. Final delivery, 1 Feb" />
                      </label>
                    </div>
                  )}
                  {/* Source Type Selector */}
                  <div className="source-type-tabs" hidden={Boolean(loadContract)}>
                    <button
                      type="button"
                      className={`source-type-btn ${sourceType === 'file' ? 'active' : ''}`}
                      onClick={() => setSourceType('file')}
                    >
                      <FileSpreadsheet size={16} /> File (CSV, Parquet or Excel)
                    </button>
                    <button
                      type="button"
                      className={`source-type-btn ${sourceType === 'postgres' ? 'active' : ''}`}
                      onClick={() => setSourceType('postgres')}
                    >
                      <Database size={16} /> Warehouse table (PostgreSQL, Snowflake, BigQuery)
                    </button>
                  </div>

                  {sourceType === 'file' ? (
                    <>
                      {/* Dropzone */}
                      <label className="file-dropzone-large">
                        <UploadCloud size={32} />
                        <strong>
                          {selectedFile ? selectedFile.name : 'Choose a CSV, Parquet or Excel file'}
                        </strong>
                        <span>
                          {selectedFile
                            ? `${formatBytes(selectedFile.size)} selected`
                            : uploadLimits ? uploadLimitText(uploadLimits) : 'Checking upload limits...'}
                        </span>
                        <input
                          type="file"
                          disabled={!uploadLimits || busy}
                          accept=".csv,.parquet,.xlsx,text/csv,application/vnd.apache.parquet,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                          onChange={(e) => {
                            const f = e.target.files?.[0];
                            if (f) void handleFilePicked(f);
                          }}
                        />
                      </label>

                      {selectedFile && uploadLimits && <p className="helper-muted">{uploadLimitText(uploadLimits)}</p>}
                      {limitsProblem && <p role="alert" className="disabled-reason">{limitsProblem}</p>}
                      {previewProblem && <p role="alert" className="disabled-reason">{previewProblem}</p>}
                      {/* File preview */}
                      {filePreview && (
                        <div className="file-preview-card">
                          <div className="preview-heading-row">
                            <strong>
                              {filePreview.headers.length} columns detected · {filePreview.totalRowsSampled} {filePreview.row_count_exact === false ? 'sample rows; all rows are checked during import' : 'rows counted'}
                            </strong>
                            <span className="badge neutral">Previewing top rows</span>
                          </div>

                          <div className="table-scroll" style={{ maxHeight: '180px' }}>
                            <table className="preview-table">
                              <thead>
                                <tr>
                                  {filePreview.headers.map((h) => (
                                    <th key={h}>
                                      <div>{h}</div>
                                      <small className="col-type-tag">
                                        {filePreview.observed_types[h] || 'unknown'}
                                      </small>
                                    </th>
                                  ))}
                                </tr>
                              </thead>
                              <tbody>
                                {filePreview.sample_rows.slice(0, 5).map((row, i) => (
                                  <tr key={i}>
                                    {filePreview.headers.map((h) => (
                                      <td key={h} className="mono-cell">
                                        {row[h] ?? <span className="null-dim">NULL</span>}
                                      </td>
                                    ))}
                                  </tr>
                                ))}
                              </tbody>
                            </table>
                          </div>
                        </div>
                      )}
                    </>
                  ) : (
                    /* Warehouse table: PostgreSQL, Snowflake or BigQuery (asks its own dataset questions). */
                    <WarehouseSource workspaceId={projectId} refresh={refresh}
                      onCreated={(id) => { setShowAddModal(false); setSelectedSnapshotId(id); }} />
                  )}
                </div>
              )}

              {/* STEP 2: CONFIRM MEANING */}
              {step === 'confirm' && (
                <form className="step-confirm-body" onSubmit={handleValidateAndCreate}>
                  <p className="confirm-intro-text">
                    MetricGuard suggests column types from the rows it read (the first 2 MiB of a large CSV) or from the Parquet schema. Every imported row is validated.
                    Please confirm the primary row identifier, numerical measure, and categorical dimensions.
                  </p>

                  {/* Compact Inferred Logical Schema */}
                  {filePreview && (
                    <div
                      className="inferred-schema-card"
                      style={{
                        background: 'var(--panel)',
                        border: '1px solid var(--line)',
                        borderRadius: 8,
                        padding: '14px 16px',
                        marginBottom: 20,
                      }}
                    >
                      <div
                        style={{
                          display: 'flex',
                          justifyContent: 'space-between',
                          alignItems: 'center',
                          marginBottom: 10,
                        }}
                      >
                        <div>
                          <div
                            style={{
                              fontSize: 13,
                              fontWeight: 700,
                              color: 'var(--ink)',
                              display: 'flex',
                              alignItems: 'center',
                              gap: 8,
                            }}
                          >
                            <Database size={15} style={{ color: 'var(--link)' }} />
                            <span>Inferred Logical Schema</span>
                            <span className="badge neutral" style={{ fontSize: 12, fontWeight: 500 }}>
                              {filePreview.headers.length} columns detected · {filePreview.totalRowsSampled} {filePreview.row_count_exact === false ? 'sample rows; all rows are checked during import' : 'rows counted'}
                            </span>
                          </div>
                          <p style={{ margin: '3px 0 0', fontSize: 12, color: 'var(--muted)' }}>
                            Dates require YYYY-MM-DD; booleans require true/false. Timestamps require YYYY-MM-DDTHH:mm:ss with up to 6 fractional digits, without a timezone. Overrides are validated at import.
                          </p>
                        </div>
                      </div>

                      <div className="table-scroll" style={{ maxHeight: 220, overflowY: 'auto' }}>
                        <table style={{ width: '100%', fontSize: 12, borderCollapse: 'collapse' }}>
                          <thead>
                            <tr style={{ borderBottom: '1px solid var(--line-strong)', textAlign: 'left', color: 'var(--text-2)' }}>
                              <th style={{ padding: '6px 8px', fontWeight: 600 }}>Column</th>
                              <th style={{ padding: '6px 8px', fontWeight: 600 }}>Logical Type</th>
                              <th style={{ padding: '6px 8px', fontWeight: 600 }}>Contract Role</th>
                              <th style={{ padding: '6px 8px', fontWeight: 600 }}>Nullability</th>
                              <th style={{ padding: '6px 8px', fontWeight: 600 }}>Sample Value</th>
                            </tr>
                          </thead>
                          <tbody>
                            {filePreview.headers.map((col) => {
                              const obsType = filePreview.observed_types[col] || 'string';
                              const isKey = col === confirmedKey;
                              const isMinuend = enableSubtraction && col === minuendColumn;
                              const isSubtrahend = enableSubtraction && col === subtrahendColumn;
                              const isMeasure = !enableSubtraction && (col === confirmedMeasure || multiExtras.includes(col));
                              const declType = col === confirmedKey ? 'VARCHAR' : (isMeasure || isMinuend || isSubtrahend) ? `DECIMAL(${precision},${scale})` : filePreview.declared_types[col] || 'VARCHAR';
                              const isCategory = confirmedCategories.includes(col);
                              const nullCount = filePreview.null_counts?.[col];
                              const sampleVal = filePreview.sample_rows.find((r) => r[col] !== null)?.[col];

                              let roleLabel = 'Unregistered (Excluded)';
                              let roleStyle: React.CSSProperties = { color: 'var(--faint)', fontSize: 12 };
                              if (isKey) {
                                roleLabel = 'Row Key (Unique)';
                                roleStyle = {
                                  background: 'var(--tint)',
                                  color: 'var(--link)',
                                  border: '1px solid var(--line-strong)',
                                  padding: '2px 7px',
                                  borderRadius: 4,
                                  fontWeight: 600,
                                  fontSize: 12,
                                  display: 'inline-block',
                                };
                              } else if (isMinuend) {
                                roleLabel = `Minuend Decimal (${confirmedUnit || 'units'})`;
                                roleStyle = {
                                  background: 'var(--tint)',
                                  color: 'var(--link)',
                                  border: '1px solid var(--line-strong)',
                                  padding: '2px 7px',
                                  borderRadius: 4,
                                  fontWeight: 600,
                                  fontSize: 12,
                                  display: 'inline-block',
                                };
                              } else if (isSubtrahend) {
                                roleLabel = `Subtrahend Decimal (${confirmedUnit || 'units'})`;
                                roleStyle = {
                                  background: 'var(--tint)',
                                  color: 'var(--link)',
                                  border: '1px solid var(--line-strong)',
                                  padding: '2px 7px',
                                  borderRadius: 4,
                                  fontWeight: 600,
                                  fontSize: 12,
                                  display: 'inline-block',
                                };
                              } else if (isMeasure) {
                                roleLabel = `Sum Measure (${confirmedUnit || 'units'})`;
                                roleStyle = {
                                  background: 'var(--tint)',
                                  color: 'var(--link)',
                                  border: '1px solid var(--line-strong)',
                                  padding: '2px 7px',
                                  borderRadius: 4,
                                  fontWeight: 600,
                                  fontSize: 12,
                                  display: 'inline-block',
                                };
                              } else if (isCategory) {
                                roleLabel = 'Categorical Dimension';
                                roleStyle = {
                                  background: 'var(--panel-2)',
                                  color: 'var(--text-2)',
                                  border: '1px solid var(--line-strong)',
                                  padding: '2px 7px',
                                  borderRadius: 4,
                                  fontWeight: 500,
                                  fontSize: 12,
                                  display: 'inline-block',
                                };
                              }

                              return (
                                <tr key={col} style={{ borderBottom: '1px solid var(--line)' }}>
                                  <td
                                    style={{
                                      padding: '6px 8px',
                                      fontFamily: 'var(--font-mono)',
                                      fontWeight: 600,
                                      color: 'var(--ink)',
                                    }}
                                  >
                                    {col}
                                  </td>
                                  <td style={{ padding: '6px 8px' }}>
                                    {isCategory ? (
                                      <select
                                        aria-label={`Logical type for ${col}`}
                                        value={
                                          categoryTypeOverrides[col] ||
                                          (obsType === 'date' ? 'date' : obsType === 'boolean' ? 'boolean' : obsType === 'timestamp' ? 'timestamp' : 'string')
                                        }
                                        onChange={(e) => setCategoryTypeOverrides((prev) => ({ ...prev, [col]: e.target.value }))}
                                        style={{
                                          fontSize: 12,
                                          padding: '2px 6px',
                                          borderRadius: 4,
                                          border: '1px solid var(--line-strong)',
                                          background: 'var(--panel)',
                                          color: 'var(--ink)',
                                          fontFamily: 'var(--font-mono)',
                                        }}
                                      >
                                        <option value="string">Text (VARCHAR)</option>
                                        <option value="boolean">Boolean (BOOLEAN)</option>
                                        <option value="date">Date (DATE)</option>
                                        <option value="timestamp">Timestamp (TIMESTAMP)</option>
                                      </select>
                                    ) : (
                                      <>
                                        <code
                                          style={{
                                            fontSize: 12,
                                            padding: '2px 6px',
                                            background: 'var(--panel-2)',
                                            border: '1px solid var(--line)',
                                            borderRadius: 4,
                                            color: 'var(--ink)',
                                            fontFamily: 'var(--font-mono)',
                                          }}
                                        >
                                          {declType}
                                        </code>
                                        <span style={{ fontSize: 12, color: 'var(--muted)', marginLeft: 6 }}>
                                          ({obsType})
                                        </span>
                                      </>
                                    )}
                                  </td>
                                  <td style={{ padding: '6px 8px' }}>
                                    <span style={roleStyle}>{roleLabel}</span>
                                  </td>
                                  <td
                                    style={{
                                      padding: '6px 8px',
                                      color: (nullCount ?? 0) > 0 ? 'var(--warn)' : 'var(--muted)',
                                      fontSize: 12,
                                    }}
                                  >
                                    {nullCount === undefined ? 'Nullable; validated on import' : nullCount > 0 ? `Nullable (${nullCount} NULLs)` : 'Non-null'}
                                  </td>
                                  <td
                                    style={{
                                      padding: '6px 8px',
                                      fontFamily: 'var(--font-mono)',
                                      color: 'var(--text-2)',
                                      fontSize: 12,
                                    }}
                                  >
                                    {sampleVal !== undefined ? (
                                      String(sampleVal)
                                    ) : (
                                      <span style={{ color: 'var(--faint)' }}>NULL</span>
                                    )}
                                  </td>
                                </tr>
                              );
                            })}
                          </tbody>
                        </table>
                      </div>
                    </div>
                  )}

                  <div className="form-stack">
                    {matchingContract && (
                      <label className="checkbox-field-standard panel-note">
                        <input type="checkbox" checked={joinExisting} onChange={e => setJoinExisting(e.target.checked)} />
                        <span>Add this file as a new load of “{matchingContract.name}”
                          <small className="field-hint">It has the same columns and settings, so the two loads can be compared record by record. Untick to register it as a separate dataset.</small></span>
                      </label>
                    )}
                    <label>
                      {matchingContract && joinExisting ? 'Label for this load' : 'Snapshot name'}
                      <input
                        required
                        value={snapshotName}
                        onChange={(e) => setSnapshotName(e.target.value)}
                        placeholder="e.g. August 2026 Orders"
                      />
                    </label>

                    <div className="form-grid two">
                      <label>
                        Which column uniquely identifies each row?
                        <select
                          required
                          value={confirmedKey}
                          onChange={(e) => { setConfirmedKey(e.target.value); setConfirmedCategories(c => c.filter(x => x !== e.target.value)); }}
                        >
                          <option value="">Select column…</option>
                          {filePreview?.headers.map((h) => (
                            <option key={h} value={h}>
                              {h} ({filePreview.observed_types[h] || 'string'})
                            </option>
                          ))}
                        </select>
                        <small className="field-hint">Example: <code>order_id</code> or <code>row_id</code>. No two rows may share a value, and none may be blank.</small>
                        {keyIssue && <span className="disabled-reason" role="alert">“{confirmedKey}” {keyIssue}, so it cannot identify each row. Choose another column or fix the file.</span>}
                      </label>

                      <label>
                        Which amount do you want to total?
                        <select
                          required
                          value={confirmedMeasure}
                          onChange={(e) => { setConfirmedMeasure(e.target.value); setConfirmedCategories(c => c.filter(x => x !== e.target.value)); }}
                        >
                          <option value="">Select numeric column…</option>
                          {filePreview?.headers.map((h) => (
                            <option key={h} value={h}>
                              {h} ({filePreview.observed_types[h] || 'string'})
                            </option>
                          ))}
                        </select>
                        <small className="field-hint">Example: <code>sales_amount</code>. Both calculations add up this column.</small>
                      </label>
                    </div>

                    {/* Categorical fields checkbox group */}
                    <div className="categories-selection-group">
                      <span className="field-label-text">
                        Which columns should be available for filtering? ({confirmedCategories.length} of 8 selected)
                      </span>
                      <div className="categories-chip-wrap">
                        {filePreview?.headers
                          .filter((h) => h !== confirmedKey && h !== confirmedMeasure && !multiExtras.includes(h))
                          .map((col) => {
                            const isChecked = confirmedCategories.includes(col);
                            return (
                              <label key={col} className={`category-chip ${isChecked ? 'active' : ''}`}>
                                <input
                                  type="checkbox"
                                  checked={isChecked}
                                  onChange={() => toggleCategory(col)}
                                />
                                <span>{col}</span>
                              </label>
                            );
                          })}
                      </div>
                      <small className="field-hint">Example: <code>status</code>, <code>region</code>. Calculations can only filter on the columns ticked here.</small>
                    </div>

                    <div className="form-grid two">
                      <label>
                        What unit does that amount use?
                        {unitSource && (
                          <span className="source-badge" style={{ marginLeft: '6px', fontSize: '12px' }}>
                            Suggested from {unitSource}
                          </span>
                        )}
                        <input
                          required
                          value={confirmedUnit}
                          onChange={(e) => {
                            setConfirmedUnit(e.target.value);
                            setUnitSource(undefined);
                          }}
                          placeholder="Select or enter unit (e.g. USD, EUR, INR, units)"
                        />
                        {!confirmedUnit && (
                          <div style={{ marginTop: '6px', display: 'flex', gap: '6px', alignItems: 'center', flexWrap: 'wrap' }}>
                            <span style={{ fontSize: '12px', color: 'var(--muted)' }}>Common units:</span>
                            {['USD', 'EUR', 'INR', 'GBP', 'count'].map((u) => (
                              <button
                                key={u}
                                type="button"
                                className="button secondary"
                                style={{ padding: '2px 8px', fontSize: '12px' }}
                                onClick={() => {
                                  setConfirmedUnit(u);
                                  setUnitSource('User selection');
                                }}
                              >
                                {u}
                              </button>
                            ))}
                          </div>
                        )}
                        {!confirmedUnit && (
                          <small className="field-hint" style={{ color: 'var(--warn)' }}>
                            Required. Example: INR, USD, points or count. MetricGuard never converts between units.
                          </small>
                        )}
                      </label>

                      <label>
                        In a few words, what does one amount mean?
                        <input
                          required
                          value={confirmedMeaning}
                          onChange={(e) => setConfirmedMeaning(e.target.value)}
                          placeholder="e.g. Sales value of one order line"
                        />
                      </label>
                    </div>

                    <label>
                      What kind of total will you compare?
                      <select value={enableCase ? 'case' : enableSubtraction ? 'subtraction' : enableMulti ? 'multi' : 'single'} onChange={(e) => {
                        const mode = e.target.value;
                        setEnableCase(mode === 'case');
                        setEnableSubtraction(mode === 'subtraction');
                        setEnableMulti(mode === 'multi');
                        if (mode === 'subtraction' && !minuendColumn) setMinuendColumn(confirmedMeasure);
                      }}>
                        <option value="single">A total of one amount column (most common)</option>
                        <option value="multi">Several amount columns, e.g. revenue and cost for margin % (preview)</option>
                        <option value="subtraction">A total of one column minus another (preview)</option>
                        <option value="case">A conditional total — CASE WHEN (preview)</option>
                      </select>
                      <small className="field-hint">Fixed for this dataset once created. Existing datasets keep their own rules.</small>
                    </label>
                    <label>
                      <input type="checkbox" checked={compoundFilters || enableSubtraction || enableCase || enableMulti}
                        disabled={enableSubtraction || enableCase || enableMulti} onChange={(e) => setCompoundFilters(e.target.checked)} />
                      Allow filters combined with AND, OR and NOT
                      <small className="field-hint">AND, OR and NOT; 16 conditions and 8 levels per filter tree. Included in subtraction and CASE modes.</small>
                    </label>
                    {enableMulti && <div className="panel-note">
                      <strong>More amount columns</strong>
                      <p>Besides <code>{confirmedMeasure || 'the main amount'}</code>, tick the other amounts to register. Calculations can then total any one of them or divide one by another, e.g. <code>SUM(revenue - cost) / SUM(revenue)</code>. Each needs its own unit; amounts in different units are never added together.</p>
                      <div style={{ display: 'grid', gap: 6 }}>
                        {filePreview?.headers.filter(h => h !== confirmedKey && h !== confirmedMeasure
                          && (filePreview.observed_types[h] === 'numeric' || extraAmounts.includes(h))).map(col => {
                          const checked = extraAmounts.includes(col);
                          return (
                            <div key={col} style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                              <label className="checkbox-field-standard">
                                <input type="checkbox" checked={checked} onChange={() => {
                                  setExtraAmounts(list => checked ? list.filter(x => x !== col) : [...list, col]);
                                  setConfirmedCategories(c => c.filter(x => x !== col));
                                }} />
                                <span>{col} ({filePreview.observed_types[col] || 'string'})</span>
                              </label>
                              {checked && (
                                <input aria-label={`Unit of ${col}`} style={{ maxWidth: 140 }} value={extraUnits[col] ?? confirmedUnit}
                                  onChange={e => setExtraUnits(u => ({ ...u, [col]: e.target.value }))} placeholder="unit, e.g. INR" />
                              )}
                            </div>
                          );
                        })}
                      </div>
                    </div>}
                    {enableCase && <div className="panel-note">
                      <strong>One conditional measure</strong>
                      <p><code>SUM(CASE WHEN {confirmedMeasure || '&lt;measure&gt;'} IS NOT NULL THEN {confirmedMeasure || '&lt;measure&gt;'} ELSE 0 END)</code></p>
                      <p>This is an editable example, not a business definition. TRUE selects the measure (which may be NULL); FALSE or UNKNOWN selects zero. An empty outer selection returns NULL. WHERE and WHEN together allow 24 conditions and 96 tree nodes.</p>
                      <label className="checkbox-field-standard">
                        <input type="checkbox" checked={scopeConfirmed} onChange={e => setScopeConfirmed(e.target.checked)} />
                        <span>I confirm the entire supplied extract defines the comparison scope.</span>
                      </label>
                      <label className="checkbox-field-standard">
                        <input type="checkbox" checked={comparabilityConfirmed} onChange={e => setComparabilityConfirmed(e.target.checked)} />
                        <span>I confirm all non-NULL measure values are comparable in the declared unit; no unit or currency conversion is implied.</span>
                      </label>
                    </div>}

                    {enableSubtraction && (
                      <div
                        style={{
                          background: 'var(--panel-2)',
                          border: '1px solid var(--line)',
                          borderRadius: 6,
                          padding: 12,
                          marginTop: 8,
                          marginBottom: 8,
                        }}
                      >
                        <div className="form-grid two">
                          <label>
                            Minuend column (A)
                            <select
                              required={enableSubtraction}
                              value={minuendColumn}
                              onChange={(e) => setMinuendColumn(e.target.value)}
                            >
                              <option value="">Select minuend…</option>
                              {filePreview?.headers
                                .filter((h) => h !== confirmedKey && h !== subtrahendColumn)
                                .map((h) => (
                                  <option key={h} value={h}>
                                    {h} ({filePreview.observed_types[h] || 'string'})
                                  </option>
                                ))}
                            </select>
                          </label>

                          <label>
                            Subtrahend column (B)
                            <select
                              required={enableSubtraction}
                              value={subtrahendColumn}
                              onChange={(e) => setSubtrahendColumn(e.target.value)}
                            >
                              <option value="">Select subtrahend…</option>
                              {filePreview?.headers
                                .filter((h) => h !== confirmedKey && h !== minuendColumn)
                                .map((h) => (
                                  <option key={h} value={h}>
                                    {h} ({filePreview.observed_types[h] || 'string'})
                                  </option>
                                ))}
                            </select>
                          </label>
                        </div>
                        <div style={{ marginTop: 8, fontSize: 12, color: 'var(--text-2)' }}>
                          <strong>Expression:</strong>{' '}
                          <code>
                            {minuendColumn || '<operand A>'} - {subtrahendColumn || '<operand B>'} ({confirmedUnit || 'units'})
                          </code>
                        </div>
                        <small className="field-hint" style={{ marginTop: 4, display: 'block' }}>
                          If either {minuendColumn || 'operand A'} or {subtrahendColumn || 'operand B'} is blank/NULL, the expression evaluates to NULL.
                        </small>
                      </div>
                    )}

                    {/* Collapsible Advanced Settings */}
                    <div className="advanced-accordion">
                      <button
                        type="button"
                        className="advanced-toggle-btn"
                        onClick={() => setShowAdvanced(!showAdvanced)}
                        aria-expanded={showAdvanced}
                      >
                        <ChevronDown size={14} className={`chevron-icon ${showAdvanced ? 'rotated' : ''}`} />
                        <span>Advanced details: precision, NULL handling, scope, lineage (optional)</span>
                      </button>

                      {showAdvanced && (
                        <div className="advanced-content-box">
                          {/* 1. Data format */}
                          <div className="advanced-subgroup">
                            <span className="advanced-subgroup-title">Data format</span>
                            <div className="form-grid three">
                              <label>
                                Decimal precision
                                <input
                                  type="number"
                                  min={1}
                                  max={38}
                                  value={precision}
                                  onChange={(e) => setPrecision(Number(e.target.value))}
                                />
                              </label>
                              <label>
                                Decimal scale
                                <input
                                  type="number"
                                  min={0}
                                  max={18}
                                  value={scale}
                                  onChange={(e) => setScale(Number(e.target.value))}
                                />
                              </label>
                              <label>
                                NULL handling policy
                                <select
                                  value={nullPolicy}
                                  onChange={async (e) => {
                                    const policy = e.target.value as 'blank_nullable' | 'native_null' | 'literal_NULL';
                                    setNullPolicy(policy);
                                    if (selectedFile && policy !== 'native_null') {
                                      setBusy(true);
                                      try {
                                        const large = selectedFile.size > 25 * 1024 * 1024;
                                        let text = large ? await selectedFile.slice(0, 2 * 1024 * 1024).text() : await selectedFile.text();
                                        if (large) text = text.slice(0, text.lastIndexOf('\n') + 1);
                                        setFilePreview(parseCsvText(text, 25, policy));
                                        setCategoryTypeOverrides({});
                                      } catch (err) {
                                        setFilePreview(null);
                                        setValidationError(humanizeError(err));
                                      } finally { setBusy(false); }
                                    }
                                  }}
                                >
                                  <option value="blank_nullable" disabled={selectedFile?.name.toLowerCase().endsWith('.parquet')}>CSV blank is NULL</option>
                                  <option value="literal_NULL" disabled={selectedFile?.name.toLowerCase().endsWith('.parquet')}>Literal &quot;NULL&quot; is NULL</option>
                                  <option value="native_null" disabled={!selectedFile?.name.toLowerCase().endsWith('.parquet')}>Parquet native typed NULL</option>
                                </select>
                              </label>
                            </div>
                          </div>

                          {/* 2. Snapshot scope */}
                          <div className="advanced-subgroup">
                            <span className="advanced-subgroup-title">Snapshot scope</span>
                            <label className="checkbox-field-standard">
                              <input
                                type="checkbox"
                                checked={scopeConfirmed}
                                onChange={(e) => setScopeConfirmed(e.target.checked)}
                              />
                              <span>Entire supplied extract defines reconciliation scope</span>
                            </label>
                          </div>

                          {/* 3. External lineage */}
                          <div className="advanced-subgroup">
                            <span className="advanced-subgroup-title">External lineage</span>
                            <label className="compact-file-upload">
                              <span>External preparation manifest (optional JSON)</span>
                              <input
                                type="file"
                                accept=".json,application/json"
                                onChange={(e) => setPreparationFile(e.target.files?.[0] || null)}
                              />
                              <small className="field-hint">
                                Optional JSON manifest recording external upstream lineage hashes.
                              </small>
                            </label>
                          </div>
                        </div>
                      )}
                    </div>
                  </div>
                </form>
              )}

              {/* STEP 3: VALIDATING PROGRESS */}
              {step === 'validating' && (
                <div className="step-status-card">
                  <RefreshCw className="spinning" size={32} />
                  <h3>Validating snapshot…</h3>
                  <p>{validationProgress || 'Checking rows, column types, and data consistency…'}{importElapsed >= 3 && ` (${importElapsed} s)`}</p>
                  {importElapsed >= 10 && (
                    <p className="field-hint">Large files can take a minute or two. You can close this window: the import keeps running, and the dataset appears in the list when it is ready.</p>
                  )}
                </div>
              )}

              {/* STEP 3: SUCCESS */}
              {step === 'success' && (
                <div className="step-status-card success">
                  <div className="success-icon-badge">
                    <CheckCircle2 size={32} />
                  </div>
                  <h3>Snapshot ready</h3>
                  <p>
                    Validated <strong>{(createdSnapshot?.row_count ?? filePreview?.totalRowsSampled ?? 0).toLocaleString('en-US')} rows</strong> and created
                    an immutable analytical snapshot.
                  </p>
                  <div className="snapshot-summary-pill-row">
                    <span><strong>Snapshot:</strong> {createdSnapshot?.name || snapshotName || selectedFile?.name}</span>
                    <span><strong>Rows:</strong> {(createdSnapshot?.row_count ?? filePreview?.totalRowsSampled ?? 0).toLocaleString('en-US')}</span>
                    <span><strong>Key:</strong> {loadContract?.row_key || confirmedKey || 'Not declared'}</span>
                    <span><strong>Amount:</strong> {loadContract ? loadContract.measure : enableSubtraction ? `${minuendColumn} − ${subtrahendColumn}` : enableMulti ? [confirmedMeasure, ...multiExtras].join(", ") : confirmedMeasure || "Not declared"}</span>
                    <span><strong>Unit:</strong> {inheritedUnit(createdSnapshot, loadContract, confirmedUnit) || 'Not declared'}</span>
                  </div>
                </div>
              )}

              {/* STEP 3: ERROR / REFUSAL */}
              {step === 'error' && (
                <div className="step-status-card error">
                  <div className="error-icon-badge">
                    <AlertCircle size={32} />
                  </div>
                  <h3>Couldn’t validate snapshot</h3>
                  <p className="error-lead">{validationError?.message || 'The extract failed row invariant or type checks.'}</p>
                  {validationError?.suggestion && (
                    <div className="error-suggestion-box">
                      <strong>Recommended fix:</strong> {validationError.suggestion}
                    </div>
                  )}
                  {validationError?.code && (
                    <details className="error-technical-details">
                      <summary>Technical details</summary>
                      <code>{validationError.code}</code>
                    </details>
                  )}
                </div>
              )}
            </div>

            {/* Modal Footer Actions */}
            <div className="modal-footer">
              {step === 'upload' && (
                <>
                  <button
                    type="button"
                    className="button secondary"
                    onClick={() => setShowAddModal(false)}
                  >
                    Cancel
                  </button>
                  {sourceType === 'file' && loadContract && (
                    <button type="button" className="button primary" disabled={!uploadReady} onClick={() => void handleAddLoad()}>
                      Validate and add load <ShieldCheck size={16} />
                    </button>
                  )}
                  {sourceType === 'file' && !loadContract && (
                    <button
                      type="button"
                      className="button primary"
                      disabled={!uploadReady}
                      onClick={() => setStep('confirm')}
                    >
                      Continue to confirmation <ArrowRight size={15} />
                    </button>
                  )}
                </>
              )}

              {step === 'confirm' && (
                <>
                  <button
                    type="button"
                    className="button secondary"
                    onClick={() => setStep('upload')}
                  >
                    <ArrowLeft size={15} /> Back
                  </button>
                  {validateBlocker && <span className="disabled-reason" id="validate-blocker">{validateBlocker}</span>}
                    <button
                      type="button"
                      className="button primary"
                      disabled={busy || Boolean(validateBlocker)}
                      aria-describedby={validateBlocker ? 'validate-blocker' : undefined}
                      onClick={handleValidateAndCreate}
                    >
                    Validate & create snapshot <ShieldCheck size={16} />
                  </button>
                </>
              )}

              {step === 'validating' && importElapsed >= 10 && (
                <button type="button" className="button secondary" onClick={() => setShowAddModal(false)}>
                  Close and keep importing
                </button>
              )}

              {step === 'success' && (
                <button
                  type="button"
                  className="button primary"
                  onClick={handleDone}
                >
                  Done
                </button>
              )}

              {step === 'error' && (
                <>
                  <button
                    type="button"
                    className="button secondary"
                    onClick={() => {
                      setValidationError(null);
                      setStep(loadContract ? 'upload' : 'confirm');
                    }}
                  >
                    <ArrowLeft size={15} /> Back to configuration
                  </button>
                  <button
                    type="button"
                    className="button primary"
                    onClick={e => loadContract ? void handleAddLoad() : void handleValidateAndCreate(e)}
                  >
                    <RefreshCw size={15} /> Try again
                  </button>
                </>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function formatBytes(bytes: number): string {
  if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${Math.max(1, Math.round(bytes / 1024)).toLocaleString('en-US')} KB`;
}
