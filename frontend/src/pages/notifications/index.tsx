/**
 * `/notifications` — the full, mobile-friendly list behind the bell.
 *
 * Filters live in the URL (`?tray=todo|fyi&status=open|new|seen|closed`) so the
 * bell's "Ver todas" lands on the tray the broker was looking at. Rows open
 * their origin (and are marked seen); "Cerrar" closes a row by hand — the
 * server is idempotent and a closed row stays closed. "Por hacer" rows also
 * close by themselves once their origin is resolved.
 *
 * Platform users have no broker (the endpoints answer 403): the page explains
 * that instead of rendering an error.
 */
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { BellOff, Check, X } from "lucide-react";

import { PageHeader } from "@/components/common/PageHeader";
import { FadeUp } from "@/components/common/motion";
import { EmptyState, ErrorBanner, LoadingRows, apiError } from "@/components/common/kit";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useCan, usePermissions } from "@/lib/permissions";
import { useSetUrlParams, useUrlParam } from "@/lib/urlParams";
import {
  useCloseNotification,
  useMarkNotificationSeen,
  useNotificationCount,
  useNotifications,
  type Notification,
  type NotificationStatus,
  type NotificationTray,
} from "@/api/notifications";
import {
  NotificationItem,
  useOpenNotification,
} from "@/components/notifications/NotificationItem";
import { ScopeSwitch } from "@/components/notifications/NotificationBell";

const PAGE_SIZE = 30;

type TrayFilter = NotificationTray | "all";
type StatusFilter = "open" | NotificationStatus;

const TRAY_FILTERS: readonly TrayFilter[] = ["all", "todo", "fyi"];
const STATUS_FILTERS: readonly StatusFilter[] = ["open", "new", "seen", "closed"];

const TRAY_LABEL = {
  all: "notifications:filters.allTrays",
  todo: "notifications:tray.todo",
  fyi: "notifications:tray.fyi",
} as const satisfies Record<TrayFilter, string>;

const STATUS_LABEL = {
  open: "notifications:filters.open",
  new: "notifications:status.new",
  seen: "notifications:status.seen",
  closed: "notifications:filters.closed",
} as const satisfies Record<StatusFilter, string>;

function asTray(value: string | null): TrayFilter {
  return value === "todo" || value === "fyi" ? value : "all";
}
function asStatus(value: string | null): StatusFilter {
  return value === "new" || value === "seen" || value === "closed" ? value : "open";
}

function RowActions({ n }: { n: Notification }) {
  const { t } = useTranslation("notifications");
  const close = useCloseNotification();
  const seen = useMarkNotificationSeen();
  if (n.status === "closed") return null;
  return (
    <>
      {n.status === "new" ? (
        <Button
          variant="ghost"
          size="icon"
          className="h-9 w-9"
          aria-label={t("item.markSeen")}
          title={t("item.markSeen")}
          disabled={seen.isPending}
          onClick={() =>
            seen.mutate(n.id, { onError: (e) => toast.error(apiError(e, t("toast.error"))) })
          }
        >
          <Check className="h-4 w-4" />
        </Button>
      ) : null}
      <Button
        variant="ghost"
        size="icon"
        className="h-9 w-9"
        aria-label={t("item.close")}
        title={t("item.close")}
        disabled={close.isPending}
        onClick={() =>
          close.mutate(n.id, {
            onSuccess: () => toast.success(t("toast.closed")),
            onError: (e) => toast.error(apiError(e, t("toast.error"))),
          })
        }
      >
        <X className="h-4 w-4" />
      </Button>
    </>
  );
}

