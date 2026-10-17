/**
 * Mantenedor building blocks (lane F3): the page header with its way back to
 * the console home, the explicit label/tone maps for every platform enum (no
 * dynamic i18n keys), a JSON viewer, a pager and the permission guards.
 *
 * Nothing here calls a broker endpoint: a platform user has no broker, and
 * every broker route answers 403 for them.
 */
import * as React from "react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { ArrowLeft, ChevronLeft, ChevronRight } from "lucide-react";
import type { BadgeProps } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { PageHeader } from "@/components/common/PageHeader";
import { useCan } from "@/lib/permissions";
import type {
  ChannelStatus,
  EventProcessing,
  InstructionKey,
  JobStatus,
  OnboardingStatus,
  PaymentChannel,
  ProviderAccountStatus,
  ProviderEnvironment,
  ProviderOwner,
  SecretKey,
} from "@/api/platform";
import type { Guard } from "@/pages/settings/components/shared";

type Tone = NonNullable<BadgeProps["variant"]>;

// --- explicit label / tone maps ---------------------------------------------------

export const ONBOARDING_LABEL: Record<OnboardingStatus, string> = {
  not_contacted: "platform:onboarding.not_contacted",
  in_conversation: "platform:onboarding.in_conversation",
  onboarding: "platform:onboarding.onboarding",
  sandbox: "platform:onboarding.sandbox",
  active: "platform:onboarding.active",
  suspended: "platform:onboarding.suspended",
};
export const ONBOARDING_TONE: Record<OnboardingStatus, Tone> = {
  not_contacted: "muted",
  in_conversation: "neutral",
  onboarding: "warn",
  sandbox: "warn",
  active: "success",
  suspended: "danger",
};

export const CHANNEL_LABEL: Record<PaymentChannel, string> = {
  webpay: "platform:channel.webpay",
  oneclick: "platform:channel.oneclick",
  bank_transfer: "platform:channel.bank_transfer",
  direct_debit: "platform:channel.direct_debit",
};

export const CHANNEL_STATUS_LABEL: Record<ChannelStatus, string> = {
  pending: "platform:channelStatus.pending",
  sandbox: "platform:channelStatus.sandbox",
  active: "platform:channelStatus.active",
  suspended: "platform:channelStatus.suspended",
};
export const CHANNEL_STATUS_TONE: Record<ChannelStatus, Tone> = {
  pending: "neutral",
  sandbox: "warn",
  active: "success",
  suspended: "danger",
};

export const OWNER_LABEL: Record<ProviderOwner, string> = {
  radal_mall: "platform:owner.radal_mall",
  insurer: "platform:owner.insurer",
};

export const ENVIRONMENT_LABEL: Record<ProviderEnvironment, string> = {
  sandbox: "platform:environment.sandbox",
  production: "platform:environment.production",
};
export const ENVIRONMENT_TONE: Record<ProviderEnvironment, Tone> = {
  sandbox: "warn",
  production: "brand",
};

export const ACCOUNT_STATUS_LABEL: Record<ProviderAccountStatus, string> = {
  draft: "platform:accountStatus.draft",
  active: "platform:accountStatus.active",
  disabled: "platform:accountStatus.disabled",
};
export const ACCOUNT_STATUS_TONE: Record<ProviderAccountStatus, Tone> = {
  draft: "neutral",
  active: "success",
  disabled: "muted",
};

export const EVENT_STATUS_LABEL: Record<EventProcessing, string> = {
  received: "platform:eventStatus.received",
  processed: "platform:eventStatus.processed",
  ignored: "platform:eventStatus.ignored",
  failed: "platform:eventStatus.failed",
};
/** Provider webhook `event_type` (backend `infer_event_type`): explicit map. */
export const EVENT_TYPE_LABEL: Record<string, string> = {
  payment: "platform:events.type.payment",
  activation: "platform:events.type.activation",
};
export const EVENT_STATUS_TONE: Record<EventProcessing, Tone> = {
  received: "neutral",
  processed: "success",
  ignored: "muted",
  failed: "danger",
};

export const JOB_STATUS_LABEL: Record<JobStatus, string> = {
  running: "platform:jobStatus.running",
  succeeded: "platform:jobStatus.succeeded",
  failed: "platform:jobStatus.failed",
  skipped: "platform:jobStatus.skipped",
};
export const JOB_STATUS_TONE: Record<JobStatus, Tone> = {
  running: "brand",
  succeeded: "success",
  failed: "danger",
  skipped: "muted",
};

export const INSTRUCTION_LABEL: Record<InstructionKey, string> = {
  portal_url: "platform:instruction.portal_url",
  bank_name: "platform:instruction.bank_name",
  account_type: "platform:instruction.account_type",
  account_number: "platform:instruction.account_number",
  account_holder: "platform:instruction.account_holder",
  account_rut: "platform:instruction.account_rut",
  transfer_email: "platform:instruction.transfer_email",
  receipt_email: "platform:instruction.receipt_email",
  notes: "platform:instruction.notes",
};

