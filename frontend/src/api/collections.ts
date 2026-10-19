/**
 * Collections — the payment plan and its instalment ledger.
 *
 * The invariant the corpus shows in every plan:
 *
 *     SUM(installment.gross_amount_uf) == policy gross premium
 *                                       + SUM(endorsement.total_premium_delta_uf)
 *
 * with the last instalment absorbing the rounding. The server validates it
 * BEFORE writing anything and answers 422 with expected/received/difference —
 * never a silent fix. `GET /collections/{id}/status` returns the derived view
 * (outstanding, overdue, compliance %, alerts and the `balances` flag).
 *
 * Art. 528: non-payment suspends cover. `terminated_at`, `rehabilitated_at`,
 * `days_without_cover` and `art528_events` on the plan are that sequence.
 */
import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import api from "@/lib/api";
import { clean, qk } from "@/api/keys";
import type {
  CollectionInstallment,
  CollectionInstallmentCreate,
  CollectionInstallmentUpdate,
  CollectionPlan,
  CollectionPlanCreate,
  CollectionPlanStatus,
  CollectionPlanUpdate,
  CollectionStatus,
  DecimalString,
  IsoDate,
  IsoDateTime,
  PaymentMode,
  ScopeParams,
} from "@/api/types";

export interface CollectionPlanPage {
  items: CollectionPlan[];
  total: number;
  limit: number;
  offset: number;
}

export interface CollectionPlanListParams extends ScopeParams {
  policy_id?: number;
  /** Single value — see the note in `api/policies.ts`. */
  status?: CollectionPlanStatus;
  limit?: number;
  offset?: number;
}

export function useCollectionPlans(
  params: CollectionPlanListParams = {},
  enabled = true,
) {
  return useQuery({
    queryKey: qk.collections.list(params),
    enabled,
    queryFn: async () => {
      const { data } = await api.get<CollectionPlanPage>("/collections", {
        params: clean(params),
      });
      return data;
    },
  });
}

export function useCollectionPlan(planId: number | undefined) {
  return useQuery({
    queryKey: qk.collections.detail(planId ?? 0),
    enabled: !!planId,
    queryFn: async () => {
      const { data } = await api.get<CollectionPlan>(`/collections/${planId}`);
      return data;
    },
  });
}

export function useCreateCollectionPlan() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload: CollectionPlanCreate) => {
      const { data } = await api.post<CollectionPlan>("/collections", payload);
      return data;
    },
    onSuccess: (data) => {
      void qc.invalidateQueries({ queryKey: qk.collections.all });
      void qc.invalidateQueries({ queryKey: qk.policies.detail(data.policy_id) });
    },
  });
}

export function useUpdateCollectionPlan(planId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload: CollectionPlanUpdate) => {
      const { data } = await api.patch<CollectionPlan>(`/collections/${planId}`, payload);
      return data;
    },
    onSuccess: (data) => {
      qc.setQueryData(qk.collections.detail(planId), data);
      invalidateLegacyLedger(qc);
    },
  });
}

/**
 * The v1 ledger writes (plan PATCH, ledger PUT, "Marcar pagada") turn into real
 * v12 payments server-side, so they must refresh the same scopes the v12
 * writes do: the board rows, per-cuota payments, notices, the summary (the
 * rail chip), the policy's cobranza block and the cuota's bell alerts.
 */
function invalidateLegacyLedger(qc: ReturnType<typeof useQueryClient>) {
  void qc.invalidateQueries({ queryKey: qk.collections.all });
  void qc.invalidateQueries({ queryKey: qk.policies.all });
  void qc.invalidateQueries({ queryKey: qk.notifications.all });
}

export function useInstallments(planId: number | undefined) {
  return useQuery({
    queryKey: qk.collections.installments(planId ?? 0),
    enabled: !!planId,
    queryFn: async () => {
      const { data } = await api.get<CollectionInstallment[]>(
        `/collections/${planId}/installments`,
      );
      return data;
    },
  });
}

/** Full replace of the ledger. The Σ is validated before a single row is written. */
export function useReplaceInstallments(planId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (installments: CollectionInstallmentCreate[]) => {
      const { data } = await api.put<CollectionPlan>(
        `/collections/${planId}/installments`,
        { installments },
      );
      return data;
    },
    onSuccess: (data) => {
      qc.setQueryData(qk.collections.detail(planId), data);
      invalidateLegacyLedger(qc);
    },
  });
}

