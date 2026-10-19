import { useEffect, useRef, useState } from 'react';
import { UploadCloud } from 'lucide-react';
import { api, type DatasetPreviewRecord, type RelationalContract, type RelationalSnapshot, type SnapshotImport } from '../api';
import { classifyInvestigationError, type ClassifiedInvestigationError } from '../investigationUtils';

type Role = 'fact' | 'dimension';
const pending = (state?: string) => state === 'queued' || state === 'running' || state === 'pending';
const tableName = (file: File | null, fallback: string) => {
  const base = (file?.name || '').replace(/\.csv$/i, '').toLowerCase().replace(/[^a-z0-9_]/g, '_').replace(/^[^a-z_]+/, '');
  return (base || fallback).slice(0, 63);
};

/** A further lookup table (beyond the first): its file, how the main table points to it, and its filter columns. */
type ExtraLookup = { id: string; file: File | null; preview: DatasetPreviewRecord | null; key: string; link: string; filters: string[] };
const MAX_EXTRA_LOOKUPS = 3;

type Props = {
  onCreated: (snapshotId: string) => void;
  refresh: () => Promise<unknown>;
  canUpload: boolean;
  blocker: string;
};

/**
 * Guided upload of one fact table and one lookup (dimension) table. Registers a
 * relational contract from plain answers, then imports both files through the
 * same isolated, validated path as the two-table editor.
 */
