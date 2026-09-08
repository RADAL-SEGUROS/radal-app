/**
 * The AI agent — streaming chat with a hard fallback.
 *
 * `POST /ai/threads/{id}/stream` is a real SSE endpoint and locally it truly
 * streams. Behind CloudFront the Function URL invoke mode is `buffered` by
 * contract (deployment.md, OAC constraint 4), so the whole body lands in one
 * chunk at the end. That is NOT a bug to fix by changing the invoke mode — it
 * would regress the OAC contract. Instead: if no token has arrived within
 * {@link FALLBACK_MS}, this page abandons the stream and replays the turn on
 * the non-streaming `POST /ai/threads/{id}/messages`, which persists both
 * messages exactly the same way.
 */
import * as React from "react";
import { useTranslation } from "react-i18next";
import { Bot, Plus, Send, User as UserIcon } from "lucide-react";

import { PageHeader } from "@/components/common/PageHeader";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState, ErrorBanner, MonoChip } from "@/pages/proposals/shared";
import {
  streamAgentMessage,
  useAgentMessages,
  useAgentThreads,
  useCreateAgentThread,
  useSendAgentMessage,
} from "@/api/ai";
import { qk } from "@/api/keys";
import { useQueryClient } from "@tanstack/react-query";
import { formatDateTime } from "@/lib/format";
import { cn } from "@/lib/utils";

/** How long to wait for the first SSE token before falling back. */
const FALLBACK_MS = 5000;

