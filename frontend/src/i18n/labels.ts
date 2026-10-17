/**
 * Explicit enum → i18n-key maps (CLAUDE.md rule 4: no dynamic keys).
 *
 * A key built at runtime (a template literal or a concatenation passed to `t`)
 * compiles and builds, then prints the raw key on screen the day the server
 * sends a value the bundle does not label. Every server enum the UI renders is
 * mapped here to a LITERAL key instead, typed `satisfies Record<Enum, string>`
 * so a new backend value fails `tsc` until it gets a label. Values the server may still send outside
 * the union fall back through `enumLabel` to a caller-chosen text (by default
 * the raw token, which is what the old `defaultValue: value` rendered) — never
 * to a constructed key.
 */
import type {
  AccountClientRole,
  AccountErrorCode,
  AccountGroupStatus,
  AssetStatus,
  BrokerProposalStatus,
  CaseFileKind,
  CaseFileStatus,
  CaseOrigin,
  CaseSection,
  CaseStage,
  ClaimItemKind,
  ClaimRuling,
  ClaimStatus,
  ClientStatus,
  CollectionPlanStatus,
  ComparisonStatus,
  ContactMatchLevel,
  ContactScope,
  CoverageKind,
  DocumentCategory,
  DocumentDirection,
  EndorsementKind,
  EndorsementStatus,
  EntityType,
  ExtractionStatus,
  FacetGroup,
  GroupTimelineKind,
  InspectionRequestStatus,
  InspectionStatus,
  InstallmentStatus,
  InsurerMatchResult,
  InsurerStatus,
  LeadStatus,
  OfferingChannel,
  OfferingStatus,
  PackKind,
  PackPart,
  PackStatus,
  PaymentMode,
  PendingActionCode,
  PendingActionSeverity,
  PersonType,
  PlacementStatus,
  PolicyStatus,
  Priority,
  ProposalOrigin,
  ProposalStatus,
  QuoteRequestStatus,
  RecordExpedienteStatus,
  RecordFolderKey,
  RenewalReason,
  UserType,
  WarrantySource,
  WarrantyStatus,
} from "@/api/types";

type LabelMap = Readonly<Record<string, string>>;

/** Any translate function — a `useTranslation()` `t`, `i18n.t`, or a wrapper. */
type Translate = (key: string) => string;

/** Own-key lookup (never a prototype member such as `constructor`). */
export function keyFor<M extends LabelMap>(
  map: M,
  value: string | null | undefined,
): M[keyof M] | undefined {
  if (value == null || !Object.prototype.hasOwnProperty.call(map, value)) return undefined;
  return map[value as keyof M];
}

/**
 * Translate a server value through one of the maps. An unknown value renders
 * `fallback` (default: the raw value, or "—" when there is none).
 */
export function enumLabel(
  t: Translate,
  map: LabelMap,
  value: string | null | undefined,
  fallback?: string,
): string {
  const key = keyFor(map, value);
  if (key) return t(key);
  return fallback ?? (value || "—");
}

export const CASE_KIND_LABEL = {
  account: "cases:kinds.account",
  endorsement: "cases:kinds.endorsement",
  collection: "cases:kinds.collection",
  claim: "cases:kinds.claim",
  renewal: "cases:kinds.renewal",
} as const satisfies Record<CaseFileKind, string>;

export const CASE_STATUS_LABEL = {
  open: "cases:statuses.open",
  on_hold: "cases:statuses.on_hold",
  won: "cases:statuses.won",
  lost: "cases:statuses.lost",
  cancelled: "cases:statuses.cancelled",
  closed: "cases:statuses.closed",
} as const satisfies Record<CaseFileStatus, string>;

export const CASE_NEXT_LABEL = {
  lead: "cases:journey.next.lead",
  intake: "cases:journey.next.intake",
  pre_underwriting: "cases:journey.next.pre_underwriting",
  technical_basis: "cases:journey.next.technical_basis",
  market_submission: "cases:journey.next.market_submission",
  quotes_received: "cases:journey.next.quotes_received",
  comparison: "cases:journey.next.comparison",
  insured_decision: "cases:journey.next.insured_decision",
  proposal_issued: "cases:journey.next.proposal_issued",
  ratified: "cases:journey.next.ratified",
  policy_issued: "cases:journey.next.policy_issued",
  mirror_validation: "cases:journey.next.mirror_validation",
  active: "cases:journey.next.active",
  renewal_review: "cases:journey.next.renewal_review",
  endorsement_requested: "cases:journey.next.endorsement_requested",
  endorsement_proposed: "cases:journey.next.endorsement_proposed",
  endorsement_issued: "cases:journey.next.endorsement_issued",
  endorsement_applied: "cases:journey.next.endorsement_applied",
  collection_scheduled: "cases:journey.next.collection_scheduled",
  collection_in_progress: "cases:journey.next.collection_in_progress",
  collection_overdue: "cases:journey.next.collection_overdue",
  collection_settled: "cases:journey.next.collection_settled",
  collection_suspended: "cases:journey.next.collection_suspended",
  claim_reported: "cases:journey.next.claim_reported",
  claim_adjusting: "cases:journey.next.claim_adjusting",
  claim_preliminary: "cases:journey.next.claim_preliminary",
  claim_final: "cases:journey.next.claim_final",
  claim_settled: "cases:journey.next.claim_settled",
  closed: "cases:journey.next.closed",
} as const satisfies Record<CaseStage, string>;

