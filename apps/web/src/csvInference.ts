/**
 * Safe CSV Parsing and Meaning Inference for MetricGuard Data Sources.
 * Infers technical facts (types, key, measure, categories) without silently inventing business semantics.
 */

export interface ParsedCsvPreview {
  headers: string[];
  declared_types: Record<string, string | null>;
  observed_types: Record<string, string>;
  null_counts?: Record<string, number>;
  sample_rows: Array<Record<string, string | null>>;
  totalRowsSampled: number;
  /** Per column, why it cannot be a row key (checked over every row), e.g. 'repeats "A1"'. */
  key_issues?: Record<string, string>;
}

export interface InferredMeaning {
  rowKey: string;
  measure: string;
  categories: string[];
  precision: number;
  scale: number;
  unit: string;
  unitSource?: string;
  meaning: string;
  nullPolicy: 'blank_nullable' | 'native_null';
  candidateKeys: string[];
  candidateMeasures: string[];
  /** Set when no column is a safe row key; explains the chosen column's problem. */
  keyWarning?: string;
  candidateCategories: string[];
}

/**
 * Parses raw CSV text into a structured preview with headers, sample rows, and observed types.
 */
export function parseCsvText(text: string, maxSampleRows = 25, nullPolicy: 'blank_nullable' | 'literal_NULL' = 'blank_nullable'): ParsedCsvPreview {
  // Scan every record; retain only bounded samples. Preserve quoted newlines and whitespace.
  if (text.length > 25 * 1024 * 1024) throw new Error('Source exceeds 25 MiB');
  const records: string[][] = [];
  let row: string[] = [], cell = '', quoted = false, closed = false;
  text = text.replace(/^\uFEFF/, '');
  const finish = () => {
    row.push(cell);
    if (!(row.length === 1 && row[0] === '')) records.push(row);
    row = []; cell = ''; closed = false;
    // Matches the server's MAX_FLAT_ROWS (5,000,000 records plus the header row).
    if (records.length > 5_000_001) throw new Error('Flat snapshot exceeds 5,000,000 rows');
  };
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (quoted) {
      if (c === '"' && text[i + 1] === '"') { cell += '"'; i++; }
      else if (c === '"') { quoted = false; closed = true; }
      else cell += c;
    } else if (c === '"' && cell === '' && !closed) quoted = true;
    else if (c === ',') { row.push(cell); cell = ''; closed = false; }
    else if (c === '\r' || c === '\n') { if (c === '\r' && text[i + 1] === '\n') i++; finish(); }
    else {
      if (closed || c === '"') throw new Error('Invalid CSV quoting');
      cell += c;
    }
  }
  if (quoted) throw new Error('Unterminated CSV quote');
  if (cell !== '' || row.length || closed) finish();
  const headers = records.shift() || [];
  if (headers.some(h => !h) || new Set(headers).size !== headers.length) throw new Error('CSV headers are missing or duplicated');
  const sampleRows: Array<Record<string, string | null>> = [];
  const columnTypeCounts: Record<string, { num: number; date: number; timestamp: number; bool: number; str: number; total: number; nullCount: number; scaleMax: number }> = {};
  for (const h of headers) {
    columnTypeCounts[h] = { num: 0, date: 0, timestamp: 0, bool: 0, str: 0, total: 0, nullCount: 0, scaleMax: 0 };
  }
  // Row-key suitability over every row: stop tracking a column once it fails.
  const seen: Record<string, Set<string> | null> = Object.fromEntries(headers.map(h => [h, new Set<string>()]));
  const keyIssues: Record<string, string> = {};

  const isValidDate = (s: string) => {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(s)) return false;
    const [y, m, d] = s.split('-').map(Number);
    if (m < 1 || m > 12 || d < 1 || d > 31) return false;
    if (y < 1) return false;
    const dt = new Date(0); dt.setUTCFullYear(y, m - 1, d); dt.setUTCHours(0, 0, 0, 0);
    return dt.getUTCFullYear() === y && dt.getUTCMonth() === m - 1 && dt.getUTCDate() === d;
  };

  const isValidTimestamp = (s: string) => {
    if (!/^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?$/.test(s)) return false;
    return isValidDate(s.slice(0, 10)) && Number(s.slice(11, 13)) < 24 && Number(s.slice(14, 16)) < 60 && Number(s.slice(17, 19)) < 60;
  };

  for (const values of records) {
    if (values.length !== headers.length) throw new Error('Source row has the wrong field count');
    const row: Record<string, string | null> = {};
    headers.forEach((h, idx) => {
      const val = values[idx] === (nullPolicy === 'blank_nullable' ? '' : 'NULL') ? null : values[idx];
      row[h] = val;
      const seenValues = seen[h];
      if (seenValues) {
        if (val === null) { keyIssues[h] = 'has blank values'; seen[h] = null; }
        else if (seenValues.has(val)) { keyIssues[h] = `repeats "${val.length > 40 ? val.slice(0, 40) + '…' : val}"`; seen[h] = null; }
        else seenValues.add(val);
      }

      if (columnTypeCounts[h]) {
        if (val === null) {
          columnTypeCounts[h].nullCount++;
        } else {
          columnTypeCounts[h].total++;
          if (/^(?:true|false)$/.test(val)) {
            columnTypeCounts[h].bool++;
          } else if (/^-?(?:\d+|\d*\.\d+)$/.test(val)) {
            columnTypeCounts[h].num++;
            const dot = val.indexOf('.');
            if (dot !== -1) {
              const decPlaces = val.length - dot - 1;
              if (decPlaces > columnTypeCounts[h].scaleMax) {
                columnTypeCounts[h].scaleMax = decPlaces;
              }
            }
          } else if (isValidDate(val)) {
            columnTypeCounts[h].date++;
          } else if (isValidTimestamp(val)) {
            columnTypeCounts[h].timestamp++;
          } else {
            columnTypeCounts[h].str++;
          }
        }
      }
    });
    if (sampleRows.length < maxSampleRows) sampleRows.push(row);
  }

  const observedTypes: Record<string, string> = {};
  const declaredTypes: Record<string, string | null> = {};
  const nullCounts: Record<string, number> = {};

  for (const h of headers) {
    const counts = columnTypeCounts[h];
    nullCounts[h] = counts ? counts.nullCount : 0;
    if (!counts || counts.total === 0) {
      observedTypes[h] = 'string';
      declaredTypes[h] = 'VARCHAR';
    } else if (counts.bool === counts.total) {
      observedTypes[h] = 'boolean';
      declaredTypes[h] = 'BOOLEAN';
    } else if (counts.num === counts.total) {
      if (counts.scaleMax === 0) {
        observedTypes[h] = 'integer';
        declaredTypes[h] = 'BIGINT';
      } else {
        observedTypes[h] = 'numeric';
        declaredTypes[h] = `DECIMAL(18,${Math.min(counts.scaleMax || 2, 18)})`;
      }
    } else if (counts.date === counts.total) {
      observedTypes[h] = 'date';
      declaredTypes[h] = 'DATE';
    } else if (counts.timestamp === counts.total) {
      observedTypes[h] = 'timestamp';
      declaredTypes[h] = 'TIMESTAMP';
    } else {
      observedTypes[h] = 'string';
      declaredTypes[h] = 'VARCHAR';
    }
  }

  return {
    headers,
    declared_types: declaredTypes,
    observed_types: observedTypes,
    null_counts: nullCounts,
    sample_rows: sampleRows,
    totalRowsSampled: records.length,
    key_issues: keyIssues,
  };
}

