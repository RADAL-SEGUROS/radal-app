/**
 * Portal search RPA (`/portal-searches/*`, v12 lane B6 backend / F6 frontend).
 *
 * Shapes mirror `backend/app/schemas/portal_searches.py` exactly. Secrets are
 * write-only: a credential read only ever says `has_secret`.
 *
 * Runs are executed asynchronously (job `rpa.run`, or inline in dev), so the
 * list and detail hooks POLL while a run is `queued` or `running` and stop the
 * moment nothing is in flight.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import api from "@/lib/api";
import { qk } from "@/api/keys";

// --- Types --------------------------------------------------------------------

export type PortalStatus = "available" | "maintenance";
export type PortalMaintenanceCode =
  | "rpa_disabled"
  | "not_configured"
  | "layout_changed"
  | "coming_soon";
export type CredentialStatus = "unverified" | "valid" | "invalid";
export type PortalRunStatus = "queued" | "running" | "succeeded" | "failed" | "cancelled";
export type PortalErrorKind =
  | "credentials_missing"
  | "login_failed"
  | "layout_changed"
  | "timeout"
  | "captcha"
  | "no_results"
  | "portal_unavailable"
  | "unexpected";
export type AlternativeKind = "retry" | "upload_quote_pdf" | "other_portal";

export const PORTAL_RUN_STATUSES: readonly PortalRunStatus[] = [
  "queued",
  "running",
  "succeeded",
  "failed",
  "cancelled",
];

export interface PortalSubtypeRef {
  id: number | null;
  code: string;
  name: string | null;
}

export interface PortalInputField {
  name: string;
  /** `text | integer | select | rut | date` (connector-defined). */
  type: string;
  required: boolean;
  options: string[] | null;
}

export interface PortalCredential {
  portal: string;
  username: string | null;
  has_secret: boolean;
  status: CredentialStatus;
  last_verified_at: string | null;
  last_error: string | null;
  updated_at: string | null;
}

export interface Portal {
  key: string;
  name: string;
  version: string | null;
  status: PortalStatus | string;
  /** Spanish, server-side: why the portal cannot be used right now. */
  maintenance_reason: string | null;
  maintenance_code: PortalMaintenanceCode | string | null;
  maintenance_scope: "global" | "broker" | string | null;
  /** A "pronto" portal: no connector yet. */
  coming_soon: boolean;
  supported_subtypes: string[];
  subtypes: PortalSubtypeRef[];
  requires_credentials: boolean;
  has_credentials: boolean;
  credential: PortalCredential | null;
  input_fields: PortalInputField[];
}

export interface PortalRunStep {
  /** Internal step id (`open_login`, `fill_username`…) — never shown raw. */
  name: string;
  /** Spanish, server-side: what the step did ("Abrir el portal"). */
  label?: string | null;
  op: string | null;
  ok: boolean;
  /** Spanish, sanitised (never credentials, never HTML). */
  detail: string | null;
  at: string | null;
  elapsed_ms: number | null;
}

/** Decimal strings on the wire (pydantic `Decimal`). */
export interface PortalQuote {
  id: number;
  insurer_id: number | null;
  insurer_name: string | null;
  plan_name: string | null;
  premium_uf: string | number | null;
  premium_clp: number | null;
  deductible: string | null;
  coverages: unknown[] | Record<string, unknown> | null;
  proposal_id: number | null;
}

export interface PortalRunAlternative {
  kind: AlternativeKind | string;
  available: boolean;
  /** Spanish, server-side. */
  reason: string | null;
  portal: string | null;
  portal_name: string | null;
  case_file_id: number | null;
}

export interface PortalRunSummary {
  id: number;
  portal: string;
  portal_name: string;
  status: PortalRunStatus;
  error_kind: PortalErrorKind | null;
  /** = `error_kind` when failed (the frontend's message key); else null. */
  user_message_key: PortalErrorKind | string | null;
  case_file_id: number | null;
  insurance_subtype_id: number | null;
  insurance_subtype_code: string | null;
  /** Optional: the subtype's name ("Vehículos particulares"). */
  insurance_subtype_name?: string | null;
  requested_by_user_id: number | null;
  input: Record<string, unknown>;
  quote_count: number;
  connector_version: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  can_retry: boolean;
  can_cancel: boolean;
  /**
   * The run is queued but nothing will ever execute it in this environment
   * (RPA disabled): render "No se ejecutará", never "En cola" forever.
   */
  will_not_run?: boolean;
  /** Spanish, server-side: why it will not run. */
  will_not_run_reason?: string | null;
}

export interface PortalRun extends PortalRunSummary {
  steps: PortalRunStep[];
  quotes: PortalQuote[];
  screenshot_document_id: number | null;
  screenshot_url: string | null;
  alternatives: PortalRunAlternative[];
}

