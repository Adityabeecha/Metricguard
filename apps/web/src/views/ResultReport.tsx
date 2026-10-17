import { useEffect, useMemo, useState } from 'react';
import { api, type Evidence, type EvidenceSummary, type FlatEvidence, type RelationalEvidence, type RelationalRun, type Result, type Run, type Snapshot, type Version } from '../api';
import { groupDigits, summarizeResult, type RecordDelta } from '../resultSummary';
import { RelationalEvidencePanel } from './RelationalEvidencePanel';

const PAGE = 100;

type AnyEvidence = Evidence & Partial<FlatEvidence> & Partial<RelationalEvidence>;
const recordKey = (row: AnyEvidence) => row.fact_key ?? row.row_key ?? row.order_id ?? '—';

/** Server-computed change summary (exact counts across every record) for one run. */
export function useEvidenceSummary(run: Run | RelationalRun | null) {
  const [summary, setSummary] = useState<EvidenceSummary | null>(null);
  const [problem, setProblem] = useState('');
  const runId = run?.result ? run.id : '';
  useEffect(() => {
    if (!runId) { setSummary(null); return; }
    let active = true;
    setProblem('');
    api.evidenceSummary(runId)
      .then(s => { if (active) setSummary(s); })
      .catch(e => { if (active) setProblem(e instanceof Error ? e.message : String(e)); });
    return () => { active = false; };
  }, [runId]);
  return { summary, problem };
}

/** One page of a filtered/sorted record view, fetched from the server. */
function useRecordPage(runId: string, view: { offset: number; changedOnly: boolean; q: string; order: 'file' | 'impact' }) {
  const [rows, setRows] = useState<AnyEvidence[]>([]);
  const [total, setTotal] = useState(0);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState('');
  useEffect(() => {
    if (!runId) return;
    let active = true;
    setBusy(true); setProblem('');
    const timer = setTimeout(() => {
      api.evidenceView(runId, { ...view, limit: PAGE })
        .then(res => { if (active) { setRows(res.items as AnyEvidence[]); setTotal(res.total); } })
        .catch(e => { if (active) setProblem(e instanceof Error ? e.message : String(e)); })
        .finally(() => { if (active) setBusy(false); });
    }, view.q ? 250 : 0);
    return () => { active = false; clearTimeout(timer); };
  }, [runId, view.offset, view.changedOnly, view.q, view.order]);
  return { rows, total, busy, problem };
}

type Props = {
  run: Run | RelationalRun;
  unit: string;
  relational: boolean;
  baselineLabel: string;
  targetLabel: string;
  baselineSql: string;
  targetSql: string;
  baselineDescription?: string;
  targetDescription?: string;
  snapshot?: Snapshot | null;
  versions?: Version[];
};

function signedClass(value: string | null | undefined) {
  if (!value || !/[1-9]/.test(value)) return '';
  return value.startsWith('-') ? 'record-delta neg' : 'record-delta pos';
}

function inclusion(row: AnyEvidence, side: 'baseline' | 'target') {
  const included = side === 'baseline' ? row.baseline_included : row.target_included;
  const nullTerm = side === 'baseline' ? row.baseline_null_term : row.target_null_term;
  if (!included) return 'Excluded';
  return nullTerm ? 'Included · blank amount (ignored)' : 'Included';
}

