import { useState } from 'react';
import { Check, Sparkles } from 'lucide-react';
import { api, type Bootstrap, type Snapshot, type SnapshotImport } from '../api';
import { existingSample, firstRunSteps, SAMPLE_CONTRACT, SAMPLE_FILE, SAMPLE_NAME } from '../sampleDataset';
import './firstRun.css';

/** Add the bundled synthetic orders through the normal import path (same checks and limits as an upload). */
export async function addSampleDataset(projectId: string, onProgress?: (text: string) => void): Promise<string> {
  onProgress?.('Fetching the sample file…');
  const response = await fetch(SAMPLE_FILE);
  if (!response.ok) throw new Error('The sample file could not be loaded. Reload the page and try again.');
  const file = new File([await response.blob()], 'sample_orders.csv', { type: 'text/csv' });
  const contract = await api.createFlatContract({ ...SAMPLE_CONTRACT }, projectId);
  const form = new FormData();
  form.append('file', file);
  form.append('name', SAMPLE_NAME);
  form.append('contract_id', contract.id);
  form.append('project_id', projectId);
  onProgress?.('Checking 5,000 sample orders…');
  const result = await api.uploadFlat(form, projectId);
  if ('id' in result && !('state' in result)) return (result as Snapshot).id;  // already imported
  let current = result as SnapshotImport;
  for (let polls = 0; current.state === 'queued' || current.state === 'running'; polls++) {
    if (polls > 300) throw new Error('The sample is still being checked. Open Datasets in a minute to use it.');
    await new Promise(resolve => setTimeout(resolve, polls < 20 ? 1000 : 3000));
    current = await api.snapshotImport(current.id);
  }
  if (current.state === 'failed' || !current.snapshot_id) {
    throw new Error(current.error?.message || 'The sample dataset could not be added.');
  }
  return current.snapshot_id;
}


/** "Try with sample orders": adds the bundled synthetic dataset, then hands its id on (usually to New comparison). */
export function SampleDatasetButton({ data, projectId, onReady, variant = 'secondary' }: {
  data: Bootstrap; projectId: string; onReady: (snapshotId: string) => void; variant?: 'primary' | 'secondary';
}) {
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState('');
  const [problem, setProblem] = useState('');
  const existing = existingSample(data);
  const start = async () => {
    if (existing) { onReady(existing.id); return; }
    setBusy(true); setProblem('');
    try { onReady(await addSampleDataset(projectId, setProgress)); }
    catch (e) { setProblem(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); setProgress(''); }
  };
  return <div className="sample-action">
    <button type="button" className={`button ${variant}`} onClick={() => void start()} disabled={busy}>
      <Sparkles size={15} aria-hidden="true" /> {busy ? 'Adding sample orders…' : existing ? 'Compare with the sample orders' : 'Try with sample orders'}
    </button>
    {busy && progress && <small role="status">{progress}</small>}
    {!busy && !existing && !problem && <small>5,000 synthetic orders · nothing to upload</small>}
    {problem && <small className="disabled-reason" role="alert">{problem}</small>}
  </div>;
}

/** The first-run strip: Add data → Compare → Save the case. Gone once the first case is saved. */
export function FirstRunGuide({ data, onAddData, onCompare, projectId, onSampleReady }: {
  data: Bootstrap; onAddData: () => void; onCompare: () => void; projectId: string; onSampleReady: (snapshotId: string) => void;
}) {
  const steps = firstRunSteps(data);
  if (!steps.length) return null;
  const next = steps.find(s => !s.done)?.key;
  return <section className="first-run" aria-labelledby="first-run-title">
    <h2 id="first-run-title">Get to your first case</h2>
    <ol>
      {steps.map((step, i) => <li key={step.key} className={step.done ? 'done' : step.key === next ? 'current' : ''}>
        <span className="first-run-no" aria-hidden="true">{step.done ? <Check size={13} /> : i + 1}</span>
        <div>
          <strong>{step.label}</strong>{step.done && <span className="sr-only"> (done)</span>}
          {step.key === 'data' && !step.done && <div className="first-run-actions">
            <button type="button" className="button primary small" onClick={onAddData}>Upload a file</button>
            <SampleDatasetButton data={data} projectId={projectId} onReady={onSampleReady} />
          </div>}
          {step.key === 'compare' && step.key === next && <div className="first-run-actions">
            <button type="button" className="button primary small" onClick={onCompare}>New comparison</button>
          </div>}
          {step.key === 'save' && step.key === next && <p>Open your comparison, write what explains the difference, and save it. It becomes a sealed case anyone with access can replay.</p>}
        </div>
      </li>)}
    </ol>
  </section>;
}
