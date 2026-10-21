import { evidenceCountLabel, twoLoadCountNotes, evidenceSealLabel } from './evidence/evidencePresentation';
import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { api, type ConcentrationItem, type DistinctSummary, type EvidenceConcentration, type EntityRecords, type EvidenceBreakdown, type Evidence, type EvidenceSummary, type FlatEvidence, type RatioBlock, type RelationalEvidence, type RelationalRun, type Result, type Run, type Snapshot, type Version } from '../api';
import { resultUnit, fractionDisplay, groupDigits, percentOf, summarizeResult, type RecordDelta } from '../resultSummary';
import { RelationalEvidencePanel } from './RelationalEvidencePanel';
import { caseNumber } from '../investigationLibrary';
import { decimalSum } from '../exactDecimal';
import { Bridge } from './evidence/Bridge';
import { Ledger, type LedgerGroup } from './evidence/Ledger';
import { categoryLabel, type CategoryKey } from './evidence/categories';
import { HashPrint } from './evidence/HashPrint';
import { groupShare, preferredBreakdownColumn } from './evidence/breakdownSelection';
import { recordConcentrationLine } from './evidence/recordConcentration';
import { SqlComparison } from './evidence/SqlComparison';
import { RecordContext } from './evidence/RecordContext';
import { workspaceEvidenceClient, type EvidenceDataClient } from './evidence/evidenceClient';

const PAGE = 100;

type AnyEvidence = Evidence & Partial<FlatEvidence> & Partial<RelationalEvidence>;
const recordKey = (row: AnyEvidence) => row.fact_key ?? row.row_key ?? row.order_id ?? '—';

/** Server-computed change summary (exact counts across every record) for one run. */
export function useEvidenceSummary(run: Run | RelationalRun | null,client:EvidenceDataClient=workspaceEvidenceClient) {
  const [summary, setSummary] = useState<EvidenceSummary | null>(null);
  const [problem, setProblem] = useState('');
  const expired = run?.evidence_state === 'expired';
  const runId = run?.result && !expired ? run.id : '';
  useEffect(() => {
    if (!runId) { setSummary(null); setProblem(expired ? 'Detailed records expired — rerun to see them' : ''); return; }
    let active = true;
    setProblem(''); setSummary(null);
    client.evidenceSummary(runId)
      .then(s => { if (active) setSummary(s); })
      .catch(e => { if (active) setProblem(e instanceof Error ? e.message : String(e)); });
    return () => { active = false; };
  }, [runId, expired,client]);
  return { summary, problem };
}

/** One page of a filtered/sorted record view, fetched from the server. */
function useRecordPage(runId: string, view: { offset: number; changedOnly: boolean; q: string; order: 'file' | 'impact'; withContext: boolean },client:EvidenceDataClient) {
  const [rows, setRows] = useState<AnyEvidence[]>([]);
  const [total, setTotal] = useState(0);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState('');
  useEffect(() => {
    if (!runId) return;
    let active = true;
    setBusy(true); setProblem('');
    const timer = setTimeout(() => {
      client.evidenceView(runId, { ...view, limit: PAGE })
        .then(res => { if (active) { setRows(res.items as AnyEvidence[]); setTotal(res.total); } })
        .catch(e => { if (active) setProblem(e instanceof Error ? e.message : String(e)); })
        .finally(() => { if (active) setBusy(false); });
    }, view.q ? 250 : 0);
    return () => { active = false; clearTimeout(timer); };
  }, [runId, view.offset, view.changedOnly, view.q, view.order, view.withContext,client]);
  return { rows, total, busy, problem };
}

/** Seconds since `active` became true; 0 while inactive. */
function useElapsed(active: boolean) {
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    setElapsed(0);
    if (!active) return;
    const started = Date.now();
    const timer = setInterval(() => setElapsed(Math.round((Date.now() - started) / 1000)), 1000);
    return () => clearInterval(timer);
  }, [active]);
  return elapsed;
}

const NO_MATCH = '\u0000no-match';
const columnLabel = (c: string) => c.startsWith('link.') ? `link key to ${c.slice(5) === 'dimension' ? 'the lookup table' : c.slice(5)}`
  : c.startsWith('dimension.') ? `${c.slice(10)} (lookup)` : c.startsWith('fact.') ? c.slice(5) : c;
