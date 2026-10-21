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
