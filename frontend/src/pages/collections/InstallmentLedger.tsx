/**
 * The instalment ledger.
 *
 * The rule every plan in the corpus obeys:
 *
 *     Σ installment.gross_amount_uf == policy gross premium
 *                                    + Σ endorsement.total_premium_delta_uf
 *
 * with the last instalment absorbing the rounding. The server validates it
 * before writing and answers 422 — so this table shows the three terms side by
 * side and marks the difference, instead of quietly hiding a mismatch behind a
 * total that "looks right".
 *
 * The running column is the cumulative Σ: where the ledger drifts is visible on
 * the exact row that drifts it.
 */
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { AlertTriangle, CheckCircle2, Coins, Wallet } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { cn } from "@/lib/utils";
import { formatDate } from "@/lib/format";
import { useCan } from "@/lib/permissions";
import { useCollectionStatus, useInstallments, useUpdateInstallment } from "@/api/collections";
import { useEndorsements } from "@/api/endorsements";
import { usePolicy } from "@/api/policies";
import { num, type CollectionInstallment } from "@/api/types";
import {
  DisabledHint,
  EmptyState,
  ErrorBanner,
  KeyValue,
  LoadingRows,
  MonoChip,
  Section,
  apiError,
  uf,
} from "@/pages/proposals/shared";
import { PostsaleBadge, offBy } from "@/pages/policies/shared";

const CLOSED: CollectionInstallment["status"][] = [
  "paid",
  "paid_late",
  "credited",
  "cancelled",
];

function today(): string {
  return new Date().toISOString().slice(0, 10);
}

function daysLate(dueDate: string | null, paidOn: string): number | null {
  if (!dueDate) return null;
  const diff = new Date(paidOn).getTime() - new Date(dueDate).getTime();
  return diff <= 0 ? 0 : Math.round(diff / 86_400_000);
}

