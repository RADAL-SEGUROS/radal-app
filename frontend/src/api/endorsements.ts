/**
 * Endorsements — every amendment the policy takes after issuance.
 *
 * The deltas CARRY A SIGN: an exclusion or a sum-insured decrease is negative,
 * and an administrative endorsement (pledge update, policyholder change) is
 * all-zero — valid, not an error. The arithmetic is the house one, applied to
 * the deltas: `net = taxable + exempt`, `vat = 0.19 * taxable`,
 * `total = net + vat`.
 *
 * Issuing is the transactional moment: the server attaches the carrier's
 * document AND applies the deltas to the policy and to the collection plan in
 * one transaction, which is why `useIssueEndorsement` invalidates all three.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import api from "@/lib/api";
import { clean, qk } from "@/api/keys";
import type {
  Endorsement,
  EndorsementBatchCreate,
  EndorsementBatchIssueRequest,
  EndorsementBatchResponse,
  EndorsementCreate,
  EndorsementIssueRequest,
  EndorsementIssueResult,
  EndorsementStatus,
  EndorsementUpdate,
  ScopeParams,
} from "@/api/types";

export interface EndorsementPage {
  items: Endorsement[];
  total: number;
  limit: number;
  offset: number;
}

export interface EndorsementListParams extends ScopeParams {
  policy_id?: number;
  /** Single value — see the note in `api/policies.ts`. */
  status?: EndorsementStatus;
  /** The N members of one prórroga (spec v3 §4.3). */
  batch_key?: string;
  limit?: number;
  offset?: number;
}

export function useEndorsements(params: EndorsementListParams = {}, enabled = true) {
  return useQuery({
    queryKey: qk.endorsements.list(params),
    enabled,
    queryFn: async () => {
      const { data } = await api.get<EndorsementPage>("/endorsements", {
        params: clean(params),
      });
      return data;
    },
  });
}

export function useEndorsement(endorsementId: number | undefined) {
  return useQuery({
    queryKey: qk.endorsements.detail(endorsementId ?? 0),
    enabled: !!endorsementId,
    queryFn: async () => {
      const { data } = await api.get<Endorsement>(`/endorsements/${endorsementId}`);
      return data;
    },
  });
}

/**
 * One endorsement on one policy.
 *
 * `kind` may be any motive EXCEPT the batchable ones
 * (`BATCHABLE_ENDORSEMENT_KINDS` — today just `period_extension`): the server
 * refuses those here with a 422, because a prórroga is a manual multi-select
 * over N policies and is written only by `useBatchEndorsements` (rule 3).
 * Filter the motive dropdown with that constant rather than a literal.
 */
export function useCreateEndorsement() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload: EndorsementCreate) => {
      const { data } = await api.post<Endorsement>("/endorsements", payload);
      return data;
    },
    onSuccess: (data) => {
      void qc.invalidateQueries({ queryKey: qk.endorsements.all });
      void qc.invalidateQueries({ queryKey: qk.policies.detail(data.policy_id) });
    },
  });
}

export function useUpdateEndorsement(endorsementId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload: EndorsementUpdate) => {
      const { data } = await api.patch<Endorsement>(
        `/endorsements/${endorsementId}`,
        payload,
      );
      return data;
    },
    onSuccess: (data) => {
      qc.setQueryData(qk.endorsements.detail(endorsementId), data);
      void qc.invalidateQueries({ queryKey: qk.endorsements.lists() });
      void qc.invalidateQueries({ queryKey: qk.policies.detail(data.policy_id) });
    },
  });
}

export function useDeleteEndorsement() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (endorsementId: number) => {
      await api.delete(`/endorsements/${endorsementId}`);
      return endorsementId;
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.endorsements.all });
      void qc.invalidateQueries({ queryKey: qk.policies.all });
    },
  });
}

/**
 * Issue: attach the carrier's document and commit the deltas.
 *
 * One transaction server-side moves the endorsement, the policy premium and
 * insured amount, and the collection plan total — so all three caches drop.
 */
