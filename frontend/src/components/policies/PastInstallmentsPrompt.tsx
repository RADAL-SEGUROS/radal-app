/**
 * "Esta póliza tiene N cuotas con vencimiento pasado (UF X). ¿Ya están
 * pagadas?" — review r2 B2/C4, owner decision D1.
 *
 * A policy uploaded (or typed) late arrives with cuotas whose due date has
 * already passed. Until the broker answers, those cuotas raise no "Riesgo de
 * término" and count in no overdue KPI (server-side). The same question is
 * asked twice, with the same component:
 *
 *   - in the upload dialog, right after the 201 (`variant="step"`, with
 *     **Decidir después**);
 *   - on the policy page, as a banner while `past_installments_pending`
 *     (`variant="banner"`).
 *
 * **Sí, marcar pagadas** registers one full payment per cuota through the
 * ledger (`paid_on = due_date`); **No, quedan vencidas** lets the alerts
 * resume. The answer needs `Collections.Edit`; without it the buttons render
 * disabled WITH the reason (no dead buttons).
 */
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { CalendarClock, CheckCircle2, Clock, RefreshCw, XCircle } from "lucide-react";

import { Button } from "@/components/ui/button";
import { DisabledHint, apiError } from "@/components/common/kit";
import { useCan } from "@/lib/permissions";
import { formatDate, formatUF } from "@/lib/format";
import { cn } from "@/lib/utils";
import { num } from "@/api/types";
import {
  useDecidePastInstallments,
  type PastInstallmentsDecision,
} from "@/api/policies";

export function PastInstallmentsPrompt({
  policyId,
  planId,
  count,
  totalUf,
  firstDue,
  lastDue,
  variant,
  onDecided,
  onLater,
}: {
  policyId: number;
  planId: number | null;
  /** Null when the server only flagged "pending" without a count. */
  count: number | null;
  totalUf?: string | number | null;
  firstDue?: string | null;
  lastDue?: string | null;
  variant: "step" | "banner";
  onDecided?: (decision: PastInstallmentsDecision) => void;
  /** Only on the upload step: leave it for the policy page's banner. */
  onLater?: () => void;
}) {
  const { t } = useTranslation("policies");
  const canEdit = useCan("Collections", "Edit");
  const decide = useDecidePastInstallments(policyId);

  const amount = num(totalUf ?? null);
  const amountText = amount !== null ? formatUF(amount) : null;
  const title =
    count === null
      ? t("pastInstallments.titleUnknown")
      : amountText
        ? t("pastInstallments.title", { count, amount: amountText })
        : t("pastInstallments.titleNoAmount", { count });

  const blocked =
    !canEdit.isLoading && !canEdit.allowed
      ? t("pastInstallments.noPermission")
      : planId === null
        ? t("pastInstallments.noPlan")
        : null;

  const answer = (decision: PastInstallmentsDecision) => {
    if (planId === null) return;
    decide.mutate(
      { planId, decision },
      {
        onSuccess: () => {
          toast.success(
            decision === "paid" ? t("pastInstallments.donePaid") : t("pastInstallments.doneUnpaid"),
          );
          onDecided?.(decision);
        },
        onError: (error) => toast.error(apiError(error, t("pastInstallments.error"))),
      },
    );
  };

  const pending = decide.isPending ? decide.variables?.decision : null;

  return (
    <div
      role={variant === "banner" ? "status" : undefined}
      className={cn(
        "flex flex-col gap-3 rounded-[12px] border border-warn-line bg-warn-soft/40 p-4",
        variant === "banner" && "shadow-elev",
      )}
    >
      <div className="flex items-start gap-2.5">
        <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-warn-soft text-warn-text">
          <CalendarClock className="h-4 w-4" />
        </span>
        <div className="min-w-0">
          <div className="text-body font-medium text-ink [text-wrap:pretty]">{title}</div>
          {firstDue && lastDue ? (
            <p className="mt-0.5 text-caption tabular-nums text-ink-3">
              {firstDue === lastDue
                ? t("pastInstallments.dueOn", { date: formatDate(firstDue, "dd-MM-yyyy") })
                : t("pastInstallments.dueRange", {
                    first: formatDate(firstDue, "dd-MM-yyyy"),
                    last: formatDate(lastDue, "dd-MM-yyyy"),
                  })}
            </p>
          ) : null}
          <p className="mt-1 text-caption text-ink-2 [text-wrap:pretty]">
            {t("pastInstallments.hint")}
          </p>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <DisabledHint hint={blocked}>
          <Button
            size="sm"
            variant="secondary"
            disabled={!!blocked || decide.isPending}
            onClick={() => answer("paid")}
          >
            {pending === "paid" ? (
              <RefreshCw className="h-4 w-4 animate-spin motion-reduce:animate-none" />
            ) : (
              <CheckCircle2 className="h-4 w-4" />
            )}
            {t("pastInstallments.paid")}
          </Button>
        </DisabledHint>
        <DisabledHint hint={blocked}>
          <Button
            size="sm"
            variant="secondary"
            disabled={!!blocked || decide.isPending}
            onClick={() => answer("unpaid")}
          >
            {pending === "unpaid" ? (
              <RefreshCw className="h-4 w-4 animate-spin motion-reduce:animate-none" />
            ) : (
              <XCircle className="h-4 w-4" />
            )}
            {t("pastInstallments.unpaid")}
          </Button>
        </DisabledHint>
        {onLater ? (
          <Button size="sm" variant="ghost" disabled={decide.isPending} onClick={onLater}>
            <Clock className="h-4 w-4" />
            {t("pastInstallments.later")}
          </Button>
        ) : null}
      </div>
      {blocked ? <span className="text-caption text-ink-3">{blocked}</span> : null}
    </div>
  );
}