/**
 * Safely infers recommended meaning for row_key, measure, and categories.
 * Strict rules:
 * - Does NOT invent currency or financial meaning silently.
 * - Confirms business meaning via returned suggestions.
 */
export function inferMeaning(
  headers: string[],
  observedTypes: Record<string, string>,
  sampleRows: Array<Record<string, string | null>> = [],
  filename = '',
  keyIssues?: Record<string, string>,
): InferredMeaning {
  const cleanHeaders = headers.map((h) => h.toLowerCase());

  // 1. Identify Candidate Keys. A key must be unique and never blank; when the
  // whole file was scanned (keyIssues), that is checked over every row.
  const keyNamePatterns = [/^(?:order_id|id|record_id|transaction_id|payment_id|sale_id|user_id|item_id)$/i, /_id$/i, /_key$/i, /^key$/i,
    /_(?:ref|no|number|code)$/i, /^(?:ref|reference|code)$/i];
  const nameMatch = (h: string) => keyNamePatterns.some(p => p.test(h));
  const typeOf = (h: string) => (observedTypes[h] || '').toLowerCase();
  const decimalLike = (h: string) => typeOf(h) === 'numeric' || typeOf(h).includes('decimal');
  const valid = (h: string) => {
    if (keyIssues) return !keyIssues[h];
    const values = sampleRows.map(r => r[h]).filter((v): v is string => v !== null && v !== '');
    return sampleRows.length > 0 && values.length === sampleRows.length && new Set(values).size === values.length;
  };
  const byName = [...headers].sort((a, b) => keyNamePatterns.findIndex(p => p.test(a)) - keyNamePatterns.findIndex(p => p.test(b)))
    .filter(nameMatch);
  const candidateKeys: string[] = [];
  const add = (h: string) => { if (!candidateKeys.includes(h)) candidateKeys.push(h); };
  byName.filter(h => valid(h) && !decimalLike(h)).forEach(add);
  // Unique string/integer columns that do not look like amounts.
  headers.filter(h => valid(h) && !decimalLike(h) && ['string', 'integer'].includes(typeOf(h))
    && !/amount|total|revenue|price|value|cost|profit|sales/i.test(h)).forEach(add);
  let keyWarning: string | undefined;
  if (candidateKeys.length === 0) {
    const fallback = byName[0] || headers[0];
    if (fallback) {
      add(fallback);
      const issue = keyIssues?.[fallback];
      keyWarning = issue ? `No column uniquely identifies every row. “${fallback}” ${issue}.` : undefined;
    }
  }
  const rowKey = candidateKeys[0] || '';

  // 2. Identify Candidate Measures
  const candidateMeasures: string[] = [];
  const measureNamePatterns = [
    /^(?:amount|order_amount|total|total_amount|price|revenue|net_revenue|sales|cost|profit|payment_value|sale_amount|refund_amount)$/i,
    /amount/i,
    /total/i,
    /revenue/i,
    /price/i,
    /value/i,
  ];

  for (const pattern of measureNamePatterns) {
    for (const h of headers) {
      if (h !== rowKey && pattern.test(h) && !candidateMeasures.includes(h)) {
        const type = (observedTypes[h] || '').toLowerCase();
        if (type === 'numeric' || type === 'number' || type === 'integer' || type.includes('decimal')) {
          candidateMeasures.push(h);
        }
      }
    }
  }

  // Check any remaining numeric columns
  for (const h of headers) {
    if (h !== rowKey && !candidateMeasures.includes(h)) {
      const type = (observedTypes[h] || '').toLowerCase();
      if (type === 'numeric' || type === 'number' || type.includes('decimal')) {
        candidateMeasures.push(h);
      }
    }
  }

  // Fallback: any integer column that isn't the key
  for (const h of headers) {
    if (h !== rowKey && !candidateMeasures.includes(h)) {
      const type = (observedTypes[h] || '').toLowerCase();
      if (type === 'integer') {
        candidateMeasures.push(h);
      }
    }
  }

  const measure = candidateMeasures[0] || '';

  // 3. Identify Candidate Categories
  const candidateCategories: string[] = [];
  const categoryNamePatterns = [/^(?:status|region|category|channel|state|type|payment_status|segment|country|tier)$/i, /status/i, /region/i, /category/i];

  for (const pattern of categoryNamePatterns) {
    for (const h of headers) {
      if (h !== rowKey && h !== measure && pattern.test(h) && !candidateCategories.includes(h)) {
        candidateCategories.push(h);
      }
    }
  }

  // Include other string/date columns up to limit of 8
  for (const h of headers) {
    if (h !== rowKey && h !== measure && !candidateCategories.includes(h)) {
      const type = (observedTypes[h] || '').toLowerCase();
      if (type === 'string' || type === 'timestamp' || type === 'date' || type === 'boolean') {
        candidateCategories.push(h);
      }
    }
  }

  const categories = candidateCategories.slice(0, 8);

  // 4. Default Precision & Scale
  let scale = 2;
  if (measure && sampleRows.length > 0) {
    let maxScale = 0;
    for (const row of sampleRows) {
      const val = row[measure];
      if (val && val.includes('.')) {
        const decimals = val.split('.')[1]?.length || 0;
        if (decimals > maxScale) maxScale = decimals;
      }
    }
    if (maxScale > 0) scale = Math.min(maxScale, 18);
  }

  // 5. Unit Label: only set if explicit unit metadata is present in trusted input
  let unit = '';
  let unitSource: string | undefined = undefined;

  if (cleanHeaders.includes('currency') || cleanHeaders.includes('unit')) {
    const colName = cleanHeaders.includes('currency') ? 'currency' : 'unit';
    const sampleVal = sampleRows.find((r) => r[colName])?.[colName];
    if (sampleVal && typeof sampleVal === 'string' && sampleVal.trim()) {
      unit = sampleVal.trim().toUpperCase();
      unitSource = `Source column "${colName}"`;
    }
  } else if (/_(usd|eur|gbp|inr|jpy|cad|aud)$/i.test(measure)) {
    const match = measure.match(/_(usd|eur|gbp|inr|jpy|cad|aud)$/i);
    if (match) {
      unit = match[1].toUpperCase();
      unitSource = `Column suffix "${match[0]}"`;
    }
  }

  const nullPolicy = filename.toLowerCase().endsWith('.parquet') ? 'native_null' : 'blank_nullable';

  return {
    rowKey,
    measure,
    categories,
    precision: 18,
    scale,
    unit,
    unitSource,
    meaning: measure ? `Row ${measure.replace(/_/g, ' ')}` : 'Row total',
    nullPolicy,
    candidateKeys,
    candidateMeasures,
    keyWarning,
    candidateCategories,
  };
}
