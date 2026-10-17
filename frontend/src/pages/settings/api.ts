/**
 * Settings-side hooks for the broker's cobranza configuration (v12, lane F3).
 *
 * `api/collections.ts` and `api/payments.ts` belong to lane F2, so the three
 * endpoint families Configuración needs live here, keyed with the SAME shared
 * `qk.*` keys F2 uses — a save here refreshes Cobranza and vice versa:
 *
 * - `GET/PUT /collections/settings` (read Collections.View, write Settings.Manage)
 * - `GET/PUT /collections/templates` + `POST /collections/templates/approve`
 *   (read Collections.View, write Settings.Manage). Approval is PER MESSAGE: a
 *   content edit withdraws that message's approval server-side (the others keep
 *   sending); nothing reaches a payer on unapproved wording.
 * - `GET /payments/insurers` (Collections.View) + `PUT /payments/insurers/{id}/override`
 *   (Settings.Manage) — delegated to F2's hooks in `@/api/payments` so the key and
 *   the invalidation rule exist once. The broker NEVER sees an onboarding status.
 */
import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseMutationResult,
  type UseQueryResult,
} from "@tanstack/react-query";
import api from "@/lib/api";
import { qk } from "@/api/keys";
import type { PaymentChannel, PaymentInstructions } from "@/api/platform";
import { usePaymentInsurers, useUpdatePaymentInsurerOverride } from "@/api/payments";

// --- collection settings ---------------------------------------------------------

export type MailSenderChoice = "radal" | "broker_account";

export interface CollectionSettings {
  cadence_days: number[];
  followup_days: number;
  /** Decimal strings on the wire. */
  tolerance_uf: string;
  tolerance_pct: string;
  tolerance_clp: number;
  notices_enabled: boolean;
  notices_approved_at: string | null;
  notices_approved_by_user_id: number | null;
  default_sender: MailSenderChoice;
  sender_mail_account_id: number | null;
  native_collection_enabled: boolean;
  auto_status_inquiries: boolean;
  inquiry_after_days: number;
  updated_at: string | null;
}

export interface CollectionSettingsUpdate {
  cadence_days?: number[];
  followup_days?: number;
  tolerance_uf?: string;
  tolerance_pct?: string;
  tolerance_clp?: number;
  notices_enabled?: boolean;
  default_sender?: MailSenderChoice;
  sender_mail_account_id?: number | null;
  native_collection_enabled?: boolean;
  auto_status_inquiries?: boolean;
  inquiry_after_days?: number;
}

export function useCollectionSettings(enabled = true) {
  return useQuery({
    queryKey: qk.collections.settings,
    enabled,
    queryFn: async () => {
      const { data } = await api.get<CollectionSettings>("/collections/settings");
      return data;
    },
  });
}

export function useUpdateCollectionSettings() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload: CollectionSettingsUpdate) => {
      const { data } = await api.put<CollectionSettings>("/collections/settings", payload);
      return data;
    },
    onSuccess: (data) => {
      qc.setQueryData(qk.collections.settings, data);
      void qc.invalidateQueries({ queryKey: qk.collections.all });
      // `native_collection_enabled` decides every insurer's `level_available`.
      void qc.invalidateQueries({ queryKey: qk.payments.insurers });
    },
  });
}

// --- notice templates -------------------------------------------------------------

export type NoticeKind =
  | "payer_reminder"
  | "payer_overdue"
  | "payment_link"
  | "insurer_status_inquiry";
export type NoticeChannel = "email" | "whatsapp";

export interface NoticeTemplate {
  id: number | null;
  kind: NoticeKind;
  cadence_level: number | null;
  channel: NoticeChannel;
  subject: string;
  body: string;
  is_active: boolean;
  is_default: boolean;
  approved_at: string | null;
  approved_by_user_id: number | null;
  /**
   * Per-message approval (newer servers): editing one message withdraws ONLY
   * its approval; the others keep sending. Absent on older servers — the UI
   * then falls back to `approved_at` / the set-wide flag.
   */
  is_approved?: boolean;
  approved_by_name?: string | null;
}

/** One slot to approve; `POST /collections/templates/approve` without items = all. */
export interface NoticeTemplateSlot {
  kind: NoticeKind;
  cadence_level: number | null;
}

export interface NoticeTemplates {
  items: NoticeTemplate[];
  variables: string[];
  approved: boolean;
  approved_at: string | null;
  approved_by_user_id: number | null;
}

export interface NoticeTemplateIn {
  kind: NoticeKind;
  cadence_level: number | null;
  channel: NoticeChannel;
  subject: string;
  body: string;
  is_active: boolean;
}

export function useNoticeTemplates(enabled = true) {
  return useQuery({
    queryKey: qk.collections.templates,
    enabled,
    queryFn: async () => {
      const { data } = await api.get<NoticeTemplates>("/collections/templates");
      return data;
    },
  });
}

export function useUpdateNoticeTemplates() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (templates: NoticeTemplateIn[]) => {
      const { data } = await api.put<NoticeTemplates>("/collections/templates", { templates });
      return data;
    },
    onSuccess: (data) => {
      qc.setQueryData(qk.collections.templates, data);
      // The approval stamp lives on the settings row too.
      void qc.invalidateQueries({ queryKey: qk.collections.settings });
    },
  });
}

export function useApproveNoticeTemplates() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (items?: NoticeTemplateSlot[]) => {
      const { data } = await api.post<NoticeTemplates>(
        "/collections/templates/approve",
        items && items.length ? { items } : {},
      );
      return data;
    },
    onSuccess: (data) => {
      qc.setQueryData(qk.collections.templates, data);
      void qc.invalidateQueries({ queryKey: qk.collections.settings });
    },
  });
}

// --- insurer capabilities + broker override -----------------------------------------

export interface InsurerRef {
  id: number;
  name: string;
  legal_name: string;
  rut: string | null;
}

export interface BrokerInsurerOverride {
  collections_email: string | null;
  contact_name: string | null;
  payment_instructions: PaymentInstructions;
  native_enabled: boolean;
  notes: string | null;
  updated_at: string | null;
}

export interface InsurerCapability {
  insurer: InsurerRef;
  /** `native` (cobro en línea) or `assisted`. */
  level_available: "native" | "assisted";
  channels: PaymentChannel[];
  /** RADAL profile ⊕ broker override (override wins per field). */
  instructions: PaymentInstructions;
  collections_email: string | null;
  broker_override: BrokerInsurerOverride | null;
}

export interface InsurerOverrideUpdate {
  collections_email?: string | null;
  contact_name?: string | null;
  payment_instructions?: PaymentInstructions | null;
  native_enabled?: boolean | null;
  notes?: string | null;
}

export type InsurerScope = "mine" | "all";

/**
 * Thin typed views over lane F2's hooks (`@/api/payments`) — ONE query key and
 * ONE invalidation rule for these endpoints. F2 types channels and instructions
 * loosely (`string[]`, `Record<string, string>`); the server only ever sends the
 * `PaymentChannel` / `InstructionKey` members, so this file narrows them.
 */
export function useInsurerCapabilities(scope: InsurerScope, enabled = true) {
  return usePaymentInsurers({ scope }, enabled) as unknown as UseQueryResult<InsurerCapability[]>;
}

/** Override for one insurer; refreshes the insurer list AND Cobranza (levels move). */
export function useUpdateInsurerOverride(insurerId: number) {
  return useUpdatePaymentInsurerOverride(insurerId) as unknown as UseMutationResult<
    InsurerCapability,
    Error,
    InsurerOverrideUpdate
  >;
}
