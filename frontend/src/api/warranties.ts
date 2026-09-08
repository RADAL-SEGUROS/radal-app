/**
 * Warranties — the R-n / G-n / M-n tracker.
 *
 * The single code threads through inspection -> policy -> collection tracker ->
 * claim notice -> adjuster report -> endorsement, which is why a warranty write
 * invalidates its policy as well as the warranty itself: the claim and the
 * collection dashboards both read its status.
 *
 * Creation lives with the policy (`POST /policies/{id}/warranties`, see
 * `@/api/policies`); this module owns the single-row reads and updates.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import api from "@/lib/api";
import { qk } from "@/api/keys";
import type { Warranty, WarrantyUpdate } from "@/api/types";

export function useWarranty(warrantyId: number | undefined) {
  return useQuery({
    queryKey: qk.warranties.detail(warrantyId ?? 0),
    enabled: !!warrantyId,
    queryFn: async () => {
      const { data } = await api.get<Warranty>(`/warranties/${warrantyId}`);
      return data;
    },
  });
}

/**
 * `policyId` is not part of the URL — it is what the mutation invalidates, so
 * the tracker on the policy page refreshes without a manual refetch.
 */
export function useUpdateWarranty(warrantyId: number, policyId?: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload: WarrantyUpdate) => {
      const { data } = await api.patch<Warranty>(`/warranties/${warrantyId}`, payload);
      return data;
    },
    onSuccess: (data) => {
      qc.setQueryData(qk.warranties.detail(warrantyId), data);
      const target = policyId ?? data.policy_id;
      if (target) {
        void qc.invalidateQueries({ queryKey: qk.policies.warranties(target) });
        void qc.invalidateQueries({ queryKey: qk.policies.detail(target) });
      }
    },
  });
}
