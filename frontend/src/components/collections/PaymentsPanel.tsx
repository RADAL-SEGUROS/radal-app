/**
 * The payments booked on one cuota, each with its evidence level — and the two
 * things a broker does to a payment: C1.5 Confirmar pago (evidence →
 * insurer_confirmed) and Revertir (with a reason; a payment is never deleted).
 *
 * "Confirmar pago" only appears on an active payment that is not yet confirmed:
 * without a payment registered there is nothing to confirm (team spec C1.5).
 */
import * as React from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { CheckCircle2, Loader2, Undo2 } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { DisabledHint, ErrorBanner, LoadingRows } from "@/components/common/kit";
import { PreviewDocButton } from "@/components/common/DownloadDocButton";
import { formatDate } from "@/lib/format";
import { useCan } from "@/lib/permissions";
import {
  useConfirmInstallmentPayment,
  useInstallmentPayments,
  useReverseInstallmentPayment,
  type InstallmentPaymentRead,
  type PaymentReversalReason,
} from "@/api/collections";
import {
  EVIDENCE_LEVEL_LABEL,
  EVIDENCE_LEVEL_TONE,
  METHOD_LABEL,
  PAYMENT_SOURCE_LABEL,
  REVERSAL_REASON_LABEL,
  labelOf,
  toneOf,
} from "@/components/collections/labels";
import { Field, NativeSelect, money } from "@/components/collections/shared";

const STRONG = new Set(["insurer_confirmed", "provider_confirmed", "settled"]);
const REASONS: PaymentReversalReason[] = ["error", "direct_debit_rejected", "chargeback", "other"];

export function EvidenceLevelBadge({ level }: { level: string | null | undefined }) {
  const { t } = useTranslation("collections");
  if (!level) return null;
  return (
    <Badge variant={toneOf(EVIDENCE_LEVEL_TONE, level)} className="px-1.5 py-0 text-[11px]">
      {labelOf(t, EVIDENCE_LEVEL_LABEL, level, t("evidenceLevel.unknown"))}
    </Badge>
  );
}

function ReverseForm({
  payment,
  onDone,
  onCancel,
}: {
  payment: InstallmentPaymentRead;
  onDone: () => void;
  onCancel: () => void;
}) {
  const { t } = useTranslation(["collections", "common"]);
  const reverse = useReverseInstallmentPayment();
  const [reason, setReason] = React.useState<PaymentReversalReason>("error");
  const [note, setNote] = React.useState("");

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      await reverse.mutateAsync({ paymentId: payment.id, reason, note: note.trim() || null });
      toast.success(t("collections:payments.reversed"));
      onDone();
    } catch {
      // ErrorBanner.
    }
  };

  return (
    <form onSubmit={(e) => void submit(e)} className="mt-3 flex flex-col gap-3 border-t border-line pt-3">
      <Field label={t("collections:payments.reverseReason")} htmlFor={`rev-reason-${payment.id}`}>
        <NativeSelect
          id={`rev-reason-${payment.id}`}
          value={reason}
          onChange={(e) => setReason(e.target.value as PaymentReversalReason)}
        >
          {REASONS.map((r) => (
            <option key={r} value={r}>
              {labelOf(t, REVERSAL_REASON_LABEL, r)}
            </option>
          ))}
        </NativeSelect>
      </Field>
      <Field label={t("collections:payments.reverseNote")} htmlFor={`rev-note-${payment.id}`}>
        <Textarea id={`rev-note-${payment.id}`} rows={2} value={note} onChange={(e) => setNote(e.target.value)} />
      </Field>
      <ErrorBanner error={reverse.error} />
      <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
        <Button type="button" variant="ghost" size="sm" onClick={onCancel} disabled={reverse.isPending}>
          {t("common:actions.cancel")}
        </Button>
        <Button type="submit" variant="destructive" size="sm" disabled={reverse.isPending}>
          {reverse.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Undo2 className="h-4 w-4" />}
          {t("collections:payments.reverseSubmit")}
        </Button>
      </div>
    </form>
  );
}

