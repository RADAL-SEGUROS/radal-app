/**
 * The pending-action card — the human half of rule 6 (suggest -> human
 * confirm -> commit).
 *
 * Confirmar renders from `action.allowed`, computed server-side for the
 * caller; when `false` it renders disabled WITH the permission reason —
 * never hidden (no dead buttons). The server re-checks the real RBAC gate on
 * confirm regardless. Descartar needs no privilege.
 */
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { ArrowUpRight, CircleCheck, CircleX, PenLine, TriangleAlert } from "lucide-react";
import type { AgentAction } from "@/api/ai";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { DisabledHint } from "@/pages/proposals/shared";
import { cn } from "@/lib/utils";

function formatArg(value: unknown): string {
  if (value === true) return "✓";
  if (value === false) return "—";
  if (Array.isArray(value)) return value.map((v) => String(v)).join(", ");
  if (value !== null && typeof value === "object") return JSON.stringify(value);
  return String(value);
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
  const { t } = useTranslation("agent");

  const args = Object.entries(action.arguments ?? {}).filter(
    ([, value]) => value !== null && value !== undefined && value !== "",
  );

  const permissionReason = action.allowed
    ? null
    : t("action.needsPermission", {
        action: t(`rbacActions.${action.action}`, { defaultValue: action.action }),
        module: t(`modules.${action.module}`, { defaultValue: action.module }),
      });

  return (
    <div className="ms-9 max-w-[560px] rounded-card bg-bg-surface p-4 shadow-elev">
      {/* Header: Spanish tool label + status. */}
      <div className="flex items-center gap-2">
        <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-sm bg-brand-soft text-brand-deep">
          <PenLine className="h-[15px] w-[15px]" strokeWidth={1.5} aria-hidden />
        </span>
        <span className="min-w-0 flex-1 truncate text-label font-medium text-text-primary">
          {t(`tools.${action.tool}`, { defaultValue: action.tool })}
        </span>
        <Badge variant={STATUS_BADGE[action.status]}>{t(`action.${action.status}`)}</Badge>
      </div>

      {action.summary ? (
        <p className="mt-2 text-body text-text-secondary [text-wrap:pretty]">{action.summary}</p>
      ) : null}

      {/* Two-column argument table: label per known key, monospace values. */}
      {args.length > 0 ? (
        <dl className="mt-3 grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1 rounded-lg bg-bg-recessed px-3 py-2.5">
          {args.map(([key, value]) => (
            <div key={key} className="contents">
              <dt className="whitespace-nowrap font-mono text-mono-sm uppercase leading-6 text-text-muted">
                {t(`args.${key}`, { defaultValue: key })}
              </dt>
              <dd className="break-words font-mono text-mono leading-6 text-text-primary">
                {formatArg(value)}
              </dd>
            </div>
          ))}
        </dl>
      ) : null}

      {/* Footer per status. */}
      {action.status === "proposed" ? (
        <div className="mt-3 flex items-center gap-2">
          <DisabledHint hint={permissionReason}>
            <Button
              size="sm"
              disabled={!action.allowed || isMutating}
              onClick={() => onConfirm(action)}
            >
              {t("action.confirm")}
            </Button>
          </DisabledHint>
          <Button
            size="sm"
            variant="ghost"
            disabled={isMutating}
            onClick={() => onDiscard(action)}
          >
            {t("action.discard")}
          </Button>
        </div>
      ) : (
        <ResolvedRow action={action} />
      )}
    </div>
  );
}

function ResolvedRow({ action }: { action: AgentAction }) {
  const { t } = useTranslation("agent");
  if (action.status === "confirmed") {
    return (
      <div className="mt-3 flex flex-wrap items-center gap-x-2 gap-y-1 text-caption text-[var(--pos-text)]">
        <CircleCheck className="h-4 w-4 shrink-0" strokeWidth={1.5} aria-hidden />
        <span className="[text-wrap:pretty]">{action.result?.detail ?? t("action.confirmed")}</span>
        {action.result?.url ? (
          <Link
            to={action.result.url}
            className="inline-flex items-center gap-0.5 font-medium text-brand underline-offset-4 hover:text-brand-deep hover:underline"
          >
            {t("action.open")}
            <ArrowUpRight className="h-3.5 w-3.5" strokeWidth={1.5} aria-hidden />
          </Link>
        ) : null}
      </div>
    );
  }
  if (action.status === "failed") {
    return (
      <div className="mt-3 flex items-center gap-2 text-caption text-[var(--neg-text)]">
        <TriangleAlert className="h-4 w-4 shrink-0" strokeWidth={1.5} aria-hidden />
        <span className="[text-wrap:pretty]">{action.error ?? t("action.failed")}</span>
      </div>
    );
  }
  return (
    <div className={cn("mt-3 flex items-center gap-2 text-caption text-text-muted")}>
      <CircleX className="h-4 w-4 shrink-0" strokeWidth={1.5} aria-hidden />
      <span>{t("action.discarded")}</span>
    </div>
  );
}
