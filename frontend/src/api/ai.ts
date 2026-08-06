/**
 * AI — proposal extraction and the agent chat.
 *
 * The write path is always **suggest -> human confirm -> commit**:
 *   1. `useExtractProposal()` reads an already-uploaded document and returns a
 *      `ProposalSuggestion`. It writes NO proposal.
 *   2. The broker edits the suggestion in the UI.
 *   3. `useConfirmExtraction()` commits the reviewed payload as a real proposal.
 *
 * Nothing auto-commits, and the `extraction` row (model, prompt version, raw
 * output, confidence, source document) is persisted either way for traceability.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import api from "@/lib/api";
import { qk } from "@/api/keys";
import type {
  AgentExchange,
  AgentMessage,
  AgentScope,
  AgentThread,
  ExtractionSuggestionResponse,
  ListResponse,
  Proposal,
  ProposalConfirmRequest,
} from "@/api/types";

// --- Extraction --------------------------------------------------------------

/** SUGGEST step. Reads one document; commits nothing. */
export function useExtractProposal() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload: { document_id: number }) => {
      const { data } = await api.post<ExtractionSuggestionResponse>(
        "/ai/proposals/extract",
        payload,
      );
      return data;
    },
    onSuccess: (data) => {
      qc.setQueryData(qk.extractions.detail(data.extraction.id), data);
    },
  });
}

/** Re-read a persisted extraction and its suggestion (the audit trail). */
export function useExtraction(extractionId: number | undefined) {
  return useQuery({
    queryKey: qk.extractions.detail(extractionId ?? 0),
    enabled: !!extractionId,
    queryFn: async () => {
      const { data } = await api.get<ExtractionSuggestionResponse>(
        `/ai/extractions/${extractionId}`,
      );
      return data;
    },
  });
}

/** COMMIT step. The reviewed (possibly edited) suggestion becomes a proposal. */
export function useConfirmExtraction(extractionId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload: ProposalConfirmRequest) => {
      const { data } = await api.post<Proposal>(
        `/ai/proposals/${extractionId}/confirm`,
        payload,
      );
      return data;
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.proposals.all });
      void qc.invalidateQueries({ queryKey: qk.quotes.all });
      void qc.invalidateQueries({ queryKey: qk.insurers.all });
    },
  });
}

// --- Agent chat --------------------------------------------------------------

export function useAgentThreads(enabled = true) {
  return useQuery({
    queryKey: qk.agentThreads.list(),
    enabled,
    queryFn: async () => {
      const { data } = await api.get<ListResponse<AgentThread>>("/ai/threads");
      return data;
    },
  });
}

export function useCreateAgentThread() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload: {
      scope?: AgentScope;
      entity_id?: number | null;
      title?: string | null;
    }) => {
      const { data } = await api.post<AgentThread>("/ai/threads", payload);
      return data;
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.agentThreads.lists() });
    },
  });
}

export function useAgentMessages(threadId: number | undefined) {
  return useQuery({
    queryKey: qk.agentThreads.messages(threadId ?? 0),
    enabled: !!threadId,
    queryFn: async () => {
      const { data } = await api.get<ListResponse<AgentMessage>>(
        `/ai/threads/${threadId}/messages`,
      );
      return data;
    },
  });
}

/** One turn: posts the user message, returns it together with the answer. */
export function useSendAgentMessage(threadId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload: { content: string }) => {
      const { data } = await api.post<AgentExchange>(
        `/ai/threads/${threadId}/messages`,
        payload,
      );
      return data;
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.agentThreads.messages(threadId) });
      void qc.invalidateQueries({ queryKey: qk.agentThreads.lists() });
    },
  });
}
