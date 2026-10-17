/**
 * Mail — the broker's mailboxes (v12, `docs/v12/04-mail.md`, 02 §6).
 *
 * `GET /mail/accounts` always leads with a VIRTUAL, read-only entry (`id: null`,
 * `kind: "radal_system"`) describing the Radal sender (`contacto@radalseguros.cl`,
 * sent through Amazon SES; replies read from that Zoho mailbox — `transport` /
 * `transport_label` say which) that every broker can use out of the box, followed by the custom
 * SMTP/IMAP mailboxes the caller may see, plus the provider picker (Google /
 * Microsoft come back `available: false` with the server's "pronto" reason —
 * the UI shows them disabled, never as a dead button).
 *
 * Passwords are WRITE-ONLY: requests may carry `password`; responses only ever
 * say `has_secret`.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import api from "@/lib/api";
import { clean, qk } from "@/api/keys";

export type MailProvider = "radal_system" | "custom" | "google" | "microsoft";
export type MailSecurity = "ssl" | "starttls" | "none";
export type MailAccountStatus = "draft" | "connected" | "error" | "paused";
export type MailOwner = "me" | "shared" | "other" | "radal";

export const MAIL_SECURITIES: readonly MailSecurity[] = ["ssl", "starttls", "none"] as const;

export interface MailAccount {
  /** `null` for the virtual Radal system sender. */
  id: number | null;
  kind: "radal_system" | "custom" | "google" | "microsoft";
  provider: MailProvider;
  address: string;
  display_name: string | null;
  owner: MailOwner;
  owner_user_id: number | null;
  smtp_host: string | null;
  smtp_port: number | null;
  smtp_security: MailSecurity | null;
  imap_host: string | null;
  imap_port: number | null;
  imap_security: MailSecurity | null;
  username: string | null;
  has_secret: boolean;
  send_enabled: boolean;
  receive_enabled: boolean;
  status: MailAccountStatus;
  last_sync_at: string | null;
  last_error: string | null;
  read_only: boolean;
  /** True when sending is live (`MAIL_ENABLED`); otherwise mail is only recorded. */
  mail_enabled: boolean;
  can_manage: boolean;
  /** Radal system entry only: how it sends (`MAIL_TRANSPORT`); `null` on broker mailboxes. */
  transport?: "ses" | "smtp" | null;
  /** Radal system entry only: the server's Spanish one-liner, e.g. "Amazon SES · respuestas a … (Zoho)". */
  transport_label?: string | null;
}

export interface MailProviderOption {
  provider: MailProvider;
  available: boolean;
  reason: string | null;
}

export interface MailAccountList {
  items: MailAccount[];
  providers: MailProviderOption[];
}

export interface MailAccountCreate {
  provider?: "custom";
  address: string;
  display_name?: string | null;
  smtp_host?: string | null;
  smtp_port?: number | null;
  smtp_security?: MailSecurity | null;
  imap_host?: string | null;
  imap_port?: number | null;
  imap_security?: MailSecurity | null;
  username?: string | null;
  password?: string | null;
  send_enabled?: boolean;
  receive_enabled?: boolean;
  owner?: "me" | "shared";
}

export interface MailAccountUpdate {
  display_name?: string | null;
  smtp_host?: string | null;
  smtp_port?: number | null;
  smtp_security?: MailSecurity | null;
  imap_host?: string | null;
  imap_port?: number | null;
  imap_security?: MailSecurity | null;
  username?: string | null;
  /** Absent / empty = keep the stored one. */
  password?: string | null;
  send_enabled?: boolean;
  receive_enabled?: boolean;
  paused?: boolean;
}

export interface MailCheck {
  smtp_ok: boolean | null;
  imap_ok: boolean | null;
  smtp_error: string | null;
  imap_error: string | null;
  checked_at: string | null;
  ok: boolean;
}

export interface MailAccountTestResult {
  account: MailAccount;
  check: MailCheck;
}

export type MailDirection = "outbound" | "inbound";

export interface MailMessage {
  id: number;
  mail_account_id: number | null;
  direction: MailDirection;
  status: string;
  classification: string | null;
  thread_token: string | null;
  from_address: string | null;
  to_addresses: unknown[] | null;
  subject: string | null;
  body_text: string | null;
  occurred_at: string | null;
  error: string | null;
  created_at: string;
}

export interface MailMessagePage {
  items: MailMessage[];
  total: number;
  limit: number;
  offset: number;
}

export interface MailMessageParams {
  direction?: MailDirection;
  limit?: number;
  offset?: number;
}

/** The mailboxes + provider picker. */
export function useMailAccounts(enabled = true) {
  return useQuery({
    queryKey: qk.mail.accounts,
    enabled,
    queryFn: async () => {
      const { data } = await api.get<MailAccountList>("/mail/accounts");
      return data;
    },
  });
}

export function useCreateMailAccount() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload: MailAccountCreate) => {
      const { data } = await api.post<MailAccount>("/mail/accounts", {
        provider: "custom",
        ...payload,
      });
      return data;
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.mail.all });
    },
  });
}

export function useUpdateMailAccount() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, payload }: { id: number; payload: MailAccountUpdate }) => {
      const { data } = await api.patch<MailAccount>(`/mail/accounts/${id}`, payload);
      return data;
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.mail.all });
    },
  });
}

export function useDeleteMailAccount() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (id: number) => {
      await api.delete(`/mail/accounts/${id}`);
      return id;
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.mail.all });
      // A deleted sender resets the cobranza default sender server-side.
      void qc.invalidateQueries({ queryKey: qk.collections.settings });
    },
  });
}

/** SMTP + IMAP login through the transport; sets `status` connected / error. */
export function useTestMailAccount() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (id: number) => {
      const { data } = await api.post<MailAccountTestResult>(`/mail/accounts/${id}/test`);
      return data;
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.mail.accounts });
    },
  });
}

/** The broker's mail log (`Collections.View`). */
export function useMailMessages(params: MailMessageParams = {}, enabled = true) {
  return useQuery({
    queryKey: qk.mail.messages(params),
    enabled,
    queryFn: async () => {
      const { data } = await api.get<MailMessagePage>("/mail/messages", {
        params: clean(params),
      });
      return data;
    },
  });
}

/** Conventional port for a security mode — the form re-suggests it on change. */
export function defaultPort(kind: "smtp" | "imap", security: MailSecurity): number {
  if (kind === "smtp") {
    return security === "ssl" ? 465 : security === "starttls" ? 587 : 25;
  }
  return security === "ssl" ? 993 : 143;
}