export interface PortalRunPage {
  items: PortalRunSummary[];
  total: number;
  limit: number;
  offset: number;
}

export interface PortalRunCreate {
  portal: string;
  insurance_subtype_id: number;
  case_file_id?: number | null;
  input: Record<string, unknown>;
}

export interface PortalCredentialWrite {
  username: string;
  /** Omit to keep the stored password. */
  password?: string | null;
}

export interface PortalRunListParams {
  case_file_id?: number;
  portal?: string;
  status?: PortalRunStatus;
  limit?: number;
  offset?: number;
}

/** 422 body of the portal endpoints: `detail={code, message, errors?…}`. */
export interface PortalApiErrorDetail {
  code?: string;
  message?: string;
  errors?: Array<{ field: string; message: string }>;
  [key: string]: unknown;
}

export function isInFlight(status: PortalRunStatus | string | null | undefined): boolean {
  return status === "queued" || status === "running";
}

/** In flight AND actually going to run — the polling and spinner condition. */
export function runIsLive(
  run: Pick<PortalRunSummary, "status" | "will_not_run"> | null | undefined,
): boolean {
  return !!run && isInFlight(run.status) && !run.will_not_run;
}

/** Pull the structured 422 detail out of an axios error (or `null`). */
export function portalErrorDetail(error: unknown): PortalApiErrorDetail | null {
  const detail = (error as { response?: { data?: { detail?: unknown } } } | null)?.response
    ?.data?.detail;
  if (detail && typeof detail === "object" && !Array.isArray(detail)) {
    return detail as PortalApiErrorDetail;
  }
  return null;
}

const LIST_POLL_MS = 4000;
const DETAIL_POLL_MS = 2500;

// --- Portals + credentials -------------------------------------------------------

export function usePortals(enabled = true) {
  return useQuery({
    queryKey: qk.portalSearches.portals,
    enabled,
    queryFn: async () => (await api.get<Portal[]>("/portal-searches/portals")).data,
  });
}

export function useSavePortalCredentials(portal: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload: PortalCredentialWrite) =>
      (
        await api.put<PortalCredential>(
          `/portal-searches/credentials/${encodeURIComponent(portal)}`,
          payload,
        )
      ).data,
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.portalSearches.portals });
    },
  });
}

export function useDeletePortalCredentials(portal: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async () => {
      await api.delete(`/portal-searches/credentials/${encodeURIComponent(portal)}`);
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.portalSearches.portals });
    },
  });
}

// --- Runs ---------------------------------------------------------------------------

export function usePortalRuns(params: PortalRunListParams = {}, enabled = true) {
  return useQuery({
    queryKey: qk.portalSearches.list(params),
    enabled,
    queryFn: async () =>
      (await api.get<PortalRunPage>("/portal-searches", { params })).data,
    refetchInterval: (query) =>
      query.state.data?.items.some((run) => runIsLive(run)) ? LIST_POLL_MS : false,
  });
}

export function usePortalRun(runId: number | null | undefined) {
  return useQuery({
    queryKey: qk.portalSearches.detail(runId ?? 0),
    enabled: !!runId,
    queryFn: async () => (await api.get<PortalRun>(`/portal-searches/${runId}`)).data,
    refetchInterval: (query) => (runIsLive(query.state.data) ? DETAIL_POLL_MS : false),
  });
}

function useRunMutationSuccess() {
  const qc = useQueryClient();
  return (run: PortalRun) => {
    qc.setQueryData(qk.portalSearches.detail(run.id), run);
    void qc.invalidateQueries({ queryKey: qk.portalSearches.lists() });
    void qc.invalidateQueries({ queryKey: qk.portalSearches.portals });
  };
}

export function useCreatePortalRun() {
  const onSuccess = useRunMutationSuccess();
  return useMutation({
    mutationFn: async (payload: PortalRunCreate) =>
      (await api.post<PortalRun>("/portal-searches", payload)).data,
    onSuccess,
  });
}

/** Queues a NEW run cloned from a failed/cancelled one (idempotent server-side). */
export function useRetryPortalRun() {
  const onSuccess = useRunMutationSuccess();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (runId: number) =>
      (await api.post<PortalRun>(`/portal-searches/${runId}/retry`)).data,
    onSuccess: (run, sourceId) => {
      onSuccess(run);
      void qc.invalidateQueries({ queryKey: qk.portalSearches.detail(sourceId) });
      // The failure's bell entry is closed by the retry.
      void qc.invalidateQueries({ queryKey: qk.notifications.all });
    },
  });
}

export function useCancelPortalRun() {
  const onSuccess = useRunMutationSuccess();
  return useMutation({
    mutationFn: async (runId: number) =>
      (await api.post<PortalRun>(`/portal-searches/${runId}/cancel`)).data,
    onSuccess,
  });
}
