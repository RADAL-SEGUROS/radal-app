/**
 * Policies — the post-sale root.
 *
 * The policy is the mirror of the accepted issuance proposal: everything that
 * happens after the award (endorsements, collection, warranties, claims) hangs
 * off this row. Two rules the UI must respect:
 *
 *  - the contractual period is a DATETIME (`period_start_at` / `period_end_at`,
 *    the 12:00 convention). `start_date` / `end_date` are the legacy dates the
 *    server keeps in sync — never render them instead of the datetimes;
 *  - money follows the house arithmetic: `net = taxable + exempt`,
 *    `vat = 0.19 * taxable` (NOT on net), `total = net + vat`.
 *
 * Query keys and types are published by `@/api/keys` and `@/api/types` — this
 * module never invents one.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import api from "@/lib/api";
import { clean, qk } from "@/api/keys";
import type {
  MirrorDiff,
  MirrorDiffQueueRequest,
  MirrorDiffQueueResult,
  Policy,
  PolicyCreate,
  PolicyFromProposalRequest,
  PolicySummary,
  PolicyUpdate,
  PolicyUploadRequest,
  PolicyUploadResponse,
  Warranty,
  WarrantyCreate,
  SummaryParams,
  ScopeParams,
} from "@/api/types";

// =============================================================================
// v12 — policies are the main view (02 §2). Types live HERE (lane F1), not in
// `api/types.ts`: the v12 row is the legacy `Policy` plus the computed fields
// the router fills in (status, ramo/subtipo, cobranza line, "Falta: X").
// =============================================================================

export const POLICY_COMPUTED_STATUSES = ["upcoming", "active", "expired", "cancelled"] as const;
export type PolicyComputedStatus = (typeof POLICY_COMPUTED_STATUSES)[number];
/** What `?status=` accepts: the computed four plus the stored-only two. */
export type PolicyStatusFilter = PolicyComputedStatus | "draft" | "renewed";

export const POLICY_COLLECTION_FILTERS = ["overdue", "due_soon", "no_plan", "ok"] as const;
export type PolicyCollectionFilter = (typeof POLICY_COLLECTION_FILTERS)[number];

/** `policy_registry.MISSING_KEYS`, in the server's display order. */
export const POLICY_MISSING_KEYS = [
  "source_document",
  "policy_number",
  "plan",
  "gross_premium",
  "payer_contact",
  "premiums",
  "commission",
] as const;
export type PolicyMissingKey = (typeof POLICY_MISSING_KEYS)[number];

/** `policy_registry._FEATURES` — the operability rows of the ficha. */
export const POLICY_FEATURES = [
  "collections",
  "payer_notices",
  "endorsements",
  "claims",
  "premium_reports",
  "commission",
  "source_document",
] as const;
export type PolicyFeature = (typeof POLICY_FEATURES)[number];

export const PARTY_ROLES = [
  "insured",
  "policyholder",
  "payer",
  "beneficiary",
  "additional_insured",
] as const;
export type PartyRole = (typeof PARTY_ROLES)[number];

export const PAYMENT_METHODS = [
  "coupon_book",
  "direct_debit",
  "card_debit",
  "transfer",
  "single_charge",
  "other",
] as const;
export type PaymentMethod = (typeof PAYMENT_METHODS)[number];

export type PolicyOrigin = "reader" | "manual" | "journey" | "portal_rpa" | "migration";
export type PolicyCurrency = "UF" | "USD";
export type CollectionLevelValue = "native" | "assisted" | "record";

export interface CatalogRef {
  id: number;
  code: string | null;
  name: string;
}

/** The cobranza line of a policy row: next cuota, arrears and level. */
export interface PolicyCollectionSummary {
  plan_id: number | null;
  state: "no_plan" | "overdue" | "due_soon" | "ok" | string;
  next_due_date: string | null;
  next_due_amount_uf: string | null;
  overdue_count: number;
  overdue_uf: string;
  level: CollectionLevelValue | null;
  level_reason: string | null;
  installments_count: number;
  paid_count: number;
}

export interface PolicyOperability {
  feature: PolicyFeature | string;
  enabled: boolean;
  /** Spanish, from the server. */
  reason: string | null;
  missing: string[];
}

export interface PolicyJourney {
  case_file_id: number | null;
  group_id: number | null;
  stage: string | null;
  entered_stages: string[];
  skipped_stages: string[];
}

