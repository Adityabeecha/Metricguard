import { useRef } from 'react';
import { insertAt, refusalPosition, sqlTokens, tokenAt } from './evidence/sqlHighlight';
import './sqlEditor.css';

export type SqlColumn = { name: string; type: string; role?: string };

/** SQL box with light highlighting and the dataset's columns beside it (click one to insert it). */
export function SqlEditor({ id, value, onChange, columns, tables, placeholder, rows = 6, refusal }: {
  id?: string; value: string; onChange: (sql: string) => void; columns: SqlColumn[]; tables: string[]; placeholder?: string; rows?: number;
  /** A refusal message; "(at character N)" in it underlines that part of the SQL. */
  refusal?: string | null;
}) {
  const area = useRef<HTMLTextAreaElement>(null);
  const layer = useRef<HTMLPreElement>(null);
  const insert = (text: string) => {
    const el = area.current;
    const start = el?.selectionStart ?? value.length, end = el?.selectionEnd ?? value.length;
    const next = insertAt(value, start, end, text);
    onChange(next.value);
    requestAnimationFrame(() => { el?.focus(); el?.setSelectionRange(next.cursor, next.cursor); });
  };
  const tokens = sqlTokens(value, columns.map(c => c.name), tables);
  const refused = tokenAt(tokens, refusalPosition(refusal));
  return <div className="sql-editor">
    {(columns.length > 0 || tables.length > 0) && <div className="sql-schema" aria-label="Table and columns">
      {tables.map(t => <button key={t} type="button" className="sql-chip table" onClick={() => insert(t)} title={`Insert ${t}`}>{t}</button>)}
      {columns.map(c => <button key={c.name} type="button" className="sql-chip" onClick={() => insert(c.name)} title={`Insert ${c.name}${c.role ? ` (${c.role})` : ''}`}>
        {c.name}<span className="sql-type">{c.role || c.type}</span></button>)}
    </div>}
    <div className="sql-input">
      <pre ref={layer} className="sql-layer" aria-hidden="true">{tokens.map((t, i) => <span key={i} className={`tok-${t.kind}${i === refused ? ' tok-refused' : ''}`}>{t.text}</span>)}{'\n'}</pre>
      <textarea id={id} ref={area} rows={rows} spellCheck={false} value={value} placeholder={placeholder}
        onChange={e => onChange(e.target.value)}
        onScroll={e => { if (layer.current) { layer.current.scrollTop = e.currentTarget.scrollTop; layer.current.scrollLeft = e.currentTarget.scrollLeft; } }} />
    </div>
  </div>;
}
