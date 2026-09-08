/**
 * Leads — the data-point-only pipeline that exists BEFORE there is a RUT.
 *
 * A lead is deliberately not a `client`: no files, no placement, one follow-up
 * date. `POST /leads/{id}/convert` is the only way it becomes real, creating
 * insured + client + asset + placement + `case_file(kind=account)` in one
 * transaction.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import api from "@/lib/api";
import { clean, qk } from "@/api/keys";
import type {
  IsoDate,
  Lead,
  LeadConversionResult,
  LeadConvertRequest,
  LeadCreate,
  LeadStatus,
  LeadSummary,
  LeadUpdate,
  OffsetPage,
} from "@/api/types";

export interface LeadListParams {
  status?: LeadStatus[];
  owner_id?: number;
  insurance_line_id?: number;
  follow_up_before?: IsoDate;
  q?: string;
  limit?: number;
  offset?: number;
}

export function useLeads(params: LeadListParams = {}, enabled = true) {
  return useQuery({
    queryKey: qk.leads.list(params),
    enabled,
    queryFn: async () => {
      const { data } = await api.get<OffsetPage<Lead>>("/leads", {
        params: clean(params),
      });
      return data;
    },
  });
}

export function useLeadsSummary(enabled = true) {
  return useQuery({
    queryKey: qk.leads.summary(),
    enabled,
    queryFn: async () => {
      const { data } = await api.get<LeadSummary>("/leads/summary");
      return data;
    },
  });
}

export function useLead(leadId: number | undefined) {
  return useQuery({
    queryKey: qk.leads.detail(leadId ?? 0),
    enabled: !!leadId,
    queryFn: async () => {
      const { data } = await api.get<Lead>(`/leads/${leadId}`);
      return data;
    },
  });
}

export function useCreateLead() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload: LeadCreate) => {
      const { data } = await api.post<Lead>("/leads", payload);
      return data;
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.leads.all });
    },
  });
}

export function useUpdateLead(leadId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload: LeadUpdate) => {
      const { data } = await api.patch<Lead>(`/leads/${leadId}`, payload);
      return data;
    },
    onSuccess: (data) => {
      qc.setQueryData(qk.leads.detail(leadId), data);
      void qc.invalidateQueries({ queryKey: qk.leads.lists() });
      void qc.invalidateQueries({ queryKey: qk.leads.summary() });
    },
  });
}

export function useDeleteLead() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (leadId: number) => {
      await api.delete(`/leads/${leadId}`);
      return leadId;
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.leads.all });
    },
  });
}

/** One transaction: insured + client + asset + placement + account case file. */
export function useConvertLead(leadId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload: LeadConvertRequest) => {
      const { data } = await api.post<LeadConversionResult>(
        `/leads/${leadId}/convert`,
        payload,
      );
      return data;
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.leads.all });
      void qc.invalidateQueries({ queryKey: qk.caseFiles.all });
      void qc.invalidateQueries({ queryKey: qk.clients.all });
      void qc.invalidateQueries({ queryKey: qk.placements.all });
    },
  });
}
