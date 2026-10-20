import { evidenceCountLabel } from './evidencePresentation';
import { Stamp } from './Stamp';

export type BridgeStep = { key: string; label: string; value: number; text: string; records: number };
export type BridgeProps = {
  start: { label: string; value: number; text: string };
  end: { label: string; value: number; text: string };
  steps: BridgeStep[];
  residualText: string;
  balanced: boolean;
  /** One sentence read by screen readers instead of the drawing. */
  description: string;
  onPick?: (key: string) => void;
  recordsAffectTotal?: boolean;
};

const W = 900, CHART = 170, TOP = 28, LABELS = 78;

/**
 * The reconciliation bridge (docs/design/evidence-ui-spec.md §2): current total, one step per change
 * category, the unexplained remainder and the proposed total. Numbers on the bars are the exact
 * strings from the server; the drawing only positions them.
 */
export function Bridge({ start, end, steps, residualText, balanced, description, onPick, recordsAffectTotal = false }: BridgeProps) {
  const levels = [start.value];
  steps.forEach(s => levels.push(levels[levels.length - 1] + s.value));
  levels.push(end.value);
  const min = Math.min(...levels), max = Math.max(...levels);
  const span = max - min || Math.abs(max) || 1;
  const startsAtZero = min >= 0 && min <= span * 0.25;
  const lo = startsAtZero ? 0 : min - span * 0.25;
  const hi = max + span * 0.08;
  const y = (v: number) => TOP + CHART - ((v - lo) / (hi - lo || 1)) * CHART;
  const columns = 2 + steps.length + 1;
  const cw = W / columns;
  const bw = Math.min(cw * 0.52, 120);
  const x = (i: number) => i * cw + (cw - bw) / 2;
  const bottom = TOP + CHART;
  const broken = !startsAtZero;

  const totalBar = (i: number, v: number, text: string, label: string) => (
    <g key={`total-${i}`}>
      <rect x={x(i)} y={y(v)} width={bw} height={Math.max(1, bottom - y(v))} className="bridge-total" />
      {broken && <path d={`M${x(i) - 2} ${bottom - 14} l${bw / 4 + 1} -5 l${bw / 4} 5 l${bw / 4} -5 l${bw / 4 + 1} 5 v6 h${-bw - 4} z`} className="bridge-break" />}
      <text x={x(i) + bw / 2} y={y(v) - 8} className="bridge-figure" textAnchor="middle">{text}</text>
      <text x={x(i) + bw / 2} y={bottom + 20} className="bridge-label" textAnchor="middle">{label}</text>
    </g>
  );

  return (
    <figure className="bridge" aria-label="Reconciliation bridge">
      <svg className="bridge-svg" viewBox={`0 0 ${W} ${TOP + CHART + LABELS}`} role="img" aria-label={description}>
        <defs>
          <pattern id="bridge-hatch" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
            <rect width="6" height="6" className="bridge-hatch-ground" />
            <line x1="0" y1="0" x2="0" y2="6" className="bridge-hatch-line" />
          </pattern>
        </defs>
        <line x1={0} x2={W} y1={bottom} y2={bottom} className="bridge-axis" />
        {totalBar(0, start.value, start.text, start.label)}
        {steps.map((s, i) => {
          const from = levels[i], to = levels[i + 1];
          const col = i + 1;
          const top = y(Math.max(from, to)), height = Math.max(2, Math.abs(y(from) - y(to)));
          return (
            <g key={s.key} className={onPick ? 'bridge-step pickable' : 'bridge-step'} onClick={onPick ? () => onPick(s.key) : undefined}
               role={onPick ? 'button' : undefined} tabIndex={onPick ? 0 : undefined}
               onKeyDown={onPick ? (e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onPick(s.key); } }) : undefined}
               aria-label={onPick ? `${s.label}: ${s.text}, ${evidenceCountLabel(s.records, recordsAffectTotal)}. Show these records.` : undefined}>
              <line x1={x(col - 1) + bw} x2={x(col)} y1={y(from)} y2={y(from)} className="bridge-connector" />
              <rect x={x(col)} y={top} width={bw} height={height} className={s.value < 0 ? 'bridge-down' : 'bridge-up'} />
              <text x={x(col) + bw / 2} y={top - 8} className="bridge-figure" textAnchor="middle">{s.text}</text>
              <text x={x(col) + bw / 2} y={bottom + 20} className="bridge-label" textAnchor="middle">{s.label}</text>
              <text x={x(col) + bw / 2} y={bottom + 38} className="bridge-records" textAnchor="middle">{evidenceCountLabel(s.records)}{recordsAffectTotal && <tspan x={x(col) + bw / 2} dy="15">{s.records === 1 ? 'that changes the total' : 'that change the total'}</tspan>}</text>
            </g>
          );
        })}
        {(() => {
          const col = steps.length + 1;
          const level = levels[levels.length - 2];
          return (
            <g>
              <line x1={x(col - 1) + bw} x2={x(col) + bw} y1={y(level)} y2={y(level)} className="bridge-connector" />
              <line x1={x(col)} x2={x(col) + bw} y1={y(level)} y2={y(level)} className="bridge-residual" />
              <text x={x(col) + bw / 2} y={y(level) - 8} className="bridge-figure" textAnchor="middle">{residualText}</text>
              <text x={x(col) + bw / 2} y={bottom + 20} className="bridge-label" textAnchor="middle">Unexplained</text>
            </g>
          );
        })()}
        <line x1={x(steps.length + 1) + bw} x2={x(steps.length + 2)} y1={y(end.value)} y2={y(end.value)} className="bridge-connector" />
        {totalBar(steps.length + 2, end.value, end.text, end.label)}
      </svg>

      {/* Phones: the same steps as a ruled list. */}
      <ol className="bridge-list" aria-label="Reconciliation steps">
        <li className="total"><span>{start.label}</span><b>{start.text}</b></li>
        {steps.map(s => (
          <li key={s.key}>
            <button type="button" disabled={!onPick} onClick={() => onPick?.(s.key)}>
              <span>{s.label}<small>{evidenceCountLabel(s.records, recordsAffectTotal)}</small></span><b>{s.text}</b>
            </button>
          </li>
        ))}
        <li><span>Unexplained</span><b>{residualText}</b></li>
        <li className="total"><span>{end.label}</span><b>{end.text}</b></li>
      </ol>

      <figcaption className="bridge-caption">
        <span>{broken ? 'Totals are drawn on a shortened axis so the steps stay readable; every figure is exact.' : 'Every figure is exact.'}{onPick ? ' Select a step to see its records.' : ''}</span>
        {balanced && <Stamp kind="balanced" />}
      </figcaption>
    </figure>
  );
}
