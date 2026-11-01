import { useEffect, useState } from 'react';
import { api } from './api';

export type Usage = { max_rows?: number | null; max_upload_mb?: number | null; max_runs_per_day?: number | null;
  runs_last_24h?: number | null; storage_bytes?: number | null; storage_quota_bytes?: number | null; guest_expires_at?: number | null };

export function useUsage(workspaceId: string, refreshKey?: unknown) {
  const [usage, setUsage] = useState<Usage | null>(null);
  const [problem, setProblem] = useState('');
  useEffect(() => {
    let active = true;
    setUsage(null); setProblem('');
    api.get<Usage>(`/usage?project_id=${encodeURIComponent(workspaceId)}`)
      .then(value => { if (active) setUsage(value); })
      .catch(error => { if (active) setProblem(error instanceof Error ? error.message : String(error)); });
    return () => { active = false; };
  }, [workspaceId, refreshKey]);
  return { usage, problem };
}

export function runsLeft(usage: Usage | null): string {
  if (usage?.max_runs_per_day == null || usage.runs_last_24h == null) return '';
  return `${Math.max(0, usage.max_runs_per_day - usage.runs_last_24h)} of ${usage.max_runs_per_day} comparisons left today`;
}
