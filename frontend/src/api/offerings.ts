/**
 * Offerings — the shareable package built from a quote request, highlighting one
 * recommended proposal.
 *
 * Sending is deliberately MANUAL: `POST /offerings/{id}/send` only *records* the
 * channel the broker used (WhatsApp / email / download). `share_url` is the
 * public link they paste; the token itself is the credential.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import api from "@/lib/api";
import { clean, qk } from "@/api/keys";
import type {
  DocumentDownload,
  ListResponse,
  Offering,
  OfferingCreate,
  OfferingSend,
  OfferingStatus,
  OfferingUpdate,
} from "@/api/types";

export interface OfferingListParams {
  quote_request_id?: number;
  status?: OfferingStatus;
  limit?: number;
  offset?: number;
}

export function useOfferings(params: OfferingListParams = {}, enabled = true) {
  return useQuery({
    queryKey: qk.offerings.list(params),
    enabled,
    queryFn: async () => {
      const { data } = await api.get<ListResponse<Offering>>("/offerings", {
        params: clean(params),
      });
      return data;
    },
  });
}

export function useOffering(offeringId: number | undefined) {
  return useQuery({
    queryKey: qk.offerings.detail(offeringId ?? 0),
    enabled: !!offeringId,
    queryFn: async () => {
      const { data } = await api.get<Offering>(`/offerings/${offeringId}`);
      return data;
    },
  });
}

export function useOfferingPdf(offeringId: number | undefined, enabled = true) {
  return useQuery({
    queryKey: qk.offerings.pdf(offeringId ?? 0),
    enabled: !!offeringId && enabled,
    staleTime: 30_000,
    queryFn: async () => {
      const { data } = await api.get<DocumentDownload>(`/offerings/${offeringId}/pdf`);
      return data;
    },
  });
}

export function useCreateOffering() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload: OfferingCreate) => {
      const { data } = await api.post<Offering>("/offerings", payload);
      return data;
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.offerings.all });
    },
  });
}

function useOfferingWrite<TVars>(offeringId: number, fn: (vars: TVars) => Promise<Offering>) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: (data) => {
      qc.setQueryData(qk.offerings.detail(offeringId), data);
      void qc.invalidateQueries({ queryKey: qk.offerings.lists() });
    },
  });
}

export function useUpdateOffering(offeringId: number) {
  return useOfferingWrite(offeringId, async (payload: OfferingUpdate) => {
    const { data } = await api.patch<Offering>(`/offerings/${offeringId}`, payload);
    return data;
  });
}

/** Records how the offering left the building — it does not deliver anything. */
export function useRecordOfferingSent(offeringId: number) {
  return useOfferingWrite(offeringId, async (payload: OfferingSend) => {
    const { data } = await api.post<Offering>(`/offerings/${offeringId}/send`, payload);
    return data;
  });
}

export function useRegenerateOfferingPdf(offeringId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async () => {
      const { data } = await api.post<Offering>(`/offerings/${offeringId}/pdf`, {});
      return data;
    },
    onSuccess: (data) => {
      qc.setQueryData(qk.offerings.detail(offeringId), data);
      void qc.invalidateQueries({ queryKey: qk.offerings.pdf(offeringId) });
    },
  });
}

export function useDeleteOffering() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (offeringId: number) => {
      await api.delete(`/offerings/${offeringId}`);
      return offeringId;
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.offerings.all });
    },
  });
}
