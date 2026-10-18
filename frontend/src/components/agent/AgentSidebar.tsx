/**
 * AGENT-mode content of the single context-switching sidebar.
 *
 * Rendered by `Sidebar.tsx` whenever the route is `/agent` or `/agent/:id`,
 * exactly like group mode: a back row (to the main menu), "+ Nueva
 * conversación", a search box and the user's stored sessions grouped Hoy ·
 * Ayer · Últimos 7 días · Anteriores. Each row opens `/agent/:id`; its menu
 * renames (inline → `PATCH /ai/threads/{id} {title}`) or archives (`DELETE`,
 * with a Deshacer toast that PATCHes `is_archived: false`). A pinned
 * "Archivadas" toggle swaps the list for the archived ones, each with
 * Restaurar.
 *
 * The "back" row does not navigate: `/agent` IS the home, so there is no page
 * "behind" it. It asks the shell to show the global menu instead
 * (`onShowMainMenu`), and the global rail's Agente row brings this back.
 *
 * Titles come from the server (set from the first user message). A thread the
 * page has just created is not listed until its first turn lands; meanwhile the
 * active row is shown as "Nueva conversación" so the rail never contradicts
 * the page.
 */
import * as React from "react";
import { Link, NavLink, useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import {
  Archive,
  ArchiveRestore,
  ArrowLeft,
  Loader2,
  MessageSquare,
  MoreHorizontal,
  Pencil,
  Plus,
  Search,
  X,
} from "lucide-react";

import {
  useAgentSession,
  useAgentSessions,
  useArchiveAgentSession,
  useUpdateAgentSession,
  type AgentSession,
} from "@/api/copilot";
import { apiError } from "@/components/common/kit";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  RAIL_ROW_ACTIVE,
  RAIL_ROW_BASE,
  RAIL_ROW_IDLE,
  RailSectionLabel,
  railRowClass,
} from "@/components/layout/SidebarGlobal";
import { formatDate } from "@/lib/format";
import { can, usePermissions } from "@/lib/permissions";
import { cn } from "@/lib/utils";

// --- Time ------------------------------------------------------------------------------

type Bucket = "today" | "yesterday" | "week" | "older";

const BUCKETS: readonly { key: Bucket; labelKey: string }[] = [
  { key: "today", labelKey: "agent.sessions.today" },
  { key: "yesterday", labelKey: "agent.sessions.yesterday" },
  { key: "week", labelKey: "agent.sessions.week" },
  { key: "older", labelKey: "agent.sessions.older" },
];

function sessionDate(session: AgentSession): Date | null {
  const raw = session.last_message_at ?? session.created_at;
  if (!raw) return null;
  const d = new Date(raw);
  return Number.isNaN(d.getTime()) ? null : d;
}

function startOfDay(d: Date): number {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
}

const DAY = 24 * 60 * 60 * 1000;

function bucketOf(date: Date | null, now: Date): Bucket {
  if (!date) return "older";
  const days = Math.round((startOfDay(now) - startOfDay(date)) / DAY);
  if (days <= 0) return "today";
  if (days === 1) return "yesterday";
  if (days < 7) return "week";
  return "older";
}

/** "hace 5 min" · "hace 3 h" · "ayer" · "hace 3 días" · "12 oct". */
function useRelativeTime() {
  const { t, i18n } = useTranslation("dashboard");
  const lang = i18n.language?.startsWith("en") ? "en" : "es";
  return React.useCallback(
    (date: Date | null, now: Date): string => {
      if (!date) return "";
      const minutes = Math.floor((now.getTime() - date.getTime()) / 60000);
      if (minutes < 1) return t("agent.sessions.justNow");
      const rtf = new Intl.RelativeTimeFormat(lang, { numeric: "auto", style: "short" });
      if (minutes < 60) return rtf.format(-minutes, "minute");
      const days = Math.round((startOfDay(now) - startOfDay(date)) / DAY);
      if (days <= 0) return rtf.format(-Math.floor(minutes / 60), "hour");
      if (days < 7) return rtf.format(-days, "day");
      return formatDate(date, "d MMM");
    },
    [lang, t],
  );
}

