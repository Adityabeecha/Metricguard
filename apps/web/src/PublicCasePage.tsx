import { useEffect, useMemo, useState } from 'react';
import { Printer } from 'lucide-react';
import { type PublicCaseDetail } from './api';
import { Logo } from './Logo';
import { ThemeSwitch } from './ThemeSwitch';
import { caseNumber } from './investigationLibrary';
import { publicCase, publicEvidenceClient } from './publicCaseApi';
import { ResultReport } from './views/ResultReport';
import { CasePrintCustody } from './views/evidence/CasePrintCustody';
import './publicCase.css';

/** An isolated public surface. It never mounts workspace loaders, navigation or mutations. */
export function PublicCasePage({ token }: { token: string }) {
  const [detail, setDetail] = useState<PublicCaseDetail | null>(null);
  const [problem, setProblem] = useState('');
  const [attempt, setAttempt] = useState(0);
  const client = useMemo(() => publicEvidenceClient(token), [token]);
  useEffect(() => {
    let active = true;
    setDetail(null); setProblem('');
    document.title = 'Shared case — MetricGuard';
    // The token must not be sent as a referrer when opening an external destination.
    const meta = document.createElement('meta'); meta.name = 'referrer'; meta.content = 'no-referrer'; document.head.appendChild(meta);
    const robots = document.createElement('meta'); robots.name = 'robots'; robots.content = 'noindex, nofollow'; document.head.appendChild(robots);
    publicCase(token).then(value => { if (active) setDetail(value); })
      .catch(error => { if (active) setProblem(error instanceof Error ? error.message : String(error)); });
    return () => { active = false; meta.remove(); robots.remove(); };
  }, [token, attempt]);

  const ctx = detail?.context as { mode?: string; unit_label?: string; currency?: string } | undefined;
  return <div className="public-case-page">
    <a className="skip-link" href="#shared-case-content">Skip to case</a>
    <header className="shared-case-header"><a href="/" aria-label="MetricGuard home"><Logo size={28} /><strong>MetricGuard</strong></a><ThemeSwitch compact /></header>
    <main id="shared-case-content" className="view-container guided-page">
      <p className="shared-case-notice">Shared read-only{detail ? ` by ${detail.workspace_name}` : ''}</p>
      {problem ? <div className="alert error" role="alert"><span>{problem}</span><button className="button secondary small" onClick={() => setAttempt(v => v + 1)}>Try again</button></div>
        : !detail ? <p role="status">Opening shared case…</p> : <>
          <div className="page-heading"><div><h1>{detail.title}</h1><p><span className="case-no">{caseNumber(detail.run.id)}</span> · Sealed {new Date(detail.created_at).toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' })}</p></div>
            <div className="heading-actions"><button className="button secondary small" onClick={() => window.print()}><Printer size={15} /> Print / Save PDF</button></div></div>
          <ResultReport savedCase readOnly evidenceClient={client} run={detail.run} unit={ctx?.unit_label || ctx?.currency || ''}
            relational={ctx?.mode === 'relational_snapshot'} snapshot={detail.snapshot}
            baselineLabel="Current calculation" targetLabel="Proposed calculation"
            baselineSql={detail.baseline_version.raw_sql} targetSql={detail.target_version.raw_sql}
            versions={[detail.baseline_version, detail.target_version]}
            note={<div className="case-conclusion"><h2>Conclusion</h2><p style={{ whiteSpace: 'pre-wrap' }}>{detail.decision}</p></div>} />
          <details className="public-custody"><summary>Custody log</summary><CasePrintCustody detail={detail} /></details>
          <div className="print-only print-custody"><CasePrintCustody detail={detail} /></div>
        </>}
    </main>
  </div>;
}
