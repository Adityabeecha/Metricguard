import { useEffect, useMemo, useState } from 'react';
import { api, type Evidence, type FlatEvidence, type RelationalEvidence, type RelationalRun, type Result, type Run, type Snapshot, type Version } from '../api';
import { summarizeResult, type RecordDelta } from '../resultSummary';
import { RelationalEvidencePanel } from './RelationalEvidencePanel';

const PAGE = 100;
const MAX_PAGES = 10;

type AnyEvidence = Evidence & Partial<FlatEvidence> & Partial<RelationalEvidence>;
const recordKey = (row: AnyEvidence) => row.fact_key ?? row.row_key ?? row.order_id ?? '—';

/** Reads row evidence (up to 1,000 rows) so the summary can count the records behind a change. */
export function useRecordEvidence(run: Run | RelationalRun | null) {
  const [rows, setRows] = useState<AnyEvidence[]>([]);
  const [total, setTotal] = useState(0);
  const [complete, setComplete] = useState(false);
  const [problem, setProblem] = useState('');
  const runId = run?.result ? run.id : '';

  useEffect(() => {
    if (!runId) { setRows([]); setTotal(0); setComplete(false); return; }
    let active = true;
    setProblem('');
    (async () => {
      const collected: AnyEvidence[] = [];
      let seen = 0, count = 0;
      for (let page = 0; page < MAX_PAGES; page++) {
        const res = await api.flatEvidence(runId, page * PAGE, PAGE);
        if (!active) return;
        count = res.total;
        collected.push(...(res.items as AnyEvidence[]));
        seen += res.items.length;
        // Each page read re-verifies the whole artifact, so large datasets load one
        // page here; the rest is reached through the paged table below.
        if (seen >= res.total || res.items.length === 0 || res.total > PAGE * MAX_PAGES) break;
      }
      setRows(collected); setTotal(count); setComplete(seen >= count);
    })().catch(e => { if (active) setProblem(e instanceof Error ? e.message : String(e)); });
    return () => { active = false; };
  }, [runId]);

  return { rows, total, complete, problem };
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
  const evidence = useRecordEvidence(run);
  const summary = useMemo(() => summarizeResult(result, unit, evidence.total || evidence.rows.length ? {
    rows: evidence.rows.map<RecordDelta>(r => ({ key: recordKey(r), delta: r.delta })),
    total: evidence.total,
    complete: evidence.complete,
  } : undefined), [result, unit, evidence.rows, evidence.total, evidence.complete]);

  const changed = evidence.rows.filter(r => r.delta != null && /[1-9]/.test(r.delta));
  const total = evidence.total || evidence.rows.length;
  const [pageStart, setPageStart] = useState(0);
  const [pageRows, setPageRows] = useState<AnyEvidence[] | null>(null);
  const [pageBusy, setPageBusy] = useState(false);
  const [pageProblem, setPageProblem] = useState('');
  const loadedPage = pageStart + PAGE <= evidence.rows.length || (evidence.complete && pageStart < evidence.rows.length);
  const visibleRows = loadedPage ? evidence.rows.slice(pageStart, pageStart + PAGE) : pageRows || [];
  const goTo = async (start: number) => {
    setPageStart(start); setPageProblem('');
    if (start + PAGE <= evidence.rows.length || (evidence.complete && start < evidence.rows.length)) { setPageRows(null); return; }
    setPageBusy(true);
    try { setPageRows((await api.flatEvidence(run.id, start, PAGE)).items as AnyEvidence[]); }
    catch (e) { setPageProblem(e instanceof Error ? e.message : String(e)); }
    finally { setPageBusy(false); }
  };
  const nullRows = evidence.rows.filter(r => r.baseline_null_term || r.target_null_term);
  const show = (v: string | null) => (v == null ? 'Undefined (no values)' : unit ? `${v} ${unit}` : v);

  return (
    <div className="result-summary">
      <p className="result-headline" aria-live="polite">{summary.headline}</p>
      {summary.recordsSentence && <p style={{ margin: 0 }}>{summary.recordsSentence}</p>}
      {evidence.problem && <p className="disabled-reason">Supporting records could not be loaded: {evidence.problem}</p>}

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

      {changed.length > 0 && (
        <div>
          <h3 style={{ fontSize: 14, margin: '0 0 6px' }}>Records that explain the difference</h3>
          <div className="table-scroll">
            <table>
              <thead><tr><th>Record</th><th>Current</th><th>Proposed</th><th>Change</th></tr></thead>
              <tbody>
                {changed.slice(0, 10).map(r => (
                  <tr key={recordKey(r)}>
                    <td className="mono-cell">{recordKey(r)}</td>
                    <td>{inclusion(r, 'baseline')}{r.baseline_contribution != null && ` · ${r.baseline_contribution}`}</td>
                    <td>{inclusion(r, 'target')}{r.target_contribution != null && ` · ${r.target_contribution}`}</td>
                    <td className={signedClass(r.delta)}>{r.delta}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {changed.length > 10 && <small>Showing 10 of {changed.length}{evidence.complete ? '' : ' found so far'}. All records are under “Supporting records”.</small>}
          {!evidence.complete && <small> These are drawn from the first {evidence.rows.length.toLocaleString()} of {total.toLocaleString()} records.</small>}
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
          <summary>Supporting records ({total.toLocaleString()})</summary>
          <div>
            <p>Every record in the dataset, including those excluded by both calculations. Change = proposed contribution − current contribution.</p>
            <div className="table-scroll">
              <table>
                <thead><tr><th>Record</th><th>Current</th><th>Proposed</th><th>Change</th></tr></thead>
                <tbody>
                  {visibleRows.map(r => (
                    <tr key={recordKey(r)}>
                      <td className="mono-cell">{recordKey(r)}</td>
                      <td>{inclusion(r, 'baseline')}{r.baseline_contribution != null && ` · ${r.baseline_contribution}`}</td>
                      <td>{inclusion(r, 'target')}{r.target_contribution != null && ` · ${r.target_contribution}`}</td>
                      <td className={signedClass(r.delta)}>{r.delta ?? '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {total > PAGE && (
              <div className="report-actions" role="group" aria-label="Record pages">
                <button type="button" className="button secondary small" disabled={pageBusy || pageStart === 0} onClick={() => void goTo(Math.max(0, pageStart - PAGE))}>Previous</button>
                <span role="status">
                  {pageBusy ? 'Loading…' : `Records ${(pageStart + 1).toLocaleString()}–${Math.min(pageStart + PAGE, total).toLocaleString()} of ${total.toLocaleString()}`}
                </span>
                <button type="button" className="button secondary small" disabled={pageBusy || pageStart + PAGE >= total} onClick={() => void goTo(pageStart + PAGE)}>Next</button>
              </div>
            )}
            {pageProblem && <p className="disabled-reason">This page could not be loaded: {pageProblem}</p>}
            {total > PAGE && <p>Each page is checked against the stored evidence hash before it is shown, so large datasets take a moment per page. Downloaded reports include the first 100 records.</p>}
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
            <p>{nullRows.length === 0 ? 'No record in the loaded evidence has a blank amount.' : `${nullRows.length} record(s) have a blank amount in at least one calculation: ${nullRows.slice(0, 8).map(recordKey).join(', ')}${nullRows.length > 8 ? '…' : ''}.`}</p>
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
            {snapshot && <p>Dataset “{snapshot.name}” · {snapshot.row_count} rows · frozen {snapshot.created_at ? new Date(snapshot.created_at).toLocaleString() : ''}</p>}
            <p>Run {run.id} · {run.state} · started {new Date(run.created_at).toLocaleString()}</p>
            <pre className="json-view">{JSON.stringify(result.provenance, null, 2)}</pre>
          </div>
        </details>
      </div>
    </div>
  );
}