/** Mark one instalment paid / late / credited. `number` is the ordinal, not the id. */
export function useUpdateInstallment(planId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({
      number,
      ...payload
    }: CollectionInstallmentUpdate & { number: number }) => {
      const { data } = await api.patch<CollectionInstallment>(
        `/collections/${planId}/installments/${number}`,
        payload,
      );
      return data;
    },
    onSuccess: () => invalidateLegacyLedger(qc),
  });
}

/** The derived dashboard: outstanding, overdue, compliance %, alerts, `balances`. */
export function useCollectionStatus(planId: number | undefined) {
  return useQuery({
    queryKey: qk.collections.status(planId ?? 0),
    enabled: !!planId,
    queryFn: async () => {
      const { data } = await api.get<CollectionStatus>(`/collections/${planId}/status`);
      return data;
    },
  });
}

// =============================================================================
// v12 — the Cobranza board (cuota-level), payments, gestiones, notices, evidence
// =============================================================================
//
// Shapes mirror `backend/app/schemas/collection_board.py` and
// `backend/app/schemas/inquiries.py` exactly. Type names are prefixed so the
// `export *` barrel in `api/index.ts` never collides with another module.
//
// Every write invalidates `qk.collections.all` — the board rows, the summary
// cards (and therefore the rail's overdue chip), the plan detail and the
// evidence inbox all hang off that root — plus the policy and notification
// scopes, because a payment also closes the cuota's bell alerts and changes
// the policy's cobranza block.

/**
 * `review` (review r2 D1): past cuotas of a policy loaded late, waiting for
 * the broker's paid / unpaid answer — never counted as overdue.
 */
export type CollectionBucket = "upcoming" | "overdue" | "current" | "settled" | "review";
export const COLLECTION_BUCKETS: readonly CollectionBucket[] = [
  "upcoming",
  "overdue",
  "current",
  "settled",
  "review",
] as const;

export type CobranzaLevel = "record" | "assisted" | "native";
export const COLLECTION_LEVELS: readonly CobranzaLevel[] = [
  "native",
  "assisted",
  "record",
] as const;

/** The derived cuota state (`services/collection/status.py::StateName`). */
export type InstallmentStateName =
  | "voided"
  | "paid"
  | "pending_review"
  | "promised"
  | "termination_risk"
  | "overdue"
  | "due_today"
  | "upcoming";

/** Flags that never change the state: partial, rejected PAC, unconfirmed, no receipt. */
export type InstallmentFlag = "partial" | "direct_debit_rejected" | "unconfirmed" | "no_receipt";

export type PaymentCurrencyCode = "UF" | "CLP" | "USD";
export type PaymentEvidenceLevel =
  | "declared"
  | "reported"
  | "insurer_confirmed"
  | "provider_confirmed"
  | "settled";
export type PaymentRecordSource =
  | "manual"
  | "insurer_statement"
  | "insurer_reply"
  | "insurer_share_link"
  | "provider"
  | "payer_upload"
  | "agent";
export type PaymentReversalReason = "error" | "direct_debit_rejected" | "chargeback" | "other";

export interface BoardLastNote {
  id: number;
  body: string;
  created_at: IsoDateTime | null;
  follow_up_on: IsoDate | null;
  author_id: number | null;
}

/** One cuota on the Cobranza board (live state, not the cache). */
export interface BoardInstallmentRow {
  id: number;
  plan_id: number;
  policy_id: number;
  case_file_id: number | null;
  number: number;
  installment_count: number | null;
  coupon_number: string | null;
  due_date: IsoDate | null;
  currency: "UF" | "USD";
  gross_amount_uf: DecimalString;
  paid_uf: DecimalString;
  balance_uf: DecimalString;
  /** CLP of the open balance at today's UF (`null` without a rate / USD cuota). */
  balance_clp: number | null;
  state: InstallmentStateName | string;
  flags: string[];
  buckets: string[];
  days_late: number;
  evidence: PaymentEvidenceLevel | string | null;
  paid_on: IsoDate | null;
  status: string;
  level: CobranzaLevel;
  /** Spanish, from the server — the tooltip of the level badge. */
  level_reason: string;
  level_missing: string[];
  channels: string[];
  promise_until: IsoDate | null;
  direct_debit_rejected_at: IsoDate | null;
  last_note: BoardLastNote | null;
  policy_number: string;
  insured_name: string | null;
  insured_rut: string | null;
  insurer_id: number | null;
  insurer_name: string | null;
  line_code: string | null;
  line_name: string | null;
  payment_mode: PaymentMode | null;
  notices_enabled: boolean;
  /**
   * Which automatic charge the cuota rides on — PAC (bank account) or PAT
   * (card). Optional: older servers omit it and the UI falls back to
   * `payment_mode` (`direct_debit` = PAC, `card_debit` = PAT).
   */
  direct_debit_kind?: "pac" | "pat" | null;
}

