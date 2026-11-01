import type { ChangeCategory } from '../../api';

export type CategoryKey = ChangeCategory['key'];

/** How each change category reads for this kind of comparison (see docs/design/evidence-ui-spec.md). */
export function categoryLabel(key: CategoryKey, opts: { loads?: boolean; distinct?: boolean; sources?: boolean }): string {
  if (opts.loads && opts.sources) {
    return {
      removed: 'Only in source A', added: 'Only in source B', value_changed: 'Value differs',
      entered_filter: 'Matches the filter only in B', left_filter: 'Matches the filter only in A', excluded: 'Excluded',
    }[key];
  }
  if (opts.loads) {
    return {
      removed: 'Gone from the later load', added: 'New in the later load', value_changed: 'Value changed',
      entered_filter: 'Now matches the filter', left_filter: 'No longer matches the filter', excluded: 'Excluded',
    }[key];
  }
  if (opts.distinct) {
    return { excluded: 'No longer counted', added: 'Newly counted', value_changed: 'Changed',
      removed: 'Removed', entered_filter: 'Entered', left_filter: 'Left' }[key];
  }
  return { excluded: 'Excluded by proposed', added: 'Added by proposed', value_changed: 'Value changed',
    removed: 'Removed', entered_filter: 'Entered', left_filter: 'Left' }[key];
}


/** Plain explanations for each category in the selected comparison. */
export function categoryDescription(key: CategoryKey, opts: { loads?: boolean; distinct?: boolean; sources?: boolean }): string {
  if (opts.loads && opts.sources) return {
    removed: 'The record exists only in source A.', added: 'The record exists only in source B.', value_changed: 'The same record has a different amount in each source.',
    entered_filter: 'The record matches the filter only in source B.', left_filter: 'The record matches the filter only in source A.', excluded: 'The record is excluded by the filter.',
  }[key];
  if (opts.loads) return {
    removed: 'The record exists only in the older file.', added: 'The record exists only in the newer file.', value_changed: 'The same record has a different amount in the later load.',
    entered_filter: 'The record now matches the filter.', left_filter: 'The record no longer matches the filter.', excluded: 'The record is excluded by the filter.',
  }[key];
  if (opts.distinct) return { excluded: 'The value is no longer counted.', added: 'The value is newly counted.', value_changed: 'The value changed.', removed: 'The value was removed.', entered_filter: 'The value entered the filter.', left_filter: 'The value left the filter.' }[key];
  return { excluded: 'The record counts in the current calculation but not the proposed one.', added: 'The record counts in the proposed calculation but not the current one.', value_changed: 'The record has a different amount in the two calculations.', removed: 'The record was removed.', entered_filter: 'The record entered the filter.', left_filter: 'The record left the filter.' }[key];
}