export function useIssueEndorsement(endorsementId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload: EndorsementIssueRequest = {}) => {
      const { data } = await api.post<EndorsementIssueResult>(
        `/endorsements/${endorsementId}/issue`,
        payload,
      );
      return data;
    },
    onSuccess: (result) => {
      qc.setQueryData(qk.endorsements.detail(endorsementId), result.endorsement);
      void qc.invalidateQueries({ queryKey: qk.endorsements.all });
      void qc.invalidateQueries({ queryKey: qk.policies.all });
      void qc.invalidateQueries({ queryKey: qk.collections.all });
      void qc.invalidateQueries({ queryKey: qk.caseFiles.all });
      // The group tree renders the endorsement leaf and the policy's dates.
      void qc.invalidateQueries({ queryKey: qk.accountGroups.all });
      void qc.invalidateQueries({ queryKey: qk.navigator.all });
    },
  });
}

// =============================================================================
// Prórroga — the batch endorsement (spec v3 §4.3, rule 3)
// =============================================================================
//
// A prórroga is a MANUAL MULTI-SELECT: the broker picks the policies, the
// server never derives them from a period. Nothing is pre-selected in the UI.
//
// It moves `policy.end_date` (and `period_end_at`) only — never the account
// folder's vigencia, which is why every member carries zero premium deltas and
// the collection ledger is left alone. Extending a policy is therefore NOT the
// same act as changing a vigencia: that one is `POST /case-files/{id}/reperiod`.

/** The members of one batch, so the tree can render it once with N chips. */
export function useEndorsementBatch(batchKey: string | undefined, enabled = true) {
  return useQuery({
    queryKey: qk.endorsements.batch(batchKey ?? ""),
    enabled: !!batchKey && enabled,
    queryFn: async () => {
      const { data } = await api.get<EndorsementPage>("/endorsements", {
        params: clean({ batch_key: batchKey, limit: 200 }),
      });
      return data;
    },
  });
}

/**
 * Fan one period extension out over N policies of the SAME group: N
 * endorsements + N `case_file(kind=endorsement)` children sharing one
 * server-minted `batch_key`.
 *
 * 422 `policies_span_groups` when the selection crosses groups — surface it as
 * a field error on the multi-select, not as a toast.
 *
 * This CREATES the endorsements; nothing is applied to a policy until
 * `useIssueBatch` runs.
 */
export function useBatchEndorsements() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload: EndorsementBatchCreate) => {
      const { data } = await api.post<EndorsementBatchResponse>(
        "/endorsements/batch",
        payload,
      );
      return data;
    },
    onSuccess: (result) => {
      void qc.invalidateQueries({ queryKey: qk.endorsements.all });
      // One new post-sale child case per policy.
      void qc.invalidateQueries({ queryKey: qk.caseFiles.all });
      void qc.invalidateQueries({ queryKey: qk.policies.all });
      void qc.invalidateQueries({ queryKey: qk.accountGroups.all });
      void qc.invalidateQueries({ queryKey: qk.navigator.all });
      void qc.invalidateQueries({ queryKey: qk.endorsements.batch(result.batch_key) });
    },
  });
}

/**
 * Issue every member of the batch and apply the extension once per policy.
 *
 * The carrier normally sends ONE document for the whole prórroga, so
 * `issued_document_id` is shared by all members. The response echoes each
 * policy's new `policy_end_date`, which is what the tree's "prorrogada hasta …"
 * caption reads.
 */
export function useIssueBatch(batchKey: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload: EndorsementBatchIssueRequest = {}) => {
      const { data } = await api.post<EndorsementBatchResponse>(
        `/endorsements/batch/${batchKey}/issue`,
        payload,
      );
      return data;
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.endorsements.all });
      void qc.invalidateQueries({ queryKey: qk.endorsements.batch(batchKey) });
      // `policy.end_date` moved on every member; the ledger did not, but the
      // collection view reads the policy, so it is refreshed too.
      void qc.invalidateQueries({ queryKey: qk.policies.all });
      void qc.invalidateQueries({ queryKey: qk.collections.all });
      void qc.invalidateQueries({ queryKey: qk.caseFiles.all });
      void qc.invalidateQueries({ queryKey: qk.accountGroups.all });
      void qc.invalidateQueries({ queryKey: qk.navigator.all });
    },
  });
}
