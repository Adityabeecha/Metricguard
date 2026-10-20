import { hashCells, shortHash } from './hashCells.ts';

export { hashCells, shortHash };

export function HashPrint({ hash, size = 24, label = true }: { hash?: string | null; size?: number; label?: boolean }) {
  if (!hash) return <span className="hash-print empty">—</span>;
  const cells = hashCells(hash);
  const cell = size / 6;
  return (
    <span className="hash-print" title={hash}>
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} role="img" aria-label={`Fingerprint of hash ${shortHash(hash)}`}>
        <rect width={size} height={size} rx={2} className="hash-print-ground" />
        {cells.map((on, i) => on && (
          <rect key={i} x={(i % 6) * cell} y={Math.floor(i / 6) * cell} width={cell} height={cell} className="hash-print-cell" />
        ))}
      </svg>
      {label && <code>{shortHash(hash)}</code>}
    </span>
  );
}