const showValue = (v: unknown) => v === NO_MATCH ? 'no matching lookup row' : v === null || v === undefined ? 'blank' : String(v);

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
                <td className={signedClass(change)}>{minus(value(change))}</td>
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
function EntityRecordsButton({ runId, entity,client }: { runId: string; entity: string;client:EvidenceDataClient }) {
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
    client.entityRecords(runId, entity, offset).then(d => { if (active) setData(d); })
      .catch(e => { if (active) setProblem(e instanceof Error ? e.message : String(e)); })
      .finally(() => { if (active) setBusy(false); });
    return () => { active = false; };
  }, [runId, entity, open, offset, retry,client]);
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
function Breakdown({ runId, unit, percent, onColumns, preferred = [] , loads, onPickGroup, client = workspaceEvidenceClient }: { runId: string; unit: string; percent?: boolean; onColumns: (c: string[]) => void; preferred?: string[] ; loads?: boolean; onPickGroup?: (group: LedgerGroup) => void; client?: EvidenceDataClient }) {
  const [column, setColumn] = useState('');
  // Across-column concentration; also picks the opening column when the filter column would only restate the filter.
  const [concentration, setConcentration] = useState<EvidenceConcentration | null | undefined>(undefined);
  useEffect(() => {
    let active = true;
    setConcentration(undefined);
    if (!client.evidenceConcentration) { setConcentration(null); return; }
    client.evidenceConcentration(runId).then(c => { if (active) setConcentration(c); }).catch(() => { if (active) setConcentration(null); });
    return () => { active = false; };
  }, [runId, client]);
  const opening = openingColumn(concentration, preferred);
  const [data, setData] = useState<EvidenceBreakdown | null>(null);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState('');
  const [all, setAll] = useState(false);
  useEffect(() => {
    if (concentration === undefined && !column) return;  // wait to know which column to open on
    let active = true;
    setBusy(true); setProblem('');
    client.evidenceBreakdown(runId, column || undefined, all ? 200 : 10)
      .then(d => {
        if (!active) return;
        onColumns(d.columns || []);
        // Open where the change concentrates outside the filter columns; otherwise on a filter column.
        const wanted = !column && ((opening && (d.columns || []).includes(opening) ? opening : '') || preferredBreakdownColumn(d.columns || [], preferred));
        if (wanted && wanted !== d.column) { setColumn(wanted); return; }
        setData(d);
        if (!column && d.column) setColumn(d.column);
      })
      .catch(e => { if (active) setProblem(e instanceof Error ? e.message : String(e)); })
      .finally(() => { if (active) setBusy(false); });
    return () => { active = false; };
  }, [runId, column, all, concentration === undefined]);
  if (problem) return <p className="disabled-reason">The breakdown could not be loaded: {problem}</p>;
  if (!data) return busy ? <p role="status">Working out where the difference comes from…</p> : null;
  const unavailable = Object.entries(data.unavailable_columns || {});
  const unavailableNote = unavailable.length > 0 && (
    <small style={{ display: 'block' }}>Not available for an exact split: {unavailable.map(([c, why]) => `${columnLabel(c)} — ${why}`).join(' ')}</small>
  );
  if (!data.available) return <div><p className="example">{data.reason}</p>{unavailableNote}</div>;
  // Changed-only listings carry no per-group totals; leave those columns out instead of a column of dashes.
  const sideTotals = (data.groups || []).some(g => g.current != null || g.proposed != null);
  const [cur, pro] = loads ? ['Earlier load', 'Later load'] : ['Current', 'Proposed'];
  const groupLabel = (g: { value: unknown; no_match?: boolean }) =>
    g.no_match ? `No matching ${data.column?.split('.')[0] === 'dimension' ? 'lookup' : data.column?.split('.')[0]} row` : showValue(g.value);
  const shown = (v: string | null | undefined) => v == null ? '—' : percent ? groupDigits(percentOf(v)) : groupDigits(v);
  const changeShown = (v: string | null | undefined) => v == null ? '—' : minus(percent ? `${groupDigits(percentOf(v))} pts` : `${groupDigits(v)}${unit ? ` ${unit}` : ''}`);
  const recordTotal = data.total_records || 0;
  const groups = [...(data.groups || [])];
  const moved = groups.filter(g => g.change != null && /[1-9]/.test(g.change));
  const still = groups.filter(g => !(g.change != null && /[1-9]/.test(g.change)));
  type Line = { key: string; label: string; change: string | null | undefined; exact?: string | null; records: number; changed: number;
    current?: string | null; proposed?: string | null; other?: boolean; value?: unknown; noMatch?: boolean };
  const lines: Line[] = [...moved, ...still].map(g => ({ key: `${g.no_match ? 'no-match' : ''}${showValue(g.value)}`, label: groupLabel(g),
    change: g.change, exact: g.change_exact, records: g.records, changed: g.changed_records, current: g.current, proposed: g.proposed,
    value: g.value, noMatch: g.no_match }));
  if ((data.other_groups || 0) > 0) {
    lines.push({ key: '__other', label: `Other (${groupDigits(String(data.other_groups))} values)`, change: data.other_change,
      records: data.other_records ?? 0, changed: data.other_changed_records ?? 0, other: true });
  }
  return (
    <div className="where-it-sits">
      <div className="where-head">
        <h3><span className="exhibit">B</span> Where the difference sits</h3>
        <label>by
          <select value={data.column || ''} onChange={e => { setColumn(e.target.value); setAll(false); }}>
            {data.columns.map(c => <option key={c} value={c}>{columnLabel(c)}</option>)}
          </select>
        </label>
      </div>
      <p className="where-legend">
        <span className="legend-bar" aria-hidden="true" /> Share of the difference
        <span className="legend-tick" aria-hidden="true" /> Share of all records.
        A value that takes far more of the difference than of the records is where the change sits.
      </p>
      <table className="ledger-table where-table" aria-busy={busy}>
        <thead><tr>
          <th scope="col">{columnLabel(data.column || '')}{data.grouping === 'day' ? ' (by day)' : ''}</th>
          <th scope="col" className="num">{data.ratio ? 'Effect on the ratio' : 'Change'}</th>
          <th scope="col">Share of the difference</th>
          <th scope="col" className="num">Changed records</th>
          {sideTotals && <><th scope="col" className="num">{data.ratio ? `${cur} ratio` : cur}</th><th scope="col" className="num">{data.ratio ? `${pro} ratio` : pro}</th></>}
        </tr></thead>
        <tbody>
          {lines.map(line => {
            const diffShare = groupShare(line.change, line.exact, data.abs_change_total);
            const recordShare = recordTotal ? line.records / recordTotal : 0;
            const concentrated = !line.other && diffShare >= 0.1 && diffShare >= 2 * recordShare;
            const zero = !(line.change != null && /[1-9]/.test(line.change));
            return (
              <tr key={line.key} className={zero ? 'muted' : line.other ? 'other' : undefined}>
                <th scope="row" data-label={columnLabel(data.column || '')}>
                  {onPickGroup && !line.other && line.changed > 0
                    ? <button type="button" className="link-button group-pick" title="Show these records" onClick={() => onPickGroup({ column: data.column || '', columnLabel: columnLabel(data.column || ''),
                        value: line.value ?? null, noMatch: line.noMatch, label: line.label, totalText: changeShown(line.change) })}>{line.label}</button>
                    : line.label}
                  {concentrated && <span className="concentrated">Concentrated</span>}</th>
                <td className="num change" data-label="Change" title={line.exact ? `Exact: ${line.exact}` : undefined}>{changeShown(line.change)}</td>
                <td className="shares" data-label="Share">
                  <span className="share-track" aria-hidden="true">
                    <span className="share-diff" style={{ width: `${Math.min(100, diffShare * 100)}%` }} />
                    <span className="share-records" style={{ left: `${Math.min(100, recordShare * 100)}%` }} />
                  </span>
                  <span className="share-text">{(diffShare * 100).toFixed(diffShare > 0 && diffShare < 0.001 ? 2 : 1)}% <small>of the difference · {(recordShare * 100).toFixed(1)}% of records</small></span>
                </td>
                <td className="num" data-label="Changed records">{groupDigits(String(line.changed))} <small>of {groupDigits(String(line.records))}</small></td>
                {sideTotals && <>
                  <td className="num" data-label={cur}>{line.other ? '' : data.ratio ? shown(line.current) : minus(groupDigits(line.current ?? '—'))}</td>
                  <td className="num" data-label={pro}>{line.other ? '' : data.ratio ? shown(line.proposed) : minus(groupDigits(line.proposed ?? '—'))}</td>
                </>}
              </tr>
            );
          })}
        </tbody>
      </table>
      {(data.other_groups || 0) > 0 && !all && <button type="button" className="link-button" onClick={() => setAll(true)}>Show every value</button>}
      <small style={{ display: 'block' }}>{data.ratio
        ? 'Each group’s effect is the exact sum of its records’ effects, and the groups add up exactly to the change in the overall ratio. Shown values are rounded; hover an effect for its exact fraction.'
        : 'Exact sums over every record; the groups, with Other, add up exactly to the total change. Shares are rounded for display.'}</small>
      {data.coverage_note && <small style={{ display: 'block' }}>{sideTotals ? data.coverage_note : 'Only records whose inclusion changed are listed for this large dataset, so each value’s change is exact; its totals on each side are not shown.'}</small>}
      {data.grouping === 'day' && <small style={{ display: 'block' }}>Timestamps are grouped by calendar day.</small>}
      {unavailableNote}
      {concentration && <Concentration data={concentration} exclude={preferred} unit={unit} percent={percent} onPick={onPickGroup} />}
    </div>
  );
}

