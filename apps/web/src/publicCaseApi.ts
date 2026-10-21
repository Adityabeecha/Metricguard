import { ApiError, evidenceGroupParams, type PublicCaseDetail } from './api';
import type { EvidenceDataClient } from './views/evidence/evidenceClient';

/** Capability requests deliberately carry no workspace/key/session credentials. */
async function read<T>(token: string, suffix = ''): Promise<T> {
  const response = await fetch(`/api/v1/shared-cases/${encodeURIComponent(token)}${suffix}`, {
    credentials: 'omit', cache: 'no-store', referrerPolicy: 'no-referrer',
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new ApiError(body.error?.code || `HTTP_${response.status}`, response.status === 404
      ? 'This shared case is unavailable. The link may have expired or been revoked.'
      : 'The shared case could not be loaded. Try again.');
  }
  return response.json();
}

export const publicCase = (token: string) => read<PublicCaseDetail>(token);

/** Every method closes over the same token; the caller's run id is never sent. */
export function publicEvidenceClient(token: string): EvidenceDataClient {
  return {
    evidenceSummary: () => read(token, '/evidence-summary'),
    evidenceView: (_id, view) => {
      const params = new URLSearchParams({ offset: String(view.offset), limit: String(view.limit) });
      if (view.changedOnly) params.set('changed_only', 'true');
      if (view.withContext) params.set('with_context', 'true');
      if (view.category) params.set('category', view.category);
      if (view.q) params.set('q', view.q);
      if (view.order) params.set('order', view.order);
      return read(token, `/evidence?${params}`);
    },
    evidenceBreakdown: (_id, column, top) => {
      const params = new URLSearchParams();
      if (column) params.set('column', column);
      if (top) params.set('top', String(top));
      return read(token, `/evidence-breakdown?${params}`);
    },
    evidenceGroup: (_id, view) => read(token, `/evidence-group?${evidenceGroupParams(view)}`),
    evidenceConcentration: () => read(token, '/evidence-concentration'),
    entityRecords: (_id, entity, offset = 0, limit = 100) => read(token, `/entity-records?${new URLSearchParams({ entity, offset: String(offset), limit: String(limit) })}`),
  };
}