export default function AgentPage() {
  const { t } = useTranslation("dashboard");
  const { t: tc } = useTranslation("common");
  const qc = useQueryClient();

  const threads = useAgentThreads();
  const createThread = useCreateAgentThread();
  const [threadId, setThreadId] = React.useState<number | null>(null);

  // Land on the newest thread as soon as the list arrives.
  React.useEffect(() => {
    if (threadId === null && threads.data?.items.length) {
      setThreadId(threads.data.items[0].id);
    }
  }, [threadId, threads.data]);

  const messages = useAgentMessages(threadId ?? undefined);
  const sendFallback = useSendAgentMessage(threadId ?? 0);

  const [input, setInput] = React.useState("");
  const [streamed, setStreamed] = React.useState("");
  const [pendingUser, setPendingUser] = React.useState<string | null>(null);
  const [busy, setBusy] = React.useState(false);
  const [usedFallback, setUsedFallback] = React.useState(false);
  const [error, setError] = React.useState<unknown>(null);
  const abortRef = React.useRef<AbortController | null>(null);

  React.useEffect(() => () => abortRef.current?.abort(), []);

  const send = async () => {
    const content = input.trim();
    if (!content || !threadId || busy) return;

    setInput("");
    setPendingUser(content);
    setStreamed("");
    setUsedFallback(false);
    setError(null);
    setBusy(true);

    const controller = new AbortController();
    abortRef.current = controller;

    let sawToken = false;
    const timer = window.setTimeout(() => {
      if (!sawToken) controller.abort();
    }, FALLBACK_MS);

    const finish = () => {
      window.clearTimeout(timer);
      abortRef.current = null;
      setBusy(false);
      setPendingUser(null);
      setStreamed("");
      void qc.invalidateQueries({ queryKey: qk.agentThreads.messages(threadId) });
      void qc.invalidateQueries({ queryKey: qk.agentThreads.lists() });
    };

    try {
      await streamAgentMessage(
        threadId,
        content,
        {
          onToken: (delta) => {
            sawToken = true;
            setStreamed((prev) => prev + delta);
          },
          onDone: finish,
          onError: (_code, detail) => {
            window.clearTimeout(timer);
            setError(detail);
            setBusy(false);
            setPendingUser(null);
          },
        },
        controller.signal,
      );
      // A stream that ends without a `done` frame still needs the cleanup.
      if (abortRef.current) finish();
    } catch {
      // Aborted (or the network refused the stream) -> the buffered path.
      window.clearTimeout(timer);
      setUsedFallback(true);
      sendFallback.mutate(
        { content },
        {
          onSuccess: finish,
          onError: (err) => {
            setError(err);
            setBusy(false);
            setPendingUser(null);
          },
        },
      );
    }
  };

  const items = messages.data?.items ?? [];

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        title={t("agent.title")}
        subtitle={t("agent.subtitle")}
        actions={
          <Button
            size="sm"
            variant="secondary"
            disabled={createThread.isPending}
            onClick={() =>
              createThread.mutate(
                { scope: "general" },
                { onSuccess: (thread) => setThreadId(thread.id) },
              )
            }
          >
            <Plus className="h-4 w-4" />
            {t("agent.newThread")}
          </Button>
        }
      />

      <div className="grid gap-4 lg:grid-cols-[minmax(0,260px)_minmax(0,1fr)]">
        {/* Threads */}
        <Card className="flex max-h-[70vh] flex-col gap-1 overflow-y-auto p-2">
          {threads.isLoading ? (
            <Skeleton className="h-24 w-full" />
          ) : (threads.data?.items.length ?? 0) === 0 ? (
            <p className="p-3 text-caption text-text-muted">
              {t("agent.noThreads")}
            </p>
          ) : (
            threads.data?.items.map((thread) => (
              <button
                key={thread.id}
                type="button"
                onClick={() => setThreadId(thread.id)}
                className={cn(
                  "rounded-[10px] px-3 py-2 text-left text-caption transition-colors",
                  thread.id === threadId
                    ? "bg-[color-mix(in_srgb,var(--teal)_14%,transparent)] font-semibold text-teal-deep"
                    : "text-text-tertiary hover:bg-[color-mix(in_srgb,var(--ink)_5%,transparent)]",
                )}
              >
                <span className="block truncate">
                  {thread.title ?? `#${thread.id}`}
                </span>
                <span className="block truncate text-[11px] text-text-muted">
                  {formatDateTime(thread.last_message_at ?? thread.created_at)}
                </span>
              </button>
            ))
          )}
        </Card>

        {/* Conversation */}
        <Card className="flex min-h-[60vh] flex-col p-4">
          <div className="flex-1 overflow-y-auto">
            {!threadId ? (
              <EmptyState
                title={t("agent.pickThread")}
                icon={<Bot className="h-6 w-6" />}
              />
            ) : messages.isLoading ? (
              <Skeleton className="h-40 w-full" />
            ) : items.length === 0 && !pendingUser ? (
              <EmptyState
                title={t("agent.empty")}
                icon={<Bot className="h-6 w-6" />}
              />
            ) : (
              <ul className="flex flex-col gap-3">
                {items.map((message) => (
                  <Bubble
                    key={message.id}
                    role={message.role}
                    content={message.content ?? ""}
                    meta={message.model}
                  />
                ))}
                {pendingUser ? <Bubble role="user" content={pendingUser} /> : null}
                {streamed ? <Bubble role="assistant" content={streamed} streaming /> : null}
                {busy && !streamed ? (
                  <li className="text-caption text-text-muted">{tc("actions.loading")}</li>
                ) : null}
              </ul>
            )}
          </div>

          {usedFallback ? (
            <Badge variant="warn" className="mt-3 self-start">
              {t("agent.fallback")}
            </Badge>
          ) : null}

          {error ? <ErrorBanner error={error} className="mt-3" /> : null}

          <div className="mt-3 flex items-end gap-2">
            <textarea
              rows={2}
              value={input}
              disabled={!threadId || busy}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  void send();
                }
              }}
              placeholder={t("agent.placeholder")}
              className="flex-1 rounded-[11px] border border-line bg-bg-recessed px-3 py-2 text-body text-text-primary outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-60"
            />
            <Button disabled={!threadId || !input.trim() || busy} onClick={() => void send()}>
              <Send className="h-4 w-4" />
              {t("agent.send")}
            </Button>
          </div>
        </Card>
      </div>
    </div>
  );
}

function Bubble({
  role,
  content,
  meta,
  streaming,
}: {
  role: string;
  content: string;
  meta?: string | null;
  streaming?: boolean;
}) {
  const isUser = role === "user";
  return (
    <li className={cn("flex gap-2.5", isUser && "flex-row-reverse")}>
      <span
        className={cn(
          "mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full",
          isUser ? "bg-[color-mix(in_srgb,var(--blue)_15%,transparent)]" : "bg-[color-mix(in_srgb,var(--teal)_15%,transparent)]",
        )}
      >
        {isUser ? (
          <UserIcon className="h-3.5 w-3.5 text-blue-deep" />
        ) : (
          <Bot className="h-3.5 w-3.5 text-teal-deep" />
        )}
      </span>
      <div
        className={cn(
          "max-w-[80%] rounded-[12px] border px-3.5 py-2.5",
          isUser
            ? "border-[color-mix(in_srgb,var(--blue)_25%,transparent)] bg-[color-mix(in_srgb,var(--blue)_8%,transparent)]"
            : "border-line bg-bg-surface",
        )}
      >
        <p className="whitespace-pre-wrap text-body text-text-secondary">
          {content}
          {streaming ? <span className="animate-pulse">▍</span> : null}
        </p>
        {meta ? <MonoChip className="mt-1.5">{meta}</MonoChip> : null}
      </div>
    </li>
  );
}
