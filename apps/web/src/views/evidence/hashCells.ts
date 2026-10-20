/**
 * A deterministic picture of a SHA-256: a 6×6 mirror-symmetric glyph from the hash's first bytes.
 * The same hash always draws the same glyph, so matching evidence looks identical at a glance.
 */
export function hashCells(hash: string): boolean[] {
  const hex = (hash || '').replace(/[^0-9a-f]/gi, '').padEnd(36, '0');
  const cells: boolean[] = [];
  for (let row = 0; row < 6; row++) {
    const half: boolean[] = [];
    for (let col = 0; col < 3; col++) half.push(parseInt(hex[row * 3 + col], 16) % 2 === 1);
    cells.push(...half, ...[...half].reverse());
  }
  return cells;
}

export const shortHash = (hash?: string | null) => (hash ? `${hash.slice(0, 4)}…${hash.slice(-4)}` : '—');
