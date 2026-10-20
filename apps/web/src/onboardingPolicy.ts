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

/** Retain an early selection until its workspace's limits arrive, with one preview per pick. */
export function createUploadPreviewQueue<T>(preview: (file: T, limits: UploadLimits, project: string, isCurrent: () => boolean) => Promise<void>) {
  let selected: {file:T;project:string;generation:number} | null = null;
  let limits: {value:UploadLimits;project:string} | null = null;
  let generation = 0;
  let started = -1;
  const run = () => {
    const current = selected;
    if (!current || !limits || current.project !== limits.project || started === current.generation) return;
    started = current.generation;
    void preview(current.file, limits.value, current.project, () => generation === current.generation);
  };
  return {
    select(file:T, project:string) { selected = {file,project,generation:++generation}; run(); },
    setLimits(value:UploadLimits, project:string) { limits = {value,project}; run(); },
    cancel() { generation++; selected = null; },
  };
}

export function suggestUploadUnit(amountColumn:string, sourceUnit = '', source?:string, lastUnit = ''): {unit:string;source:string|undefined} {
  if (sourceUnit.trim()) return {unit:sourceUnit.trim(),source};
  const currency = amountColumn.match(/(?:^|[^a-z])(USD|EUR|INR|GBP|JPY|CAD|AUD|CHF|CNY|BRL)(?=$|[^a-z])/i)?.[1];
  if (currency) return {unit:currency.toUpperCase(),source:`Column name “${amountColumn}”`};
  return lastUnit.trim() ? {unit:lastUnit.trim(),source:'Last unit used in this workspace'} : {unit:'',source:undefined};
}

type LoadContract = {row_key:string;measure?:string;decimal_columns?:Array<{name:string;scale?:number;unit_label?:string}>;categories:string[];dimension_types?:Record<string,string>;nullable_categories?:string[];scale:number;unit_label:string};
type LoadPreview = {headers:string[];observed_types:Record<string,string>;null_counts?:Record<string,number>;totalRowsSampled:number;key_issues?:Record<string,string>;sample_rows:Array<Record<string,string|null>>};
/** A match is a suggestion only: the analyst must still choose to reuse its meaning. */
export function loadCompatibilityProblem(preview:LoadPreview, contract:LoadContract):string {
  const amounts = contract.decimal_columns?.length ? contract.decimal_columns : [{name:contract.measure || '',scale:contract.scale,unit_label:contract.unit_label}];
  const columns = [contract.row_key,...amounts.map(d => d.name),...contract.categories];
  const sameColumns = columns.length === preview.headers.length && new Set(columns).size === columns.length && columns.every(c => preview.headers.includes(c));
  if (!sameColumns) return 'A newer load must have the same columns as this dataset.';
  if (preview.key_issues?.[contract.row_key]) return `Row ID “${contract.row_key}” ${preview.key_issues[contract.row_key]}.`;
  const allBlank = (column:string) => preview.totalRowsSampled > 0 && preview.null_counts?.[column] === preview.totalRowsSampled;
  for (const amount of amounts) {
    if (!allBlank(amount.name) && preview.observed_types[amount.name] !== 'numeric') return `Amount “${amount.name}” must contain numbers.`;
    const explicitUnit = suggestUploadUnit(amount.name).unit;
    if (explicitUnit && explicitUnit !== (amount.unit_label || contract.unit_label)) return `Amount “${amount.name}” uses a different unit from this dataset.`;
    const allowedScale = amount.scale ?? contract.scale;
    if (preview.sample_rows.some(row => (row[amount.name]?.split('.')[1]?.length || 0) > allowedScale)) return `Amount “${amount.name}” needs more decimal places than this dataset allows.`;
  }
  for (const column of contract.categories) {
    const required = contract.dimension_types?.[column] || 'string';
    if (required !== 'string' && !allBlank(column) && preview.observed_types[column] !== required) return `Filter column “${column}” must use ${required} values.`;
    if ((preview.null_counts?.[column] || 0) > 0 && !contract.nullable_categories?.includes(column)) return `Filter column “${column}” does not allow blanks.`;
  }
  const currencyColumn = preview.headers.find(c => /^(currency|currency_code)$/i.test(c));
  if (currencyColumn && preview.sample_rows.some(row => row[currencyColumn] && row[currencyColumn] !== contract.unit_label)) return 'The file currency differs from the existing dataset unit.';
  return '';
}

export async function completeDatasetUpload(snapshotId:string, destination:'compare'|'datasets', onCompare:(id:string)=>void|Promise<void>, onDatasets:(id:string)=>void|Promise<void>):Promise<void> {
  await (destination === 'compare' ? onCompare(snapshotId) : onDatasets(snapshotId));
}
