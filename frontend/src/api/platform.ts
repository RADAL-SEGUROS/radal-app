/**
 * Platform — RADAL's maintainer console (`/platform/*`, v12 02 §5).
 *
 * Every endpoint is `Platform.<action>` AND a platform user with no broker:
 * a broker admin gets 403. These pages therefore never call a broker endpoint
 * (no `/insurers`, no `/collections`) — the insurer list comes from
 * `GET /platform/insurer-profiles`, which returns EVERY canonical insurer with
 * its profile (a missing profile reads as `not_contacted`).
 *
 * Secrets are WRITE-ONLY. Provider accounts expose `has_secret` and the NAMES
 * of the keys that carry a value (`secret_keys`) — never a value. On update a
 * secret field that is absent/`null` is kept and `""` clears it.
 */
import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import api from "@/lib/api";
import { clean, qk } from "@/api/keys";

// --- enums --------------------------------------------------------------------

export type OnboardingStatus =
  | "not_contacted"
  | "in_conversation"
  | "onboarding"
  | "sandbox"
  | "active"
  | "suspended";
export const ONBOARDING_STATUSES: readonly OnboardingStatus[] = [
  "not_contacted",
  "in_conversation",
  "onboarding",
  "sandbox",
  "active",
  "suspended",
] as const;

export type PaymentChannel = "webpay" | "oneclick" | "bank_transfer" | "direct_debit";
export const PAYMENT_CHANNELS: readonly PaymentChannel[] = [
  "webpay",
  "oneclick",
  "bank_transfer",
  "direct_debit",
] as const;

export type ChannelStatus = "pending" | "sandbox" | "active" | "suspended";
export const CHANNEL_STATUSES: readonly ChannelStatus[] = [
  "pending",
  "sandbox",
  "active",
  "suspended",
] as const;

export type ProviderName = "forpay";
export type ProviderOwner = "radal_mall" | "insurer";
export const PROVIDER_OWNERS: readonly ProviderOwner[] = ["radal_mall", "insurer"] as const;
export type ProviderEnvironment = "sandbox" | "production";
export const PROVIDER_ENVIRONMENTS: readonly ProviderEnvironment[] = [
  "sandbox",
  "production",
] as const;
export type ProviderAccountStatus = "draft" | "active" | "disabled";
export const PROVIDER_ACCOUNT_STATUSES: readonly ProviderAccountStatus[] = [
  "draft",
  "active",
  "disabled",
] as const;

export type EventProcessing = "received" | "processed" | "ignored" | "failed";
export const EVENT_PROCESSING: readonly EventProcessing[] = [
  "received",
  "processed",
  "ignored",
  "failed",
] as const;

export type JobStatus = "running" | "succeeded" | "failed" | "skipped";
export const JOB_STATUSES: readonly JobStatus[] = [
  "running",
  "succeeded",
  "failed",
  "skipped",
] as const;

/** The insurer payment-instruction fields (`services.payments.insurers.INSTRUCTION_KEYS`). */
export const INSTRUCTION_KEYS = [
  "portal_url",
  "bank_name",
  "account_type",
  "account_number",
  "account_holder",
  "account_rut",
  "transfer_email",
  "receipt_email",
  "notes",
] as const;
export type InstructionKey = (typeof INSTRUCTION_KEYS)[number];
export type PaymentInstructions = Partial<Record<InstructionKey, string>>;

/** The write-only secret fields of a provider account. */
export const SECRET_KEYS = [
  "api_key",
  "password",
  "hmac_key",
  "public_key_pem",
  "jwe_alg",
  "jwe_enc",
  "signature_fields",
] as const;
export type SecretKey = (typeof SECRET_KEYS)[number];

// --- shapes -------------------------------------------------------------------

export interface PlatformChannel {
  id: number;
  insurer_id: number;
  insurer_name: string | null;
  channel: PaymentChannel;
  provider_account_id: number | null;
  provider_account_label: string | null;
  store_commerce_code: string | null;
  status: ChannelStatus;
  fee_model: Record<string, unknown> | null;
  activated_at: string | null;
  updated_at: string | null;
}

export interface InsurerProfile {
  insurer_id: number;
  insurer_name: string;
  insurer_rut: string | null;
  has_profile: boolean;
  onboarding_status: OnboardingStatus;
  status_note: string | null;
  expected_live_on: string | null;
  payment_instructions: PaymentInstructions;
  collections_email: string | null;
  sender_domains: string[];
  updated_by_user_id: number | null;
  updated_at: string | null;
  channels: PlatformChannel[];
}

export interface InsurerProfileUpdate {
  onboarding_status?: OnboardingStatus | null;
  status_note?: string | null;
  expected_live_on?: string | null;
  payment_instructions?: PaymentInstructions | null;
  collections_email?: string | null;
  sender_domains?: string[] | null;
}