// --- Sidebar -------------------------------------------------------------------------------

export function AgentSidebar({
  activeThreadId,
  onShowMainMenu,
  className,
}: {
  activeThreadId: number | null;
  onShowMainMenu: () => void;
  className?: string;
}) {
  const { t } = useTranslation("dashboard");
  const { t: tc } = useTranslation("common");
  const navigate = useNavigate();
  const { data: perms, isLoading: permsLoading } = usePermissions();
  const allowed = !permsLoading && can(perms, "Dashboard", "View");

  const [showArchived, setShowArchived] = React.useState(false);
  const [query, setQuery] = React.useState("");
  const [debounced, setDebounced] = React.useState("");
  React.useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(query.trim()), 250);
    return () => window.clearTimeout(timer);
  }, [query]);

  const list = useAgentSessions({ archived: showArchived, q: debounced }, allowed);
  const sessions = list.data?.items ?? [];
  const total = list.data?.total ?? sessions.length;

  const update = useUpdateAgentSession();
  const archive = useArchiveAgentSession();
  const [editingId, setEditingId] = React.useState<number | null>(null);

  // Recomputed per render: buckets roll over without a timer, which is enough
  // for a list that re-renders on every turn.
  const now = new Date();
  const relative = useRelativeTime();

  const restore = (session: AgentSession, toastKey: "restoredToast" | null) =>
    update.mutate(
      { threadId: session.id, is_archived: false },
      {
        onSuccess: () => {
          if (toastKey) toast.success(t("agent.sessions.restoredToast"));
        },
        onError: (error) => toast.error(apiError(error, t("agent.sessions.restoreError"))),
      },
    );

  const onArchive = (session: AgentSession) =>
    archive.mutate(session.id, {
      onSuccess: () => {
        if (session.id === activeThreadId) navigate("/agent");
        toast(t("agent.sessions.archivedToast"), {
          action: {
            label: t("agent.sessions.undo"),
            onClick: () => restore(session, null),
          },
        });
      },
      onError: (error) => toast.error(apiError(error, t("agent.sessions.archiveError"))),
    });

  const onRename = (session: AgentSession, title: string) => {
    setEditingId(null);
    const next = title.trim();
    if (!next || next === (session.title ?? "").trim()) return;
    update.mutate(
      { threadId: session.id, title: next },
      { onError: (error) => toast.error(apiError(error, t("agent.sessions.renameError"))) },
    );
  };

  // The thread the page just opened is listed only after its first turn: show
  // it as "Nueva conversación" while it is a real, empty, unarchived thread
  // (the detail query is shared with the page, so this costs no request).
  const activeDetail = useAgentSession(allowed ? activeThreadId : null);
  const activeMissing =
    !showArchived &&
    !debounced &&
    activeThreadId !== null &&
    list.isSuccess &&
    !!activeDetail.data &&
    !activeDetail.data.is_archived &&
    !activeDetail.data.last_message_at &&
    !sessions.some((s) => s.id === activeThreadId);

  const grouped = BUCKETS.map((bucket) => ({
    ...bucket,
    items: sessions.filter((s) => bucketOf(sessionDate(s), now) === bucket.key),
  })).filter((group) => group.items.length > 0 || (group.key === "today" && activeMissing));

  return (
    <div className={cn("flex min-h-0 flex-1 flex-col", className)}>
      {/* Header — the way back, then the one primary action of this rail. */}
      <div className="flex flex-col gap-2 px-3 pb-2 pt-2">
        <button
          type="button"
          onClick={onShowMainMenu}
          className="-mx-1 flex items-center gap-1.5 rounded-sm px-1 py-1 text-left text-caption text-ink-3 transition-[color,background-color] duration-150 ease-out hover:bg-paper-2 hover:text-ink"
        >
          <ArrowLeft className="h-[15px] w-[15px] shrink-0" strokeWidth={1.5} />
          <span className="truncate">{t("agent.sessions.back")}</span>
        </button>

        <NavLink
          to="/agent"
          end
          className={({ isActive }) =>
            cn(
              "flex min-h-10 items-center gap-2 rounded-sm border border-line bg-bone px-3 text-label text-ink shadow-elev transition-[background-color,border-color,transform] duration-150 ease-out hover:border-line-strong hover:bg-paper-2 active:scale-[0.98] lg:min-h-9",
              isActive && "border-brand-line",
            )
          }
        >
          <Plus className="h-4 w-4 shrink-0" strokeWidth={1.75} />
          <span className="truncate">{t("agent.sessions.new")}</span>
        </NavLink>

        <label className="relative block">
          <span className="sr-only">{t("agent.sessions.search")}</span>
          <Search
            className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-ink-3"
            strokeWidth={1.75}
            aria-hidden
          />
          <input
            type="search"
            maxLength={120}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={t("agent.sessions.search")}
            className="h-10 w-full rounded-sm border border-line bg-bone pl-8 pr-8 text-label text-ink outline-none transition-[border-color,box-shadow] duration-150 ease-out placeholder:font-normal placeholder:text-ink-3 focus-visible:border-line-strong focus-visible:ring-2 focus-visible:ring-brand-ring lg:h-8 [&::-webkit-search-cancel-button]:hidden"
          />
          {query ? (
            <button
              type="button"
              aria-label={t("agent.sessions.clearSearch")}
              onClick={() => setQuery("")}
              className="absolute right-1.5 top-1/2 grid h-6 w-6 -translate-y-1/2 place-items-center rounded-sm text-ink-3 transition-[background-color,color] duration-150 ease-out hover:bg-paper-2 hover:text-ink"
            >
              <X className="h-3.5 w-3.5" strokeWidth={1.75} />
            </button>
          ) : null}
        </label>
      </div>

      <nav aria-label={t("agent.sessions.title")} className="min-h-0 flex-1 overflow-y-auto px-3 pb-2">
        {showArchived ? (
          <RailSectionLabel className="pt-2">{t("agent.sessions.archived")}</RailSectionLabel>
        ) : null}

        {!allowed && !permsLoading ? null : list.isLoading ? (
          <div className="flex flex-col gap-1.5 px-3 py-2">
            <Skeleton className="h-5 w-full" />
            <Skeleton className="h-5 w-4/5" />
            <Skeleton className="h-5 w-3/5" />
          </div>
        ) : list.isError ? (
          <div className="flex flex-col gap-2 px-3 py-2">
            <p className="text-[11.5px] leading-snug text-ink-3">{t("agent.sessions.loadError")}</p>
            <Button
              size="sm"
              variant="secondary"
              disabled={list.isFetching}
              onClick={() => void list.refetch()}
            >
              {tc("actions.retry")}
            </Button>
          </div>
        ) : sessions.length === 0 && !activeMissing ? (
          <div className="px-3 py-2">
            {debounced ? (
              <p className="text-[12.5px] text-ink-3 [overflow-wrap:anywhere]">
                {t("agent.sessions.noResults", { q: debounced })}
              </p>
            ) : showArchived ? (
              <p className="text-[12.5px] text-ink-3">{t("agent.sessions.archivedEmpty")}</p>
            ) : (
              <>
                <p className="text-[12.5px] font-medium text-ink-3">{t("agent.sessions.empty")}</p>
                <p className="mt-0.5 text-[11.5px] leading-snug text-ink-3">
                  {t("agent.sessions.emptyHint")}
                </p>
              </>
            )}
          </div>
        ) : showArchived ? (
          <ul className="flex flex-col gap-0.5">
            {sessions.map((session) => (
              <ArchivedRow
                key={session.id}
                session={session}
                active={session.id === activeThreadId}
                when={relative(sessionDate(session), now)}
                busy={update.isPending && update.variables?.threadId === session.id}
                onRestore={() => restore(session, "restoredToast")}
              />
            ))}
          </ul>
        ) : (
          grouped.map((group) => (
            <div key={group.key}>
              <RailSectionLabel className="pt-3">{t(group.labelKey)}</RailSectionLabel>
              <ul className="flex flex-col gap-0.5">
                {group.key === "today" && activeMissing ? (
                  <li>
                    <div className={cn(RAIL_ROW_BASE, RAIL_ROW_ACTIVE, "min-h-[34px]")}>
                      <MessageSquare className="h-[14px] w-[14px] shrink-0" strokeWidth={1.5} aria-hidden />
                      <span className="truncate">{t("agent.sessions.untitledNew")}</span>
                    </div>
                  </li>
                ) : null}
                {group.items.map((session) => (
                  <SessionRow
                    key={session.id}
                    session={session}
                    active={session.id === activeThreadId}
                    when={relative(sessionDate(session), now)}
                    editing={editingId === session.id}
                    onEdit={() => setEditingId(session.id)}
                    onCancelEdit={() => setEditingId(null)}
                    onRename={(title) => onRename(session, title)}
                    onArchive={() => onArchive(session)}
                    archiving={archive.isPending && archive.variables === session.id}
                  />
                ))}
              </ul>
            </div>
          ))
        )}

        {list.isSuccess && total > sessions.length ? (
          <p className="px-3 pt-3 text-[11.5px] leading-snug text-ink-3">
            {t("agent.sessions.partial", { shown: sessions.length, total })}
          </p>
        ) : null}
      </nav>

      {/* Pinned — the archive toggle. */}
      <div className="shrink-0 border-t border-line px-2 py-2">
        <button
          type="button"
          aria-pressed={showArchived}
          onClick={() => {
            setShowArchived((v) => !v);
            setEditingId(null);
          }}
          className={cn(railRowClass(false), "w-full text-left")}
        >
          {showArchived ? (
            <MessageSquare className="h-[16px] w-[16px] shrink-0" strokeWidth={1.5} />
          ) : (
            <Archive className="h-[16px] w-[16px] shrink-0" strokeWidth={1.5} />
          )}
          <span className="truncate">
            {showArchived ? t("agent.sessions.hideArchived") : t("agent.sessions.showArchived")}
          </span>
        </button>
      </div>
    </div>
  );
}

