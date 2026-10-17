/**
 * Explicit enum → i18n-key maps for the v12 policy surfaces (lane F1).
 *
 * Never `t(\`policies:status.${value}\`)`: a new server value would compile and
 * then print a raw key. Each map is typed against the enum so a missing member
 * is a type error, and `labelOf` falls back to a neutral "Otro" key for values
 * the server may add later (e.g. a new missing key) instead of the raw value.
 */
import type { TFunction } from "i18next";
import type {
  PartyRole,
  PaymentMethod,
  PolicyCollectionFilter,
  PolicyComputedStatus,
  PolicyFeature,
  PolicyMissingKey,
  PolicyOrigin,
} from "@/api/policies";
import type { BadgeProps } from "@/components/ui/badge";

export type BadgeVariant = NonNullable<BadgeProps["variant"]>;

export const COMPUTED_STATUS_LABEL = {
  upcoming: "policies:status.upcoming",
  active: "policies:status.active",
  expired: "policies:status.expired",
  cancelled: "policies:status.cancelled",
} as const satisfies Record<PolicyComputedStatus, string>;

export const COMPUTED_STATUS_TONE: Record<PolicyComputedStatus, BadgeVariant> = {
  upcoming: "brand",
  active: "success",
  expired: "muted",
  cancelled: "danger",
};

/** The legacy stored statuses, shown only when `computed_status` is absent. */
export const STORED_STATUS_LABEL = {
  draft: "policies:storedStatus.draft",
  active: "policies:status.active",
  expired: "policies:status.expired",
  cancelled: "policies:status.cancelled",
  renewed: "policies:storedStatus.renewed",
} as const;

export const COLLECTION_FILTER_LABEL = {
  overdue: "policies:collection.overdue",
  due_soon: "policies:collection.due_soon",
  no_plan: "policies:collection.no_plan",
  ok: "policies:collection.ok",
} as const satisfies Record<PolicyCollectionFilter, string>;

export const MISSING_LABEL = {
  source_document: "policies:missing.source_document",
  policy_number: "policies:missing.policy_number",
  plan: "policies:missing.plan",
  gross_premium: "policies:missing.gross_premium",
  payer_contact: "policies:missing.payer_contact",
  premiums: "policies:missing.premiums",
  commission: "policies:missing.commission",
} as const satisfies Record<PolicyMissingKey, string>;

export const FEATURE_LABEL = {
  collections: "policies:feature.collections",
  payer_notices: "policies:feature.payer_notices",
  endorsements: "policies:feature.endorsements",
  claims: "policies:feature.claims",
  premium_reports: "policies:feature.premium_reports",
  commission: "policies:feature.commission",
  source_document: "policies:feature.source_document",
} as const satisfies Record<PolicyFeature, string>;

export const PARTY_ROLE_LABEL = {
  insured: "policies:party.role.insured",
  policyholder: "policies:party.role.policyholder",
  payer: "policies:party.role.payer",
  beneficiary: "policies:party.role.beneficiary",
  additional_insured: "policies:party.role.additional_insured",
} as const satisfies Record<PartyRole, string>;

export const PAYMENT_METHOD_LABEL = {
  coupon_book: "policies:paymentMethod.coupon_book",
  direct_debit: "policies:paymentMethod.direct_debit",
  card_debit: "policies:paymentMethod.card_debit",
  transfer: "policies:paymentMethod.transfer",
  single_charge: "policies:paymentMethod.single_charge",
  other: "policies:paymentMethod.other",
} as const satisfies Record<PaymentMethod, string>;

export const ORIGIN_LABEL = {
  reader: "policies:origin.reader",
  manual: "policies:origin.manual",
  journey: "policies:origin.journey",
  portal_rpa: "policies:origin.portal_rpa",
  migration: "policies:origin.migration",
} as const satisfies Record<PolicyOrigin, string>;

/** The account/renewal chain (the stages a policy's colocación walks). */
export const ACCOUNT_STAGES = [
  "lead",
  "intake",
  "pre_underwriting",
  "technical_basis",
  "market_submission",
  "quotes_received",
  "comparison",
  "insured_decision",
  "proposal_issued",
  "ratified",
  "policy_issued",
  "mirror_validation",
  "active",
  "renewal_review",
] as const;
export type AccountStage = (typeof ACCOUNT_STAGES)[number];

export const STAGE_LABEL = {
  lead: "policies:stage.lead",
  intake: "policies:stage.intake",
  pre_underwriting: "policies:stage.pre_underwriting",
  technical_basis: "policies:stage.technical_basis",
  market_submission: "policies:stage.market_submission",
  quotes_received: "policies:stage.quotes_received",
  comparison: "policies:stage.comparison",
  insured_decision: "policies:stage.insured_decision",
  proposal_issued: "policies:stage.proposal_issued",
  ratified: "policies:stage.ratified",
  policy_issued: "policies:stage.policy_issued",
  mirror_validation: "policies:stage.mirror_validation",
  active: "policies:stage.active",
  renewal_review: "policies:stage.renewal_review",
} as const satisfies Record<AccountStage, string>;

/** Look a value up in one of the maps above; unknown values read "Otro". */
export function labelOf<M extends Record<string, string>>(
  map: M,
  value: string | null | undefined,
  t: TFunction,
): string {
  if (!value) return "—";
  // Own keys only: `map["constructor"]` would otherwise be a function.
  const key = Object.prototype.hasOwnProperty.call(map, value)
    ? (map as Record<string, string>)[value]
    : undefined;
  return key ? t(key) : t("policies:other");
}