export interface InsurerProfileParams {
  q?: string;
  onboarding_status?: OnboardingStatus;
}

export type SignatureFields = string[] | Record<string, string[]>;

export interface ProviderAccount {
  id: number;
  provider: ProviderName;
  label: string;
  owner: ProviderOwner;
  insurer_id: number | null;
  insurer_name: string | null;
  environment: ProviderEnvironment;
  base_url: string;
  commerce: string | null;
  channel_header: string | null;
  process_id: string | null;
  username: string | null;
  sales_executive_id: string | null;
  status: ProviderAccountStatus;
  has_secret: boolean;
  secret_keys: SecretKey[];
  webhook_key: string;
  webhook_url: string | null;
  webhook_urls: Record<string, string>;
  previous_webhook_secret_valid_until: string | null;
  last_verified_at: string | null;
  last_error: string | null;
  /**
   * The LAST Verificar result (newer servers): `ok`, `failed` (a later error
   * than the last success) or `never`. An account resolves cuotas to "En
   * línea" only when active AND `ok`.
   */
  verify_status?: ProviderVerifyStatus;
  created_at: string | null;
  updated_at: string | null;
}

export type ProviderVerifyStatus = "ok" | "failed" | "never";

/** The server's `verify_status`, or the same rule derived from the two stamps. */
export function verifyStatusOf(
  account: Pick<ProviderAccount, "verify_status" | "last_verified_at" | "last_error">,
): ProviderVerifyStatus {
  if (account.verify_status === "ok" || account.verify_status === "failed" || account.verify_status === "never") {
    return account.verify_status;
  }
  if (account.last_error) return "failed";
  return account.last_verified_at ? "ok" : "never";
}

export interface ProviderAccountSecrets {
  api_key?: string | null;
  password?: string | null;
  hmac_key?: string | null;
  public_key_pem?: string | null;
  jwe_alg?: string | null;
  jwe_enc?: string | null;
  signature_fields?: SignatureFields | null;
}

export interface ProviderAccountCreate extends ProviderAccountSecrets {
  provider?: ProviderName;
  label: string;
  owner: ProviderOwner;
  insurer_id?: number | null;
  environment?: ProviderEnvironment;
  base_url?: string | null;
  commerce?: string | null;
  channel_header?: string | null;
  process_id?: string | null;
  username?: string | null;
  sales_executive_id?: string | null;
  status?: ProviderAccountStatus;
}

export type ProviderAccountUpdate = Partial<Omit<ProviderAccountCreate, "provider">>;

export interface VerifyResult {
  ok: boolean;
  error: string | null;
  account: ProviderAccount;
}

export interface ChannelUpsert {
  provider_account_id?: number | null;
  store_commerce_code?: string | null;
  status?: ChannelStatus | null;
  fee_model?: Record<string, unknown> | null;
}

export interface ProviderEvent {
  id: number;
  provider: ProviderName;
  provider_account_id: number | null;
  dedup_key: string;
  event_type: string | null;
  signature_valid: boolean;
  received_at: string;
  processing_status: EventProcessing;
  attempts: number;
  error: string | null;
  broker_id: number | null;
}

export interface ProviderEventDetail extends ProviderEvent {
  payload: Record<string, unknown> | null;
}

export interface ProviderEventPage {
  items: ProviderEvent[];
  total: number;
  limit: number;
  offset: number;
}

export interface ProviderEventParams {
  status?: EventProcessing;
  provider_account_id?: number;
  signature_valid?: boolean;
  limit?: number;
  offset?: number;
}

export interface JobRun {
  id: number | null;
  job: string;
  scheduled_for: string;
  status: JobStatus;
  started_at: string | null;
  finished_at: string | null;
  stats: Record<string, unknown> | null;
  error: string | null;
}

export interface JobPage {
  items: JobRun[];
  total: number;
  limit: number;
  offset: number;
  /** Every registered job name (so jobs that never ran still show). */
  registered: string[];
}

export interface JobParams {
  job?: string;
  status?: JobStatus;
  since?: string;
  limit?: number;
  offset?: number;
}

// --- insurer profiles ------------------------------------------------------------

export function useInsurerProfiles(params: InsurerProfileParams = {}) {
  return useQuery({
    queryKey: qk.platform.insurerProfiles.list(params),
    queryFn: async () => {
      const { data } = await api.get<InsurerProfile[]>("/platform/insurer-profiles", {
        params: clean(params),
      });
      return data;
    },
  });
}

/** Upsert (`PUT` creates the profile when the insurer has none yet). */
export function useUpdateInsurerProfile() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({
      insurerId,
      payload,
    }: {
      insurerId: number;
      payload: InsurerProfileUpdate;
    }) => {
      const { data } = await api.put<InsurerProfile>(
        `/platform/insurer-profiles/${insurerId}`,
        payload,
      );
      return data;
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.platform.insurerProfiles.all });
    },
  });
}