export function TwoTableUpload({ onCreated, refresh, canUpload, blocker }: Props) {
  const [files, setFiles] = useState<Record<Role, File | null>>({ fact: null, dimension: null });
  const [previews, setPreviews] = useState<Record<Role, DatasetPreviewRecord | null>>({ fact: null, dimension: null });
  const [factKey, setFactKey] = useState('');
  const [dimKey, setDimKey] = useState('');
  const [link, setLink] = useState('');
  const [amount, setAmount] = useState('');
  const [unit, setUnit] = useState('');
  const [meaning, setMeaning] = useState('');
  const [factFilters, setFactFilters] = useState<string[]>([]);
  const [dimFilters, setDimFilters] = useState<string[]>([]);
  const [name, setName] = useState('');
  const [precision, setPrecision] = useState(18);
  const [scale, setScale] = useState(2);
  const [nullPolicy, setNullPolicy] = useState<'blank_nullable' | 'literal_NULL'>('blank_nullable');
  const [wholeFiles, setWholeFiles] = useState(false);
  const [sameUnit, setSameUnit] = useState(false);
  const [job, setJob] = useState<SnapshotImport | null>(null);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<ClassifiedInvestigationError | null>(null);
  const [extras, setExtras] = useState<ExtraLookup[]>([]);
  const previewRequests = useRef<Record<Role, number>>({ fact: 0, dimension: 0 });

  const headers = (role: Role) => previews[role]?.preview?.headers || [];
  const extraHeaders = (i: number) => extras[i]?.preview?.preview?.headers || [];
  const links = [link, ...extras.map(x => x.link)].filter(Boolean);
  const updateExtra = (i: number, patch: Partial<ExtraLookup>) => setExtras(list => list.map((x, j) => j === i ? { ...x, ...patch } : x));

  // Poll previews and the import until they finish.
  useEffect(() => {
    const waiting = (['fact', 'dimension'] as Role[]).filter(r => previews[r] && pending(previews[r]!.state));
    const waitingExtras = extras.map((x, i) => (x.preview && pending(x.preview.state) ? i : -1)).filter(i => i >= 0);
    if (!waiting.length && !waitingExtras.length && !(job && pending(job.state))) return;
    let active = true;
    const timer = setTimeout(async () => {
      try {
        for (const role of waiting) {
          const next = await api.datasetPreview(previews[role]!.id);
          if (active) setPreviews(p => ({ ...p, [role]: next }));
        }
        for (const i of waitingExtras) {
          const next = await api.datasetPreview(extras[i].preview!.id);
          if (active) updateExtra(i, { preview: next });
        }
        if (job && pending(job.state)) {
          const next = await api.snapshotImport(job.id);
          if (!active) return;
          setJob(next);
          if (next.state === 'validated' && next.snapshot_id) { await refresh(); onCreated(next.snapshot_id); }
          else if (!pending(next.state)) setProblem(classifyInvestigationError(next.error || { code: 'IMPORT_FAILED', message: 'The files could not be validated.' }));
        }
      } catch (e) { if (active) setProblem(classifyInvestigationError(e)); }
    }, 1200);
    return () => { active = false; clearTimeout(timer); };
  }, [previews, job, extras]);

  const choose = (role: Role, file: File | null) => {
    const request = ++previewRequests.current[role];
    setFiles(f => ({ ...f, [role]: file }));
    setPreviews(p => ({ ...p, [role]: null }));
    if (role === 'fact') { setFactKey(''); setLink(''); setAmount(''); setFactFilters([]); setExtras(list => list.map(x => ({ ...x, link: '' }))); }
    else { setDimKey(''); setDimFilters([]); }
    if (!file) return;
    const form = new FormData();
    form.append('file', file);
    api.previewDataset(form).then(p => {
      if (previewRequests.current[role] === request) setPreviews(prev => ({ ...prev, [role]: p }));
    }).catch(e => { if (previewRequests.current[role] === request) setProblem(classifyInvestigationError(e)); });
  };

  const chooseExtra = (i: number, file: File | null) => {
    const id = extras[i].id;
    updateExtra(i, { file, preview: null, key: '', link: '', filters: [] });
    if (!file) return;
    const form = new FormData();
    form.append('file', file);
    api.previewDataset(form).then(p => setExtras(list => list.map(x => x.id === id && x.file === file ? { ...x, preview: p } : x)))
      .catch(e => setProblem(classifyInvestigationError(e)));
  };

  const extraMissing = extras.map((x, i) => !x.file ? `Choose the CSV for lookup table ${i + 2}.`
    : !extraHeaders(i).length ? `Wait for lookup table ${i + 2} to be read.`
    : !x.link ? `Choose the main-table column that connects to lookup table ${i + 2}.`
    : !x.key ? `Choose lookup table ${i + 2}’s identifying column.` : '').find(Boolean) || '';
  const missing = !files.fact || !files.dimension ? 'Choose both CSV files.'
    : !headers('fact').length || !headers('dimension').length ? 'Wait for both files to be read.'
    : !factKey ? 'Choose the column that identifies each record in the main table.'
    : !amount ? 'Choose the amount to total.'
    : !link ? 'Choose the column that connects to the lookup table.'
    : !dimKey ? 'Choose the lookup table’s identifying column.'
    : !unit.trim() ? 'Enter the unit of the amount.'
    : factKey === amount || links.includes(factKey) || links.includes(amount) ? 'Record identifier, amount and connecting columns must be different.'
    : !Number.isInteger(precision) || precision < 1 || precision > 38 ? 'Decimal precision must be a whole number from 1 to 38.'
    : !Number.isInteger(scale) || scale < 0 || scale > 18 || scale > precision ? 'Decimal places must be a whole number from 0 to 18, no greater than precision.'
    : extraMissing ? extraMissing
    : new Set(links).size !== links.length ? 'Each lookup table needs its own connecting column in the main table.'
    : !wholeFiles || !sameUnit ? 'Tick both confirmations.'
    : '';

  async function submit() {
    setBusy(true); setProblem(null);
    try {
      const factTable = tableName(files.fact, 'fact_records');
      let dimTable = tableName(files.dimension, 'dim_records');
      if (dimTable === factTable) dimTable = `${dimTable.slice(0, 56)}_lookup`;
      if (extras.length) {
        // Several lookup tables: relational contract v2, one lookup_<role> file per lookup.
        const used = new Set([factTable, 'fact']);
        const lookups = [{ file: files.dimension!, key: dimKey, link, filters: dimFilters }, ...extras.map(x => ({ file: x.file!, key: x.key, link: x.link, filters: x.filters }))]
          .map((x, i) => {
            const base = tableName(x.file, `lookup_${i + 1}`);
            let table = base, suffix = 1;
            while (used.has(table)) {
              const tail = `_${suffix++}`;
              table = `${base.slice(0, 63 - tail.length)}${tail}`;
            }
            used.add(table);
            return { ...x, table };
          });
        const contract = await api.post<RelationalContract>('/relational/contracts', {
          version: 2, mode: 'relational_snapshot', policy: 'relational-star-v1',
          name: name.trim() || `${factTable} with ${lookups.map(x => x.table).join(', ')}`,
          fact: { table: factTable, key: factKey, grain: `One row per ${factKey}`, categories: factFilters.filter(c => c !== factKey && c !== amount && !links.includes(c)) },
          dimensions: lookups.map(x => ({ role: x.table, table: x.table, key: x.key, grain: `One row per ${x.key}`,
                                          categories: x.filters.filter(c => c !== x.key), fact_foreign_key: x.link })),
          measure: { column: amount, precision, scale }, measure_meaning: meaning.trim() || amount, unit_label: unit.trim(),
          values_comparable: true, scope: 'entire_supplied_snapshot', null_policy: nullPolicy,
        });
        const form = new FormData();
        form.append('fact_file', files.fact!);
        for (const x of lookups) form.append(`lookup_${x.table}`, x.file);
        form.append('contract_id', contract.id);
        form.append('name', (name.trim() || [files.fact!.name, ...lookups.map(x => x.file.name)].join(' + ')).slice(0, 160));
        const result = await api.uploadRelational(form);
        if ('state' in result) setJob(result as SnapshotImport);
        else { await refresh(); onCreated((result as RelationalSnapshot).id); }
        return;
      }
      const contract = await api.post<RelationalContract>('/relational/contracts', {
        version: 1, mode: 'relational_snapshot', policy: 'relational-snapshot-v1',
        name: name.trim() || `${factTable} with ${dimTable}`,
        fact: { table: factTable, key: factKey, grain: `One row per ${factKey}`, categories: factFilters.filter(c => c !== factKey && c !== amount && c !== link) },
        dimension: { table: dimTable, key: dimKey, grain: `One row per ${dimKey}`, categories: dimFilters.filter(c => c !== dimKey) },
        relationship: { fact_foreign_key: link, dimension_key: dimKey, cardinality: 'many_to_one', missing_dimension_policy: 'preserve_and_report' },
        measure: { column: amount, precision, scale }, measure_meaning: meaning.trim() || amount, unit_label: unit.trim(),
        values_comparable: true, scope: 'entire_supplied_snapshot', null_policy: nullPolicy,
      });
      const form = new FormData();
      form.append('fact_file', files.fact!);
      form.append('dimension_file', files.dimension!);
      form.append('contract_id', contract.id);
      form.append('name', (name.trim() || `${files.fact!.name} + ${files.dimension!.name}`).slice(0, 160));
      const result = await api.uploadRelational(form);
      if ('state' in result) setJob(result as SnapshotImport);
      else { await refresh(); onCreated((result as RelationalSnapshot).id); }
    } catch (e) {
      setProblem(classifyInvestigationError(e));
    } finally {
      setBusy(false);
    }
  }

  const columnSelect = (role: Role, value: string, set: (v: string) => void, exclude: string[] = []) => (
    <select value={value} onChange={e => set(e.target.value)}>
      <option value="">Choose a column…</option>
      {headers(role).filter(h => !exclude.includes(h)).map(h => <option key={h} value={h}>{h}</option>)}
    </select>
  );
  const filterBoxes = (role: Role, chosen: string[], set: (v: string[]) => void, exclude: string[]) => (
    <div className="categories-chip-wrap">
      {headers(role).filter(h => !exclude.includes(h)).map(h => (
        <label key={h} className={`category-chip ${chosen.includes(h) ? 'active' : ''}`}>
          <input type="checkbox" checked={chosen.includes(h)} disabled={!chosen.includes(h) && chosen.length >= 8}
            onChange={e => set(e.target.checked ? [...chosen, h] : chosen.filter(x => x !== h))} />
          <span>{h}</span>
        </label>
      ))}
    </div>
  );

  return (
    <div className="guided-card" aria-labelledby="two-table-title">
      <h2 id="two-table-title">Upload connected tables</h2>
      <p className="lead">A main table of records (for example sales or rentals) and one to four lookup tables describing things each record points to (for example stores or days). Use UTF-8 CSV files.</p>
      {!canUpload && <span className="disabled-reason">{blocker}</span>}
      <div className="calc-grid">
        {(['fact', 'dimension'] as Role[]).map(role => (
          <div className="calc-card" key={role}>
            <h3>{role === 'fact' ? 'Main table (records)' : 'Lookup table'}</h3>
            <label>CSV file<input type="file" accept=".csv,text/csv" disabled={!canUpload || busy} onChange={e => choose(role, e.target.files?.[0] || null)} /></label>
            {previews[role] && pending(previews[role]!.state) && <p role="status">Reading the file…</p>}
            {previews[role]?.error && <span className="disabled-reason">{previews[role]!.error!.message}</span>}
            {headers(role).length > 0 && <small>{headers(role).length} columns: {headers(role).join(', ')}</small>}
          </div>
        ))}
      </div>

      {headers('fact').length > 0 && headers('dimension').length > 0 && (
        <>
          <div className="calc-grid">
            <div className="calc-card">
              <label>Which column uniquely identifies each record in the main table?{columnSelect('fact', factKey, setFactKey)}<span className="example">Example: <code>rental_id</code></span></label>
              <label>Which amount do you want to total?{columnSelect('fact', amount, setAmount, [factKey])}<span className="example">Example: <code>rides</code> or <code>sales_amount</code></span></label>
              <label>What unit does that amount use?<input value={unit} onChange={e => setUnit(e.target.value)} placeholder="e.g. INR, rentals, points" /><span className="example">MetricGuard never converts units.</span></label>
              <label>In a few words, what does one amount mean? (optional)<input value={meaning} onChange={e => setMeaning(e.target.value)} placeholder="e.g. Rentals in one hour" /></label>
            </div>
            <div className="calc-card">
              <label>Which column in the main table connects to the lookup table?{columnSelect('fact', link, setLink, [factKey, amount])}<span className="example">Example: <code>day_id</code> in rentals points to a day</span></label>
              <label>Which column uniquely identifies each row of the lookup table?{columnSelect('dimension', dimKey, setDimKey)}<span className="example">Records whose link has no match are kept and reported, never dropped silently.</span></label>
            </div>
          </div>
          <fieldset style={{ border: 0, padding: 0, margin: 0, display: 'grid', gap: 6 }}>
            <legend style={{ fontSize: 13, fontWeight: 600 }}>Which columns should be available for filtering? (up to 8 per table)</legend>
            <small>Main table</small>{filterBoxes('fact', factFilters, setFactFilters, [factKey, amount, ...links])}
            <small>Lookup table</small>{filterBoxes('dimension', dimFilters, setDimFilters, [dimKey])}
          </fieldset>
          {extras.map((x, i) => (
            <div className="calc-card" key={x.id}>
              <h3>Lookup table {i + 2}</h3>
              <label>CSV file<input type="file" accept=".csv,text/csv" disabled={!canUpload || busy} onChange={e => chooseExtra(i, e.target.files?.[0] || null)} /></label>
              {x.preview && pending(x.preview.state) && <p role="status">Reading the file…</p>}
              {x.preview?.error && <span className="disabled-reason">{x.preview.error.message}</span>}
              {extraHeaders(i).length > 0 && (
                <>
                  <label>Which column in the main table connects to this lookup table?
                    <select value={x.link} onChange={e => updateExtra(i, { link: e.target.value })}>
                      <option value="">Choose a column…</option>
                      {headers('fact').filter(h => h !== factKey && h !== amount && !links.filter(l => l !== x.link).includes(h)).map(h => <option key={h} value={h}>{h}</option>)}
                    </select>
                  </label>
                  <label>Which column uniquely identifies each row of this lookup table?
                    <select value={x.key} onChange={e => updateExtra(i, { key: e.target.value })}>
                      <option value="">Choose a column…</option>
                      {extraHeaders(i).map(h => <option key={h} value={h}>{h}</option>)}
                    </select>
                  </label>
                  <small>Filter columns (up to 8)</small>
                  <div className="categories-chip-wrap">
                    {extraHeaders(i).filter(h => h !== x.key).map(h => (
                      <label key={h} className={`category-chip ${x.filters.includes(h) ? 'active' : ''}`}>
                        <input type="checkbox" checked={x.filters.includes(h)} disabled={!x.filters.includes(h) && x.filters.length >= 8}
                          onChange={e => updateExtra(i, { filters: e.target.checked ? [...x.filters, h] : x.filters.filter(v => v !== h) })} />
                        <span>{h}</span>
                      </label>
                    ))}
                  </div>
                </>
              )}
              <button type="button" className="button secondary small" style={{ width: 'fit-content' }} onClick={() => setExtras(list => list.filter((_, j) => j !== i))}>Remove this lookup table</button>
            </div>
          ))}
          {extras.length < MAX_EXTRA_LOOKUPS && (
            <button type="button" className="button secondary small" style={{ width: 'fit-content' }} disabled={!canUpload || busy}
              onClick={() => setExtras(list => [...list, { id: crypto.randomUUID(), file: null, preview: null, key: '', link: '', filters: [] }])}>
              Add another lookup table (e.g. products as well as customers)
            </button>
          )}
          <label>Dataset name (optional)<input value={name} maxLength={160} onChange={e => setName(e.target.value)} placeholder="e.g. Hourly rentals with day details" /></label>
          <label className="checkbox-field-standard"><input type="checkbox" checked={wholeFiles} onChange={e => setWholeFiles(e.target.checked)} /> <span>These complete files are the whole dataset to compare.</span></label>
          <label className="checkbox-field-standard"><input type="checkbox" checked={sameUnit} onChange={e => setSameUnit(e.target.checked)} /> <span>Every amount is in the unit above; no conversion is needed.</span></label>
          <details>
            <summary>Advanced details</summary>
            <div className="calc-grid" style={{ marginTop: 8 }}>
              <label>Decimal precision<input type="number" min={1} max={38} value={precision} onChange={e => setPrecision(Number(e.target.value))} /></label>
              <label>Decimal places<input type="number" min={0} max={18} value={scale} onChange={e => setScale(Number(e.target.value))} /></label>
              <label>Blank values
                <select value={nullPolicy} onChange={e => setNullPolicy(e.target.value as typeof nullPolicy)}>
                  <option value="blank_nullable">An empty cell means blank (NULL)</option>
                  <option value="literal_NULL">The text NULL means blank</option>
                </select>
              </label>
            </div>
          </details>
          <div className="guided-nav">
            <span id="two-table-blocker" className="disabled-reason">{missing}</span>
            <button className="button primary" disabled={busy || !canUpload || Boolean(missing) || pending(job?.state)} aria-describedby={missing ? 'two-table-blocker' : undefined} onClick={() => void submit()}>
              <UploadCloud size={15} /> {busy || pending(job?.state) ? 'Validating both files…' : 'Validate and use these tables'}
            </button>
          </div>
        </>
      )}
      {problem && (
        <div className="alert error" role="alert">
          <span><strong>{problem.title}:</strong> {problem.message}{problem.guidance && <> — {problem.guidance}</>}</span>
        </div>
      )}
    </div>
  );
}
