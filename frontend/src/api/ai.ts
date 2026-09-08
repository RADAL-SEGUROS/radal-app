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
import api, { API_URL, getAccessToken } from "@/lib/api";
import { qk } from "@/api/keys";
import type {
  AgentExchange,
  AgentMessage,
  AgentScope,
  AgentThread,
  AiSummary,
  CategoryListResponse,
  CategorySpec,
  DocumentConfirmRequest,
  DocumentConfirmResponse,
  DocumentExtractionRequest,
  DocumentExtractionResponse,
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

// --- Generic, registry-driven extraction --------------------------------------

/**
 * The document-category registry as JSON.
 *
 * This is what makes the extraction review form generic: a new category needs
 * a backend schema and NO new React component. Cached for the session — the
 * registry only changes when the backend redeploys.
 */
export function useCategoryRegistry(enabled = true) {
  return useQuery({
    queryKey: qk.aiCategories.lists(),
    enabled,
    staleTime: 30 * 60 * 1000,
    queryFn: async () => {
      const { data } = await api.get<CategoryListResponse>("/ai/categories");
      return data;
    },
  });
}

/** Look one category up in the registry. `undefined` = no schema, no AI button. */
export function useCategorySpec(category: string | null | undefined): CategorySpec | undefined {
  const { data } = useCategoryRegistry();
  if (!category || !data) return undefined;
  return data.items.find(
    (spec) => spec.category === category || spec.canonical_category === category,
  );
}

/** SUGGEST step for ANY category. Writes an `extraction` row, commits nothing. */
export function useExtractDocument() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload: DocumentExtractionRequest) => {
      const { data } = await api.post<DocumentExtractionResponse>(
        "/ai/documents/extract",
        payload,
      );
      return data;
    },
    onSuccess: (data) => {
      qc.setQueryData(qk.extractions.detail(data.extraction.id), data);
    },
  });
}

/** COMMIT step: the human-reviewed payload is written to its prefill target. */
export function useConfirmDocumentExtraction(extractionId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload: DocumentConfirmRequest) => {
      const { data } = await api.post<DocumentConfirmResponse>(
        `/ai/documents/${extractionId}/confirm`,
        payload,
      );
      return data;
    },
    onSuccess: () => {
      // A confirmation can touch almost any module, so invalidate broadly.
      void qc.invalidateQueries({ queryKey: qk.proposals.all });
      void qc.invalidateQueries({ queryKey: qk.caseFiles.all });
      void qc.invalidateQueries({ queryKey: qk.documents.all });
    },
  });
}

// --- Summaries (prose is a suggestion too) -------------------------------------

export function useSummarizeProposal(proposalId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async () => {
      const { data } = await api.post<AiSummary>(`/ai/proposals/${proposalId}/summary`);
      return data;
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.proposals.detail(proposalId) });
    },
  });
}

export function useSummarizeCaseFile(caseId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async () => {
      const { data } = await api.post<AiSummary>(`/ai/case-files/${caseId}/summary`);
      return data;
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.caseFiles.detail(caseId) });
    },
  });
}

// --- Agentic turn (v4 agent spec §7 / §8.7) ----------------------------------
//
// The single-agent endpoints. All plain axios calls through `lib/api.ts` —
// the SSE/OAC header re-implementation further down stays FROZEN and unused
// by these hooks (the agent turn is buffered by design: a tool loop cannot
// stream usefully, and CloudFront buffers the body anyway).
//
// Types live here (additive) rather than in `api/types.ts`, which this work
// package does not own; the reconciler may fold them in later.

export type AgentActionStatus = "proposed" | "confirmed" | "discarded" | "failed";

/** One @-mention attached to a user message. */
export interface ContextRef {
  entity_type: string;
  entity_id: number;
}

/** Executor output on a confirmed action (`{entity_type, entity_id, url, detail}`). */
export interface AgentActionResult {
  entity_type?: string | null;
  entity_id?: number | null;
  url?: string | null;
  detail?: string | null;
  [key: string]: unknown;
}

