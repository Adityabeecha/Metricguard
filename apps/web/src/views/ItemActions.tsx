import { useEffect, useRef, useState, type FormEvent } from 'react';
import { MoreHorizontal } from 'lucide-react';
import './itemActions.css';

type Mode = 'menu' | 'rename' | 'delete' | null;

/** Row menu for a list item: rename it, or delete it after confirming by name. */
export function ItemActions({ name, kind, onRename, onDelete, deleteNote }: {
  name: string;
  kind: 'dataset' | 'case';
  onRename: (name: string) => Promise<void>;
  onDelete: () => Promise<void>;
  deleteNote?: string;
}) {
  const [mode, setMode] = useState<Mode>(null);
  const [value, setValue] = useState(name);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState('');
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  // Fixed to the viewport, so a scrolling list never clips the menu.
  const [anchor, setAnchor] = useState<{ top: number; right: number } | null>(null);
  const place = () => {
    const rect = trigger.current?.getBoundingClientRect();
    if (rect) setAnchor({ top: Math.min(rect.bottom + 4, window.innerHeight - 220), right: Math.max(8, window.innerWidth - rect.right) });
  };

  const close = () => { setMode(null); setProblem(''); setBusy(false); trigger.current?.focus(); };
  useEffect(() => {
    if (!mode) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') close(); };
    const onClick = (e: MouseEvent) => { if (root.current && !root.current.contains(e.target as Node)) close(); };
    document.addEventListener('keydown', onKey);
    document.addEventListener('mousedown', onClick);
    window.addEventListener('resize', place);
    window.addEventListener('scroll', place, true);
    return () => {
      document.removeEventListener('keydown', onKey); document.removeEventListener('mousedown', onClick);
      window.removeEventListener('resize', place); window.removeEventListener('scroll', place, true);
    };
  }, [mode]);

  const run = async (action: () => Promise<void>) => {
    setBusy(true); setProblem('');
    try { await action(); close(); }
    catch (e) { setProblem(e instanceof Error ? e.message : String(e)); setBusy(false); }
  };
  const rename = (e: FormEvent) => { e.preventDefault(); if (value.trim() && value.trim() !== name) void run(() => onRename(value.trim())); else close(); };

  return <div className="item-actions" ref={root} onClick={e => e.stopPropagation()}>
    <button ref={trigger} type="button" className="item-actions-trigger" aria-label={`More actions for ${name}`} aria-haspopup="menu" aria-expanded={mode === 'menu'}
      onClick={() => { place(); setMode(m => m ? null : 'menu'); }}><MoreHorizontal size={16} /></button>
    {mode === 'menu' && <div className="item-actions-pop" role="menu" style={anchor ? { top: anchor.top, right: anchor.right } : undefined}>
      <button type="button" role="menuitem" onClick={() => { setValue(name); setMode('rename'); }}>Rename</button>
      <button type="button" role="menuitem" className="danger" onClick={() => setMode('delete')}>Delete</button>
    </div>}
    {mode === 'rename' && <form className="item-actions-pop" style={anchor ? { top: anchor.top, right: anchor.right } : undefined} onSubmit={rename} aria-label={`Rename ${kind}`}>
      <label>New name<input autoFocus value={value} maxLength={160} onChange={e => setValue(e.target.value)} /></label>
      {problem && <p className="disabled-reason" role="alert">{problem}</p>}
      <div className="item-actions-buttons">
        <button type="button" className="button secondary small" onClick={close}>Cancel</button>
        <button type="submit" className="button primary small" disabled={busy || !value.trim()}>{busy ? 'Saving…' : 'Rename'}</button>
      </div>
    </form>}
    {mode === 'delete' && <div className="item-actions-pop" style={anchor ? { top: anchor.top, right: anchor.right } : undefined} role="alertdialog" aria-label={`Delete ${kind}`}>
      <p>Delete <b>“{name}”</b>? This can't be undone.</p>
      {deleteNote && <p className="hint">{deleteNote}</p>}
      {problem && <p className="disabled-reason" role="alert">{problem}</p>}
      <div className="item-actions-buttons">
        <button type="button" className="button secondary small" onClick={close} autoFocus>Cancel</button>
        <button type="button" className="button danger small" disabled={busy} onClick={() => void run(onDelete)}>{busy ? 'Deleting…' : `Delete ${kind}`}</button>
      </div>
    </div>}
  </div>;
}
