/**
 * The copilot — the broker's home (`/agent`) and every stored session
 * (`/agent/:threadId`). One route (`agent/:threadId?`), so this component
 * survives the hop from the welcome to the conversation it just opened: the
 * optimistic bubble and the in-flight turn are not lost to a remount.
 *
 *  - **No thread** → the `Welcome` hero (greeting, floating indicators, common
 *    queries). Sending from here — typed or a query card — creates the thread
 *    (`POST /ai/threads`), loads the query's skill when it names one
 *    (`POST /ai/threads/{id}/skills`), navigates to `/agent/:id` and sends.
 *  - **A thread** → the agentic conversation: @-mentions, slash commands, the
 *    skills strip, tool traces and pending-action cards.
 *
 * Every turn goes through the BUFFERED `POST /ai/agent/messages`
 * (`useAgentTurn`): a tool loop cannot stream usefully and CloudFront buffers
 * the body anyway, so the page shows an honest staged indicator ("Pensando…"
 * → "Consultando datos…"). `streamAgentMessage` stays in `api/ai.ts`, untouched.
 *
 * Rule 6 — suggest → human confirm → commit: a write the agent proposes is a
 * pending-action card (Confirmar / Descartar) executed ONLY by the confirm
 * endpoint. Nothing here auto-confirms.
 *
 * The session list lives in the rail (`components/agent/AgentSidebar`, the
 * Sidebar's agent mode); each turn invalidates `qk.agentThreads.lists()` so a
 * fresh title appears there as soon as the server sets it.
 */
import * as React from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Archive, BookOpen, Loader2, MessageSquareOff, Send } from "lucide-react";

import { ErrorBanner, apiError } from "@/components/common/kit";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
  useAgentActions,
  useAgentMessages,
  useAgentSkills,
  useAgentTurn,
  useConfirmAgentAction,
  useCreateAgentThread,
  useDiscardAgentAction,
  useSetThreadSkills,
  useThreadSkills,
  type AgentAction,
  type AgentSkill,
  type AgentTurnMessage,
} from "@/api/ai";
import { copilotHomeKey, useAgentSession, useUpdateAgentSession } from "@/api/copilot";
import { qk } from "@/api/keys";
import { ContextChips } from "@/components/agent/ContextChips";
import { CommandPalette, filterCommands, type SlashCommand } from "@/components/agent/CommandPalette";
import { MentionPopover, useMentionOptions, type MentionOption } from "@/components/agent/MentionPopover";
import { PendingActionCard } from "@/components/agent/PendingActionCard";
import {
  ActiveSkillChips,
  SkillSuggestion,
  SkillsStrip,
  suggestSkill,
} from "@/components/agent/SkillsStrip";
import type { AgentChip } from "@/components/agent/refs";
import { cn } from "@/lib/utils";
import { Bubble, TranscriptRow, buildRows } from "./Transcript";
import { Welcome, type QueryStart } from "./Welcome";

/** Server cap on `context_refs` per message (`MAX_CONTEXT_REFS`). */
const MAX_REFS = 6;
/** After this long in flight, the indicator admits the agent is using tools. */
const TOOLS_STAGE_MS = 4000;

const MENTION_LISTBOX = "agent-mention-listbox";
const COMMAND_LISTBOX = "agent-command-listbox";

function httpStatus(error: unknown): number {
  return (error as { response?: { status?: number } } | null)?.response?.status ?? 0;
}

/** The optimistic user bubble, pinned to the thread it was sent to. */
interface PendingUser {
  threadId: number;
  content: string;
  chips: AgentChip[];
}

