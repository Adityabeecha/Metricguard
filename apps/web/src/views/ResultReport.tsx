import { useEffect, useMemo, useState } from 'react';
import { api, type DistinctSummary, type EntityRecords, type EvidenceBreakdown, type Evidence, type EvidenceSummary, type FlatEvidence, type RatioBlock, type RelationalEvidence, type RelationalRun, type Result, type Run, type Snapshot, type Version } from '../api';
import { resultUnit, fractionDisplay, groupDigits, percentOf, summarizeResult, type RecordDelta } from '../resultSummary';
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
    setProblem(''); setSummary(null);
    api.evidenceSummary(runId)
      .then(s => { if (active) setSummary(s); })
      .catch(e => { if (active) setProblem(e instanceof Error ? e.message : String(e)); });
    return () => { active = false; };
  }, [runId]);
  return { summary, problem };
}

/** One page of a filtered/sorted record view, fetched from the server. */
function useRecordPage(runId: string, view: { offset: number; changedOnly: boolean; q: string; order: 'file' | 'impact'; withContext: boolean }) {
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
  }, [runId, view.offset, view.changedOnly, view.q, view.order, view.withContext]);
  return { rows, total, busy, problem };
}

const columnLabel = (c: string) => c.startsWith('dimension.') ? `${c.slice(10)} (lookup)` : c.startsWith('fact.') ? c.slice(5) : c;
const showValue = (v: unknown) => v === null || v === undefined ? 'blank' : String(v);

const PART_LABEL = (part: RatioBlock['numerator'], amountUnit: string) => {
  const unit = part.unit_label || amountUnit;
  if (part.aggregate === 'sum') return `total ${part.minus ? `${part.measure} − ${part.minus}` : part.measure}${unit ? ` (${unit})` : ''}`;
  return part.measure === '*' ? 'number of records' : `records with a value in ${part.measure}`;
};

