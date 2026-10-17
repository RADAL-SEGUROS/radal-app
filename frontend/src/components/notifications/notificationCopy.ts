/**
 * How each bell row reads — an EXPLICIT map from `NotificationKind` to its
 * i18n keys (no dynamic `t(\`kind.${kind}\`)`: a new enum value would compile
 * and then render a raw key). `params` is the server's render data; every
 * value used here is formatted on the client and falls back to "—".
 *
 * The params differ per raiser (see the backend callers): the clock's
 * installment alerts send `number` / `installment_count` / `due_date` /
 * `amount_uf` / `days_late`; the Forpay webhook sends `installment_number`;
 * mail/public-share evidence send `policy_number` and `insurer`; RPA sends
 * `portal_name` / `quote_count`; mail errors send `address`; the 2b.5
 * `policy_linked_insured` FYI sends `policy_number` / `insured_name` /
 * `uploaded_by`.
 */
import type { TFunction } from "i18next";
import { formatDate, formatUF } from "@/lib/format";
import type { Notification, NotificationKind } from "@/api/notifications";

export interface NotificationCopyKeys {
  title: string;
  body: string;
}

export const NOTIFICATION_COPY = {
  installment_due: {
    title: "notifications:kind.installment_due.title",
    body: "notifications:kind.installment_due.body",
  },
  installment_overdue: {
    title: "notifications:kind.installment_overdue.title",
    body: "notifications:kind.installment_overdue.body",
  },
  termination_risk: {
    title: "notifications:kind.termination_risk.title",
    body: "notifications:kind.termination_risk.body",
  },
  direct_debit_rejected: {
    title: "notifications:kind.direct_debit_rejected.title",
    body: "notifications:kind.direct_debit_rejected.body",
  },
  promise_broken: {
    title: "notifications:kind.promise_broken.title",
    body: "notifications:kind.promise_broken.body",
  },
  plan_missing: {
    title: "notifications:kind.plan_missing.title",
    body: "notifications:kind.plan_missing.body",
  },
  inquiry_reply: {
    title: "notifications:kind.inquiry_reply.title",
    body: "notifications:kind.inquiry_reply.body",
  },
  evidence_pending: {
    title: "notifications:kind.evidence_pending.title",
    body: "notifications:kind.evidence_pending.body",
  },
  payment_received: {
    title: "notifications:kind.payment_received.title",
    body: "notifications:kind.payment_received.body",
  },
  native_collection_available: {
    title: "notifications:kind.native_collection_available.title",
    body: "notifications:kind.native_collection_available.body",
  },
  portal_search_failed: {
    title: "notifications:kind.portal_search_failed.title",
    body: "notifications:kind.portal_search_failed.body",
  },
  portal_search_done: {
    title: "notifications:kind.portal_search_done.title",
    body: "notifications:kind.portal_search_done.body",
  },
  mail_account_error: {
    title: "notifications:kind.mail_account_error.title",
    body: "notifications:kind.mail_account_error.body",
  },
  sensitive_action: {
    title: "notifications:kind.sensitive_action.title",
    body: "notifications:kind.sensitive_action.body",
  },
  policy_linked_insured: {
    title: "notifications:kind.policy_linked_insured.title",
    body: "notifications:kind.policy_linked_insured.body",
  },
} as const satisfies Record<NotificationKind, NotificationCopyKeys>;

/** `sensitive_action` carries `params.event`; each known event has its own line. */
const SENSITIVE_EVENT_BODY = {
  chargeback: "notifications:sensitiveEvent.chargeback",
  provider_payment_unconverted: "notifications:sensitiveEvent.provider_payment_unconverted",
} as const;

const FALLBACK = {
  title: "notifications:kind.unknown.title",
  body: "notifications:kind.unknown.body",
} as const;

/** Own-key lookup: `"constructor" in {}` is true, a plain index is not safe. */
function own<T>(map: Record<string, T>, key: string): T | undefined {
  return Object.prototype.hasOwnProperty.call(map, key) ? map[key] : undefined;
}

function str(value: unknown): string | null {
  if (value === null || value === undefined || value === "") return null;
  return String(value);
}

