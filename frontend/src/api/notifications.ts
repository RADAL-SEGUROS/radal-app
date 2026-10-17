/**
 * The bell (v12 02 §4) — `GET /notifications`, `GET /notifications/count`,
 * `POST /notifications/{id}/seen|close`.
 *
 * Personal rows by default: the server scopes by broker AND user (another
 * user's id is a 404). `scope=all` — broker ADMINS only (gate the switch with
 * `useCan("Settings", "Manage")`) — lists every open notification of the
 * broker, de-duplicated per origin, each with its `recipient_name`. Two trays: `todo` (Por hacer — they
 * close when their origin is resolved) and `fyi` (Para saber — they close when
 * opened or after 7 days). One live row per origin and recipient: a repeat
 * escalates in place instead of piling up.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import api from "@/lib/api";
import { clean, qk } from "@/api/keys";

export const NOTIFICATION_TRAYS = ["todo", "fyi"] as const;
export type NotificationTray = (typeof NOTIFICATION_TRAYS)[number];

/** `mine` = the caller's rows (default); `all` = the whole broker (admins). */
export type NotificationScope = "mine" | "all";

export const NOTIFICATION_STATUSES = ["new", "seen", "closed"] as const;
export type NotificationStatus = (typeof NOTIFICATION_STATUSES)[number];

/** Every `NotificationKind` of `app/models/enums.py` — keep in lockstep. */
export const NOTIFICATION_KINDS = [
  "installment_due",
  "installment_overdue",
  "termination_risk",
  "direct_debit_rejected",
  "promise_broken",
  "plan_missing",
  "inquiry_reply",
  "evidence_pending",
  "payment_received",
  "native_collection_available",
  "portal_search_failed",
  "portal_search_done",
  "mail_account_error",
  "sensitive_action",
  "policy_linked_insured",
] as const;
export type NotificationKind = (typeof NOTIFICATION_KINDS)[number];

export interface Notification {
  id: number;
  tray: NotificationTray;
  kind: NotificationKind;
  level: string | null;
  origin_type: string;
  origin_id: number;
  /** Render data — JSON-safe (Decimal → string, date → ISO). */
  params: Record<string, unknown> | null;
  link_path: string | null;
  status: NotificationStatus;
  created_at: string;
  /** When the alert was last raised/escalated — the sort key. */
  updated_at: string;
  closed_at: string | null;
  /** `scope=all` only: who the row is addressed to. */
  recipient_name?: string | null;
}

export interface NotificationPage {
  items: Notification[];
  total: number;
  limit: number;
  offset: number;
}

export interface NotificationCount {
  todo_new: number;
  fyi_new: number;
}

export interface NotificationListParams {
  tray?: NotificationTray;
  /** Omitted → open rows (new + seen); `closed` → history. */
  status?: NotificationStatus;
  /** Omitted / `mine` → the caller's rows; `all` → the broker's (admins). */
  scope?: NotificationScope;
  limit?: number;
  offset?: number;
}

/** Poll period of the badge (07 §2: every 60 s). */
export const NOTIFICATION_POLL_MS = 60_000;

/**
 * The badge count. `mine` keeps the historic key (`qk.notifications.count`)
 * and sends no param, so it works against any server; `all` is its own entry.
 */
export function useNotificationCount(enabled = true, scope: NotificationScope = "mine") {
  return useQuery({
    queryKey: scope === "all" ? [...qk.notifications.count, "all"] : qk.notifications.count,
    enabled,
    refetchInterval: NOTIFICATION_POLL_MS,
    refetchIntervalInBackground: false,
    // A platform user has no broker (403): never hammer a failing endpoint.
    retry: false,
    queryFn: async () =>
      (
        await api.get<NotificationCount>("/notifications/count", {
          params: scope === "all" ? { scope } : undefined,
        })
      ).data,
  });
}

export function useNotifications(params: NotificationListParams = {}, enabled = true) {
  // `mine` is the server default: never sent, so the cache key matches the old one.
  const { scope, ...rest } = params;
  const effective: NotificationListParams = scope === "all" ? { ...rest, scope } : rest;
  return useQuery({
    queryKey: qk.notifications.list(effective),
    enabled,
    retry: false,
    queryFn: async () =>
      (await api.get<NotificationPage>("/notifications", { params: clean(effective) })).data,
  });
}

function useNotificationAction(action: "seen" | "close") {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (id: number) =>
      (await api.post<Notification>(`/notifications/${id}/${action}`)).data,
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.notifications.all });
    },
  });
}

/** new → seen (keeps `updated_at`). Idempotent; a closed row stays closed. */
export function useMarkNotificationSeen() {
  return useNotificationAction("seen");
}

/** Close a row (idempotent). */
export function useCloseNotification() {
  return useNotificationAction("close");
}
