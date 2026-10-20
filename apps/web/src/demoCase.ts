import { decimalSum } from './exactDecimal.ts';

/** The public sample case MG-F3A3: the seeded synthetic orders (demo/closeout/sales_orders.csv) and its sealed hashes. */
export const DEMO_CASE = {
  number: 'MG-F3A3',
  title: 'Demo · Revenue, completed orders only',
  unit: 'INR',
  dataset: 'Demo sales orders',
  current: { label: 'All orders', sql: 'SELECT SUM(sales_amount) FROM snapshot' },
  proposed: { label: 'Completed orders only', sql: "SELECT SUM(sales_amount) FROM snapshot WHERE status = 'completed'" },
  conclusion: 'Limiting the total to completed orders removes three orders (pending R003 200.00, returned R004 50.00, cancelled R007 90.00): 340.00 INR lower. R006 has a blank amount and is ignored by both totals.',
  sealedAt: '17 Oct 2026, 21:15',
  replayedAt: '20 Oct 2026, 15:57',
  hashes: {
    dataset: '442bd3d056832e6ea3a24bc62635fcffc72c957f4deef1d7e50d28c5990af648',
    current: '6b999209d489aebcf4fb97d3b256bacdf59861d822dea0a4a908135cb5ee64c2',
    proposed: '41f98de6826f493fadbf63d56d07bf74b097be7ab1467224126809c92378afa8',
    evidence: '761d2b86d3f47de44f00929f5ab7053a31fd41143cf6214f4b5fdaf20ae790a7',
  },
} as const;

export type DemoRow = { row_id: string; sales_amount: string | null; status: string; region: string | null; channel: string };

export const DEMO_ROWS: DemoRow[] = [
  { row_id: 'R001', sales_amount: '120.00', status: 'completed', region: 'north', channel: 'web' },
  { row_id: 'R002', sales_amount: '80.50', status: 'completed', region: 'south', channel: 'store' },
  { row_id: 'R003', sales_amount: '200.00', status: 'pending', region: 'north', channel: 'web' },
  { row_id: 'R004', sales_amount: '50.00', status: 'returned', region: 'west', channel: 'store' },
  { row_id: 'R005', sales_amount: '150.25', status: 'completed', region: 'north', channel: 'web' },
  { row_id: 'R006', sales_amount: null, status: 'completed', region: 'south', channel: 'web' },
  { row_id: 'R007', sales_amount: '90.00', status: 'cancelled', region: 'east', channel: 'store' },
  { row_id: 'R008', sales_amount: '60.00', status: 'completed', region: null, channel: 'web' },
];

export const DEMO_COLUMNS = ['status', 'region', 'channel'] as const;
export type DemoColumn = typeof DEMO_COLUMNS[number];

const inCurrent = (r: DemoRow) => r.sales_amount != null;
const inProposed = (r: DemoRow) => r.sales_amount != null && r.status === 'completed';
const negate = (v: string) => v.startsWith('-') ? v.slice(1) : `-${v}`;

/** Per-record change, proposed minus current (exact decimal strings). */
export function demoChanges(rows: DemoRow[] = DEMO_ROWS) {
  return rows
    .filter(r => inCurrent(r) !== inProposed(r))
    .map(r => ({ row: r, current: r.sales_amount as string, proposed: inProposed(r) ? r.sales_amount : null,
      delta: inProposed(r) ? (r.sales_amount as string) : negate(r.sales_amount as string) }))
    .sort((a, b) => Math.abs(Number(b.delta)) - Math.abs(Number(a.delta)) || a.row.row_id.localeCompare(b.row.row_id));
}

/** Both totals, the change, and what the listed records leave unexplained. */
export function demoTotals(rows: DemoRow[] = DEMO_ROWS) {
  const current = decimalSum(rows.filter(inCurrent).map(r => r.sales_amount as string));
  const proposed = decimalSum(rows.filter(inProposed).map(r => r.sales_amount as string));
  const delta = decimalSum([proposed, negate(current)]);
  const listed = decimalSum(demoChanges(rows).map(c => c.delta));
  const residual = decimalSum([delta, negate(listed)]);
  return { current, proposed, delta, listed, residual, records: rows.length, blank: rows.filter(r => r.sales_amount == null).length };
}

/** Exhibit B: the difference split by one column, largest share first. */
export function demoBreakdown(column: DemoColumn, rows: DemoRow[] = DEMO_ROWS) {
  const changes = demoChanges(rows);
  const absTotal = decimalSum(changes.map(c => c.delta.replace('-', '')));
  const values = [...new Set(rows.map(r => r[column]))];
  return values.map(value => {
    const group = rows.filter(r => r[column] === value);
    const moved = changes.filter(c => c.row[column] === value);
    const change = moved.length ? decimalSum(moved.map(c => c.delta)) : '0.00';
    return { value, change, records: group.length, changed: moved.length,
      share: Number(absTotal) ? Math.abs(Number(change)) / Number(absTotal) : 0, recordShare: group.length / rows.length };
  }).sort((a, b) => b.share - a.share || b.records - a.records);
}
