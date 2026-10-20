import type { ChangeCategory, Result } from '../../api';

export function evidenceCountLabel(count: number, changesTotal = false): string {
  return `${count.toLocaleString('en-US')} record${count === 1 ? '' : 's'}${changesTotal ? (count === 1 ? ' that changes the total' : ' that change the total') : ''}`;
}

/** Run summary counts all arrivals/departures; evidence categories count non-zero contributions. */
export function twoLoadCountNotes(summary: Result['change_summary'], categories: Pick<ChangeCategory, 'key' | 'records'>[] | null): string[] {
  if (!summary || categories === null) return [];
  const labels = { added: 'new records in the later load', removed: 'records removed from the later load', value_changed: 'records changed value',
    entered_filter: 'records now match the filter', left_filter: 'records no longer match the filter' };
  return (Object.keys(labels) as Array<keyof typeof labels>).flatMap(key => {
    const total = summary[key]?.records;
    if (!total) return [];
    const affecting = categories.find(category => category.key === key)?.records ?? 0;
    if (affecting > total) return []; // inconsistent metadata must not manufacture a count
    const label = total === 1 ? labels[key].replace('records','record').replace('now match','now matches').replace('no longer match','no longer matches') : labels[key];
    return [`${total.toLocaleString('en-US')} ${label}; ${affecting.toLocaleString('en-US')} ${affecting === 1 ? 'changes' : 'change'} the total${affecting < total ? ' (the others are outside the calculation, have no amount, or contribute zero)' : ''}.`];
  });
}

export function evidenceSealLabel(saved: boolean, status?: 'verifying' | 'intact' | 'broken' | null): string {
  if (!saved) return 'EVIDENCE READY';
  return status === 'intact' ? 'Seal intact' : status === 'broken' ? 'Seal broken' : status === 'verifying' ? 'Verifying' : 'SEALED';
}