function amount(value: unknown, currency: unknown): string | null {
  const raw = str(value);
  if (raw === null) return null;
  const n = Number(raw);
  if (Number.isNaN(n)) return raw;
  if (currency === "USD") {
    return `USD ${new Intl.NumberFormat("es-CL", { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(n)}`;
  }
  if (currency === "CLP") {
    return `$ ${new Intl.NumberFormat("es-CL", { maximumFractionDigits: 0 }).format(n)}`;
  }
  return formatUF(n);
}

function date(value: unknown): string | null {
  const raw = str(value);
  return raw ? formatDate(raw) : null;
}

/** A hand-typed policy without its number stores `SIN-N-{id}`; never print it. */
const PLACEHOLDER_NUMBER = /^SIN-N-/;

/** The interpolation vars every body may use — always strings, never undefined. */
function vars(n: Notification, dash: string, noNumber: string): Record<string, string> {
  const p = n.params ?? {};
  const policyNumber = str(p.policy_number);
  const number = str(p.number) ?? str(p.installment_number);
  const count = str(p.installment_count);
  return {
    policy: policyNumber
      ? PLACEHOLDER_NUMBER.test(policyNumber)
        ? noNumber
        : policyNumber
      : dash,
    cuota: number ? (count ? `${number}/${count}` : number) : dash,
    due: date(p.due_date) ?? dash,
    amount: amount(p.amount_uf ?? p.amount, p.currency) ?? dash,
    days: str(p.days_late) ?? "0",
    promise: date(p.promise_until) ?? dash,
    rejected: date(p.rejected_on) ?? dash,
    paid: date(p.paid_on) ?? dash,
    insured: str(p.insured_name) ?? dash,
    insurer: str(p.insurer_name) ?? str(p.insurer) ?? dash,
    portal: str(p.portal_name) ?? str(p.portal) ?? dash,
    quotes: str(p.quote_count) ?? "0",
    address: str(p.address) ?? dash,
    evidence: str(p.evidence_count) ?? "0",
    currency: str(p.currency) ?? dash,
    by: str(p.uploaded_by) ?? dash,
  };
}

export function describeNotification(
  n: Notification,
  t: TFunction,
): { title: string; body: string } {
  const keys: NotificationCopyKeys =
    own<NotificationCopyKeys>(NOTIFICATION_COPY, n.kind) ?? FALLBACK;
  const v = vars(n, "—", t("notifications:item.noNumber"));
  let bodyKey: string = keys.body;
  let titleKey: string = keys.title;
  if (n.kind === "direct_debit_rejected") {
    // Name the charge the cuota actually rides on: a PAT is never "PAC rechazado".
    const kind = str(n.params?.direct_debit_kind) ?? str(n.params?.method) ?? str(n.params?.payment_mode);
    titleKey =
      kind === "pac" || kind === "direct_debit"
        ? keys.title
        : kind === "pat" || kind === "card_debit"
          ? "notifications:kind.direct_debit_rejected.titlePat"
          : "notifications:kind.direct_debit_rejected.titleGeneric";
  }
  if (n.kind === "sensitive_action") {
    const event = str(n.params?.event);
    const eventKey = event ? own<string>(SENSITIVE_EVENT_BODY, event) : undefined;
    if (eventKey) bodyKey = eventKey;
  }
  return { title: t(titleKey, v), body: t(bodyKey, v) };
}

/** "hace 5 min" — relative to now, in the active UI language. */
export function relativeTime(iso: string, language: string): string {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "";
  const seconds = Math.round((then - Date.now()) / 1000);
  const rtf = new Intl.RelativeTimeFormat(language.startsWith("en") ? "en" : "es", {
    numeric: "auto",
  });
  const abs = Math.abs(seconds);
  if (abs < 60) return rtf.format(seconds, "second");
  if (abs < 3600) return rtf.format(Math.round(seconds / 60), "minute");
  if (abs < 86400) return rtf.format(Math.round(seconds / 3600), "hour");
  if (abs < 86400 * 30) return rtf.format(Math.round(seconds / 86400), "day");
  return formatDate(iso);
}
