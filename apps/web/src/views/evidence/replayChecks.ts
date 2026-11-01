// What "Replay & verify" compared, in plain words. Pure, so node tests can import it.

type Artifact = { sha256?: string; row_count?: number; delta_sum?: string };
type ResultLike = {
  baseline_value: string | null; target_value: string | null; delta_target_minus_baseline: string | null; residual: string | null;
  ratio?: { baseline: { exact: string | null }; target: { exact: string | null }; gap_exact?: string | null } | null;
  evidence_artifact?: Artifact | null;
};
export type ReplayCheck = { label: string; saved: string; replayed: string; same: boolean };

const shown = (value: string | number | null | undefined) => value == null || value === '' ? '—' : String(value);

/** One line per compared fact; identical exact strings count as the same, nothing is rounded. */
export function replayChecks(saved: ResultLike, replayed: ResultLike): ReplayCheck[] {
  const a = (saved.evidence_artifact || {}) as Artifact, b = (replayed.evidence_artifact || {}) as Artifact;
  const rows: Array<[string, string | number | null | undefined, string | number | null | undefined]> = [
    ['Current value', saved.baseline_value, replayed.baseline_value],
    ['Proposed value', saved.target_value, replayed.target_value],
    ['Total difference', saved.delta_target_minus_baseline, replayed.delta_target_minus_baseline],
    ['Unexplained remainder', saved.residual, replayed.residual],
  ];
  if (saved.ratio || replayed.ratio) {
    rows.push(['Current ratio (exact)', saved.ratio?.baseline.exact, replayed.ratio?.baseline.exact],
      ['Proposed ratio (exact)', saved.ratio?.target.exact, replayed.ratio?.target.exact],
      ['Ratio gap (exact)', saved.ratio?.gap_exact, replayed.ratio?.gap_exact]);
  }
  if (a.row_count != null || b.row_count != null) rows.push(['Records in the evidence', a.row_count, b.row_count]);
  if (a.sha256 || b.sha256) rows.push(['Evidence fingerprint (SHA-256)', a.sha256?.slice(0, 12), b.sha256?.slice(0, 12)]);
  return rows.map(([label, x, y]) => ({ label, saved: shown(x), replayed: shown(y), same: shown(x) === shown(y) }));
}
