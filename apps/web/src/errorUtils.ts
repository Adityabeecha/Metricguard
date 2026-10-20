/**
 * User-facing Error Translation for MetricGuard.
 * Translates raw backend codes and exceptions into actionable analyst explanations.
 */

export interface HumanizedError {
  title: string;
  message: string;
  suggestion?: string;
  field?: string;
  code?: string;
}

export function humanizeError(
  err: unknown,
  context?: { key?: string; measure?: string; filename?: string }
): HumanizedError {
  let rawCode = '';
  let rawMsg = '';

  if (typeof err === 'object' && err !== null) {
    if ('code' in err && typeof (err as { code: unknown }).code === 'string') {
      rawCode = (err as { code: string }).code.toUpperCase();
    }
    if ('message' in err && typeof (err as { message: unknown }).message === 'string') {
      rawMsg = (err as { message: string }).message;
    }
  } else if (typeof err === 'string') {
    rawMsg = err;
  }

  // Extract error code if embedded in error message or status code
  if (!rawCode && rawMsg) {
    const match = rawMsg.match(/\b(ROW_KEY_NOT_UNIQUE|DUPLICATE_KEY|NULL_IN_ROW_KEY|NULL_KEY|MEASURE_NOT_NUMERIC|INVALID_MEASURE|INVALID_SCHEMA|UPLOAD_LIMIT|UNSUPPORTED_FORMAT|INVALID_CONTRACT|ISOLATION_REQUIRED)\b/i);
    if (match) {
      rawCode = match[1].toUpperCase();
    } else if (/isolation|supervisor|runner|docker/i.test(rawMsg)) {
      rawCode = 'ISOLATION_REQUIRED';
    } else if (rawMsg.includes('413') || /payload too large/i.test(rawMsg)) {
      rawCode = 'UPLOAD_LIMIT';
    }
  }

  // Handle known error codes
  switch (rawCode) {
    case 'FREE_TIER_ROWS':
    case 'FREE_TIER_UPLOAD':
    case 'FREE_TIER_ACTIVE_RUNS':
    case 'FREE_TIER_DAILY_RUNS':
      return { title: 'Free beta limit', message: rawMsg, code: rawCode };
    case 'ROW_KEY_NOT_UNIQUE':
    case 'DUPLICATE_KEY':
      return {
        title: 'Row Key Not Unique',
        message: `The selected row key "${context?.key || 'column'}" contains duplicate values. Every row in your dataset must have a unique identifier.`,
        suggestion: 'Select a column with distinct values per row, or deduplicate the extract before uploading.',
        field: context?.key || 'row_key',
        code: rawCode,
      };

    case 'NULL_IN_ROW_KEY':
    case 'NULL_KEY':
      return {
        title: 'Missing Key Values',
        message: `The selected row key "${context?.key || 'column'}" contains empty or NULL entries.`,
        suggestion: 'Ensure the key column has no null values across all rows.',
        field: context?.key || 'row_key',
        code: rawCode,
      };

    case 'MEASURE_NOT_NUMERIC':
    case 'INVALID_MEASURE':
      return {
        title: 'Non-numeric Measure Field',
        message: `The selected measure "${context?.measure || 'column'}" contains text or non-numeric values.`,
        suggestion: 'Choose a column that contains purely decimal or integer numbers.',
        field: context?.measure || 'measure',
        code: rawCode,
      };

    case 'INVALID_SCHEMA':
      return {
        title: 'Data Configuration Mismatch',
        message: "This file doesn't match the selected data configuration.",
        suggestion: 'Verify that the column names in your confirmation match the columns present in the file.',
        code: rawCode,
      };

    case 'UPLOAD_LIMIT':
      return {
        title: 'File Size Limit Exceeded',
        message: 'The selected file exceeds the upload limit: 512 MiB for one-table CSV or Parquet files, 25 MiB for other uploads.',
        suggestion: 'Upload a smaller extract, or prepare a narrower one (fewer rows or columns).',
        code: rawCode,
      };

    case 'UNSUPPORTED_FORMAT':
      return {
        title: 'Unsupported File Format',
        message: 'MetricGuard accepts CSV (.csv) and Parquet (.parquet) files.',
        suggestion: 'Please convert your extract to CSV or Parquet format.',
        code: rawCode,
      };

    case 'INVALID_CONTRACT':
      return {
        title: 'Configuration Issue',
        message: rawMsg || 'The dataset configuration does not match the file format or null policy.',
        // Suggest the fix that matches the actual problem, not a generic one.
        suggestion: /distinct|identifier|duplicate column/i.test(rawMsg)
          ? 'Each column can have only one role: the row key, the amount and the filter columns must all be different columns.'
          : /null/i.test(rawMsg)
          ? 'Check the blank-value handling under Advanced details: blank cells for CSV, native NULLs for Parquet.'
          : /precision|scale|decimal/i.test(rawMsg)
          ? 'Check the decimal precision and places under Advanced details.'
          : 'Go back to the previous step and review your answers; the message above names the problem.',
        code: rawCode,
      };

    case 'INVALID_KEYS':
      return /blank|null|missing|empty/i.test(rawMsg)
        ? { title: 'Missing Key Values', message: rawMsg, suggestion: 'Choose a row key column with a value in every row.', field: context?.key || 'row_key', code: rawCode }
        : { title: 'Row Key Not Unique', message: rawMsg, suggestion: 'Choose a column with a different value in every row, or remove duplicate rows before uploading.', field: context?.key || 'row_key', code: rawCode };

    case 'ISOLATION_REQUIRED':
      return {
        title: 'Processing unavailable',
        message: 'Data validation is temporarily unavailable. Check system status or try again.',
        suggestion: 'Check system status in Settings or try again shortly.',
        code: rawCode,
      };

    default:
      // Pattern match raw message if code wasn't standard
      if (/duplicate/i.test(rawMsg)) {
        return {
          title: 'Duplicate Identifier',
          message: rawMsg,
          suggestion: 'Ensure row identifiers are strictly unique.',
          code: rawCode || 'DUPLICATE',
        };
      }
      if (/null policy/i.test(rawMsg)) {
        return {
          title: 'Format and Null Policy Mismatch',
          message: 'The file format does not match the configured NULL handling policy.',
          suggestion: 'Use "blank_nullable" for CSV files or "native_null" for Parquet files.',
          code: rawCode || 'NULL_POLICY_MISMATCH',
        };
      }

      return {
        title: 'Data Validation Notice',
        message: rawMsg || 'An unexpected issue occurred while validating the data source.',
        code: rawCode || 'VALIDATION_ERROR',
      };
  }
}
