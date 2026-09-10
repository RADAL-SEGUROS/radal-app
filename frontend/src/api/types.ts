/**
 * Wire types for the Radal v2 API — a hand-written mirror of the backend
 * Pydantic schemas in `backend/app/schemas/*.py`.
 *
 * Two conventions the whole frontend depends on:
 *
 * 1. **Decimals arrive as STRINGS.** Pydantic v2 serialises `Decimal` to a JSON
 *    string ("1234.5600") to avoid float drift, so every UF amount, per-mille
 *    rate, percentage and score is typed `DecimalString`. Convert at the edge
 *    with {@link num} before formatting or arithmetic — never with `+value`
 *    scattered through components.
 * 2. **Enums are the backend's lowercase English tokens.** They are values, not
 *    copy: the UI renders them through i18n (`t("status." + value)`), never raw.
 */

/** A `Decimal` field as it arrives on the wire (e.g. `"1234.5600"`). */
export type DecimalString = string;

/** ISO-8601 date (`YYYY-MM-DD`). */
export type IsoDate = string;

/** ISO-8601 datetime, UTC. */
export type IsoDateTime = string;

/** Parse a wire Decimal into a JS number. `null`/`undefined`/`""` -> `null`. */
export function num(value: DecimalString | number | null | undefined): number | null {
  if (value === null || value === undefined || value === "") return null;
  const parsed = typeof value === "number" ? value : Number(value);
  return Number.isNaN(parsed) ? null : parsed;
}

/** Same as {@link num} but falls back to 0 — for sums and chart inputs. */
export function num0(value: DecimalString | number | null | undefined): number {
  return num(value) ?? 0;
}

// --- Shared vocabularies -----------------------------------------------------

export const USER_TYPES = ["platform", "broker", "insurer", "insured"] as const;
export type UserType = (typeof USER_TYPES)[number];

export const PRIORITIES = ["low", "normal", "high", "urgent"] as const;
export type Priority = (typeof PRIORITIES)[number];

export const PERSON_TYPES = ["natural", "legal"] as const;
export type PersonType = (typeof PERSON_TYPES)[number];

export const COVERAGE_KINDS = ["coverage", "exclusion"] as const;
export type CoverageKind = (typeof COVERAGE_KINDS)[number];

/** Polymorphic document / activity target. Mirrors the S3 route segment. */
export const ENTITY_TYPES = [
  "broker",
  "user",
  "client",
  "insured",
  "insurer",
  "asset",
  "insurance_line",
  "placement",
  "quote_request",
  "proposal",
  "inspection_request",
  "inspection",
  "policy",
  "claim",
  "offering",
  "case_file",
  "sales_lead",
  "endorsement",
  "collection_plan",
  "warranty",
  // v3 groups & accounts: group archives are `entity_type=account_group` rows.
  "account_group",
] as const;
export type EntityType = (typeof ENTITY_TYPES)[number];

export const CLIENT_STATUSES = ["prospect", "onboarding", "active", "archived"] as const;
export type ClientStatus = (typeof CLIENT_STATUSES)[number];

export const ASSET_STATUSES = ["active", "inactive", "archived"] as const;
export type AssetStatus = (typeof ASSET_STATUSES)[number];

export const PLACEMENT_STATUSES = [
  "draft",
  "inspection",
  "pre_underwriting",
  "quoting",
  "negotiating",
  "awarded",
  "active",
  "closed",
] as const;
export type PlacementStatus = (typeof PLACEMENT_STATUSES)[number];

export const QUOTE_STATUSES = ["draft", "sent", "receiving", "closed", "cancelled"] as const;
export type QuoteRequestStatus = (typeof QUOTE_STATUSES)[number];

export const PROPOSAL_STATUSES = [
  "draft",
  "submitted",
  "accepted",
  "rejected",
  "withdrawn",
  "expired",
] as const;
export type ProposalStatus = (typeof PROPOSAL_STATUSES)[number];

export const PROPOSAL_ORIGINS = ["native", "external"] as const;
export type ProposalOrigin = (typeof PROPOSAL_ORIGINS)[number];

export const INSPECTION_REQUEST_STATUSES = [
  "pending",
  "scheduled",
  "in_progress",
  "completed",
  "cancelled",
] as const;
export type InspectionRequestStatus = (typeof INSPECTION_REQUEST_STATUSES)[number];

export const INSPECTION_STATUSES = ["draft", "in_review", "issued", "archived"] as const;
export type InspectionStatus = (typeof INSPECTION_STATUSES)[number];

export const INSURER_STATUSES = ["active", "inactive", "deregistered"] as const;
export type InsurerStatus = (typeof INSURER_STATUSES)[number];

export const OFFERING_STATUSES = ["draft", "sent", "viewed", "accepted", "expired"] as const;
export type OfferingStatus = (typeof OFFERING_STATUSES)[number];

export const OFFERING_CHANNELS = ["whatsapp", "email", "download", "link"] as const;
export type OfferingChannel = (typeof OFFERING_CHANNELS)[number];

export const DOCUMENT_CATEGORIES = [
  // Original v2 set.
  "cmf_certificate",
  "appointment",
  "logo",
  "asset_sheet",
  "asset_photo",
  "valuation",
  "insured_amounts",
  "evidence",
  "inspection_report",
  "technical_brief",
  "claims_history",
  "proposal",
  "power_of_attorney",
  "offering",
  "other",
  // Case-file registry (spec §3). `proposal` above is the deprecated alias of
  // `insurer_quotation` and is kept so the existing upload flow keeps working.
  "prospect_request",
  "business_questionnaire",
  "insured_values_schedule",
  "loss_history",
  "submission_letter",
  "risk_engineering_plan",
  "resubmission_letter",
  "insurer_quotation",
  "declination",
  "conditional_pronouncement",
  "quote_comparison",
  "issuance_proposal",
  "technical_recommendation",
  "policy",
  "payment_plan",
  "collection_status",
  "endorsement_proposal",
  "endorsement",
  "compliance_notice",
  "claim_notice",
  "claim_preliminary_report",
  "claim_final_report",
  "broker_closing_note",
  "submission_pack",
  "comparison_pack",
  "proposal_pack",
  // v3: the ZIP of a group (or of one vigencia), generated by
  // `POST /account-groups/{id}/archives`.
  "archive_pack",
] as const;
export type DocumentCategory = (typeof DOCUMENT_CATEGORIES)[number];

/** Who sent the document to whom — the registry's `direction`. */
export const DOCUMENT_DIRECTIONS = [
  "insured_to_broker",
  "broker_to_insurers",
  "broker_to_insurer",
  "broker_to_insured",
  "broker_and_insured_to_insurers",
  "insurer_to_broker",
  "third_party_to_broker",
  "adjuster_to_parties",
  "internal",
] as const;
export type DocumentDirection = (typeof DOCUMENT_DIRECTIONS)[number];

// --- Pagination --------------------------------------------------------------

/** Page-based envelope (`clients`, `assets`, `placements`). */
export interface Page<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
  pages: number;
}

/** Offset-based envelope (`quotes`, `proposals`, `insurers`, `users`). */
export interface OffsetPage<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

/** Bare list envelope (`documents`, `inspections`, `offerings`). */
export interface ListResponse<T> {
  total: number;
  items: T[];
}

// --- Identity ----------------------------------------------------------------

export interface UserSummary {
  id: number;
  email: string;
  full_name: string;
  role: string;
  user_type: UserType;
  is_active: boolean;
  avatar_key: string | null;
}

export interface User extends UserSummary {
  job_title: string | null;
  phone: string | null;
  broker_id: number | null;
  insurer_id: number | null;
  insured_id: number | null;
  last_login_at: IsoDateTime | null;
  created_at: IsoDateTime | null;
  updated_at: IsoDateTime | null;
}

/** The user's home organization — broker, insurer or insured. */
export interface Organization {
  type: UserType;
  id: number;
  legal_name: string;
  trade_name: string | null;
  logo_key: string | null;
  status: string | null;
}

export interface TokenPair {
  access_token: string;
  refresh_token: string;
  token_type: "bearer";
  expires_in: number;
}

export interface LoginResponse extends TokenPair {
  user: User;
  organization: Organization | null;
}

export interface MeResponse {
  user: User;
  organization: Organization | null;
}

// --- Clients -----------------------------------------------------------------

/** The canonical, cross-broker insured behind every client row. */
export interface InsuredSummary {
  id: number;
  rut: string;
  person_type: PersonType;
  legal_name: string;
  trade_name: string | null;
  tax_activity: string | null;
  contact_name: string | null;
  email: string | null;
  phone: string | null;
  address: string | null;
  commune: string | null;
  region: string | null;
  logo_key: string | null;
}

export interface AccountManagerSummary {
  id: number;
  full_name: string;
  email: string;
  job_title: string | null;
  avatar_key: string | null;
}

export interface ClientListItem {
  id: number;
  broker_id: number;
  insured_id: number;
  status: ClientStatus;
  source: string | null;
  sector: string | null;
  since: IsoDate | null;
  contact_name: string | null;
  contact_email: string | null;
  contact_phone: string | null;
  created_at: IsoDateTime | null;
  updated_at: IsoDateTime | null;
  insured: InsuredSummary;
  account_manager: AccountManagerSummary | null;
  assets_count: number;
  placements_count: number;
  active_placements_count: number;
}

export interface Client extends ClientListItem {
  internal_notes: string | null;
  policies_count: number;
}

export interface ClientSummary {
  total: number;
  by_status: Record<string, number>;
  with_active_placements: number;
  unassigned: number;
}

/** A client is created from a RUT — the insured is found or created server-side. */
export interface ClientCreate {
  rut: string;
  legal_name?: string | null;
  person_type?: PersonType;
  trade_name?: string | null;
  tax_activity?: string | null;
  insured_contact_name?: string | null;
  insured_email?: string | null;
  insured_phone?: string | null;
  insured_address?: string | null;
  insured_commune?: string | null;
  insured_region?: string | null;
  status?: ClientStatus;
  account_manager_id?: number | null;
  source?: string | null;
  sector?: string | null;
  since?: IsoDate | null;
  contact_name?: string | null;
  contact_email?: string | null;
  contact_phone?: string | null;
  internal_notes?: string | null;
}

/** Only the broker-private CRM side is patchable; the insured is shared. */
export type ClientUpdate = Partial<
  Pick<
    ClientCreate,
    | "status"
    | "account_manager_id"
    | "source"
    | "sector"
    | "since"
    | "contact_name"
    | "contact_email"
    | "contact_phone"
    | "internal_notes"
  >
>;

// --- Assets ------------------------------------------------------------------

export interface AssetClientSummary {
  id: number;
  legal_name: string | null;
  rut: string | null;
}

export interface AssetListItem {
  id: number;
  broker_id: number;
  client_id: number;
  asset_type: string;
  name: string;
  address: string | null;
  commune: string | null;
  region: string | null;
  status: AssetStatus;
  built_area_m2: DecimalString | null;
  land_area_m2: DecimalString | null;
  construction_year: number | null;
  activity: string | null;
  created_at: IsoDateTime | null;
  updated_at: IsoDateTime | null;
  client: AssetClientSummary | null;
  placements_count: number;
  active_placements_count: number;
}

/** The ten promoted underwriting attributes + the type-specific JSON tail. */
export interface Asset extends AssetListItem {
  structure: string | null;
  floors: number | null;
  fire_protection: Record<string, unknown> | null;
  power_supply: Record<string, unknown> | null;
  fire_station_distance_km: DecimalString | null;
  seismic_zone: string | null;
  attributes: Record<string, unknown> | null;
  inspections_count: number;
}

export interface AssetSummary {
  total: number;
  by_status: Record<string, number>;
  by_type: Record<string, number>;
  without_placements: number;
}

export interface AssetCreate {
  client_id?: number | null;
  asset_type: string;
  name: string;
  address?: string | null;
  commune?: string | null;
  region?: string | null;
  status?: AssetStatus;
  attributes?: Record<string, unknown> | null;
  built_area_m2?: number | string | null;
  land_area_m2?: number | string | null;
  construction_year?: number | null;
  structure?: string | null;
  floors?: number | null;
  activity?: string | null;
  fire_protection?: Record<string, unknown> | null;
  power_supply?: Record<string, unknown> | null;
  fire_station_distance_km?: number | string | null;
  seismic_zone?: string | null;
}

export type AssetUpdate = Partial<Omit<AssetCreate, "client_id">>;

// --- Placements --------------------------------------------------------------

export interface PlacementRefSummary {
  id: number;
  name: string | null;
}

export interface PlacementClientSummary {
  id: number;
  legal_name: string | null;
  rut: string | null;
}

export interface PlacementListItem {
  id: number;
  broker_id: number;
  client_id: number;
  asset_id: number;
  insurance_line_id: number;
  period: string | null;
  period_start: IsoDate | null;
  period_end: IsoDate | null;
  status: PlacementStatus;
  created_at: IsoDateTime | null;
  updated_at: IsoDateTime | null;
  client: PlacementClientSummary | null;
  asset: PlacementRefSummary | null;
  insurance_line: PlacementRefSummary | null;
  quote_requests_count: number;
  proposals_count: number;
  inspection_requests_count: number;
  days_to_period_end: number | null;
}

export interface Placement extends PlacementListItem {
  notes: string | null;
  brief_document_id: number | null;
  policies_count: number;
  /** Exactly which status buttons the UI may enable right now. */
  allowed_transitions: PlacementStatus[];
}

export interface PlacementTransitionOptions {
  id: number;
  status: PlacementStatus;
  allowed: PlacementStatus[];
  is_terminal: boolean;
}

export interface PlacementSummary {
  total: number;
  by_status: Record<string, number>;
  by_insurance_line: Record<string, number>;
  open: number;
  in_market: number;
  awaiting_inspection: number;
  expiring_within_60_days: number;
}

