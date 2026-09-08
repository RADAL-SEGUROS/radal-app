/**
 * Warranty tracker — R-n / G-n / M-n.
 *
 * The highest-value post-sale structure in the corpus: the same code threads
 * through inspection -> policy -> collection tracker -> claim notice ->
 * adjuster report -> endorsement. Its status is not bookkeeping; it decides
 * whether a loss is covered, which is why the vocabulary is that precise:
 *
 *   met_on_time · met_late · met_after_claim · breached · waived
 *
 * `met_late` and `met_after_claim` are separate states on purpose — the second
 * is the one an adjuster reads as causality.
 */
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { ShieldAlert } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { formatDate } from "@/lib/format";
import { useCan } from "@/lib/permissions";
import { usePolicyWarranties } from "@/api/policies";
import { useUpdateWarranty } from "@/api/warranties";
import {
  WARRANTY_STATUSES,
  num,
  type Warranty,
  type WarrantyStatus,
} from "@/api/types";
import {
  DisabledHint,
  EmptyState,
  ErrorBanner,
  LoadingRows,
  MonoChip,
  Section,
  apiError,
  uf,
} from "@/pages/proposals/shared";
import { PostsaleBadge } from "@/pages/policies/shared";

const MET: WarrantyStatus[] = ["met_on_time", "met_late", "met_after_claim"];

function isOverdue(warranty: Warranty): boolean {
  if (!warranty.due_date) return false;
  if (MET.includes(warranty.status) || warranty.status === "waived") return false;
  return new Date(warranty.due_date).getTime() < Date.now();
}

