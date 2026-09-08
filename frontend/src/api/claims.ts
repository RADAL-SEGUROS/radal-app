/**
 * Claims — the loss, its per-partida quantification and the adjuster's rulings.
 *
 * `claim_item` is the adjuster's table, 1:N and always summed: notified ->
 * determined -> damage -> deductible -> indemnity. The server returns the
 * totals alongside the rows so the UI never re-adds them by hand.
 *
 * Times are hour-level on purpose (`occurred_at`, `reported_at`): the corpus
 * contains a 4-hour franchise, so a bare date would fabricate coverage.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import api from "@/lib/api";
import { clean, qk } from "@/api/keys";
import type {
  Claim,
  ClaimCloseRequest,
  ClaimCreate,
  ClaimItemCreate,
  ClaimItems,
  ClaimStatus,
  ClaimUpdate,
} from "@/api/types";

export interface ClaimPage {
  items: Claim[];
  total: number;
  limit: number;
  offset: number;
}

export interface ClaimListParams {
  policy_id?: number;
  client_id?: number;
  case_file_id?: number;
  /** Single value — see the note in `api/policies.ts`. */
  status?: ClaimStatus;
  q?: string;
  limit?: number;
  offset?: number;
}

export function useClaims(params: ClaimListParams = {}, enabled = true) {
  return useQuery({
    queryKey: qk.claims.list(params),
    enabled,
    queryFn: async () => {
      const { data } = await api.get<ClaimPage>("/claims", { params: clean(params) });
      return data;
    },
  });
}

export function useClaim(claimId: number | undefined) {
  return useQuery({
    queryKey: qk.claims.detail(claimId ?? 0),
    enabled: !!claimId,
    queryFn: async () => {
      const { data } = await api.get<Claim>(`/claims/${claimId}`);
      return data;
    },
  });
}

export function useCreateClaim() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload: ClaimCreate) => {
      const { data } = await api.post<Claim>("/claims", payload);
      return data;
    },
    onSuccess: (data) => {
      void qc.invalidateQueries({ queryKey: qk.claims.all });
      if (data.policy_id) {
        void qc.invalidateQueries({ queryKey: qk.policies.detail(data.policy_id) });
      }
    },
  });
}

export function useUpdateClaim(claimId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload: ClaimUpdate) => {
      const { data } = await api.patch<Claim>(`/claims/${claimId}`, payload);
      return data;
    },
    onSuccess: (data) => {
      qc.setQueryData(qk.claims.detail(claimId), data);
      void qc.invalidateQueries({ queryKey: qk.claims.lists() });
    },
  });
}

/** The adjuster's table plus the server-computed totals. */
export function useClaimItems(claimId: number | undefined) {
  return useQuery({
    queryKey: qk.claims.items(claimId ?? 0),
    enabled: !!claimId,
    queryFn: async () => {
      const { data } = await api.get<ClaimItems>(`/claims/${claimId}/items`);
      return data;
    },
  });
}

/** Full replace — the shape the adjuster's reports arrive in. */
export function useReplaceClaimItems(claimId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (items: ClaimItemCreate[]) => {
      const { data } = await api.put<ClaimItems>(`/claims/${claimId}/items`, { items });
      return data;
    },
    onSuccess: (data) => {
      qc.setQueryData(qk.claims.items(claimId), data);
      void qc.invalidateQueries({ queryKey: qk.claims.detail(claimId) });
    },
  });
}

/** Final ruling + loss ratio. `Claims.Approve` only. */
export function useCloseClaim(claimId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload: ClaimCloseRequest) => {
      const { data } = await api.post<Claim>(`/claims/${claimId}/close`, payload);
      return data;
    },
    onSuccess: (data) => {
      qc.setQueryData(qk.claims.detail(claimId), data);
      void qc.invalidateQueries({ queryKey: qk.claims.all });
      void qc.invalidateQueries({ queryKey: qk.caseFiles.all });
      if (data.policy_id) {
        void qc.invalidateQueries({ queryKey: qk.policies.detail(data.policy_id) });
      }
    },
  });
}
