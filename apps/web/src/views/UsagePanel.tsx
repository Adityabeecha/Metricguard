import { useEffect, useState } from 'react';
import { api } from '../api';

type Usage = { max_rows: number | null; max_upload_mb: number | null; max_runs_per_day: number | null;
  runs_last_24h: number; storage_bytes: number; storage_basis: string };

export function UsagePanel({ workspaceId }: { workspaceId: string }) {
  const [usage, setUsage] = useState<Usage | null>(null);
  const [problem, setProblem] = useState('');
  useEffect(() => {
    let active = true;
    setUsage(null); setProblem('');
    api.get<Usage>(`/usage?project_id=${encodeURIComponent(workspaceId)}`)
      .then(value => { if (active) setUsage(value); })
      .catch(error => { if (active) setProblem(error instanceof Error ? error.message : String(error)); });
    return () => { active = false; };
  }, [workspaceId]);
  const number = (value: number | null) => value == null ? 'No deployment limit' : value.toLocaleString();
  return <section className="panel" aria-labelledby="usage-heading">
    <div className="panel-header"><h2 id="usage-heading">Usage</h2><p>Limits and usage for this workspace. Read only.</p></div>
    <div style={{ padding: 'var(--space-4, 16px) var(--space-5, 20px)' }}>
      {problem && <p role="alert">{problem}</p>}
      {!usage && !problem && <p role="status">Loading usage…</p>}
      {usage && <><dl className="form-stack">
        <div><dt>Rows per dataset</dt><dd className="n">{number(usage.max_rows)}</dd></div>
        <div><dt>Upload size</dt><dd className="n">{usage.max_upload_mb == null ? number(null) : `${number(usage.max_upload_mb)} MB`}</dd></div>
        <div><dt>Comparisons in the last 24 hours</dt><dd className="n">{number(usage.runs_last_24h)}{usage.max_runs_per_day != null ? ` of ${number(usage.max_runs_per_day)}` : ' (no daily limit)'}</dd></div>
        <div><dt>Storage used</dt><dd className="n">{(usage.storage_bytes / (1024 * 1024)).toLocaleString(undefined, { maximumFractionDigits: 2 })} MiB</dd></div>
      </dl><p className="hint">Includes your datasets and saved evidence.</p></>}
    </div>
  </section>;
}