export const GROUP_TIMELINE_KIND_LABEL = {
  stage: "accounts:timeline.kinds.stage",
  activity: "accounts:timeline.kinds.activity",
  note: "accounts:timeline.kinds.note",
  policy: "accounts:timeline.kinds.policy",
  endorsement: "accounts:timeline.kinds.endorsement",
  claim: "accounts:timeline.kinds.claim",
  collection: "accounts:timeline.kinds.collection",
} as const satisfies Record<GroupTimelineKind, string>;

export const PENDING_ACTION_CODE_LABEL = {
  antecedentes_missing: "accounts:summary.codes.antecedentes_missing",
  antecedentes_unregistered: "accounts:summary.codes.antecedentes_unregistered",
  proposals_unconfirmed: "accounts:summary.codes.proposals_unconfirmed",
  comparison_unaligned: "accounts:summary.codes.comparison_unaligned",
  propuesta_missing: "accounts:summary.codes.propuesta_missing",
  stage_blocked: "accounts:summary.codes.stage_blocked",
} as const satisfies Record<PendingActionCode, string>;

export const PENDING_ACTION_REASON_LABEL = {
  antecedentes_missing: "accounts:summary.reasons.antecedentes_missing",
  antecedentes_unregistered: "accounts:summary.reasons.antecedentes_unregistered",
  proposals_unconfirmed: "accounts:summary.reasons.proposals_unconfirmed",
  comparison_unaligned: "accounts:summary.reasons.comparison_unaligned",
  propuesta_missing: "accounts:summary.reasons.propuesta_missing",
  stage_blocked: "accounts:summary.reasons.stage_blocked",
} as const satisfies Record<PendingActionCode, string>;

export const CASE_ORIGIN_LABEL = {
  new: "accounts:origin.new",
  renewal: "accounts:origin.renewal",
  period_change: "accounts:origin.period_change",
} as const satisfies Record<CaseOrigin, string>;

export const CASE_ORIGIN_HINT = {
  new: "accounts:originHint.new",
  renewal: "accounts:originHint.renewal",
  period_change: "accounts:originHint.period_change",
} as const satisfies Record<CaseOrigin, string>;

export const ENDORSEMENT_KIND_LABEL = {
  location_inclusion: "postsale:endorsement.kind.location_inclusion",
  location_exclusion: "postsale:endorsement.kind.location_exclusion",
  vehicle_inclusion: "postsale:endorsement.kind.vehicle_inclusion",
  vehicle_exclusion: "postsale:endorsement.kind.vehicle_exclusion",
  additional_insured: "postsale:endorsement.kind.additional_insured",
  activity_extension: "postsale:endorsement.kind.activity_extension",
  aggregate_reinstatement: "postsale:endorsement.kind.aggregate_reinstatement",
  roster_increase: "postsale:endorsement.kind.roster_increase",
  roster_adjustment: "postsale:endorsement.kind.roster_adjustment",
  pledge_update: "postsale:endorsement.kind.pledge_update",
  policyholder_change: "postsale:endorsement.kind.policyholder_change",
  sum_insured_increase: "postsale:endorsement.kind.sum_insured_increase",
  sum_insured_decrease: "postsale:endorsement.kind.sum_insured_decrease",
  deductible_reduction: "postsale:endorsement.kind.deductible_reduction",
  period_extension: "postsale:endorsement.kind.period_extension",
  other: "postsale:endorsement.kind.other",
} as const satisfies Record<EndorsementKind, string>;

export const ENDORSEMENT_STATUS_LABEL = {
  draft: "postsale:endorsement.status.draft",
  proposed: "postsale:endorsement.status.proposed",
  issued: "postsale:endorsement.status.issued",
  applied: "postsale:endorsement.status.applied",
  rejected: "postsale:endorsement.status.rejected",
  cancelled: "postsale:endorsement.status.cancelled",
} as const satisfies Record<EndorsementStatus, string>;

export const COLLECTION_PLAN_STATUS_LABEL = {
  pending: "postsale:collection.status.pending",
  current: "postsale:collection.status.current",
  overdue: "postsale:collection.status.overdue",
  settled: "postsale:collection.status.settled",
  suspended: "postsale:collection.status.suspended",
  terminated: "postsale:collection.status.terminated",
  rehabilitated: "postsale:collection.status.rehabilitated",
} as const satisfies Record<CollectionPlanStatus, string>;

export const PAYMENT_MODE_LABEL = {
  coupon_book: "postsale:collection.paymentMode.coupon_book",
  direct_debit: "postsale:collection.paymentMode.direct_debit",
  card_debit: "postsale:collection.paymentMode.card_debit",
  transfer: "postsale:collection.paymentMode.transfer",
  single_charge: "postsale:collection.paymentMode.single_charge",
  other: "postsale:collection.paymentMode.other",
} as const satisfies Record<PaymentMode, string>;