export interface PlacementCreate {
  asset_id: number;
  insurance_line_id: number;
  period?: string | null;
  period_start?: IsoDate | null;
  period_end?: IsoDate | null;
  status?: PlacementStatus;
  notes?: string | null;
  brief_document_id?: number | null;
}

/** Status is deliberately absent — it moves only through `POST /transition`. */
export interface PlacementUpdate {
  insurance_line_id?: number | null;
  period?: string | null;
  period_start?: IsoDate | null;
  period_end?: IsoDate | null;
  notes?: string | null;
  brief_document_id?: number | null;
}

// --- Quote requests ----------------------------------------------------------

export interface QuoteLineItem {
  id: number;
  quote_request_id: number;
  name: string;
  value_uf: DecimalString;
  detail: string | null;
  sort_order: number;
}

export interface QuoteLineItemCreate {
  name: string;
  value_uf: number | string;
  detail?: string | null;
  sort_order?: number;
}

export interface QuotePlacementRef {
  id: number;
  client_id: number;
  asset_id: number;
  insurance_line_id: number;
  period: string | null;
  status: PlacementStatus;
}

export interface QuoteRequest {
  id: number;
  broker_id: number;
  placement_id: number;
  insured_object: string | null;
  declared_value_uf: DecimalString | null;
  currency: string;
  requested_coverages: string | null;
  desired_start: IsoDate | null;
  desired_end: IsoDate | null;
  sent_at: IsoDateTime | null;
  due_at: IsoDateTime | null;
  priority: Priority;
  status: QuoteRequestStatus;
  recipient_insurer_ids: number[] | null;
  created_by_id: number | null;
  created_at: IsoDateTime | null;
  updated_at: IsoDateTime | null;
  line_items: QuoteLineItem[];
  /** Derived server-side: the sum `declared_value_uf` must match. */
  line_items_total_uf: DecimalString;
  proposal_count: number;
  placement: QuotePlacementRef | null;
}

export interface QuoteRequestCreate {
  placement_id: number;
  insured_object?: string | null;
  declared_value_uf?: number | string | null;
  currency?: string;
  requested_coverages?: string | null;
  desired_start?: IsoDate | null;
  desired_end?: IsoDate | null;
  due_at?: IsoDateTime | null;
  priority?: Priority;
  status?: QuoteRequestStatus;
  recipient_insurer_ids?: number[] | null;
  line_items?: QuoteLineItemCreate[];
}

export type QuoteRequestUpdate = Partial<Omit<QuoteRequestCreate, "placement_id">>;

export interface QuoteRequestSend {
  recipient_insurer_ids: number[];
  due_at?: IsoDateTime | null;
}

// --- Proposals ---------------------------------------------------------------

/** One peril's deductible. `basis` travels with the number — it differs per peril. */
export interface DeductibleTerm {
  basis?: string | null;
  pct?: DecimalString | number | null;
  min_uf?: DecimalString | number | null;
  max_uf?: DecimalString | number | null;
  amount_uf?: DecimalString | number | null;
  days?: number | null;
  notes?: string | null;
  [key: string]: unknown;
}

/** Deductibles are keyed by peril: `{ fire: {...}, earthquake: {...} }`. */
export type Deductibles = Record<string, DeductibleTerm>;

export interface ProposalCoverage {
  id: number;
  proposal_id: number;
  kind: CoverageKind;
  text: string;
  normalized_code: string | null;
  sort_order: number;
}

export interface ProposalCoverageCreate {
  kind: CoverageKind;
  text: string;
  normalized_code?: string | null;
  sort_order?: number;
}

/** Insurer identity as the comparator needs it: rut + cmf_code, never a name. */
export interface InsurerRef {
  id: number;
  rut: string;
  cmf_code: string;
  legal_name: string;
  trade_name: string | null;
  is_native: boolean;
}

/**
 * Chilean premium arithmetic (see `docs/v2-architecture.md` §4.2):
 * `net = taxable + exempt`, `vat = 0.19 * taxable` (NOT on net — earthquake
 * cover is VAT-exempt), `total = net + vat`,
 * `comprehensive_rate = taxable_rate + exempt_rate`.
 * The server derives what it can and 422s on a broken invariant.
 */
export interface ProposalMoney {
  taxable_premium_uf: DecimalString | null;
  exempt_premium_uf: DecimalString | null;
  net_premium_uf: DecimalString | null;
  vat_uf: DecimalString | null;
  total_premium_uf: DecimalString | null;
  taxable_rate_permille: DecimalString | null;
  exempt_rate_permille: DecimalString | null;
  comprehensive_rate_permille: DecimalString | null;
  commission_pct: DecimalString | null;
}

export interface Proposal extends ProposalMoney {
  id: number;
  broker_id: number;
  quote_request_id: number;
  insurer_id: number;
  origin: ProposalOrigin;
  /** MANDATORY: a proposal cannot exist without the file it was read from. */
  source_document_id: number;
  modality: string | null;
  activity_classification: string | null;
  validity_business_days: number | null;
  coverage_start: IsoDate | null;
  coverage_end: IsoDate | null;
  received_at: IsoDate | null;
  deductibles: Deductibles | null;
  warranties: string | null;
  notes: string | null;
  status: ProposalStatus;
  extraction_id: number | null;
  extraction_confidence: DecimalString | null;
  is_confirmed: boolean;
  confirmed_by_id: number | null;
  confirmed_at: IsoDateTime | null;
  created_at: IsoDateTime | null;
  updated_at: IsoDateTime | null;
  coverages: ProposalCoverage[];
  insurer: InsurerRef | null;
}

export interface ProposalCreate extends Partial<Record<keyof ProposalMoney, number | string | null>> {
  quote_request_id: number;
  insurer_id: number;
  source_document_id: number;
  origin?: ProposalOrigin;
  modality?: string | null;
  activity_classification?: string | null;
  validity_business_days?: number | null;
  coverage_start?: IsoDate | null;
  coverage_end?: IsoDate | null;
  received_at?: IsoDate | null;
  deductibles?: Deductibles | null;
  warranties?: string | null;
  notes?: string | null;
  status?: ProposalStatus;
  extraction_id?: number | null;
  extraction_confidence?: number | string | null;
  coverages?: ProposalCoverageCreate[];
}

export type ProposalUpdate = Partial<Omit<ProposalCreate, "quote_request_id">>;

/** Outcome of accept/reject — the whole cascade in one payload. */
export interface ProposalDecisionResult {
  proposal_id: number;
  proposal_status: ProposalStatus;
  rejected_proposal_ids: number[];
  quote_request_id: number;
  quote_request_status: QuoteRequestStatus;
  placement_id: number;
  placement_status: string;
}

// --- Proposal comparison -----------------------------------------------------

export interface ComparisonQuote {
  id: number;
  placement_id: number;
  insured_object: string | null;
  declared_value_uf: DecimalString | null;
  currency: string;
  status: QuoteRequestStatus;
  desired_start: IsoDate | null;
  desired_end: IsoDate | null;
  due_at: IsoDateTime | null;
  line_items_total_uf: DecimalString;
}

export interface ComparisonColumn extends ProposalMoney {
  proposal_id: number;
  insurer: InsurerRef;
  origin: ProposalOrigin;
  status: ProposalStatus;
  is_confirmed: boolean;
  source_document_id: number;
  extraction_confidence: DecimalString | null;
  modality: string | null;
  activity_classification: string | null;
  validity_business_days: number | null;
  coverage_start: IsoDate | null;
  coverage_end: IsoDate | null;
  received_at: IsoDate | null;
  warranties: string | null;
  coverage_count: number;
  exclusion_count: number;
}

export interface DeductibleCell {
  proposal_id: number;
  /** `null` = this proposal says nothing about the peril, itself a finding. */
  term: DeductibleTerm | null;
}

export interface DeductibleRow {
  peril: string;
  cells: DeductibleCell[];
}

export interface CoverageCell {
  proposal_id: number;
  included: boolean;
  text: string | null;
}

export interface CoverageRow {
  key: string;
  label: string;
  kind: CoverageKind;
  normalized_code: string | null;
  cells: CoverageCell[];
}

export interface ComparisonHighlights {
  lowest_total_premium_proposal_id: number | null;
  lowest_comprehensive_rate_proposal_id: number | null;
  highest_commission_proposal_id: number | null;
}

export interface ProposalComparison {
  quote: ComparisonQuote;
  proposal_count: number;
  columns: ComparisonColumn[];
  perils: string[];
  deductibles: DeductibleRow[];
  coverages: CoverageRow[];
  exclusions: CoverageRow[];
  highlights: ComparisonHighlights;
}

// --- Insurers ----------------------------------------------------------------

export interface NativeInsurerProfile {
  insurer_id: number;
  commercial_agreement: string | null;
  onboarded_at: IsoDateTime | null;
  managed_by_user_id: number | null;
  priority: number;
  sla_hours: number | null;
  notes: string | null;
  created_at: IsoDateTime | null;
  updated_at: IsoDateTime | null;
}

export interface Insurer {
  id: number;
  rut: string;
  cmf_code: string;
  legal_name: string;
  trade_name: string | null;
  is_native: boolean;
  status: InsurerStatus;
  cmf_status: string | null;
  payment_url: string | null;
  logo_key: string | null;
  created_by_broker_id: number | null;
  created_at: IsoDateTime | null;
  updated_at: IsoDateTime | null;
  /** Populated only for native insurers (commercial terms never leak). */
  native_profile: NativeInsurerProfile | null;
  /** Whether the caller may edit — render the control disabled when false. */
  can_edit: boolean;
}

export interface InsurerCreate {
  rut: string;
  cmf_code: string;
  legal_name: string;
  trade_name?: string | null;
  status?: InsurerStatus;
  cmf_status?: string | null;
  payment_url?: string | null;
}

export type InsurerUpdate = Partial<InsurerCreate>;

export type ContactScope = "broker" | "global";
export type ContactMatchLevel = "broker_line" | "broker" | "line" | "global";

export interface InsurerContact {
  id: number;
  insurer_id: number;
  /** `null` = a platform-wide default rather than this broker's override. */
  broker_id: number | null;
  /** `null` = serves all insurance lines. */
  insurance_line_id: number | null;
  name: string;
  email: string | null;
  phone: string | null;
  role: string | null;
  is_primary: boolean;
  created_at: IsoDateTime | null;
  updated_at: IsoDateTime | null;
  scope: ContactScope;
  can_edit: boolean;
}

export interface InsurerContactCreate {
  name: string;
  email?: string | null;
  phone?: string | null;
  role?: string | null;
  is_primary?: boolean;
  insurance_line_id?: number | null;
  scope?: ContactScope;
}

export type InsurerContactUpdate = Partial<Omit<InsurerContactCreate, "scope">>;

export interface ResolvedContact {
  insurer_id: number;
  broker_id: number;
  insurance_line_id: number | null;
  /** Which fallback tier answered: (broker+line) -> (broker) -> (line) -> global. */
  match_level: ContactMatchLevel;
  contact: InsurerContact;
}

export interface InsurerRecommendation {
  insurer: Insurer;
  priority: number;
  sla_hours: number | null;
  onboarded_at: IsoDateTime | null;
  has_line_contact: boolean;
  contact: ResolvedContact | null;
  reason: string;
}

export interface InsurerRecommendationResponse {
  insurance_line_id: number | null;
  insurance_line_name: string | null;
  items: InsurerRecommendation[];
}

/** Matching is by normalized rut / cmf_code ONLY — never by name. */
export interface InsurerMatchRequest {
  rut?: string | null;
  cmf_code?: string | null;
  legal_name?: string | null;
}

export interface InsurerMatchResult {
  insurer: Insurer;
  created: boolean;
  matched_on: "rut" | "cmf_code" | null;
}

// --- Inspections -------------------------------------------------------------

export interface InspectionRequest {
  id: number;
  broker_id: number;
  asset_id: number;
  placement_id: number | null;
  insurance_line_id: number | null;
  reason: string | null;
  urgency: Priority;
  target_date: IsoDate | null;
  status: InspectionRequestStatus;
  requested_by_id: number | null;
  requested_at: IsoDateTime | null;
  created_at: IsoDateTime | null;
  updated_at: IsoDateTime | null;
}

export interface InspectionRequestCreate {
  asset_id: number;
  placement_id?: number | null;
  insurance_line_id?: number | null;
  reason?: string | null;
  urgency?: Priority;
  target_date?: IsoDate | null;
  status?: InspectionRequestStatus;
}

export type InspectionRequestUpdate = Partial<Omit<InspectionRequestCreate, "asset_id">>;

/** Colindancia — a neighbouring property and whether it aggravates the risk. */
export interface InspectionBoundary {
  id: number;
  inspection_id: number;
  orientation: string | null;
  description: string | null;
  distance: string | null;
  aggravating: string | null;
  is_aggravating: boolean;
  sort_order: number;
}

export interface InspectionBoundaryCreate {
  orientation?: string | null;
  description?: string | null;
  distance?: string | null;
  aggravating?: string | null;
  is_aggravating?: boolean;
  sort_order?: number;
}

export type InspectionBoundaryUpdate = Partial<InspectionBoundaryCreate>;

/** The eight promoted score columns, all 0-100. */
export interface InspectionScores {
  technical_score: DecimalString | null;
  commercial_score: DecimalString | null;
  location_score: DecimalString | null;
  loss_estimate_score: DecimalString | null;
  overall_score: DecimalString | null;
  pml_pct: DecimalString | null;
  eml_pct: DecimalString | null;
  risk_classification: string | null;
}

export interface Inspection extends InspectionScores {
  id: number;
  broker_id: number;
  asset_id: number;
  inspection_request_id: number | null;
  inspector_id: number | null;
  version: number;
  status: InspectionStatus;
  visit_date: IsoDate | null;
  report_date: IsoDate | null;
  folio: string | null;
  findings_summary: string | null;
  checklist: Record<string, unknown> | null;
  checklist_version: number | null;
  report_document_id: number | null;
  boundaries: InspectionBoundary[];
  created_at: IsoDateTime | null;
  updated_at: IsoDateTime | null;
}