// --- Rows ---------------------------------------------------------------------------------

function SessionRow({
  session,
  active,
  when,
  editing,
  onEdit,
  onCancelEdit,
  onRename,
  onArchive,
  archiving,
}: {
  session: AgentSession;
  active: boolean;
  when: string;
  editing: boolean;
  onEdit: () => void;
  onCancelEdit: () => void;
  onRename: (title: string) => void;
  onArchive: () => void;
  archiving: boolean;
}) {
  const { t } = useTranslation("dashboard");
  const title = session.title?.trim() || t("agent.untitled", { id: session.id });
  const inputRef = React.useRef<HTMLInputElement | null>(null);
  const [draft, setDraft] = React.useState(title);
  const committed = React.useRef(false);

  React.useEffect(() => {
    if (!editing) return;
    committed.current = false;
    setDraft(session.title?.trim() || "");
    window.requestAnimationFrame(() => inputRef.current?.select());
  }, [editing, session.title]);

  if (editing) {
    const commit = () => {
      if (committed.current) return;
      committed.current = true;
      onRename(draft);
    };
    return (
      <li>
        <input
          ref={inputRef}
          value={draft}
          maxLength={120}
          aria-label={t("agent.sessions.renameLabel")}
          onChange={(e) => setDraft(e.target.value)}
          onBlur={commit}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              commit();
            } else if (e.key === "Escape") {
              e.preventDefault();
              committed.current = true;
              onCancelEdit();
            }
          }}
          className="h-[34px] w-full rounded-sm border border-line-strong bg-bone px-2.5 text-label text-ink outline-none ring-2 ring-brand-ring"
        />
      </li>
    );
  }

  return (
    <li className="group relative">
      <Link
        to={`/agent/${session.id}`}
        title={session.preview ? `${title}\n${session.preview}` : title}
        aria-current={active ? "page" : undefined}
        className={cn(
          RAIL_ROW_BASE,
          active ? RAIL_ROW_ACTIVE : RAIL_ROW_IDLE,
          // The menu overlays the timestamp (which fades on hover) on desktop;
          // on touch it is always shown, so the row reserves its width.
          "min-h-[34px] pr-9 lg:pr-3",
        )}
      >
        <span className="min-w-0 flex-1 truncate">{title}</span>
        <span
          className={cn(
            "shrink-0 text-[11px] font-normal tabular-nums text-ink-3 transition-opacity duration-150 ease-out",
            "lg:group-focus-within:opacity-0 lg:group-hover:opacity-0",
          )}
        >
          {when}
        </span>
      </Link>
      <DropdownMenu>
        <DropdownMenuTrigger
          aria-label={t("agent.sessions.menu", { title })}
          className={cn(
            "absolute right-1 top-1/2 grid h-7 w-7 -translate-y-1/2 place-items-center rounded-sm text-ink-3 outline-none transition-[opacity,background-color,color] duration-150 ease-out hover:bg-paper-2 hover:text-ink focus-visible:opacity-100 focus-visible:ring-2 focus-visible:ring-brand-ring data-[state=open]:opacity-100",
            // Touch has no hover: the menu stays visible below `lg`.
            "opacity-100 lg:opacity-0 lg:group-focus-within:opacity-100 lg:group-hover:opacity-100",
          )}
        >
          {archiving ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
          ) : (
            <MoreHorizontal className="h-4 w-4" strokeWidth={1.75} />
          )}
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="w-44">
          <DropdownMenuItem onSelect={onEdit}>
            <Pencil className="h-4 w-4" />
            {t("agent.sessions.rename")}
          </DropdownMenuItem>
          <DropdownMenuItem onSelect={onArchive} disabled={archiving}>
            <Archive className="h-4 w-4" />
            {t("agent.sessions.archive")}
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    </li>
  );
}