export interface PolicyParty {
  id: number;
  role: PartyRole;
  client_id: number | null;
  name: string;
  rut: string | null;
  email: string | null;
  phone: string | null;
  beneficiary_kind: string | null;
  share_pct: string | null;
  is_primary: boolean;
  notify_collections: boolean;
}

/** `PolicyRead` as v12 serves it on every list row. */
export interface PolicyRow extends Policy {
  insurance_subtype_id: number | null;
  origin: PolicyOrigin | null;
  has_source_document: boolean;
  is_provisional: boolean;
  currency: PolicyCurrency;
  payment_method: PaymentMethod | null;
  terminated_on: string | null;
  termination_reason: string | null;
  computed_status: PolicyComputedStatus | null;
  subtype: CatalogRef | null;
  line: CatalogRef | null;
  insured_name: string | null;
  insured_rut: string | null;
  group_id: number | null;
  has_policy_number: boolean;
  collection_summary: PolicyCollectionSummary | null;
  missing: string[];
}

/** `GET /policies/{id}` — the ficha: row + parties, operability, journey. */
export interface PolicyDetail extends PolicyRow {
  parties: PolicyParty[];
  operability: PolicyOperability[];
  journey: PolicyJourney | null;
  /** Non-blocking advice from the last write (e.g. possible duplicate). */
  warnings: string[];
}

/** `GET /policies/summary` with the v12 cards. */
export interface PolicySummaryV12 extends PolicySummary {
  active_premium_uf: string;
  expiring_90: number;
  no_plan: number;
  overdue_installments: number;
  overdue_uf: string;
  without_source_document: number;
  provisional: number;
}

export interface PartyContactInput {
  name: string;
  rut?: string | null;
  email?: string | null;
  phone?: string | null;
}

/** `POST /policies`, v12 manual path (vía Manual). */
export interface ManualPolicyCreate {
  insurer_id: number;
  insurance_subtype_id: number;
  client_id?: number | null;
  insured_rut?: string | null;
  insured_name?: string | null;
  start_date: string;
  end_date: string;
  policy_number?: string | null;
  taxable_premium_uf?: string | null;
  exempt_premium_uf?: string | null;
  total_premium_uf?: string | null;
  commission_pct?: string | null;
  currency?: PolicyCurrency;
  is_provisional?: boolean;
  payment_method?: PaymentMethod | null;
  policyholder?: PartyContactInput | null;
  payer?: PartyContactInput | null;
  notes?: string | null;
}

export interface PartyInput extends PartyContactInput {
  role: PartyRole;
  client_id?: number | null;
  beneficiary_kind?: string | null;
  share_pct?: string | null;
  is_primary?: boolean;
  notify_collections?: boolean;
}

/** `POST /policies/upload` — v12 adds the no-folder path (2b.5). */
export interface PolicyUploadRequestV12 extends PolicyUploadRequest {
  client_id?: number | null;
  insurance_subtype_id?: number | null;
  create_client?: boolean;
}

/** The 422 body when the insured RUT read from the PDF is not in the portfolio. */
export interface PolicyInsuredUnresolved {
  code: "insured_unresolved";
  reason?: string;
  suggestion?: { rut: string | null; legal_name: string | null } | null;
  extraction_id?: number;
}

/** `GET /policies` — the server sends an offset page. */
export interface PolicyPage {
  items: PolicyRow[];
  total: number;
  limit: number;
  offset: number;
}

export interface PolicyListParams extends ScopeParams {
  client_id?: number;
  insurer_id?: number;
  placement_id?: number;
  /**
   * Repeatable (`lib/api.ts` repeats the bare key). v12: the COMPUTED statuses
   * (`upcoming | active | expired | cancelled`) plus the stored-only
   * `draft | renewed`; repeated values OR together server-side.
   */
  status?: PolicyStatusFilter | PolicyStatusFilter[];
  /** Policy number, insured name or RUT. */
  q?: string;
  /** Ramo code of the closed catalogue (e.g. `VEH`). */
  line_code?: string;
  subtype_id?: number;
  collection?: PolicyCollectionFilter[];
  missing?: PolicyMissingKey[];
  /** "Mías": policies whose account folder the caller owns. */
  owner?: "me";
  limit?: number;
  offset?: number;
}

export function usePolicies(params: PolicyListParams = {}, enabled = true) {
  return useQuery({
    queryKey: qk.policies.list(params),
    enabled,
    queryFn: async () => {
      const { data } = await api.get<PolicyPage>("/policies", {
        params: clean(params),
      });
      return data;
    },
  });
}

/** `GET /policies/summary` — broker-scoped counts for the analytics dashboard.
 *  Gate on `Policies.View` via `enabled`; the server 403s without it. */