export interface InspectionCreate extends Partial<Record<keyof InspectionScores, number | string | null>> {
  asset_id: number;
  inspection_request_id?: number | null;
  inspector_id?: number | null;
  version?: number | null;
  status?: InspectionStatus;
  visit_date?: IsoDate | null;
  report_date?: IsoDate | null;
  folio?: string | null;
  findings_summary?: string | null;
  checklist?: Record<string, unknown> | null;
  checklist_version?: number | null;
  report_document_id?: number | null;
  boundaries?: InspectionBoundaryCreate[];
}

export type InspectionUpdate = Partial<Omit<InspectionCreate, "asset_id" | "boundaries" | "version">>;

export interface InspectionVersionCreate {
  copy_checklist?: boolean;
  copy_scores?: boolean;
  copy_boundaries?: boolean;
  inspector_id?: number | null;
  visit_date?: IsoDate | null;
}

// --- Documents ---------------------------------------------------------------

/** The ONLY place an S3 key lives. Entities reference files by `document.id`. */
export interface RadalDocument {
  id: number;
  broker_id: number;
  entity_type: EntityType;
  entity_id: number;
  s3_key: string;
  bucket: string;
  original_name: string;
  mime_type: string | null;
  size_bytes: number | null;
  checksum: string | null;
  category: DocumentCategory;
  phase: string | null;
  /** Which expediente this file was filed under (null for loose uploads). */
  case_file_id: number | null;
  /** Which sub-expediente folder inside that case. */
  section: CaseSection | null;
  /** The corpus code hint, e.g. `00A`, `07R`, `09B`. */
  document_code: string | null;
  uploaded_by_id: number | null;
  created_at: IsoDateTime | null;
  updated_at: IsoDateTime | null;
}

export interface DocumentDownload {
  id: number;
  original_name: string;
  mime_type: string | null;
  size_bytes: number | null;
  s3_key: string;
  url: string;
  /** `null` for the local backend, whose URLs do not expire. */
  expires_in_seconds: number | null;
}

export interface DocumentUpdate {
  category?: DocumentCategory;
  phase?: string | null;
  original_name?: string;
}

// --- Offerings ---------------------------------------------------------------

export interface Offering {
  id: number;
  broker_id: number;
  quote_request_id: number;
  selected_proposal_id: number | null;
  share_token: string;
  pdf_document_id: number | null;
  sent_via: OfferingChannel | null;
  status: OfferingStatus;
  sent_at: IsoDateTime | null;
  viewed_at: IsoDateTime | null;
  expires_at: IsoDateTime | null;
  created_by_id: number | null;
  created_at: IsoDateTime | null;
  updated_at: IsoDateTime | null;
  /** The public link the broker copies into WhatsApp / email. */
  share_url: string | null;
  /** The insured's own choice, recorded from the public surface. */
  decided_proposal_id: number | null;
  decided_note: string | null;
  decided_at: IsoDateTime | null;
}

export interface OfferingCreate {
  quote_request_id: number;
  selected_proposal_id?: number | null;
  expires_at?: IsoDateTime | null;
}

// --- Public offering (insured decision surface) ------------------------------

/** One proposal's headline figures for the public page — never any commission. */
export interface OfferingPublicProposal {
  id: number | null;
  insurer_name: string | null;
  insurer_cmf_code: string | null;
  modality: string | null;
  is_recommended: boolean;
  total_premium_uf: DecimalString | null;
  net_premium_uf: DecimalString | null;
  vat_uf: DecimalString | null;
  comprehensive_rate_permille: DecimalString | null;
  validity_business_days: number | null;
  coverage_start: IsoDate | null;
  coverage_end: IsoDate | null;
}

/** The unauthenticated share-link projection — no broker/internal ids. */
export interface OfferingPublicRead {
  share_token: string;
  status: OfferingStatus;
  sent_at: IsoDateTime | null;
  viewed_at: IsoDateTime | null;
  expires_at: IsoDateTime | null;
  broker_name: string | null;
  insured_object: string | null;
  declared_value_uf: DecimalString | null;
  /** The broker's recommendation (kept for back-compat). */
  proposal: OfferingPublicProposal | null;
  /** Every open proposal of the quote, in clear language. */
  proposals: OfferingPublicProposal[];
  decided_proposal_id: number | null;
  decided_note: string | null;
  decided_at: IsoDateTime | null;
  pdf_url: string | null;
}

export interface OfferingDecisionRequest {
  proposal_id: number;
  note?: string | null;
}

// --- Comparison expedient (v8: incremental proposal comparison) --------------

export const COMPARISON_STATUSES = ["draft", "aligned", "superseded"] as const;
export type ComparisonStatus = (typeof COMPARISON_STATUSES)[number];

/** The open facet groups an offer is decomposed into (budget_proposal spec). */
export const FACET_GROUPS = [
  "coverage",
  "exclusion",
  "deductible",
  "sublimit",
  "clause",
  "warranty",
  "other",
] as const;
export type FacetGroup = (typeof FACET_GROUPS)[number];

/** Alignment check kinds surfaced in the grid. */
export const COMPARISON_CHECK_TYPES = ["wrong_file", "partial_facet"] as const;
export type ComparisonCheckType = (typeof COMPARISON_CHECK_TYPES)[number];

/** One canonical dictionary row snapshotted at the comparison's version. */
export interface ComparisonDictionaryEntry {
  key: string;
  group: FacetGroup | string;
  label: string | null;
  /** Only on `extra_facets`: the source ids this facet is present in. */
  present_in?: number[];
}

/** One aligned cell: the value plus its plain-language + verbatim wording.
 *  LEGACY (pre-v8 holistic pass) — retained for callers still on the facet grid. */
export interface ComparisonCell {
  present: boolean | null;
  label: string | null;
  description: string | null;
  verbatim: string | null;
  value: Record<string, unknown> | null;
}

/**
 * One standardized dimension cell: which column it belongs to, whether the offer
 * covers it, its value and the offer's EXACT wording. The AI holistic pass emits
 * one cell per insurer per dimension.
 */
export interface ComparisonDimensionCell {
  comparison_source_id: number | null;
  present?: boolean | null;
  value?: string | number | null;
  verbatim?: string | null;
}

/**
 * One standardized dimension row of `aligned_matrix.dimensions`: a canonical
 * comparison axis (e.g. "sismo") aligned across every insurer. `scope` is
 * `common` when every offer carries it, `extra` otherwise.
 */
export interface ComparisonDimension {
  key: string;
  group: FacetGroup | string;
  label: string | null;
  scope: "common" | "extra" | null;
  cells: ComparisonDimensionCell[];
}

/** One aligned column of the matrix: the insurer/source identity. */
export interface ComparisonMatrixColumn {
  proposal_id: number | null;
  comparison_source_id: number | null;
  is_wrong_file: boolean;
  wrong_file_reason?: string | null;
}

/** One alignment finding: a wrong file, or a facet not priced by everyone. */
export interface ComparisonCheck {
  type: ComparisonCheckType | string;
  comparison_source_id?: number | null;
  proposal_id?: number | null;
  reason?: string | null;
  key?: string;
  label?: string | null;
  present_in?: number[];
  missing_from?: number[];
}

/**
 * The AI recommendation from the holistic `submit_comparison` pass. Points at the
 * recommended column (by `comparison_source_id` and/or the promoted `proposal_id`),
 * carries a Spanish `rationale` and a short list of `caveats` to review. Null until
 * the comparison is aligned.
 */
export interface ComparisonRecommendation {
  recommended_comparison_source_id: number | null;
  recommended_proposal_id: number | null;
  rationale: string | null;
  caveats: string[];
}

/** The rendered aligned view stored on `comparison.aligned_matrix` (v8 holistic). */
export interface ComparisonAlignedMatrix {
  dimensions: ComparisonDimension[];
  columns: ComparisonMatrixColumn[];
  recommendation?: ComparisonRecommendation | null;
  used_ai?: boolean;
  /** The readings exceeded the input threshold and were run in batches then merged. */
  batched?: boolean;
  batch_count?: number;
}

/**
 * The fixed money/period core a column extracted, surfaced PRE-promotion so the
 * grid can show a premium/rate highlight before any column is promoted to a real
 * proposal. Read verbatim from the column's persisted extraction — display only,
 * never re-validated. Null on a wrong-file column or one with no parse.
 */
export interface ComparisonPremiumCore {
  taxable_premium_uf: DecimalString | number | null;
  exempt_premium_uf: DecimalString | number | null;
  net_premium_uf: DecimalString | number | null;
  vat_uf: DecimalString | number | null;
  total_premium_uf: DecimalString | number | null;
  taxable_rate_permille: DecimalString | number | null;
  exempt_rate_permille: DecimalString | number | null;
  comprehensive_rate_permille: DecimalString | number | null;
  commission_pct: DecimalString | number | null;
  validity_business_days: number | string | null;
  period_start_at: string | null;
  period_end_at: string | null;
  coverage_start: string | null;
  coverage_end: string | null;
}

/** One column of the comparison, with its cached source verdict flattened. */
export interface ComparisonEntry {
  id: number;
  comparison_id: number;
  proposal_id: number | null;
  comparison_source_id: number | null;
  is_recommended: boolean;
  sort_order: number;
  is_wrong_file: boolean;
  wrong_file_reason: string | null;
  source_extraction_id: number | null;
  document_id: number | null;
  facets: unknown[] | Record<string, unknown> | null;
  /** The money/period core, surfaced pre-promotion. Null for a wrong-file column. */
  premium: ComparisonPremiumCore | null;
  created_at: IsoDateTime | null;
  updated_at: IsoDateTime | null;
}

/** The comparison header + columns + aligned grid + snapshotted dictionary. */
export interface Comparison {
  id: number;
  broker_id: number;
  case_file_id: number | null;
  placement_id: number | null;
  status: ComparisonStatus;
  canonical_version: number;
  dictionary: ComparisonDictionaryEntry[] | null;
  aligned_matrix: ComparisonAlignedMatrix | null;
  /** The AI's recommended offer, surfaced first-class. Null until aligned. */
  recommendation: ComparisonRecommendation | null;
  pdf_document_id: number | null;
  source_extraction_id: number | null;
  entries: ComparisonEntry[];
  created_at: IsoDateTime | null;
  updated_at: IsoDateTime | null;
}

export interface ComparisonCreate {
  case_file_id: number;
  placement_id?: number | null;
}

export interface ComparisonEntryCreate {
  document_id: number;
}

export interface ComparisonEntryUpdate {
  is_recommended?: boolean | null;
  sort_order?: number | null;
  facets?: unknown[] | null;
}

/** The response to adding a column: the entry plus the read verdict. */
export interface ComparisonEntryResult {
  entry: ComparisonEntry;
  extraction_id: number;
  document_type: string;
  is_wrong_file: boolean;
  rejection_reason: string | null;
  warnings: string[];
}

export interface ComparisonAlignmentResult {
  comparison: Comparison;
  used_ai: boolean;
  canonical_version: number;
  /** The readings were run in batches then merged (large input). */
  batched: boolean;
  recommendation?: ComparisonRecommendation | null;
  warnings: string[];
}

/**
 * Optional insurer identity the reviewer supplies at promote time. Real
 * cotización PDFs carry only the insured's RUT, so the extractor often cannot
 * fill the insurer's identity; a valid supplied value wins over the parsed one.
 * The server 422s (`insurer_unresolved` / `insurer_identity_incomplete`) when
 * neither source resolves an insurer.
 */
export interface ComparisonPromotePayload {
  insurer_rut?: string | null;
  insurer_cmf_code?: string | null;
}

export interface ComparisonPromoteResult {
  entry: ComparisonEntry;
  proposal_id: number;
  insurer_id: number;
  quote_request_id: number;
}

// --- Broker propuesta (v8 outbound artifact) ---------------------------------
// TERMINOLOGY TRAP: a `BrokerProposal` is NOT a `Proposal`. `Proposal` is the
// insurer's inbound offer; `BrokerProposal` is the outbound propuesta the broker
// mints SOLELY from an aligned comparison snapshot, ratifies, and sends.

export const BROKER_PROPOSAL_STATUSES = [
  "draft",
  "issued",
  "ratified",
  "superseded",
] as const;
export type BrokerProposalStatus = (typeof BROKER_PROPOSAL_STATUSES)[number];

/** The winning column snapshot the mint freezes into `payload.winning_proposal`. */
export interface BrokerProposalWinner {
  id: number;
  insurer_id: number;
  taxable_premium_uf: DecimalString | null;
  exempt_premium_uf: DecimalString | null;
  net_premium_uf: DecimalString | null;
  vat_uf: DecimalString | null;
  total_premium_uf: DecimalString | null;
  comprehensive_rate_permille: DecimalString | null;
}

/**
 * The validated minimum core the propuesta freezes: insurer identity, the insured,
 * the vigencia and the premium core in UF. Shapes vary (the AI fills what the
 * winning offer stated), so every leaf is lenient and unknown keys survive.
 */
export interface BrokerProposalCore {
  insured_name?: string | null;
  insured_rut?: string | null;
  insurer_name?: string | null;
  insurer_rut?: string | null;
  insurer_cmf_code?: string | null;
  coverage_start?: string | null;
  coverage_end?: string | null;
  validity_business_days?: number | string | null;
  taxable_premium_uf?: DecimalString | number | null;
  exempt_premium_uf?: DecimalString | number | null;
  net_premium_uf?: DecimalString | number | null;
  vat_uf?: DecimalString | number | null;
  total_premium_uf?: DecimalString | number | null;
  comprehensive_rate_permille?: DecimalString | number | null;
  commission_pct?: DecimalString | number | null;
  [key: string]: unknown;
}

/** One row of the propuesta's free tail — a heterogeneous dimension appended. */
export interface BrokerProposalAdditionalItem {
  group?: string | null;
  label?: string | null;
  value?: string | number | null;
  verbatim?: string | null;
  [key: string]: unknown;
}