export function InstallmentLedger({
  planId,
  policyId,
}: {
  planId: number;
  policyId: number;
}) {
  const { t } = useTranslation("postsale");
  const installments = useInstallments(planId);
  const collectionStatus = useCollectionStatus(planId);
  const policy = usePolicy(policyId);
  const endorsements = useEndorsements({ policy_id: policyId, limit: 100 });
  const update = useUpdateInstallment(planId);
  const canEdit = useCan("Collections", "Edit");

  const rows = [...(installments.data ?? [])].sort((a, b) => a.number - b.number);

  const scheduled = rows.reduce((sum, row) => sum + (num(row.gross_amount_uf) ?? 0), 0);
  const policyGross = num(policy.data?.total_premium_uf ?? null);
  const deltas = (endorsements.data?.items ?? [])
    .filter((e) => e.status === "issued" || e.status === "applied")
    .reduce((sum, e) => sum + (num(e.total_premium_delta_uf) ?? 0), 0);
  const expected = num(collectionStatus.data?.expected_total_uf ?? null) ?? (policyGross ?? 0) + deltas;
  const difference = scheduled - expected;
  const balances = collectionStatus.data?.balances ?? !offBy(scheduled, expected);

  const gate = canEdit.isLoading
    ? t("shared.loading")
    : canEdit.allowed
      ? null
      : t("installment.noPermission");

  const markPaid = async (row: CollectionInstallment) => {
    const paidOn = today();
    const late = daysLate(row.due_date, paidOn);
    try {
      await update.mutateAsync({
        number: row.number,
        status: late && late > 0 ? "paid_late" : "paid",
        paid_on: paidOn,
        days_late: late,
      });
      toast.success(t("shared.saved"));
    } catch (error) {
      toast.error(apiError(error, t("shared.notFound")));
    }
  };

  let running = 0;

  return (
    <Section
      title={t("installment.title")}
      description={t("installment.description")}
      actions={
        rows.length > 0 ? (
          balances ? (
            <Badge variant="success" className="gap-1">
              <CheckCircle2 className="h-3 w-3" />
              {t("installment.balance.ok")}
            </Badge>
          ) : (
            <Badge variant="danger" className="gap-1">
              <AlertTriangle className="h-3 w-3" />
              {t("installment.balance.mismatch")}
            </Badge>
          )
        ) : null
      }
    >
      <ErrorBanner error={installments.error} className="mb-3" />
      {installments.isLoading ? <LoadingRows rows={4} /> : null}

      {!installments.isLoading && rows.length === 0 ? (
        <EmptyState
          title={t("installment.empty")}
          hint={t("installment.emptyHint")}
          icon={<Coins className="h-6 w-6" />}
        />
      ) : null}

      {rows.length > 0 ? (
        <>
          <div className="mb-4 grid grid-cols-2 gap-4 md:grid-cols-5">
            <KeyValue label={t("installment.balance.scheduled")} value={uf(scheduled)} />
            <KeyValue
              label={t("installment.balance.policyGross")}
              value={uf(policy.data?.total_premium_uf ?? null)}
            />
            <KeyValue label={t("installment.balance.endorsementDeltas")} value={uf(deltas)} />
            <KeyValue label={t("installment.balance.expected")} value={uf(expected)} />
            <KeyValue
              label={t("installment.balance.difference")}
              value={uf(difference)}
              tone={balances ? "success" : "danger"}
            />
          </div>

          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>{t("installment.columns.number")}</TableHead>
                  <TableHead>{t("installment.columns.coupon")}</TableHead>
                  <TableHead>{t("installment.columns.dueDate")}</TableHead>
                  <TableHead>{t("installment.columns.gross")}</TableHead>
                  <TableHead>{t("installment.columns.running")}</TableHead>
                  <TableHead>{t("installment.columns.paidOn")}</TableHead>
                  <TableHead>{t("installment.columns.daysLate")}</TableHead>
                  <TableHead>{t("installment.columns.status")}</TableHead>
                  <TableHead className="text-right">{t("installment.markPaid")}</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {rows.map((row) => {
                  running += num(row.gross_amount_uf) ?? 0;
                  const closed = CLOSED.includes(row.status);
                  return (
                    <TableRow key={row.id}>
                      <TableCell className="tabular-nums font-medium">{row.number}</TableCell>
                      <TableCell>
                        {row.coupon_number ? <MonoChip>{row.coupon_number}</MonoChip> : "—"}
                      </TableCell>
                      <TableCell className="whitespace-nowrap">
                        {formatDate(row.due_date)}
                      </TableCell>
                      <TableCell className="tabular-nums font-medium">
                        {uf(row.gross_amount_uf)}
                      </TableCell>
                      <TableCell className="tabular-nums text-text-muted">{uf(running)}</TableCell>
                      <TableCell className="whitespace-nowrap">
                        {row.paid_on ? formatDate(row.paid_on) : "—"}
                      </TableCell>
                      <TableCell
                        className={cn(
                          "tabular-nums",
                          (row.days_late ?? 0) > 0 ? "text-signal-danger" : "text-text-muted",
                        )}
                      >
                        {row.days_late ?? "—"}
                      </TableCell>
                      <TableCell>
                        <div className="flex flex-col items-start gap-1">
                          <PostsaleBadge
                            value={row.status}
                            label={t(`installment.status.${row.status}`)}
                          />
                          {row.endorsement_id ? (
                            <span className="text-caption text-text-muted">
                              {t("installment.fromEndorsement")}
                            </span>
                          ) : null}
                        </div>
                      </TableCell>
                      <TableCell className="text-right">
                        <DisabledHint hint={gate}>
                          <Button
                            variant="secondary"
                            size="sm"
                            disabled={!canEdit.allowed || closed || update.isPending}
                            onClick={() => void markPaid(row)}
                          >
                            <Wallet className="h-3.5 w-3.5" />
                            {t("installment.markPaid")}
                          </Button>
                        </DisabledHint>
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </div>
        </>
      ) : null}
    </Section>
  );
}