/**
 * One proposed/resolved write (rule 6: suggest -> human confirm -> commit).
 * `allowed` is computed per response for the CALLER so the card can render
 * Confirmar disabled-with-reason without a second request; the server still
 * re-checks the real RBAC gate on confirm.
 */
export interface AgentAction {
  id: number;
  thread_id: number;
  message_id: number | null;
  tool: string;
  arguments: Record<string, unknown>;
  summary: string | null;
  module: string;
  action: string;
  status: AgentActionStatus;
  result: AgentActionResult | null;
  error: string | null;
  confirmed_at: string | null;
  created_at: string | null;
  allowed: boolean;
}

/** The linkage stub a `role=tool` message carries in `tool_calls`. */
export interface AgentToolCallMeta {
  tool_call_id?: string | null;
  action_id?: number | null;
  name?: string | null;
  status?: string | null;
}

/** `MessageRead` with the additive v4 fields (tool_calls, context_refs). */
export interface AgentTurnMessage extends AgentMessage {
  tool_calls?: AgentToolCallMeta | Array<Record<string, unknown>> | null;
  context_refs?: ContextRef[] | null;
}

export interface AgentTurnRequest {
  thread_id?: number | null;
  content: string;
  context_refs?: ContextRef[];
}

export interface AgentTurnResponse {
  thread: AgentThread;
  messages: AgentTurnMessage[];
  pending_actions: AgentAction[];
}

/** Local query key for `GET /ai/agent/actions` (keys.ts is not this pass's file). */
export const agentActionsKey = (threadId?: number | null) =>
  ["agent-actions", threadId ?? null] as const;

/** Confirmed writes touch real modules — invalidate what each tool changed. */
const TOOL_INVALIDATIONS: Record<string, ReadonlyArray<readonly string[]>> = {
  create_group: [qk.accountGroups.all, qk.navigator.all],
  attach_client_to_group: [qk.accountGroups.all, qk.navigator.all],
  create_case_file: [qk.caseFiles.all, qk.navigator.all],
  renew_case: [qk.caseFiles.all, qk.navigator.all],
  add_note: [qk.notes.all],
};

/** One agentic turn: context refs + tool loop + pending write proposals. */
export function useAgentTurn() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload: AgentTurnRequest) => {
      const { data } = await api.post<AgentTurnResponse>("/ai/agent/messages", payload);
      return data;
    },
    onSuccess: (data) => {
      void qc.invalidateQueries({ queryKey: qk.agentThreads.messages(data.thread.id) });
      void qc.invalidateQueries({ queryKey: qk.agentThreads.lists() });
      void qc.invalidateQueries({ queryKey: agentActionsKey(data.thread.id) });
    },
  });
}

/** Rehydration for the page: every action of one (own) thread. */
export function useAgentActions(threadId: number | undefined, status?: AgentActionStatus[]) {
  return useQuery({
    queryKey: [...agentActionsKey(threadId ?? 0), status ?? null] as const,
    enabled: !!threadId,
    queryFn: async () => {
      const params = new URLSearchParams();
      if (threadId) params.set("thread_id", String(threadId));
      for (const s of status ?? []) params.append("status", s);
      const { data } = await api.get<ListResponse<AgentAction>>(
        `/ai/agent/actions?${params.toString()}`,
      );
      return data;
    },
  });
}

/** The COMMIT of rule 6 — executes under the confirming user's real RBAC gate. */
export function useConfirmAgentAction() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (actionId: number) => {
      const { data } = await api.post<AgentAction>(`/ai/agent/actions/${actionId}/confirm`);
      return data;
    },
    onSuccess: (action) => {
      void qc.invalidateQueries({ queryKey: agentActionsKey(action.thread_id) });
      void qc.invalidateQueries({ queryKey: qk.agentThreads.messages(action.thread_id) });
      for (const key of TOOL_INVALIDATIONS[action.tool] ?? []) {
        void qc.invalidateQueries({ queryKey: key });
      }
    },
  });
}

