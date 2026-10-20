/**
 * The copilot home and the agent's stored sessions.
 *
 * - `GET /ai/copilot/home` — the welcome payload: the user's first name, the
 *   scope of the numbers ("mine" / "all"), a few live indicators about the
 *   book and the server-chosen common queries. Every field is optional on
 *   purpose: the UI maps KNOWN keys through explicit const maps
 *   (`pages/agent/catalog.ts`) and skips the rest.
 * - `GET /ai/threads?archived=&q=&limit=&offset=` — the session list behind the
 *   agent sub-sidebar. Threads with no message are not listed by the server;
 *   the title is set server-side from the first user message.
 * - `GET/PATCH/DELETE /ai/threads/{id}` — one session; DELETE archives (204),
 *   PATCH `{is_archived: false}` restores, PATCH `{title}` renames.
 *
 * All list keys live under `qk.agentThreads.lists()`, so the existing turn /
 * create invalidations in `api/ai.ts` refresh the sidebar for free.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import api from "@/lib/api";
import { qk } from "@/api/keys";
import type { AgentThread, ListResponse } from "@/api/types";

// --- Copilot home ----------------------------------------------------------------

export type CopilotTone = "neutral" | "warn" | "danger" | "ok";
export type CopilotUnit = "count" | "uf";

export interface CopilotIndicator {
  key: string;
  value: number | string | null;
  /** A UF amount riding under the main value (e.g. the overdue total). */
  secondary?: number | string | null;
  unit?: CopilotUnit | null;
  tone?: CopilotTone | null;
  /** In-app path (or absolute URL) the card opens. */
  link?: string | null;
  /**
   * Review r3 R7: `team` marks a TEAM INBOX (pagos por confirmar…) that stays
   * broker-wide even when the home shows "Mis números" — the UI labels it "Equipo".
   */
  scope?: "team" | "mine" | "all" | null;
}

export interface CopilotSuggestion {
  key: string;
  /** Skill key to load on the new thread before the prompt is sent. */
  skill?: string | null;
}

export interface CopilotHome {
  first_name?: string | null;
  role?: string | null;
  scope?: "mine" | "all" | null;
  indicators?: CopilotIndicator[] | null;
  suggestions?: CopilotSuggestion[] | null;
}

export const copilotHomeKey = ["ai", "copilot", "home"] as const;

export function useCopilotHome(enabled = true) {
  return useQuery({
    queryKey: copilotHomeKey,
    enabled,
    staleTime: 60 * 1000,
    retry: false,
    queryFn: async () => (await api.get<CopilotHome>("/ai/copilot/home")).data,
  });
}

// --- Sessions ----------------------------------------------------------------------

/** A thread as the session list returns it (additive fields optional). */
export interface AgentSession extends AgentThread {
  message_count?: number | null;
  preview?: string | null;
}

export interface SessionListParams {
  archived: boolean;
  q?: string;
  limit?: number;
  offset?: number;
}

export function useAgentSessions(params: SessionListParams, enabled = true) {
  const q = params.q?.trim() || undefined;
  const query = { archived: params.archived, q, limit: params.limit ?? 100, offset: params.offset ?? 0 };
  return useQuery({
    queryKey: qk.agentThreads.list({ ...query, view: "sessions" }),
    enabled,
    placeholderData: (previous) => previous,
    queryFn: async () => {
      const { data } = await api.get<ListResponse<AgentSession>>("/ai/threads", {
        params: query,
      });
      return data;
    },
  });
}

export function useAgentSession(threadId: number | null | undefined) {
  return useQuery({
    queryKey: qk.agentThreads.detail(threadId ?? 0),
    enabled: !!threadId,
    retry: false,
    queryFn: async () => (await api.get<AgentSession>(`/ai/threads/${threadId}`)).data,
  });
}

function useInvalidateSessions() {
  const qc = useQueryClient();
  return (threadId?: number) => {
    void qc.invalidateQueries({ queryKey: qk.agentThreads.lists() });
    if (threadId) void qc.invalidateQueries({ queryKey: qk.agentThreads.detail(threadId) });
  };
}

/** `PATCH /ai/threads/{id}` — rename and/or (un)archive. */
export function useUpdateAgentSession() {
  const invalidate = useInvalidateSessions();
  return useMutation({
    mutationFn: async ({
      threadId,
      ...payload
    }: {
      threadId: number;
      title?: string;
      is_archived?: boolean;
    }) => (await api.patch<AgentSession>(`/ai/threads/${threadId}`, payload)).data,
    onSettled: (_data, _error, variables) => invalidate(variables.threadId),
  });
}

/** `DELETE /ai/threads/{id}` — archives (204); restore with PATCH. */
export function useArchiveAgentSession() {
  const invalidate = useInvalidateSessions();
  return useMutation({
    mutationFn: async (threadId: number) => {
      await api.delete(`/ai/threads/${threadId}`);
      return threadId;
    },
    onSettled: (_data, _error, threadId) => invalidate(threadId),
  });
}