export const INSTALLMENT_STATUS_LABEL = {
  pending: "postsale:installment.status.pending",
  due: "postsale:installment.status.due",
  paid: "postsale:installment.status.paid",
  paid_late: "postsale:installment.status.paid_late",
  overdue: "postsale:installment.status.overdue",
  credited: "postsale:installment.status.credited",
  cancelled: "postsale:installment.status.cancelled",
} as const satisfies Record<InstallmentStatus, string>;

export const CLAIM_STATUS_LABEL = {
  reported: "postsale:claim.status.reported",
  under_review: "postsale:claim.status.under_review",
  settled: "postsale:claim.status.settled",
  paid: "postsale:claim.status.paid",
  rejected: "postsale:claim.status.rejected",
  closed: "postsale:claim.status.closed",
} as const satisfies Record<ClaimStatus, string>;

export const CLAIM_RULING_LABEL = {
  pending: "postsale:claim.ruling.pending",
  covered: "postsale:claim.ruling.covered",
  partially_covered: "postsale:claim.ruling.partially_covered",
  rejected: "postsale:claim.ruling.rejected",
} as const satisfies Record<ClaimRuling, string>;

export const CLAIM_ITEM_KIND_LABEL = {
  material_damage: "postsale:claimItem.kind.material_damage",
  business_interruption: "postsale:claimItem.kind.business_interruption",
  expense: "postsale:claimItem.kind.expense",
  liability: "postsale:claimItem.kind.liability",
  personal_accident: "postsale:claimItem.kind.personal_accident",
  recovery: "postsale:claimItem.kind.recovery",
} as const satisfies Record<ClaimItemKind, string>;

export const POLICY_STATUS_LABEL = {
  draft: "postsale:policy.status.draft",
  active: "postsale:policy.status.active",
  expired: "postsale:policy.status.expired",
  cancelled: "postsale:policy.status.cancelled",
  renewed: "postsale:policy.status.renewed",
} as const satisfies Record<PolicyStatus, string>;

export const WARRANTY_SOURCE_LABEL = {
  inspection_recommendation: "postsale:warranty.source.inspection_recommendation",
  underwriting_warranty: "postsale:warranty.source.underwriting_warranty",
  engineering_measure: "postsale:warranty.source.engineering_measure",
} as const satisfies Record<WarrantySource, string>;

export const WARRANTY_STATUS_LABEL = {
  pending: "postsale:warranty.status.pending",
  in_progress: "postsale:warranty.status.in_progress",
  met_on_time: "postsale:warranty.status.met_on_time",
  met_late: "postsale:warranty.status.met_late",
  met_after_claim: "postsale:warranty.status.met_after_claim",
  breached: "postsale:warranty.status.breached",
  waived: "postsale:warranty.status.waived",
} as const satisfies Record<WarrantyStatus, string>;

export const PLACEMENT_STATUS_LABEL = {
  draft: "placements:status.draft",
  inspection: "placements:status.inspection",
  pre_underwriting: "placements:status.pre_underwriting",
  quoting: "placements:status.quoting",
  negotiating: "placements:status.negotiating",
  awarded: "placements:status.awarded",
  active: "placements:status.active",
  closed: "placements:status.closed",
} as const satisfies Record<PlacementStatus, string>;

export const PLACEMENT_QUOTE_STATUS_LABEL = {
  draft: "placements:quoteStatus.draft",
  sent: "placements:quoteStatus.sent",
  receiving: "placements:quoteStatus.receiving",
  closed: "placements:quoteStatus.closed",
  cancelled: "placements:quoteStatus.cancelled",
} as const satisfies Record<QuoteRequestStatus, string>;

export const PLACEMENT_PROPOSAL_ORIGIN_LABEL = {
  native: "placements:proposals.origin.native",
  external: "placements:proposals.origin.external",
} as const satisfies Record<ProposalOrigin, string>;

export const PLACEMENT_PROPOSAL_STATUS_LABEL = {
  draft: "placements:proposalStatus.draft",
  submitted: "placements:proposalStatus.submitted",
  accepted: "placements:proposalStatus.accepted",
  rejected: "placements:proposalStatus.rejected",
  withdrawn: "placements:proposalStatus.withdrawn",
  expired: "placements:proposalStatus.expired",
} as const satisfies Record<ProposalStatus, string>;

export const PLACEMENT_INSPECTION_STATUS_LABEL = {
  pending: "placements:inspectionStatus.pending",
  scheduled: "placements:inspectionStatus.scheduled",
  in_progress: "placements:inspectionStatus.in_progress",
  completed: "placements:inspectionStatus.completed",
  cancelled: "placements:inspectionStatus.cancelled",
} as const satisfies Record<InspectionRequestStatus, string>;

export const CLIENT_STATUS_LABEL = {
  prospect: "clients:status.prospect",
  onboarding: "clients:status.onboarding",
  active: "clients:status.active",
  archived: "clients:status.archived",
} as const satisfies Record<ClientStatus, string>;

export const ASSET_STATUS_LABEL = {
  active: "clients:assets.status.active",
  inactive: "clients:assets.status.inactive",
  archived: "clients:assets.status.archived",
} as const satisfies Record<AssetStatus, string>;

export const PERSON_TYPE_LABEL = {
  natural: "clients:personType.natural",
  legal: "clients:personType.legal",
} as const satisfies Record<PersonType, string>;

