type ContextRow={context?:Record<string,unknown>|null;baseline_context?:Record<string,unknown>|null;target_context?:Record<string,unknown>|null;baseline_present?:boolean;target_present?:boolean};
const label=(value:unknown) => value==null ? 'blank' : value==='\u0000no-match' ? 'no matching lookup row' : String(value);

/** Context is separately sealed from monetary contributions. Missing records are never shown as blank values. */
export function RecordContext({row,loads=false}:{row:ContextRow;loads?:boolean}) {
  const names=[...new Set([...Object.keys(row.context||{}),...Object.keys(row.baseline_context||{}),...Object.keys(row.target_context||{})])];
  if (!names.length) return null;
  const dual=loads && ('baseline_context' in row || 'target_context' in row);
  return <details className="record-context">
    <summary>Filter columns <span className="n">({names.length})</span></summary>
    <dl>{names.map(name=> {
      const was=row.baseline_present===false || row.baseline_context===null ? 'Not in this load' : label(row.baseline_context?.[name]);
      const now=row.target_present===false || row.target_context===null ? 'Not in this load' : label(row.target_context?.[name]);
      return <div key={name}><dt>{name}</dt><dd>{dual && was!==now
        ? <span className="field-change"><span className="field-was">{was}</span><span aria-label="becomes">→</span><span className="field-now">{now}</span></span>
        : label(row.context?.[name])}</dd></div>;
    })}</dl>
  </details>;
}
