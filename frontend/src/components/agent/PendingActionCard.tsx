/**
 * The pending-action card — the human half of rule 6 (suggest -> human
 * confirm -> commit).
 *
 * Confirmar renders from `action.allowed`, computed server-side for the
 * caller; when `false` it renders disabled WITH the permission reason —
 * never hidden (no dead buttons). The server re-checks the real RBAC gate on
 * confirm regardless. Descartar needs no privilege. Nothing here ever
 * auto-confirms.
 *
 * Generic by construction: the header label comes from the explicit
 * `TOOL_LABEL_KEY` map (unknown tools fall back to a generic label — never
 * the tool's raw id), the sentence is the server's Spanish `summary`, and the body is the
 * server-built `display` rows (review r2 B6/C6: Spanish labels, Chilean
 * money and dates, the cuota NUMBER — never an internal id or an enum). An
 * action stored before `display` existed falls back to its arguments, with
 * internal ids hidden and known enums, numbers and dates humanised.
 *
 * Confirmed results: an ABSOLUTE `result.url` (a public share link, a Forpay
 * checkout) opens in a new tab; an in-app path routes inside the SPA. A share
 * link also offers WhatsApp + copy.
 */
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import {
  ArrowUpRight,
  CircleCheck,
  CircleX,
  ExternalLink,
  MessageCircle,
  PenLine,
  TriangleAlert,
} from "lucide-react";
import type { AgentAction } from "@/api/ai";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { CopyButton, DisabledHint } from "@/components/common/kit";
import {
  ACTION_STATUS_LABEL_KEY,
  ARG_LABEL_KEY,
  MODULE_LABEL_KEY,
  RBAC_ACTION_LABEL_KEY,
  humanize,
  lookup,
  toolLabelKey,
} from "@/components/agent/labels";
import { cn } from "@/lib/utils";
import { formatNumber } from "@/lib/format";
import { PAYMENT_MODE_LABEL, enumLabel } from "@/i18n/labels";

type TFn = (key: string, opts?: Record<string, unknown>) => string;

function argLabel(t: TFn, key: string): string {
  const labelKey = lookup(ARG_LABEL_KEY, key);
  return labelKey ? t(labelKey) : humanize(key);
}

/** Internal ids are never shown to the broker (C6): `installment_id: 68` is not "cuota 68". */
function isInternalId(key: string): boolean {
  return key === "id" || key.endsWith("_id") || key.endsWith("_ids");
}

/** Argument keys whose value is a payment-method enum. */
const PAYMENT_METHOD_ARGS = new Set(["method", "payment_method"]);

const ISO_DATE = /^(\d{4})-(\d{2})-(\d{2})(?:$|T)/;

function formatArg(t: TFn, value: unknown, key?: string): string {
  if (value === true) return t("common:units.si");
  if (value === false) return t("common:units.no");
  if (typeof value === "number") {
    return formatNumber(value, Number.isInteger(value) ? 0 : 2);
  }
  if (typeof value === "string") {
    if (key && PAYMENT_METHOD_ARGS.has(key)) return enumLabel(t, PAYMENT_MODE_LABEL, value);
    const date = ISO_DATE.exec(value);
    if (date) return `${date[3]}-${date[2]}-${date[1]}`;
    if (/^-?\d+\.\d+$/.test(value)) return formatNumber(Number(value), 2);
    return value;
  }
  if (Array.isArray(value)) return value.map((v) => formatArg(t, v, key)).join(", ");
  if (value !== null && typeof value === "object") {
    return Object.entries(value as Record<string, unknown>)
      .filter(([k, v]) => v !== null && v !== undefined && v !== "" && !isInternalId(k))
      .map(([k, v]) => `${argLabel(t, k)}: ${formatArg(t, v, k)}`)
      .join(" · ");
  }
  return String(value);
}

/** The rows the card shows: the server's `display`, else the humanised arguments. */
function cardRows(t: TFn, action: AgentAction): { key: string; label: string; value: string }[] {
  if (action.display && action.display.length > 0) {
    return action.display
      .filter((row) => row && row.label && row.value !== null && row.value !== undefined && row.value !== "")
      .map((row, index) => ({
        key: `${index}-${row.label}`,
        label: row.label,
        value: typeof row.value === "number" ? formatNumber(row.value, Number.isInteger(row.value) ? 0 : 2) : String(row.value),
      }));
  }
  return Object.entries(action.arguments ?? {})
    .filter(([key, value]) => value !== null && value !== undefined && value !== "" && !isInternalId(key))
    .map(([key, value]) => ({ key, label: argLabel(t, key), value: formatArg(t, value, key) }));
}

export function isAbsoluteUrl(url: string | null | undefined): boolean {
  return !!url && /^https?:\/\//i.test(url);
}

const STATUS_BADGE: Record<AgentAction["status"], "brand" | "success" | "muted" | "danger"> = {
  proposed: "brand",
  confirmed: "success",
  discarded: "muted",
  failed: "danger",
};