export default function AgentPage() {
  const { t } = useTranslation("dashboard");
  const navigate = useNavigate();
  const qc = useQueryClient();

  // Thread from the URL --------------------------------------------------------------
  const { threadId: rawThreadId } = useParams<{ threadId?: string }>();
  const threadId = rawThreadId && /^\d+$/.test(rawThreadId) ? Number(rawThreadId) : null;
  React.useEffect(() => {
    if (rawThreadId && threadId === null) navigate("/agent", { replace: true });
  }, [rawThreadId, threadId, navigate]);
  const threadRef = React.useRef(threadId);
  threadRef.current = threadId;

  const session = useAgentSession(threadId);
  const updateSession = useUpdateAgentSession();
  const createThread = useCreateAgentThread();
  const messages = useAgentMessages(threadId ?? undefined);
  const actions = useAgentActions(threadId ?? undefined);
  const turn = useAgentTurn();
  const confirm = useConfirmAgentAction();
  const discard = useDiscardAgentAction();

  // Skills ----------------------------------------------------------------------------
  const skills = useAgentSkills();
  const threadSkills = useThreadSkills(threadId);
  const setSkills = useSetThreadSkills();
  /** Skills chosen on the welcome, before a conversation exists. */
  const [draftSkills, setDraftSkills] = React.useState<string[]>([]);
  const [skillsOpen, setSkillsOpen] = React.useState(false);
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
  /** True for the whole welcome → thread hop (create + skills), not just the POST. */
  const [starting, setStarting] = React.useState(false);
  const skillsLocked = turn.isPending || setSkills.isPending || starting;

  const applySkills = async (next: string[]) => {
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
  const [pendingUser, setPendingUser] = React.useState<PendingUser | null>(null);
  const [startingKey, setStartingKey] = React.useState<string | null>(null);
  const [stage, setStage] = React.useState<"thinking" | "tools">("thinking");

  React.useEffect(() => {
    if (!turn.isPending) return;
    setStage("thinking");
    const timer = window.setTimeout(() => setStage("tools"), TOOLS_STAGE_MS);
    return () => window.clearTimeout(timer);
  }, [turn.isPending]);

  /**
   * Chips, dismissals, draft skills and a half-typed command belong to one
   * conversation: switching sessions in the rail resets them — except for the
   * hop this page makes itself (welcome → the thread it just created), which
   * must keep the optimistic bubble and the in-flight turn.
   */
  const ownNavigation = React.useRef<number | null>(null);
  const previousThread = React.useRef(threadId);
  React.useEffect(() => {
    if (previousThread.current === threadId) return;
    previousThread.current = threadId;
    if (ownNavigation.current !== null && ownNavigation.current === threadId) {
      ownNavigation.current = null;
      return;
    }
    setChips([]);
    setDraftSkills([]);
    setDismissed(new Set());
    setPendingCommand(null);
    setClosedFor(null);
    setInput("");
    setCaret(0);
    setSkillsOpen(false);
  }, [threadId]);

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
    setPendingCommand(command);
    setText("@");
  };

  // Sending ----------------------------------------------------------------------------------

  /** One buffered turn on an EXISTING thread. */
  const runTurn = (target: number, content: string, sentChips: AgentChip[]) => {
    setPendingUser({ threadId: target, content, chips: sentChips });
    turn.mutate(
      { thread_id: target, content, context_refs: sentChips.map((chip) => chip.ref) },
      {
        onSuccess: () => {
          setPendingUser(null);
          // The server titles a thread from its first message: refresh the
          // header and the rail as soon as the turn lands.
          void qc.invalidateQueries({ queryKey: qk.agentThreads.detail(target) });
          void qc.invalidateQueries({ queryKey: qk.agentThreads.lists() });
        },
        onError: () => {
          setPendingUser(null);
          // Never lose what the broker typed — if they are still looking at it.
          if (threadRef.current === target) setInput(content);
        },
      },
    );
  };

  /**
   * Welcome → conversation: create the thread, load the skills, hop to it,
   * send. A failed turn leaves a thread with no message, which the server
   * does not list; the retry reuses it from the conversation view.
   */
  const startConversation = async (
    content: string,
    sentChips: AgentChip[],
    query: QueryStart | null,
  ) => {
    if (turn.isPending || starting) return;
    setStarting(true);
    setStartingKey(query?.key ?? null);
    let id: number;
    try {
      id = (await createThread.mutateAsync({ scope: "general" })).id;
    } catch (error) {
      setStarting(false);
      setStartingKey(null);
      if (!query) setInput(content);
      toast.error(apiError(error, t("agent.home.startError")));
      return;
    }
    const wanted = [...draftSkills];
    if (query?.skill && !wanted.includes(query.skill)) {
      // Only a skill the catalogue knows (the portal skill, say, is not listed
      // while the feature is off). Unknown catalogue = trust the server's hint.
      if (!skills.data || skills.data.some((s) => s.key === query.skill)) wanted.push(query.skill);
    }
    if (wanted.length > 0) {
      try {
        await setSkills.mutateAsync({ threadId: id, skills: wanted });
      } catch {
        toast.warning(t("agent.home.skillWarning"));
      }
    }
    setDraftSkills([]);
    setStarting(false);
    setStartingKey(null);
    ownNavigation.current = id;
    navigate(`/agent/${id}`);
    runTurn(id, content, sentChips);
  };

  const send = async () => {
    const content = input.trim();
    if (!content || turn.isPending || starting || setSkills.isPending) return;
    const sentChips = chips;
    setText("");
    setPendingCommand(null);
    setChips([]);
    if (!threadId) {
      await startConversation(content, sentChips, null);
      return;
    }
    runTurn(threadId, content, sentChips);
  };

  const onQuery = (query: QueryStart) => {
    void startConversation(query.prompt, chips, query);
    setChips([]);
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
        // A confirmed write can move the welcome's numbers.
        void qc.invalidateQueries({ queryKey: copilotHomeKey });
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
  const pendingHere = pendingUser && pendingUser.threadId === threadId ? pendingUser : null;
  const turnHere = turn.isPending && turn.variables?.thread_id === threadId;
  const turnErrorHere = !!turn.error && turn.variables?.thread_id === threadId;

  const bottomRef = React.useRef<HTMLDivElement | null>(null);
  React.useEffect(() => {
    if (threadId === null) return;
    bottomRef.current?.scrollIntoView({ block: "end" });
  }, [threadId, rows.length, pendingHere, orphanActions.length, turnHere]);

  const notFound =
    threadId !== null && (httpStatus(messages.error) === 404 || httpStatus(session.error) === 404);
  const conversationEmpty = rows.length === 0 && !pendingHere && orphanActions.length === 0;
  // A thread with no message yet carries the server's dated fallback title;
  // until its first turn lands it reads as what it is — a new conversation.
  const fresh = !!session.data && !session.data.last_message_at && !session.data.message_count;
  const title =
    (!fresh && session.data?.title?.trim()) || t("agent.sessions.untitledNew");
  const composerLocked = turn.isPending || starting;

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      {/* Scroll region: the welcome, or the conversation. */}
      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto flex w-full max-w-[880px] flex-col px-4 pb-6 pt-5 sm:px-7">
          {threadId === null ? (
            <Welcome onStart={onQuery} startingKey={startingKey} disabled={composerLocked} />
          ) : notFound ? (
            <div className="flex flex-col items-center gap-3 py-20 text-center">
              <span className="grid h-10 w-10 place-items-center rounded-sm bg-paper-2 text-ink-3">
                <MessageSquareOff className="h-5 w-5" strokeWidth={1.5} aria-hidden />
              </span>
              <p className="text-h3 text-ink">{t("agent.session.notFound")}</p>
              <p className="max-w-md text-caption text-ink-3 [text-wrap:pretty]">
                {t("agent.session.notFoundHint")}
              </p>
              <Button asChild variant="secondary" size="sm" className="min-h-10 sm:min-h-8">
                <Link to="/agent">{t("agent.session.backHome")}</Link>
              </Button>
            </div>
          ) : (
            <div className="flex flex-col gap-4">
              <header className="flex min-w-0 flex-col gap-1 border-b border-line pb-3">
                {session.isLoading && !pendingHere ? (
                  <Skeleton className="h-6 w-2/3" />
                ) : (
                  <h1 className="truncate text-h2 text-ink" title={title}>
                    {title}
                  </h1>
                )}
                <p className="text-caption text-ink-3">{t("agent.action.writesNote")}</p>
              </header>

              {session.data?.is_archived ? (
                <div className="flex flex-wrap items-center gap-3 rounded-lg bg-paper-2 px-3.5 py-2.5">
                  <Archive className="h-4 w-4 shrink-0 text-ink-3" strokeWidth={1.5} aria-hidden />
                  <p className="min-w-0 flex-1 text-caption text-ink-2">
                    {t("agent.session.archivedBanner")}
                  </p>
                  <Button
                    size="sm"
                    variant="secondary"
                    className="min-h-10 sm:min-h-8"
                    disabled={updateSession.isPending}
                    onClick={() =>
                      updateSession.mutate(
                        { threadId, is_archived: false },
                        {
                          onSuccess: () => toast.success(t("agent.sessions.restoredToast")),
                          onError: (error) =>
                            toast.error(apiError(error, t("agent.sessions.restoreError"))),
                        },
                      )
                    }
                  >
                    {t("agent.sessions.restore")}
                  </Button>
                </div>
              ) : null}

              {messages.isLoading && !pendingHere ? (
                <div className="flex flex-col gap-3">
                  <Skeleton className="h-14 w-3/5 self-end" />
                  <Skeleton className="h-24 w-4/5" />
                </div>
              ) : messages.isError && !notFound ? (
                <ErrorBanner error={messages.error} />
              ) : conversationEmpty && !turnHere ? (
                <p className="py-10 text-center text-caption text-ink-3">{t("agent.emptyHint")}</p>
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
                  {pendingHere ? (
                    <Bubble role="user" content={pendingHere.content} chips={pendingHere.chips} />
                  ) : null}
                  {turnHere ? (
                    <li className="flex items-center gap-2 text-caption text-ink-3 sm:ps-9" aria-live="polite">
                      <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
                      <span className="animate-pulse">
                        {stage === "tools" ? t("agent.progress.tools") : t("agent.progress.thinking")}
                      </span>
                    </li>
                  ) : null}
                </ul>
              )}

              {turnErrorHere ? (
                <ErrorBanner
                  error={
                    // Provider down / not configured: a localized line, never
                    // a provider's developer message.
                    [502, 503, 504].includes(httpStatus(turn.error))
                      ? t("agent.providerUnavailable")
                      : turn.error
                  }
                />
              ) : null}
              <div ref={bottomRef} />
            </div>
          )}
        </div>
      </div>

      {/* Composer dock — always visible. */}
      {!notFound ? (
        <div className="shrink-0 border-t border-line bg-paper">
          <div className="mx-auto flex w-full max-w-[880px] flex-col gap-2 px-4 pb-[max(12px,env(safe-area-inset-bottom))] pt-3 sm:px-7">
            {skillsOpen ? (
              <SkillsStrip
                skills={skills.data}
                activeKeys={activeKeys}
                onToggle={toggleSkill}
                isLoading={skills.isLoading}
                isError={skills.isError}
                disabled={skillsLocked}
              />
            ) : null}
            {skillsOpen && turn.isPending ? (
              <p className="-mt-1 text-caption text-ink-3" aria-live="polite">
                {t("agent.skills.waitTurn")}
              </p>
            ) : null}

            {suggestion ? (
              <SkillSuggestion
                skill={suggestion}
                disabled={skillsLocked}
                onActivate={() => activateSkill(suggestion.key)}
                onDismiss={() => setDismissed((prev) => new Set(prev).add(suggestion.key))}
              />
            ) : null}

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
                disabled={composerLocked}
                role="combobox"
                aria-label={t("agent.placeholder")}
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
                placeholder={threadId === null ? t("agent.home.placeholder") : t("agent.placeholder")}
                className="min-h-[60px] min-w-0 flex-1 resize-none rounded-lg border border-line bg-bone px-3 py-2 text-body text-ink shadow-elev outline-none transition-[border-color,box-shadow] duration-150 ease-out focus-visible:border-line-strong focus-visible:ring-2 focus-visible:ring-brand-ring disabled:opacity-60"
              />
              <Button
                className="min-h-10 shrink-0"
                disabled={!input.trim() || composerLocked}
                onClick={() => void send()}
                aria-label={t("agent.send")}
              >
                {starting && startingKey === null ? (
                  <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
                ) : (
                  <Send className="h-4 w-4" />
                )}
                <span className="hidden sm:inline">{t("agent.send")}</span>
              </Button>
            </div>

            <div className="flex items-center justify-between gap-3">
              <Button
                variant="ghost"
                size="sm"
                className={cn("-ml-2 min-h-10 sm:min-h-8", skillsOpen && "text-ink")}
                aria-expanded={skillsOpen}
                onClick={() => setSkillsOpen((v) => !v)}
              >
                <BookOpen className="h-3.5 w-3.5" strokeWidth={1.5} aria-hidden />
                {t("agent.skills.title")}
                {activeKeys.length > 0 ? (
                  <span className="rounded-full bg-brand-soft px-1.5 text-[11px] tabular-nums leading-[18px] text-brand-deep">
                    {activeKeys.length}
                  </span>
                ) : null}
              </Button>
              <p className="hidden min-w-0 truncate text-caption text-ink-3 sm:block">
                {t("agent.composerHint")}
              </p>
              <span className="text-caption text-ink-3 sm:hidden" aria-live="polite">
                {starting ? t("agent.home.starting") : ""}
              </span>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
