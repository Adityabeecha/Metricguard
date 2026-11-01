import { decimalSum } from './exactDecimal.ts';

/** The public sample case MG-F3A3: the seeded synthetic orders (demo/closeout/sales_orders.csv) and its sealed hashes. */
export type DemoRow = { row_id: string; sales_amount: string | null; status: string; region: string | null; channel: string };

export type DemoCaseDef = {
  number: string; title: string; unit: string; dataset: string;
  current: { label: string; sql: string }; proposed: { label: string; sql: string };
  conclusion: string; sealedAt: string; replayedAt: string; limit: string;
  hashes: { dataset: string; current: string; proposed: string; evidence: string };
  inCurrent: (r: DemoRow) => boolean; inProposed: (r: DemoRow) => boolean;
  /** Rows are bundled (small case) or fetched from /samples (large case). */
  rowsUrl?: string;
};

export const DEMO_CASE: DemoCaseDef = {
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
  limit: 'This is a small illustration with eight synthetic orders. The main sample case uses 5,000.',
  inCurrent: r => r.sales_amount != null,
  inProposed: r => r.sales_amount != null && r.status === 'completed',
};

/**
 * The main public sample MG-8B79: 5,000 synthetic October orders (scripts/make_sample_case.py, fixed seed)
 * sealed and replayed by the real engine, which produced these totals and fingerprints. The data has one
 * planted cause: app-channel refunds in the second half of October run near 30% instead of 4%.
 */
export const LARGE_CASE: DemoCaseDef = {
  number: 'MG-8B79',
  title: 'Revenue: dashboard vs finance, October',
  unit: 'INR',
  dataset: 'Sample orders, October 2026',
  current: { label: 'Dashboard: every order except cancelled', sql: "SELECT SUM(amount) FROM snapshot WHERE status <> 'cancelled'" },
  proposed: { label: 'Finance: paid orders only', sql: "SELECT SUM(amount) FROM snapshot WHERE status = 'paid'" },
  conclusion: 'Finance is 21,23,763.59 INR lower because the dashboard also counts pending orders (11,31,056.43 INR) and refunded orders (9,92,707.16 INR). '
    + 'The app channel holds 51% of the difference but only 32% of orders: app refunds from 16 to 31 October alone are 31% of the gap, '
    + 'with a refund rate near 30% against 4% everywhere else. Check the app\'s refund sync for the second half of October.',
  sealedAt: '1 Nov 2026',
  replayedAt: '1 Nov 2026',
  hashes: {
    dataset: '647d236e5b769a29cc9811f3ee7934c399188b0560c85fda92e65f22bad7d6d4',
    current: '424f3c2b67145302ac97cb4773000521ad1703160a78a3dc23fa95cd9ab67b60',
    proposed: 'cf66b3be993f2617c32efd86d31d2523f2907f04f742a6534ac28a76720e4b9d',
    evidence: '12fc2d96a63c684cf2eb41783222f8af0eb9a7a37c5f6dc208637389403b8507',
  },
  limit: 'The orders are synthetic, with one planted cause, so the breakdown has something real to find. Your own cases work the same way on up to 50,000 rows on the free beta.',
  inCurrent: r => r.sales_amount != null && r.status !== 'cancelled',
  inProposed: r => r.sales_amount != null && r.status === 'paid',
  rowsUrl: '/samples/sample_case_orders.csv',
};

/** The sample shown at a /demo address: the large case unless the small MG-F3A3 is asked for. */
export function demoCaseFor(pathname: string): DemoCaseDef {
  return /mg-f3a3/i.test(pathname) ? DEMO_CASE : LARGE_CASE;
}

/** Rows of sample_case_orders.csv (order_id, order_date, region, channel, status, customer_id, amount). */
export function parseSampleCsv(text: string): DemoRow[] {
  const [head, ...lines] = text.trim().split(/\r?\n/);
  const at = Object.fromEntries(head.split(',').map((name, i) => [name, i]));
  return lines.map(line => {
    const cells = line.split(',');
    return { row_id: cells[at.order_id], sales_amount: cells[at.amount] || null, status: cells[at.status],
      region: cells[at.region] || null, channel: cells[at.channel] };
  });
}

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

const negate = (v: string) => v.startsWith('-') ? v.slice(1) : `-${v}`;

/** Per-record change, proposed minus current (exact decimal strings). */
export function demoChanges(rows: DemoRow[] = DEMO_ROWS, def: DemoCaseDef = DEMO_CASE) {
  return rows
    .filter(r => def.inCurrent(r) !== def.inProposed(r))
    .map(r => ({ row: r, current: r.sales_amount as string, proposed: def.inProposed(r) ? r.sales_amount : null,
      delta: def.inProposed(r) ? (r.sales_amount as string) : negate(r.sales_amount as string) }))
    .sort((a, b) => Math.abs(Number(b.delta)) - Math.abs(Number(a.delta)) || a.row.row_id.localeCompare(b.row.row_id));
}

/** Both totals, the change, and what the listed records leave unexplained. */
export function demoTotals(rows: DemoRow[] = DEMO_ROWS, def: DemoCaseDef = DEMO_CASE) {
  const current = decimalSum(rows.filter(def.inCurrent).map(r => r.sales_amount as string));
  const proposed = decimalSum(rows.filter(def.inProposed).map(r => r.sales_amount as string));
  const delta = decimalSum([proposed, negate(current)]);
  const listed = decimalSum(demoChanges(rows, def).map(c => c.delta));
  const residual = decimalSum([delta, negate(listed)]);
  return { current, proposed, delta, listed, residual, records: rows.length, blank: rows.filter(r => r.sales_amount == null).length };
}

/** Facts shown by the sample's saved/replayed checks; both use these same frozen rows. */
export function demoReplayResult(rows: DemoRow[] = DEMO_ROWS, def: DemoCaseDef = DEMO_CASE) {
  const totals = demoTotals(rows, def);
  return { baseline_value: totals.current, target_value: totals.proposed, delta_target_minus_baseline: totals.delta,
    residual: totals.residual, evidence_artifact: { row_count: totals.records, sha256: def.hashes.evidence } };
}

/** Exhibit B: the difference split by one column, largest share first. */
export function demoBreakdown(column: DemoColumn, rows: DemoRow[] = DEMO_ROWS, def: DemoCaseDef = DEMO_CASE) {
  const changes = demoChanges(rows, def);
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
