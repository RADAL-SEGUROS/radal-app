/**
 * Explicit i18n keys for every `CaseStage` — the v12 rule is "no dynamic keys":
 * a `Record<CaseStage, …>` makes TypeScript fail when an enum member is added
 * without its label, instead of the screen printing a raw `stages.x` key.
 */
import type { CaseStage } from "@/api/types";

export const STAGE_LABEL_KEY: Record<CaseStage, string> = {
  lead: "cases:stages.lead",
  intake: "cases:stages.intake",
  pre_underwriting: "cases:stages.pre_underwriting",
  technical_basis: "cases:stages.technical_basis",
  market_submission: "cases:stages.market_submission",
  quotes_received: "cases:stages.quotes_received",
  comparison: "cases:stages.comparison",
  insured_decision: "cases:stages.insured_decision",
  proposal_issued: "cases:stages.proposal_issued",
  ratified: "cases:stages.ratified",
  policy_issued: "cases:stages.policy_issued",
  mirror_validation: "cases:stages.mirror_validation",
  active: "cases:stages.active",
  renewal_review: "cases:stages.renewal_review",
  endorsement_requested: "cases:stages.endorsement_requested",
  endorsement_proposed: "cases:stages.endorsement_proposed",
  endorsement_issued: "cases:stages.endorsement_issued",
  endorsement_applied: "cases:stages.endorsement_applied",
  collection_scheduled: "cases:stages.collection_scheduled",
  collection_in_progress: "cases:stages.collection_in_progress",
  collection_overdue: "cases:stages.collection_overdue",
  collection_settled: "cases:stages.collection_settled",
  collection_suspended: "cases:stages.collection_suspended",
  claim_reported: "cases:stages.claim_reported",
  claim_preliminary: "cases:stages.claim_preliminary",
  claim_adjusting: "cases:stages.claim_adjusting",
  claim_final: "cases:stages.claim_final",
  claim_settled: "cases:stages.claim_settled",
  closed: "cases:stages.closed",
};