/** The non-filter column with the strongest concentration, to open "Where it sits" on. */
function openingColumn(concentration: EvidenceConcentration | null | undefined, filters: string[]): string {
  const outside = (concentration?.items || []).find(item => !filters.some(f => item.columns[0] === f || item.columns[0].endsWith(`.${f}`)));
  return outside?.columns[0] || '';
}

/** Values and pairs of values that hold far more of the difference than of the records (filter columns left out). */
function Concentration({ data, exclude, unit, percent, onPick }: { data: EvidenceConcentration; exclude: string[]; unit: string; percent?: boolean; onPick?: (group: LedgerGroup) => void }) {
  const outside = (item: ConcentrationItem) => item.columns.every(c => !exclude.some(f => c === f || c.endsWith(`.${f}`)));
  const lines = [...data.items, ...data.pairs].filter(outside)
    .sort((a, b) => (b.lift || 0) - (a.lift || 0) || b.difference_share - a.difference_share).slice(0, 8);
  if (!data.available) return null;
  if (!lines.length) return <section className="concentration" aria-labelledby="concentration-title">
    <h4 id="concentration-title">Concentration across other columns</h4>
    <p className="where-legend">No value or pair of values holds much more of the difference than of the records: the difference is spread evenly across the other columns.</p>
  </section>;
  const valueText = (v: unknown, noMatch?: boolean) => noMatch ? 'no matching row' : showValue(v);
  const label = (item: ConcentrationItem) => item.columns.map((c, i) => `${columnLabel(c)} = ${valueText(item.values[i], item.no_match?.[i])}`).join(' × ');
  const changeText = (v: string | null) => v == null ? '—' : minus(percent ? `${groupDigits(percentOf(v))} pts` : `${groupDigits(v)}${unit ? ` ${unit}` : ''}`);
  return <section className="concentration" aria-labelledby="concentration-title">
    <h4 id="concentration-title">Concentration across other columns</h4>
    <p className="where-legend">Values and combinations that hold far more of the difference than of the records.</p>
    <ul>
      {lines.map(item => <li key={label(item)}>
        {onPick && item.changed_records > 0
          ? <button type="button" className="link-button group-pick" onClick={() => onPick({
              column: item.columns[0], columnLabel: item.columns.map(columnLabel).join(' × '), value: item.values[0] ?? null, noMatch: item.no_match?.[0],
              column2: item.columns[1], value2: item.columns[1] ? item.values[1] ?? null : undefined, noMatch2: item.no_match?.[1],
              label: item.columns.map((_, i) => valueText(item.values[i], item.no_match?.[i])).join(' × '), totalText: changeText(item.change) })}>{label(item)}</button>
          : <b>{label(item)}</b>}
        <span className="concentration-figures">{(item.difference_share * 100).toFixed(1)}% of the difference · {(item.record_share * 100).toFixed(1)}% of records · {changeText(item.change)}</span>
      </li>)}
    </ul>
    {(data.capped || []).map(note => <small key={note} style={{ display: 'block' }}>{note}</small>)}
  </section>;
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
  /** Replay outcome shown on the seal. */
  savedCase?: boolean;
  sealStatus?: 'verifying' | 'intact' | 'broken' | null;
  /** Shown under the answer, beside the seal (a saved case's written conclusion). */
  note?: ReactNode;
  /** Opens the custody log (saved cases). */
  onCustody?: () => void;
  evidenceClient?:EvidenceDataClient;
  readOnly?:boolean;
};