export const SECRET_LABEL: Record<SecretKey, string> = {
  api_key: "platform:providerAccounts.secret.api_key",
  password: "platform:providerAccounts.secret.password",
  hmac_key: "platform:providerAccounts.secret.hmac_key",
  public_key_pem: "platform:providerAccounts.secret.public_key_pem",
  jwe_alg: "platform:providerAccounts.secret.jwe_alg",
  jwe_enc: "platform:providerAccounts.secret.jwe_enc",
  signature_fields: "platform:providerAccounts.secret.signature_fields",
};

/** Registered job names (`app.jobs.JOBS`) → Spanish label. Unknown names render as-is. */
export const JOB_LABEL: Record<string, string> = {
  noop: "platform:jobs.label.noop",
  "mail.poll": "platform:jobs.label.mail_poll",
  "fx.daily": "platform:jobs.label.fx_daily",
  "collection.clock": "platform:jobs.label.collection_clock",
  "notifications.expire": "platform:jobs.label.notifications_expire",
  "payments.sync": "platform:jobs.label.payments_sync",
  "payments.payouts": "platform:jobs.label.payments_payouts",
  "rpa.run": "platform:jobs.label.rpa_run",
  "collection.notices": "platform:jobs.label.collection_notices",
  "collection.inquiries": "platform:jobs.label.collection_inquiries",
};

/** Webhook URL kinds (`services.payments.accounts.WEBHOOK_KINDS`). */
export const WEBHOOK_KIND_LABEL: Record<string, string> = {
  payment: "platform:providerAccounts.webhookKind.payment",
  activation: "platform:providerAccounts.webhookKind.activation",
};

/** A label from an explicit map, falling back to the raw value (never a raw key). */
export function labelFrom(
  t: (key: string) => string,
  map: Record<string, string>,
  value: string | null | undefined,
  fallback = "—",
): string {
  if (!value) return fallback;
  const key = map[value];
  return key ? t(key) : value;
}

// --- permission guards --------------------------------------------------------------

/** Platform.Create / Edit / Manage, each with the Spanish reason a disabled control shows. */
export function usePlatformGuards(): { create: Guard; edit: Guard; manage: Guard } {
  const { t } = useTranslation("platform");
  const create = useCan("Platform", "Create");
  const edit = useCan("Platform", "Edit");
  const manage = useCan("Platform", "Manage");
  return {
    create: { allowed: create.allowed, reason: t("guard.noCreate") },
    edit: { allowed: edit.allowed, reason: t("guard.noEdit") },
    manage: { allowed: manage.allowed, reason: t("guard.noManage") },
  };
}

// --- layout -------------------------------------------------------------------------

export function PlatformHeader({
  title,
  subtitle,
  actions,
}: {
  title: React.ReactNode;
  subtitle?: React.ReactNode;
  actions?: React.ReactNode;
}) {
  const { t } = useTranslation("platform");
  return (
    <PageHeader
      eyebrow={
        <Link
          to="/platform"
          className="inline-flex min-h-8 items-center gap-1.5 rounded-sm text-ink-3 transition-[color] duration-150 ease-out hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <ArrowLeft className="h-3.5 w-3.5" />
          {t("back")}
        </Link>
      }
      title={title}
      subtitle={subtitle}
      actions={actions}
    />
  );
}

/** Pretty-printed JSON, scrolling inside its own well. */
export function JsonBlock({ value, empty }: { value: unknown; empty: string }) {
  if (value === null || value === undefined || (typeof value === "object" && Object.keys(value).length === 0)) {
    return <p className="text-caption text-ink-3">{empty}</p>;
  }
  return (
    <pre className="max-h-[50vh] overflow-auto rounded-lg border border-line bg-paper-2 p-3 font-mono text-[12px] leading-5 text-ink-2">
      {JSON.stringify(value, null, 2)}
    </pre>
  );
}

/** Previous / next for an `{items,total,limit,offset}` page. */
export function Pager({
  total,
  limit,
  offset,
  onOffset,
}: {
  total: number;
  limit: number;
  offset: number;
  onOffset: (next: number) => void;
}) {
  const { t } = useTranslation("platform");
  if (total <= limit && offset === 0) return null;
  const from = total === 0 ? 0 : offset + 1;
  const to = Math.min(offset + limit, total);
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 pt-3">
      <span className="text-caption tabular-nums text-ink-3">
        {t("common.page", { from, to, total })}
      </span>
      <div className="flex gap-2">
        <Button
          variant="secondary"
          size="sm"
          className="min-h-9"
          disabled={offset === 0}
          onClick={() => onOffset(Math.max(0, offset - limit))}
        >
          <ChevronLeft />
          {t("common.previous")}
        </Button>
        <Button
          variant="secondary"
          size="sm"
          className="min-h-9"
          disabled={offset + limit >= total}
          onClick={() => onOffset(offset + limit)}
        >
          {t("common.next")}
          <ChevronRight />
        </Button>
      </div>
    </div>
  );
}

/** Seconds between two ISO timestamps, or null. */
export function durationSeconds(start: string | null, end: string | null): number | null {
  if (!start || !end) return null;
  const ms = new Date(end).getTime() - new Date(start).getTime();
  return Number.isFinite(ms) && ms >= 0 ? Math.round(ms / 100) / 10 : null;
}