export interface BoardInstallmentPage {
  items: BoardInstallmentRow[];
  total: number;
  limit: number;
  offset: number;
  as_of: IsoDate;
  uf_rate: DecimalString | null;
  /** Size of every bucket over the other filters (subtab badges). */
  counts: Partial<Record<CollectionBucket, number>>;
}

export interface BoardInstallmentParams {
  bucket?: CollectionBucket;
  insurer_id?: number;
  line_code?: string;
  method?: PaymentMode;
  level?: CobranzaLevel;
  owner?: "me";
  q?: string;
  policy_id?: number;
  plan_id?: number;
  limit?: number;
  offset?: number;
}

export interface CollectionMoneyCount {
  count: number;
  uf: DecimalString;
}

/** `GET /collections/summary` — the generic summary + the v12 cards (broker-wide). */
export interface CollectionsBoardSummary {
  entity: string;
  total: number;
  by_status?: Record<string, number>;
  total_uf?: DecimalString | null;
  as_of: IsoDate;
  due_7d: CollectionMoneyCount;
  due_30d: CollectionMoneyCount;
  overdue: CollectionMoneyCount;
  termination_risk: CollectionMoneyCount;
  collected_month_uf: DecimalString;
  unconfirmed_payments: number;
  pending_evidence: number;
  plans_missing: number;
  /** Review r2 D1: past cuotas awaiting the paid / unpaid answer (never overdue). */
  past_installments_pending?: CollectionMoneyCount;
  /** Review r2 B5: `mine` when the cards were narrowed with `owner=me`. */
  owner_scope?: "all" | "mine";
}

export interface InstallmentModeRead {
  level: CobranzaLevel;
  reason: string;
  channels: string[];
  missing: string[];
}

export interface InstallmentPaymentRead {
  id: number;
  installment_id: number;
  amount: DecimalString;
  currency: PaymentCurrencyCode;
  uf_rate: DecimalString | null;
  amount_uf: DecimalString;
  paid_on: IsoDate;
  method: PaymentMode | null;
  source: PaymentRecordSource;
  evidence_level: PaymentEvidenceLevel;
  status: "active" | "reversed";
  reversal_reason: PaymentReversalReason | null;
  reversal_note: string | null;
  reversed_at: IsoDateTime | null;
  confirmed_by_insurer: boolean;
  confirmed_at: IsoDateTime | null;
  receipt_document_id: number | null;
  registered_by_user_id: number | null;
  note: string | null;
  created_at: IsoDateTime | null;
}

export interface InstallmentPaymentCreate {
  amount: string;
  currency: PaymentCurrencyCode;
  paid_on: IsoDate;
  method?: PaymentMode | null;
  receipt_document_id?: number | null;
  confirmed_by_insurer?: boolean;
  note?: string | null;
}

export interface InstallmentStateRead {
  state: string;
  flags: string[];
  paid_uf: DecimalString;
  balance_uf: DecimalString;
  days_late: number;
  evidence: string | null;
  paid_on: IsoDate | null;
}

export interface InstallmentPaymentResult {
  payment: InstallmentPaymentRead;
  installment_id: number;
  status: string;
  state: InstallmentStateRead;
}

export interface InstallmentWriteResult {
  installment_id: number;
  status: string;
  state: InstallmentStateRead;
  promise_until: IsoDate | null;
  direct_debit_rejected_at: IsoDate | null;
  note_id: number | null;
}

/** The v12 fields `GET /collections/{id}` adds on top of the legacy plan. */
export interface CollectionPlanCobranza {
  level_preference?: "auto" | "assisted" | "record";
  notices_enabled?: boolean;
  notices_paused_until?: IsoDate | null;
  notices_pause_reason?: string | null;
}

