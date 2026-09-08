/**
 * Policies — the post-sale root.
 *
 * The policy is the mirror of the accepted issuance proposal: everything that
 * happens after the award (endorsements, collection, warranties, claims) hangs
 * off this row. Two rules the UI must respect:
 *
 *  - the contractual period is a DATETIME (`period_start_at` / `period_end_at`,
 *    the 12:00 convention). `start_date` / `end_date` are the legacy dates the
 *    server keeps in sync — never render them instead of the datetimes;
 *  - money follows the house arithmetic: `net = taxable + exempt`,
 *    `vat = 0.19 * taxable` (NOT on net), `total = net + vat`.
 *
 * Query keys and types are published by `@/api/keys` and `@/api/types` — this
 * module never invents one.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import api from "@/lib/api";
import { clean, qk } from "@/api/keys";
import type {
  MirrorDiff,
  MirrorDiffQueueRequest,
  MirrorDiffQueueResult,
  Policy,
  PolicyCreate,
  PolicyFromProposalRequest,
  PolicyStatus,
  PolicySummary,
  PolicyUpdate,
  Warranty,
  WarrantyCreate,
} from "@/api/types";

/** `GET /policies` — the server sends an offset page. */
export interface PolicyPage {
  items: Policy[];
  total: number;
  limit: number;
  offset: number;
}

export interface PolicyListParams {
  client_id?: number;
  insurer_id?: number;
  placement_id?: number;
  case_file_id?: number;
  /** Groups & accounts (spec v3 §4.3) — the broker-private Group. */
  account_group_id?: number;
  /** Single value: `lib/api.ts` sets no array param serializer, and the
   *  server reads repeated `status=` keys, not `status[]=`. */
  status?: PolicyStatus;
  q?: string;
  limit?: number;
  offset?: number;
}

export function usePolicies(params: PolicyListParams = {}, enabled = true) {
  return useQuery({
    queryKey: qk.policies.list(params),
    enabled,
    queryFn: async () => {
      const { data } = await api.get<PolicyPage>("/policies", {
        params: clean(params),
      });
      return data;
    },
  });
}

/** `GET /policies/summary` — broker-scoped counts for the analytics dashboard.
 *  Gate on `Policies.View` via `enabled`; the server 403s without it. */
export function usePoliciesSummary(enabled = true) {
  return useQuery({
    queryKey: qk.policies.summary(),
    enabled,
    queryFn: async () => {
      const { data } = await api.get<PolicySummary>("/policies/summary");
      return data;
    },
  });
}

export function usePolicy(policyId: number | undefined) {
  return useQuery({
    queryKey: qk.policies.detail(policyId ?? 0),
    enabled: !!policyId,
    queryFn: async () => {
      const { data } = await api.get<Policy>(`/policies/${policyId}`);
      return data;
    },
  });
}

export function useCreatePolicy() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload: PolicyCreate) => {
      const { data } = await api.post<Policy>("/policies", payload);
      return data;
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.policies.all });
      void qc.invalidateQueries({ queryKey: qk.caseFiles.all });
    },
  });
}

/**
 * The mirror baseline: a draft policy built from the accepted proposal, so the
 * carrier's issued document can be diffed against what was actually agreed.
 */
export function useCreatePolicyFromProposal() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload: PolicyFromProposalRequest) => {
      const { data } = await api.post<Policy>("/policies/from-proposal", payload);
      return data;
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.policies.all });
      void qc.invalidateQueries({ queryKey: qk.proposals.all });
      void qc.invalidateQueries({ queryKey: qk.caseFiles.all });
    },
  });
}

export function useUpdatePolicy(policyId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload: PolicyUpdate) => {
      const { data } = await api.patch<Policy>(`/policies/${policyId}`, payload);
      return data;
    },
    onSuccess: (data) => {
      qc.setQueryData(qk.policies.detail(policyId), data);
      void qc.invalidateQueries({ queryKey: qk.policies.lists() });
      // The period and the premium feed the collection ledger's expected total.
      void qc.invalidateQueries({ queryKey: qk.collections.all });
    },
  });
}

export function useDeletePolicy() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (policyId: number) => {
      await api.delete(`/policies/${policyId}`);
      return policyId;
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.policies.all });
    },
  });
}

// --- Mirror-diff ---------------------------------------------------------------

/**
 * Field-by-field diff of the confirmed issuance proposal against the confirmed
 * policy. Not an LLM feature — `services/mirror.py` aligns coverages by number,
 * deductibles by peril key and money on the five premium fields.
 */
export function useMirrorDiff(policyId: number | undefined, enabled = true) {
  return useQuery({
    queryKey: qk.policies.mirrorDiff(policyId ?? 0),
    enabled: !!policyId && enabled,
    queryFn: async () => {
      const { data } = await api.get<MirrorDiff>(`/policies/${policyId}/mirror-diff`);
      return data;
    },
  });
}

/** Turn selected diff rows into `endorsement(status=draft)` — human confirms later. */
export function useQueueMirrorDiff(policyId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload: MirrorDiffQueueRequest = {}) => {
      const { data } = await api.post<MirrorDiffQueueResult>(
        `/policies/${policyId}/mirror-diff/queue`,
        payload,
      );
      return data;
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.policies.mirrorDiff(policyId) });
      void qc.invalidateQueries({ queryKey: qk.endorsements.all });
      void qc.invalidateQueries({ queryKey: qk.policies.detail(policyId) });
    },
  });
}

// --- Warranties (R-n / G-n / M-n) ----------------------------------------------

export function usePolicyWarranties(policyId: number | undefined) {
  return useQuery({
    queryKey: qk.policies.warranties(policyId ?? 0),
    enabled: !!policyId,
    queryFn: async () => {
      const { data } = await api.get<Warranty[]>(`/policies/${policyId}/warranties`);
      return data;
    },
  });
}

export function useCreateWarranty(policyId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload: WarrantyCreate) => {
      const { data } = await api.post<Warranty>(
        `/policies/${policyId}/warranties`,
        payload,
      );
      return data;
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.policies.warranties(policyId) });
      void qc.invalidateQueries({ queryKey: qk.policies.detail(policyId) });
      void qc.invalidateQueries({ queryKey: qk.warranties.all });
    },
  });
}