type Tab = 'records' | 'breakdown' | 'definitions' | 'notes';
const minus = (text: string) => text.replace(/^-/, '\u2212');

function signedClass(value: string | null | undefined) {
  if (!value || !/[1-9]/.test(value)) return 'record-delta';
  return 'record-delta';
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
  if (!included) {
    // Two tables: say when the join, not a condition, left the record out.
    const joinIn = side === 'baseline' ? row.baseline_join_included : row.target_join_included;
    if (joinIn === false) return 'Excluded · no matching lookup row';
    return 'Excluded';
  }
  return nullTerm ? 'Included · blank amount (ignored)' : 'Included';
}

export function ResultReport({ run, unit: declaredUnit, relational, baselineLabel, targetLabel, baselineSql, targetSql, baselineDescription, targetDescription, snapshot, versions, breakdownColumns, sealStatus, note, onCustody, savedCase = false,evidenceClient=workspaceEvidenceClient,readOnly=false }: Props) {
  const [tab, setTab] = useState<Tab>('records');
  const result = run.result as Result;
  const expired = run.evidence_state === 'expired';
  const kind = result.aggregate;
  // The same calculation on an earlier and a later load of one dataset, instead of two calculations.
  const loads = Boolean((run as Run).target_snapshot_id);
  const [cur, pro] = loads ? ['Earlier load', 'Later load'] : ['Current', 'Proposed'];
  const differing = result.change_summary
    ? Object.entries(result.change_summary).filter(([k]) => k !== 'unchanged').reduce((n, [, v]) => n + (v?.records || 0), 0)
    : result.evidence_coverage?.listed_records;
  const ratio = kind === 'ratio' ? result.ratio ?? null : null;
  const percent = ratio?.unit === 'share';
  // COUNT(*) counts records and COUNT(DISTINCT) counts values: the dataset's declared unit does not apply.
  const unit = resultUnit(result, declaredUnit);
  const noun = ratio ? 'value' : kind === 'count_distinct' || kind === 'count' ? 'count' : 'total';
  const evidence = useEvidenceSummary(run,evidenceClient);
  const top = (evidence.summary?.top_changes || []) as AnyEvidence[];
  // Large changed-records-only runs state how many records they list in the result itself,
  // so the count is right before the evidence summary has loaded.
  const listedTotal: number | null = result.evidence_coverage?.listed_records ?? evidence.summary?.total ?? null;
  const summaryLoading = Boolean(run.result) && !evidence.summary && !evidence.problem;
  const summary = useMemo(() => summarizeResult(result, unit, evidence.summary ? {
    rows: top.map<RecordDelta>(r => ({ key: recordKey(r), delta: r.delta })),
    total: evidence.summary.total,
    complete: true,
    changedCount: evidence.summary.changed,
    differing,
  } : undefined, loads ? 'loads' : 'calculations'), [result, unit, evidence.summary]);

  const [offset, setOffset] = useState(0);
  const [changedOnly, setChangedOnly] = useState(false);
  const [q, setQ] = useState('');
  const [order, setOrder] = useState<'file' | 'impact'>('file');
  const [jump, setJump] = useState('');
  const [columns, setColumns] = useState<string[]>([]);
  const [shown, setShown] = useState<string[]>([]);
  const [csvProblem, setCsvProblem] = useState('');
  const [ledgerCategory, setLedgerCategory] = useState<CategoryKey | ''>('');
  const [ledgerGroup, setLedgerGroup] = useState<LedgerGroup | null>(null);
  const pickGroup = (group: LedgerGroup) => { setLedgerGroup(group); setLedgerCategory(''); setTab('records'); };
  const [registerOpen, setRegisterOpen] = useState(false);
  const records = useRecordPage(run.result && !expired ? run.id : '', { offset, changedOnly, q, order, withContext: true },evidenceClient);
  const pages = Math.max(1, Math.ceil(records.total / PAGE));
  const pageNo = Math.floor(offset / PAGE) + 1;
  const waitingFor = summaryLoading || (records.busy && records.rows.length === 0);
  const waited = useElapsed(waitingFor);
  // The first opening of a result verifies every listed record against the seal before any is shown.
  const loadingNote = (what: string) => <>
    {what}{listedTotal != null && listedTotal > 10_000
      ? ` Every one of the ${groupDigits(String(listedTotal))} listed records is checked against the seal before any is shown, so the first opening of a large result can take a minute or two; later openings are quick.`
      : ''}{waited >= 3 ? ` (${waited} s)` : ''}
  </>;
  const goPage = (n: number) => setOffset((Math.min(Math.max(1, n), pages) - 1) * PAGE);
  const show = (v: string | null) => (v == null ? (ratio ? 'Undefined' : 'Undefined (no values)')
    : percent ? `${groupDigits(percentOf(v))}%` : unit ? `${groupDigits(v)} ${unit}` : groupDigits(v));
  const num = (v: string | null | undefined) => (v == null ? '—' : groupDigits(v));
  const keyHeader = kind === 'count_distinct' ? (result.distinct?.entity_column ?? 'Value') : 'Record';
  const listLabel = loads ? 'Records that differ between the loads' : result.evidence_coverage ? 'Listed records'
    : `All ${kind === 'count_distinct' ? `${keyHeader} values` : 'records'}`;
  const changeOf = (r: AnyEvidence) => r.delta;
  const changeText = (r: AnyEvidence) => {
    if (!ratio && r.change) return <>{minus(num(r.delta))} <small>· {CHANGE_LABEL[r.change] || r.change}</small></>;
    if (!ratio) return minus(num(r.delta));
    if (r.effect == null) return '—';
    return <>{minus(percent ? `${groupDigits(percentOf(r.effect))} pts` : `${groupDigits(r.effect)}${unit ? ` ${unit}` : ''}`)}
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
    return <>{inclusion(r, side)}{contribution != null && <> · <span className="n">{minus(num(contribution))}</span></>}</>;
  };
  const downloadCsv = () => {
    setCsvProblem('');
    api.download(`/comparisons/${encodeURIComponent(run.id)}/evidence.csv`, `metricguard-${run.id}-records.csv`)
      .catch(e => setCsvProblem(e instanceof Error ? e.message : String(e)));
  };

  // ---- Reconciliation (docs/design/evidence-ui-spec.md §2–3)
  const categories = evidence.summary?.categories ?? null;
  const labels = { loads, distinct: kind === 'count_distinct' };
  const fmt = (v: string | null | undefined) => v == null ? '—'
    : minus(percent ? `${groupDigits(percentOf(v))} pts` : `${groupDigits(v)}${unit ? ` ${unit}` : ''}`);
  const stepValue = (c: { change: string; effect?: string | null }) => (ratio ? (c.effect ?? '0') : c.change);
  const residual = ratio ? '0' : (result.residual ?? '0');
  const balanced = !/[1-9]/.test(residual);
  const listedChange = categories && !ratio ? decimalSum(categories.map(c => c.change)) : null;
  const bridge = categories && categories.length > 0 && result.baseline_value != null && result.target_value != null ? {
    start: { label: cur, value: percent ? percentOf(result.baseline_value) : result.baseline_value, text: show(result.baseline_value) },
    end: { label: pro, value: percent ? percentOf(result.target_value) : result.target_value, text: show(result.target_value) },
    steps: categories.map(c => ({ key: c.key, label: categoryLabel(c.key, labels), records: c.records,
      value: percent ? percentOf(stepValue(c)) : stepValue(c), text: fmt(stepValue(c)) })),
    residualText: fmt(residual),
    balanced,
    description: `${cur} ${show(result.baseline_value)}, ${categories.map(c => `${categoryLabel(c.key, labels).toLowerCase()} ${fmt(stepValue(c))} (${evidenceCountLabel(c.records, loads)})`).join(', ')}, unexplained ${fmt(residual)}, equals ${pro.toLowerCase()} ${show(result.target_value)}${balanced ? '; balanced' : ''}.`,
  } : null;
  const ledgerTotal = ledgerCategory
    ? fmt(stepValue(categories?.find(c => c.key === ledgerCategory) ?? { change: '0' }))
    : ratio ? fmt(result.delta_target_minus_baseline) : fmt(listedChange);
  const unchanged = result.evidence_coverage
    ? result.evidence_coverage.total_records - result.evidence_coverage.listed_records
    : evidence.summary ? evidence.summary.total - evidence.summary.changed : null;
  const balanceText = ratio
    ? <>Record effects add up exactly to the change in the ratio ({fmt(result.delta_target_minus_baseline)}){balanced ? <> · <b>Balanced ✓</b></> : null}</>
    : <>Listed changes {fmt(listedChange)} + unexplained {fmt(residual)} = {fmt(result.delta_target_minus_baseline)}{balanced ? <> · <b>Balanced ✓</b></> : null}</>;
  const pickCategory = (key: string) => { setLedgerGroup(null); setLedgerCategory(key as CategoryKey); setTab('records'); };

  return (
    <div className="result-summary">
      <div className="case-header">
        <section aria-labelledby="answer-title">
          <h2 id="answer-title" className="sr-only">Answer</h2>
          <div className="answer-figure">
            <span className="figure" aria-label="Change, proposed minus current">
              {result.delta_target_minus_baseline == null ? 'Undefined' : minus(groupDigits(percent ? percentOf(result.delta_target_minus_baseline) : result.delta_target_minus_baseline))}
            </span>
            {result.delta_target_minus_baseline != null && <span className="figure-unit">{percent ? 'pts' : unit}</span>}
          </div>
          <p className="answer-sentence" aria-live="polite">{summary.headline}{summary.recordsSentence ? ` ${summary.recordsSentence}` : ''}</p>
          {evidence.problem && <p className="disabled-reason" role="status">{expired ? evidence.problem : `The record summary could not be loaded: ${evidence.problem}`}</p>}
          <div className="answer-pair">
            <span>{cur} {noun}<b>{show(result.baseline_value)}</b></span>
            <span>{pro} {noun}<b>{show(result.target_value)}</b></span>
            <span>Unexplained<b>{result.residual == null ? 'Not applicable' : ratio ? '0 (exact)' : show(result.residual)}</b></span>
          </div>
          {note}
        </section>
        <SealCard run={run} snapshot={snapshot} status={sealStatus} onCustody={onCustody} saved={savedCase} />
      </div>

      {bridge && !expired && <Bridge {...bridge} onPick={pickCategory} recordsAffectTotal={loads} />}

      {loads && !expired && twoLoadCountNotes(result.change_summary, categories).map(note => <p className="helper-muted" key={note}>{note}</p>)}
      <div className="tabs-wrap"><div className="tabs result-tabs" role="tablist" aria-label="Result details">
        {([['records', 'A', kind === 'count_distinct' ? `${keyHeader} values` : 'Records'], ['breakdown', 'B', 'Where it sits'], ['definitions', 'C', 'Definitions'], ['notes', 'D', 'Limits']] as Array<[Tab, string, string]>).map(([id, letter, label]) => (
          <button key={id} type="button" role="tab" aria-selected={tab === id} className={`tab ${tab === id ? 'active' : ''}`} onClick={() => setTab(id)}>
            <span className="exhibit" aria-hidden="true">{letter}</span>{label}{id === 'records' && evidence.summary ? <span className="tab-count">{groupDigits(String(evidence.summary.changed))}</span> : null}
          </button>
        ))}
      </div></div>

      <div className="definitions-exhibit" hidden={tab !== 'definitions'}>
        <div className="calc-pair"><p>{baselineLabel}{baselineDescription && ` · ${baselineDescription}`}</p><p>{targetLabel}{targetDescription && ` · ${targetDescription}`}</p></div>
        <SqlComparison before={baselineSql} after={targetSql} currentLabel={cur} proposedLabel={pro} />
      </div>

      {tab === 'definitions' && ratio && (
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
      {tab === 'breakdown' && ratio && <RatioPanel ratio={ratio} percent={percent} amountUnit={declaredUnit} outputUnit={unit} />}
      {tab === 'breakdown' && kind === 'count_distinct' && result.distinct && <DistinctPanel distinct={result.distinct} />}

      {/* Kept mounted so its column list feeds the record table's "Show columns"; hidden outside its tab. */}
      {run.result && !expired && <div hidden={tab !== 'breakdown'}><Breakdown runId={run.id} unit={unit} percent={percent} onColumns={setColumns} preferred={breakdownColumns} loads={loads} onPickGroup={pickGroup} client={evidenceClient} /></div>}

      {expired && (tab === 'records' || tab === 'breakdown') && <p className="disabled-reason" role="status">Detailed records expired — rerun to see them</p>}
      {tab === 'records' && summaryLoading && (
        <div className="guided-explain run-progress" role="status">
          <span className="run-spinner" aria-hidden="true" />
          <span>{loadingNote('Finding the records that explain the difference.')}</span>
        </div>
      )}

      {tab === 'records' && !expired && recordConcentrationLine(evidence.summary,result) && <p className="record-concentration" role="status">{recordConcentrationLine(evidence.summary,result)}</p>}

      {tab === 'records' && categories && categories.length > 0 && !expired && (
        <Ledger evidenceClient={evidenceClient} runId={run.id} categories={categories} category={ledgerCategory} onCategory={key => { setLedgerGroup(null); setLedgerCategory(key); }} labels={labels}
          group={ledgerGroup} onClearGroup={() => setLedgerGroup(null)}
          keyHeader={keyHeader} side={sideCell} change={changeText} additive={!ratio} totalText={ledgerTotal}
          balanceText={balanceText} balanced={balanced} unchanged={unchanged}
          unchangedNoun={kind === 'count_distinct' ? `${keyHeader} values` : 'records'}
          onShowRegister={() => { setRegisterOpen(true); setTimeout(() => document.getElementById('full-register')?.scrollIntoView({ behavior: 'smooth', block: 'start' }), 50); }} />
      )}

      {tab === 'records' && !categories && top.length > 0 && (
        <div>
          <h3 style={{ fontSize: 14, margin: '0 0 6px' }}>{kind === 'count_distinct' ? `${keyHeader} values` : 'Records'} that explain the difference{top.length > 1 ? ' (largest first)' : ''}</h3>
          <div className="table-scroll">
            <table className="stack-table">
              <thead><tr><th>{keyHeader}</th><th>{cur}</th><th>{pro}</th><th className="figure-cell">{ratio ? 'Effect on the ratio' : 'Change'}</th></tr></thead>
              <tbody>
                {top.slice(0, 10).map(r => (
                  <tr key={recordKey(r)}>
                    <td className="mono-cell">{recordKey(r)}</td>
                    <td className="side-cell" data-label={cur}>{sideCell(r, 'baseline')}</td>
                    <td className="side-cell" data-label={pro}>{sideCell(r, 'target')}</td>
                    <td className={signedClass(changeOf(r))} data-label={ratio ? 'Effect' : 'Change'} title={r.effect_exact ? `Exact: ${r.effect_exact}${percent ? ' (×100 for points)' : ''}` : undefined}>{changeText(r)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {(evidence.summary?.changed ?? 0) > 10 && <small>Showing the 10 largest of {groupDigits(String(evidence.summary!.changed))} changed {kind === 'count_distinct' ? `${keyHeader} values` : 'records'}. Open “{listLabel}” below to see them all.</small>}
        </div>
      )}

      {!expired && <section className="print-only print-records">
        <h3><span className="exhibit">A</span> Largest record changes</h3>
        <p>{recordConcentrationLine(evidence.summary,result)}</p>
        <p>Top {Math.min(10,top.length)} of {groupDigits(String(evidence.summary?.changed||0))} changed records. This is a bounded excerpt; the saved case keeps the full evidence.</p>
        <table className="ledger-table"><thead><tr><th>{keyHeader}</th><th>{cur}</th><th>{pro}</th><th>Change</th></tr></thead>
          <tbody>{top.slice(0,10).map(row=><tr key={recordKey(row)}><td>{recordKey(row)}</td><td>{sideCell(row,'baseline')}</td><td>{sideCell(row,'target')}</td><td>{changeText(row)}</td></tr>)}</tbody>
        </table>
      </section>}

      {tab === 'notes' && summary.uncertainties.length > 0 && (
        <div>
          <h3 style={{ fontSize: 14, margin: '0 0 6px' }}>What remains uncertain</h3>
          <ul className="uncertainty-list">{summary.uncertainties.map(u => <li key={u}>{u}</li>)}</ul>
        </div>
      )}

      <div className="detail-stack">
        <details id="full-register" hidden={expired || tab !== 'records'} open={registerOpen || (top.length === 0 && !summaryLoading && !categories)}
          onToggle={e => setRegisterOpen((e.currentTarget as HTMLDetailsElement).open)}>
          <summary>Full register · {listLabel} ({listedTotal == null ? '…' : groupDigits(String(listedTotal))})</summary>
          <div>
            {result.evidence_coverage
              ? <p>This dataset has {groupDigits(String(result.evidence_coverage.total_records))} records. Listed here are the {groupDigits(String(result.evidence_coverage.listed_records))} whose inclusion{kind === 'ratio' ? ' or contribution' : ''} changed; the other {groupDigits(String(result.evidence_coverage.total_records - result.evidence_coverage.listed_records))} are included by both calculations ({groupDigits(String(result.evidence_coverage.unchanged_included_in_both))}) or by neither ({groupDigits(String(result.evidence_coverage.unchanged_included_in_neither))}), so each contributes the same to both totals and nothing to the change. Change = proposed contribution − current contribution.</p>
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
              {!readOnly && <button type="button" className="button secondary small" onClick={downloadCsv}>Download {kind === 'count_distinct' ? 'distinct values' : result.evidence_coverage ? 'listed records' : 'all records'} (CSV)</button>}
            </div>
            {csvProblem && <p className="disabled-reason">The download failed: {csvProblem}</p>}
            {records.problem && <p className="disabled-reason">Records could not be loaded: {records.problem}</p>}
            <div className="table-scroll" aria-busy={records.busy}>
              <table className="stack-table">
                <thead><tr><th>{keyHeader}</th>{shown.map(c => <th key={c}>{columnLabel(c)}</th>)}<th>{cur}</th><th>{pro}</th><th className="figure-cell">{ratio ? 'Effect on the ratio' : 'Change'}</th></tr></thead>
                <tbody>
                  {records.rows.map(r => (
                    <tr key={recordKey(r)}>
                      <td className="mono-cell">{recordKey(r)}
                        <RecordContext row={r} loads={loads} />
                        {kind === 'count_distinct' && <EntityRecordsButton client={evidenceClient} runId={run.id} entity={recordKey(r)} />}
                      </td>
                      {shown.map(c => <td key={c} data-label={columnLabel(c)}>{showValue((r as { context?: Record<string, unknown> }).context?.[c])}</td>)}
                      <td className="side-cell" data-label={cur}>{sideCell(r, 'baseline')}</td>
                      <td className="side-cell" data-label={pro}>{sideCell(r, 'target')}</td>
                      <td className={signedClass(changeOf(r))} data-label={ratio ? 'Effect' : 'Change'} title={r.effect_exact ? `Exact: ${r.effect_exact}${percent ? ' (×100 for points)' : ''}` : undefined}>{changeText(r)}</td>
                    </tr>
                  ))}
                  {records.busy && records.rows.length === 0 && <tr><td colSpan={4 + shown.length} role="status">{loadingNote('Loading records.')}</td></tr>}
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

        {relational && !readOnly && (
          <details hidden={tab !== 'definitions'}>
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

        <details hidden={tab !== 'notes'}>
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

        <details hidden={tab !== 'notes'}>
          <summary>Reconciliation</summary>
          <div>
            <p>Sum of record changes plus the unexplained difference equals the total change.</p>
            {result.steps?.length > 0 && (
              <div className="table-scroll">
                <table>
                  <thead><tr><th>#</th><th>Rule changed</th><th>Before</th><th>After</th><th>Contribution</th></tr></thead>
                  <tbody>{result.steps.map(s => (
                    <tr key={s.position}><td>{s.position}</td><td>{s.rule.replaceAll('_', ' ')}</td><td>{s.previous_value ?? '—'}</td><td>{s.next_value ?? '—'}</td><td className={signedClass(s.contribution)}>{s.contribution == null ? '—' : minus(s.contribution)}</td></tr>
                  ))}</tbody>
                </table>
              </div>
            )}
            <p>Status: {result.explanation_status?.replaceAll('_', ' ') || 'not reported'} · definitions {result.definition_status?.replaceAll('_', ' ')}.</p>
          </div>
        </details>

        <details hidden={tab !== 'definitions'}>
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

/** The evidence seal: case number, frozen data and evidence hashes, and the replay outcome.
 *  The case number is the sealed run's, so it is the same during review and after saving. */
function SealCard({ run, snapshot, status, onCustody, saved = false }: { saved?: boolean; run: Run | RelationalRun; snapshot?: Snapshot | null; status?: 'verifying' | 'intact' | 'broken' | null; onCustody?: () => void }) {
  const result = run.result as Result & { evidence_artifact?: { sha256?: string; row_count?: number } };
  const provenance = (result?.provenance || {}) as { database_hash?: string; snapshot_manifest_hash?: string };
  const sealed = (run as { completed_at?: string }).completed_at || run.created_at;
  return (
    <aside className={`seal-card ${status ?? ''}`} aria-label="Evidence seal">
      <div className="seal-strip">
        <span>{evidenceSealLabel(saved, status)}</span>
        <span className="seal-no">{caseNumber(run.id)}</span>
      </div>
      <dl>
        <dt>Dataset</dt><dd title={provenance.database_hash}>{snapshot?.name && <span className="seal-name">{snapshot.name}</span>}<HashPrint hash={provenance.database_hash} size={18} /></dd>
        <dt>Evidence</dt><dd title={result?.evidence_artifact?.sha256}><HashPrint hash={result?.evidence_artifact?.sha256} size={18} /></dd>
        <dt>{saved ? 'Sealed' : 'Compared'}</dt><dd>{sealed ? new Date(sealed).toLocaleString('en-GB', { day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' }) : '—'}</dd>
        <dt>Engine</dt><dd>{run.execution_mode === 'isolated' ? 'MetricGuard' : run.execution_mode}</dd>
      </dl>
      <div className={`seal-status ${status === 'intact' ? 'intact' : status === 'broken' ? 'broken' : ''}`} role="status">
        {status === 'intact' ? 'Seal intact · replay matched byte for byte'
          : status === 'broken' ? 'Replay differs from the sealed evidence'
            : status === 'verifying' ? 'Replaying on the same frozen data…'
              : 'Verified on publication · replay to re-check'}
      </div>
      {onCustody && <button type="button" className="link-button seal-custody" onClick={onCustody}>Custody log ›</button>}
    </aside>
  );
}
