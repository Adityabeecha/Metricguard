/** Technical upload policy shared by the wizard and its regression checks. */
export type UploadLimits = { max_rows: number | null; max_upload_mb: number | null };
export const DEFAULT_COMPOUND_FILTERS = true;
const count = (value: number) => value.toLocaleString('en-US');

export function uploadLimitText(limits: UploadLimits): string {
  const pieces = [limits.max_upload_mb == null ? '' : `${count(limits.max_upload_mb)} MB`,
    limits.max_rows == null ? '' : `${count(limits.max_rows)} rows`].filter(Boolean);
  return pieces.length ? `Up to ${pieces.join(' and ')} on the free beta`
    : 'Drop a CSV (up to 512 MiB), Parquet or Excel (.xlsx) file';
}
export function previewRowLimitMessage(limits: Pick<UploadLimits, 'max_rows'>, rows: number, exact = true): string {
  if (limits.max_rows == null || rows <= limits.max_rows) return '';
  const amount = exact ? count(rows) : `more than ${count(limits.max_rows)}`;
  return `Free beta limit: ${count(limits.max_rows)} rows per dataset (this file has ${amount}).`;
}
export function canConfirmUpload(previewReady: boolean, busy: boolean, problem: string): boolean {
  return previewReady && !busy && !problem;
}
export function defaultFlatImport(compoundFilters = DEFAULT_COMPOUND_FILTERS): { version: 4 | 5; predicate_policy?: 'flat-boolean-v1' } {
  return compoundFilters ? { version: 5, predicate_policy: 'flat-boolean-v1' } : { version: 4 };
}
export function loadLabelAfterFilePick(current: string, filename: string, newerLoad: boolean): string {
  return newerLoad || current.trim() ? current : filename.replace(/\.(csv|parquet|xlsx)$/i, '');
}
export function inheritedUnit(snapshot: {unit_label?:string|null;currency?:string|null} | null,
                              contract: {unit_label?:string} | null, entered: string): string {
  return snapshot?.unit_label || contract?.unit_label || entered || snapshot?.currency || '';
}
export function datasetUsageLabel(calculations: number, runs: number): string {
  return `Usage (${count(calculations)} saved calculation${calculations === 1 ? '' : 's'} / ${count(runs)} run${runs === 1 ? '' : 's'})`;
}
