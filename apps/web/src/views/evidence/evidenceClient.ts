import { api } from '../../api';

/** Public cases supply a token-scoped reader; normal results retain their workspace API. */
export type EvidenceDataClient = Pick<typeof api, 'evidenceSummary'|'evidenceView'|'evidenceBreakdown'|'evidenceGroup'|'entityRecords'> & {
  evidenceConcentration?: (id:string) => Promise<import('../../api').EvidenceConcentration>;
};
export const workspaceEvidenceClient:EvidenceDataClient=api;