export default function NotificationsPage() {
  const { t } = useTranslation("notifications");
  const perms = usePermissions();
  const isPlatform = perms.data?.user_type === "platform";
  const enabled = !!perms.data && !isPlatform;

  const [trayParam] = useUrlParam("tray");
  const [statusParam] = useUrlParam("status");
  const [pageParam] = useUrlParam("page");
  const [scopeParam] = useUrlParam("scope");
  const setParams = useSetUrlParams();
  const tray = asTray(trayParam);
  const status = asStatus(statusParam);
  const page = Math.max(0, Number(pageParam) || 0);
  // "Todas" only for broker admins (server permission matrix); others: "Mías".
  const canSeeAll = useCan("Settings", "Manage").allowed;
  const scope = canSeeAll && scopeParam === "all" ? "all" : "mine";

  const count = useNotificationCount(enabled, scope);
  const list = useNotifications(
    {
      tray: tray === "all" ? undefined : tray,
      status: status === "open" ? undefined : status,
      scope,
      limit: PAGE_SIZE,
      offset: page * PAGE_SIZE,
    },
    enabled,
  );
  const openRow = useOpenNotification(undefined, { markSeen: scope === "mine" });

  // `status=new|seen` are open rows narrowed further: the server's `status`
  // param matches exactly, so both are served as-is.
  const items = list.data?.items ?? [];
  const total = list.data?.total ?? 0;
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  if (perms.data && isPlatform) {
    return (
      <>
        <PageHeader title={t("title")} subtitle={t("description")} />
        <Card>
          <EmptyState
            title={t("platform.title")}
            hint={t("platform.hint")}
            icon={<BellOff className="h-5 w-5" />}
          />
        </Card>
      </>
    );
  }

  return (
    <>
      <PageHeader
        title={t("title")}
        subtitle={t("description")}
      />

      <FadeUp delay={0.04}>
        <Card className="flex flex-col gap-3 p-3 sm:flex-row sm:flex-wrap sm:items-center sm:p-4">
          <Tabs
            value={tray}
            onValueChange={(v) => setParams({ tray: v === "all" ? null : v, page: null })}
          >
            <TabsList className="w-full sm:w-auto">
              {TRAY_FILTERS.map((value) => (
                <TabsTrigger key={value} value={value} className="flex-1 sm:flex-none">
                  {t(TRAY_LABEL[value])}
                  {value === "todo" && (count.data?.todo_new ?? 0) > 0 ? (
                    <span className="ml-1 tabular-nums">({count.data?.todo_new})</span>
                  ) : null}
                  {value === "fyi" && (count.data?.fyi_new ?? 0) > 0 ? (
                    <span className="ml-1 tabular-nums">({count.data?.fyi_new})</span>
                  ) : null}
                </TabsTrigger>
              ))}
            </TabsList>
          </Tabs>
          <Tabs
            value={status}
            onValueChange={(v) => setParams({ status: v === "open" ? null : v, page: null })}
          >
            <TabsList className="w-full overflow-x-auto sm:w-auto">
              {STATUS_FILTERS.map((value) => (
                <TabsTrigger key={value} value={value} className="flex-1 sm:flex-none">
                  {t(STATUS_LABEL[value])}
                </TabsTrigger>
              ))}
            </TabsList>
          </Tabs>
          {canSeeAll ? (
            <ScopeSwitch
              value={scope}
              onChange={(next) => setParams({ scope: next === "all" ? "all" : null, page: null })}
            />
          ) : null}
          <span className="text-caption text-ink-3 sm:ml-auto">
            {t("resultCount", { count: total })}
          </span>
        </Card>
      </FadeUp>

      <ErrorBanner error={list.error} />

      <FadeUp delay={0.08}>
        <Card className="p-1.5 sm:p-2">
          {list.isLoading ? (
            <div className="p-3">
              <LoadingRows rows={5} />
            </div>
          ) : list.isError ? (
            // The banner above carries the server's reason; an "all clear"
            // empty state here would read as good news.
            <EmptyState title={t("bell.error")} icon={<BellOff className="h-5 w-5" />} />
          ) : items.length === 0 ? (
            <EmptyState
              title={status === "closed" ? t("empty.closedTitle") : t("empty.title")}
              hint={status === "closed" ? t("empty.closedHint") : t("empty.hint")}
            />
          ) : (
            <ul className="flex flex-col divide-y divide-line">
              {items.map((n) => (
                <li key={n.id} className="py-0.5">
                  <NotificationItem
                    notification={n}
                    onOpen={openRow}
                    showRecipient={scope === "all"}
                    actions={scope === "mine" ? <RowActions n={n} /> : undefined}
                  />
                </li>
              ))}
            </ul>
          )}
        </Card>
      </FadeUp>

      {total > PAGE_SIZE ? (
        <div className="flex items-center justify-between gap-2">
          <Button
            variant="secondary"
            size="sm"
            className="h-10"
            disabled={page === 0}
            onClick={() => setParams({ page: page - 1 > 0 ? String(page - 1) : null })}
          >
            {t("pager.previous")}
          </Button>
          <span className="text-caption tabular-nums text-ink-3">
            {t("pager.page", { page: page + 1, pages })}
          </span>
          <Button
            variant="secondary"
            size="sm"
            className="h-10"
            disabled={page + 1 >= pages}
            onClick={() => setParams({ page: String(page + 1) })}
          >
            {t("pager.next")}
          </Button>
        </div>
      ) : null}
    </>
  );
}
