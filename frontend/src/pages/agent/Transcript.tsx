/**
 * The conversation transcript: the server's message list turned into rows —
 * user / assistant bubbles, tool traces, pending-action cards and the quiet
 * notes a resolved action leaves behind.
 *
 * Rule 6 — suggest → human confirm → commit: a write the agent proposes is
 * rendered as a `PendingActionCard` (Confirmar / Descartar) and executes ONLY
 * through the confirm endpoint. Nothing here auto-confirms.
 */
import { useTranslation } from "react-i18next";
import { Bot, User as UserIcon } from "lucide-react";

import type { AgentAction, AgentToolCallMeta, AgentTurnMessage, ContextRef } from "@/api/ai";
import { PendingActionCard } from "@/components/agent/PendingActionCard";
import { ToolTraceRow } from "@/components/agent/ToolTraceRow";
import { RefIcon, type AgentChip } from "@/components/agent/refs";
import { refTypeLabelKey } from "@/components/agent/labels";
import { cn } from "@/lib/utils";

// --- Transcript model ---------------------------------------------------------------

export type Row =
  | { kind: "user"; id: string; content: string; refs: ContextRef[] }
  | { kind: "assistant"; id: string; content: string; model: string | null }
  | { kind: "trace"; id: string; tool: string; status: string; content: string | null; summary: string | null }
  | { kind: "action"; id: string; action: AgentAction }
  | { kind: "note"; id: string; status: string; detail: string | null };

function parseJson(content: string | null): Record<string, unknown> | null {
  if (!content) return null;
  try {
    const parsed: unknown = JSON.parse(content);
    return parsed && typeof parsed === "object" && !Array.isArray(parsed)
      ? (parsed as Record<string, unknown>)
      : null;
  } catch {
    return null;
  }
}

function toolMeta(message: AgentTurnMessage): AgentToolCallMeta | null {
  const meta = message.tool_calls;
  if (!meta || Array.isArray(meta)) return null;
  return meta as AgentToolCallMeta;
}

const NOTE_STATUSES = new Set(["confirmed", "discarded", "failed"]);

export function buildRows(
  messages: AgentTurnMessage[],
  actionsById: Map<number, AgentAction>,
  skillTitle: (key: string) => string | null,
  t: (key: string, opts?: Record<string, unknown>) => string,
): { rows: Row[]; rendered: Set<number> } {
  const rows: Row[] = [];
  const rendered = new Set<number>();
  for (const message of messages) {
    const id = `m-${message.id}`;
    if (message.role === "user") {
      rows.push({
        kind: "user",
        id,
        content: message.content ?? "",
        refs: (message.context_refs ?? []).filter(
          (ref): ref is ContextRef =>
            !!ref && typeof ref.entity_type === "string" && typeof ref.entity_id === "number",
        ),
      });
      continue;
    }
    if (message.role === "assistant") {
      // Iteration messages carry only tool_calls; their tool rows follow.
      if (message.content && message.content.trim()) {
        rows.push({ kind: "assistant", id, content: message.content, model: message.model });
      }
      continue;
    }
    if (message.role !== "tool") continue;

    const meta = toolMeta(message);
    const tool = meta?.name ?? "";
    const status = meta?.status ?? "ok";
    const payload = parseJson(message.content);

    if (status === "pending_confirmation") {
      const actionId = Number(payload?.action_id ?? meta?.action_id);
      const action = Number.isFinite(actionId) ? actionsById.get(actionId) : undefined;
      if (action) {
        if (!rendered.has(action.id)) {
          rows.push({ kind: "action", id: `a-${action.id}`, action });
          rendered.add(action.id);
        }
        continue;
      }
    }
    if (NOTE_STATUSES.has(status) && meta?.action_id) {
      const detail = typeof payload?.detail === "string" ? payload.detail : null;
      rows.push({ kind: "note", id, status, detail });
      continue;
    }

    let summary: string | null = null;
    if (tool === "load_skill" && status === "ok") {
      const key = typeof payload?.key === "string" ? payload.key : null;
      const title =
        (typeof payload?.title === "string" ? payload.title : null) ?? (key ? skillTitle(key) : null);
      if (title) summary = t("agent.trace.skillLoaded", { title });
    }
    rows.push({ kind: "trace", id, tool, status, content: message.content, summary });
  }
  return { rows, rendered };
}

