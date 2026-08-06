/**
 * Proposals — the centre of gravity. One insurer's offer against one quote
 * request, standardised so offers are comparable regardless of origin.
 *
 * Two rules the UI must respect:
 *  - a proposal REQUIRES `source_document_id` (upload the file first);
 *  - its insurer must already carry both `rut` and `cmf_code`.
 *
 * Accepting one proposal cascades: the siblings are rejected, the quote closes
 * and the placement is awarded — `ProposalDecisionResult` reports the whole
 * cascade, which is why accept/reject invalidate quotes and placements too.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import api from "@/lib/api";
import { clean, qk } from "@/api/keys";
import type {
  CoverageKind,
  OffsetPage,
  Proposal,
  ProposalCoverage,
  ProposalCoverageCreate,
  ProposalCreate,
  ProposalDecisionResult,
  ProposalStatus,
  ProposalUpdate,
} from "@/api/types";

export interface ProposalListParams {
  quote_request_id?: number;
  placement_id?: number;
  insurer_id?: number;
  status?: ProposalStatus;
  limit?: number;
  offset?: number;
}

export function useProposals(params: ProposalListParams = {}, enabled = true) {
  return useQuery({
    queryKey: qk.proposals.list(params),
    enabled,
    queryFn: async () => {
      const { data } = await api.get<OffsetPage<Proposal>>("/proposals", {
        params: clean(params),
      });
      return data;
    },
  });
}

export function useProposal(proposalId: number | undefined) {
  return useQuery({
    queryKey: qk.proposals.detail(proposalId ?? 0),
    enabled: !!proposalId,
    queryFn: async () => {
      const { data } = await api.get<Proposal>(`/proposals/${proposalId}`);
      return data;
    },
  });
}

export function useProposalCoverages(proposalId: number | undefined, kind?: CoverageKind) {
  return useQuery({
    queryKey: qk.proposals.coverages(proposalId ?? 0, { kind }),
    enabled: !!proposalId,
    queryFn: async () => {
      const { data } = await api.get<ProposalCoverage[]>(
        `/proposals/${proposalId}/coverages`,
        { params: clean({ kind }) },
      );
      return data;
    },
  });
}

export function useCreateProposal() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload: ProposalCreate) => {
      const { data } = await api.post<Proposal>("/proposals", payload);
      return data;
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.proposals.all });
      void qc.invalidateQueries({ queryKey: qk.quotes.all });
    },
  });
}

export function useUpdateProposal(proposalId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload: ProposalUpdate) => {
      const { data } = await api.patch<Proposal>(`/proposals/${proposalId}`, payload);
      return data;
    },
    onSuccess: (data) => {
      qc.setQueryData(qk.proposals.detail(proposalId), data);
      void qc.invalidateQueries({ queryKey: qk.proposals.lists() });
      void qc.invalidateQueries({ queryKey: qk.quotes.all });
    },
  });
}

export function useDeleteProposal() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (proposalId: number) => {
      await api.delete(`/proposals/${proposalId}`);
      return proposalId;
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.proposals.all });
      void qc.invalidateQueries({ queryKey: qk.quotes.all });
    },
  });
}

// --- Coverages / exclusions --------------------------------------------------

function useProposalWrite<TVars>(
  proposalId: number,
  fn: (vars: TVars) => Promise<Proposal>,
) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: (data) => {
      qc.setQueryData(qk.proposals.detail(proposalId), data);
      void qc.invalidateQueries({ queryKey: qk.proposals.coverages(proposalId) });
      void qc.invalidateQueries({ queryKey: qk.proposals.lists() });
    },
  });
}

export function useAddProposalCoverage(proposalId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload: ProposalCoverageCreate) => {
      const { data } = await api.post<ProposalCoverage>(
        `/proposals/${proposalId}/coverages`,
        payload,
      );
      return data;
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.proposals.detail(proposalId) });
      void qc.invalidateQueries({ queryKey: qk.proposals.coverages(proposalId) });
    },
  });
}

/** Replaces the WHOLE coverage + exclusion set. */
export function useReplaceProposalCoverages(proposalId: number) {
  return useProposalWrite(proposalId, async (payload: ProposalCoverageCreate[]) => {
    const { data } = await api.put<Proposal>(`/proposals/${proposalId}/coverages`, payload);
    return data;
  });
}

export function useUpdateProposalCoverage(proposalId: number) {
  return useProposalWrite(
    proposalId,
    async ({ coverageId, ...payload }: Partial<ProposalCoverageCreate> & { coverageId: number }) => {
      const { data } = await api.patch<Proposal>(
        `/proposals/${proposalId}/coverages/${coverageId}`,
        payload,
      );
      return data;
    },
  );
}

export function useDeleteProposalCoverage(proposalId: number) {
  return useProposalWrite(proposalId, async (coverageId: number) => {
    const { data } = await api.delete<Proposal>(
      `/proposals/${proposalId}/coverages/${coverageId}`,
    );
    return data;
  });
}

// --- Human confirmation + award decision ------------------------------------

/** Suggest -> HUMAN CONFIRM -> commit. Nothing AI-written is trusted until this. */
export function useConfirmProposal(proposalId: number) {
  return useProposalWrite(proposalId, async (payload: { is_confirmed?: boolean } = {}) => {
    const { data } = await api.post<Proposal>(`/proposals/${proposalId}/confirm`, {
      is_confirmed: payload.is_confirmed ?? true,
    });
    return data;
  });
}

function useDecision(proposalId: number, action: "accept" | "reject") {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload: { reason?: string | null } = {}) => {
      const { data } = await api.post<ProposalDecisionResult>(
        `/proposals/${proposalId}/${action}`,
        payload,
      );
      return data;
    },
    onSuccess: () => {
      // The cascade touches siblings, the quote request and the placement.
      void qc.invalidateQueries({ queryKey: qk.proposals.all });
      void qc.invalidateQueries({ queryKey: qk.quotes.all });
      void qc.invalidateQueries({ queryKey: qk.placements.all });
    },
  });
}

export function useAcceptProposal(proposalId: number) {
  return useDecision(proposalId, "accept");
}

export function useRejectProposal(proposalId: number) {
  return useDecision(proposalId, "reject");
}