function ArchivedRow({
  session,
  active,
  when,
  busy,
  onRestore,
}: {
  session: AgentSession;
  active: boolean;
  when: string;
  busy: boolean;
  onRestore: () => void;
}) {
  const { t } = useTranslation("dashboard");
  const title = session.title?.trim() || t("agent.untitled", { id: session.id });
  return (
    <li className="flex flex-col gap-1 rounded-sm py-0.5">
      <Link
        to={`/agent/${session.id}`}
        title={title}
        aria-current={active ? "page" : undefined}
        className={cn(RAIL_ROW_BASE, active ? RAIL_ROW_ACTIVE : RAIL_ROW_IDLE, "min-h-[34px]")}
      >
        <span className="min-w-0 flex-1 truncate">{title}</span>
        <span className="shrink-0 text-[11px] font-normal tabular-nums text-ink-3">{when}</span>
      </Link>
      <Button
        size="sm"
        variant="ghost"
        className="ml-2 min-h-9 self-start text-caption lg:min-h-7"
        disabled={busy}
        onClick={onRestore}
      >
        {busy ? (
          <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
        ) : (
          <ArchiveRestore className="h-3.5 w-3.5" strokeWidth={1.5} aria-hidden />
        )}
        {t("agent.sessions.restore")}
      </Button>
    </li>
  );
}
