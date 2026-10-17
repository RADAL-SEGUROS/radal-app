/**
 * The topbar notification bell (v12, lane F1).
 *
 * - Badge: Por hacer NEW rows (team spec: "con n° de Por hacer nuevas") from
 *   `GET /notifications/count`, polled every 60 s; a quiet dot flags new
 *   Para saber rows when there is no Por hacer to count.
 * - Popover with the two trays — Por hacer (piden acción; close when their
 *   origin is resolved) and Para saber (informan; close when opened or after 7
 *   days). Rows are fetched only while the popover is open.
 * - A row click opens its `link_path` and marks it seen. "Ver todas" goes to
 *   the full `/notifications` page.
 *
 * Platform users have no broker (the endpoints 403), so for them the bell is a
 * plain link to the full page, which explains it — never a dead control.
 * Keeps the `NotificationBell` export and the 38 px footprint `Topbar` mounts.
 */
import * as React from "react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { Bell, Inbox } from "lucide-react";

import { cn } from "@/lib/utils";
import { useCan, usePermissions } from "@/lib/permissions";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
  useNotificationCount,
  useNotifications,
  type NotificationScope,
  type NotificationTray,
} from "@/api/notifications";
import {
  NotificationItem,
  useOpenNotification,
} from "@/components/notifications/NotificationItem";

const TRIGGER_CLASS =
  "relative grid h-[38px] w-[38px] shrink-0 place-items-center rounded-sm border border-line bg-bone text-ink-3 transition-[background-color,border-color,color,transform] duration-150 ease-out hover:border-line-strong hover:bg-paper-2 hover:text-ink active:scale-[0.96]";

function badgeText(n: number): string {
  return n > 99 ? "99+" : String(n);
}

/**
 * Mías / Todas — broker admins only (`Settings.Manage`, from the server's
 * permission matrix). Ejecutivos never see it and keep "Mías".
 */
