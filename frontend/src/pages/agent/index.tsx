/**
 * The AI agent — an agentic conversation, not a plain chat.
 *
 * Every turn goes through the BUFFERED `POST /ai/agent/messages`
 * (`useAgentTurn`): context refs (@-mentions) + the server tool loop + write
 * PROPOSALS. A tool loop cannot stream usefully and CloudFront buffers the
 * body anyway, so this page shows an honest staged indicator instead
 * ("Pensando…" → "Consultando datos…"). The SSE helper `streamAgentMessage`
 * stays in `api/ai.ts`, untouched (its OAC re-implementation is frozen).
 *
 * Rule 6 — suggest → human confirm → commit: a write the agent proposes is
 * rendered as a pending-action card (Confirmar / Descartar) and is executed
 * ONLY by the confirm endpoint. Nothing here auto-confirms.
 *
 * v12 additions: the skills strip (`GET /ai/skills`), the thread's active
 * skills as removable chips (`POST /ai/threads/{id}/skills`), a suggested
 * skill from the server triggers while typing, labels for every registry
 * tool, and confirmed results that open absolute URLs (share / checkout
 * links) in a new tab and in-app paths through the router.
 */
import * as React from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { Bot, Loader2, Plus, Send, User as UserIcon } from "lucide-react";

import { PageHeader } from "@/components/common/PageHeader";
import { EmptyState, ErrorBanner, apiError } from "@/components/common/kit";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  useAgentActions,
  useAgentMessages,
  useAgentSkills,
  useAgentThreads,
  useAgentTurn,
  useConfirmAgentAction,
  useCreateAgentThread,
  useDiscardAgentAction,
  useSetThreadSkills,
  useThreadSkills,
  type AgentAction,
  type AgentSkill,
  type AgentToolCallMeta,
  type AgentTurnMessage,
  type ContextRef,
} from "@/api/ai";
import { ContextChips } from "@/components/agent/ContextChips";
import { CommandPalette, filterCommands, type SlashCommand } from "@/components/agent/CommandPalette";
import { MentionPopover, useMentionOptions, type MentionOption } from "@/components/agent/MentionPopover";
import { PendingActionCard } from "@/components/agent/PendingActionCard";
import { ToolTraceRow } from "@/components/agent/ToolTraceRow";
import {
  ActiveSkillChips,
  SkillSuggestion,
  SkillsStrip,
  suggestSkill,
} from "@/components/agent/SkillsStrip";
import { RefIcon, type AgentChip } from "@/components/agent/refs";
import { refTypeLabelKey } from "@/components/agent/labels";
import { formatDateTime } from "@/lib/format";
import { cn } from "@/lib/utils";

/** Server cap on `context_refs` per message (`MAX_CONTEXT_REFS`). */
const MAX_REFS = 6;
/** After this long in flight, the indicator admits the agent is using tools. */
const TOOLS_STAGE_MS = 4000;

const MENTION_LISTBOX = "agent-mention-listbox";
const COMMAND_LISTBOX = "agent-command-listbox";

// --- Transcript model ---------------------------------------------------------------

type Row =
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