export function PendingActionCard({
  action,
  onConfirm,
  onDiscard,
  isMutating,
}: {
  action: AgentAction;
  onConfirm: (action: AgentAction) => void;
  onDiscard: (action: AgentAction) => void;
  /** Both buttons disable while either mutation is in flight. */
  isMutating: boolean;
}) {
  const { t } = useTranslation("dashboard");

  const rows = cardRows(t, action);

  const moduleKey = lookup(MODULE_LABEL_KEY, action.module);
  const actionKey = lookup(RBAC_ACTION_LABEL_KEY, action.action);
  const permissionReason = action.allowed
    ? null
    : t("agent.action.needsPermission", {
        action: actionKey ? t(actionKey) : action.action,
        module: moduleKey ? t(moduleKey) : action.module,
      });

  const toolLabel = t(toolLabelKey(action.tool));

  return (
    <div className="max-w-[560px] rounded-card border border-line bg-bone p-4 shadow-elev sm:ms-9">
      {/* Header: Spanish tool label + status. */}
      <div className="flex items-center gap-2">
        <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-sm bg-brand-soft text-brand-deep">
          <PenLine className="h-[15px] w-[15px]" strokeWidth={1.5} aria-hidden />
        </span>
        <span className="min-w-0 flex-1 truncate text-label font-medium text-ink">
          {toolLabel}
        </span>
        <Badge variant={STATUS_BADGE[action.status]}>
          {t(ACTION_STATUS_LABEL_KEY[action.status])}
        </Badge>
      </div>

      {action.summary ? (
        <p className="mt-2 text-body text-ink-2 [text-wrap:pretty]">{action.summary}</p>
      ) : null}

      {/* Two-column table: the server's Spanish `display` (or the humanised args). */}
      {rows.length > 0 ? (
        <dl className="mt-3 grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1 rounded-lg bg-bg-recessed px-3 py-2.5">
          {rows.map((row) => (
            <div key={row.key} className="contents">
              <dt className="whitespace-nowrap text-caption leading-6 text-ink-3">{row.label}</dt>
              <dd className="break-words text-body leading-6 tabular-nums text-ink">{row.value}</dd>
            </div>
          ))}
        </dl>
      ) : null}

      {/* Footer per status. */}
      {action.status === "proposed" ? (
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <DisabledHint hint={permissionReason}>
            <Button
              size="sm"
              className="min-h-10 sm:min-h-8"
              disabled={!action.allowed || isMutating}
              onClick={() => onConfirm(action)}
            >
              {t("agent.action.confirm")}
            </Button>
          </DisabledHint>
          <Button
            size="sm"
            variant="ghost"
            className="min-h-10 sm:min-h-8"
            disabled={isMutating}
            onClick={() => onDiscard(action)}
          >
            {t("agent.action.discard")}
          </Button>
        </div>
      ) : (
        <ResolvedRow action={action} />
      )}
    </div>
  );
}

/** Where a confirmed result points inside the app (or `null`). */
function internalPath(action: AgentAction): string | null {
  const result = action.result;
  if (!result) return null;
  if (result.entity_type === "portal_search" && result.entity_id) {
    return `/portal-search?tab=runs&run=${result.entity_id}`;
  }
  const url = typeof result.url === "string" ? result.url : null;
  if (url && !isAbsoluteUrl(url) && url.startsWith("/")) return url;
  return null;
}

function ResolvedRow({ action }: { action: AgentAction }) {
  const { t } = useTranslation("dashboard");
  if (action.status === "confirmed") {
    const result = action.result ?? {};
    const url = typeof result.url === "string" ? result.url : null;
    const external = url && isAbsoluteUrl(url) ? url : null;
    const inApp = internalPath(action);
    const whatsapp =
      typeof result.whatsapp_url === "string" && isAbsoluteUrl(result.whatsapp_url)
        ? result.whatsapp_url
        : null;
    const linkClass =
      "inline-flex min-h-8 items-center gap-1 font-medium text-brand underline-offset-4 hover:text-brand-deep hover:underline";
    return (
      <div className="mt-3 flex flex-col gap-2">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-caption text-pos-text">
          <CircleCheck className="h-4 w-4 shrink-0" strokeWidth={1.5} aria-hidden />
          <span className="[text-wrap:pretty]">{result.detail ?? t("agent.action.confirmed")}</span>
          {inApp ? (
            <Link to={inApp} className={linkClass}>
              {action.tool === "run_portal_search" ? t("agent.action.openRun") : t("agent.action.open")}
              <ArrowUpRight className="h-3.5 w-3.5" strokeWidth={1.5} aria-hidden />
            </Link>
          ) : null}
        </div>
        {external ? (
          <div className="flex flex-wrap items-center gap-2">
            <Button asChild size="sm" variant="secondary" className="min-h-10 sm:min-h-8">
              <a href={external} target="_blank" rel="noopener noreferrer">
                <ExternalLink className="h-3.5 w-3.5" strokeWidth={1.5} aria-hidden />
                {t("agent.action.openExternal")}
              </a>
            </Button>
            {whatsapp ? (
              <Button asChild size="sm" variant="secondary" className="min-h-10 sm:min-h-8">
                <a href={whatsapp} target="_blank" rel="noopener noreferrer">
                  <MessageCircle className="h-3.5 w-3.5" strokeWidth={1.5} aria-hidden />
                  {t("agent.action.whatsapp")}
                </a>
              </Button>
            ) : null}
            <CopyButton
              value={external}
              label={t("agent.action.copyLink")}
              className="min-h-10 sm:min-h-8"
            />
          </div>
        ) : null}
      </div>
    );
  }
  if (action.status === "failed") {
    return (
      <div className="mt-3 flex items-center gap-2 text-caption text-neg-text">
        <TriangleAlert className="h-4 w-4 shrink-0" strokeWidth={1.5} aria-hidden />
        <span className="[text-wrap:pretty]">{action.error ?? t("agent.action.failed")}</span>
      </div>
    );
  }
  return (
    <div className={cn("mt-3 flex items-center gap-2 text-caption text-ink-3")}>
      <CircleX className="h-4 w-4 shrink-0" strokeWidth={1.5} aria-hidden />
      <span>{t("agent.action.discarded")}</span>
    </div>
  );
}