/**
 * The snapshotted comparison the propuesta was minted from — the aligned matrix,
 * the dictionary and the winning offer's frozen money core.
 */
export interface BrokerProposalComparisonSnapshot {
  comparison_id?: number;
  case_file_id?: number | null;
  canonical_version?: number;
  dictionary?: ComparisonDictionaryEntry[] | null;
  aligned_matrix?: ComparisonAlignedMatrix | null;
  winning_proposal?: BrokerProposalWinner;
  [key: string]: unknown;
}

export interface BrokerProposalPayload {
  /** The validated minimum core (v8 shape). */
  core?: BrokerProposalCore | null;
  /** The free tail — everything else worth carrying. */
  additional?: BrokerProposalAdditionalItem[] | null;
  /** The comparison this was minted from. */
  comparison_snapshot?: BrokerProposalComparisonSnapshot | null;
  // --- Legacy top-level fields (pre-v8 payloads) ---
  comparison_id?: number;
  case_file_id?: number | null;
  canonical_version?: number;
  winning_proposal?: BrokerProposalWinner;
  [key: string]: unknown;
}

export interface BrokerProposal {
  id: number;
  broker_id: number;
  case_file_id: number | null;
  comparison_id: number | null;
  winning_proposal_id: number | null;
  content_hash: string | null;
  payload: BrokerProposalPayload | null;
  pdf_document_id: number | null;
  status: BrokerProposalStatus;
  is_ratified: boolean;
  ratified_at: IsoDateTime | null;
  ratified_by_id: number | null;
  created_at: IsoDateTime | null;
  updated_at: IsoDateTime | null;
}

/** `POST /broker-proposals` — mint from an ALIGNED comparison + a promoted winner. */
export interface BrokerProposalCreate {
  comparison_id: number;
  winning_proposal_id: number;
  winner_note?: string | null;
}

/** `POST /broker-proposals/{id}/ratify`. */
export interface BrokerProposalRatify {
  note?: string | null;
}

export interface OfferingUpdate {
  selected_proposal_id?: number | null;
  expires_at?: IsoDateTime | null;
  status?: OfferingStatus;
}

export interface OfferingSend {
  channel: OfferingChannel;
  sent_at?: IsoDateTime | null;
}

// --- AI (suggest -> human confirm -> commit) ---------------------------------

export interface ExtractionInsurerRef {
  cmf_code: string | null;
  rut: string | null;
  legal_name: string | null;
  trade_name: string | null;
}

export interface CoverageSuggestion {
  kind: CoverageKind;
  text: string;
  normalized_code: string | null;
  sort_order: number;
}

/** The standard proposal shape: AI-filled, human-editable, NOT yet committed. */
export interface ProposalSuggestion {
  insurer: ExtractionInsurerRef;
  modality: string | null;
  activity_classification: string | null;
  taxable_premium_uf: DecimalString | null;
  exempt_premium_uf: DecimalString | null;
  net_premium_uf: DecimalString | null;
  vat_uf: DecimalString | null;
  total_premium_uf: DecimalString | null;
  taxable_rate_permille: DecimalString | null;
  exempt_rate_permille: DecimalString | null;
  comprehensive_rate_permille: DecimalString | null;
  commission_pct: DecimalString | null;
  validity_business_days: number | null;
  coverage_start: IsoDate | null;
  coverage_end: IsoDate | null;
  received_at: IsoDate | null;
  deductibles: Deductibles;
  warranties: string | null;
  notes: string | null;
  coverages: CoverageSuggestion[];
}

export type ExtractionStatus = "pending" | "running" | "succeeded" | "failed";

/** The persisted AI job — the audit trail, always returned with the suggestion. */
export interface Extraction {
  id: number;
  document_id: number;
  kind: string;
  model: string;
  prompt_version: string;
  status: ExtractionStatus;
  confidence: DecimalString | null;
  error: string | null;
  prompt_tokens: number | null;
  completion_tokens: number | null;
  started_at: IsoDateTime | null;
  finished_at: IsoDateTime | null;
  created_at: IsoDateTime | null;
}

export interface ExtractionSuggestionResponse {
  extraction: Extraction;
  suggestion: ProposalSuggestion | null;
  /** Raw parsed JSON, kept so a field the schema could not coerce stays visible. */
  parsed: Record<string, unknown> | null;
  warnings: string[];
}

export interface ProposalConfirmRequest {
  quote_request_id: number;
  proposal: ProposalSuggestion;
  status?: ProposalStatus;
  is_confirmed?: boolean;
}

export type AgentScope = "proposal" | "quote" | "general";
export type AgentRole = "system" | "user" | "assistant" | "tool";

export interface AgentThread {
  id: number;
  broker_id: number;
  user_id: number;
  scope: AgentScope;
  entity_type: string | null;
  entity_id: number | null;
  title: string | null;
  is_archived: boolean;
  last_message_at: IsoDateTime | null;
  created_at: IsoDateTime | null;
}

export interface AgentMessage {
  id: number;
  thread_id: number;
  role: AgentRole;
  content: string | null;
  model: string | null;
  tokens: number | null;
  created_at: IsoDateTime | null;
}

export interface AgentExchange {
  thread_id: number;
  user_message: AgentMessage;
  assistant_message: AgentMessage;
}

// --- Users -------------------------------------------------------------------

export interface UserCreate {
  email: string;
  full_name: string;
  role: string;
  password?: string | null;
  job_title?: string | null;
  phone?: string | null;
  is_active?: boolean;
}

export interface UserUpdate {
  full_name?: string;
  job_title?: string | null;
  phone?: string | null;
}

export interface UserInviteResponse {
  user: User;
  /** Returned exactly ONCE, when the API generated the password. */
  temporary_password: string | null;
}

export interface AssignableRole {
  role: string;
  user_type: UserType;
}

// =============================================================================
// Case files (expedientes) — spec §2.2 / §6.2
// =============================================================================

/** Sub-expediente vocabulary; mirrors `CaseSection` (spec §1). */
export const CASE_SECTIONS = [
  "root_prospect",
  "submission",
  "insurer_quotes",
  "broker_proposal",
  "policy_file",
  "collection",
  "endorsement",
  "claim",
  "renewal",
] as const;
export type CaseSection = (typeof CASE_SECTIONS)[number];

export const CASE_FILE_KINDS = [
  "account",
  "endorsement",
  "collection",
  "claim",
  "renewal",
] as const;
export type CaseFileKind = (typeof CASE_FILE_KINDS)[number];

export const CASE_FILE_STATUSES = [
  "open",
  "on_hold",
  "won",
  "lost",
  "cancelled",
  "closed",
] as const;
export type CaseFileStatus = (typeof CASE_FILE_STATUSES)[number];

/**
 * The journey machine. The first block is the account/renewal chain — the
 * seven hitos c1..c7 the Journey component renders — followed by the three
 * post-sale chains and the terminal `closed`.
 */
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
] as const;

export const ENDORSEMENT_STAGES = [
  "endorsement_requested",
  "endorsement_proposed",
  "endorsement_issued",
  "endorsement_applied",
] as const;

export const COLLECTION_STAGES = [
  "collection_scheduled",
  "collection_in_progress",
  "collection_overdue",
  "collection_settled",
  "collection_suspended",
] as const;

export const CLAIM_STAGES = [
  "claim_reported",
  "claim_adjusting",
  "claim_preliminary",
  "claim_final",
  "claim_settled",
] as const;

export const CASE_STAGES = [
  ...ACCOUNT_STAGES,
  "renewal_review",
  ...ENDORSEMENT_STAGES,
  ...COLLECTION_STAGES,
  ...CLAIM_STAGES,
  "closed",
] as const;
export type CaseStage = (typeof CASE_STAGES)[number];

/** The forward path per kind — what the Journey component draws as a rail. */
export const CASE_STAGE_FLOW: Record<CaseFileKind, readonly CaseStage[]> = {
  account: [...ACCOUNT_STAGES],
  renewal: ["renewal_review", ...ACCOUNT_STAGES],
  endorsement: [...ENDORSEMENT_STAGES],
  collection: [...COLLECTION_STAGES],
  claim: [...CLAIM_STAGES],
};

export interface CaseFileRef {
  id: number;
  reference: string | null;
  title: string;
  kind: CaseFileKind;
  stage: CaseStage;
  status: CaseFileStatus;
  sequence_no: number;
  version: number;
  opened_at: IsoDateTime | null;
  closed_at: IsoDateTime | null;
  /** Sibling-in-time context (v3): how this folder came to exist. */
  origin: CaseOrigin;
  period_label: string | null;
}

export interface CaseStageEvent {
  id: number;
  case_file_id: number;
  from_stage: CaseStage | null;
  to_stage: CaseStage;
  occurred_at: IsoDateTime;
  user_id: number | null;
  note: string | null;
  meta: Record<string, unknown> | null;
}

export interface CaseFile {
  id: number;
  broker_id: number;
  client_id: number;
  placement_id: number | null;
  policy_id: number | null;
  parent_case_file_id: number | null;
  supersedes_case_file_id: number | null;
  insurance_line_id: number | null;

  kind: CaseFileKind;
  stage: CaseStage;
  status: CaseFileStatus;
  reference: string | null;
  title: string;
  sequence_no: number;
  version: number;
  owner_user_id: number | null;
  opened_at: IsoDateTime | null;
  due_at: IsoDateTime | null;
  closed_at: IsoDateTime | null;
  summary: string | null;
  meta: Record<string, unknown> | null;
  created_at: IsoDateTime | null;
  updated_at: IsoDateTime | null;

  client_legal_name: string | null;
  client_rut: string | null;
  insurance_line_name: string | null;
  documents_count: number;

  // --- Groups & accounts (spec v3 §4.3, last bullet) ----------------------
  /** The broker-private Group this folder belongs to. */
  account_group_id: number | null;
  /** The AUTHORITATIVE validity period of the folder — the timeline sort key. */
  period_start: IsoDate | null;
  period_end: IsoDate | null;
  /** `"2026-2027"`. A grouping LABEL over per-account full dates, never a range. */
  period_label: string | null;
  origin: CaseOrigin;
  /** The folder this one was renewed / re-perioded FROM. */
  origin_case_file_id: number | null;
  /** Router-filled: the group's name. */
  account_group_name: string | null;
  /**
   * True once a stage event beyond `intake` exists (rule 1): the period is a
   * folder from then on, and the only door is `POST /case-files/{id}/reperiod`.
   */
  period_locked: boolean;
  /** Member RUTs: `account_client` rows ∪ placements, contratante first. */
  client_ids: number[];
}

export interface CaseSectionCount {
  section: CaseSection | null;
  count: number;
}

export interface CaseFileDetail extends CaseFile {
  placement_status: string | null;
  policy_number: string | null;
  period: string | null;
  documents_by_section: CaseSectionCount[];
  children: CaseFileRef[];
  versions: CaseFileRef[];
  latest_stage_event: CaseStageEvent | null;
  proposals_count: number;
  quote_requests_count: number;
  packs_count: number;
  notes_count: number;
}

export interface CaseFileCreate {
  kind?: CaseFileKind;
  client_id?: number | null;
  placement_id?: number | null;
  policy_id?: number | null;
  parent_case_file_id?: number | null;
  insurance_line_id?: number | null;
  title?: string | null;
  stage?: CaseStage | null;
  status?: CaseFileStatus;
  owner_user_id?: number | null;
  due_at?: IsoDateTime | null;
  summary?: string | null;
  meta?: Record<string, unknown> | null;

  // --- Groups & accounts (spec v3 §4.3) -----------------------------------
  /** REQUIRED by the server for `kind=account` (422 otherwise). */
  period_start?: IsoDate | null;
  period_end?: IsoDate | null;
  /** Omitted -> the server derives `` `${start.year}-${end.year}` ``. */
  period_label?: string | null;
  /** Omitted -> the client's group. */
  account_group_id?: number | null;
  /** Extra RUTs -> `account_client`; the contratante need not be repeated. */
  client_ids?: number[] | null;
  /** Lets `kind=renewal` be created WITHOUT a `policy_id`. */
  origin_case_file_id?: number | null;
}

export interface CaseFileUpdate {
  title?: string | null;
  status?: CaseFileStatus | null;
  owner_user_id?: number | null;
  due_at?: IsoDateTime | null;
  summary?: string | null;
  meta?: Record<string, unknown> | null;
  insurance_line_id?: number | null;
  policy_id?: number | null;
  parent_case_file_id?: number | null;
}

/** One row of `GET /case-files/{id}/transitions` — the UI enables from this. */
export interface CaseTransitionOption {
  to_stage: CaseStage;
  allowed: boolean;
  reason: string | null;
}

export interface CaseFileTransitions {
  id: number;
  kind: CaseFileKind;
  stage: CaseStage;
  options: CaseTransitionOption[];
  is_terminal: boolean;
}

export interface CaseFileSummary {
  total: number;
  open: number;
  by_kind: Record<string, number>;
  by_stage: Record<string, number>;
  by_status: Record<string, number>;
  overdue: number;
}

/** The English `code` tokens `GET /case-files/{id}/pending-actions` can carry. */
export const PENDING_ACTION_CODES = [
  "antecedentes_missing",
  "antecedentes_unregistered",
  "proposals_unconfirmed",
  "comparison_unaligned",
  "propuesta_missing",
  "stage_blocked",
] as const;
export type PendingActionCode = (typeof PENDING_ACTION_CODES)[number];

export type PendingActionSeverity = "info" | "warning" | "blocker";

/**
 * One actionable gap in the account, for the overview Journey and the account
 * SUMMARY. `code` is an English token (dynamic-key labelled), `tab` names the
 * desk that owns the fix (server vocabulary: `record | comparison | proposal |
 * journey`), `reason` is the server's own human sentence, `count` is how many
 * items the gap covers.
 */
