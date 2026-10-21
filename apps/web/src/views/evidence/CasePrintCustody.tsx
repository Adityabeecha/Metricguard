import type { PublicCaseDetail, Run } from '../../api';
import { HashPrint } from './HashPrint';
import { caseNumber } from '../../investigationLibrary';

/** A static custody record is also present in the print DOM when the interactive drawer is closed. */
export function CasePrintCustody({ detail, replays = [] }: { detail: Omit<PublicCaseDetail, 'workspace_name'>; replays?: Run[] }) {
  const evidenceHash = (run: Run) => (run.result as unknown as { evidence_artifact?: { sha256?: string } })?.evidence_artifact?.sha256;
  const hash = (value?: string | null) => <span className="custody-print-hash"><HashPrint hash={value || undefined} size={18} /><code>{value || 'Not recorded'}</code></span>;
  return <section className="case-print-custody" aria-label="Case custody record">
    <h2>Custody log</h2>
    <dl>
      <dt>Dataset frozen</dt><dd>{detail.snapshot.name} · {detail.snapshot.row_count} records · {detail.snapshot.created_at}{hash(detail.snapshot.manifest_hash)}</dd>
      <dt>Current calculation pinned</dt><dd>{hash(detail.baseline_version.ir_hash || detail.baseline_version.raw_hash)}</dd>
      <dt>Proposed calculation pinned</dt><dd>{hash(detail.target_version.ir_hash || detail.target_version.raw_hash)}</dd>
      <dt>Compared</dt><dd>{detail.run.completed_at || detail.run.created_at}{hash(evidenceHash(detail.run))}</dd>
      <dt>Sealed as case {caseNumber(detail.run.id)}</dt><dd>{detail.created_at}</dd>
      {replays.map(run => <div key={run.id}><dt>Replay · {run.state}</dt><dd>{run.completed_at || run.created_at}{hash(evidenceHash(run))}</dd></div>)}
    </dl>
  </section>;
}
