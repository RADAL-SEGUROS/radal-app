/**
 * Payments (v12, lane F2) — Forpay payment intents and the per-insurer
 * cobranza capabilities a broker sees.
 *
 * Shapes mirror `backend/app/schemas/payments.py`. A payment link exists only
 * for `native`-level cuotas: the server answers 409 with a Spanish reason
 * otherwise, and the UI renders the control disabled with the row's own
 * `level_reason` before it ever gets there.
 *
 * `GET /payments/insurers` never exposes an onboarding status or an ETA — the
 * broker never needs to know an insurer is "on its way" to Forpay.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import api from "@/lib/api";
import { clean, qk } from "@/api/keys";
import type { DecimalString, IsoDate, IsoDateTime } from "@/api/types";

export type PaymentIntentStatus =
  | "created"
  | "pending"
  | "paid"
  | "partially_paid"
  | "failed"
  | "expired"
  | "cancelled";

export interface PaymentIntentItem {
  id: number;
  installment_id: number;
  installment_number: number | null;
  due_date: IsoDate | null;
  policy_id: number | null;
  policy_number: string | null;
  insurer_id: number | null;
  insurer_name: string | null;
  amount_uf: DecimalString;
  store_commerce_code: string | null;
  external_installment_id: string;
  paid: boolean;
}

export interface PaymentIntent {
  id: number;
  external_id: string;
  status: PaymentIntentStatus;
  checkout_url: string | null;
  provider_account_id: number | null;
  share_link_id: number | null;
  payer_party_id: number | null;
  total_uf: DecimalString;
  expires_at: IsoDateTime | null;
  created_by_user_id: number | null;
  created_at: IsoDateTime | null;
  updated_at: IsoDateTime | null;
  items: PaymentIntentItem[];
}

export interface PaymentIntentCreated {
  intent: PaymentIntent;
  checkout_url: string;
  /** The same cuota set already had an open link: that one is returned. */
  reused: boolean;
}

export interface PaymentIntentPage {
  items: PaymentIntent[];
  total: number;
  limit: number;
  offset: number;
}

export interface PaymentIntentParams {
  installment_id?: number;
  policy_id?: number;
  status?: PaymentIntentStatus[];
  limit?: number;
  offset?: number;
}

export interface PaymentInsurerRef {
  id: number;
  name: string;
  legal_name: string;
  rut: string | null;
}

export interface PaymentBrokerOverride {
  collections_email: string | null;
  contact_name: string | null;
  payment_instructions: Record<string, string>;
  native_enabled: boolean;
  notes: string | null;
  updated_at: IsoDateTime | null;
}

/** What a broker sees of an insurer's cobranza. */
export interface PaymentInsurerCapability {
  insurer: PaymentInsurerRef;
  level_available: "native" | "assisted";
  channels: string[];
  instructions: Record<string, string>;
  collections_email: string | null;
  broker_override: PaymentBrokerOverride | null;
}

export interface PaymentInstructionsInput {
  portal_url?: string | null;
  bank_name?: string | null;
  account_type?: string | null;
  account_number?: string | null;
  account_holder?: string | null;
  account_rut?: string | null;
  transfer_email?: string | null;
  receipt_email?: string | null;
  notes?: string | null;
}

export interface PaymentInsurerOverrideUpdate {
  collections_email?: string | null;
  contact_name?: string | null;
  payment_instructions?: PaymentInstructionsInput | null;
  native_enabled?: boolean | null;
  notes?: string | null;
}

export function usePaymentIntents(params: PaymentIntentParams = {}, enabled = true) {
  return useQuery({
    queryKey: qk.payments.list(params),
    enabled,
    queryFn: async () => {
      const { data } = await api.get<PaymentIntentPage>("/payments/intents", {
        params: clean(params),
      });
      return data;
    },
  });
}

export function usePaymentIntent(intentId: number | undefined) {
  return useQuery({
    queryKey: qk.payments.intent(intentId ?? 0),
    enabled: !!intentId,
    queryFn: async () => {
      const { data } = await api.get<PaymentIntent>(`/payments/intents/${intentId}`);
      return data;
    },
  });
}

/** "Link de pago": a Forpay checkout over native-level cuotas. */
export function useCreatePaymentIntent() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload: { installment_ids: number[]; share_link_id?: number | null }) => {
      const { data } = await api.post<PaymentIntentCreated>("/payments/intents", payload);
      return data;
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.payments.all });
      void qc.invalidateQueries({ queryKey: qk.collections.all });
    },
  });
}

export function useCancelPaymentIntent() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (intentId: number) => {
      const { data } = await api.post<PaymentIntent>(`/payments/intents/${intentId}/cancel`);
      return data;
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.payments.all });
      void qc.invalidateQueries({ queryKey: qk.collections.all });
    },
  });
}

/** `scope=mine` (default): the insurers of the broker's own policies. */
export function usePaymentInsurers(
  params: { scope?: "mine" | "all"; insurer_id?: number } = {},
  enabled = true,
) {
  return useQuery({
    queryKey: [...qk.payments.insurers, params] as const,
    enabled,
    staleTime: 5 * 60 * 1000,
    queryFn: async () => {
      const { data } = await api.get<PaymentInsurerCapability[]>("/payments/insurers", {
        params: clean(params),
      });
      return data;
    },
  });
}

/** Broker override (contact email, instructions, opt-out). Settings.Manage. */
export function useUpdatePaymentInsurerOverride(insurerId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload: PaymentInsurerOverrideUpdate) => {
      const { data } = await api.put<PaymentInsurerCapability>(
        `/payments/insurers/${insurerId}/override`,
        payload,
      );
      return data;
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.payments.insurers });
      void qc.invalidateQueries({ queryKey: qk.collections.all });
    },
  });
}