export interface PendingAction {
  code: PendingActionCode | string;
  severity: PendingActionSeverity | string;
  tab: string;
  reason: string;
  count: number;
}

export interface PendingActionsResponse {
  case_file_id: number;
  actions: PendingAction[];
}

// --- Module summaries (the /analytics dashboard) -----------------------------
//
// `GET /{module}/summary` — auth required, gated on the module's `View`
// permission, every count broker-scoped. `by_status` is zero-filled over the
// full enum server-side, so the UI can index it without guards. Decimal fields
// arrive as `DecimalString` (an empty book serializes `"0"`, not `"0.0000"`) —
// parse with `num()`, never string-compare.

/** `GET /quotes/summary`. */
export interface QuoteSummary {
  total: number;
  by_status: Record<QuoteRequestStatus, number>;
  /** `sent_at` within the last 30 days. */
  sent_last_30d: number;
  /** `due_at < now` and still in draft | sent | receiving. */
  overdue: number;
}

export interface ProposalInsurerCount {
  insurer_id: number;
  insurer_name: string;
  count: number;
}

/** `GET /proposals/summary`. */
export interface ProposalSummary {
  total: number;
  by_status: Record<ProposalStatus, number>;
  /** Top 10 by count desc (ties by insurer_id asc); counts ALL statuses. */
  by_insurer: ProposalInsurerCount[];
  confirmed_count: number;
  /** SUM over proposals still in the running (draft | submitted | accepted). */
  total_premium_uf: DecimalString;
  /** AVG `comprehensive_rate_permille` over the same statuses; null when none. */
  avg_rate_permille: DecimalString | null;
}

/** `GET /policies/summary`. */
export interface PolicySummary {
  total: number;
  by_status: Record<PolicyStatus, number>;
  active_count: number;
  /** SUM over ACTIVE policies only. */
  total_premium_uf: DecimalString;
  /** ACTIVE with `end_date` between today and today+60d (inclusive). */
  expiring_within_60_days: number;
}

export interface CaseDocument {
  id: number;
  original_name: string;
  category: DocumentCategory;
  category_label: string;
  section: CaseSection | null;
  document_code: string | null;
  mime_type: string | null;
  size_bytes: number | null;
  uploaded_by_id: number | null;
  created_at: IsoDateTime | null;
  url: string;
  expires_in_seconds: number | null;
}

export interface CaseDocumentSection {
  section: CaseSection | null;
  label: string;
  documents: CaseDocument[];
}

export interface CaseDocumentGroups {
  case_file_id: number;
  total: number;
  sections: CaseDocumentSection[];
}

export interface CaseTimelineEntry {
  /** `"stage" | "activity" | "note"` — the server sends a bare string. */
  kind: string;
  occurred_at: IsoDateTime;
  /** Identifier or action key only — never prose. */
  title: string;
  /** Free text the user wrote. Rendered as-is. */
  detail: string | null;
  /** Enum token behind `detail`; translate it, never print it raw. */
  detail_token: string | null;
  /** Stage rows only. */
  from_stage: string | null;
  to_stage: string | null;
  user_id: number | null;
  meta: Record<string, unknown> | null;
}

export interface CaseFileTimeline {
  case_file_id: number;
  entries: CaseTimelineEntry[];
}

// =============================================================================
// Leads — spec §2.1 / §6.1
// =============================================================================

export const LEAD_STATUSES = [
  "new",
  "contacted",
  "qualified",
  "converted",
  "lost",
] as const;
export type LeadStatus = (typeof LEAD_STATUSES)[number];

export interface Lead {
  id: number;
  broker_id: number;
  name: string;
  rut: string | null;
  contact_name: string | null;
  contact_email: string | null;
  contact_phone: string | null;
  source: string | null;
  insurance_line_id: number | null;
  estimated_premium_uf: DecimalString | null;
  status: LeadStatus;
  follow_up_on: IsoDate | null;
  owner_user_id: number | null;
  lost_reason: string | null;
  converted_client_id: number | null;
  converted_case_file_id: number | null;
  summary: string | null;
  created_at: IsoDateTime | null;
  updated_at: IsoDateTime | null;

  insurance_line_name: string | null;
  notes_count: number;
}

export interface LeadCreate {
  name: string;
  rut?: string | null;
  contact_name?: string | null;
  contact_email?: string | null;
  contact_phone?: string | null;
  source?: string | null;
  insurance_line_id?: number | null;
  estimated_premium_uf?: number | string | null;
  follow_up_on?: IsoDate | null;
  owner_user_id?: number | null;
  summary?: string | null;
  status?: LeadStatus;
}

export type LeadUpdate = Partial<LeadCreate> & { lost_reason?: string | null };

export interface LeadSummary {
  total: number;
  by_status: Record<string, number>;
  due_this_week: number;
  overdue: number;
}

export interface LeadAssetInput {
  name: string;
  asset_type?: string;
  address?: string | null;
  commune?: string | null;
  region?: string | null;
}

export interface LeadConvertRequest {
  insured_rut: string;
  insured_legal_name: string;
  insurance_line_id?: number | null;
  period?: string | null;
  period_start?: IsoDate | null;
  period_end?: IsoDate | null;
  title?: string | null;
  asset?: LeadAssetInput | null;
}

export interface LeadConversionResult {
  lead: Lead;
  client_id: number;
  insured_id: number;
  asset_id: number;
  placement_id: number;
  case_file_id: number;
  case_file_reference: string | null;
}

// =============================================================================
// Notes & activity — spec §6.3
// =============================================================================

export interface Note {
  id: number;
  broker_id: number;
  entity_type: EntityType;
  entity_id: number;
  author_id: number | null;
  body: string;
  is_internal: boolean;
  phase: string | null;
  follow_up_on: IsoDate | null;
  created_at: IsoDateTime | null;
  updated_at: IsoDateTime | null;
  author_name: string | null;
}

export interface NoteCreate {
  entity_type: EntityType;
  entity_id: number;
  body: string;
  is_internal?: boolean;
  phase?: string | null;
  follow_up_on?: IsoDate | null;
}

export interface NoteUpdate {
  body?: string;
  is_internal?: boolean;
  phase?: string | null;
  follow_up_on?: IsoDate | null;
}

export interface Activity {
  id: number;
  broker_id: number;
  user_id: number | null;
  action: string;
  entity_type: EntityType;
  entity_id: number | null;
  description: string | null;
  meta: Record<string, unknown> | null;
  occurred_at: IsoDateTime;
  user_name: string | null;
}

// =============================================================================
// Packs — spec §6.5 / §8
// =============================================================================

export const PACK_KINDS = ["submission", "comparison", "proposal"] as const;
export type PackKind = (typeof PACK_KINDS)[number];

export const PACK_STATUSES = [
  "draft",
  "generating",
  "generated",
  "sent",
  "failed",
] as const;
export type PackStatus = (typeof PACK_STATUSES)[number];

/** Contact precedence: broker+line -> broker -> line -> global. */
export interface PackRecipient {
  insurer_id: number;
  legal_name: string;
  is_native: boolean;
  contact_name: string | null;
  contact_email: string | null;
  resolution_level: string | null;
}

export interface RecipientList {
  case_file_id: number;
  insurance_line_id: number | null;
  items: PackRecipient[];
}

export interface CasePack {
  id: number;
  broker_id: number;
  case_file_id: number;
  kind: PackKind;
  section: CaseSection | null;
  status: PackStatus;
  pdf_document_id: number | null;
  zip_document_id: number | null;
  summary: string | null;
  summary_model: string | null;
  summary_prompt_version: string | null;
  is_summary_confirmed: boolean;
  recipients: PackRecipient[];
  generated_at: IsoDateTime | null;
  sent_at: IsoDateTime | null;
  generated_by_id: number | null;
  error: string | null;
  created_at: IsoDateTime | null;
  updated_at: IsoDateTime | null;
  pdf: DocumentDownload | null;
  zip: DocumentDownload | null;
}

export type PackPart = "pdf" | "zip";

// =============================================================================
// Post-sale — policies, endorsements, collections, claims, warranties
// (spec §2.5-§2.8 / §6.4). Types published HERE by core; the postsale pass
// consumes them and never redeclares one locally.
// =============================================================================

export const POLICY_STATUSES = [
  "draft",
  "active",
  "expired",
  "cancelled",
  "renewed",
] as const;
export type PolicyStatus = (typeof POLICY_STATUSES)[number];

export interface PolicyMoney {
  insured_amount_uf: DecimalString | null;
  taxable_premium_uf: DecimalString | null;
  exempt_premium_uf: DecimalString | null;
  net_premium_uf: DecimalString | null;
  vat_uf: DecimalString | null;
  total_premium_uf: DecimalString | null;
  commission_pct: DecimalString | null;
}

export interface Policy extends PolicyMoney {
  id: number;
  broker_id: number;
  client_id: number;
  asset_id: number | null;
  placement_id: number | null;
  proposal_id: number | null;
  insurer_id: number;
  insurance_line_id: number | null;
  case_file_id: number | null;
  source_document_id: number | null;
  renews_policy_id: number | null;

  policy_number: string;
  start_date: IsoDate | null;
  end_date: IsoDate | null;
  /** The contractual 12:00 convention; `start_date`/`end_date` stay in sync. */
  period_start_at: IsoDateTime | null;
  period_end_at: IsoDateTime | null;
  issued_at: IsoDate | null;
  status: PolicyStatus;

  cover_mode: string | null;
  cmf_policy_code: string | null;
  insured_amount_semantics: string | null;
  aggregate_limit_uf: DecimalString | null;
  average_rate_permille: DecimalString | null;
  indemnity_limit: string | null;

  deductibles: Deductibles | null;
  notes: string | null;
  created_at: IsoDateTime | null;
  updated_at: IsoDateTime | null;

  insurer_name: string | null;
  client_legal_name: string | null;
  endorsements_count: number;
  warranties_count: number;
  claims_count: number;

  // v8 validate-then-dynamic ingestion: `payload` is the FULL confirmed parse
  // (the dynamic remainder beyond the typed money/vigencia columns above), and
  // the fixed-core verdict travels alongside it. All optional — a policy created
  // by hand or from a proposal carries none of them.
  payload: Record<string, unknown> | null;
  is_core_valid: boolean | null;
  core_validation: PolicyCoreValidation | null;
  extraction_id: number | null;
}

/** The fixed minimal core the uploader checks: corredor / asegurado / vigencia /
 *  desglose de prima. Shape is defensive — the server owns the exact contents. */
export interface PolicyCoreValidation {
  is_core_valid?: boolean;
  missing?: string[];
  detail?: Record<string, unknown> | null;
  [key: string]: unknown;
}

/** `POST /policies/upload` — point at an already-filed document. */
export interface PolicyUploadRequest {
  document_id: number;
  /** Attach the document to this account folder when it carries none yet. */
  case_file_id?: number | null;
  /** Force the commit even when the fixed core is incomplete (confirmed override). */
  override?: boolean;
}

/** `POST /policies/upload` success — the committed policy + the core verdict. */
export interface PolicyUploadResponse {
  policy: Policy;
  extraction_id: number;
  is_core_valid: boolean;
  core_validation: PolicyCoreValidation | null;
  overridden: boolean;
  warnings: string[];
}

/** The 422 body when the upload does not look like a policy (`not_a_policy`). */
export interface PolicyNotAPolicyDetail {
  code: "not_a_policy";
  reason?: string;
  missing?: string[];
  detail?: Record<string, unknown> | null;
}

export interface PolicyCreate
  extends Partial<Record<keyof PolicyMoney, number | string | null>> {
  insurer_id: number;
  policy_number: string;
  client_id?: number | null;
  asset_id?: number | null;
  placement_id?: number | null;
  proposal_id?: number | null;
  insurance_line_id?: number | null;
  case_file_id?: number | null;
  source_document_id?: number | null;
  renews_policy_id?: number | null;
  start_date?: IsoDate | null;
  end_date?: IsoDate | null;
  period_start_at?: IsoDateTime | null;
  period_end_at?: IsoDateTime | null;
  issued_at?: IsoDate | null;
  status?: PolicyStatus;
  cover_mode?: string | null;
  cmf_policy_code?: string | null;
  insured_amount_semantics?: string | null;
  aggregate_limit_uf?: number | string | null;
  average_rate_permille?: number | string | null;
  indemnity_limit?: string | null;
  deductibles?: Record<string, unknown> | null;
  notes?: string | null;
}

export type PolicyUpdate = Partial<Omit<PolicyCreate, "insurer_id" | "policy_number">> & {
  insurer_id?: number;
  policy_number?: string;
};

export interface PolicyFromProposalRequest {
  proposal_id: number;
  policy_number?: string | null;
  source_document_id?: number | null;
  case_file_id?: number | null;
  period_start_at?: IsoDateTime | null;
  period_end_at?: IsoDateTime | null;
}

/** One field where the issued policy departs from the issuance proposal. */
export interface MirrorDiffRow {
  path: string;
  expected: unknown;
  found: unknown;
  severity: string;
  suggested_endorsement_kind: EndorsementKind | string | null;
}

export interface MirrorDiff {
  policy_id: number;
  proposal_extraction_id: number | null;
  policy_extraction_id: number | null;
  /** "confirmed" (human signed off) or "latest_succeeded" (fallback while the
   *  review is pending); null when that side is missing entirely. */
  proposal_source: string | null;
  policy_source: string | null;
  missing_sources: string[];
  rows: MirrorDiffRow[];
}

export interface MirrorDiffQueueRequest {
  /** Empty = every row of the current diff. */
  paths?: string[];
  case_file_id?: number | null;
}

export interface MirrorDiffQueueResult {
  policy_id: number;
  queued: number;
  endorsement_ids: number[];
}

// --- Warranties (R-n / G-n / M-n) --------------------------------------------

export const WARRANTY_SOURCES = [
  "inspection_recommendation",
  "underwriting_warranty",
  "engineering_measure",
] as const;
export type WarrantySource = (typeof WARRANTY_SOURCES)[number];