export const DOCUMENT_CATEGORY_LABEL = {
  cmf_certificate: "documents:categories.cmf_certificate",
  appointment: "documents:categories.appointment",
  logo: "documents:categories.logo",
  asset_sheet: "documents:categories.asset_sheet",
  asset_photo: "documents:categories.asset_photo",
  valuation: "documents:categories.valuation",
  insured_amounts: "documents:categories.insured_amounts",
  evidence: "documents:categories.evidence",
  inspection_report: "documents:categories.inspection_report",
  technical_brief: "documents:categories.technical_brief",
  claims_history: "documents:categories.claims_history",
  proposal: "documents:categories.proposal",
  power_of_attorney: "documents:categories.power_of_attorney",
  offering: "documents:categories.offering",
  other: "documents:categories.other",
  prospect_request: "documents:categories.prospect_request",
  business_questionnaire: "documents:categories.business_questionnaire",
  insured_values_schedule: "documents:categories.insured_values_schedule",
  loss_history: "documents:categories.loss_history",
  submission_letter: "documents:categories.submission_letter",
  risk_engineering_plan: "documents:categories.risk_engineering_plan",
  resubmission_letter: "documents:categories.resubmission_letter",
  insurer_quotation: "documents:categories.insurer_quotation",
  declination: "documents:categories.declination",
  conditional_pronouncement: "documents:categories.conditional_pronouncement",
  quote_comparison: "documents:categories.quote_comparison",
  issuance_proposal: "documents:categories.issuance_proposal",
  technical_recommendation: "documents:categories.technical_recommendation",
  policy: "documents:categories.policy",
  payment_plan: "documents:categories.payment_plan",
  collection_status: "documents:categories.collection_status",
  endorsement_proposal: "documents:categories.endorsement_proposal",
  endorsement: "documents:categories.endorsement",
  compliance_notice: "documents:categories.compliance_notice",
  claim_notice: "documents:categories.claim_notice",
  claim_preliminary_report: "documents:categories.claim_preliminary_report",
  claim_final_report: "documents:categories.claim_final_report",
  broker_closing_note: "documents:categories.broker_closing_note",
  submission_pack: "documents:categories.submission_pack",
  comparison_pack: "documents:categories.comparison_pack",
  proposal_pack: "documents:categories.proposal_pack",
  archive_pack: "documents:categories.archive_pack",
} as const satisfies Record<DocumentCategory, string>;

export const DOCUMENT_DIRECTION_LABEL = {
  insured_to_broker: "documents:directions.insured_to_broker",
  broker_to_insurers: "documents:directions.broker_to_insurers",
  broker_to_insurer: "documents:directions.broker_to_insurer",
  broker_to_insured: "documents:directions.broker_to_insured",
  broker_and_insured_to_insurers: "documents:directions.broker_and_insured_to_insurers",
  insurer_to_broker: "documents:directions.insurer_to_broker",
  third_party_to_broker: "documents:directions.third_party_to_broker",
  adjuster_to_parties: "documents:directions.adjuster_to_parties",
  internal: "documents:directions.internal",
} as const satisfies Record<DocumentDirection, string>;

export const ENTITY_TYPE_LABEL = {
  broker: "settings:entities.broker",
  user: "settings:entities.user",
  client: "settings:entities.client",
  insured: "settings:entities.insured",
  insurer: "settings:entities.insurer",
  asset: "settings:entities.asset",
  insurance_line: "settings:entities.insurance_line",
  placement: "settings:entities.placement",
  quote_request: "settings:entities.quote_request",
  proposal: "settings:entities.proposal",
  inspection_request: "settings:entities.inspection_request",
  inspection: "settings:entities.inspection",
  policy: "settings:entities.policy",
  claim: "settings:entities.claim",
  offering: "settings:entities.offering",
  case_file: "settings:entities.case_file",
  sales_lead: "settings:entities.sales_lead",
  endorsement: "settings:entities.endorsement",
  collection_plan: "settings:entities.collection_plan",
  warranty: "settings:entities.warranty",
  account_group: "settings:entities.account_group",
} as const satisfies Record<EntityType, string>;

export const OFFERING_STATUS_LABEL = {
  draft: "offerings:status.draft",
  sent: "offerings:status.sent",
  viewed: "offerings:status.viewed",
  accepted: "offerings:status.accepted",
  expired: "offerings:status.expired",
} as const satisfies Record<OfferingStatus, string>;

export const OFFERING_CHANNEL_LABEL = {
  whatsapp: "offerings:channels.whatsapp",
  email: "offerings:channels.email",
  download: "offerings:channels.download",
  link: "offerings:channels.link",
} as const satisfies Record<OfferingChannel, string>;

export const QUOTE_STATUS_LABEL = {
  draft: "quotes:status.draft",
  sent: "quotes:status.sent",
  receiving: "quotes:status.receiving",
  closed: "quotes:status.closed",
  cancelled: "quotes:status.cancelled",
} as const satisfies Record<QuoteRequestStatus, string>;

export const QUOTE_PRIORITY_LABEL = {
  low: "quotes:priority.low",
  normal: "quotes:priority.normal",
  high: "quotes:priority.high",
  urgent: "quotes:priority.urgent",
} as const satisfies Record<Priority, string>;

