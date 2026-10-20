import { useEffect, useState, type ReactNode } from 'react';
import { api, type ChangeCategory, type Evidence, type FlatEvidence, type RelationalEvidence } from '../../api';
import { groupDigits } from '../../resultSummary';
import { runningTotals } from '../../exactDecimal';
import { categoryLabel, type CategoryKey } from './categories';

type Row = Evidence & Partial<FlatEvidence> & Partial<RelationalEvidence>;
const keyOf = (row: Row) => row.fact_key ?? row.row_key ?? row.order_id ?? '—';
const minus = (text: string) => text.replace(/^-/, '−');

/** A 'Where it sits' group the ledger is narrowed to. */
export type LedgerGroup = { column: string; columnLabel: string; value: unknown; noMatch?: boolean; label: string; totalText: string };

export type LedgerProps = {
  runId: string;
  group?: LedgerGroup | null;
  onClearGroup?: () => void;
  categories: ChangeCategory[];
  category: CategoryKey | '';
  onCategory: (key: CategoryKey | '') => void;
  labels: { loads?: boolean; distinct?: boolean };
  keyHeader: string;
  /** Current and proposed state of one record, e.g. "Included · 200.00". */
  side: (row: Row, which: 'baseline' | 'target') => ReactNode;
  change: (row: Row) => ReactNode;
  /** Exact running totals are shown for additive results (not ratios). */
  additive: boolean;
  totalText: string;
  balanceText: ReactNode;
  balanced: boolean;
  unchanged: number | null;
  onShowRegister: () => void;
  unchangedNoun: string;
};

/** Exhibit A (docs/design/evidence-ui-spec.md §3): the records that explain the difference, as a ruled ledger. */
export function Ledger(props: LedgerProps) {
  const { runId, categories, category, onCategory, labels, keyHeader, side, change, additive, group } = props;
  const [limit, setLimit] = useState(10);
  const [search, setSearch] = useState('');
  const [query, setQuery] = useState('');
  // Search as you type, without a request per keystroke.
  useEffect(() => { const timer = setTimeout(() => setQuery(search.trim()), 250); return () => clearTimeout(timer); }, [search]);
  const [rows, setRows] = useState<Row[]>([]);
  const [total, setTotal] = useState(0);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState('');
  useEffect(() => { setLimit(10); }, [category, group, query]);
  useEffect(() => {
    let active = true;
    setBusy(true); setProblem('');
    const load = group
      ? api.evidenceGroup(runId, { column: group.column, value: group.value, noMatch: group.noMatch, q: query, offset: 0, limit })
      : api.evidenceView(runId, { offset: 0, limit, changedOnly: true, order: 'impact', category: category || undefined, q: query });
    load
      .then(res => { if (active) { setRows(res.items as Row[]); setTotal(res.total); } })
      .catch(e => { if (active) setProblem(e instanceof Error ? e.message : String(e)); })
      .finally(() => { if (active) setBusy(false); });
    return () => { active = false; };
  }, [runId, category, limit, group, query]);

  const allCount = categories.reduce((n, c) => n + c.records, 0);
  // A running total only adds up over the full, unsearched list.
  const running = additive && !query ? runningTotals(rows.map(r => r.delta ?? '0')) : [];
  const showRunning = additive && !query;
  const leaves = (r: Row) => (r.baseline_included && !r.target_included) || r.target_present === false;

  return (
    <section className="ledger" aria-labelledby="exhibit-a">
      <div className="ledger-head">
        <h3 id="exhibit-a"><span className="exhibit">A</span> {labels.loads ? 'Records that change the total' : 'Records that explain the difference'}</h3>
        <label className="ledger-search">
          <span className="sr-only">Find a record</span>
          <input type="search" placeholder={`Find ${keyHeader}…`} value={search} onChange={e => setSearch(e.target.value)} maxLength={256} />
        </label>
        {group && (
          <p className="ledger-group" role="status">
            Only <b>{group.columnLabel} = {group.label}</b> · <button type="button" className="link-button" onClick={props.onClearGroup}>Show all records</button>
          </p>
        )}
        {!group && <div className="ledger-tabs" role="tablist" aria-label="Kind of change">
          <button type="button" role="tab" aria-selected={category === ''} onClick={() => onCategory('')}>
            All changes <span className="n">{groupDigits(String(allCount))}</span>
          </button>
          {categories.map(c => (
            <button key={c.key} type="button" role="tab" aria-selected={category === c.key} onClick={() => onCategory(c.key)}>
              {categoryLabel(c.key, labels)} <span className="n">{groupDigits(String(c.records))}</span>
            </button>
          ))}
        </div>}
      </div>

      {problem && <p className="disabled-reason">Records could not be loaded: {problem}</p>}
      <table className="ledger-table" aria-busy={busy}>
        <thead>
          <tr><th scope="col">{keyHeader}</th><th scope="col">{labels.loads ? 'Earlier load → Later load' : 'Current → Proposed'}</th><th scope="col" className="num">Change</th>
            {showRunning && <th scope="col" className="num">Running total</th>}</tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={keyOf(r)}>
              <td className="key" data-label={keyHeader}>{keyOf(r)}</td>
              <td className="move" data-label={labels.loads ? 'Earlier load → Later load' : 'Current → Proposed'}>
                <span className={`was${leaves(r) ? ' left' : ''}`}>{side(r, 'baseline')}</span>
                <span className="arrow" aria-label="becomes">→</span>
                <span className="now">{side(r, 'target')}</span>
              </td>
              <td className="num change" data-label="Change">{change(r)}</td>
              {showRunning && <td className="num running" data-label="Running total">{minus(groupDigits(running[i]))}</td>}
            </tr>
          ))}
          {busy && rows.length === 0 && <tr><td colSpan={showRunning ? 4 : 3} role="status">Loading records…</td></tr>}
          {!busy && rows.length === 0 && !problem && <tr><td colSpan={showRunning ? 4 : 3}>{query ? `No changed record matches “${query}”.` : 'No records in this group.'}</td></tr>}
        </tbody>
        {!query && <tfoot>
          <tr className="ledger-total">
            <th scope="row" colSpan={2}>{group ? `Total · ${group.columnLabel} = ${group.label}` : category ? `Total · ${categoryLabel(category, labels)}` : 'Total of listed changes'}</th>
            <td className="num"><span className="double-rule">{group ? group.totalText : props.totalText}</span></td>
            {showRunning && <td />}
          </tr>
        </tfoot>}
      </table>

      <div className="ledger-foot">
        {total > rows.length && (
          <p>
            Showing the {groupDigits(String(rows.length))} largest of {groupDigits(String(total))}.{' '}
            {limit < 100
              ? <button type="button" className="link-button" onClick={() => setLimit(l => Math.min(100, l * 2 + 5))}>Show more</button>
              : <button type="button" className="link-button" onClick={props.onShowRegister}>Open the full register</button>}
          </p>
        )}
        {!group && !query && <p className={`ledger-balance${props.balanced ? ' ok' : ''}`}>{props.balanceText}</p>}
        {!group && !query && props.unchanged != null && props.unchanged > 0 && (
          <p className="ledger-unchanged">
            ✓ {groupDigits(String(props.unchanged))} {props.unchangedNoun}{labels.loads ? ' do not change the total' : ' unchanged'} · <button type="button" className="link-button" onClick={props.onShowRegister}>Show them</button>
          </p>
        )}
      </div>
    </section>
  );
}
