import { useState } from 'react';
import { Layers } from 'lucide-react';
import { api, ApiError, type RelationalContract, type Snapshot } from '../api';

/** Above this many fact records a two-table comparison must run on a prepared (joined) copy. */
export const DIRECT_TWO_TABLE_LIMIT = 100_000;

type Prepared = { snapshot: Snapshot; column_mapping: Record<string, string> };

type Props = {
  snapshot: Snapshot;
  contract: RelationalContract;
  /** Called with the new single-table dataset once it is ready. */
  onPrepared: (snapshotId: string) => void | Promise<void>;
};

/**
 * Joins the fact table to its lookups once and freezes the result as a single-table dataset,
 * which every calculation type can compare at up to 5,000,000 rows. Columns keep their source
 * names; a name used by two tables gets its lookup prefix (customer_region, store_region).
 */
export function PrepareTwoTable({ snapshot, contract, onPrepared }: Props) {
  const lookups = contract.dimensions || [];
  const [joins, setJoins] = useState<Record<string, 'left' | 'inner' | 'skip'>>(
    () => Object.fromEntries(lookups.map(d => [d.role, 'left' as const])));
  const fields = [
    ...contract.fact.categories.map(c => ({ id: `fact.${c}`, role: 'fact', column: c, table: contract.fact.table })),
    ...lookups.flatMap(d => d.categories.map(c => ({ id: `${d.role}.${c}`, role: d.role, column: c, table: d.table }))),
  ];
  const [chosen, setChosen] = useState<string[]>(() => fields.slice(0, 8).map(f => f.id));
  const [name, setName] = useState(`${snapshot.name} (prepared)`);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState('');
  const [done, setDone] = useState<Prepared | null>(null);

  if (contract.version === 1) {
    return <p className="disabled-reason">This dataset uses the original one-lookup format, which cannot be prepared. Upload it again as two connected tables to compare more than {DIRECT_TWO_TABLE_LIMIT.toLocaleString()} facts.</p>;
  }
  const usable = fields.filter(f => f.role === 'fact' || joins[f.role] !== 'skip');
  const selected = chosen.filter(id => usable.some(f => f.id === id));
  const toggle = (id: string) => setChosen(list => list.includes(id) ? list.filter(x => x !== id) : list.length >= 8 ? list : [...list, id]);

  const prepare = async () => {
    setBusy(true); setProblem('');
    try {
      const body = {
        name: name.trim() || `${snapshot.name} (prepared)`,
        joins: Object.fromEntries(Object.entries(joins).filter(([, kind]) => kind !== 'skip')),
        selected_columns: selected,
      };
      const result = await api.post<Prepared>(`/relational/snapshots/${encodeURIComponent(snapshot.id)}/prepare-flat`, body);
      setDone(result);
      await onPrepared(result.snapshot.id);
    } catch (e) {
      setProblem(e instanceof ApiError ? `${e.message} (${e.code})` : e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  if (done) {
    return (
      <div className="guided-explain" role="status">
        <b>Ready: “{done.snapshot.name}”</b> ({done.snapshot.row_count.toLocaleString()} rows). Filter on{' '}
        {Object.entries(done.column_mapping).map(([source, column], i) => (
          <span key={source}>{i ? ', ' : ''}<code>{column}</code>{column !== source.split('.')[1] && <> (from {source})</>}</span>
        ))}. The amount column is <code>amount</code>.
      </div>
    );
  }

  return (
    <div className="guided-explain prepare-two-table">
      <b><Layers size={14} /> Prepare for large comparisons</b>
      <p>Joins the fact table to its lookups once and saves the result as a single-table dataset. Every calculation type then works on it, up to 5,000,000 rows. The original dataset is not changed.</p>
      {lookups.length > 0 && (
        <fieldset>
          <legend>How each lookup joins</legend>
          {lookups.map(d => (
            <label key={d.role} className="prepare-row">
              <span><code>{d.table}</code> ({d.role})</span>
              <select value={joins[d.role]} onChange={e => setJoins(j => ({ ...j, [d.role]: e.target.value as 'left' | 'inner' | 'skip' }))}>
                <option value="left">Keep every fact (LEFT JOIN)</option>
                <option value="inner">Only facts with a match (INNER JOIN)</option>
                <option value="skip">Don’t use this lookup</option>
              </select>
            </label>
          ))}
        </fieldset>
      )}
      <fieldset>
        <legend>Columns to filter on (up to 8)</legend>
        <div className="column-picker">
          {usable.map(f => (
            <label key={f.id}>
              <input type="checkbox" checked={selected.includes(f.id)} onChange={() => toggle(f.id)}
                disabled={!selected.includes(f.id) && selected.length >= 8} />
              <code>{f.column}</code> <span className="example">({f.table})</span>
            </label>
          ))}
        </div>
      </fieldset>
      <label className="prepare-row">
        <span>Name</span>
        <input value={name} onChange={e => setName(e.target.value)} maxLength={160} />
      </label>
      <div className="report-actions">
        <button className="button primary small" disabled={busy || !selected.length} onClick={() => void prepare()}>
          {busy ? 'Preparing…' : 'Prepare dataset'}
        </button>
        {!selected.length && <span className="disabled-reason">Choose at least one column.</span>}
      </div>
      {busy && <p className="example" role="status">Large datasets take about 15 seconds per 500,000 facts.</p>}
      {problem && <div className="alert error" role="alert"><span>{problem}</span></div>}
    </div>
  );
}