/** Declining requires no privilege beyond owning the thread. */
export function useDiscardAgentAction() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (actionId: number) => {
      const { data } = await api.post<AgentAction>(`/ai/agent/actions/${actionId}/discard`);
      return data;
    },
    onSuccess: (action) => {
      void qc.invalidateQueries({ queryKey: agentActionsKey(action.thread_id) });
      void qc.invalidateQueries({ queryKey: qk.agentThreads.messages(action.thread_id) });
    },
  });
}

// --- Streaming chat -------------------------------------------------------------

/**
 * SSE reader for `POST /ai/threads/{id}/stream`.
 *
 * It re-implements the two OAC mechanisms `lib/api.ts` owns (dual
 * `Authorization` + `X-Radal-Token`, and `x-amz-content-sha256` over the EXACT
 * body string that is sent) because `fetch` — not axios — is what can read a
 * response body incrementally. `lib/api.ts` itself stays frozen.
 *
 * Behind CloudFront the Function URL is `buffered` by contract, so the whole
 * body lands in one chunk at the end. That is expected — the caller falls back
 * to the non-streaming `/messages` endpoint when no token arrives in time.
 */
export interface StreamCallbacks {
  onStart?: (data: Record<string, unknown>) => void;
  onToken: (delta: string) => void;
  onDone?: (data: Record<string, unknown>) => void;
  onError?: (code: string, detail: string) => void;
}

async function sha256Hex(input: string): Promise<string> {
  const bytes = new TextEncoder().encode(input);
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

export async function streamAgentMessage(
  threadId: number,
  content: string,
  callbacks: StreamCallbacks,
  signal?: AbortSignal,
): Promise<void> {
  const token = getAccessToken();
  const body = JSON.stringify({ content });
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    Accept: "text/event-stream",
  };
  if (token) {
    headers["Authorization"] = `Bearer ${token}`;
    headers["X-Radal-Token"] = token;
  }
  if (crypto?.subtle) headers["x-amz-content-sha256"] = await sha256Hex(body);

  const response = await fetch(`${API_URL}/ai/threads/${threadId}/stream`, {
    method: "POST",
    headers,
    body,
    signal,
  });

  if (!response.ok || !response.body) {
    const code = response.headers.get("X-Radal-AI-Error") ?? "ai_unavailable";
    let detail = `HTTP ${response.status}`;
    try {
      const payload = (await response.json()) as { detail?: unknown };
      if (typeof payload.detail === "string") detail = payload.detail;
    } catch {
      /* the body was not JSON — keep the status line */
    }
    callbacks.onError?.(code, detail);
    return;
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let event = "message";

  const dispatch = (name: string, raw: string) => {
    let data: Record<string, unknown> = {};
    try {
      data = raw ? (JSON.parse(raw) as Record<string, unknown>) : {};
    } catch {
      data = { raw };
    }
    if (name === "start") callbacks.onStart?.(data);
    else if (name === "token") callbacks.onToken(String(data.delta ?? ""));
    else if (name === "done") callbacks.onDone?.(data);
    else if (name === "error") {
      callbacks.onError?.(String(data.code ?? "ai_error"), String(data.detail ?? ""));
    }
  };

  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    // SSE frames are separated by a blank line; a frame is `event:` + `data:`.
    let split = buffer.indexOf("\n\n");
    while (split !== -1) {
      const frame = buffer.slice(0, split);
      buffer = buffer.slice(split + 2);
      let payload = "";
      for (const line of frame.split("\n")) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        else if (line.startsWith("data:")) payload += line.slice(5).trim();
      }
      if (event) dispatch(event, payload);
      event = "message";
      split = buffer.indexOf("\n\n");
    }
  }
}