export const QUOTE_ORIGIN_LABEL = {
  native: "quotes:origin.native",
  external: "quotes:origin.external",
} as const satisfies Record<ProposalOrigin, string>;

export const QUOTE_PROPOSAL_STATUS_LABEL = {
  draft: "quotes:proposalStatus.draft",
  submitted: "quotes:proposalStatus.submitted",
  accepted: "quotes:proposalStatus.accepted",
  rejected: "quotes:proposalStatus.rejected",
  withdrawn: "quotes:proposalStatus.withdrawn",
  expired: "quotes:proposalStatus.expired",
} as const satisfies Record<ProposalStatus, string>;

export const QUOTE_OFFERING_STATUS_LABEL = {
  draft: "quotes:offeringStatus.draft",
  sent: "quotes:offeringStatus.sent",
  viewed: "quotes:offeringStatus.viewed",
  accepted: "quotes:offeringStatus.accepted",
  expired: "quotes:offeringStatus.expired",
} as const satisfies Record<OfferingStatus, string>;

export const PROPOSAL_STATUS_LABEL = {
  draft: "proposals:status.draft",
  submitted: "proposals:status.submitted",
  accepted: "proposals:status.accepted",
  rejected: "proposals:status.rejected",
  withdrawn: "proposals:status.withdrawn",
  expired: "proposals:status.expired",
} as const satisfies Record<ProposalStatus, string>;

export const PROPOSAL_ORIGIN_LABEL = {
  native: "proposals:origin.native",
  external: "proposals:origin.external",
} as const satisfies Record<ProposalOrigin, string>;

export const COVERAGE_KIND_LABEL = {
  coverage: "proposals:coverageKind.coverage",
  exclusion: "proposals:coverageKind.exclusion",
} as const satisfies Record<CoverageKind, string>;

export const INSURER_STATUS_LABEL = {
  active: "insurers:status.active",
  inactive: "insurers:status.inactive",
  deregistered: "insurers:status.deregistered",
} as const satisfies Record<InsurerStatus, string>;

export const LEAD_STATUS_LABEL = {
  new: "leads:statuses.new",
  contacted: "leads:statuses.contacted",
  qualified: "leads:statuses.qualified",
  converted: "leads:statuses.converted",
  lost: "leads:statuses.lost",
} as const satisfies Record<LeadStatus, string>;

export const PACK_STATUS_LABEL = {
  draft: "packs:statuses.draft",
  generating: "packs:statuses.generating",
  generated: "packs:statuses.generated",
  sent: "packs:statuses.sent",
  failed: "packs:statuses.failed",
} as const satisfies Record<PackStatus, string>;

export const GROUP_STATUS_LABEL = {
  active: "accounts:group.status.active",
  archived: "accounts:group.status.archived",
} as const satisfies Record<AccountGroupStatus, string>;

export const ACCOUNT_CLIENT_ROLE_LABEL = {
  policyholder: "accounts:expediente.role.policyholder",
  insured: "accounts:expediente.role.insured",
} as const satisfies Record<AccountClientRole, string>;

export const RECORD_FOLDER_LABEL = {
  amounts: "accounts:folders.amounts",
  loss_history: "accounts:folders.loss_history",
  report: "accounts:folders.report",
  questionnaire: "accounts:folders.questionnaire",
  slip: "accounts:folders.slip",
} as const satisfies Record<RecordFolderKey, string>;

export const RENEWAL_REASON_LABEL = {
  account_not_active: "accounts:renewal.reason.account_not_active",
  already_renewed: "accounts:renewal.reason.already_renewed",
  period_open: "accounts:renewal.reason.period_open",
} as const satisfies Record<RenewalReason, string>;

export const ACCOUNT_ERROR_LABEL = {
  period_locked: "accounts:errors.period_locked",
  folder_exists: "accounts:errors.folder_exists",
  client_not_in_group: "accounts:errors.client_not_in_group",
  client_in_other_group: "accounts:errors.client_in_other_group",
  policies_span_groups: "accounts:errors.policies_span_groups",
  renewal_not_allowed: "accounts:errors.renewal_not_allowed",
} as const satisfies Record<AccountErrorCode, string>;

export const COMPARISON_STATUS_LABEL = {
  draft: "comparison:status.draft",
  aligned: "comparison:status.aligned",
  superseded: "comparison:status.superseded",
} as const satisfies Record<ComparisonStatus, string>;

export const FACET_GROUP_LABEL = {
  coverage: "comparison:facetGroups.coverage",
  exclusion: "comparison:facetGroups.exclusion",
  deductible: "comparison:facetGroups.deductible",
  sublimit: "comparison:facetGroups.sublimit",
  clause: "comparison:facetGroups.clause",
  warranty: "comparison:facetGroups.warranty",
  other: "comparison:facetGroups.other",
} as const satisfies Record<FacetGroup, string>;

export const BROKER_PROPOSAL_STATUS_LABEL = {
  draft: "propuesta:status.draft",
  issued: "propuesta:status.issued",
  ratified: "propuesta:status.ratified",
  superseded: "propuesta:status.superseded",
} as const satisfies Record<BrokerProposalStatus, string>;

