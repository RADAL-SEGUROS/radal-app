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
  "other",
] as const;
export type DocumentCategory = (typeof DOCUMENT_CATEGORIES)[number];

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
}

export interface OfferingCreate {
  quote_request_id: number;
  selected_proposal_id?: number | null;
  expires_at?: IsoDateTime | null;
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
