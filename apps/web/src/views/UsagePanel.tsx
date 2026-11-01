import { useUsage } from '../useUsage';

export function UsagePanel({ workspaceId }: { workspaceId: string }) {
  const { usage, problem } = useUsage(workspaceId);
  const number = (value: number | null | undefined) => value == null ? 'No deployment limit' : value.toLocaleString();
  const mb = (value: number) => (value / (1024 * 1024)).toLocaleString(undefined, { maximumFractionDigits: 2 });
  const bar = (value: number, limit: number) => <progress value={Math.min(value, limit)} max={limit} aria-label="Usage" />;
  const minutes = usage?.guest_expires_at == null ? null : Math.max(0, Math.ceil((usage.guest_expires_at * 1000 - Date.now()) / 60000));
  return <section className="panel" aria-labelledby="usage-heading">
    <div className="panel-header"><h2 id="usage-heading">Usage</h2><p>Limits and usage for this workspace. Read only.</p></div>
    <div style={{ padding: 'var(--space-4, 16px) var(--space-5, 20px)' }}>
      {problem && <p role="alert">{problem}</p>}
      {!usage && !problem && <p role="status">Loading usage...</p>}
      {usage && <><dl className="form-stack">
        <div><dt>Rows per dataset</dt><dd className="n">{number(usage.max_rows)}</dd></div>
        <div><dt>Upload size</dt><dd className="n">{usage.max_upload_mb == null ? number(null) : `${number(usage.max_upload_mb)} MB`}</dd></div>
        <div><dt>Comparisons in the last 24 hours</dt><dd className="n">{usage.runs_last_24h == null ? 'Unavailable' : number(usage.runs_last_24h)}{usage.max_runs_per_day != null ? ` of ${number(usage.max_runs_per_day)}` : ' (no daily limit)'}</dd>{usage.runs_last_24h != null && usage.max_runs_per_day != null && usage.max_runs_per_day > 0 && bar(usage.runs_last_24h, usage.max_runs_per_day)}</div>
        <div><dt>Storage used</dt><dd className="n">{usage.storage_bytes == null ? 'Unavailable' : `${mb(usage.storage_bytes)} MB`}{usage.storage_quota_bytes != null ? ` of ${mb(usage.storage_quota_bytes)} MB` : ''}</dd>{usage.storage_bytes != null && usage.storage_quota_bytes != null && usage.storage_quota_bytes > 0 && bar(usage.storage_bytes, usage.storage_quota_bytes)}</div>
      </dl>{minutes != null && <p>Erased in {Math.floor(minutes / 60)} h {minutes % 60} min</p>}<p className="hint">Storage includes your datasets and saved evidence. Every comparison you run counts toward the daily limit, whether or not you save it as a case.</p></>}
    </div>
  </section>;
}
