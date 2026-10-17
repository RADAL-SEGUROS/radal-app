/**
 * One bell row, shared by the popover and the full `/notifications` page.
 *
 * Clicking opens the origin (`link_path`) and marks the row seen; the origin
 * carries the action at hand (e.g. Registrar pago). A row with no link is not
 * a dead control: it is rendered as plain text and its only action is
 * "Marcar como vista".
 */
import * as React from "react";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import {
  AlertTriangle,
  Bell,
  CircleDollarSign,
  Link2,
  FileQuestion,
  Mail,
  Search,
  ShieldAlert,
  Zap,
} from "lucide-react";

import { cn } from "@/lib/utils";
import { safeHttpUrl } from "@/lib/url";
import type { Notification, NotificationKind } from "@/api/notifications";
import { useMarkNotificationSeen } from "@/api/notifications";
import { describeNotification, relativeTime } from "@/components/notifications/notificationCopy";

const KIND_ICON: Record<NotificationKind, React.ComponentType<{ className?: string }>> = {
  installment_due: CircleDollarSign,
  installment_overdue: AlertTriangle,
  termination_risk: ShieldAlert,
  direct_debit_rejected: AlertTriangle,
  promise_broken: AlertTriangle,
  plan_missing: FileQuestion,
  inquiry_reply: Mail,
  evidence_pending: Mail,
  payment_received: CircleDollarSign,
  native_collection_available: Zap,
  portal_search_failed: Search,
  portal_search_done: Search,
  mail_account_error: Mail,
  sensitive_action: ShieldAlert,
  policy_linked_insured: Link2,
};

/**
 * Internal paths stay in the SPA; anything absolute opens outside. `//host`
 * and `/\host` are protocol-relative to a browser (the URL parser reads a
 * backslash as a slash), and control characters have no place in a path —
 * none of them is treated as internal.
 */
export function isInternal(path: string): boolean {
  // eslint-disable-next-line no-control-regex
  return /^\/(?![/\\])/.test(path) && !/[\\\u0000-\u001f]/.test(path);
}

export function useOpenNotification(
  onNavigate?: () => void,
  options: { markSeen?: boolean } = {},
) {
  const navigate = useNavigate();
  const seen = useMarkNotificationSeen();
  // "Todas" (admins) lists other people's rows: opening one must not mark it
  // seen on their behalf (and the server would 404 another user's row).
  const markSeen = options.markSeen ?? true;
  return React.useCallback(
    (n: Notification) => {
      if (markSeen && n.status === "new") seen.mutate(n.id);
      if (!n.link_path) return;
      onNavigate?.();
      if (isInternal(n.link_path)) {
        navigate(n.link_path);
        return;
      }
      // Only an absolute http(s) URL leaves the SPA; anything else is ignored.
      const external = safeHttpUrl(n.link_path);
      if (external) window.open(external, "_blank", "noopener,noreferrer");
    },
    [navigate, onNavigate, seen, markSeen],
  );
}

export function NotificationItem({
  notification,
  onOpen,
  actions,
  compact,
  showRecipient,
}: {
  notification: Notification;
  onOpen: (n: Notification) => void;
  /** Trailing controls (the full page's "Cerrar"). */
  actions?: React.ReactNode;
  compact?: boolean;
  /** "Todas" (admins): say whose row it is. */
  showRecipient?: boolean;
}) {
  const { t, i18n } = useTranslation("notifications");
  const { title, body } = describeNotification(notification, t);
  const Icon = Object.prototype.hasOwnProperty.call(KIND_ICON, notification.kind)
    ? KIND_ICON[notification.kind]
    : Bell;
  const isNew = notification.status === "new";
  const isClosed = notification.status === "closed";
  const clickable = !!notification.link_path || (isNew && !showRecipient);

  const content = (
    <>
      <span
        className={cn(
          "mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg",
          notification.tray === "todo" ? "bg-warn-soft text-warn-text" : "bg-brand-soft text-brand-deep",
          isClosed && "bg-paper-2 text-ink-3",
        )}
      >
        <Icon className="h-4 w-4" />
      </span>
      <span className="min-w-0 flex-1">
        <span className="flex items-start justify-between gap-2">
          <span
            className={cn(
              "text-body leading-5",
              isNew ? "font-semibold text-ink" : "font-medium text-ink-2",
            )}
          >
            {title}
          </span>
          {isNew ? (
            <span
              aria-label={t("status.new")}
              className="mt-1.5 h-2 w-2 shrink-0 rounded-full bg-brand"
            />
          ) : null}
        </span>
        <span
          className={cn(
            "mt-0.5 block text-caption text-ink-3",
            compact && "line-clamp-2",
          )}
        >
          {body}
        </span>
        <span className="mt-1 block text-[11px] tabular-nums text-ink-3">
          {relativeTime(notification.updated_at, i18n.language)}
          {showRecipient
            ? ` · ${t("item.recipient", { name: notification.recipient_name || t("item.recipientUnknown") })}`
            : null}
          {!notification.link_path && isNew && !showRecipient ? ` · ${t("item.markSeen")}` : null}
        </span>
      </span>
    </>
  );

  return (
    <div className="flex items-start gap-2">
      {clickable ? (
        <button
          type="button"
          onClick={() => onOpen(notification)}
          className="flex min-h-[44px] min-w-0 flex-1 items-start gap-3 rounded-lg px-2 py-2 text-left transition-[background-color,transform] duration-150 ease-out hover:bg-paper-2 active:scale-[0.98]"
        >
          {content}
        </button>
      ) : (
        <div className="flex min-w-0 flex-1 items-start gap-3 px-2 py-2">{content}</div>
      )}
      {actions ? <div className="flex shrink-0 items-center gap-1 pt-2">{actions}</div> : null}
    </div>
  );
}