export const WARRANTY_STATUSES = [
  "pending",
  "in_progress",
  "met_on_time",
  "met_late",
  "met_after_claim",
  "breached",
  "waived",
] as const;
export type WarrantyStatus = (typeof WARRANTY_STATUSES)[number];

export interface Warranty {
  id: number;
  broker_id: number;
  policy_id: number;
  case_file_id: number | null;
  code: string | null;
  title: string | null;
  requirement: string | null;
  source: WarrantySource;
  /** The inspection's A/B/C/D letter. */
  category: string | null;
  deadline_days: number | null;
  due_date: IsoDate | null;
  is_permanent: boolean;
  is_suspensive: boolean;
  status: WarrantyStatus;
  completed_on: IsoDate | null;
  verification: string | null;
  budget_uf: DecimalString | null;
  actual_cost_uf: DecimalString | null;
  evidence_document_id: number | null;
  sort_order: number;
  created_at: IsoDateTime | null;
  updated_at: IsoDateTime | null;
}

export interface WarrantyCreate {
  code?: string | null;
  title?: string | null;
  requirement?: string | null;
  source?: WarrantySource;
  category?: string | null;
  deadline_days?: number | null;
  due_date?: IsoDate | null;
  is_permanent?: boolean;
  is_suspensive?: boolean;
  status?: WarrantyStatus;
  completed_on?: IsoDate | null;
  verification?: string | null;
  budget_uf?: number | string | null;
  actual_cost_uf?: number | string | null;
  evidence_document_id?: number | null;
  sort_order?: number;
  case_file_id?: number | null;
}

export type WarrantyUpdate = Partial<WarrantyCreate>;

// --- Endorsements -------------------------------------------------------------

export const ENDORSEMENT_KINDS = [
  "location_inclusion",
  "location_exclusion",
  "vehicle_inclusion",
  "vehicle_exclusion",
  "additional_insured",
  "activity_extension",
  "aggregate_reinstatement",
  "roster_increase",
  "roster_adjustment",
  "pledge_update",
  "policyholder_change",
  "sum_insured_increase",
  "sum_insured_decrease",
  "deductible_reduction",
  // v3 prórroga: moves `policy.end_date` only, never the account folder's
  // period. Written ONLY by `POST /endorsements/batch` (rule 3).
  "period_extension",
  "other",
] as const;
export type EndorsementKind = (typeof ENDORSEMENT_KINDS)[number];

/**
 * The motives that may be fanned out over N policies — mirrors
 * `app.schemas.endorsement.BATCH_KINDS`. `POST /endorsements` REFUSES these
 * (422) and `POST /endorsements/batch` accepts only these, so a motive picker
 * on the single-endorsement form must exclude them and the prórroga screen
 * must offer nothing else. Adding a second batchable motive is a change here
 * and in `BATCH_KINDS`, nowhere else.
 */
export const BATCHABLE_ENDORSEMENT_KINDS = ["period_extension"] as const;
export type BatchableEndorsementKind = (typeof BATCHABLE_ENDORSEMENT_KINDS)[number];

export const ENDORSEMENT_STATUSES = [
  "draft",
  "proposed",
  "issued",
  "applied",
  "rejected",
  "cancelled",
] as const;
export type EndorsementStatus = (typeof ENDORSEMENT_STATUSES)[number];

/**
 * Deltas CARRY A SIGN — an exclusion or a sum-insured decrease is negative and
 * an administrative endorsement is all-zero. Never take an absolute value.
 * `net = taxable + exempt`, `vat = 0.19 * taxable`, `total = net + vat`.
 */
export interface EndorsementMoney {
  insured_amount_delta_uf: DecimalString | null;
  taxable_premium_delta_uf: DecimalString | null;
  exempt_premium_delta_uf: DecimalString | null;
  net_premium_delta_uf: DecimalString | null;
  vat_delta_uf: DecimalString | null;
  total_premium_delta_uf: DecimalString | null;
  commission_delta_uf: DecimalString | null;
}

export interface Endorsement extends EndorsementMoney {
  id: number;
  broker_id: number;
  policy_id: number;
  case_file_id: number | null;
  endorsement_number: string | null;
  sequence_no: number;
  kind: EndorsementKind;
  status: EndorsementStatus;

  effective_at: IsoDateTime | null;
  ends_at: IsoDateTime | null;
  issued_at: IsoDate | null;

  proposal_document_id: number | null;
  issued_document_id: number | null;

  motive: string | null;
  contractual_basis: string | null;

  prorata_days: number | null;
  unexpired_days: number | null;

  effect: Record<string, unknown> | unknown[] | null;
  deductibles: Record<string, unknown> | null;

  extraction_id: number | null;
  extraction_confidence: DecimalString | null;
  is_confirmed: boolean;
  confirmed_by_id: number | null;
  confirmed_at: IsoDateTime | null;
  created_at: IsoDateTime | null;
  updated_at: IsoDateTime | null;

  /**
   * UUID shared by the N endorsements of one prórroga (v3). Server-minted by
   * `POST /endorsements/batch`; never client-supplied. `null` on a single
   * endorsement — which is why the tree renders a batch once, with N chips.
   */
  batch_key: string | null;
}

export interface EndorsementCreate
  extends Partial<Record<keyof EndorsementMoney, number | string | null>> {
  policy_id: number;
  case_file_id?: number | null;
  endorsement_number?: string | null;
  sequence_no?: number | null;
  kind?: EndorsementKind;
  status?: EndorsementStatus;
  effective_at?: IsoDateTime | null;
  ends_at?: IsoDateTime | null;
  issued_at?: IsoDate | null;
  proposal_document_id?: number | null;
  issued_document_id?: number | null;
  motive?: string | null;
  contractual_basis?: string | null;
  effect?: Record<string, unknown> | unknown[] | null;
  deductibles?: Record<string, unknown> | null;
  prorata_days?: number | null;
  unexpired_days?: number | null;
  extraction_id?: number | null;
  extraction_confidence?: number | string | null;
}

export type EndorsementUpdate = Partial<Omit<EndorsementCreate, "policy_id">> & {
  is_confirmed?: boolean;
};

export interface EndorsementIssueRequest {
  issued_document_id?: number | null;
  endorsement_number?: string | null;
  issued_at?: IsoDate | null;
  effective_at?: IsoDateTime | null;
  note?: string | null;
}

export interface EndorsementIssueResult {
  endorsement: Endorsement;
  policy_id: number;
  policy_total_premium_uf: DecimalString | null;
  policy_insured_amount_uf: DecimalString | null;
  collection_plan_id: number | null;
  collection_total_premium_uf: DecimalString | null;
}

// --- Collections ---------------------------------------------------------------

export const PAYMENT_MODES = [
  "coupon_book",
  "direct_debit",
  "card_debit",
  "transfer",
  "single_charge",
  "other",
] as const;
export type PaymentMode = (typeof PAYMENT_MODES)[number];

export const COLLECTION_PLAN_STATUSES = [
  "pending",
  "current",
  "overdue",
  "settled",
  "suspended",
  "terminated",
  "rehabilitated",
] as const;
export type CollectionPlanStatus = (typeof COLLECTION_PLAN_STATUSES)[number];

export const INSTALLMENT_STATUSES = [
  "pending",
  "due",
  "paid",
  "paid_late",
  "overdue",
  "credited",
  "cancelled",
] as const;
export type InstallmentStatus = (typeof INSTALLMENT_STATUSES)[number];

export interface CollectionInstallment {
  id: number;
  broker_id: number;
  collection_plan_id: number;
  number: number;
  coupon_number: string | null;
  due_date: IsoDate | null;
  gross_amount_uf: DecimalString;
  net_premium_uf: DecimalString | null;
  commission_uf: DecimalString | null;
  paid_on: IsoDate | null;
  days_late: number | null;
  status: InstallmentStatus;
  endorsement_id: number | null;
  note: string | null;
  created_at: IsoDateTime | null;
  updated_at: IsoDateTime | null;
}

export interface CollectionInstallmentCreate {
  number: number;
  coupon_number?: string | null;
  due_date?: IsoDate | null;
  gross_amount_uf: number | string;
  net_premium_uf?: number | string | null;
  commission_uf?: number | string | null;
  paid_on?: IsoDate | null;
  days_late?: number | null;
  status?: InstallmentStatus;
  endorsement_id?: number | null;
  note?: string | null;
}

export type CollectionInstallmentUpdate = Partial<
  Omit<CollectionInstallmentCreate, "number">
>;

export interface CollectionPlan {
  id: number;
  broker_id: number;
  policy_id: number;
  case_file_id: number | null;
  plan_number: string | null;
  payment_mode: PaymentMode;
  installment_count: number | null;
  total_premium_uf: DecimalString | null;
  bank: string | null;
  account_number: string | null;
  monthly_interest_rate_pct: DecimalString | null;
  status: CollectionPlanStatus;
  as_of_date: IsoDate | null;
  terminated_at: IsoDateTime | null;
  rehabilitated_at: IsoDateTime | null;
  days_without_cover: number | null;
  rehabilitation_cost_uf: DecimalString | null;
  /** Art. 528 suspension / termination / rehabilitation ledger. */
  art528_events: unknown[] | Record<string, unknown> | null;
  management_note: string | null;
  created_at: IsoDateTime | null;
  updated_at: IsoDateTime | null;
  installments: CollectionInstallment[];
}

export interface CollectionPlanCreate {
  policy_id: number;
  case_file_id?: number | null;
  plan_number?: string | null;
  payment_mode?: PaymentMode;
  installment_count?: number | null;
  total_premium_uf?: number | string | null;
  bank?: string | null;
  account_number?: string | null;
  monthly_interest_rate_pct?: number | string | null;
  status?: CollectionPlanStatus;
  as_of_date?: IsoDate | null;
  days_without_cover?: number | null;
  rehabilitation_cost_uf?: number | string | null;
  art528_events?: unknown[] | Record<string, unknown> | null;
  management_note?: string | null;
  installments?: CollectionInstallmentCreate[];
}

export type CollectionPlanUpdate = Partial<
  Omit<CollectionPlanCreate, "policy_id" | "installments">
> & {
  terminated_at?: IsoDateTime | null;
  rehabilitated_at?: IsoDateTime | null;
};

export interface CollectionAlert {
  code: string;
  severity: string;
  detail: string;
}

export interface CollectionStatus {
  collection_plan_id: number;
  policy_id: number;
  status: CollectionPlanStatus;
  as_of_date: IsoDate | null;
  installment_count: number;
  total_scheduled_uf: DecimalString;
  paid_uf: DecimalString;
  outstanding_uf: DecimalString;
  overdue_uf: DecimalString;
  overdue_count: number;
  paid_count: number;
  compliance_pct: DecimalString;
  next_due_date: IsoDate | null;
  expected_total_uf: DecimalString | null;
  /** Σ instalments == policy gross + Σ endorsement deltas. */
  balances: boolean;
  alerts: CollectionAlert[];
}

// --- Claims ---------------------------------------------------------------------

export const CLAIM_STATUSES = [
  "reported",
  "under_review",
  "settled",
  "paid",
  "rejected",
  "closed",
] as const;
export type ClaimStatus = (typeof CLAIM_STATUSES)[number];

export const CLAIM_RULINGS = [
  "pending",
  "covered",
  "partially_covered",
  "rejected",
] as const;
export type ClaimRuling = (typeof CLAIM_RULINGS)[number];

export const CLAIM_ITEM_KINDS = [
  "material_damage",
  "business_interruption",
  "expense",
  "liability",
  "personal_accident",
  "recovery",
] as const;
export type ClaimItemKind = (typeof CLAIM_ITEM_KINDS)[number];

export interface ClaimItem {
  id: number;
  broker_id: number;
  claim_id: number;
  kind: ClaimItemKind;
  item: string | null;
  basis: string | null;
  notified_uf: DecimalString | null;
  determined_uf: DecimalString | null;
  damage_uf: DecimalString | null;
  deductible_uf: DecimalString | null;
  indemnity_uf: DecimalString | null;
  sort_order: number;
  note: string | null;
  created_at: IsoDateTime | null;
  updated_at: IsoDateTime | null;
}

export interface ClaimItemCreate {
  kind?: ClaimItemKind;
  item?: string | null;
  basis?: string | null;
  notified_uf?: number | string | null;
  determined_uf?: number | string | null;
  damage_uf?: number | string | null;
  deductible_uf?: number | string | null;
  indemnity_uf?: number | string | null;
  sort_order?: number;
  note?: string | null;
}

export interface ClaimItemTotals {
  notified_uf: DecimalString;
  determined_uf: DecimalString;
  damage_uf: DecimalString;
  deductible_uf: DecimalString;
  indemnity_uf: DecimalString;
}

export interface ClaimItems {
  claim_id: number;
  items: ClaimItem[];
  totals: ClaimItemTotals;
}

export interface Claim {
  id: number;
  broker_id: number;
  policy_id: number | null;
  client_id: number;
  asset_id: number | null;
  case_file_id: number | null;

  claim_number: string | null;
  kind: string | null;
  event_date: IsoDate | null;
  reported_date: IsoDate | null;
  /** Hour-level; the 4-hour franchise in the corpus is contractual. */
  occurred_at: IsoDateTime | null;
  reported_at: IsoDateTime | null;
  notice_deadline_days: number | null;
  description: string | null;
  status: ClaimStatus;

  adjuster_name: string | null;
  adjuster_registry: string | null;
  coverage_ruling: ClaimRuling;
  deductible_uf: DecimalString | null;
  loss_ratio_pct: DecimalString | null;

  estimated_amount_uf: DecimalString | null;
  settled_amount_uf: DecimalString | null;
  paid_amount_uf: DecimalString | null;
  recovery_uf: DecimalString | null;
  reserve_uf: DecimalString | null;
  cost_uf: DecimalString | null;
  participation: unknown[] | null;

  created_at: IsoDateTime | null;
  updated_at: IsoDateTime | null;