export function ScopeSwitch({
  value,
  onChange,
  className,
}: {
  value: NotificationScope;
  onChange: (scope: NotificationScope) => void;
  className?: string;
}) {
  const { t } = useTranslation("notifications");
  const options: { value: NotificationScope; label: string }[] = [
    { value: "mine", label: t("scope.mine") },
    { value: "all", label: t("scope.all") },
  ];
  return (
    <div
      role="group"
      aria-label={t("scope.label")}
      className={cn("inline-flex rounded-[10px] bg-paper-2 p-0.5", className)}
    >
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          aria-pressed={value === o.value}
          onClick={() => onChange(o.value)}
          className={cn(
            "min-h-7 rounded-sm px-2.5 text-caption transition-[background-color,color,box-shadow,transform] duration-150 ease-out active:scale-[0.96]",
            value === o.value ? "bg-bone font-medium text-ink shadow-elev" : "text-ink-3 hover:text-ink",
          )}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

function TrayList({
  tray,
  open,
  scope,
  onNavigate,
}: {
  tray: NotificationTray;
  open: boolean;
  scope: NotificationScope;
  onNavigate: () => void;
}) {
  const { t } = useTranslation("notifications");
  const list = useNotifications({ tray, scope, limit: 20 }, open);
  const openRow = useOpenNotification(onNavigate, { markSeen: scope === "mine" });
  const items = list.data?.items ?? [];

  if (list.isLoading) {
    return (
      <div className="flex flex-col gap-2 p-2">
        <Skeleton className="h-12 w-full" />
        <Skeleton className="h-12 w-full" />
        <Skeleton className="h-12 w-full" />
      </div>
    );
  }
  if (list.isError) {
    return <p className="px-3 py-6 text-center text-caption text-neg-text">{t("bell.error")}</p>;
  }
  if (items.length === 0) {
    return (
      <div className="flex flex-col items-center gap-2 px-4 py-8 text-center">
        <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-brand-soft text-brand-deep">
          <Inbox className="h-4 w-4" />
        </span>
        <p className="text-caption text-ink-3">
          {tray === "todo" ? t("empty.todo") : t("empty.fyi")}
        </p>
      </div>
    );
  }
  return (
    <ul className="flex max-h-[min(60vh,420px)] flex-col gap-0.5 overflow-y-auto p-1">
      {items.map((n) => (
        <li key={n.id}>
          <NotificationItem notification={n} onOpen={openRow} compact showRecipient={scope === "all"} />
        </li>
      ))}
    </ul>
  );
}

export function NotificationBell({ className }: { className?: string }) {
  const { t } = useTranslation("notifications");
  const perms = usePermissions();
  const isPlatform = perms.data?.user_type === "platform";
  const enabled = !!perms.data && !isPlatform;

  const canSeeAll = useCan("Settings", "Manage").allowed;
  const [scopeRaw, setScope] = React.useState<NotificationScope>("mine");
  const scope: NotificationScope = canSeeAll ? scopeRaw : "mine";

  // The badge is ALWAYS the caller's own "Por hacer"; the tabs follow the scope.
  const [open, setOpen] = React.useState(false);
  const [tray, setTray] = React.useState<NotificationTray>("todo");
  const count = useNotificationCount(enabled);
  const allCount = useNotificationCount(enabled && open && scope === "all", "all");

  const todoNew = count.data?.todo_new ?? 0;
  const fyiNew = count.data?.fyi_new ?? 0;
  const tabCounts = scope === "all" ? allCount.data : count.data;
  const tabTodo = tabCounts?.todo_new ?? 0;
  const tabFyi = tabCounts?.fyi_new ?? 0;
  const label =
    todoNew > 0 ? t("bell.labelWithCount", { count: todoNew }) : t("bell.label");

  if (!enabled) {
    return (
      <Link
        to="/notifications"
        aria-label={t("bell.label")}
        title={t("bell.label")}
        className={cn(TRIGGER_CLASS, className)}
      >
        <Bell className="h-[18px] w-[18px]" strokeWidth={1.75} />
      </Link>
    );
  }

  return (
    <Popover
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        // Open on the tray that needs attention.
        if (next) setTray(todoNew > 0 || fyiNew === 0 ? "todo" : "fyi");
      }}
    >
      <PopoverTrigger asChild>
        <button type="button" aria-label={label} title={label} className={cn(TRIGGER_CLASS, className)}>
          <Bell className="h-[18px] w-[18px]" strokeWidth={1.75} />
          {todoNew > 0 ? (
            <span className="absolute -right-1.5 -top-1.5 min-w-[18px] rounded-full bg-neg px-1 text-center text-[11px] font-semibold leading-[18px] text-white tabular-nums">
              {badgeText(todoNew)}
            </span>
          ) : fyiNew > 0 ? (
            <span
              aria-hidden
              className="absolute right-1.5 top-1.5 h-2 w-2 rounded-full bg-brand"
            />
          ) : null}
        </button>
      </PopoverTrigger>
      <PopoverContent
        align="end"
        sideOffset={8}
        className="w-[min(380px,calc(100vw-24px))] p-0"
      >
        <Tabs value={tray} onValueChange={(v) => setTray(v as NotificationTray)}>
          <div className="flex flex-wrap items-center justify-between gap-2 border-b border-line px-3 py-2.5">
            <span className="text-body font-semibold text-ink">{t("title")}</span>
            <TabsList>
              <TabsTrigger value="todo" className="text-caption">
                {t("tray.todo")}
                {tabTodo > 0 ? <span className="ml-1 tabular-nums">({tabTodo})</span> : null}
              </TabsTrigger>
              <TabsTrigger value="fyi" className="text-caption">
                {t("tray.fyi")}
                {tabFyi > 0 ? <span className="ml-1 tabular-nums">({tabFyi})</span> : null}
              </TabsTrigger>
            </TabsList>
            {canSeeAll ? (
              <div className="flex w-full items-center justify-between gap-2">
                <span className="text-caption text-ink-3">
                  {scope === "all" ? t("scope.allHint") : t("scope.mineHint")}
                </span>
                <ScopeSwitch value={scope} onChange={setScope} />
              </div>
            ) : null}
          </div>
          <TabsContent value="todo" className="mt-0">
            <TrayList
              tray="todo"
              scope={scope}
              open={open && tray === "todo"}
              onNavigate={() => setOpen(false)}
            />
          </TabsContent>
          <TabsContent value="fyi" className="mt-0">
            <TrayList
              tray="fyi"
              scope={scope}
              open={open && tray === "fyi"}
              onNavigate={() => setOpen(false)}
            />
          </TabsContent>
        </Tabs>
        <div className="border-t border-line p-2">
          <Button asChild variant="ghost" size="sm" className="w-full">
            <Link
              to={`/notifications?tray=${tray}${scope === "all" ? "&scope=all" : ""}`}
              onClick={() => setOpen(false)}
            >
              {t("bell.viewAll")}
            </Link>
          </Button>
        </div>
      </PopoverContent>
    </Popover>
  );
}

export default NotificationBell;
