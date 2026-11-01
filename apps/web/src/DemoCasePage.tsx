import { useEffect, useState } from 'react';
import { ArrowRight } from 'lucide-react';
import { runningTotals } from './exactDecimal';
import { digitStyleFor, groupDigits } from './resultSummary';
import { DEMO_COLUMNS, DEMO_ROWS, demoBreakdown, demoCaseFor, demoChanges, demoReplayResult, demoTotals, parseSampleCsv, type DemoColumn, type DemoRow } from './demoCase';
import { Bridge } from './views/evidence/Bridge';
import { HashPrint } from './views/evidence/HashPrint';
import { Stamp } from './views/evidence/Stamp';
import { categoryDescription } from './views/evidence/categories';
import { replayChecks } from './views/evidence/replayChecks';

type Tab = 'records' | 'breakdown' | 'definitions' | 'notes';
const minus = (v: string) => v.replace(/^-/, '−');
const PAGE = 100;

/** The public, read-only sample case: the same evidence parts as the app, computed from the case's synthetic orders. */
export function DemoCasePage() {
  const C = demoCaseFor(window.location.pathname);
  const style = digitStyleFor(C.unit);
  const digits = (v: string) => groupDigits(v, style);
  const money = (v: string) => `${minus(digits(v))} ${C.unit}`;
  const [rows, setRows] = useState<DemoRow[] | null>(C.rowsUrl ? null : DEMO_ROWS);
  const [problem, setProblem] = useState('');
  useEffect(() => {
    if (!C.rowsUrl) return;
    let active = true;
    fetch(C.rowsUrl).then(r => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.text(); })
      .then(text => { if (active) setRows(parseSampleCsv(text)); })
      .catch(() => { if (active) setProblem('The sample orders could not be loaded. Reload the page to try again.'); });
    return () => { active = false; };
  }, [C.rowsUrl]);
  const [tab, setTab] = useState<Tab>('records');
  const [column, setColumn] = useState<DemoColumn>(C.rowsUrl ? 'channel' : 'status');
  const [shown, setShown] = useState(PAGE);
  if (!rows) return <article className="demo-case"><p role="status">{problem || 'Loading 5,000 sample orders…'}</p></article>;
  const t = demoTotals(rows, C);
  const changes = demoChanges(rows, C);
  const running = runningTotals(changes.map(c => c.delta));
  const groups = demoBreakdown(column, rows, C);
  const balanced = !/[1-9]/.test(t.residual);
  const checks = replayChecks(demoReplayResult(rows, C), demoReplayResult(rows, C));
  const excludedDescription = categoryDescription('excluded', {});

  return <article className="demo-case">
    <p className="demo-banner" role="note">
      <span>Sample case · synthetic orders · read-only.</span>
      <a href="/signup">Sign up to run your own <ArrowRight size={14} aria-hidden="true" /></a>
    </p>

    <header className="demo-case-head">
      <p className="hero-case-line"><span className="case-no">{C.number}</span><span>{C.dataset} · {groupDigits(String(t.records))} orders</span><Stamp kind="sealed" /><Stamp kind="replayed" /></p>
      <h1>{C.title}</h1>
    </header>

    <section className="demo-answer" aria-labelledby="demo-answer-title">
      <h2 id="demo-answer-title" className="sr-only">Answer</h2>
      <div className="answer-figure"><span className="figure">{minus(digits(t.delta))}</span><span className="figure-unit">{C.unit}</span></div>
      <p className="answer-sentence">The proposed calculation is lower than the current one. {groupDigits(String(changes.length))} records explain it, with nothing unexplained.</p>
      <div className="answer-pair">
        <span>Current · {C.current.label}<b>{money(t.current)}</b></span>
        <span>Proposed · {C.proposed.label}<b>{money(t.proposed)}</b></span>
        <span>Unexplained<b>{money(t.residual)}</b></span>
      </div>
      <p className="demo-conclusion"><b>Conclusion.</b> {C.conclusion}</p>
    </section>

    <Bridge
      unit={C.unit}
      start={{ label: 'Current', value: Number(t.current), text: money(t.current) }}
      end={{ label: 'Proposed', value: Number(t.proposed), text: money(t.proposed) }}
      steps={[{ key: 'excluded', label: 'Excluded by proposed', description: excludedDescription, value: Number(t.listed), text: money(t.listed), records: changes.length }]}
      residualText={money(t.residual)} balanced={balanced}
      onPick={() => setTab('records')}
      description={`Current ${money(t.current)}, excluded by proposed ${money(t.listed)} (${changes.length} records), unexplained ${money(t.residual)}, equals proposed ${money(t.proposed)}${balanced ? '; balanced' : ''}.`} />

    <div className="tabs-wrap"><div className="tabs result-tabs" role="tablist" aria-label="Case details">
      {([['records', 'A', 'Records'], ['breakdown', 'B', 'Where it sits'], ['definitions', 'C', 'Definitions'], ['notes', 'D', 'Limits']] as Array<[Tab, string, string]>).map(([id, letter, label]) => (
        <button key={id} type="button" role="tab" aria-selected={tab === id} className={`tab ${tab === id ? 'active' : ''}`} onClick={() => setTab(id)}>
          <span className="exhibit" aria-hidden="true">{letter}</span>{label}{id === 'records' ? <span className="tab-count">{groupDigits(String(changes.length))}</span> : null}
        </button>
      ))}
    </div></div>

    {tab === 'records' && <section className="ledger" aria-labelledby="demo-a">
      <div className="ledger-head"><h3 id="demo-a"><span className="exhibit">A</span> Records that explain the difference</h3></div>
      <p className="helper-muted">Excluded by proposed: {excludedDescription}</p>
      <table className="ledger-table">
        <thead><tr><th scope="col">Order</th><th scope="col">Current → Proposed</th><th scope="col" className="num">Change</th><th scope="col" className="num">Running total</th></tr></thead>
        <tbody>{changes.slice(0, shown).map((c, i) => <tr key={c.row.row_id}>
          <td className="key" data-label="Order">{c.row.row_id} <small>{c.row.status} · {c.row.channel}</small></td>
          <td className="move" data-label="Current → Proposed"><span className="was left">{digits(c.current)}</span><span className="arrow" aria-label="becomes">→</span><span className="now">{c.proposed == null ? 'excluded' : digits(c.proposed)}</span></td>
          <td className="num change" data-label="Change">{minus(digits(c.delta))}</td>
          <td className="num running" data-label="Running total">{minus(digits(running[i]))}</td>
        </tr>)}</tbody>
        <tfoot><tr className="ledger-total"><th scope="row" colSpan={2}>Total of listed changes</th><td className="num"><span className="double-rule">{money(t.listed)}</span></td><td /></tr></tfoot>
      </table>
      {shown < changes.length && <p><button type="button" className="button secondary small" onClick={() => setShown(n => n + PAGE)}>
        Show {Math.min(PAGE, changes.length - shown)} more of {groupDigits(String(changes.length - shown))}</button></p>}
      <div className="ledger-foot">
        <p className={`ledger-balance${balanced ? ' ok' : ''}`}>Listed changes {money(t.listed)} + unexplained {money(t.residual)} = {money(t.delta)}{balanced ? <> · <b>Balanced ✓</b></> : null}</p>
        <p className="ledger-unchanged">✓ {groupDigits(String(t.records - changes.length))} orders unchanged, including {t.blank} with a blank amount that neither total counts.</p>
      </div>
    </section>}

    {tab === 'breakdown' && <div className="where-it-sits">
      <div className="where-head">
        <h3><span className="exhibit">B</span> Where the difference sits</h3>
        <label>by <select value={column} onChange={e => setColumn(e.target.value as DemoColumn)}>{DEMO_COLUMNS.map(c => <option key={c} value={c}>{c}</option>)}</select></label>
      </div>
      <p className="where-legend"><span className="legend-bar" aria-hidden="true" /> Share of the difference <span className="legend-tick" aria-hidden="true" /> Share of all records.</p>
      <table className="ledger-table where-table">
        <thead><tr><th scope="col">{column}</th><th scope="col" className="num">Change</th><th scope="col">Share of the difference</th><th scope="col" className="num">Changed records</th></tr></thead>
        <tbody>{groups.map(g => {
          const zero = !/[1-9]/.test(g.change);
          return <tr key={String(g.value)} className={zero ? 'muted' : undefined}>
            <th scope="row" data-label={column}>{g.value ?? '(blank)'}{!zero && g.share >= 0.1 && g.share >= 1.25 * g.recordShare && <span className="concentrated">Concentrated</span>}</th>
            <td className="num change" data-label="Change">{money(g.change)}</td>
            <td className="shares" data-label="Share">
              <span className="share-track" aria-hidden="true"><span className="share-diff" style={{ width: `${g.share * 100}%` }} /><span className="share-records" style={{ left: `${g.recordShare * 100}%` }} /></span>
              <span className="share-text">{(g.share * 100).toFixed(1)}% <small>of the difference · {(g.recordShare * 100).toFixed(1)}% of records</small></span>
            </td>
            <td className="num" data-label="Changed records">{groupDigits(String(g.changed))} <small>of {groupDigits(String(g.records))}</small></td>
          </tr>;
        })}</tbody>
      </table>
    </div>}

    {tab === 'definitions' && <div className="calc-pair">
      <div><h3>Current calculation · {C.current.label}</h3><p><code className="mono-cell">{C.current.sql}</code></p></div>
      <div><h3>Proposed calculation · {C.proposed.label}</h3><p><code className="mono-cell">{C.proposed.sql}</code></p></div>
    </div>}

    {tab === 'notes' && <div className="demo-limits">
      <h3><span className="exhibit">D</span> Limits</h3>
      <ul>
        <li>Both calculations ran on the same frozen dataset; the result says nothing about whether the source data is right.</li>
        <li>Blank amounts are left out of both totals, so they never appear as a change.</li>
        <li>{C.limit}</li>
      </ul>
    </div>}

    <section aria-labelledby="demo-replay-title">
      <h2 id="demo-replay-title">Replay &amp; verify</h2>
      <p>The sample's saved and replayed values are identical.</p>
      <table className="ledger-table replay-checks">
        <caption className="sr-only">Sample saved case and replay checks</caption>
        <thead><tr><th>Checked</th><th>Saved case</th><th>Replay</th><th>Result</th></tr></thead>
        <tbody>{checks.map(check => <tr key={check.label}>
          <td data-label="Checked">{check.label}</td><td className="n" data-label="Saved case">{check.saved}</td>
          <td className="n" data-label="Replay">{check.replayed}</td><td data-label="Result">{check.same ? 'Same' : 'Differs'}</td>
        </tr>)}</tbody>
      </table>
    </section>

    <section className="demo-custody" aria-labelledby="demo-custody-title">
      <h2 id="demo-custody-title">Custody log</h2>
      <ol className="custody-log">
        <li className="done"><div><div className="what">Dataset frozen</div><div className="when">{C.sealedAt}</div><div className="detail">{C.dataset} · {groupDigits(String(t.records))} rows <HashPrint hash={C.hashes.dataset} /></div></div></li>
        <li className="done"><div><div className="what">Calculations pinned</div><div className="detail">Current <HashPrint hash={C.hashes.current} /> Proposed <HashPrint hash={C.hashes.proposed} /></div></div></li>
        <li className="done"><div><div className="what">Compared on the isolated engine</div><div className="when">{C.sealedAt} · no network</div><div className="detail">Evidence <HashPrint hash={C.hashes.evidence} /></div></div></li>
        <li className="done"><div><div className="what">Sealed as case {C.number}</div><div className="when">{C.sealedAt}</div></div></li>
        <li className="ok"><div><div className="what">Replayed: identical</div><div className="when">{C.replayedAt}</div><div className="detail">Evidence <HashPrint hash={C.hashes.evidence} /> <Stamp kind="replayed" /></div></div></li>
      </ol>
      <p className="helper-muted">In your own workspace, Replay re-runs the saved inputs on the isolated engine and checks that the evidence fingerprint is identical.</p>
    </section>

    <p className="demo-next"><a className="button primary" href="/signup">Sign up to run your own <ArrowRight size={16} aria-hidden="true" /></a></p>
  </article>;
}
