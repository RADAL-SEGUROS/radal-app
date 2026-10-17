/**
 * Share links (v12, 02 §7) — the broker side AND the public side.
 *
 * Broker side (`/share-links`) goes through the shared `api` instance like every
 * other authenticated call.
 *
 * Public side (`/public/share/{token}`): the token IS the credential, so these
 * requests must never carry the broker's app token. A broker who opens their
 * own link while logged in would otherwise send `Authorization` /
 * `X-Radal-Token` to a public endpoint. This module therefore owns a dedicated
 * axios instance that replicates ONLY the OAC body-hash mechanism of
 * `lib/api.ts` (same precedent as `api/public.ts` and `api/ai.ts`): JSON bodies
 * are hashed and `config.data` is reassigned to the exact string hashed;
 * multipart bodies (the comprobante upload) send `UNSIGNED-PAYLOAD`. Breaking
 * either produces `InvalidSignatureException` in production only.
 */
import axios, { type InternalAxiosRequestConfig } from "axios";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, API_URL } from "@/lib/api";
import { qk } from "@/api/keys";
import type { DecimalString, IsoDate, IsoDateTime } from "@/api/types";

// =============================================================================
// Types (mirror backend/app/schemas/share.py)
// =============================================================================

export type ShareEntityType =
  | "policy"
  | "collection_plan"
  | "installment"
  | "case_file"
  | "status_inquiry";
export type ShareAudience = "insured" | "insurer";

/** The capabilities a broker may set; omitted keys take the audience defaults. */
export interface SharePermissionsInput {
  can_pay?: boolean;
  can_upload_receipt?: boolean;
  can_report_status?: boolean;
}

export interface ShareLinkCreate {
  entity_type: ShareEntityType;
  entity_id: number;
  audience: ShareAudience;
  permissions?: SharePermissionsInput | null;
  expires_in_days?: number | null;
  label?: string | null;
}

export interface ShareLinkRead {
  id: number;
  entity_type: ShareEntityType;
  entity_id: number;
  audience: ShareAudience;
  permissions: Record<string, boolean>;
  label: string | null;
  token: string;
  url: string;
  whatsapp_url: string;
  whatsapp_text: string;
  is_live: boolean;
  expires_at: IsoDateTime | null;
  revoked_at: IsoDateTime | null;
  view_count: number;
  last_viewed_at: IsoDateTime | null;
  created_by_user_id: number | null;
  created_at: IsoDateTime | null;
}

export interface ShareLinkList {
  items: ShareLinkRead[];
  total: number;
}

/** Cuota states computed by `services.collection.status` (closed list). */
export type PublicInstallmentState =
  | "upcoming"
  | "due_today"
  | "overdue"
  | "termination_risk"
  | "promised"
  | "paid"
  | "voided";

export type PublicReportStatus = "paid" | "unpaid" | "partial" | "cancelled";
export type PublicCurrency = "UF" | "USD" | "CLP";

export interface PublicInstructions {
  insurer_name: string | null;
  /** Already filtered server-side to http(s); the page re-checks anyway. */
  portal_url: string | null;
  bank_name: string | null;
  account_type: string | null;
  account_number: string | null;
  account_holder: string | null;
  account_rut: string | null;
  transfer_email: string | null;
  receipt_email: string | null;
  notes: string | null;
}

export interface PublicPolicy {
  id: number;
  policy_number: string | null;
  insurer_name: string | null;
  line_name: string | null;
  subtype_code: string | null;
  subtype_name: string | null;
  start_date: IsoDate | null;
  end_date: IsoDate | null;
  insured_name: string | null;
  currency: string;
  payment_instructions: PublicInstructions | null;
}

export interface PublicInstallment {
  id: number;
  policy_id: number;
  number: number;
  coupon_number: string | null;
  due_date: IsoDate | null;
  currency: string;
  amount_uf: DecimalString;
  amount_clp: number | null;
  paid_uf: DecimalString;
  balance_uf: DecimalString;
  balance_clp: number | null;
  state: PublicInstallmentState | string;
  partial: boolean;
  days_late: number;
  level: "native" | "assisted" | "record" | null;
  action: "pay" | "instructions" | "report" | "none";
  can_upload_receipt: boolean;
  can_report: boolean;
  reported: { status: string | null; reported_at: IsoDateTime | null } | null;
  /** Insured audience: a receipt the payer sent that the broker has not decided yet. */
  receipt_pending?: { status: string | null; reported_at: IsoDateTime | null } | null;
}

export interface PublicShareRead {
  audience: ShareAudience;
  entity_type: ShareEntityType;
  label: string | null;
  expires_at: IsoDateTime | null;
  permissions: {
    can_pay: boolean;
    can_upload_receipt: boolean;
    can_report_status: boolean;
    show_documents: boolean;
  };
  today: IsoDate;
  broker: { name: string | null; logo_url: string | null };
  policy: PublicPolicy | null;
  policies: PublicPolicy[];
  installments: PublicInstallment[];
  uf_rate: DecimalString | null;
  case_file: { reference: string | null; title: string | null; period_label: string | null } | null;
  inquiry: { reference: string; status: string | null } | null;
  report_statuses: PublicReportStatus[];
}

export interface PublicReportCreate {
  installment_id: number;
  status: PublicReportStatus;
  amount?: string | null;
  currency?: PublicCurrency;
  paid_on?: IsoDate | null;
  comment?: string | null;
}

export interface PublicReceiptCreate {
  installment_id: number;
  file: File;
  amount?: string | null;
  currency?: PublicCurrency;
  paid_on?: IsoDate | null;
  comment?: string | null;
}