  items: ClaimItem[];
  policy_number: string | null;
}

export interface ClaimCreate {
  policy_id?: number | null;
  client_id?: number | null;
  asset_id?: number | null;
  case_file_id?: number | null;
  claim_number?: string | null;
  kind?: string | null;
  event_date?: IsoDate | null;
  reported_date?: IsoDate | null;
  occurred_at?: IsoDateTime | null;
  reported_at?: IsoDateTime | null;
  notice_deadline_days?: number | null;
  description?: string | null;
  status?: ClaimStatus;
  adjuster_name?: string | null;
  adjuster_registry?: string | null;
  coverage_ruling?: ClaimRuling;
  deductible_uf?: number | string | null;
  loss_ratio_pct?: number | string | null;
  estimated_amount_uf?: number | string | null;
  settled_amount_uf?: number | string | null;
  paid_amount_uf?: number | string | null;
  recovery_uf?: number | string | null;
  reserve_uf?: number | string | null;
  cost_uf?: number | string | null;
  participation?: unknown[] | null;
  items?: ClaimItemCreate[];
}

export type ClaimUpdate = Partial<Omit<ClaimCreate, "client_id" | "items">>;

export interface ClaimCloseRequest {
  coverage_ruling: ClaimRuling;
  settled_amount_uf?: number | string | null;
  paid_amount_uf?: number | string | null;
  deductible_uf?: number | string | null;
  recovery_uf?: number | string | null;
  loss_ratio_pct?: number | string | null;
  note?: string | null;
}

// =============================================================================
// AI — the generic registry-driven extraction (spec §3 / §6.6)
// =============================================================================

/** One field of a category's Pydantic schema, as `GET /ai/categories` sends it. */
export interface CategoryField {
  name: string;
  type: string | null;
  required: boolean;
  description: string | null;
}

/** A registry row — everything the generic SuggestionForm needs to render. */
export interface CategorySpec {
  category: string;
  canonical_category: string;
  is_alias: boolean;
  code: string | null;
  codes: string[];
  section: string;
  direction: string;
  prompt_version: string;
  module: string;
  schema_name: string;
  prefill_target: string;
  extraction_kind: string;
  /** Spanish prompt guidance — the one place Spanish is allowed outside i18n. */
  guidance: string;
  fields: CategoryField[];
}

export interface CategoryListResponse {
  items: CategorySpec[];
  total: number;
}

export interface DocumentExtractionRequest {
  document_id: number;
  category?: string | null;
}

export interface DocumentExtractionResponse {
  extraction: Extraction;
  category: string;
  canonical_category: string;
  schema_name: string;
  schema_version: string;
  prefill_target: string;
  section: string;
  /** The coerced payload, shaped by the category's schema. */
  payload: Record<string, unknown> | null;
  parsed: Record<string, unknown> | null;
  /** Only set on the insurer-quotation path, for the legacy proposal form. */
  suggestion: ProposalSuggestion | null;
  warnings: string[];
}

export interface DocumentConfirmRequest {
  payload: Record<string, unknown>;
  category?: string | null;
  quote_request_id?: number | null;
  status?: ProposalStatus;
  is_confirmed?: boolean;
}

export interface DocumentConfirmResponse {
  extraction: Extraction;
  category: string;
  prefill_target: string;
  /** True when the confirm wrote the target entity (spec §6.6). */
  applied: boolean;
  /** True for categories that legitimately commit nothing (comparatives,
   *  declinations, cover letters, closure notes) — show `commit.detail`. */
  informational: boolean;
  /** What the commit did: target, entity_type, entity_id, detail, extras. */
  commit: Record<string, unknown> | null;
  payload: Record<string, unknown> | null;
  proposal: Proposal | null;
  warnings: string[];
}

/** AI prose, always UNCONFIRMED: suggest -> edit -> confirmar. */
export interface AiSummary {
  extraction_id: number;
  text: string;
  model: string;
  prompt_version: string;
  is_confirmed: boolean;
  proposal_id: number | null;
  case_file_id: number | null;
  pack_id: number | null;
}

/** One SSE frame of `POST /ai/threads/{id}/stream`. */
export type StreamEvent = "start" | "token" | "done" | "error";

export interface StreamFrame {
  event: StreamEvent;
  data: Record<string, unknown>;
}

// =============================================================================
// Groups & accounts — spec v3 §2, §4.1, §4.2, §4.3
// =============================================================================
//
// A **Group** (`account_group`) is the broker-private label above the
// expediente ("JO PASTERÍA" ≠ the legal "Pacto Food SpA"). It carries no money
// and no stage (rule 7): everything money- or stage-shaped lives on the
// **Account** — `case_file(kind=account|renewal)`, one insurance line × one
// validity period × N RUTs.
//
// Vigencia is a LABEL over per-account FULL dates, never a shared range: one
// group can hold Vehículos ago–ago and Incendio abr–abr both under
// `period_label = "2026-2027"`. Always render `period_start`/`period_end`
// (with months) on a ramo or policy node; `period_label` groups them only.

export const ACCOUNT_GROUP_STATUSES = ["active", "archived"] as const;
export type AccountGroupStatus = (typeof ACCOUNT_GROUP_STATUSES)[number];

/** Membership role of one RUT inside an account (`account_client.role`). */
export const ACCOUNT_CLIENT_ROLES = ["policyholder", "insured"] as const;
export type AccountClientRole = (typeof ACCOUNT_CLIENT_ROLES)[number];

/**
 * How a folder came to exist — one of the three axes that never mix on
 * `case_file` (origin / version / post-sale). A renewal folder is a SIBLING in
 * time of the prior vigencia, never a child of it.
 *
 * Rendered through i18n: ``t(`accounts:origin.${origin}`)``.
 */
export const CASE_ORIGINS = ["new", "renewal", "period_change"] as const;
export type CaseOrigin = (typeof CASE_ORIGINS)[number];

/**
 * The five ANTECEDENTES leaves (the team's Excel G8–G12: MONTOS,
 * SINIESTRALIDAD, INFORME, CUESTIONARIO, SLIP DE T&C).
 *
 * The key list is fixed, but **the category mapping is server-owned**: read it
 * from `NavigatorResponse.record_folders`, never hardcode it in a component.
 */
export const RECORD_FOLDER_KEYS = [
  "amounts",
  "loss_history",
  "report",
  "questionnaire",
  "slip",
] as const;
export type RecordFolderKey = (typeof RECORD_FOLDER_KEYS)[number];

/** Why `POST /case-files/{id}/renew` would be refused (null when allowed). */
export const RENEWAL_REASONS = [
  "account_not_active",
  "already_renewed",
  "period_open",
] as const;
export type RenewalReason = (typeof RENEWAL_REASONS)[number];

/** The kinds a merged GROUP timeline row can carry. */
export const GROUP_TIMELINE_KINDS = [
  "stage",
  "activity",
  "note",
  "policy",
  "endorsement",
  "claim",
  "collection",
] as const;
export type GroupTimelineKind = (typeof GROUP_TIMELINE_KINDS)[number];

/** Structured 422 codes the group / account flows return in `detail.code`. */
export const ACCOUNT_ERROR_CODES = [
  "period_locked",
  "folder_exists",
  "client_not_in_group",
  "client_in_other_group",
  "policies_span_groups",
  "renewal_not_allowed",
] as const;
export type AccountErrorCode = (typeof ACCOUNT_ERROR_CODES)[number];

// --- Groups: read models -----------------------------------------------------

/** The derived primary RUT: the contratante of the latest `period_start`. */
export interface GroupClientRef {
  id: number;
  rut: string;
  legal_name: string;
}

/** One member client, as listed on the group detail. */
export interface AccountGroupClient {
  id: number;
  rut: string;
  legal_name: string;
  trade_name: string | null;
  status: ClientStatus;
}

/** How a group's avatar is rendered (v8). */
export const ACCOUNT_GROUP_ICON_KINDS = ["emoji", "glyph", "image"] as const;
export type AccountGroupIconKind = (typeof ACCOUNT_GROUP_ICON_KINDS)[number];

/**
 * The resolved group avatar as it comes back on read: `value` is the emoji
 * character or the curated glyph name; `url` is a scoped link for an image
 * icon, `null` otherwise.
 */
export interface AccountGroupIcon {
  kind: AccountGroupIconKind;
  value: string | null;
  url: string | null;
}

/**
 * The avatar payload on create/update. An `image` icon references an uploaded
 * `document` by id (rule 8: the S3 key lives only on the document row); an
 * `emoji`/`glyph` icon carries its character or glyph name in `value`.
 */
export interface AccountGroupIconInput {
  kind: AccountGroupIconKind;
  value?: string | null;
  document_id?: number | null;
}

/** List row. Every count is over cases VISIBLE to the caller. */
export interface AccountGroup {
  id: number;
  name: string;
  slug: string;
  status: AccountGroupStatus;
  /** The group avatar; `null` falls back to the styled name initials chip. */
  icon: AccountGroupIcon | null;
  primary_client: GroupClientRef | null;
  clients_count: number;
  accounts_count: number;
  open_count: number;
  latest_period_label: string | null;
  latest_period_start: IsoDate | null;
  updated_at: IsoDateTime | null;
}

export interface AccountGroupDetail extends AccountGroup {
  notes: string | null;
  clients: AccountGroupClient[];
}

/** `GET /account-groups` uses the page envelope (same family as clients). */
export type AccountGroupPage = Page<AccountGroup>;

/** One merged row of the group timeline — newest FIRST (unlike a case timeline). */
export interface GroupTimelineEntry {
  kind: GroupTimelineKind;
  occurred_at: IsoDateTime;
  case_file_id: number | null;
  policy_id: number | null;
  /** Identifier only (policy number, folio, claim number) — never prose. */
  title: string;
  /** Free text the user wrote (note body, stage note). Rendered as-is. */
  detail: string | null;
  /** Enum token behind `detail`; translate it, never print it raw. */
  detail_token: string | null;
  /** Stage rows only. */
  from_stage: string | null;
  to_stage: string | null;
}

/**
 * Cursor page, not offset: pass `next_before` back as `before` to continue.
 * `next_before === null` means the page was not full — you are at the end.
 */
export interface GroupTimelinePage {
  entries: GroupTimelineEntry[];
  next_before: IsoDateTime | null;
}

/** The generated ZIP is a `document(entity_type=account_group)` row (rule 8). */
export interface ArchiveResponse {
  document_id: number;
  download: DocumentDownload;
}

// --- Groups: write models ----------------------------------------------------

export interface AccountGroupCreate {
  name: string;
  notes?: string | null;
  /** Optional group avatar (emoji / curated glyph / uploaded image). */
  icon?: AccountGroupIconInput | null;
  /** Existing clients of the caller's broker to attach on creation. */
  client_ids?: number[] | null;
}

/** Archiving DETACHES its clients and cases (SET NULL) — it never cascades. */
export interface AccountGroupUpdate {
  name?: string | null;
  status?: AccountGroupStatus | null;
  notes?: string | null;
  /** Set/replace the group avatar. Omit to leave it untouched. */
  icon?: AccountGroupIconInput | null;
}

export interface AccountGroupAttachClient {
  client_id: number;
}

/** Whole group when `period_label` is omitted; one vigencia when it is given. */
export interface ArchiveCreate {
  period_label?: string | null;
}

// --- Navigator: GET /navigator (the main rail) -------------------------------

/** The server-owned ANTECEDENTES mapping. The UI never hardcodes it. */
export interface RecordFolder {
  key: RecordFolderKey;
  categories: DocumentCategory[];
}

export interface NavigatorPeriod {
  label: string;
  accounts_count: number;
}

export interface NavigatorGroup {
  id: number;
  name: string;
  slug: string;
  accounts_count: number;
  open_count: number;
  latest_period_label: string | null;
  latest_period_start: IsoDate | null;
  /** Up to 3 periods, newest first. */
  periods: NavigatorPeriod[];
}

export interface NavigatorRecentCase {
  case_file_id: number;
  reference: string | null;
  title: string;
  group_id: number | null;
  period_label: string | null;
  line_name: string | null;
  stage: CaseStage;
  origin: CaseOrigin;
}

export interface NavigatorResponse {
  /** Ordered by `latest_period_start` desc, max 8. */
  groups: NavigatorGroup[];
  /** The 5 most recently updated visible account/renewal cases. */
  recent: NavigatorRecentCase[];
  ungrouped_clients_count: number;
  record_folders: RecordFolder[];
}

// --- Navigator: GET /account-groups/{id}/tree (the contextual rail) ----------

export interface TreeClient {
  id: number;
  rut: string;
  legal_name: string;
}

export interface TreeGroup {
  id: number;
  name: string;
  status: AccountGroupStatus;
  clients: TreeClient[];
}

/** The ramo's account folder. */
export interface TreeAccountNode {
  case_file_id: number;
  reference: string | null;
  kind: CaseFileKind;
  stage: CaseStage;
  status: CaseFileStatus;
  origin: CaseOrigin;
  origin_case_file_id: number | null;
  /** The folder whose `origin_case_file_id` points here (its renewal). */
  renewed_by_case_file_id: number | null;
  period_start: IsoDate | null;
  period_end: IsoDate | null;
  period_locked: boolean;
  /** The assigned line's display name (v7); the card falls back to the ramo. */
  line_name?: string | null;
  client_ids: number[];
  placement_ids: number[];
  documents_count: number;
  /** Keyed by `RecordFolderKey`; EVERY key present, `0` when empty. */
  record_counts: Record<string, number>;
  quotes_count: number;
  proposals_count: number;
  packs_count: number;
  notes_count: number;
}

/** One post-sale child case (endorsement / collection / claim) of a policy. */
export interface TreePostSaleNode {
  case_file_id: number;
  reference: string | null;
  sequence_no: number;
  version: number;
  stage: CaseStage;
  status: CaseFileStatus;
  /** `endorsement.effective_at` for endorsement children; null otherwise. */
  effective_at: IsoDateTime | null;
  /** Shared by the N members of one prórroga; render the batch ONCE. */
  batch_key: string | null;
}

