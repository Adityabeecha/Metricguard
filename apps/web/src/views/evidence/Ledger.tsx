import { useEffect, useState, type ReactNode } from 'react';
import { api, type ChangeCategory, type Evidence, type FlatEvidence, type RelationalEvidence } from '../../api';
import { groupDigits } from '../../resultSummary';
import { runningTotals } from '../../exactDecimal';
import { categoryLabel, type CategoryKey } from './categories';

type Row = Evidence & Partial<FlatEvidence> & Partial<RelationalEvidence>;
const keyOf = (row: Row) => row.fact_key ?? row.row_key ?? row.order_id ?? '—';
const minus = (text: string) => text.replace(/^-/, '−');

export type LedgerProps = {
  runId: string;
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
  const { runId, categories, category, onCategory, labels, keyHeader, side, change, additive } = props;
  const [limit, setLimit] = useState(10);
  const [rows, setRows] = useState<Row[]>([]);
  const [total, setTotal] = useState(0);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState('');
  useEffect(() => { setLimit(10); }, [category]);
  useEffect(() => {
    let active = true;
    setBusy(true); setProblem('');
    api.evidenceView(runId, { offset: 0, limit, changedOnly: true, order: 'impact', category: category || undefined })
      .then(res => { if (active) { setRows(res.items as Row[]); setTotal(res.total); } })
      .catch(e => { if (active) setProblem(e instanceof Error ? e.message : String(e)); })
      .finally(() => { if (active) setBusy(false); });
    return () => { active = false; };
  }, [runId, category, limit]);

  const allCount = categories.reduce((n, c) => n + c.records, 0);
  const running = additive ? runningTotals(rows.map(r => r.delta ?? '0')) : [];
  const leaves = (r: Row) => (r.baseline_included && !r.target_included) || r.target_present === false;

  return (
    <section className="ledger" aria-labelledby="exhibit-a">
      <div className="ledger-head">
        <h3 id="exhibit-a"><span className="exhibit">A</span> Records that explain the difference</h3>
        <div className="ledger-tabs" role="tablist" aria-label="Kind of change">
          <button type="button" role="tab" aria-selected={category === ''} onClick={() => onCategory('')}>
            All changes <span className="n">{groupDigits(String(allCount))}</span>
          </button>
          {categories.map(c => (
            <button key={c.key} type="button" role="tab" aria-selected={category === c.key} onClick={() => onCategory(c.key)}>
              {categoryLabel(c.key, labels)} <span className="n">{groupDigits(String(c.records))}</span>
            </button>
          ))}
        </div>
      </div>

      {problem && <p className="disabled-reason">Records could not be loaded: {problem}</p>}
      <table className="ledger-table" aria-busy={busy}>
        <thead>
          <tr><th scope="col">{keyHeader}</th><th scope="col">Current → Proposed</th><th scope="col" className="num">Change</th>
            {additive && <th scope="col" className="num">Running total</th>}</tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={keyOf(r)}>
              <td className="key" data-label={keyHeader}>{keyOf(r)}</td>
              <td className="move" data-label="Current → Proposed">
                <span className={`was${leaves(r) ? ' left' : ''}`}>{side(r, 'baseline')}</span>
                <span className="arrow" aria-label="becomes">→</span>
                <span className="now">{side(r, 'target')}</span>
              </td>
              <td className="num change" data-label="Change">{change(r)}</td>
              {additive && <td className="num running" data-label="Running total">{minus(groupDigits(running[i]))}</td>}
            </tr>
          ))}
          {busy && rows.length === 0 && <tr><td colSpan={additive ? 4 : 3} role="status">Loading records…</td></tr>}
          {!busy && rows.length === 0 && !problem && <tr><td colSpan={additive ? 4 : 3}>No records in this group.</td></tr>}
        </tbody>
        <tfoot>
          <tr className="ledger-total">
            <th scope="row" colSpan={2}>{category ? `Total · ${categoryLabel(category, labels)}` : 'Total of listed changes'}</th>
            <td className="num"><span className="double-rule">{props.totalText}</span></td>
            {additive && <td />}
          </tr>
        </tfoot>
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
        <p className={`ledger-balance${props.balanced ? ' ok' : ''}`}>{props.balanceText}</p>
        {props.unchanged != null && props.unchanged > 0 && (
          <p className="ledger-unchanged">
            ✓ {groupDigits(String(props.unchanged))} {props.unchangedNoun} unchanged · <button type="button" className="link-button" onClick={props.onShowRegister}>Show them</button>
          </p>
        )}
      </div>
    </section>
  );
}