export function usePoliciesSummary(
  enabled = true,
  /**
   * Scope: `account_group_id` / `case_file_id` / `date_from` / `date_to`, and
   * `group_by` for the bucket shape the charts consume. The params are part of
   * the cache key, so two scopes never share a cached answer.
   */
  params: SummaryParams = {},
) {
  return useQuery({
    queryKey: qk.policies.summary(params),
    enabled,
    queryFn: async () => {
      const { data } = await api.get<PolicySummaryV12>("/policies/summary", {
        params: clean(params),
      });
      return data;
    },
  });
}

export function usePolicy(policyId: number | undefined) {
  return useQuery({
    queryKey: qk.policies.detail(policyId ?? 0),
    enabled: !!policyId,
    queryFn: async () => {
      const { data } = await api.get<PolicyDetail>(`/policies/${policyId}`);
      return data;
    },
  });
}

export function useCreatePolicy() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload: PolicyCreate) => {
      const { data } = await api.post<Policy>("/policies", payload);
      return data;
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.policies.all });
      void qc.invalidateQueries({ queryKey: qk.caseFiles.all });
    },
  });
}

/**
 * `POST /policies` on the v12 manual path ("Registrar a mano"): five mandatory
 * data, the colocación found or created on demand, `origin=manual`. A taken
 * policy number is a 409 with a Spanish `detail`.
 */
export function useCreateManualPolicy() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload: ManualPolicyCreate) => {
      const { data } = await api.post<PolicyDetail>("/policies", payload);
      return data;
    },
    onSuccess: (data) => {
      qc.setQueryData(qk.policies.detail(data.id), data);
      void qc.invalidateQueries({ queryKey: qk.policies.all });
      void qc.invalidateQueries({ queryKey: qk.caseFiles.all });
      void qc.invalidateQueries({ queryKey: qk.accountGroups.all });
      void qc.invalidateQueries({ queryKey: qk.navigator.all });
      void qc.invalidateQueries({ queryKey: qk.clients.all });
      void qc.invalidateQueries({ queryKey: qk.collections.all });
    },
  });
}

/**
 * `PUT /policies/{id}/parties` — replace who is who wholesale: exactly one
 * policyholder, at least one insured, at most one payer (Spanish 422 else).
 */
export function useReplacePolicyParties(policyId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (parties: PartyInput[]) => {
      const { data } = await api.put<PolicyDetail>(`/policies/${policyId}/parties`, {
        parties,
      });
      return data;
    },
    onSuccess: (data) => {
      qc.setQueryData(qk.policies.detail(policyId), data);
      void qc.invalidateQueries({ queryKey: qk.policies.lists() });
      // The payer's contact feeds the cobranza notices and `missing`.
      void qc.invalidateQueries({ queryKey: qk.collections.all });
    },
  });
}

export interface PolicyPlanInstallmentInput {
  number: number;
  due_date: string;
  gross_amount_uf: string;
}

export interface PolicyPlanCreate {
  policy_id: number;
  payment_mode?: PaymentMethod;
  installment_count?: number;
  installments: PolicyPlanInstallmentInput[];
  source?: "manual" | "reader";
  origin?: "policy" | "endorsement" | "extension";
  /** v12 review #15: start the cobranza (payer reminders) in the same write. */
  start_collection?: boolean;
}

/** What the policy ficha reads back from `POST /collections/plans`. */
export interface PolicyPlanCreated {
  id: number;
  /** Non-blocking advice (Spanish), e.g. cuotas already past due. */
  warnings?: string[];
}

/**
 * `POST /collections/plans` — "Cargar plan de pago" from the policy's "Falta"
 * block. The server checks Σ cuotas = prima bruta (422 with the difference).
 * Only the id of the created plan is read back, so no cobranza type is needed.
 */
export function useCreatePolicyPlan(policyId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload: PolicyPlanCreate) => {
      const { data } = await api.post<PolicyPlanCreated>("/collections/plans", payload);
      return data;
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.policies.detail(policyId) });
      void qc.invalidateQueries({ queryKey: qk.policies.lists() });
      void qc.invalidateQueries({ queryKey: qk.policies.summary() });
      void qc.invalidateQueries({ queryKey: qk.collections.all });
    },
  });
}

/**
 * `POST /policies/upload` — validate-then-dynamic ingestion.
 *
 * The PDF must be filed through `POST /documents` first; this passes its id. On
 * a core-validation failure the server answers **HTTP 422** with a
 * `{ code: "not_a_policy", reason, missing, detail }` body — surfaced to the
 * caller so the dialog can render the reject state and offer `override: true`
 * (the broker's confirmed "register it anyway"). Success invalidates the lists.
 */