export const INSPECTION_REQUEST_STATUS_LABEL = {
  pending: "inspections:status.request.pending",
  scheduled: "inspections:status.request.scheduled",
  in_progress: "inspections:status.request.in_progress",
  completed: "inspections:status.request.completed",
  cancelled: "inspections:status.request.cancelled",
} as const satisfies Record<InspectionRequestStatus, string>;

export const INSPECTION_STATUS_LABEL = {
  draft: "inspections:status.report.draft",
  in_review: "inspections:status.report.in_review",
  issued: "inspections:status.report.issued",
  archived: "inspections:status.report.archived",
} as const satisfies Record<InspectionStatus, string>;

export const INSPECTION_URGENCY_LABEL = {
  low: "inspections:urgency.low",
  normal: "inspections:urgency.normal",
  high: "inspections:urgency.high",
  urgent: "inspections:urgency.urgent",
} as const satisfies Record<Priority, string>;

export const CASE_SECTION_LABEL = {
  root_prospect: "documents:sections.root_prospect",
  submission: "documents:sections.submission",
  insurer_quotes: "documents:sections.insurer_quotes",
  broker_proposal: "documents:sections.broker_proposal",
  policy_file: "documents:sections.policy_file",
  collection: "documents:sections.collection",
  endorsement: "documents:sections.endorsement",
  claim: "documents:sections.claim",
  renewal: "documents:sections.renewal",
} as const satisfies Record<CaseSection, string>;

/** Every `CaseStage` — the canonical map lives beside the Journey. */
export { STAGE_LABEL_KEY as CASE_STAGE_LABEL } from "@/components/groups/stageLabels";

/** `CaseHistoryEntry.kind` (typed `string` server-side; these are the known ones). */
export const CASE_TIMELINE_KIND_LABEL = {
  stage: "cases:timeline.kinds.stage",
  activity: "cases:timeline.kinds.activity",
  note: "cases:timeline.kinds.note",
} as const;

export const PENDING_ACTION_SEVERITY_LABEL = {
  info: "accounts:summary.severity.info",
  warning: "accounts:summary.severity.warning",
  blocker: "accounts:summary.severity.blocker",
} as const satisfies Record<PendingActionSeverity, string>;

/** Group-timeline note titles the server emits (`internal` / `shared`). */
export const GROUP_TIMELINE_NOTE_LABEL = {
  internal: "accounts:timeline.notes.internal",
  shared: "accounts:timeline.notes.shared",
} as const;

export const RECORD_EXPEDIENTE_STATUS_LABEL = {
  draft: "antecedentes:status.draft",
  processing: "antecedentes:status.processing",
  review: "antecedentes:status.review",
  registered: "antecedentes:status.registered",
} as const satisfies Record<RecordExpedienteStatus, string>;

/** Ramo schema section keys with a curated label; others show the schema's own label. */
export const ANTECEDENTES_SECTION_LABEL = {
  identity: "antecedentes:sections.identity",
  cover: "antecedentes:sections.cover",
  property: "antecedentes:sections.property",
  general: "antecedentes:sections.general",
} as const;

export const EXTRACTION_STATUS_LABEL = {
  pending: "proposals:ai.statuses.pending",
  running: "proposals:ai.statuses.running",
  succeeded: "proposals:ai.statuses.succeeded",
  failed: "proposals:ai.statuses.failed",
} as const satisfies Record<ExtractionStatus, string>;

/** Extraction field names with a curated label (`documents:fields`); others are humanised. */
export const DOCUMENT_FIELD_LABEL = {
  insured: "documents:fields.insured",
  insurer: "documents:fields.insurer",
  policy_number: "documents:fields.policy_number",
  quotation_number: "documents:fields.quotation_number",
  issue_date: "documents:fields.issue_date",
  period_start: "documents:fields.period_start",
  period_end: "documents:fields.period_end",
  total_insured_amount_uf: "documents:fields.total_insured_amount_uf",
  net_premium_taxable_uf: "documents:fields.net_premium_taxable_uf",
  net_premium_exempt_uf: "documents:fields.net_premium_exempt_uf",
  net_premium_total_uf: "documents:fields.net_premium_total_uf",
  vat_uf: "documents:fields.vat_uf",
  gross_premium_uf: "documents:fields.gross_premium_uf",
  coverages: "documents:fields.coverages",
  deductibles: "documents:fields.deductibles",
  exclusions: "documents:fields.exclusions",
  warranties: "documents:fields.warranties",
} as const;

/** Deductible bases (`DeductibleTerm.basis`, a free string server-side). */
export const DEDUCTIBLE_BASIS_LABEL = {
  loss: "proposals:deductibleBasis.loss",
  insured_amount: "proposals:deductibleBasis.insured_amount",
  event: "proposals:deductibleBasis.event",
  claim: "proposals:deductibleBasis.claim",
  fixed: "proposals:deductibleBasis.fixed",
  monthly: "proposals:deductibleBasis.monthly",
} as const;

/** Peril codes; an unknown peril is humanised by the caller. */
export const PERIL_LABEL = {
  fire: "proposals:perils.fire",
  earthquake: "proposals:perils.earthquake",
  theft: "proposals:perils.theft",
  water: "proposals:perils.water",
  flood: "proposals:perils.flood",
  tsunami: "proposals:perils.tsunami",
  riot: "proposals:perils.riot",
  glass: "proposals:perils.glass",
  machinery_breakdown: "proposals:perils.machinery_breakdown",
  electronic_equipment: "proposals:perils.electronic_equipment",
  business_interruption: "proposals:perils.business_interruption",
  civil_liability: "proposals:perils.civil_liability",
  all_risk: "proposals:perils.all_risk",
  other: "proposals:perils.other",
} as const;