// --- notices -----------------------------------------------------------------

export type CollectionNoticeKind =
  | "payer_reminder"
  | "payer_overdue"
  | "payment_link"
  | "insurer_status_inquiry";
export type CollectionNoticeChannel = "email" | "whatsapp";
export type CollectionNoticeStatus = "queued" | "sent" | "failed" | "bounced" | "skipped";

export interface CollectionNoticeRequest {
  installment_id: number;
  kind?: CollectionNoticeKind;
  channel?: CollectionNoticeChannel;
}

export interface CollectionNoticePreview {
  installment_id: number;
  /** Who the mail greets — the actual recipient (the pagador), not the insured. */
  recipient_name?: string | null;
  /** The amount the notice asks for: the open BALANCE. */
  amount_uf?: DecimalString | null;
  kind: CollectionNoticeKind;
  channel: CollectionNoticeChannel;
  to_address: string | null;
  subject: string;
  text: string;
  html: string;
  level: CobranzaLevel;
  level_reason: string;
  missing: string[];
  warnings: string[];
  template_source: string;
}

export interface CollectionNoticeRead {
  id: number;
  installment_id: number | null;
  kind: CollectionNoticeKind;
  cadence_level: number | null;
  channel: CollectionNoticeChannel;
  to_address: string;
  subject: string | null;
  status: CollectionNoticeStatus;
  mail_message_id: number | null;
  share_link_id: number | null;
  sent_at: IsoDateTime | null;
  error: string | null;
  created_at: IsoDateTime;
}

export interface CollectionNoticePage {
  items: CollectionNoticeRead[];
  total: number;
  limit: number;
  offset: number;
}

// --- evidence ----------------------------------------------------------------

export type PaymentEvidenceStatus =
  | "suggested"
  | "needs_review"
  | "confirmed"
  | "rejected"
  | "superseded";
export type PaymentEvidenceSource =
  | "insurer_reply"
  | "insurer_share_link"
  | "insurer_statement"
  | "payer_upload";
export type ReportedPaymentStatus = "paid" | "unpaid" | "partial" | "cancelled" | "unknown";

/** A reason entry: a stable code, or `{code, detail}` with Spanish detail. */
export type EvidenceReasonEntry = string | { code?: string; detail?: string; [k: string]: unknown };

/** One live payment of the evidence's cuota, as the confirm form lists it. */
export interface EvidenceInstallmentPayment {
  id: number;
  amount_uf: DecimalString;
  evidence_level: PaymentEvidenceLevel | string;
  paid_on: IsoDate | null;
  source: PaymentRecordSource | string | null;
}

/**
 * The suggested cuota's money as it stands NOW — so the broker sees what is
 * already booked before confirming another payment on top of it.
 */
export interface EvidenceInstallmentSummary {
  gross_uf: DecimalString;
  paid_uf: DecimalString;
  balance_uf: DecimalString;
  state: InstallmentStateName | string;
  currency: "UF" | "USD" | string;
  payments: EvidenceInstallmentPayment[];
}

export interface PaymentEvidenceRead {
  id: number;
  status: PaymentEvidenceStatus;
  source: PaymentEvidenceSource | null;
  installment_id: number | null;
  installment_number: number | null;
  installment_due_date: IsoDate | null;
  installment_amount_uf: DecimalString | null;
  policy_id: number | null;
  policy_number: string | null;
  insurer_name: string | null;
  status_inquiry_id: number | null;
  inquiry_token: string | null;
  mail_message_id: number | null;
  mail_subject: string | null;
  mail_from: string | null;
  document_id: number | null;
  extraction_id: number | null;
  reported_status: ReportedPaymentStatus | null;
  reported_amount: DecimalString | null;
  reported_currency: PaymentCurrencyCode | null;
  reported_paid_on: IsoDate | null;
  score: DecimalString | null;
  reasons: EvidenceReasonEntry[] | Record<string, unknown> | null;
  /** `false` = the sender is NOT who Radal asked — the UI must warn. */
  sender_verified: boolean | null;
  note: string | null;
  payment_id: number | null;
  decided_at: IsoDateTime | null;
  created_at: IsoDateTime;
  /** Optional (newer servers): the suggested cuota's live money + payments. */
  installment_summary?: EvidenceInstallmentSummary | null;
  /** Optional: what to pre-fill — the cuota's BALANCE, never its gross. */
  suggested_amount_uf?: DecimalString | null;
}