// --- Rows -------------------------------------------------------------------------------------

export function TranscriptRow({
  row,
  onConfirm,
  onDiscard,
  isMutating,
}: {
  row: Row;
  onConfirm: (action: AgentAction) => void;
  onDiscard: (action: AgentAction) => void;
  isMutating: boolean;
}) {
  const { t } = useTranslation("dashboard");
  switch (row.kind) {
    case "user":
      return <Bubble role="user" content={row.content} refs={row.refs} />;
    case "assistant":
      return <Bubble role="assistant" content={row.content} />;
    case "trace":
      return (
        <li>
          <ToolTraceRow tool={row.tool} status={row.status} content={row.content} summary={row.summary} />
        </li>
      );
    case "action":
      return (
        <li>
          <PendingActionCard
            action={row.action}
            onConfirm={onConfirm}
            onDiscard={onDiscard}
            isMutating={isMutating}
          />
        </li>
      );
    case "note": {
      const label =
        row.status === "confirmed"
          ? t("agent.notes.confirmed")
          : row.status === "failed"
            ? t("agent.notes.failed")
            : t("agent.notes.discarded");
      return (
        <li className="flex items-center gap-2 text-caption text-ink-3 sm:ps-9">
          <span className="h-px w-4 bg-line" aria-hidden />
          <span className="min-w-0 [text-wrap:pretty]">
            {label}
            {row.detail ? ` · ${row.detail}` : ""}
          </span>
        </li>
      );
    }
  }
}

export function Bubble({
  role,
  content,
  refs,
  chips,
}: {
  role: "user" | "assistant";
  content: string;
  /** Persisted refs (type + id only). */
  refs?: ContextRef[];
  /** Optimistic chips (with their labels). */
  chips?: AgentChip[];
}) {
  const { t } = useTranslation("dashboard");
  const isUser = role === "user";
  const refItems = chips?.length
    ? chips.map((chip) => ({ key: chip.key, type: chip.ref.entity_type, label: chip.label }))
    : (refs ?? []).map((ref) => ({
        key: `${ref.entity_type}:${ref.entity_id}`,
        type: ref.entity_type,
        label: `${t(refTypeLabelKey(ref.entity_type))} #${ref.entity_id}`,
      }));
  return (
    <li className={cn("flex gap-2.5", isUser && "flex-row-reverse")}>
      <span
        className={cn(
          "mt-0.5 hidden h-7 w-7 shrink-0 items-center justify-center rounded-full sm:flex",
          isUser ? "bg-paper-2 text-ink-2" : "bg-brand-soft text-brand-deep",
        )}
      >
        {isUser ? <UserIcon className="h-3.5 w-3.5" /> : <Bot className="h-3.5 w-3.5" />}
      </span>
      <div
        className={cn(
          "max-w-[92%] rounded-[12px] px-3.5 py-2.5 sm:max-w-[80%]",
          isUser ? "bg-paper-2" : "border border-line bg-bone",
        )}
      >
        {refItems.length > 0 ? (
          <div className="mb-1.5 flex flex-wrap gap-1">
            {refItems.map((ref) => (
              <span
                key={ref.key}
                className="inline-flex max-w-full items-center gap-1 rounded-[5px] bg-brand-soft px-1.5 py-0.5 text-[11px] text-brand-deep"
              >
                <RefIcon entityType={ref.type} className="h-3 w-3 shrink-0" />
                <span className="truncate">{ref.label}</span>
              </span>
            ))}
          </div>
        ) : null}
        <p className="whitespace-pre-wrap break-words text-body text-ink-2">{content}</p>
      </div>
    </li>
  );
}
