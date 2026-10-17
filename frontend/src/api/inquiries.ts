/**
 * Status inquiries (v12, lane F2) — Radal ASKS the insurer whether a cuota was
 * paid, because the payment lands in the insurer's account, not the broker's.
 *
 * `POST /inquiries` mints an insurer share link and sends the mail; the reply
 * (by mail or through the link) becomes payment EVIDENCE that a human confirms
 * in "Respuestas por confirmar". Nothing here writes a payment.
 *
 * Shapes mirror `backend/app/schemas/inquiries.py`.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import api from "@/lib/api";
import { clean, qk } from "@/api/keys";
import type { DecimalString, IsoDate, IsoDateTime } from "@/api/types";
import type { PaymentEvidenceRead } from "@/api/collections";

export type StatusInquiryStatus =
  | "draft"
  | "sent"
  | "replied"
  | "resolved"
  | "failed"
  | "cancelled";

export type InquiryMailStatus =
  | "queued"
  | "sent"
  | "failed"
  | "skipped"
  | "received"
  | "processed"
  | "ignored";

export type InquirySendVia = "radal" | "account" | "auto";

export interface StatusInquiryInstallment {
  id: number;
  number: number;
  due_date: IsoDate | null;
  gross_amount_uf: DecimalString;
  currency: string;
  status: string;
}

export interface StatusInquiryMail {
  id: number;
  status: InquiryMailStatus;
  from_address: string | null;
  to_addresses: unknown[] | null;
  subject: string | null;
  occurred_at: IsoDateTime | null;
  error: string | null;
}

export interface StatusInquiry {
  id: number;
  token: string;
  status: StatusInquiryStatus;
  policy_id: number;
  policy_number: string | null;
  insured_name: string | null;
  insurer_id: number;
  insurer_name: string | null;
  to_address: string | null;
  installment_ids: number[];
  installments: StatusInquiryInstallment[];
  share_link_id: number | null;
  share_url: string | null;
  outbound_message_id: number | null;
  mail_status: InquiryMailStatus | null;
  sent_at: IsoDateTime | null;
  replied_at: IsoDateTime | null;
  resolved_at: IsoDateTime | null;
  created_by_user_id: number | null;
  created_by_job: boolean;
  created_at: IsoDateTime;
  evidence_open: number;
}

export interface StatusInquiryDetail extends StatusInquiry {
  messages: StatusInquiryMail[];
  evidence: PaymentEvidenceRead[];
}

export interface StatusInquiryPage {
  items: StatusInquiry[];
  total: number;
  limit: number;
  offset: number;
}

export interface StatusInquiryParams {
  status?: StatusInquiryStatus;
  policy_id?: number;
  limit?: number;
  offset?: number;
}

export interface StatusInquiryCreate {
  policy_id: number;
  installment_ids: number[];
  to_address?: string | null;
  message?: string | null;
  send_via?: InquirySendVia;
  mail_account_id?: number | null;
}

/**
 * A second OPEN inquiry for the same cuota is refused (409) with the id of the
 * one already open — under `detail.existing_inquiry_id` or at the top level.
 */
export function existingInquiryIdOf(error: unknown): number | null {
  const response = (error as { response?: { status?: number; data?: unknown } } | null)?.response;
  if (!response || response.status !== 409) return null;
  const data = response.data as { detail?: unknown; existing_inquiry_id?: unknown } | undefined;
  const detail = data?.detail;
  const raw =
    (detail && typeof detail === "object" && !Array.isArray(detail)
      ? (detail as { existing_inquiry_id?: unknown }).existing_inquiry_id
      : undefined) ?? data?.existing_inquiry_id;
  const id = typeof raw === "number" ? raw : typeof raw === "string" ? Number(raw) : NaN;
  return Number.isFinite(id) && id > 0 ? id : null;
}

function useInvalidateInquiries() {
  const qc = useQueryClient();
  return () => {
    void qc.invalidateQueries({ queryKey: qk.inquiries.all });
    void qc.invalidateQueries({ queryKey: qk.collections.all });
  };
}

export function useStatusInquiries(params: StatusInquiryParams = {}, enabled = true) {
  return useQuery({
    queryKey: qk.inquiries.list(params),
    enabled,
    placeholderData: (previous) => previous,
    queryFn: async () => {
      const { data } = await api.get<StatusInquiryPage>("/inquiries", {
        params: clean(params),
      });
      return data;
    },
  });
}

export function useStatusInquiry(inquiryId: number | undefined) {
  return useQuery({
    queryKey: qk.inquiries.detail(inquiryId ?? 0),
    enabled: !!inquiryId,
    queryFn: async () => {
      const { data } = await api.get<StatusInquiryDetail>(`/inquiries/${inquiryId}`);
      return data;
    },
  });
}

/** "Consultar a la compañía": inquiry + insurer share link + mail. */
export function useCreateStatusInquiry() {
  const invalidate = useInvalidateInquiries();
  return useMutation({
    mutationFn: async (payload: StatusInquiryCreate) => {
      const { data } = await api.post<StatusInquiry>("/inquiries", {
        send_via: "auto",
        ...payload,
        to_address: payload.to_address || null,
        message: payload.message || null,
      });
      return data;
    },
    onSuccess: invalidate,
  });
}

export function useResendStatusInquiry() {
  const invalidate = useInvalidateInquiries();
  return useMutation({
    mutationFn: async ({ inquiryId, message }: { inquiryId: number; message?: string | null }) => {
      const { data } = await api.post<StatusInquiry>(`/inquiries/${inquiryId}/resend`, {
        message: message || null,
        send_via: "auto",
      });
      return data;
    },
    onSuccess: invalidate,
  });
}

export function useCancelStatusInquiry() {
  const invalidate = useInvalidateInquiries();
  return useMutation({
    mutationFn: async (inquiryId: number) => {
      const { data } = await api.post<StatusInquiry>(`/inquiries/${inquiryId}/cancel`);
      return data;
    },
    onSuccess: invalidate,
  });
}