export function useUploadPolicy() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload: PolicyUploadRequestV12) => {
      const { data } = await api.post<PolicyUploadResponse>("/policies/upload", payload);
      return data;
    },
    onSuccess: (data) => {
      qc.setQueryData(qk.policies.detail(data.policy.id), data.policy);
      void qc.invalidateQueries({ queryKey: qk.policies.all });
      void qc.invalidateQueries({ queryKey: qk.caseFiles.all });
      void qc.invalidateQueries({ queryKey: qk.accountGroups.all });
      void qc.invalidateQueries({ queryKey: qk.navigator.all });
      void qc.invalidateQueries({ queryKey: qk.documents.all });
    },
  });
}

/**
 * The mirror baseline: a draft policy built from the accepted proposal, so the
 * carrier's issued document can be diffed against what was actually agreed.
 */
export function useCreatePolicyFromProposal() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload: PolicyFromProposalRequest) => {
      const { data } = await api.post<Policy>("/policies/from-proposal", payload);
      return data;
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.policies.all });
      void qc.invalidateQueries({ queryKey: qk.proposals.all });
      void qc.invalidateQueries({ queryKey: qk.caseFiles.all });
    },
  });
}

export function useUpdatePolicy(policyId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload: PolicyUpdateV12) => {
      const { data } = await api.patch<PolicyDetail>(`/policies/${policyId}`, payload);
      return data;
    },
    onSuccess: (data) => {
      qc.setQueryData(qk.policies.detail(policyId), data);
      void qc.invalidateQueries({ queryKey: qk.policies.lists() });
      // The period and the premium feed the collection ledger's expected total.
      void qc.invalidateQueries({ queryKey: qk.collections.all });
    },
  });
}

/** "Completar" / "Corregir dato" (R19): a changed value that already existed
 *  needs `correction_reason`, else 422 `correction_reason_required`. */
export type PolicyUpdateV12 = PolicyUpdate & {
  insurance_subtype_id?: number | null;
  is_provisional?: boolean | null;
  currency?: PolicyCurrency | null;
  payment_method?: PaymentMethod | null;
  correction_reason?: string | null;
};

export function useDeletePolicy() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (policyId: number) => {
      await api.delete(`/policies/${policyId}`);
      return policyId;
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.policies.all });
    },
  });
}

// --- Mirror-diff ---------------------------------------------------------------

/**
 * Field-by-field diff of the confirmed issuance proposal against the confirmed
 * policy. Not an LLM feature — `services/mirror.py` aligns coverages by number,
 * deductibles by peril key and money on the five premium fields.
 */
export function useMirrorDiff(policyId: number | undefined, enabled = true) {
  return useQuery({
    queryKey: qk.policies.mirrorDiff(policyId ?? 0),
    enabled: !!policyId && enabled,
    queryFn: async () => {
      const { data } = await api.get<MirrorDiff>(`/policies/${policyId}/mirror-diff`);
      return data;
    },
  });
}

/** Turn selected diff rows into `endorsement(status=draft)` — human confirms later. */
export function useQueueMirrorDiff(policyId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload: MirrorDiffQueueRequest = {}) => {
      const { data } = await api.post<MirrorDiffQueueResult>(
        `/policies/${policyId}/mirror-diff/queue`,
        payload,
      );
      return data;
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.policies.mirrorDiff(policyId) });
      void qc.invalidateQueries({ queryKey: qk.endorsements.all });
      void qc.invalidateQueries({ queryKey: qk.policies.detail(policyId) });
    },
  });
}

// --- Warranties (R-n / G-n / M-n) ----------------------------------------------

export function usePolicyWarranties(policyId: number | undefined) {
  return useQuery({
    queryKey: qk.policies.warranties(policyId ?? 0),
    enabled: !!policyId,
    queryFn: async () => {
      const { data } = await api.get<Warranty[]>(`/policies/${policyId}/warranties`);
      return data;
    },
  });
}

export function useCreateWarranty(policyId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload: WarrantyCreate) => {
      const { data } = await api.post<Warranty>(
        `/policies/${policyId}/warranties`,
        payload,
      );
      return data;
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.policies.warranties(policyId) });
      void qc.invalidateQueries({ queryKey: qk.policies.detail(policyId) });
      void qc.invalidateQueries({ queryKey: qk.warranties.all });
    },
  });
}
