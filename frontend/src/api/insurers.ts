/**
 * Insurers — one canonical, cross-broker catalog flagged **native** (a Radal
 * commercial partner, with a rich profile and contacts) or **external** (tracked
 * from an uploaded proposal).
 *
 * Identity is `rut` + `cmf_code`, and matching goes through
 * `POST /insurers/match` with those identifiers ONLY — never a name, because
 * OCR yields "HDI Seguros S.A." / "HDI SEGUROS SA" / "H.D.I." for one company.
 *
 * Contacts fall back in order: (broker+line) -> (broker) -> (line) -> global.
 * `insurer.can_edit` / `contact.can_edit` tell the UI whether to render the edit
 * control enabled or visibly disabled.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import api from "@/lib/api";
import { clean, qk } from "@/api/keys";
import type {
  Insurer,
  InsurerContact,
  InsurerContactCreate,
  InsurerContactUpdate,
  InsurerCreate,
  InsurerMatchRequest,
  InsurerMatchResult,
  InsurerRecommendationResponse,
  InsurerStatus,
  InsurerUpdate,
  OffsetPage,
  ResolvedContact,
} from "@/api/types";

export interface InsurerListParams {
  is_native?: boolean;
  status?: InsurerStatus;
  /** Free text over name / rut / cmf_code. */
  q?: string;
  limit?: number;
  offset?: number;
}

export function useInsurers(params: InsurerListParams = {}, enabled = true) {
  return useQuery({
    queryKey: qk.insurers.list(params),
    enabled,
    queryFn: async () => {
      const { data } = await api.get<OffsetPage<Insurer>>("/insurers", {
        params: clean(params),
      });
      return data;
    },
  });
}

export function useInsurer(insurerId: number | undefined) {
  return useQuery({
    queryKey: qk.insurers.detail(insurerId ?? 0),
    enabled: !!insurerId,
    queryFn: async () => {
      const { data } = await api.get<Insurer>(`/insurers/${insurerId}`);
      return data;
    },
  });
}

/** Native partners suggested for an insurance line, best first. */
export function useInsurerRecommendations(
  params: { insurance_line_id?: number; limit?: number } = {},
  enabled = true,
) {
  return useQuery({
    queryKey: qk.insurers.recommendations(params),
    enabled,
    queryFn: async () => {
      const { data } = await api.get<InsurerRecommendationResponse>(
        "/insurers/recommendations",
        { params: clean(params) },
      );
      return data;
    },
  });
}

export function useCreateInsurer() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload: InsurerCreate) => {
      const { data } = await api.post<Insurer>("/insurers", payload);
      return data;
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.insurers.all });
    },
  });
}

export function useUpdateInsurer(insurerId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload: InsurerUpdate) => {
      const { data } = await api.patch<Insurer>(`/insurers/${insurerId}`, payload);
      return data;
    },
    onSuccess: (data) => {
      qc.setQueryData(qk.insurers.detail(insurerId), data);
      void qc.invalidateQueries({ queryKey: qk.insurers.lists() });
    },
  });
}

/** Find-or-create by normalized rut / cmf_code. Never matches on a name. */
export function useMatchInsurer() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload: InsurerMatchRequest) => {
      const { data } = await api.post<InsurerMatchResult>("/insurers/match", payload);
      return data;
    },
    onSuccess: (result) => {
      if (result.created) void qc.invalidateQueries({ queryKey: qk.insurers.all });
    },
  });
}

// --- Contacts ----------------------------------------------------------------

export function useInsurerContacts(
  insurerId: number | undefined,
  params: { insurance_line_id?: number } = {},
) {
  return useQuery({
    queryKey: qk.insurers.contacts(insurerId ?? 0, params),
    enabled: !!insurerId,
    queryFn: async () => {
      const { data } = await api.get<InsurerContact[]>(`/insurers/${insurerId}/contacts`, {
        params: clean(params),
      });
      return data;
    },
  });
}

/** The single effective contact for (insurer, broker, line) + which tier answered. */
export function useResolvedInsurerContact(
  insurerId: number | undefined,
  params: { insurance_line_id?: number } = {},
) {
  return useQuery({
    queryKey: qk.insurers.resolvedContact(insurerId ?? 0, params),
    enabled: !!insurerId,
    retry: false,
    queryFn: async () => {
      const { data } = await api.get<ResolvedContact>(
        `/insurers/${insurerId}/contacts/resolve`,
        { params: clean(params) },
      );
      return data;
    },
  });
}

function useContactWrite<TVars, TResult>(
  insurerId: number,
  fn: (vars: TVars) => Promise<TResult>,
) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.insurers.detail(insurerId) });
      void qc.invalidateQueries({ queryKey: qk.insurers.contacts(insurerId) });
    },
  });
}

export function useCreateInsurerContact(insurerId: number) {
  return useContactWrite(insurerId, async (payload: InsurerContactCreate) => {
    const { data } = await api.post<InsurerContact>(`/insurers/${insurerId}/contacts`, payload);
    return data;
  });
}

export function useUpdateInsurerContact(insurerId: number) {
  return useContactWrite(
    insurerId,
    async ({ contactId, ...payload }: InsurerContactUpdate & { contactId: number }) => {
      const { data } = await api.patch<InsurerContact>(
        `/insurers/${insurerId}/contacts/${contactId}`,
        payload,
      );
      return data;
    },
  );
}

export function useDeleteInsurerContact(insurerId: number) {
  return useContactWrite(insurerId, async (contactId: number) => {
    await api.delete(`/insurers/${insurerId}/contacts/${contactId}`);
    return contactId;
  });
}
