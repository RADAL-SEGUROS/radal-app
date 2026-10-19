/**
 * Cobranza vocabulary — every server enum the lane renders, mapped to an
 * EXPLICIT i18n key (07 §: no dynamic keys). A `t(\`state.${x}\`)` compiles
 * and then prints a raw key the day the server adds a value; these maps make
 * an unknown value fall back to a readable label instead.
 *
 * Spanish copy follows the team spec (Cobranza, clic a clic): Por vencer ·
 * Vence hoy · Vencida · Riesgo de término · En acuerdo · Pagada · Anulada;
 * marks Parcial · Rechazada (PAC) · Sin confirmar · Sin comprobante.
 */
import type { TFunction } from "i18next";
import type { BadgeProps } from "@/components/ui/badge";

type Variant = NonNullable<BadgeProps["variant"]>;

export const STATE_LABEL = {
  upcoming: "collections:state.upcoming",
  due_today: "collections:state.due_today",
  overdue: "collections:state.overdue",
  termination_risk: "collections:state.termination_risk",
  promised: "collections:state.promised",
  paid: "collections:state.paid",
  voided: "collections:state.voided",
  pending_review: "collections:state.pending_review",
} as const;

export const STATE_TONE: Record<keyof typeof STATE_LABEL, Variant> = {
  upcoming: "neutral",
  due_today: "warn",
  overdue: "danger",
  termination_risk: "danger",
  promised: "brand",
  paid: "success",
  voided: "muted",
  pending_review: "warn",
};

export const FLAG_LABEL = {
  partial: "collections:flag.partial",
  direct_debit_rejected: "collections:flag.direct_debit_rejected",
  unconfirmed: "collections:flag.unconfirmed",
  no_receipt: "collections:flag.no_receipt",
} as const;

export const FLAG_TONE: Record<keyof typeof FLAG_LABEL, Variant> = {
  partial: "warn",
  direct_debit_rejected: "danger",
  unconfirmed: "outline",
  no_receipt: "outline",
};

/**
 * The rejected-charge mark names the charge the cuota actually rides on: a
 * PAT (card) cuota must never read "Rechazada (PAC)". `generic` when the
 * server says neither (`direct_debit_kind` absent and no PAC/PAT method).
 */
export const DEBIT_REJECTED_FLAG_LABEL = {
  pac: "collections:flag.direct_debit_rejected",
  pat: "collections:flag.direct_debit_rejected_pat",
  generic: "collections:flag.direct_debit_rejected_generic",
} as const;

export const DEBIT_REJECTED_ACTION_LABEL = {
  pac: "collections:actions.debitRejected",
  pat: "collections:actions.debitRejectedPat",
  generic: "collections:actions.debitRejectedGeneric",
} as const;

export const LEVEL_LABEL = {
  native: "collections:level.native",
  assisted: "collections:level.assisted",
  record: "collections:level.record",
} as const;

export const BUCKET_LABEL = {
  upcoming: "collections:bucket.upcoming",
  overdue: "collections:bucket.overdue",
  current: "collections:bucket.current",
  settled: "collections:bucket.settled",
  review: "collections:bucket.review",
} as const;

export const BUCKET_EMPTY = {
  upcoming: "collections:board.empty.upcoming",
  overdue: "collections:board.empty.overdue",
  current: "collections:board.empty.current",
  settled: "collections:board.empty.settled",
  review: "collections:board.empty.review",
} as const;

export const METHOD_LABEL = {
  coupon_book: "collections:method.coupon_book",
  direct_debit: "collections:method.direct_debit",
  card_debit: "collections:method.card_debit",
  transfer: "collections:method.transfer",
  single_charge: "collections:method.single_charge",
  other: "collections:method.other",
} as const;

export const EVIDENCE_LEVEL_LABEL = {
  declared: "collections:evidenceLevel.declared",
  reported: "collections:evidenceLevel.reported",
  insurer_confirmed: "collections:evidenceLevel.insurer_confirmed",
  provider_confirmed: "collections:evidenceLevel.provider_confirmed",
  settled: "collections:evidenceLevel.settled",
} as const;

export const EVIDENCE_LEVEL_TONE: Record<keyof typeof EVIDENCE_LEVEL_LABEL, Variant> = {
  declared: "neutral",
  reported: "warn",
  insurer_confirmed: "success",
  provider_confirmed: "success",
  settled: "success",
};

export const PAYMENT_SOURCE_LABEL = {
  manual: "collections:paymentSource.manual",
  insurer_statement: "collections:paymentSource.insurer_statement",
  insurer_reply: "collections:paymentSource.insurer_reply",
  insurer_share_link: "collections:paymentSource.insurer_share_link",
  provider: "collections:paymentSource.provider",
  payer_upload: "collections:paymentSource.payer_upload",
  agent: "collections:paymentSource.agent",
} as const;

export const REVERSAL_REASON_LABEL = {
  error: "collections:reversalReason.error",
  direct_debit_rejected: "collections:reversalReason.direct_debit_rejected",
  chargeback: "collections:reversalReason.chargeback",
  other: "collections:reversalReason.other",
} as const;

/** `level_missing` codes from the resolver: what would make assisted work better. */
export const MISSING_LABEL = {
  payer_contact: "collections:missing.payer_contact",
  insurer_contact: "collections:missing.insurer_contact",
  templates_unapproved: "collections:missing.templates_unapproved",
  mail_sender: "collections:missing.mail_sender",
} as const;

