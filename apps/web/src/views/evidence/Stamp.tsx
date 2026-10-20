/** The only stamps the product uses (DESIGN.md / evidence-ui-spec §6): at most one per screen region. */
export type StampKind = 'sealed' | 'balanced' | 'replayed' | 'differs';

const TEXT: Record<StampKind, string> = { sealed: 'Sealed', balanced: 'Balanced', replayed: 'Replayed ✓', differs: 'Differs' };

export function Stamp({ kind, label }: { kind: StampKind; label?: string }) {
  return <span className={`mg-stamp ${kind}`}>{label ?? TEXT[kind]}</span>;
}