function buildRows(
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

// --- Page ------------------------------------------------------------------------------

export default function AgentPage() {
  const { t } = useTranslation("dashboard");

  // Threads -------------------------------------------------------------------------
  const threads = useAgentThreads();
  const createThread = useCreateAgentThread();
  const [threadId, setThreadId] = React.useState<number | null>(null);
  /** "Nueva conversación" opens a draft: the first send creates the thread. */
  const [draft, setDraft] = React.useState(false);

  // Opens on the newest conversation that has messages. A thread with none
  // (a turn whose AI call failed after skills were set, say) is not a
  // conversation and never surfaces as an empty "Conversación #1" (review v12).
  React.useEffect(() => {
    if (threadId !== null || draft) return;
    const first = threads.data?.items.find((item) => item.last_message_at != null);
    if (first) setThreadId(first.id);
  }, [threadId, draft, threads.data]);

  const messages = useAgentMessages(threadId ?? undefined);
  const actions = useAgentActions(threadId ?? undefined);
  const turn = useAgentTurn();
  const confirm = useConfirmAgentAction();
  const discard = useDiscardAgentAction();

  // Skills ----------------------------------------------------------------------------
  const skills = useAgentSkills();
  const threadSkills = useThreadSkills(threadId);
  const setSkills = useSetThreadSkills();
  /** Skills chosen before the conversation exists — held here, not on a thread. */
  const [draftSkills, setDraftSkills] = React.useState<string[]>([]);
  const activeKeys: string[] = !threadId
    ? draftSkills
    : setSkills.isPending && setSkills.variables
      ? setSkills.variables.skills
      : (threadSkills.data?.active_skills ?? []).map((s) => s.key);
  const skillByKey = React.useMemo(() => {
    const map = new Map<string, AgentSkill>();
    for (const skill of skills.data ?? []) map.set(skill.key, skill);
    for (const skill of threadSkills.data?.active_skills ?? []) {
      if (!map.has(skill.key)) map.set(skill.key, skill);
    }
    return map;
  }, [skills.data, threadSkills.data]);
  const activeSkills = activeKeys
    .map((key) => skillByKey.get(key))
    .filter((s): s is AgentSkill => !!s);
  const [dismissed, setDismissed] = React.useState<Set<string>>(() => new Set());
  /**
   * Skill changes are locked while a turn is in flight: in draft mode the turn
   * itself creates the thread server-side, so `ensureThread()` would create a
   * second, orphan thread and the skill would never reach the conversation.
   */
  const skillsLocked = turn.isPending || setSkills.isPending || createThread.isPending;

  const ensureThread = async (): Promise<number> => {
    if (threadId) return threadId;
    const thread = await createThread.mutateAsync({ scope: "general" });
    setDraft(false);
    setThreadId(thread.id);
    return thread.id;
  };

  const applySkills = async (next: string[]) => {
    // No conversation yet: keep the choice locally. Creating a thread just to
    // hold skills left an empty conversation behind whenever the first turn
    // then failed; the thread is created on the first send instead.
    if (!threadId) {
      setDraftSkills(next);
      return;
    }
    try {
      await setSkills.mutateAsync({ threadId, skills: next });
    } catch (error) {
      toast.error(apiError(error, t("agent.skills.updateError")));
    }
  };
  const toggleSkill = (key: string) => {
    const next = activeKeys.includes(key)
      ? activeKeys.filter((k) => k !== key)
      : [...activeKeys, key];
    void applySkills(next);
  };
  const activateSkill = (key: string) => {
    if (activeKeys.includes(key) || !skillByKey.has(key)) return;
    void applySkills([...activeKeys, key]);
  };

  // Composer ------------------------------------------------------------------------------
  const textareaRef = React.useRef<HTMLTextAreaElement | null>(null);
  const [input, setInput] = React.useState("");
  const [caret, setCaret] = React.useState(0);
  const [chips, setChips] = React.useState<AgentChip[]>([]);
  const [closedFor, setClosedFor] = React.useState<string | null>(null);
  const [activeIndex, setActiveIndex] = React.useState(0);
  const [pendingCommand, setPendingCommand] = React.useState<SlashCommand | null>(null);
  const [pendingUser, setPendingUser] = React.useState<{ content: string; chips: AgentChip[] } | null>(null);
  const [stage, setStage] = React.useState<"thinking" | "tools">("thinking");


  React.useEffect(() => {
    if (!turn.isPending) return;
    setStage("thinking");
    const timer = window.setTimeout(() => setStage("tools"), TOOLS_STAGE_MS);
    return () => window.clearTimeout(timer);
  }, [turn.isPending]);

  const beforeCaret = input.slice(0, caret);
  const popoverClosed = closedFor === input;
  const mentionMatch = /(^|\s)@([^\s@]{0,40})$/.exec(beforeCaret);
  const mentionQuery = !popoverClosed && mentionMatch ? mentionMatch[2] : null;
  const slashMatch = mentionQuery === null ? /^\/(\S*)$/.exec(beforeCaret) : null;
  const slashQuery = !popoverClosed && slashMatch ? slashMatch[1] : null;
  const commands = slashQuery !== null ? filterCommands(slashQuery) : [];
  const mention = useMentionOptions(mentionQuery);

  const listLength = mentionQuery !== null ? mention.options.length : commands.length;
  React.useEffect(() => {
    setActiveIndex(0);
  }, [mentionQuery, slashQuery]);

  const setText = (text: string, caretAt?: number) => {
    setInput(text);
    const position = caretAt ?? text.length;
    setCaret(position);
    window.requestAnimationFrame(() => {
      const el = textareaRef.current;
      if (!el) return;
      el.focus();
      el.setSelectionRange(position, position);
    });
  };

  const applyCommandTemplate = (command: SlashCommand, chip: AgentChip | null) => {
    const text = t(command.templateKey, { chip: chip?.label ?? "" });
    const quotes = text.indexOf('""');
    setText(text, quotes >= 0 ? quotes + 1 : undefined);
  };

  const chipFits = (command: SlashCommand, chip: AgentChip) =>
    command.needsChip !== null &&
    (command.needsChip.length === 0 ||
      command.needsChip.some((kind) => kind === chip.ref.entity_type));

  const selectMention = (option: MentionOption) => {
    const query = mentionQuery ?? "";
    const start = beforeCaret.length - query.length - 1;
    const text = input.slice(0, Math.max(0, start)) + input.slice(caret);
    let added = option.chip;
    if (!chips.some((c) => c.key === option.chip.key)) {
      if (chips.length >= MAX_REFS) {
        toast.error(t("agent.chips.limit", { max: MAX_REFS }));
        added = chips[chips.length - 1];
      } else {
        setChips((prev) => [...prev, option.chip]);
      }
    }
    if (pendingCommand && added && chipFits(pendingCommand, added)) {
      const command = pendingCommand;
      setPendingCommand(null);
      applyCommandTemplate(command, added);
      return;
    }
    setText(text, Math.max(0, start));
  };

  const selectCommand = (command: SlashCommand) => {
    if (command.skill) activateSkill(command.skill);
    if (command.needsChip === null) {
      applyCommandTemplate(command, null);
      return;
    }
    const chip = chips.find((c) => chipFits(command, c)) ?? null;
    if (chip) {
      applyCommandTemplate(command, chip);
      return;
    }
    // The command needs a ref first: open the @ popover, then prefill.
    setPendingCommand(command);
    setText("@");
  };

  const send = async () => {
    const content = input.trim();
    // A thread may be being created right now; sending before it lands would
    // make the server open a second thread.
    if (!content || turn.isPending || createThread.isPending || setSkills.isPending) return;
    const sentChips = chips;
    setPendingUser({ content, chips: sentChips });
    setText("");
    setPendingCommand(null);
    const restore = () => {
      setPendingUser(null);
      setInput(content); // never lose what the broker typed
    };

    // Skills picked in a draft must reach the FIRST turn: only then is a
    // thread opened ahead of it (and, if the turn fails, it stays unlisted —
    // it has no messages — and the retry reuses it).
    let target = threadId;
    if (!target && draftSkills.length > 0) {
      try {
        target = await ensureThread();
        await setSkills.mutateAsync({ threadId: target, skills: draftSkills });
        setDraftSkills([]);
      } catch (error) {
        restore();
        toast.error(apiError(error, t("agent.skills.updateError")));
        return;
      }
    }

    turn.mutate(
      {
        thread_id: target ?? undefined,
        content,
        context_refs: sentChips.map((chip) => chip.ref),
      },
      {
        onSuccess: (data) => {
          setDraft(false);
          setThreadId(data.thread.id);
          setPendingUser(null);
        },
        onError: restore,
      },
    );
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    const open = mentionQuery !== null || slashQuery !== null;
    if (open) {
      if (e.key === "Escape") {
        e.preventDefault();
        setClosedFor(input);
        setPendingCommand(null);
        return;
      }
      if (listLength > 0) {
        if (e.key === "ArrowDown") {
          e.preventDefault();
          setActiveIndex((i) => (i + 1) % listLength);
          return;
        }
        if (e.key === "ArrowUp") {
          e.preventDefault();
          setActiveIndex((i) => (i - 1 + listLength) % listLength);
          return;
        }
        if (e.key === "Enter" || e.key === "Tab") {
          e.preventDefault();
          if (mentionQuery !== null) {
            const option = mention.options[activeIndex];
            if (option) selectMention(option);
          } else {
            const command = commands[activeIndex];
            if (command) selectCommand(command);
          }
          return;
        }
      }
    }
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void send();
    }
  };

  const suggestion = React.useMemo(() => {
    if (mentionQuery !== null || slashQuery !== null) return null;
    const exclude = new Set<string>([...activeKeys, ...dismissed]);
    return suggestSkill(input, skills.data, exclude);
  }, [input, skills.data, activeKeys, dismissed, mentionQuery, slashQuery]);

  // Actions ------------------------------------------------------------------------------
  const actionsById = React.useMemo(() => {
    const map = new Map<number, AgentAction>();
    for (const action of actions.data?.items ?? []) map.set(action.id, action);
    // A just-finished turn carries its proposals before the refetch lands.
    for (const action of turn.data?.pending_actions ?? []) {
      if (action.thread_id === threadId && !map.has(action.id)) map.set(action.id, action);
    }
    return map;
  }, [actions.data, turn.data, threadId]);

  const actionMutating = confirm.isPending || discard.isPending;
  const onConfirm = (action: AgentAction) =>
    confirm.mutate(action.id, {
      onSuccess: (result) => {
        if (result.status === "failed") toast.error(result.error ?? t("agent.action.failed"));
        else toast.success(t("agent.action.confirmedToast"));
      },
      onError: (error) => toast.error(apiError(error, t("agent.action.failed"))),
    });
  const onDiscard = (action: AgentAction) =>
    discard.mutate(action.id, {
      onSuccess: () => toast.success(t("agent.action.discardedToast")),
      onError: (error) => toast.error(apiError(error)),
    });

  // Transcript ---------------------------------------------------------------------------
  const items = (messages.data?.items ?? []) as AgentTurnMessage[];
  const { rows, rendered } = React.useMemo(
    () =>
      buildRows(
        items,
        actionsById,
        (key) => skillByKey.get(key)?.title ?? null,
        (key, opts) => t(key, opts),
      ),
    [items, actionsById, skillByKey, t],
  );
  const orphanActions = [...actionsById.values()].filter((a) => !rendered.has(a.id));

  const bottomRef = React.useRef<HTMLDivElement | null>(null);
  React.useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: "end" });
  }, [rows.length, pendingUser, orphanActions.length]);

  // Only real conversations are listed: a thread with no message yet is never
  // shown as an empty "Conversación #N".
  const threadItems = (threads.data?.items ?? []).filter((item) => item.last_message_at != null);
  const threadLabel = (thread: { id: number; title: string | null }) =>
    thread.title ?? t("agent.untitled", { id: thread.id });
  /** Chips, dismissals and a half-typed command belong to one conversation. */
  const resetComposer = () => {
    setChips([]);
    setDraftSkills([]);
    setDismissed(new Set());
    setPendingCommand(null);
    setClosedFor(null);
  };
  const openThread = (id: number) => {
    if (id === threadId) return;
    resetComposer();
    setDraft(false);
    setThreadId(id);
  };
  const startDraft = () => {
    resetComposer();
    setDraft(true);
    setThreadId(null);
    setText("");
  };

  const conversationEmpty = rows.length === 0 && !pendingUser && orphanActions.length === 0;

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        title={t("agent.title")}
        subtitle={t("agent.subtitle")}
        actions={
          <Button
            size="sm"
            variant="secondary"
            className="min-h-10 sm:min-h-8"
            disabled={turn.isPending}
            onClick={startDraft}
          >
            <Plus className="h-4 w-4" />
            {t("agent.newThread")}
          </Button>
        }
      />

      <div className="grid gap-4 lg:grid-cols-[minmax(0,260px)_minmax(0,1fr)]">
        {/* Threads — a list on desktop, a compact picker on a phone. */}
        <Card className="hidden max-h-[72vh] flex-col gap-1 overflow-y-auto p-2 lg:flex">
          <p className="px-3 pb-1 pt-2 text-caption font-medium text-ink-3">{t("agent.threads")}</p>
          {threads.isLoading ? (
            <Skeleton className="h-24 w-full" />
          ) : threadItems.length === 0 ? (
            <p className="p-3 text-caption text-ink-3">{t("agent.noThreads")}</p>
          ) : (
            threadItems.map((thread) => (
              <button
                key={thread.id}
                type="button"
                onClick={() => openThread(thread.id)}
                className={cn(
                  "rounded-[10px] px-3 py-2 text-left text-caption transition-[background-color,color] duration-150 ease-out",
                  thread.id === threadId
                    ? "bg-brand-soft font-medium text-brand-deep"
                    : "text-ink-2 hover:bg-paper-2",
                )}
              >
                <span className="block truncate">{threadLabel(thread)}</span>
                <span className="block truncate text-[11px] text-ink-3">
                  {formatDateTime(thread.last_message_at ?? thread.created_at)}
                </span>
              </button>
            ))
          )}
        </Card>

        <Card className="flex min-h-[70dvh] min-w-0 flex-col gap-3 p-3 sm:p-4 lg:h-[calc(100dvh-210px)] lg:min-h-[520px]">
          {threadItems.length > 0 ? (
            <div className="lg:hidden">
              <Select
                value={threadId ? String(threadId) : ""}
                onValueChange={(value) => openThread(Number(value))}
              >
                <SelectTrigger aria-label={t("agent.threadPicker")} className="h-10">
                  <SelectValue placeholder={t("agent.pickThread")} />
                </SelectTrigger>
                <SelectContent>
                  {threadItems.map((thread) => (
                    <SelectItem key={thread.id} value={String(thread.id)}>
                      {threadLabel(thread)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          ) : null}

          <SkillsStrip
            skills={skills.data}
            activeKeys={activeKeys}
            onToggle={toggleSkill}
            isLoading={skills.isLoading}
            isError={skills.isError}
            disabled={skillsLocked}
          />
          {turn.isPending ? (
            <p className="-mt-1 text-caption text-ink-3" aria-live="polite">
              {t("agent.skills.waitTurn")}
            </p>
          ) : null}

          <div className="-mx-1 min-h-0 flex-1 overflow-y-auto border-t border-line px-1 pt-3">
            {threadId && messages.isLoading ? (
              <Skeleton className="h-40 w-full" />
            ) : conversationEmpty ? (
              <EmptyState
                title={t("agent.empty")}
                hint={t("agent.emptyHint")}
                icon={<Bot />}
              />
            ) : (
              <ul className="flex flex-col gap-3">
                {rows.map((row) => (
                  <TranscriptRow
                    key={row.id}
                    row={row}
                    onConfirm={onConfirm}
                    onDiscard={onDiscard}
                    isMutating={actionMutating}
                  />
                ))}
                {orphanActions.length > 0 ? (
                  <li className="flex flex-col gap-2">
                    <p className="text-caption text-ink-3 sm:ps-9">{t("agent.action.unanchored")}</p>
                    {orphanActions.map((action) => (
                      <PendingActionCard
                        key={action.id}
                        action={action}
                        onConfirm={onConfirm}
                        onDiscard={onDiscard}
                        isMutating={actionMutating}
                      />
                    ))}
                  </li>
                ) : null}
                {pendingUser ? (
                  <Bubble role="user" content={pendingUser.content} chips={pendingUser.chips} />
                ) : null}
                {turn.isPending ? (
                  <li className="flex items-center gap-2 text-caption text-ink-3 sm:ps-9" aria-live="polite">
                    <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
                    <span className="animate-pulse">
                      {stage === "tools" ? t("agent.progress.tools") : t("agent.progress.thinking")}
                    </span>
                  </li>
                ) : null}
              </ul>
            )}
            <div ref={bottomRef} />
          </div>

          {turn.error ? (
            <ErrorBanner
              error={
                // Provider down / not configured: a localized line, never a
                // provider's developer message.
                [502, 503, 504].includes(
                  (turn.error as { response?: { status?: number } }).response?.status ?? 0,
                )
                  ? t("agent.providerUnavailable")
                  : turn.error
              }
            />
          ) : null}

          {suggestion ? (
            <SkillSuggestion
              skill={suggestion}
              disabled={skillsLocked}
              onActivate={() => activateSkill(suggestion.key)}
              onDismiss={() => setDismissed((prev) => new Set(prev).add(suggestion.key))}
            />
          ) : null}

          {/* Composer */}
          <div className="flex flex-col gap-2">
            {chips.length > 0 || activeSkills.length > 0 ? (
              <div className="flex flex-wrap items-center gap-1.5">
                <ActiveSkillChips
                  skills={activeSkills}
                  disabled={skillsLocked}
                  onRemove={(key) => toggleSkill(key)}
                />
                <ContextChips
                  chips={chips}
                  onRemove={(key) => setChips((prev) => prev.filter((c) => c.key !== key))}
                />
              </div>
            ) : null}
            <div className="relative flex items-end gap-2">
              {mentionQuery !== null ? (
                <MentionPopover
                  query={mentionQuery}
                  options={mention.options}
                  isSearching={mention.isSearching}
                  activeId={mention.options[activeIndex]?.id ?? null}
                  onSelect={selectMention}
                  listboxId={MENTION_LISTBOX}
                />
              ) : slashQuery !== null ? (
                <CommandPalette
                  commands={commands}
                  activeIndex={activeIndex}
                  onSelect={selectCommand}
                  listboxId={COMMAND_LISTBOX}
                />
              ) : null}
              <textarea
                ref={textareaRef}
                rows={2}
                value={input}
                disabled={turn.isPending}
                role="combobox"
                aria-expanded={mentionQuery !== null || slashQuery !== null}
                aria-controls={
                  mentionQuery !== null ? MENTION_LISTBOX : slashQuery !== null ? COMMAND_LISTBOX : undefined
                }
                aria-activedescendant={
                  mentionQuery !== null
                    ? mention.options[activeIndex]?.id
                    : slashQuery !== null && commands[activeIndex]
                      ? `agent-cmd-${commands[activeIndex].key}`
                      : undefined
                }
                aria-autocomplete="list"
                onChange={(e) => {
                  setInput(e.target.value);
                  setCaret(e.target.selectionStart ?? e.target.value.length);
                }}
                onSelect={(e) => setCaret(e.currentTarget.selectionStart ?? 0)}
                onKeyDown={onKeyDown}
                placeholder={t("agent.placeholder")}
                className="min-h-[60px] min-w-0 flex-1 resize-none rounded-[10px] border border-line bg-bg-recessed px-3 py-2 text-body text-ink outline-none transition-[border-color,box-shadow] duration-150 ease-out focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-60"
              />
              <Button
                className="min-h-10 shrink-0"
                disabled={!input.trim() || turn.isPending || createThread.isPending}
                onClick={() => void send()}
                aria-label={t("agent.send")}
              >
                <Send className="h-4 w-4" />
                <span className="hidden sm:inline">{t("agent.send")}</span>
              </Button>
            </div>
            <p className="hidden text-caption text-ink-3 sm:block">{t("agent.composerHint")}</p>
          </div>
        </Card>
      </div>
    </div>
  );
}

// --- Rows -------------------------------------------------------------------------------------

function TranscriptRow({
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

function Bubble({
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