/** Money-rule 422 field names; an unknown field reads the generic label. */
export const MONEY_RULE_FIELD_LABEL = {
  total_premium_uf: "common:moneyRule.fields.total_premium_uf",
  net_premium_uf: "common:moneyRule.fields.net_premium_uf",
  vat_uf: "common:moneyRule.fields.vat_uf",
  exempt_premium_uf: "common:moneyRule.fields.exempt_premium_uf",
  taxable_premium_uf: "common:moneyRule.fields.taxable_premium_uf",
  installments: "common:moneyRule.fields.installments",
} as const;

/** Policy core-validation field names (`missing[]`, free strings server-side). */
export const POLICY_CORE_FIELD_LABEL = {
  corredor: "postsale:policy.upload.coreField.corredor",
  asegurado: "postsale:policy.upload.coreField.asegurado",
  vigencia: "postsale:policy.upload.coreField.vigencia",
  prima: "postsale:policy.upload.coreField.prima",
} as const;

/** Mirror-diff / collection-alert severities (`severity: string` server-side). */
export const POSTSALE_SEVERITY_LABEL = {
  high: "postsale:shared.severity.high",
  medium: "postsale:shared.severity.medium",
  low: "postsale:shared.severity.low",
} as const;

/** User roles (server-driven strings); an unknown role shows its raw name. */
export const USER_ROLE_LABEL = {
  platform_admin: "settings:roles.platform_admin",
  broker_admin: "settings:roles.broker_admin",
  broker_executive: "settings:roles.broker_executive",
  broker_inspector: "settings:roles.broker_inspector",
  broker_technician: "settings:roles.broker_technician",
  broker_commercial: "settings:roles.broker_commercial",
  broker_collections: "settings:roles.broker_collections",
  broker_claims: "settings:roles.broker_claims",
  insured_admin: "settings:roles.insured_admin",
  insured_user: "settings:roles.insured_user",
  insurer_admin: "settings:roles.insurer_admin",
  insurer_underwriter: "settings:roles.insurer_underwriter",
  insurer_executive: "settings:roles.insurer_executive",
} as const;

export const ORG_TYPE_LABEL = {
  platform: "settings:profile.orgType.platform",
  broker: "settings:profile.orgType.broker",
  insurer: "settings:profile.orgType.insurer",
  insured: "settings:profile.orgType.insured",
} as const satisfies Record<UserType, string>;

export const PACK_KIND_TITLE = {
  submission: "packs:kinds.submission.title",
  comparison: "packs:kinds.comparison.title",
  proposal: "packs:kinds.proposal.title",
} as const satisfies Record<PackKind, string>;

export const PACK_KIND_DESCRIPTION = {
  submission: "packs:kinds.submission.description",
  comparison: "packs:kinds.comparison.description",
  proposal: "packs:kinds.proposal.description",
} as const satisfies Record<PackKind, string>;

export const PACK_PART_LABEL = {
  pdf: "packs:download.pdf",
  zip: "packs:download.zip",
} as const satisfies Record<PackPart, string>;

export const PACK_PART_MISSING = {
  pdf: "packs:download.missing.pdf",
  zip: "packs:download.missing.zip",
} as const satisfies Record<PackPart, string>;

/** Recipient resolution levels (a free string on the pack recipient). */
export const PACK_RECIPIENT_LEVEL_LABEL = {
  broker_line: "packs:recipients.levels.broker_line",
  broker: "packs:recipients.levels.broker",
  line: "packs:recipients.levels.line",
  global: "packs:recipients.levels.global",
} as const;

/** Asset types (a free string server-side); an unknown type shows its raw name. */
export const ASSET_TYPE_LABEL = {
  industrial_plant: "clients:assets.types.industrial_plant",
  distribution_centre: "clients:assets.types.distribution_centre",
  office: "clients:assets.types.office",
  warehouse: "clients:assets.types.warehouse",
  clinic: "clients:assets.types.clinic",
  retail: "clients:assets.types.retail",
  fleet: "clients:assets.types.fleet",
} as const;

/**
 * Document categories as the client panel words them: the panel's own copy
 * where it has one, the canonical `documents:categories` label otherwise.
 */