export interface PublicWriteResult {
  received: boolean;
  installment_id: number;
  reported_status: string;
  created: boolean;
  message: string;
}

/** Comprobante limits enforced by the server (`RECEIPT_MAX_BYTES`, mime list). */
export const RECEIPT_MAX_BYTES = 10 * 1024 * 1024;
export const RECEIPT_ACCEPT = ".pdf,.jpg,.jpeg,.png,application/pdf,image/jpeg,image/png";
export const RECEIPT_MIME_TYPES = ["application/pdf", "image/jpeg", "image/jpg", "image/png"];

// =============================================================================
// Broker side — authenticated
// =============================================================================

/** GET /share-links?entity_type&entity_id — newest first, revoked/expired included. */
export function useShareLinks(
  entityType: ShareEntityType,
  entityId: number,
  options: { enabled?: boolean } = {},
) {
  const params = { entity_type: entityType, entity_id: entityId };
  return useQuery({
    queryKey: qk.shareLinks.list(params),
    enabled: (options.enabled ?? true) && entityId > 0,
    queryFn: async () => {
      const { data } = await api.get<ShareLinkList>("/share-links", { params });
      return data;
    },
  });
}

/** POST /share-links — mint a WhatsApp-ready public link. */
export function useCreateShareLink() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload: ShareLinkCreate) => {
      const { data } = await api.post<ShareLinkRead>("/share-links", payload);
      return data;
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.shareLinks.all });
    },
  });
}

/** POST /share-links/{id}/revoke — idempotent; the public side 404s from then on. */
export function useRevokeShareLink() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (linkId: number) => {
      const { data } = await api.post<ShareLinkRead>(`/share-links/${linkId}/revoke`);
      return data;
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.shareLinks.all });
    },
  });
}

// =============================================================================
// Public side — no auth, OAC body hash only
// =============================================================================

async function sha256Hex(input: string): Promise<string> {
  const bytes = new TextEncoder().encode(input);
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

/** No-auth axios: OAC body-hash only, never an Authorization / X-Radal-Token. */
export const publicShareApi = axios.create({
  baseURL: API_URL,
  headers: { "Content-Type": "application/json" },
});

publicShareApi.interceptors.request.use(async (config: InternalAxiosRequestConfig) => {
  const data = config.data;
  // Multipart bodies are assembled by the browser with a boundary we do not
  // control, so their bytes cannot be hashed here: SigV4's escape hatch.
  const isOpaqueBody =
    typeof FormData !== "undefined" && data instanceof FormData
      ? true
      : (typeof Blob !== "undefined" && data instanceof Blob) ||
        data instanceof ArrayBuffer ||
        ArrayBuffer.isView(data as ArrayBufferView);

  if (isOpaqueBody) {
    config.headers.set("x-amz-content-sha256", "UNSIGNED-PAYLOAD");
  } else if (crypto?.subtle) {
    let body = "";
    if (data !== undefined && data !== null) {
      body = typeof data === "string" ? data : JSON.stringify(data);
      // Hash exactly the bytes that get sent.
      config.data = body;
    }
    config.headers.set("x-amz-content-sha256", await sha256Hex(body));
  }
  return config;
});

/** GET /public/share/{token} — 404 when unknown, revoked or expired (indistinguishable). */
export function usePublicShare(token: string | undefined) {
  return useQuery({
    queryKey: qk.publicShare.detail(token ?? ""),
    enabled: !!token,
    retry: (count, error) => {
      const code = (error as { response?: { status?: number } })?.response?.status;
      // 4xx is an answer, not a hiccup: never hammer a dead token.
      if (code && code >= 400 && code < 500) return false;
      return count < 1;
    },
    refetchOnWindowFocus: false,
    queryFn: async () => {
      const { data } = await publicShareApi.get<PublicShareRead>(
        `/public/share/${encodeURIComponent(token!)}`,
      );
      return data;
    },
  });
}

/** POST /public/share/{token}/checkout → `{checkout_url}` (native cuotas only). */
export function usePublicCheckout(token: string) {
  return useMutation({
    mutationFn: async (installmentIds: number[]) => {
      const { data } = await publicShareApi.post<{ checkout_url: string }>(
        `/public/share/${encodeURIComponent(token)}/checkout`,
        { installment_ids: installmentIds },
      );
      return data;
    },
  });
}

/** POST /public/share/{token}/report — the insurer informs a cuota's status. */
export function usePublicReport(token: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload: PublicReportCreate) => {
      const { data } = await publicShareApi.post<PublicWriteResult>(
        `/public/share/${encodeURIComponent(token)}/report`,
        payload,
      );
      return data;
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.publicShare.detail(token) });
    },
  });
}

/** POST /public/share/{token}/receipt — multipart comprobante upload (PDF/JPG/PNG ≤ 10 MB). */
export function usePublicReceipt(token: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload: PublicReceiptCreate) => {
      const form = new FormData();
      form.append("file", payload.file);
      form.append("installment_id", String(payload.installment_id));
      if (payload.amount) {
        form.append("amount", payload.amount);
        form.append("currency", payload.currency ?? "UF");
      }
      if (payload.paid_on) form.append("paid_on", payload.paid_on);
      if (payload.comment) form.append("comment", payload.comment);
      const { data } = await publicShareApi.post<PublicWriteResult>(
        `/public/share/${encodeURIComponent(token)}/receipt`,
        form,
        // The instance default is JSON; axios would otherwise serialise the
        // FormData to JSON. The browser fills in the boundary.
        { headers: { "Content-Type": "multipart/form-data" } },
      );
      return data;
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.publicShare.detail(token) });
    },
  });
}