export interface PaymentEvidencePage {
  items: PaymentEvidenceRead[];
  total: number;
  limit: number;
  offset: number;
}

export interface PaymentEvidenceParams {
  status?: PaymentEvidenceStatus[];
  inquiry_id?: number;
  installment_id?: number;
  limit?: number;
  offset?: number;
}

export interface PaymentEvidenceConfirm {
  installment_id?: number | null;
  amount?: string | null;
  currency?: PaymentCurrencyCode | null;
  paid_on?: IsoDate | null;
  confirmed_by_insurer?: boolean;
  note?: string | null;
}

export interface PaymentEvidenceDecision {
  evidence: PaymentEvidenceRead;
  payment_id: number | null;
  installment_status: string | null;
}

// --- hooks ---------------------------------------------------------------------

function useInvalidateCobranza() {
  const qc = useQueryClient();
  return () => {
    void qc.invalidateQueries({ queryKey: qk.collections.all });
    void qc.invalidateQueries({ queryKey: qk.policies.all });
    void qc.invalidateQueries({ queryKey: qk.notifications.all });
    void qc.invalidateQueries({ queryKey: qk.notes.all });
    void qc.invalidateQueries({ queryKey: qk.inquiries.all });
  };
}

/** Payments of one cuota — under `qk.collections.all`, so every write refreshes it. */
export const installmentPaymentsKey = (installmentId: number) =>
  ["collections", "installment-rows", String(installmentId), "payments"] as const;
const paymentsKey = installmentPaymentsKey;

/** The server's page cap: one policy's or one plan's cuotas always fit. */
export const SCOPED_ROWS_LIMIT = 200;

/**
 * The cards. Without an owner it is keyed `qk.collections.summary()` WITHOUT
 * params so it shares one cache entry with the rail's overdue chip; with
 * "Mías" on (review r2 B5) it sends `owner=me`, so cards and list agree.
 */
export function useCollectionsBoardSummary(enabled = true, owner?: "me") {
  return useQuery({
    queryKey: owner ? qk.collections.summary({ owner }) : qk.collections.summary(),
    enabled,
    queryFn: async () => {
      const { data } = await api.get<CollectionsBoardSummary>("/collections/summary", {
        params: owner ? { owner } : undefined,
      });
      return data;
    },
  });
}

/** THE Cobranza list: cuotas with live state, level and last gestión. */
export function useBoardInstallments(params: BoardInstallmentParams = {}, enabled = true) {
  return useQuery({
    queryKey: qk.collections.installmentRows(params),
    enabled,
    placeholderData: (previous) => previous,
    queryFn: async () => {
      const { data } = await api.get<BoardInstallmentPage>("/collections/installments", {
        params: clean(params),
      });
      return data;
    },
  });
}

/**
 * Every cuota of ONE policy (no bucket), live. No `placeholderData`: a sheet
 * that switches policy must never show the previous policy's cuotas.
 * The sheet, the inquiry form and the evidence picker share this cache entry.
 */
export function usePolicyInstallmentRows(policyId: number | null | undefined, enabled = true) {
  const params: BoardInstallmentParams = { policy_id: policyId ?? 0, limit: SCOPED_ROWS_LIMIT };
  return useQuery({
    queryKey: qk.collections.installmentRows(params),
    enabled: !!policyId && enabled,
    queryFn: async () => {
      const { data } = await api.get<BoardInstallmentPage>("/collections/installments", {
        params: clean(params),
      });
      return data;
    },
  });
}

/** Every cuota of ONE plan (no bucket), live — the plan detail's v12 block. */
export function usePlanInstallmentRows(planId: number | null | undefined, enabled = true) {
  const params: BoardInstallmentParams = { plan_id: planId ?? 0, limit: SCOPED_ROWS_LIMIT };
  return useQuery({
    queryKey: qk.collections.installmentRows(params),
    enabled: !!planId && enabled,
    queryFn: async () => {
      const { data } = await api.get<BoardInstallmentPage>("/collections/installments", {
        params: clean(params),
      });
      return data;
    },
  });
}