export const CLIENT_DOCUMENT_CATEGORY_LABEL = {
  cmf_certificate: "clients:documents.categories.cmf_certificate",
  appointment: "clients:documents.categories.appointment",
  logo: "clients:documents.categories.logo",
  asset_sheet: "clients:documents.categories.asset_sheet",
  asset_photo: "clients:documents.categories.asset_photo",
  valuation: "clients:documents.categories.valuation",
  insured_amounts: "clients:documents.categories.insured_amounts",
  evidence: "clients:documents.categories.evidence",
  inspection_report: "clients:documents.categories.inspection_report",
  technical_brief: "clients:documents.categories.technical_brief",
  claims_history: "clients:documents.categories.claims_history",
  proposal: "clients:documents.categories.proposal",
  power_of_attorney: "clients:documents.categories.power_of_attorney",
  offering: "documents:categories.offering",
  other: "clients:documents.categories.other",
  prospect_request: "documents:categories.prospect_request",
  business_questionnaire: "documents:categories.business_questionnaire",
  insured_values_schedule: "documents:categories.insured_values_schedule",
  loss_history: "documents:categories.loss_history",
  submission_letter: "documents:categories.submission_letter",
  risk_engineering_plan: "documents:categories.risk_engineering_plan",
  resubmission_letter: "documents:categories.resubmission_letter",
  insurer_quotation: "documents:categories.insurer_quotation",
  declination: "documents:categories.declination",
  conditional_pronouncement: "documents:categories.conditional_pronouncement",
  quote_comparison: "documents:categories.quote_comparison",
  issuance_proposal: "documents:categories.issuance_proposal",
  technical_recommendation: "documents:categories.technical_recommendation",
  policy: "documents:categories.policy",
  payment_plan: "documents:categories.payment_plan",
  collection_status: "documents:categories.collection_status",
  endorsement_proposal: "documents:categories.endorsement_proposal",
  endorsement: "documents:categories.endorsement",
  compliance_notice: "documents:categories.compliance_notice",
  claim_notice: "documents:categories.claim_notice",
  claim_preliminary_report: "documents:categories.claim_preliminary_report",
  claim_final_report: "documents:categories.claim_final_report",
  broker_closing_note: "documents:categories.broker_closing_note",
  submission_pack: "documents:categories.submission_pack",
  comparison_pack: "documents:categories.comparison_pack",
  proposal_pack: "documents:categories.proposal_pack",
  archive_pack: "documents:categories.archive_pack",
} as const satisfies Record<DocumentCategory, string>;

export const EXPEDIENTE_COMPARISON_STATUS_LABEL = {
  draft: "accounts:expediente.comparisonStatus.draft",
  aligned: "accounts:expediente.comparisonStatus.aligned",
  superseded: "accounts:expediente.comparisonStatus.superseded",
} as const satisfies Record<ComparisonStatus, string>;

/** Where the expediente read its money block from (`money.source`). */
export const EXPEDIENTE_MONEY_SOURCE_LABEL = {
  policy: "accounts:expediente.moneySourceValue.policy",
  broker_proposal: "accounts:expediente.moneySourceValue.broker_proposal",
  proposal: "accounts:expediente.moneySourceValue.proposal",
} as const;

/** Insurer recommendation reasons (a free string server-side). */
export const INSURER_RECOMMENDATION_REASON_LABEL = {
  native_partner: "insurers:recommendations.reasons.native_partner",
  native_partner_with_contact: "insurers:recommendations.reasons.native_partner_with_contact",
  native_partner_with_line_contact: "insurers:recommendations.reasons.native_partner_with_line_contact",
} as const;

export const CONTACT_MATCH_LEVEL_LABEL = {
  broker_line: "insurers:contacts.matchLevel.broker_line",
  broker: "insurers:contacts.matchLevel.broker",
  line: "insurers:contacts.matchLevel.line",
  global: "insurers:contacts.matchLevel.global",
} as const satisfies Record<ContactMatchLevel, string>;

export const CONTACT_SCOPE_LABEL = {
  broker: "insurers:contacts.scopes.broker",
  global: "insurers:contacts.scopes.global",
} as const satisfies Record<ContactScope, string>;

export const SUB_FUNNEL_KIND_LABEL = {
  account: "postsale:subFunnel.kind.account",
  endorsement: "postsale:subFunnel.kind.endorsement",
  collection: "postsale:subFunnel.kind.collection",
  claim: "postsale:subFunnel.kind.claim",
  renewal: "postsale:subFunnel.kind.renewal",
} as const satisfies Record<CaseFileKind, string>;

/** Analytics group-by dimensions (server catalog); an unknown one renders its raw name. */
export const ANALYTICS_DIMENSION_LABEL = {
  label: "analytics:dimensions.label",
  stage: "analytics:dimensions.stage",
  status: "analytics:dimensions.status",
  insurer: "analytics:dimensions.insurer",
  insurance_line: "analytics:dimensions.insurance_line",
  account_group: "analytics:dimensions.account_group",
  month: "analytics:dimensions.month",
  kind: "analytics:dimensions.kind",
  origin: "analytics:dimensions.origin",
  priority: "analytics:dimensions.priority",
  outcome: "analytics:dimensions.outcome",
  payment_mode: "analytics:dimensions.payment_mode",
  coverage_ruling: "analytics:dimensions.coverage_ruling",
  category: "analytics:dimensions.category",
  section: "analytics:dimensions.section",
  entity_type: "analytics:dimensions.entity_type",
  risk_classification: "analytics:dimensions.risk_classification",
  sent_via: "analytics:dimensions.sent_via",
  source: "analytics:dimensions.source",
  sector: "analytics:dimensions.sector",
} as const;

export const INSURER_MATCHED_ON_LABEL = {
  rut: "proposals:upload.matchedOn.rut",
  cmf_code: "proposals:upload.matchedOn.cmf_code",
} as const satisfies Record<NonNullable<InsurerMatchResult["matched_on"]>, string>;