function WarrantyRow({
  warranty,
  policyId,
  canEdit,
  gate,
}: {
  warranty: Warranty;
  policyId: number;
  canEdit: boolean;
  gate: string | null;
}) {
  const { t } = useTranslation("postsale");
  const update = useUpdateWarranty(warranty.id, policyId);

  const budget = num(warranty.budget_uf);
  const actual = num(warranty.actual_cost_uf);
  const overrun =
    budget !== null && budget > 0 && actual !== null ? ((actual - budget) / budget) * 100 : null;

  const change = async (status: WarrantyStatus) => {
    try {
      await update.mutateAsync({
        status,
        // Completing a warranty without a date leaves the tracker unreadable.
        completed_on:
          MET.includes(status) && !warranty.completed_on
            ? new Date().toISOString().slice(0, 10)
            : warranty.completed_on,
      });
      toast.success(t("shared.saved"));
    } catch (error) {
      toast.error(apiError(error, t("shared.notFound")));
    }
  };

  return (
    <TableRow>
      <TableCell className="align-top">
        <div className="flex flex-col gap-1">
          <MonoChip>{warranty.code ?? `#${warranty.id}`}</MonoChip>
          {warranty.category ? (
            <span className="text-caption text-text-muted">
              {t("warranty.category", { letter: warranty.category })}
            </span>
          ) : null}
        </div>
      </TableCell>

      <TableCell className="align-top">
        <div className="max-w-[420px]">
          {warranty.title ? (
            <div className="font-medium text-text-primary">{warranty.title}</div>
          ) : null}
          {warranty.requirement ? (
            <p className="mt-0.5 whitespace-pre-line text-caption text-text-secondary">
              {warranty.requirement}
            </p>
          ) : null}
          <div className="mt-1 flex flex-wrap gap-1.5">
            {warranty.is_permanent ? (
              <Badge variant="neutral">{t("warranty.flags.permanent")}</Badge>
            ) : null}
            {warranty.is_suspensive ? (
              <Badge variant="warn">{t("warranty.flags.suspensive")}</Badge>
            ) : null}
          </div>
          {warranty.verification ? (
            <p className="mt-1 text-caption text-text-muted">
              {t("warranty.verification")}: {warranty.verification}
            </p>
          ) : null}
        </div>
      </TableCell>

      <TableCell className="align-top text-caption text-text-secondary">
        {t(`warranty.source.${warranty.source}`)}
      </TableCell>

      <TableCell className="align-top whitespace-nowrap">
        <div className={isOverdue(warranty) ? "text-signal-danger" : "text-text-secondary"}>
          {formatDate(warranty.due_date)}
        </div>
        {warranty.deadline_days !== null ? (
          <div className="text-caption text-text-muted">
            {t("shared.days", { count: warranty.deadline_days })}
          </div>
        ) : null}
        {isOverdue(warranty) ? (
          <Badge variant="danger" className="mt-1">
            {t("warranty.overdue")}
          </Badge>
        ) : null}
        {warranty.completed_on ? (
          <div className="mt-1 text-caption text-pos-text">
            {t("warranty.completedOn", { date: formatDate(warranty.completed_on) })}
          </div>
        ) : null}
      </TableCell>

      <TableCell className="align-top">
        <div className="flex flex-col items-start gap-1.5">
          <PostsaleBadge
            value={warranty.status}
            label={t(`warranty.status.${warranty.status}`)}
          />
          <DisabledHint hint={gate}>
            <Select
              value={warranty.status}
              disabled={!canEdit || update.isPending}
              onValueChange={(v) => void change(v as WarrantyStatus)}
            >
              <SelectTrigger className="h-8 w-[200px]">
                <SelectValue placeholder={t("warranty.markStatus")} />
              </SelectTrigger>
              <SelectContent>
                {WARRANTY_STATUSES.map((s) => (
                  <SelectItem key={s} value={s}>
                    {t(`warranty.status.${s}`)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </DisabledHint>
        </div>
      </TableCell>

      <TableCell className="align-top whitespace-nowrap tabular-nums">
        <div className="text-text-secondary">
          {t("warranty.budget")}: {uf(warranty.budget_uf, 1)}
        </div>
        <div className="text-text-primary">
          {t("warranty.actualCost")}: {uf(warranty.actual_cost_uf, 1)}
        </div>
        {overrun !== null && Math.abs(overrun) >= 1 ? (
          <div className={overrun > 0 ? "text-warn-text" : "text-pos-text"}>
            {t("warranty.overrun", {
              pct: `${overrun > 0 ? "+" : "−"}${Math.abs(overrun).toFixed(0)} %`,
            })}
          </div>
        ) : null}
      </TableCell>
    </TableRow>
  );
}

export function WarrantyTracker({ policyId }: { policyId: number }) {
  const { t } = useTranslation("postsale");
  const warranties = usePolicyWarranties(policyId);
  const canEdit = useCan("Policies", "Edit");

  const rows = [...(warranties.data ?? [])].sort(
    (a, b) => a.sort_order - b.sort_order || (a.code ?? "").localeCompare(b.code ?? ""),
  );

  const met = rows.filter((w) => MET.includes(w.status)).length;
  const gate = canEdit.isLoading
    ? t("shared.loading")
    : canEdit.allowed
      ? null
      : t("warranty.noPermission");

  return (
    <Section
      title={t("warranty.title")}
      description={t("warranty.description")}
      actions={
        rows.length > 0 ? (
          <Badge variant={met === rows.length ? "success" : "warn"}>
            {t("warranty.compliance")} {met}/{rows.length}
          </Badge>
        ) : null
      }
    >
      <ErrorBanner error={warranties.error} className="mb-3" />
      {warranties.isLoading ? <LoadingRows rows={4} /> : null}

      {!warranties.isLoading && rows.length === 0 ? (
        <EmptyState
          title={t("warranty.empty")}
          hint={t("warranty.emptyHint")}
          icon={<ShieldAlert className="h-6 w-6" />}
        />
      ) : null}

      {rows.length > 0 ? (
        <div className="overflow-x-auto">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>{t("warranty.columns.code")}</TableHead>
                <TableHead>{t("warranty.columns.requirement")}</TableHead>
                <TableHead>{t("warranty.columns.source")}</TableHead>
                <TableHead>{t("warranty.columns.due")}</TableHead>
                <TableHead>{t("warranty.columns.status")}</TableHead>
                <TableHead>{t("warranty.columns.cost")}</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((warranty) => (
                <WarrantyRow
                  key={warranty.id}
                  warranty={warranty}
                  policyId={policyId}
                  canEdit={canEdit.allowed}
                  gate={gate}
                />
              ))}
            </TableBody>
          </Table>
        </div>
      ) : null}
    </Section>
  );
}
