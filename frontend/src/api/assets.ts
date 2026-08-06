/**
 * Assets — the insurable objects (bienes asegurables) a client owns.
 *
 * Storage is hybrid: ten underwriting attributes are real columns (filterable),
 * the type-specific tail lives in `attributes` JSON shaped by the insurance
 * line's `min_fields`.
 *
 * Endpoints: `GET|POST /assets`, `GET /assets/summary`,
 * `GET|PATCH|DELETE /assets/{id}`, and the nested
 * `GET|POST /clients/{client_id}/assets`.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import api from "@/lib/api";
import { clean, qk } from "@/api/keys";
import type {
  Asset,
  AssetCreate,
  AssetListItem,
  AssetStatus,
  AssetSummary,
  AssetUpdate,
  Page,
} from "@/api/types";

export interface AssetListParams {
  client_id?: number;
  q?: string;
  asset_type?: string;
  status?: AssetStatus[];
  commune?: string;
  region?: string;
  page?: number;
  page_size?: number;
}

export function useAssets(params: AssetListParams = {}, enabled = true) {
  return useQuery({
    queryKey: qk.assets.list(params),
    enabled,
    queryFn: async () => {
      const { data } = await api.get<Page<AssetListItem>>("/assets", {
        params: clean(params),
      });
      return data;
    },
  });
}

export function useAssetsSummary(clientId?: number, enabled = true) {
  return useQuery({
    queryKey: qk.assets.summary({ client_id: clientId }),
    enabled,
    queryFn: async () => {
      const { data } = await api.get<AssetSummary>("/assets/summary", {
        params: clean({ client_id: clientId }),
      });
      return data;
    },
  });
}

export function useAsset(assetId: number | undefined) {
  return useQuery({
    queryKey: qk.assets.detail(assetId ?? 0),
    enabled: !!assetId,
    queryFn: async () => {
      const { data } = await api.get<Asset>(`/assets/${assetId}`);
      return data;
    },
  });
}

/** Nested list — the assets of one client. */
export function useClientAssets(
  clientId: number | undefined,
  params: Omit<AssetListParams, "client_id"> = {},
) {
  return useQuery({
    queryKey: qk.assets.byClient(clientId ?? 0, params),
    enabled: !!clientId,
    queryFn: async () => {
      const { data } = await api.get<Page<AssetListItem>>(`/clients/${clientId}/assets`, {
        params: clean(params),
      });
      return data;
    },
  });
}

export function useCreateAsset() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload: AssetCreate & { client_id: number }) => {
      const { data } = await api.post<Asset>("/assets", payload);
      return data;
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.assets.all });
      void qc.invalidateQueries({ queryKey: qk.clients.all });
    },
  });
}

export function useUpdateAsset(assetId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload: AssetUpdate) => {
      const { data } = await api.patch<Asset>(`/assets/${assetId}`, payload);
      return data;
    },
    onSuccess: (data) => {
      qc.setQueryData(qk.assets.detail(assetId), data);
      void qc.invalidateQueries({ queryKey: qk.assets.lists() });
    },
  });
}

export function useDeleteAsset() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (assetId: number) => {
      await api.delete(`/assets/${assetId}`);
      return assetId;
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.assets.all });
      void qc.invalidateQueries({ queryKey: qk.clients.all });
    },
  });
}
