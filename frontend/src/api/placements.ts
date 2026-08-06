/**
 * Placements — the operating folder: one asset x one insurance line x one period.
 *
 * Status NEVER moves through PATCH. It moves only through
 * `POST /placements/{id}/transition`, and `GET /placements/{id}/transitions`
 * tells the UI exactly which buttons to enable — that is how this module stays
 * free of dead controls.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import api from "@/lib/api";
import { clean, qk } from "@/api/keys";
import type {
  Page,
  Placement,
  PlacementCreate,
  PlacementListItem,
  PlacementStatus,
  PlacementSummary,
  PlacementTransitionOptions,
  PlacementUpdate,
} from "@/api/types";

export interface PlacementListParams {
  client_id?: number;
  asset_id?: number;
  insurance_line_id?: number;
  status?: PlacementStatus[];
  period?: string;
  open_only?: boolean;
  q?: string;
  page?: number;
  page_size?: number;
}

export function usePlacements(params: PlacementListParams = {}, enabled = true) {
  return useQuery({
    queryKey: qk.placements.list(params),
    enabled,
    queryFn: async () => {
      const { data } = await api.get<Page<PlacementListItem>>("/placements", {
        params: clean(params),
      });
      return data;
    },
  });
}

export function usePlacementsSummary(clientId?: number, enabled = true) {
  return useQuery({
    queryKey: qk.placements.summary({ client_id: clientId }),
    enabled,
    queryFn: async () => {
      const { data } = await api.get<PlacementSummary>("/placements/summary", {
        params: clean({ client_id: clientId }),
      });
      return data;
    },
  });
}

export function usePlacement(placementId: number | undefined) {
  return useQuery({
    queryKey: qk.placements.detail(placementId ?? 0),
    enabled: !!placementId,
    queryFn: async () => {
      const { data } = await api.get<Placement>(`/placements/${placementId}`);
      return data;
    },
  });
}

/** The authoritative list of status moves the UI may offer right now. */
export function usePlacementTransitions(placementId: number | undefined) {
  return useQuery({
    queryKey: qk.placements.transitions(placementId ?? 0),
    enabled: !!placementId,
    queryFn: async () => {
      const { data } = await api.get<PlacementTransitionOptions>(
        `/placements/${placementId}/transitions`,
      );
      return data;
    },
  });
}

export function useCreatePlacement() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload: PlacementCreate) => {
      const { data } = await api.post<Placement>("/placements", payload);
      return data;
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.placements.all });
    },
  });
}

export function useUpdatePlacement(placementId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload: PlacementUpdate) => {
      const { data } = await api.patch<Placement>(`/placements/${placementId}`, payload);
      return data;
    },
    onSuccess: (data) => {
      qc.setQueryData(qk.placements.detail(placementId), data);
      void qc.invalidateQueries({ queryKey: qk.placements.lists() });
    },
  });
}

export function useTransitionPlacement(placementId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload: { status: PlacementStatus; note?: string | null }) => {
      const { data } = await api.post<Placement>(
        `/placements/${placementId}/transition`,
        payload,
      );
      return data;
    },
    onSuccess: (data) => {
      qc.setQueryData(qk.placements.detail(placementId), data);
      void qc.invalidateQueries({ queryKey: qk.placements.transitions(placementId) });
      void qc.invalidateQueries({ queryKey: qk.placements.lists() });
    },
  });
}

export function useDeletePlacement() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (placementId: number) => {
      await api.delete(`/placements/${placementId}`);
      return placementId;
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.placements.all });
    },
  });
}