export function useInstallmentMode(installmentId: number | undefined) {
  return useQuery({
    queryKey: qk.collections.installmentMode(installmentId ?? 0),
    enabled: !!installmentId,
    queryFn: async () => {
      const { data } = await api.get<InstallmentModeRead>(
        `/collections/installments/${installmentId}/mode`,
      );
      return data;
    },
  });
}

export function useInstallmentPayments(installmentId: number | undefined, enabled = true) {
  return useQuery({
    queryKey: paymentsKey(installmentId ?? 0),
    enabled: !!installmentId && enabled,
    queryFn: async () => {
      const { data } = await api.get<InstallmentPaymentRead[]>(
        `/collections/installments/${installmentId}/payments`,
      );
      return data;
    },
  });
}

export function useRegisterInstallmentPayment(installmentId: number) {
  const invalidate = useInvalidateCobranza();
  return useMutation({
    mutationFn: async (payload: InstallmentPaymentCreate) => {
      const { data } = await api.post<InstallmentPaymentResult>(
        `/collections/installments/${installmentId}/payments`,
        payload,
      );
      return data;
    },
    onSuccess: invalidate,
  });
}

export function useReverseInstallmentPayment() {
  const invalidate = useInvalidateCobranza();
  return useMutation({
    mutationFn: async ({
      paymentId,
      reason,
      note,
    }: {
      paymentId: number;
      reason: PaymentReversalReason;
      note?: string | null;
    }) => {
      const { data } = await api.post<InstallmentPaymentResult>(
        `/collections/payments/${paymentId}/reverse`,
        { reason, note: note || null },
      );
      return data;
    },
    onSuccess: invalidate,
  });
}

export function useConfirmInstallmentPayment() {
  const invalidate = useInvalidateCobranza();
  return useMutation({
    mutationFn: async (paymentId: number) => {
      const { data } = await api.post<InstallmentPaymentResult>(
        `/collections/payments/${paymentId}/confirm`,
      );
      return data;
    },
    onSuccess: invalidate,
  });
}

export function useMarkDirectDebitRejected(installmentId: number) {
  const invalidate = useInvalidateCobranza();
  return useMutation({
    mutationFn: async (payload: { rejected_on: IsoDate; note?: string | null }) => {
      const { data } = await api.post<InstallmentWriteResult>(
        `/collections/installments/${installmentId}/direct-debit-rejected`,
        { rejected_on: payload.rejected_on, note: payload.note || null },
      );
      return data;
    },
    onSuccess: invalidate,
  });
}

export function useRegisterPaymentPromise(installmentId: number) {
  const invalidate = useInvalidateCobranza();
  return useMutation({
    mutationFn: async (payload: { promise_until: IsoDate; note?: string | null }) => {
      const { data } = await api.post<InstallmentWriteResult>(
        `/collections/installments/${installmentId}/promise`,
        { promise_until: payload.promise_until, note: payload.note || null },
      );
      return data;
    },
    onSuccess: invalidate,
  });
}

/**
 * A gestión without a promise: a plain note on the cuota (`/notes` maps
 * `installment` to the Collections module, `phase='cobranza'` like the server's own).
 */
export function useCreateInstallmentNote(installmentId: number) {
  const invalidate = useInvalidateCobranza();
  return useMutation({
    mutationFn: async (body: string) => {
      const { data } = await api.post<{ id: number }>("/notes", {
        entity_type: "installment",
        entity_id: installmentId,
        body,
        is_internal: true,
        phase: "cobranza",
      });
      return data;
    },
    onSuccess: invalidate,
  });
}

/** The payments of several cuotas at once (plan detail), one cached query per cuota. */
export function useInstallmentsPayments(installmentIds: number[]) {
  return useQueries({
    queries: installmentIds.map((id) => ({
      queryKey: paymentsKey(id),
      queryFn: async () => {
        const { data } = await api.get<InstallmentPaymentRead[]>(
          `/collections/installments/${id}/payments`,
        );
        return data;
      },
    })),
  });
}

/** The avisos sent for several cuotas (the API filters by ONE cuota). */
export function useInstallmentsNotices(installmentIds: number[]) {
  return useQueries({
    queries: installmentIds.map((id) => {
      const params = { installment_id: id, limit: 50 };
      return {
        queryKey: qk.collections.notices(params),
        queryFn: async () => {
          const { data } = await api.get<CollectionNoticePage>("/collections/notices", {
            params: clean(params),
          });
          return data;
        },
      };
    }),
  });
}