// --- provider accounts ------------------------------------------------------------

export function useProviderAccounts() {
  return useQuery({
    queryKey: qk.platform.providerAccounts.lists(),
    queryFn: async () => {
      const { data } = await api.get<ProviderAccount[]>("/platform/provider-accounts");
      return data;
    },
  });
}

export function useCreateProviderAccount() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload: ProviderAccountCreate) => {
      const { data } = await api.post<ProviderAccount>("/platform/provider-accounts", {
        provider: "forpay",
        ...payload,
      });
      return data;
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.platform.providerAccounts.all });
    },
  });
}

export function useUpdateProviderAccount() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, payload }: { id: number; payload: ProviderAccountUpdate }) => {
      const { data } = await api.put<ProviderAccount>(
        `/platform/provider-accounts/${id}`,
        payload,
      );
      return data;
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.platform.providerAccounts.all });
      void qc.invalidateQueries({ queryKey: qk.platform.insurerChannels });
    },
  });
}

export function useVerifyProviderAccount() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (id: number) => {
      const { data } = await api.post<VerifyResult>(`/platform/provider-accounts/${id}/verify`);
      return data;
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.platform.providerAccounts.all });
      // A verify result moves cuotas in/out of "En línea" (only verified accounts are native).
      void qc.invalidateQueries({ queryKey: qk.platform.insurerChannels });
      void qc.invalidateQueries({ queryKey: qk.collections.all });
      void qc.invalidateQueries({ queryKey: qk.payments.insurers });
    },
  });
}

export function useRotateWebhookSecret() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (id: number) => {
      const { data } = await api.post<ProviderAccount>(
        `/platform/provider-accounts/${id}/rotate-webhook-secret`,
      );
      return data;
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.platform.providerAccounts.all });
    },
  });
}

// --- insurer channels ----------------------------------------------------------------

export function useInsurerChannels() {
  return useQuery({
    queryKey: qk.platform.insurerChannels,
    queryFn: async () => {
      const { data } = await api.get<PlatformChannel[]>("/platform/insurer-channels");
      return data;
    },
  });
}

export function useUpsertInsurerChannel() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({
      insurerId,
      channel,
      payload,
    }: {
      insurerId: number;
      channel: PaymentChannel;
      payload: ChannelUpsert;
    }) => {
      const { data } = await api.put<PlatformChannel>(
        `/platform/insurer-channels/${insurerId}/${channel}`,
        payload,
      );
      return data;
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.platform.insurerChannels });
      void qc.invalidateQueries({ queryKey: qk.platform.insurerProfiles.all });
    },
  });
}

// --- events -----------------------------------------------------------------------

export function useProviderEvents(params: ProviderEventParams = {}) {
  return useQuery({
    queryKey: qk.platform.events(params),
    placeholderData: (prev) => prev,
    queryFn: async () => {
      const { data } = await api.get<ProviderEventPage>("/platform/events", {
        params: clean(params),
      });
      return data;
    },
  });
}

export function useProviderEvent(id: number | null) {
  return useQuery({
    queryKey: [...qk.platform.all, "event", String(id ?? 0)] as const,
    enabled: id !== null,
    queryFn: async () => {
      const { data } = await api.get<ProviderEventDetail>(`/platform/events/${id}`);
      return data;
    },
  });
}

export function useReprocessEvent() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (id: number) => {
      const { data } = await api.post<ProviderEventDetail>(`/platform/events/${id}/reprocess`);
      return data;
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: [...qk.platform.all, "events"] });
      void qc.invalidateQueries({ queryKey: [...qk.platform.all, "event"] });
    },
  });
}

// --- jobs ----------------------------------------------------------------------------

export function useJobRuns(params: JobParams = {}) {
  return useQuery({
    queryKey: [...qk.platform.jobs, params] as const,
    placeholderData: (prev) => prev,
    queryFn: async () => {
      const { data } = await api.get<JobPage>("/platform/jobs", { params: clean(params) });
      return data;
    },
  });
}

/**
 * The latest run of each named job (one `limit=1` read per job). Minute jobs
 * (mail.poll, payments.sync, rpa.run) would push the daily ones off a single
 * history page, so "Última ejecución" asks per job.
 */
export function useLatestJobRuns(names: readonly string[]) {
  return useQueries({
    queries: names.map((job) => {
      const params: JobParams = { job, limit: 1 };
      return {
        queryKey: [...qk.platform.jobs, "latest", job] as const,
        queryFn: async () => {
          const { data } = await api.get<JobPage>("/platform/jobs", { params: clean(params) });
          return data.items[0] ?? null;
        },
      };
    }),
  });
}
