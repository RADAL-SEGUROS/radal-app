/**
 * Collections — the payment plan and its instalment ledger.
 *
 * The invariant the corpus shows in every plan:
 *
 *     SUM(installment.gross_amount_uf) == policy gross premium
 *                                       + SUM(endorsement.total_premium_delta_uf)
 *
 * with the last instalment absorbing the rounding. The server validates it
 * BEFORE writing anything and answers 422 with expected/received/difference —
 * never a silent fix. `GET /collections/{id}/status` returns the derived view
 * (outstanding, overdue, compliance %, alerts and the `balances` flag).
 *
 * Art. 528: non-payment suspends cover. `terminated_at`, `rehabilitated_at`,
 * `days_without_cover` and `art528_events` on the plan are that sequence.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import api from "@/lib/api";
import { clean, qk } from "@/api/keys";
import type {
  CollectionInstallment,
  CollectionInstallmentCreate,
  CollectionInstallmentUpdate,
  CollectionPlan,
  CollectionPlanCreate,
  CollectionPlanStatus,
  CollectionPlanUpdate,
  CollectionStatus,
  ScopeParams,
} from "@/api/types";

export interface CollectionPlanPage {
  items: CollectionPlan[];
  total: number;
  limit: number;
  offset: number;
}

export interface CollectionPlanListParams extends ScopeParams {
  policy_id?: number;
  /** Single value — see the note in `api/policies.ts`. */
  status?: CollectionPlanStatus;
  limit?: number;
  offset?: number;
}

export function useCollectionPlans(
  params: CollectionPlanListParams = {},
  enabled = true,
) {
  return useQuery({
    queryKey: qk.collections.list(params),
    enabled,
    queryFn: async () => {
      const { data } = await api.get<CollectionPlanPage>("/collections", {
        params: clean(params),
      });
      return data;
    },
  });
}

export function useCollectionPlan(planId: number | undefined) {
  return useQuery({
    queryKey: qk.collections.detail(planId ?? 0),
    enabled: !!planId,
    queryFn: async () => {
      const { data } = await api.get<CollectionPlan>(`/collections/${planId}`);
      return data;
    },
  });
}

export function useCreateCollectionPlan() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload: CollectionPlanCreate) => {
      const { data } = await api.post<CollectionPlan>("/collections", payload);
      return data;
    },
    onSuccess: (data) => {
      void qc.invalidateQueries({ queryKey: qk.collections.all });
      void qc.invalidateQueries({ queryKey: qk.policies.detail(data.policy_id) });
    },
  });
}

export function useUpdateCollectionPlan(planId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload: CollectionPlanUpdate) => {
      const { data } = await api.patch<CollectionPlan>(`/collections/${planId}`, payload);
      return data;
    },
    onSuccess: (data) => {
      qc.setQueryData(qk.collections.detail(planId), data);
      void qc.invalidateQueries({ queryKey: qk.collections.lists() });
      void qc.invalidateQueries({ queryKey: qk.collections.status(planId) });
    },
  });
}

export function useInstallments(planId: number | undefined) {
  return useQuery({
    queryKey: qk.collections.installments(planId ?? 0),
    enabled: !!planId,
    queryFn: async () => {
      const { data } = await api.get<CollectionInstallment[]>(
        `/collections/${planId}/installments`,
      );
      return data;
    },
  });
}

/** Full replace of the ledger. The Σ is validated before a single row is written. */
export function useReplaceInstallments(planId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (installments: CollectionInstallmentCreate[]) => {
      const { data } = await api.put<CollectionPlan>(
        `/collections/${planId}/installments`,
        { installments },
      );
      return data;
    },
    onSuccess: (data) => {
      qc.setQueryData(qk.collections.detail(planId), data);
      void qc.invalidateQueries({ queryKey: qk.collections.installments(planId) });
      void qc.invalidateQueries({ queryKey: qk.collections.status(planId) });
      void qc.invalidateQueries({ queryKey: qk.collections.lists() });
    },
  });
}

/** Mark one instalment paid / late / credited. `number` is the ordinal, not the id. */
export function useUpdateInstallment(planId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({
      number,
      ...payload
    }: CollectionInstallmentUpdate & { number: number }) => {
      const { data } = await api.patch<CollectionInstallment>(
        `/collections/${planId}/installments/${number}`,
        payload,
      );
      return data;
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.collections.installments(planId) });
      void qc.invalidateQueries({ queryKey: qk.collections.detail(planId) });
      void qc.invalidateQueries({ queryKey: qk.collections.status(planId) });
    },
  });
}

/** The derived dashboard: outstanding, overdue, compliance %, alerts, `balances`. */
export function useCollectionStatus(planId: number | undefined) {
  return useQuery({
    queryKey: qk.collections.status(planId ?? 0),
    enabled: !!planId,
    queryFn: async () => {
      const { data } = await api.get<CollectionStatus>(`/collections/${planId}/status`);
      return data;
    },
  });
}
