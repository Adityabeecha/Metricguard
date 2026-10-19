import { useState } from 'react';
import { Database } from 'lucide-react';
import { api, type FlatContract } from '../api';
import { classifyInvestigationError, type ClassifiedInvestigationError } from '../investigationUtils';

type Kind = 'string' | 'boolean' | 'date' | 'timestamp';
type FilterCol = { name: string; type: Kind };

type Props = {
  workspaceId: string;
  onCreated: (snapshotId: string) => void;
  refresh: () => Promise<unknown>;
};

const IDENT = /^[A-Za-z_][A-Za-z0-9_]{0,127}$/;

/**
 * Freeze a PostgreSQL table into a dataset. The read-only collector runs on the
 * MetricGuard server in one REPEATABLE READ transaction; the connection string is
 * used for this request only and stored with its password removed.
 */
export function WarehouseSource({ workspaceId, onCreated, refresh }: Props) {
  const [uri, setUri] = useState('');
  const [schema, setSchema] = useState('public');
  const [table, setTable] = useState('');
  const [key, setKey] = useState('');
  const [amount, setAmount] = useState('');
  const [precision, setPrecision] = useState(18);
  const [scale, setScale] = useState(2);
  const [unit, setUnit] = useState('');
  // Optional further amount columns, "cost" or "units:pieces" (name, or name:unit).
  const [moreAmounts, setMoreAmounts] = useState('');
  const [filters, setFilters] = useState<FilterCol[]>([{ name: '', type: 'string' }]);
  const [cutoffColumn, setCutoffColumn] = useState('');
  const [cutoffValue, setCutoffValue] = useState(new Date().toISOString().slice(0, 10));
  const [sample, setSample] = useState(true);
  const [sampleRows, setSampleRows] = useState(1000);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<ClassifiedInvestigationError | null>(null);
  const [done, setDone] = useState('');

  const named = filters.filter(f => f.name.trim());
  const extras = moreAmounts.split(',').map(x => x.trim()).filter(Boolean).map(x => {
    const [name, ...rest] = x.split(':');
    return { name: name.trim(), unit: rest.join(':').trim() || unit.trim() };
  });
  const blocker = !uri.trim() ? 'Enter the connection string.'
    : !IDENT.test(schema) || !IDENT.test(table) ? 'Enter a valid schema and table name.'
    : !IDENT.test(key) ? 'Enter the column that uniquely identifies each row.'
    : !IDENT.test(amount) || amount === key ? 'Enter the amount column to total (different from the key).'
    : !unit.trim() ? 'Enter the unit of the amount.'
    : !Number.isInteger(precision) || precision < 1 || precision > 38 ? 'Decimal precision must be a whole number from 1 to 38.'
    : !Number.isInteger(scale) || scale < 0 || scale > 18 || scale > precision ? 'Decimal places must be a whole number from 0 to 18, no greater than precision.'
    : sample && (!Number.isInteger(sampleRows) || sampleRows < 1 || sampleRows > 5000000) ? 'Sample size must be a whole number from 1 to 5,000,000.'
    : extras.some(x => !IDENT.test(x.name) || x.name === key || x.name === amount) ? 'Other amount columns must be valid names, different from the key and the main amount.'
    : new Set(extras.map(x => x.name)).size !== extras.length || extras.length > 7 ? 'List each other amount column once (at most 7).'
    : named.some(f => !IDENT.test(f.name) || f.name === key || f.name === amount || extras.some(x => x.name === f.name)) ? 'Filter columns must be valid names, different from the key and amounts.'
    : new Set(named.map(f => f.name)).size !== named.length ? 'Each filter column can be listed once.'
    : !IDENT.test(cutoffColumn) ? 'Enter the date or timestamp column used as the cutoff.'
    : !/^\d{4}-\d{2}-\d{2}/.test(cutoffValue) ? 'Enter a cutoff date like 2026-10-01.'
    : '';

  async function extract(full: boolean) {
    setBusy(true); setProblem(null); setDone('');
    try {
      const categories = named.map(f => f.name);
      const common = {
        predicate_policy: 'flat-boolean-v1', mode: 'flat_snapshot', name: `${schema}.${table}`, row_key: key, scale, categories,
        dimension_types: Object.fromEntries(named.map(f => [f.name, f.type])), nullable_categories: categories,
        measure_meaning: `${[amount, ...extras.map(x => x.name)].join(', ')} from ${schema}.${table}`, unit_label: unit.trim(),
        values_comparable: true, scope: 'entire_supplied_snapshot', null_policy: 'native_null',
      };
      // Several amounts register a v8 dataset (totals of any one, ratios such as margin %).
      const contract = await api.createFlatContract(extras.length ? {
        ...common, version: 8, measure_policy: 'flat-multi-measure-v1',
        decimal_columns: [{ name: amount, precision, scale, unit_label: unit.trim() },
                          ...extras.map(x => ({ name: x.name, precision, scale, unit_label: x.unit }))],
      } : { ...common, version: 5, measure: amount, precision }) as FlatContract;
      const extraction = await api.post<{ snapshot_id?: string; row_count?: number; state?: string; error?: { message?: string } }>(
        `/projects/${encodeURIComponent(workspaceId)}/sources/postgres/extract`, {
          source_uri: uri.trim(), schema_name: schema, table_name: table, contract_id: contract.id,
          cutoff_column: cutoffColumn, cutoff_value: cutoffValue,
          snapshot_name: `${schema}.${table} at ${cutoffValue}`,
          ...(full ? {} : { sample_rows: sampleRows }),
        });
      setUri(''); // never kept in the page longer than needed
      if (!extraction.snapshot_id) throw new Error(extraction.error?.message || 'The extraction did not produce a dataset.');
      await refresh();
      setDone(`${full ? 'Full table' : `Sample of ${sampleRows} rows`} frozen: ${extraction.row_count ?? ''} rows.`);
      onCreated(extraction.snapshot_id);
    } catch (e) {
      setProblem(classifyInvestigationError(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="guided-card" aria-labelledby="warehouse-title">
      <h2 id="warehouse-title">Use a PostgreSQL table</h2>
      <p className="lead">MetricGuard reads the table once, read-only and in a single consistent transaction, and freezes the rows up to your cutoff as a dataset. Start with a sample to check your answers, then take the full table (up to 5,000,000 rows). Large datasets support bounded total and record-count comparisons; distinct counts and ratios require at most 100,000 rows.</p>
      <label>Connection string
        <input type="password" autoComplete="off" spellCheck={false} value={uri} onChange={e => setUri(e.target.value)} placeholder="postgresql://user:password@host:5432/database" />
        <span className="example">Used for this request only. It is never saved; the dataset records the connection with the password removed. Use a read-only database user.</span>
      </label>
      <div className="calc-grid">
        <div className="calc-card">
          <label>Schema<input value={schema} onChange={e => setSchema(e.target.value)} /></label>
          <label>Table<input value={table} onChange={e => setTable(e.target.value)} placeholder="orders" /></label>
          <label>Which column uniquely identifies each row?<input value={key} onChange={e => setKey(e.target.value)} placeholder="order_id" /></label>
          <label>Which amount do you want to total?<input value={amount} onChange={e => setAmount(e.target.value)} placeholder="sales_amount" /></label>
          <label>What unit does that amount use?<input value={unit} onChange={e => setUnit(e.target.value)} placeholder="INR, USD, points" /></label>
          <label>Other amount columns (optional)<input value={moreAmounts} onChange={e => setMoreAmounts(e.target.value)} placeholder="cost, units:pieces" />
            <span className="example">For ratios such as margin % or revenue per unit. Same unit as above unless written as name:unit; same decimal places.</span></label>
        </div>
        <div className="calc-card">
          <fieldset style={{ border: 0, padding: 0, margin: 0, display: 'grid', gap: 6 }}>
            <legend style={{ fontSize: 13, fontWeight: 600 }}>Which columns should be available for filtering? (up to 8)</legend>
            {filters.map((f, i) => (
              <div key={i} style={{ display: 'grid', gridTemplateColumns: 'minmax(0,1fr) minmax(0,0.8fr) auto', gap: 6 }}>
                <input aria-label={`Filter column ${i + 1}`} value={f.name} placeholder="status" onChange={e => setFilters(fs => fs.map((x, j) => j === i ? { ...x, name: e.target.value } : x))} />
                <select aria-label={`Filter column ${i + 1} type`} value={f.type} onChange={e => setFilters(fs => fs.map((x, j) => j === i ? { ...x, type: e.target.value as Kind } : x))}>
                  <option value="string">text</option><option value="boolean">true/false</option><option value="date">date</option><option value="timestamp">timestamp</option>
                </select>
                <button type="button" className="button secondary small" aria-label={`Remove filter column ${i + 1}`} onClick={() => setFilters(fs => fs.filter((_, j) => j !== i))}>×</button>
              </div>
            ))}
            <button type="button" className="button secondary small" style={{ width: 'fit-content' }} disabled={filters.length >= 8} onClick={() => setFilters(fs => [...fs, { name: '', type: 'string' }])}>Add filter column</button>
          </fieldset>
          <label>Cutoff column (rows up to this point are included)<input value={cutoffColumn} onChange={e => setCutoffColumn(e.target.value)} placeholder="created_at" /></label>
          <label>Cutoff value<input value={cutoffValue} onChange={e => setCutoffValue(e.target.value)} placeholder="2026-10-01" /></label>
        </div>
      </div>
      <details>
        <summary>Advanced details</summary>
        <div className="calc-grid" style={{ marginTop: 8 }}>
          <label>Decimal precision<input type="number" min={1} max={38} value={precision} onChange={e => setPrecision(Number(e.target.value))} /></label>
          <label>Decimal places<input type="number" min={0} max={18} value={scale} onChange={e => setScale(Number(e.target.value))} /></label>
          <label>Sample size<input type="number" min={1} max={5000000} value={sampleRows} onChange={e => setSampleRows(Number(e.target.value))} /></label>
        </div>
      </details>
      <label className="checkbox-field-standard"><input type="checkbox" checked={sample} onChange={e => setSample(e.target.checked)} /> <span>Take a sample first ({sampleRows.toLocaleString()} rows by key)</span></label>
      <div className="guided-nav">
        <span id="warehouse-blocker" className="disabled-reason">{blocker}</span>
        <button className="button primary" disabled={busy || Boolean(blocker)} aria-describedby={blocker ? 'warehouse-blocker' : undefined} onClick={() => void extract(!sample)}>
          <Database size={15} /> {busy ? 'Reading the table…' : sample ? 'Take sample' : 'Freeze full table'}
        </button>
      </div>
      {done && <p role="status">{done} {sample && 'When it looks right, untick “Take a sample first” and freeze the full table.'}</p>}
      {problem && <div className="alert error" role="alert"><span><strong>{problem.title}:</strong> {problem.message}</span></div>}
    </div>
  );
}
