import type { Snapshot } from './api';

/** A later load may only be checked against an earlier load of its frozen dataset. */
export function loadCheckPairOptions(snapshots: Snapshot[], targetId: string): Snapshot[] {
  const target = snapshots.find(item => item.id === targetId);
  if (!target?.contract_hash || target.execution_policy !== 'flat_snapshot_v1') return [];
  return snapshots.filter(item => item.id !== targetId && item.execution_policy === 'flat_snapshot_v1'
    && item.contract_hash === target.contract_hash && item.created_at <= target.created_at
    && item.manifest_hash !== target.manifest_hash)
    .sort((a, b) => b.created_at.localeCompare(a.created_at));
}

type CheckRun = { id: string; state: string };
/** One normal admission at a time. Any admission/poll failure halts the remaining checks. */
export async function executeLoadChecks<T, R extends CheckRun>(items: T[], options: {
  start: (item: T) => Promise<R>; poll: (id: string) => Promise<R>;
  pause: () => Promise<void>; onRun: (run: R) => void; stopped: () => boolean;
}): Promise<{ completed: number; error: string }> {
  let completed = 0;
  try {
    for (const item of items) {
      if (options.stopped()) break;
      let run = await options.start(item);
      options.onRun(run);
      while (run.state === 'queued' || run.state === 'running') {
        if (options.stopped()) return { completed, error: '' };
        await options.pause();
        if (options.stopped()) return { completed, error: '' };
        run = await options.poll(run.id);
        options.onRun(run);
      }
      completed += 1;
    }
    return { completed, error: '' };
  } catch (error) {
    return { completed, error: error instanceof Error ? error.message : String(error) };
  }
}