/** v12 plan fields: reminders on/off, level preference, pause (C5.4). */
export interface CollectionPlanCobranzaUpdate {
  notices_enabled?: boolean | null;
  level_preference?: "auto" | "assisted" | "record" | null;
  notices_paused_until?: IsoDate | null;
  notices_pause_reason?: string | null;
}

export function useUpdateCollectionPlanCobranza(planId: number) {
  const invalidate = useInvalidateCobranza();
  return useMutation({
    mutationFn: async (payload: CollectionPlanCobranzaUpdate) => {
      const { data } = await api.patch<CollectionPlan & CollectionPlanCobranza>(
        `/collections/plans/${planId}`,
        payload,
      );
      return data;
    },
    onSuccess: invalidate,
  });
}

/** *Iniciar cobranza*: payer reminders ON for the plan — works for any insurer. */
export function useStartCollection(planId: number) {
  const invalidate = useInvalidateCobranza();
  return useMutation({
    mutationFn: async (level_preference?: "auto" | "assisted") => {
      const { data } = await api.post<CollectionPlan & CollectionPlanCobranza>(
        `/collections/plans/${planId}/start`,
        level_preference ? { level_preference } : {},
      );
      return data;
    },
    onSuccess: invalidate,
  });
}

/**
 * A comprobante for a payment. Filed on the PLAN (`collection_plan` is a
 * document entity; `installment` is not) as `payment_receipt`.
 */
export function useUploadPaymentReceipt() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ file, planId }: { file: File; planId: number }) => {
      const form = new FormData();
      form.append("file", file);
      form.append("entity_type", "collection_plan");
      form.append("entity_id", String(planId));
      form.append("category", "payment_receipt");
      form.append("phase", "cobranza");
      const { data } = await api.post<{ id: number; filename?: string | null }>(
        "/documents",
        form,
        { headers: { "Content-Type": "multipart/form-data" } },
      );
      return data;
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.documents.all });
    },
  });
}

export function useCollectionNotices(
  params: { installment_id?: number; limit?: number; offset?: number },
  enabled = true,
) {
  return useQuery({
    queryKey: qk.collections.notices(params),
    enabled,
    queryFn: async () => {
      const { data } = await api.get<CollectionNoticePage>("/collections/notices", {
        params: clean(params),
      });
      return data;
    },
  });
}

/** Preview is a POST that writes nothing — a mutation, so it never auto-fires. */
export function usePreviewCollectionNotice() {
  return useMutation({
    mutationFn: async (payload: CollectionNoticeRequest) => {
      const { data } = await api.post<CollectionNoticePreview>(
        "/collections/notices/preview",
        payload,
      );
      return data;
    },
  });
}

export function useSendCollectionNotice() {
  const invalidate = useInvalidateCobranza();
  return useMutation({
    mutationFn: async (payload: CollectionNoticeRequest) => {
      const { data } = await api.post<CollectionNoticeRead>("/collections/notices/send", payload);
      return data;
    },
    onSuccess: invalidate,
  });
}

/** "Respuestas por confirmar". Without `status`: the open ones. */
export function usePaymentEvidence(params: PaymentEvidenceParams = {}, enabled = true) {
  return useQuery({
    queryKey: qk.collections.evidence(params),
    enabled,
    queryFn: async () => {
      const { data } = await api.get<PaymentEvidencePage>("/collections/evidence", {
        params: clean(params),
      });
      return data;
    },
  });
}

export function useConfirmPaymentEvidence() {
  const invalidate = useInvalidateCobranza();
  return useMutation({
    mutationFn: async ({
      evidenceId,
      ...payload
    }: PaymentEvidenceConfirm & { evidenceId: number }) => {
      const { data } = await api.post<PaymentEvidenceDecision>(
        `/collections/evidence/${evidenceId}/confirm`,
        payload,
      );
      return data;
    },
    onSuccess: invalidate,
  });
}

export function useRejectPaymentEvidence() {
  const invalidate = useInvalidateCobranza();
  return useMutation({
    mutationFn: async ({ evidenceId, note }: { evidenceId: number; note?: string | null }) => {
      const { data } = await api.post<PaymentEvidenceDecision>(
        `/collections/evidence/${evidenceId}/reject`,
        { note: note || null },
      );
      return data;
    },
    onSuccess: invalidate,
  });
}