export const EVIDENCE_SOURCE_LABEL = {
  insurer_reply: "collections:evidence.source.insurer_reply",
  insurer_share_link: "collections:evidence.source.insurer_share_link",
  insurer_statement: "collections:evidence.source.insurer_statement",
  payer_upload: "collections:evidence.source.payer_upload",
} as const;

export const EVIDENCE_STATUS_LABEL = {
  suggested: "collections:evidence.status.suggested",
  needs_review: "collections:evidence.status.needs_review",
  confirmed: "collections:evidence.status.confirmed",
  rejected: "collections:evidence.status.rejected",
  superseded: "collections:evidence.status.superseded",
} as const;

export const EVIDENCE_STATUS_TONE: Record<keyof typeof EVIDENCE_STATUS_LABEL, Variant> = {
  suggested: "brand",
  needs_review: "warn",
  confirmed: "success",
  rejected: "muted",
  superseded: "muted",
};

export const REPORTED_STATUS_LABEL = {
  paid: "collections:evidence.reported.paid",
  unpaid: "collections:evidence.reported.unpaid",
  partial: "collections:evidence.reported.partial",
  cancelled: "collections:evidence.reported.cancelled",
  unknown: "collections:evidence.reported.unknown",
} as const;

/** Reason codes written by `services/mail/inbound.py::_score` and the matcher. */
export const EVIDENCE_REASON_LABEL = {
  sender_matches_insurer: "collections:evidence.reason.sender_matches_insurer",
  sender_unverified: "collections:evidence.reason.sender_unverified",
  ai_read: "collections:evidence.reason.ai_read",
  heuristic_read: "collections:evidence.reason.heuristic_read",
  installment_number_match: "collections:evidence.reason.installment_number_match",
  amount_matches_installment: "collections:evidence.reason.amount_matches_installment",
  amount_differs: "collections:evidence.reason.amount_differs",
  installment_unknown: "collections:evidence.reason.installment_unknown",
  status_unknown: "collections:evidence.reason.status_unknown",
  token_match: "collections:evidence.reason.token_match",
  in_reply_to_match: "collections:evidence.reason.in_reply_to_match",
  insurer_sender_domain: "collections:evidence.reason.insurer_sender_domain",
  bounce_of_outbound: "collections:evidence.reason.bounce_of_outbound",
  share_link: "collections:evidence.reason.share_link",
} as const;

/** Reasons that argue AGAINST the suggestion — rendered as warnings. */
export const NEGATIVE_REASONS = new Set<string>([
  "sender_unverified",
  "amount_differs",
  "installment_unknown",
  "status_unknown",
]);

export const INQUIRY_STATUS_LABEL = {
  draft: "collections:inquiries.status.draft",
  sent: "collections:inquiries.status.sent",
  replied: "collections:inquiries.status.replied",
  resolved: "collections:inquiries.status.resolved",
  failed: "collections:inquiries.status.failed",
  cancelled: "collections:inquiries.status.cancelled",
} as const;

export const INQUIRY_STATUS_TONE: Record<keyof typeof INQUIRY_STATUS_LABEL, Variant> = {
  draft: "neutral",
  sent: "brand",
  replied: "warn",
  resolved: "success",
  failed: "danger",
  cancelled: "muted",
};

export const MAIL_STATUS_LABEL = {
  queued: "collections:mailStatus.queued",
  sent: "collections:mailStatus.sent",
  failed: "collections:mailStatus.failed",
  skipped: "collections:mailStatus.skipped",
  received: "collections:mailStatus.received",
  processed: "collections:mailStatus.processed",
  ignored: "collections:mailStatus.ignored",
} as const;

export const NOTICE_STATUS_LABEL = {
  queued: "collections:notice.status.queued",
  sent: "collections:notice.status.sent",
  failed: "collections:notice.status.failed",
  bounced: "collections:notice.status.bounced",
  skipped: "collections:notice.status.skipped",
} as const;

export const NOTICE_STATUS_TONE: Record<keyof typeof NOTICE_STATUS_LABEL, Variant> = {
  queued: "neutral",
  sent: "success",
  failed: "danger",
  bounced: "danger",
  skipped: "muted",
};

export const NOTICE_KIND_LABEL = {
  payer_reminder: "collections:notice.kind.payer_reminder",
  payer_overdue: "collections:notice.kind.payer_overdue",
  payment_link: "collections:notice.kind.payment_link",
  insurer_status_inquiry: "collections:notice.kind.insurer_status_inquiry",
} as const;

/**
 * Translate a server enum through one of the maps above. An unknown value
 * renders the caller's fallback (never the raw token, never a raw key).
 */
export function labelOf<M extends Record<string, string>>(
  t: TFunction,
  map: M,
  value: string | null | undefined,
  fallback = "—",
): string {
  if (value && Object.prototype.hasOwnProperty.call(map, value)) {
    return t(map[value as keyof M] as string);
  }
  return fallback;
}

export function toneOf<M extends Record<string, Variant>>(
  map: M,
  value: string | null | undefined,
  fallback: Variant = "neutral",
): Variant {
  if (value && Object.prototype.hasOwnProperty.call(map, value)) return map[value as keyof M];
  return fallback;
}
