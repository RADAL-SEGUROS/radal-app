/**
 * Explicit i18n key maps for the portal-search page (namespace `rpa`).
 *
 * No dynamic keys: every enum the server sends goes through one of these maps,
 * with an explicit fallback, so a new value renders a sensible label instead
 * of a raw `rpa:status.whatever` on screen.
 */
import type { BadgeProps } from "@/components/ui/badge";
import type { CredentialStatus, PortalErrorKind, PortalRunStatus } from "@/api/rpa";

type Variant = NonNullable<BadgeProps["variant"]>;

export function own<T>(map: Record<string, T>, key: string | null | undefined): T | null {
  return key && Object.prototype.hasOwnProperty.call(map, key) ? map[key] : null;
}

export const RUN_STATUS_LABEL_KEY: Record<PortalRunStatus, string> = {
  queued: "rpa:status.queued",
  running: "rpa:status.running",
  succeeded: "rpa:status.succeeded",
  failed: "rpa:status.failed",
  cancelled: "rpa:status.cancelled",
};

export const RUN_STATUS_TONE: Record<PortalRunStatus, Variant> = {
  queued: "neutral",
  running: "action",
  succeeded: "success",
  failed: "danger",
  cancelled: "muted",
};

/** The user-facing explanation per `error_kind` (06-rpa §4). */
export const ERROR_MESSAGE_KEY: Record<PortalErrorKind, string> = {
  credentials_missing: "rpa:errors.credentials_missing",
  login_failed: "rpa:errors.login_failed",
  layout_changed: "rpa:errors.layout_changed",
  timeout: "rpa:errors.timeout",
  captcha: "rpa:errors.captcha",
  no_results: "rpa:errors.no_results",
  portal_unavailable: "rpa:errors.portal_unavailable",
  unexpected: "rpa:errors.unexpected",
};

export function errorMessageKey(kind: string | null | undefined): string {
  return own(ERROR_MESSAGE_KEY, kind) ?? ERROR_MESSAGE_KEY.unexpected;
}

/** Field labels for the connector's `input_fields` (VEH-01 today). */
export const FIELD_LABEL_KEY: Record<string, string> = {
  plate: "rpa:fields.plate",
  brand: "rpa:fields.brand",
  model: "rpa:fields.model",
  year: "rpa:fields.year",
  use: "rpa:fields.use",
  commune: "rpa:fields.commune",
  insured_rut: "rpa:fields.insured_rut",
  start_date: "rpa:fields.start_date",
};

export const FIELD_PLACEHOLDER_KEY: Record<string, string> = {
  plate: "rpa:placeholders.plate",
  brand: "rpa:placeholders.brand",
  model: "rpa:placeholders.model",
  year: "rpa:placeholders.year",
  use: "rpa:placeholders.use",
  commune: "rpa:placeholders.commune",
  insured_rut: "rpa:placeholders.insured_rut",
  start_date: "rpa:placeholders.start_date",
};

/** Option labels for select fields (`use` = particular | commercial). */
export const OPTION_LABEL_KEY: Record<string, Record<string, string>> = {
  use: {
    particular: "rpa:use.particular",
    commercial: "rpa:use.commercial",
  },
};

export const STEP_OP_LABEL_KEY: Record<string, string> = {
  goto: "rpa:ops.goto",
  fill: "rpa:ops.fill",
  click: "rpa:ops.click",
  select: "rpa:ops.select",
  wait_for: "rpa:ops.wait_for",
  extract_table: "rpa:ops.extract_table",
  check: "rpa:ops.check",
  run: "rpa:ops.run",
};

export function stepOpLabelKey(op: string | null | undefined): string {
  return own(STEP_OP_LABEL_KEY, op) ?? "rpa:ops.other";
}

export const CREDENTIAL_STATUS_LABEL_KEY: Record<CredentialStatus, string> = {
  unverified: "rpa:credentials.status.unverified",
  valid: "rpa:credentials.status.valid",
  invalid: "rpa:credentials.status.invalid",
};

export const CREDENTIAL_STATUS_TONE: Record<CredentialStatus, Variant> = {
  unverified: "neutral",
  valid: "success",
  invalid: "danger",
};

export function humanizeField(name: string): string {
  return name.replace(/_/g, " ");
}