/** Top and bottom of a ratio, and the exact split of its change between them. */
function RatioPanel({ ratio, percent, amountUnit, outputUnit }: { ratio: RatioBlock; percent: boolean; amountUnit: string; outputUnit: string }) {
  const value = (v: string | null) => (v == null ? '—' : groupDigits(v));
  const effect = (v: string) => (percent ? `${groupDigits(percentOf(v))} pts` : `${groupDigits(v)}${outputUnit ? ` ${outputUnit}` : ''}`);
  const rows: Array<[string, RatioBlock['numerator'], 'numerator' | 'denominator', string | null]> = [
    ['Top', ratio.numerator, 'numerator', ratio.numerator_change],
    ['Bottom', ratio.denominator, 'denominator', ratio.denominator_change],
  ];
  return (
    <div>
      <h3 style={{ fontSize: 14, margin: '0 0 6px' }}>How the ratio is built</h3>
      <div className="table-scroll">
        <table>
          <thead><tr><th>Part</th><th>Current</th><th>Proposed</th><th>Change</th></tr></thead>
          <tbody>
            {rows.map(([label, part, side, change]) => (
              <tr key={label}>
                <td>{label}: {PART_LABEL(part, amountUnit)}</td>
                <td>{value(ratio.baseline[side])}</td>
                <td>{value(ratio.target[side])}</td>
                <td className={signedClass(change)}>{value(change)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {ratio.status === 'defined' ? (
        <>
          <p style={{ margin: '8px 0 4px' }}>
            The change splits exactly into a <b>top-part effect of {effect(ratio.effects[0].display)}</b> and a{' '}
            <b>bottom-part effect of {effect(ratio.effects[1].display)}</b>. Each averages the two orders of applying the changes, so nothing is left over.
          </p>
          <small style={{ display: 'block' }}>
            {ratio.pooled_average && !percent && <> A record added to both parts raises the ratio when its value is above the pooled average of {groupDigits(fractionDisplay(ratio.pooled_average, ratio.display_places))} and lowers it when below.</>}
          </small>
        </>
      ) : (
        <p className="example">The ratio is undefined on at least one side, so only the exact changes in its top and bottom parts are reported.</p>
      )}
    </div>
  );
}

/** Values counted only by one calculation, and records without a value. */
function DistinctPanel({ distinct }: { distinct: DistinctSummary }) {
  const name = distinct.entity_column;
  return (
    <div className="guided-explain">
      <b>{groupDigits(String(distinct.gained))}</b> {name} value{distinct.gained === 1 ? ' is' : 's are'} counted only by the proposed calculation,{' '}
      <b>{groupDigits(String(distinct.lost))}</b> only by the current one, and <b>{groupDigits(String(distinct.kept))}</b> by both.
      {(distinct.baseline_null_entity_rows > 0 || distinct.target_null_entity_rows > 0) && (
        <> Records with no {name} are never counted ({groupDigits(String(distinct.baseline_null_entity_rows))} in the current calculation, {groupDigits(String(distinct.target_null_entity_rows))} in the proposed one).</>
      )}
    </div>
  );
}

/** The records behind one counted value, loaded on demand. */
function EntityRecordsButton({ runId, entity }: { runId: string; entity: string }) {
  const [data, setData] = useState<EntityRecords | null>(null);
  const [problem, setProblem] = useState('');
  const [open, setOpen] = useState(false);
  const [offset, setOffset] = useState(0);
  const [busy, setBusy] = useState(false);
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    if (!open) return;
    let active = true;
    setBusy(true); setProblem('');
    api.entityRecords(runId, entity, offset).then(d => { if (active) setData(d); })
      .catch(e => { if (active) setProblem(e instanceof Error ? e.message : String(e)); })
      .finally(() => { if (active) setBusy(false); });
    return () => { active = false; };
  }, [runId, entity, open, offset, retry]);
  return (
    <div>
      <button type="button" className="button secondary small" aria-expanded={open} onClick={() => setOpen(o => !o)}>{open ? 'Hide records' : 'Show records'}</button>
      {open && (problem ? <div><small className="disabled-reason">{problem}</small><button type="button" className="button secondary small" onClick={() => setRetry(n => n + 1)}>Retry</button></div> : busy || !data ? <small role="status">Loading…</small> : (
        <div><ul style={{ margin: '4px 0', paddingLeft: 18, fontSize: 12 }}>
          {data.items.map(item => (
            <li key={item.record}><code>{item.record}</code> — current: {item.current_included ? 'included' : 'excluded'}, proposed: {item.proposed_included ? 'included' : 'excluded'}</li>
          ))}
        </ul>
          <small>{data.total ? `Records ${offset + 1}–${offset + data.items.length} of ${data.total}` : 'No supporting records'}</small>
          {data.total > 100 && <div className="report-actions">
            <button type="button" className="button secondary small" disabled={offset === 0} onClick={() => setOffset(n => Math.max(0, n - 100))}>Previous records</button>
            <button type="button" className="button secondary small" disabled={offset + data.items.length >= data.total} onClick={() => setOffset(n => n + 100)}>Next records</button>
          </div>}
        </div>
      ))}
    </div>
  );
}

/** Exact split of the change by one filter column, largest first. */
function Breakdown({ runId, unit, percent, onColumns, preferred = [] }: { runId: string; unit: string; percent?: boolean; onColumns: (c: string[]) => void; preferred?: string[] }) {
  const [column, setColumn] = useState('');
  const [data, setData] = useState<EvidenceBreakdown | null>(null);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState('');
  useEffect(() => {
    let active = true;
    setBusy(true); setProblem('');
    api.evidenceBreakdown(runId, column || undefined)
      .then(d => {
        if (!active) return;
        onColumns(d.columns || []);
        // Open on a column the calculations filter on: it usually explains the difference best.
        const tail = (c: string) => c.split('.').pop();
        const wanted = !column && preferred.map(p => (d.columns || []).find(c => c === p || tail(c) === tail(p))).find(Boolean);
        if (wanted && wanted !== d.column) { setColumn(wanted); return; }
        setData(d);
        if (!column && d.column) setColumn(d.column);
      })
      .catch(e => { if (active) setProblem(e instanceof Error ? e.message : String(e)); })
      .finally(() => { if (active) setBusy(false); });
    return () => { active = false; };
  }, [runId, column]);
  if (problem) return <p className="disabled-reason">The breakdown could not be loaded: {problem}</p>;
  if (!data) return busy ? <p role="status">Working out where the difference comes from…</p> : null;
  const unavailable = Object.entries(data.unavailable_columns || {});
  const unavailableNote = unavailable.length > 0 && (
    <small style={{ display: 'block' }}>Not available for an exact split: {unavailable.map(([c, why]) => `${columnLabel(c)} — ${why}`).join(' ')}</small>
  );
  if (!data.available) return <div><p className="example">{data.reason}</p>{unavailableNote}</div>;
  const total = Number(data.total_change || 0);
  const shown = (v: string | null | undefined) => v == null ? '—' : percent ? groupDigits(percentOf(v)) : groupDigits(v);
  const changeShown = (v: string | null | undefined) => v == null ? '—' : percent ? `${groupDigits(percentOf(v))} pts` : `${groupDigits(v)}${unit ? ` ${unit}` : ''}`;
  return (
    <div>
      <h3 style={{ fontSize: 14, margin: '0 0 6px' }}>Where the difference comes from</h3>
      <label style={{ display: 'inline-flex', gap: 8, alignItems: 'center', fontSize: 13, marginBottom: 6 }}>Split by
        <select value={data.column || ''} onChange={e => setColumn(e.target.value)}>
          {data.columns.map(c => <option key={c} value={c}>{columnLabel(c)}</option>)}
        </select>
      </label>
      <div className="table-scroll" aria-busy={busy}>
        <table>
          <thead><tr><th>{columnLabel(data.column || '')}</th><th>Records changed</th>
            <th>{data.ratio ? `Current ratio in group${percent ? ' (%)' : ''}` : 'Current'}</th><th>{data.ratio ? `Proposed ratio in group${percent ? ' (%)' : ''}` : 'Proposed'}</th>
            <th>{data.ratio ? 'Effect on the overall ratio' : 'Change'}</th><th>Share of change</th></tr></thead>
          <tbody>
            {(data.groups || []).map(g => {
              const share = total ? Number(g.change) / total : 0;
              return (
                <tr key={showValue(g.value)}>
                  <td>{showValue(g.value)}</td>
                  <td>{groupDigits(String(g.changed_records))} of {groupDigits(String(g.records))}</td>
                  <td>{data.ratio ? shown(g.current) : groupDigits(g.current ?? '—')}</td>
                  <td>{data.ratio ? shown(g.proposed) : groupDigits(g.proposed ?? '—')}</td>
                  <td className={signedClass(g.change)} title={g.change_exact ? `Exact: ${g.change_exact}` : undefined}>{data.ratio ? changeShown(g.change) : <>{groupDigits(g.change ?? '—')}{unit ? ` ${unit}` : ''}</>}</td>
                  <td>
                    <span className="share-bar" aria-hidden="true"><span style={{ width: `${Math.min(100, Math.abs(share) * 100)}%` }} /></span>
                    {' '}{total ? `${(share * 100).toFixed(1)}%` : '—'}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {(data.other_groups || 0) > 0 && <small>{data.other_groups} more values together account for {data.ratio ? changeShown(data.other_change) : <>{groupDigits(data.other_change || '0')}{unit ? ` ${unit}` : ''}</>}.</small>}
      <small style={{ display: 'block' }}>{data.ratio
        ? 'Each group’s effect is the exact sum of its records’ effects, and the groups add up exactly to the change in the overall ratio. Shown values are rounded; hover an effect for its exact fraction.'
        : 'Exact sums over every record. Shares are rounded for display; a value can move the total in either direction.'}</small>
      {data.coverage_note && <small style={{ display: 'block' }}>{data.coverage_note}</small>}
      {unavailableNote}
    </div>
  );
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
  /** Columns the calculations filter on; the breakdown opens on the first available one. */
  breakdownColumns?: string[];
};

function signedClass(value: string | null | undefined) {
  if (!value || !/[1-9]/.test(value)) return '';
  return value.startsWith('-') ? 'record-delta neg' : 'record-delta pos';
}

/** Two-data-load runs: how each record changed between the loads. */
export const CHANGE_LABEL: Record<string, string> = {
  added: 'New in the later load', removed: 'Gone from the later load', value_changed: 'Value changed',
  entered_filter: 'Now matches the filter', left_filter: 'No longer matches the filter', unchanged: 'Unchanged',
};

function inclusion(row: AnyEvidence, side: 'baseline' | 'target') {
  if ((side === 'baseline' ? row.baseline_present : row.target_present) === false) return 'Not in this load';
  const included = side === 'baseline' ? row.baseline_included : row.target_included;
  const nullTerm = side === 'baseline' ? row.baseline_null_term : row.target_null_term;
  if (!included) return 'Excluded';
  return nullTerm ? 'Included · blank amount (ignored)' : 'Included';
}

export function ResultReport({ run, unit: declaredUnit, relational, baselineLabel, targetLabel, baselineSql, targetSql, baselineDescription, targetDescription, snapshot, versions, breakdownColumns }: Props) {
  const result = run.result as Result;
  const kind = result.aggregate;
  const ratio = kind === 'ratio' ? result.ratio ?? null : null;
  const percent = ratio?.unit === 'share';
  // COUNT(*) counts records and COUNT(DISTINCT) counts values: the dataset's declared unit does not apply.
  const unit = resultUnit(result, declaredUnit);
  const noun = ratio ? 'value' : kind === 'count_distinct' || kind === 'count' ? 'count' : 'total';
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
  const [columns, setColumns] = useState<string[]>([]);
  const [shown, setShown] = useState<string[]>([]);
  const [csvProblem, setCsvProblem] = useState('');
  const records = useRecordPage(run.result ? run.id : '', { offset, changedOnly, q, order, withContext: shown.length > 0 });
  const pages = Math.max(1, Math.ceil(records.total / PAGE));
  const pageNo = Math.floor(offset / PAGE) + 1;
  const goPage = (n: number) => setOffset((Math.min(Math.max(1, n), pages) - 1) * PAGE);
  const show = (v: string | null) => (v == null ? (ratio ? 'Undefined' : 'Undefined (no values)')
    : percent ? `${groupDigits(percentOf(v))}%` : unit ? `${groupDigits(v)} ${unit}` : groupDigits(v));
  const showChange = (v: string | null) => (v == null ? 'Not defined' : percent ? `${groupDigits(percentOf(v))} pts` : show(v));
  const num = (v: string | null | undefined) => (v == null ? '—' : groupDigits(v));
  const keyHeader = kind === 'count_distinct' ? (result.distinct?.entity_column ?? 'Value') : 'Record';
  const changeOf = (r: AnyEvidence) => r.delta;
  const changeText = (r: AnyEvidence) => {
    if (!ratio && r.change) return <>{num(r.delta)} <small>· {CHANGE_LABEL[r.change] || r.change}</small></>;
    if (!ratio) return num(r.delta);
    if (r.effect == null) return '—';
    return <>{percent ? `${groupDigits(percentOf(r.effect))} pts` : `${groupDigits(r.effect)}${unit ? ` ${unit}` : ''}`}
      </>;
  };
  const sideCell = (r: AnyEvidence, side: 'baseline' | 'target') => {
    if (kind === 'count_distinct') {
      const n = side === 'baseline' ? r.baseline_supporting_rows : r.target_supporting_rows;
      return n ? `Counted · ${groupDigits(String(n))} matching record${n === 1 ? '' : 's'}` : 'Not counted · no matching record';
    }
    if (ratio) {
      const top = side === 'baseline' ? r.baseline_numerator : r.target_numerator;
      const bottom = side === 'baseline' ? r.baseline_denominator : r.target_denominator;
      const included = side === 'baseline' ? r.baseline_included : r.target_included;
      if (!included) return 'Excluded';
      const blank = (side === 'baseline' ? r.baseline_null_term : r.target_null_term) ? ' · blank amount' : '';
      return `Included · top ${num(top)} · bottom ${num(bottom)}${blank}`;
    }
    const contribution = side === 'baseline' ? r.baseline_contribution : r.target_contribution;
    return <>{inclusion(r, side)}{contribution != null && ` · ${num(contribution)}`}</>;
  };
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

      <div className="result-figures">
        <div className="result-figure"><span>Current {noun}</span><strong>{show(result.baseline_value)}</strong></div>
        <div className="result-figure"><span>Proposed {noun}</span><strong>{show(result.target_value)}</strong></div>
        <div className="result-figure change">
          <span>Change (proposed − current)</span>
          <strong className={summary.direction === 'down' ? 'down' : summary.direction === 'up' ? 'up' : ''}>
            {showChange(result.delta_target_minus_baseline)}
          </strong>
        </div>
        <div className="result-figure">
          <span>Unexplained difference</span>
          <strong>{result.residual == null ? 'Not applicable' : ratio ? '0 (exact)' : show(result.residual)}</strong>
          <small>Part of the change not traced to {kind === 'count_distinct' ? 'a counted value' : 'a record'}</small>
        </div>
      </div>
      <div className="calc-pair">
        <div>
          <h3>Current calculation{baselineLabel && ` · ${baselineLabel}`}</h3>
          <p>{baselineDescription || <code className="mono-cell">{baselineSql}</code>}</p>
        </div>
        <div>
          <h3>Proposed calculation{targetLabel && ` · ${targetLabel}`}</h3>
          <p>{targetDescription || <code className="mono-cell">{targetSql}</code>}</p>
        </div>
      </div>


      {ratio && (
        <details className="exact-values">
          <summary>Exact values (shown rounded to {ratio.display_places} decimal places)</summary>
          <p>Exact fractions decide equality and reconciliation{percent ? '; multiply by 100 for percentages and points' : ''}.</p>
          <ul>
            <li>Current: <code>{ratio.baseline.exact ?? 'undefined'}</code></li>
            <li>Proposed: <code>{ratio.target.exact ?? 'undefined'}</code></li>
            <li>Change: <code>{ratio.gap_exact ?? 'not defined'}</code></li>
            {ratio.status === 'defined' && <li>Top-part effect: <code>{ratio.effects[0].exact}</code> · bottom-part effect: <code>{ratio.effects[1].exact}</code></li>}
          </ul>
        </details>
      )}
      {ratio && <RatioPanel ratio={ratio} percent={percent} amountUnit={declaredUnit} outputUnit={unit} />}
      {kind === 'count_distinct' && result.distinct && <DistinctPanel distinct={result.distinct} />}

      {run.result && <Breakdown runId={run.id} unit={unit} percent={percent} onColumns={setColumns} preferred={breakdownColumns} />}

      {top.length > 0 && (
        <div>
          <h3 style={{ fontSize: 14, margin: '0 0 6px' }}>{kind === 'count_distinct' ? `${keyHeader} values` : 'Records'} that explain the difference{top.length > 1 ? ' (largest first)' : ''}</h3>
          <div className="table-scroll">
            <table>
              <thead><tr><th>{keyHeader}</th><th>Current</th><th>Proposed</th><th>{ratio ? 'Effect on the ratio' : 'Change'}</th></tr></thead>
              <tbody>
                {top.slice(0, 10).map(r => (
                  <tr key={recordKey(r)}>
                    <td className="mono-cell">{recordKey(r)}</td>
                    <td>{sideCell(r, 'baseline')}</td>
                    <td>{sideCell(r, 'target')}</td>
                    <td className={signedClass(changeOf(r))} title={r.effect_exact ? `Exact: ${r.effect_exact}${percent ? ' (×100 for points)' : ''}` : undefined}>{changeText(r)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {(evidence.summary?.changed ?? 0) > 10 && <small>Showing the 10 largest of {groupDigits(String(evidence.summary!.changed))} changed {kind === 'count_distinct' ? `${keyHeader} values` : 'records'}. Use “Supporting records” to see them all.</small>}
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
            {result.evidence_coverage
              ? <p>This dataset has {groupDigits(String(result.evidence_coverage.total_records))} records. Listed here are the {groupDigits(String(result.evidence_coverage.listed_records))} whose inclusion changed; the other {groupDigits(String(result.evidence_coverage.total_records - result.evidence_coverage.listed_records))} are included by both calculations ({groupDigits(String(result.evidence_coverage.unchanged_included_in_both))}) or by neither ({groupDigits(String(result.evidence_coverage.unchanged_included_in_neither))}), so each contributes the same to both totals and nothing to the change. Change = proposed contribution − current contribution.</p>
              : kind === 'count_distinct'
                ? <p>Each row is one distinct non-blank {keyHeader} value. A value contributes 1 when any of its records matches, and 0 otherwise. Open its supporting records to inspect membership.</p>
                : ratio
                  ? <p>Every record in the dataset, including those excluded by both calculations. Each effect combines its exact changes to the top and bottom parts using the same overall ratio weights. The effects sum exactly to the overall change; displayed decimals may round.</p>
                  : <p>Every record in the dataset, including those excluded by both calculations. Change = proposed contribution − current contribution.</p>}
            <div className="library-toolbar" role="group" aria-label="Filter records">
              <label>Find a record
                <input type="search" value={q} placeholder="Record key" onChange={e => { setQ(e.target.value); setOffset(0); }} />
              </label>
              <label>Show
                <select value={changedOnly ? 'changed' : 'all'} onChange={e => { setChangedOnly(e.target.value === 'changed'); setOffset(0); }}>
                  <option value="all">{kind === 'count_distinct' ? 'All distinct values' : result.evidence_coverage ? 'All listed records' : 'All records'}</option>
                  <option value="changed">{kind === 'count_distinct' ? 'Only values whose count changed' : 'Only records that changed'}</option>
                </select>
              </label>
              <label>Order
                <select value={order} onChange={e => { setOrder(e.target.value as 'file' | 'impact'); setOffset(0); }}>
                  <option value="file">Dataset order</option>
                  <option value="impact">Largest change first</option>
                </select>
              </label>
              {columns.length > 0 && (
                <fieldset className="column-picker">
                  <legend>Show columns</legend>
                  {columns.map(c => (
                    <label key={c}><input type="checkbox" checked={shown.includes(c)} disabled={!shown.includes(c) && shown.length >= 3}
                      onChange={e => setShown(s => e.target.checked ? [...s, c] : s.filter(x => x !== c))} /> {columnLabel(c)}</label>
                  ))}
                </fieldset>
              )}
              <button type="button" className="button secondary small" onClick={downloadCsv}>Download {kind === 'count_distinct' ? 'distinct values' : result.evidence_coverage ? 'listed records' : 'all records'} (CSV)</button>
            </div>
            {csvProblem && <p className="disabled-reason">The download failed: {csvProblem}</p>}
            {records.problem && <p className="disabled-reason">Records could not be loaded: {records.problem}</p>}
            <div className="table-scroll" aria-busy={records.busy}>
              <table>
                <thead><tr><th>{keyHeader}</th>{shown.map(c => <th key={c}>{columnLabel(c)}</th>)}<th>Current</th><th>Proposed</th><th>{ratio ? 'Effect on the ratio' : 'Change'}</th></tr></thead>
                <tbody>
                  {records.rows.map(r => (
                    <tr key={recordKey(r)}>
                      <td className="mono-cell">{recordKey(r)}
                        {kind === 'count_distinct' && <EntityRecordsButton runId={run.id} entity={recordKey(r)} />}
                      </td>
                      {shown.map(c => <td key={c}>{showValue((r as { context?: Record<string, unknown> }).context?.[c])}</td>)}
                      <td>{sideCell(r, 'baseline')}</td>
                      <td>{sideCell(r, 'target')}</td>
                      <td className={signedClass(changeOf(r))} title={r.effect_exact ? `Exact: ${r.effect_exact}${percent ? ' (×100 for points)' : ''}` : undefined}>{changeText(r)}</td>
                    </tr>
                  ))}
                  {!records.busy && records.rows.length === 0 && <tr><td colSpan={4 + shown.length}>No records match.</td></tr>}
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
                const proof = (snapshot as unknown as { join_proof: Record<string, number> & { dimensions?: Record<string, Record<string, number>> } }).join_proof;
                if (proof.dimensions) {
                  // Several lookup tables: one line per lookup.
                  return <ul>{Object.entries(proof.dimensions).map(([role, p]) => <li key={role}>{role}: {p.matched_fact_rows} records match · {p.unmatched_fact_rows} do not ({p.null_foreign_key_rows} have a blank link) · {proof.fanout_rows} duplicated by the joins.</li>)}</ul>;
                }
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
            <p>{kind === 'count' ? 'COUNT(*) includes every matching record even if its amount is blank. An empty set counts as 0.'
              : kind === 'count_distinct' ? `COUNT(DISTINCT ${keyHeader}) ignores blank values and counts each matching value once. An empty set counts as 0.`
                : ratio ? 'Each ratio part follows its own rule: SUM and COUNT(column) skip blank amounts; COUNT(*) includes matching records even if their amount is blank. Division is undefined if the bottom is blank or 0.'
                  : 'Blank (NULL) amounts are ignored when adding up; they are not treated as zero.'} Ordinary comparisons with blank values are UNKNOWN and do not pass a filter; IS NULL and IS NOT NULL test blanks explicitly.</p>
            <p>{!evidence.summary ? '' : evidence.summary.blank_amount_rows === 0 ? 'No record has a blank amount.' : `${groupDigits(String(evidence.summary.blank_amount_rows))} record(s) have a blank amount in at least one calculation.`}</p>
            {!ratio && kind !== 'count' && kind !== 'count_distinct' && <p>If a calculation has no non-blank amounts at all, its total is undefined rather than 0, and no change is reported.</p>}
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
