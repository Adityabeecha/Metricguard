import { useState, type FormEvent } from 'react';
import { X } from 'lucide-react';
import { api } from '../api';
import { useDialog } from '../useDialog';

/** "Report a problem or suggest something": free text, sent with the page it came from. */
export function FeedbackDialog({ workspaceId, onClose }: { workspaceId: string; onClose: () => void }) {
  const [message, setMessage] = useState('');
  const [state, setState] = useState<'idle' | 'sending' | 'sent'>('idle');
  const [problem, setProblem] = useState('');
  const ref = useDialog<HTMLDivElement>(true, onClose, state !== 'sending');
  const send = async (e: FormEvent) => {
    e.preventDefault();
    setState('sending'); setProblem('');
    try {
      await api.post('/feedback', { message: message.trim(), project_id: workspaceId, page: window.location.pathname, app_version: 'web' });
      setState('sent');
    } catch (err) { setProblem(err instanceof Error ? err.message : String(err)); setState('idle'); }
  };
  return <div className="modal-backdrop" onClick={() => state !== 'sending' && onClose()}>
    <div ref={ref} className="modal-card" role="dialog" aria-modal="true" aria-labelledby="feedback-title" onClick={e => e.stopPropagation()} style={{ maxWidth: 480 }}>
      <div className="modal-header">
        <div><h2 id="feedback-title">Feedback</h2><p className="modal-subtitle">Report a problem or suggest something. We read every note.</p></div>
        <button type="button" className="modal-close-button" onClick={onClose} aria-label="Close dialog"><X size={18} /></button>
      </div>
      {state === 'sent'
        ? <div style={{ padding: '16px 20px', display: 'grid', gap: 12 }}>
            <p role="status">Thanks — your note was sent.</p>
            <div><button type="button" className="button primary" onClick={onClose}>Done</button></div>
          </div>
        : <form onSubmit={send} style={{ padding: '16px 20px', display: 'grid', gap: 12 }}>
            <label className="form-label" htmlFor="feedback-message">What happened, or what would help?</label>
            <textarea id="feedback-message" autoFocus rows={6} maxLength={4000} value={message} onChange={e => setMessage(e.target.value)}
              placeholder="e.g. The breakdown tab showed 0% for my ratio comparison." />
            <p className="hint">Sent with this page's address. Don't include passwords or customer data.</p>
            {problem && <p className="disabled-reason" role="alert">{problem}</p>}
            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
              <button type="button" className="button secondary" onClick={onClose}>Cancel</button>
              <button type="submit" className="button primary" disabled={state === 'sending' || !message.trim()}>{state === 'sending' ? 'Sending…' : 'Send'}</button>
            </div>
          </form>}
    </div>
  </div>;
}