export function ResultReport({ run, unit, relational, baselineLabel, targetLabel, baselineSql, targetSql, baselineDescription, targetDescription, snapshot, versions }: Props) {
  const result = run.result as Result;
  const evidence = useEvidenceSummary(run);
  const top = (evidence.summary?.top_changes || []) as AnyEvidence[];
  const total = evidence.summary?.total ?? 0;
  const summary = useMemo(() => summarizeResult(result, unit, evidence.summary ? {
    rows: top.map<RecordDelta>(r => ({ key: recordKey(r), delta: r.delta })),
    total: evidence.summary.total,
    complete: true,
    changedCount: evidence.summary.changed,
  } : undefined), [result, unit, evidence.summary]);

  const [offset, setOffset] = useState(0);
  const [changedOnly, setChangedOnly] = useState(false);
  const [q, setQ] = useState('');
  const [order, setOrder] = useState<'file' | 'impact'>('file');
  const [jump, setJump] = useState('');
  const [csvProblem, setCsvProblem] = useState('');
  const records = useRecordPage(run.result ? run.id : '', { offset, changedOnly, q, order });
  const pages = Math.max(1, Math.ceil(records.total / PAGE));
  const pageNo = Math.floor(offset / PAGE) + 1;
  const goPage = (n: number) => setOffset((Math.min(Math.max(1, n), pages) - 1) * PAGE);
  const show = (v: string | null) => (v == null ? 'Undefined (no values)' : unit ? `${groupDigits(v)} ${unit}` : groupDigits(v));
  const num = (v: string | null | undefined) => (v == null ? '—' : groupDigits(v));
  const downloadCsv = () => {
    setCsvProblem('');
    api.download(`/comparisons/${encodeURIComponent(run.id)}/evidence.csv`, `metricguard-${run.id}-records.csv`)
      .catch(e => setCsvProblem(e instanceof Error ? e.message : String(e)));
  };

  return (
    <div className="result-summary">
      <p className="result-headline" aria-live="polite">{summary.headline}</p>
      {summary.recordsSentence && <p style={{ margin: 0 }}>{summary.recordsSentence}</p>}
      {evidence.problem && <p className="disabled-reason">The record summary could not be loaded: {evidence.problem}</p>}

      <div className="calc-pair">
        <div>
          <h3>Current calculation · {baselineLabel}</h3>
          <p>{baselineDescription || <code className="mono-cell">{baselineSql}</code>}</p>
        </div>
        <div>
          <h3>Proposed calculation · {targetLabel}</h3>
          <p>{targetDescription || <code className="mono-cell">{targetSql}</code>}</p>
        </div>
      </div>

      <div className="result-figures">
        <div className="result-figure"><span>Current total</span><strong>{show(result.baseline_value)}</strong></div>
        <div className="result-figure"><span>Proposed total</span><strong>{show(result.target_value)}</strong></div>
        <div className="result-figure change">
          <span>Change (proposed − current)</span>
          <strong className={summary.direction === 'down' ? 'down' : summary.direction === 'up' ? 'up' : ''}>
            {result.delta_target_minus_baseline == null ? 'Not defined' : show(result.delta_target_minus_baseline)}
          </strong>
        </div>
        <div className="result-figure">
          <span>Unexplained difference</span>
          <strong>{result.residual == null ? 'Not applicable' : show(result.residual)}</strong>
          <small>Part of the change not traced to a record</small>
        </div>
      </div>

      {top.length > 0 && (
        <div>
          <h3 style={{ fontSize: 14, margin: '0 0 6px' }}>Records that explain the difference{top.length > 1 ? ' (largest first)' : ''}</h3>
          <div className="table-scroll">
            <table>
              <thead><tr><th>Record</th><th>Current</th><th>Proposed</th><th>Change</th></tr></thead>
              <tbody>
                {top.slice(0, 10).map(r => (
                  <tr key={recordKey(r)}>
                    <td className="mono-cell">{recordKey(r)}</td>
                    <td>{inclusion(r, 'baseline')}{r.baseline_contribution != null && ` · ${num(r.baseline_contribution)}`}</td>
                    <td>{inclusion(r, 'target')}{r.target_contribution != null && ` · ${num(r.target_contribution)}`}</td>
                    <td className={signedClass(r.delta)}>{num(r.delta)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {(evidence.summary?.changed ?? 0) > 10 && <small>Showing the 10 largest of {groupDigits(String(evidence.summary!.changed))} changed records. Use “Supporting records” to see them all.</small>}
        </div>
      )}

      {summary.uncertainties.length > 0 && (
        <div>
          <h3 style={{ fontSize: 14, margin: '0 0 6px' }}>What remains uncertain</h3>
          <ul className="uncertainty-list">{summary.uncertainties.map(u => <li key={u}>{u}</li>)}</ul>
        </div>
      )}

      <div className="detail-stack">
        <details>
          <summary>Supporting records ({groupDigits(String(total))})</summary>
          <div>
            <p>Every record in the dataset, including those excluded by both calculations. Change = proposed contribution − current contribution.</p>
            <div className="library-toolbar" role="group" aria-label="Filter records">
              <label>Find a record
                <input type="search" value={q} placeholder="Record key" onChange={e => { setQ(e.target.value); setOffset(0); }} />
              </label>
              <label>Show
                <select value={changedOnly ? 'changed' : 'all'} onChange={e => { setChangedOnly(e.target.value === 'changed'); setOffset(0); }}>
                  <option value="all">All records</option>
                  <option value="changed">Only records that changed</option>
                </select>
              </label>
              <label>Order
                <select value={order} onChange={e => { setOrder(e.target.value as 'file' | 'impact'); setOffset(0); }}>
                  <option value="file">Dataset order</option>
                  <option value="impact">Largest change first</option>
                </select>
              </label>
              <button type="button" className="button secondary small" onClick={downloadCsv}>Download all records (CSV)</button>
            </div>
            {csvProblem && <p className="disabled-reason">The download failed: {csvProblem}</p>}
            {records.problem && <p className="disabled-reason">Records could not be loaded: {records.problem}</p>}
            <div className="table-scroll" aria-busy={records.busy}>
              <table>
                <thead><tr><th>Record</th><th>Current</th><th>Proposed</th><th>Change</th></tr></thead>
                <tbody>
                  {records.rows.map(r => (
                    <tr key={recordKey(r)}>
                      <td className="mono-cell">{recordKey(r)}</td>
                      <td>{inclusion(r, 'baseline')}{r.baseline_contribution != null && ` · ${num(r.baseline_contribution)}`}</td>
                      <td>{inclusion(r, 'target')}{r.target_contribution != null && ` · ${num(r.target_contribution)}`}</td>
                      <td className={signedClass(r.delta)}>{num(r.delta)}</td>
                    </tr>
                  ))}
                  {!records.busy && records.rows.length === 0 && <tr><td colSpan={4}>No records match.</td></tr>}
                </tbody>
              </table>
            </div>
            <div className="report-actions" role="group" aria-label="Record pages">
              <button type="button" className="button secondary small" disabled={records.busy || pageNo <= 1} onClick={() => goPage(pageNo - 1)}>Previous</button>
              <span role="status">
                {records.busy ? 'Loading…' : records.total === 0 ? 'No records' : `Records ${groupDigits(String(offset + 1))}–${groupDigits(String(Math.min(offset + PAGE, records.total)))} of ${groupDigits(String(records.total))}`}
              </span>
              <button type="button" className="button secondary small" disabled={records.busy || pageNo >= pages} onClick={() => goPage(pageNo + 1)}>Next</button>
              {pages > 2 && (
                <form onSubmit={e => { e.preventDefault(); const n = Number(jump); if (Number.isFinite(n)) goPage(n); setJump(''); }} style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
                  <label style={{ display: 'flex', gap: 6, alignItems: 'center', fontSize: 13 }}>Page
                    <input type="number" min={1} max={pages} value={jump} onChange={e => setJump(e.target.value)} style={{ width: 80, padding: '4px 6px' }} placeholder={`1–${pages}`} />
                  </label>
                  <button className="button secondary small" disabled={!jump}>Go</button>
                </form>
              )}
            </div>
          </div>
        </details>

        {relational && (
          <details>
            <summary>Join behavior</summary>
            <div>
              {snapshot && 'join_proof' in snapshot && (snapshot as unknown as { join_proof: Record<string, number | boolean> }).join_proof && (() => {
                const proof = (snapshot as unknown as { join_proof: Record<string, number> }).join_proof;
                return <p>{proof.matched_fact_rows} records match a dimension row · {proof.unmatched_fact_rows} do not ({proof.null_foreign_key_rows} have a blank link) · {proof.fanout_rows} duplicated by the join.</p>;
              })()}
              <p>A LEFT JOIN keeps records without a matching dimension row; an INNER JOIN drops them before any filter is applied.</p>
              <RelationalEvidencePanel key={run.id} run={run as RelationalRun} versions={versions || []} />
            </div>
          </details>
        )}

        <details>
          <summary>Missing-value handling</summary>
          <div>
            <p>Blank (NULL) amounts are ignored when adding up; they are not treated as zero. A filter compared with a blank value is neither true nor false, so the record is left out.</p>
            <p>{!evidence.summary ? '' : evidence.summary.blank_amount_rows === 0 ? 'No record has a blank amount.' : `${groupDigits(String(evidence.summary.blank_amount_rows))} record(s) have a blank amount in at least one calculation.`}</p>
            <p>If a calculation has no non-blank amounts at all, its total is undefined rather than 0, and no change is reported.</p>
          </div>
        </details>

        <details>
          <summary>Reconciliation</summary>
          <div>
            <p>Sum of record changes plus the unexplained difference equals the total change.</p>
            {result.steps?.length > 0 && (
              <div className="table-scroll">
                <table>
                  <thead><tr><th>#</th><th>Rule changed</th><th>Before</th><th>After</th><th>Contribution</th></tr></thead>
                  <tbody>{result.steps.map(s => (
                    <tr key={s.position}><td>{s.position}</td><td>{s.rule.replaceAll('_', ' ')}</td><td>{s.previous_value ?? '—'}</td><td>{s.next_value ?? '—'}</td><td className={signedClass(s.contribution)}>{s.contribution ?? '—'}</td></tr>
                  ))}</tbody>
                </table>
              </div>
            )}
            <p>Status: {result.explanation_status?.replaceAll('_', ' ') || 'not reported'} · definitions {result.definition_status?.replaceAll('_', ' ')}.</p>
          </div>
        </details>

        <details>
          <summary>Original SQL</summary>
          <div>
            <strong>Current calculation</strong>
            <pre className="sql-preview">{baselineSql || 'Not available'}</pre>
            <strong>Proposed calculation</strong>
            <pre className="sql-preview">{targetSql || 'Not available'}</pre>
          </div>
        </details>

        <details>
          <summary>Data and calculation history</summary>
          <div>
            {snapshot && <p>Dataset “{snapshot.name}” · {groupDigits(String(snapshot.row_count))} rows · frozen {snapshot.created_at ? new Date(snapshot.created_at).toLocaleString() : ''}</p>}
            <p>Run {run.id} · {run.state} · started {new Date(run.created_at).toLocaleString()}</p>
            <pre className="json-view">{JSON.stringify(result.provenance, null, 2)}</pre>
          </div>
        </details>
      </div>
    </div>
  );
}