export interface TreePolicyChildren {
  endorsement: TreePostSaleNode[];
  collection: TreePostSaleNode[];
  claim: TreePostSaleNode[];
}

export interface TreePolicyNode {
  id: number;
  policy_number: string;
  insurer_name: string | null;
  status: PolicyStatus;
  start_date: IsoDate | null;
  end_date: IsoDate | null;
  /** Set by a `period_extension` endorsement — caption "prorrogada hasta …". */
  extended_end_date: IsoDate | null;
  children: TreePolicyChildren;
}

/** The RENOVACIÓN leaf: a link when it exists, else whether it may be started. */
export interface TreeRenewal {
  case_file_id: number | null;
  allowed: boolean;
  reason: RenewalReason | null;
}

export interface TreeLineNode {
  insurance_line_id: number;
  name: string;
  requires_inspection: boolean;
  account: TreeAccountNode;
  policies: TreePolicyNode[];
  renewal: TreeRenewal;
}

/** One vigencia. `start`/`end` are the EXTREMES of its accounts' full dates. */
export interface TreePeriodNode {
  label: string;
  start: IsoDate | null;
  end: IsoDate | null;
  is_latest: boolean;
  lines: TreeLineNode[];
}

export interface GroupTree {
  group: TreeGroup;
  /** `period_start` desc; the first one is `is_latest`. */
  periods: TreePeriodNode[];
}

// --- Accounts: renew / reperiod / clients / history --------------------------

/**
 * `POST /case-files/{id}/renew` — invoked on the ACCOUNT folder, never on a
 * policy (rule 4). Opens a `kind=renewal, origin=renewal` sibling in the next
 * vigencia under the same group, with cloned placements.
 *
 * Each `copy_sections` document is copied as a NEW `document` row on a NEW
 * storage key — never two rows on one key.
 */
export interface CaseRenewRequest {
  period_start: IsoDate;
  period_end: IsoDate;
  period_label?: string | null;
  /** Defaults server-side to `["root_prospect"]`. */
  copy_sections?: CaseSection[];
  /** Omitted -> the source's members are copied. */
  client_ids?: number[] | null;
}

/**
 * `POST /case-files/{id}/reperiod` — the ONLY door for a vigencia date change
 * once the folder is `period_locked` (rule 1). Opens a sibling
 * `kind=account, origin=period_change` folder and CLOSES the source.
 */
export interface CaseReperiodRequest {
  period_start: IsoDate;
  period_end: IsoDate;
  reason: string;
}

/** `POST /case-files/{id}/clients` — 422 `client_not_in_group`. */
export interface CaseClientAttachRequest {
  client_id: number;
  role?: AccountClientRole;
}

/** One folder of the origin chain, oldest first: new -> renewal -> … */
export interface OriginChainEntry {
  case_file_id: number;
  reference: string | null;
  period_label: string | null;
  origin: CaseOrigin;
  stage: CaseStage;
}

/** The prior vigencia's claims, without the item tables. */
export interface ClaimSummary {
  id: number;
  policy_id: number | null;
  case_file_id: number | null;
  claim_number: string | null;
  kind: string | null;
  status: ClaimStatus;
  coverage_ruling: ClaimRuling;
  event_date: IsoDate | null;
  reported_date: IsoDate | null;
  estimated_amount_uf: DecimalString | null;
  settled_amount_uf: DecimalString | null;
  paid_amount_uf: DecimalString | null;
  deductible_uf: DecimalString | null;
}

/** Read-only context from the folder this one was renewed / re-perioded from. */
export interface CasePriorContext {
  case_file_id: number;
  /** Keyed by `RecordFolderKey`. */
  records: Record<string, RadalDocument[]>;
  policies: Policy[];
  claims: ClaimSummary[];
  loss_ratio_pct: DecimalString | null;
}

/**
 * `GET /case-files/{id}/history`. `prior` is `null` for `origin=new`
 * ("Sin vigencia anterior") — the renewal screen reads it but is never
 * constrained by it.
 */
export interface CaseHistory {
  case_file_id: number;
  origin_chain: OriginChainEntry[];
  prior: CasePriorContext | null;
}

// --- Prórroga: the batch endorsement (spec §4.3) -----------------------------

/**
 * `POST /endorsements/batch`. The policies are chosen EXPLICITLY (never derived
 * from a period, rule 3) and must all sit under one `account_group_id`, else
 * 422 `policies_span_groups`. Every member carries ZERO premium deltas: a
 * prórroga moves `policy.end_date` only, never the account folder's period.
 */
export interface EndorsementBatchCreate {
  policy_ids: number[];
  /**
   * Narrowed to `BATCHABLE_ENDORSEMENT_KINDS` on purpose: the backend field is
   * typed `EndorsementKind` with a validator, so OpenAPI advertises the whole
   * union while the server 422s on anything else. Defaults to
   * `"period_extension"` server-side.
   */
  kind?: BatchableEndorsementKind;
  new_end_date: IsoDate;
  effective_at: IsoDateTime;
  issued_document_id?: number | null;
  note?: string | null;
}

/** The carrier usually sends ONE document for the whole prórroga. */
export interface EndorsementBatchIssueRequest {
  issued_document_id?: number | null;
  issued_at?: IsoDate | null;
  note?: string | null;
}

export interface EndorsementBatchItem {
  policy_id: number;
  endorsement_id: number;
  case_file_id: number | null;
  /** Echoed by the ISSUE response only; `null` on create. */
  policy_end_date: IsoDate | null;
}

/** The answer to BOTH batch endpoints. */
export interface EndorsementBatchResponse {
  batch_key: string;
  kind: EndorsementKind;
  new_end_date: IsoDate | null;
  items: EndorsementBatchItem[];
}

// =============================================================================
// v6 — Antecedentes expediente (per-ramo record schema + registered instance)
// =============================================================================

/** Field primitives an antecedentes schema field may declare (spec v6 §A). */
export type RamoFieldType =
  | "text"
  | "number"
  | "integer"
  | "boolean"
  | "date"
  | "money_uf"
  | "percent"
  | "select"
  | "list"
  | "group";

/**
 * One field in a ramo schema. `list`/`group` (and any field flagged
 * `repeatable`) carry nested `fields`; `select` carries `options`. `key` is an
 * English identifier (rule 1); `label`/`description`/`options` are
 * broker-authored data and may be Spanish.
 */
export interface RamoField {
  key: string;
  label: string;
  type: RamoFieldType;
  required?: boolean;
  description?: string | null;
  options?: string[] | null;
  unit?: string | null;
  repeatable?: boolean;
  /**
   * A `list` field flagged `totals` renders as a matrix with a summed "Total"
   * row over its `money_uf` columns (the ubicaciones×materias table). The PDF
   * engine owns the sum; the frontend only authors the flag.
   */
  totals?: boolean;
  fields?: RamoField[] | null;
}

export interface RamoSection {
  key: string;
  label: string;
  description?: string | null;
  fields: RamoField[];
}

/** The `definition` JSON of a `line_record_schema` (spec v6 §A). */
export interface RamoSchema {
  sections: RamoSection[];
}

export type RecordExpedienteStatus = "draft" | "processing" | "review" | "registered";

/**
 * How many accounts / groups an existing line drives — carried ONLY on the list
 * endpoint (`GET /line-record-schemas`). Zero everywhere else. Gates delete.
 */
export interface LineRecordSchemaUsage {
  accounts: number;
  groups: number;
}

/**
 * One advisory antecedentes document a ramo recommends (v8). Purely advisory: it
 * drives the uploader's category picker and per-file explanations; it never
 * gates consolidation or the PDF. `category` is a `DocumentCategory` value.
 */
export interface RamoRecommendedFile {
  /** Stable English identifier for the recommended slot. */
  key?: string | null;
  category?: DocumentCategory | string | null;
  label?: string | null;
  /** Free label for the document type when no `DocumentCategory` fits. */
  doc_type?: string | null;
  /** Human context shown under the slot. */
  description?: string | null;
  /** Expected upload format — `"pdf" | "word" | "pdf/word" | "image"`. */
  format?: "pdf" | "word" | "pdf/word" | "image" | string | null;
  explanation?: string | null;
  required?: boolean | null;
}

/**
 * A stored line (`line_record_schema` row): the ramo plus the antecedentes it
 * requires (v7 §A). A **template** is global (`broker_id === null`) with
 * `is_template === true` — a recommended shape a broker adopts and then freely
 * edits; a **broker line** carries a `broker_id`. `insurance_line_id` is the
 * ramo *category* — a broker may keep several lines per ramo.
 */
export interface LineRecordSchema {
  id: number;
  /** `null` = a global Radal template; otherwise the owning broker. */
  broker_id: number | null;
  insurance_line_id: number;
  insurance_line_name?: string | null;
  name: string;
  version: number;
  is_active: boolean;
  /** A global recommended template (`broker_id === null && is_template`). */
  is_template: boolean;
  /** Convenience mirror of `broker_id === null`. */
  is_global?: boolean;
  /** Nonzero only on the list endpoint; drives the delete-in-use gate. */
  usage?: LineRecordSchemaUsage;
  definition: RamoSchema;
  /**
   * Advisory (v8): the antecedentes documents this ramo recommends and a
   * ramo-level prose blurb. Neither gates anything — they guide the uploader.
   */
  recommended_files?: RamoRecommendedFile[];
  explanation?: string | null;
  created_by_id: number | null;
  created_at: IsoDateTime | null;
  updated_at: IsoDateTime | null;
}

/**
 * `POST /line-record-schemas` — create a broker line. `insurance_line_id` and
 * `definition` are required UNLESS cloning a template (`from_template_id`), in
 * which case the server copies the template's ramo + definition.
 */
export interface LineRecordSchemaCreate {
  name: string;
  insurance_line_id?: number | null;
  definition?: RamoSchema;
  /** The recommended antecedentes documents — the first-class content (v9). */
  recommended_files?: RamoRecommendedFile[];
  explanation?: string | null;
  version?: number;
  is_active?: boolean;
  /** Clone this global template's definition into the new broker line. */
  from_template_id?: number;
}

/** `PUT /line-record-schemas/{id}` — patch a broker line (never the ramo). */
export interface LineRecordSchemaUpdate {
  name?: string;
  definition?: RamoSchema;
  version?: number;
  is_active?: boolean;
}

/** Back-compat alias — the create body (v6 name). */
export type LineRecordSchemaWrite = LineRecordSchemaCreate;

/**
 * `POST /case-files/{id}/line` — assign a line to an account. Broker-scoped;
 * 404 on a foreign account/line, 422 when the line's ramo != the account's.
 */
export interface LineAssignmentRequest {
  line_record_schema_id: number;
}

/** One mandatory field the resolved line requests (v7 §B). */
export interface RamoRequiredField {
  section: string;
  field: string;
  label: string;
}

/**
 * The registered / in-progress antecedentes expediente for one account, as
 * returned by `GET /case-files/{id}/antecedentes` (spec v6 §B). `payload` is
 * sectioned: `{ [sectionKey]: { [fieldKey]: value } }`, where a repeatable
 * field holds an array of row objects and a `group` field a nested object.
 */
export interface AntecedentesExpediente {
  case_file_id: number;
  status: RecordExpedienteStatus;
  insurance_line_id: number | null;
  schema: RamoSchema | null;
  schema_id: number | null;
  schema_version: number | null;
  /** The assigned line's display name (v7). Falls back to the ramo in the UI. */
  line_name: string | null;
  payload: Record<string, unknown> | null;
  confidence: DecimalString | number | null;
  extraction_id: number | null;
  /** The full persisted AI job (audit trail), embedded so the review UI can
   * render the provenance card. `null` until a SUGGEST has staged one. */
  extraction: Extraction | null;
  registered_at: IsoDateTime | null;
  registered_by_id: number | null;
  /** True once a branded PDF Document has been generated (rule 8). */
  pdf_available: boolean;
  /** The line's full mandatory checklist — drives the requisitos panel (v7). */
  required_fields: RamoRequiredField[];
  /** The still-empty subset of `required_fields`. */
  missing_required: RamoRequiredField[];
  /** No mandatory field missing — gates the Descargar Bases Técnicas button. */
  complete: boolean;
  /** Per-document AI reads behind the consolidated payload (v9). */
  document_extractions: AntecedentesDocumentExtraction[];
  warnings: string[];
}

/**
 * One document's AI extraction, as carried by `GET /case-files/{id}/
 * antecedentes` (v9). `payload` is the raw per-document read; `needs_vision`
 * flags a scanned/image document the text pass could not fully read.
 */
export interface AntecedentesDocumentExtraction {
  document_id: number;
  document_name: string;
  category: string;
  section: string | null;
  payload: Record<string, unknown> | null;
  confidence: DecimalString | number | null;
  needs_vision: boolean;
  warnings: string[];
}

/** `POST /case-files/{id}/antecedentes/process` — the suggest step (rule 6). */
export interface AntecedentesSuggestion extends AntecedentesExpediente {
  /** After a SUGGEST the extraction row is always present (one per attempt). */
  extraction: Extraction;
}

/** `POST /case-files/{id}/antecedentes/register` — the human-completed payload. */
export interface AntecedentesRegisterRequest {
  payload: Record<string, unknown>;
}

// --- Aliases matching the backend Pydantic class names -----------------------
// A3 froze these names; keeping the aliases means a page written against the
// schema module compiles without a rename pass.

export type AccountGroupRead = AccountGroup;
export type AccountGroupClientRead = AccountGroupClient;
export type AttachClient = AccountGroupAttachClient;
export type CaseRenewBody = CaseRenewRequest;
export type CaseReperiodBody = CaseReperiodRequest;
export type CaseClientAttach = CaseClientAttachRequest;
export type CaseHistoryResponse = CaseHistory;
export type EndorsementBatchIssue = EndorsementBatchIssueRequest;
