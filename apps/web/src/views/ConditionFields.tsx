// Value box and value help for one filter condition, shared by the guided comparison and the
// two-loads comparison. Values come from /snapshots/{id}/column-values (single-table datasets).
import type { ColumnValues } from '../api';
import { FEW_VALUES, unknownValueWarnings, type Filter, type FilterColumn } from '../comparisonBuilder';
import { groupDigits } from '../resultSummary';

function splitValues(raw: string): string[] { return raw.split(',').map(v => v.trim()).filter(Boolean); }

/** The value box of a condition: suggests the column's real values when they are known. */
export function ConditionValue({ id, filter, column, known, onChange }: {
  id: string; filter: Filter; column?: FilterColumn; known?: ColumnValues | null; onChange: (value: string) => void;
}) {
  const needsValue = filter.op !== 'is_null' && filter.op !== 'is_not_null';
  const list = filter.op === 'in' || filter.op === 'not_in';
  const sample = known?.values.slice(0, 2).map(v => v.value) || [];
  const placeholder = column?.type === 'boolean' ? 'true or false' : column?.type === 'date' ? '2026-09-01'
    : column?.type === 'timestamp' ? '2026-09-01T00:00:00'
    : sample.length ? (list ? sample.join(', ') : sample[0]) : list ? 'value, another value' : 'value';
  // A datalist completes the whole box, so it suits single values; lists use the value buttons below.
  const suggest = needsValue && !list && Boolean(known?.values.length);
  return (
    <label>Value
      <input disabled={!needsValue} value={needsValue ? filter.value : ''} onChange={e => onChange(e.target.value)}
        list={suggest ? id : undefined} placeholder={placeholder} />
      {suggest && (
        <datalist id={id}>
          {known!.values.map(v => <option key={v.value} value={v.value}>{groupDigits(String(v.count))} records</option>)}
        </datalist>
      )}
    </label>
  );
}

/** Clickable values for short lists, and a warning for typed values the column never holds. */
export function ConditionHelp({ filter, column, known, onChange }: {
  filter: Filter; column?: FilterColumn; known?: ColumnValues | null; onChange: (value: string) => void;
}) {
  if (filter.op === 'is_null' || filter.op === 'is_not_null' || !known) return null;
  const list = filter.op === 'in' || filter.op === 'not_in';
  const chosen = list ? splitValues(filter.value) : [filter.value.trim()];
  const pick = (value: string) => onChange(!list ? value
    : (chosen.includes(value) ? chosen.filter(v => v !== value) : [...chosen, value]).join(', '));
  const short = known.complete && known.values.length <= FEW_VALUES && column?.type !== 'date' && column?.type !== 'timestamp';
  const warnings = unknownValueWarnings(filter, column, known);
  return (
    <>
      {short && known.values.length > 0 && (
        <div className="chip-row value-chips" role="group" aria-label={`Values of ${column?.label || 'this column'}`}>
          {known.values.map(v => (
            <button key={v.value} type="button" className="chip" aria-pressed={chosen.includes(v.value)} onClick={() => pick(v.value)}>
              {v.value}<span className="n">{groupDigits(String(v.count))}</span>
            </button>
          ))}
        </div>
      )}
      {!short && known.values.length > 0 && !known.complete && (
        <span className="example">{groupDigits(String(known.distinct_count))} different values; the box suggests the {known.values.length} most common.</span>
      )}
      {warnings.map(w => <span key={w} className="form-hint warn" role="status">{w}</span>)}
    </>
  );
}
