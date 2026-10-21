/** Light SQL tokenizer for highlighting only; it never decides what SQL is accepted (the server parser does). */
export type SqlToken = { text: string; kind: 'keyword' | 'function' | 'string' | 'number' | 'column' | 'table' | 'operator' | 'plain' };

const KEYWORDS = new Set(['select', 'from', 'where', 'and', 'or', 'not', 'in', 'is', 'null', 'between', 'case', 'when', 'then',
  'else', 'end', 'distinct', 'as', 'true', 'false', 'like', 'join', 'left', 'inner', 'on']);
const FUNCTIONS = new Set(['sum', 'count', 'avg', 'nullif', 'coalesce']);

export function sqlTokens(sql: string, columns: string[] = [], tables: string[] = []): SqlToken[] {
  const cols = new Set(columns.map(c => c.toLowerCase()));
  const tabs = new Set(tables.map(t => t.toLowerCase()));
  const out: SqlToken[] = [];
  const pattern = /('(?:[^']|'')*'?)|(\d+(?:\.\d+)?)|([A-Za-z_][A-Za-z0-9_]*)|(<>|!=|<=|>=|[=<>*/+\-(),.;])|(\s+)|(.)/gs;
  for (const m of sql.matchAll(pattern)) {
    const [text, str, num, word, op] = m;
    if (str !== undefined) out.push({ text, kind: 'string' });
    else if (num !== undefined) out.push({ text, kind: 'number' });
    else if (word !== undefined) {
      const lower = word.toLowerCase();
      out.push({ text, kind: KEYWORDS.has(lower) ? 'keyword' : FUNCTIONS.has(lower) ? 'function' : tabs.has(lower) ? 'table' : cols.has(lower) ? 'column' : 'plain' });
    } else if (op !== undefined) out.push({ text, kind: 'operator' });
    else out.push({ text, kind: 'plain' });
  }
  return out;
}

/** Insert text at the cursor, adding a space where it would otherwise run into a word. */
export function insertAt(value: string, start: number, end: number, text: string): { value: string; cursor: number } {
  const before = value.slice(0, start), after = value.slice(end);
  const lead = before && !/[\s(,]$/.test(before) ? ' ' : '';
  const trail = after && !/^[\s),]/.test(after) ? ' ' : '';
  const inserted = `${lead}${text}${trail}`;
  return { value: before + inserted + after, cursor: before.length + lead.length + text.length };
}