export function PaymentsPanel({ installmentId }: { installmentId: number }) {
  const { t } = useTranslation(["collections", "common"]);
  const payments = useInstallmentPayments(installmentId);
  const confirm = useConfirmInstallmentPayment();
  const canEdit = useCan("Collections", "Edit");
  const [reversing, setReversing] = React.useState<number | null>(null);
  const gate = canEdit.isLoading
    ? t("common:state.loading")
    : canEdit.allowed
      ? null
      : t("collections:sheet.noEditPermission");

  if (payments.isLoading) return <LoadingRows rows={2} />;
  if (payments.error) return <ErrorBanner error={payments.error} />;
  const items = payments.data ?? [];
  if (items.length === 0) {
    return <p className="text-caption text-ink-3">{t("collections:payments.empty")}</p>;
  }

  const hasActions = items.some((p) => p.status !== "reversed");
  return (
    <ul className="flex flex-col divide-y divide-line">
      {/* Why Confirmar / Revertir are disabled, visible (tooltips never open on touch). */}
      {gate && hasActions ? <li className="pb-2 text-caption text-ink-3">{gate}</li> : null}
      {items.map((p) => {
        const reversed = p.status === "reversed";
        const canConfirm = !reversed && !STRONG.has(p.evidence_level) && !p.confirmed_by_insurer;
        return (
          <li key={p.id} className="py-3 first:pt-0 last:pb-0">
            <div className="flex flex-wrap items-start justify-between gap-2">
              <div className="min-w-0">
                <div className={reversed ? "text-body font-medium text-ink-3 line-through" : "text-body font-medium text-ink"}>
                  <span className="tabular-nums">{money(p.amount, p.currency)}</span>
                  {p.currency !== "UF" ? (
                    <span className="ml-1.5 text-caption text-ink-3 tabular-nums">({money(p.amount_uf, "UF")})</span>
                  ) : null}
                </div>
                <div className="mt-0.5 text-caption text-ink-3">
                  {formatDate(p.paid_on)}
                  {p.method ? ` · ${labelOf(t, METHOD_LABEL, p.method, "")}` : ""}
                  {` · ${labelOf(t, PAYMENT_SOURCE_LABEL, p.source, "")}`}
                </div>
              </div>
              <div className="flex flex-wrap items-center gap-1">
                {reversed ? (
                  <Badge variant="muted" className="px-1.5 py-0 text-[11px]">
                    {t("collections:payments.reversedBadge")}
                  </Badge>
                ) : (
                  <EvidenceLevelBadge level={p.evidence_level} />
                )}
              </div>
            </div>
            {reversed && p.reversal_reason ? (
              <p className="mt-1 text-caption text-ink-3">
                {labelOf(t, REVERSAL_REASON_LABEL, p.reversal_reason)}
                {p.reversal_note ? ` — ${p.reversal_note}` : ""}
              </p>
            ) : null}
            {p.note ? <p className="mt-1 text-caption text-ink-2">{p.note}</p> : null}
            {!reversed ? (
              <div className="mt-2 flex flex-wrap gap-1.5">
                {p.receipt_document_id ? (
                  <PreviewDocButton documentId={p.receipt_document_id} label={t("collections:payments.receipt")} />
                ) : null}
                {canConfirm ? (
                  <DisabledHint hint={gate}>
                    <Button
                      variant="success"
                      size="sm"
                      disabled={!canEdit.allowed || confirm.isPending}
                      onClick={() =>
                        confirm.mutate(p.id, {
                          onSuccess: () => toast.success(t("collections:payments.confirmed")),
                        })
                      }
                    >
                      <CheckCircle2 className="h-4 w-4" />
                      {t("collections:payments.confirm")}
                    </Button>
                  </DisabledHint>
                ) : null}
                <DisabledHint hint={gate}>
                  <Button
                    variant="ghost"
                    size="sm"
                    disabled={!canEdit.allowed}
                    onClick={() => setReversing(reversing === p.id ? null : p.id)}
                  >
                    <Undo2 className="h-4 w-4" />
                    {t("collections:payments.reverse")}
                  </Button>
                </DisabledHint>
              </div>
            ) : null}
            {reversing === p.id ? (
              <ReverseForm payment={p} onDone={() => setReversing(null)} onCancel={() => setReversing(null)} />
            ) : null}
          </li>
        );
      })}
      {confirm.error ? (
        <li className="pt-3">
          <ErrorBanner error={confirm.error} />
        </li>
      ) : null}
    </ul>
  );
}
