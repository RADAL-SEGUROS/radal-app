/**
 * THE COMPARISON VIEW — every proposal of one quote request, side by side.
 *
 * The server does the alignment work (`GET /quotes/{id}/comparison`): one column
 * per proposal, one deductible row per peril seen ANYWHERE (so a peril a
 * competitor priced and this one ignored shows up as a hole, which is itself a
 * finding), and coverage/exclusion rows aligned on `normalized_code`.
 *
 * Everything on screen is either wired (accept / reject / open / build an
 * offering) or rendered visibly disabled with the reason in a tooltip.
 */
import * as React from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import {
  ArrowLeft,
  ArrowUpRight,
  Award,
  Check,
  Minus,
  Percent,
  Share2,
  TrendingDown,
  X,
} from "lucide-react";

import { PageHeader } from "@/components/common/PageHeader";
import { FadeUp } from "@/components/common/motion";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useCan } from "@/lib/permissions";
import { formatDate } from "@/lib/format";
import { useQuoteComparison } from "@/api/quotes";
import { useAcceptProposal, useRejectProposal } from "@/api/proposals";
import { useCreateOffering } from "@/api/offerings";
import { cn } from "@/lib/utils";
import type {
  ComparisonColumn,
  CoverageRow,
  DeductibleRow,
  ProposalComparison,
} from "@/api/types";
import {
  ConfidenceBadge,
  DisabledHint,
  EmptyState,
  ErrorBanner,
  MonoChip,
  StatusBadge,
  apiError,
  deductibleText,
  formatDeductible,
  permille,
  pct,
  uf,
  usePerilLabel,
} from "@/pages/proposals/shared";

export default function ProposalComparisonPage() {
  const { quoteId } = useParams<{ quoteId: string }>();
  const id = Number(quoteId);
  const { t } = useTranslation("proposals");
  const { t: tq } = useTranslation("quotes");
  const [includeRejected, setIncludeRejected] = React.useState(false);

  const comparison = useQuoteComparison(Number.isFinite(id) ? id : undefined, {
    include_rejected: includeRejected,
  });

  const data = comparison.data;

  return (
    <>
      <PageHeader
        eyebrow={
          <Link to={`/quotes/${id}`} className="inline-flex items-center gap-1.5 no-underline">
            <ArrowLeft className="h-3.5 w-3.5" />
            {tq("detail.backToQuote")}
          </Link>
        }
        title={t("compare.title")}
        subtitle={
          data ? (
            <span className="flex flex-wrap items-center gap-2">
              <MonoChip>COT-{String(data.quote.id).padStart(4, "0")}</MonoChip>
              <span>{data.quote.insured_object || tq("table.noObject")}</span>
              <span className="text-text-muted">·</span>
              <span>
                {t("compare.declared")} {uf(data.quote.declared_value_uf)}
              </span>
            </span>
          ) : (
            t("compare.subtitle")
          )
        }
        actions={
          <label className="flex cursor-pointer items-center gap-2 text-caption text-text-secondary">
            <input
              type="checkbox"
              checked={includeRejected}
              onChange={(e) => setIncludeRejected(e.target.checked)}
              className="h-4 w-4 accent-[var(--teal)]"
            />
            {t("compare.includeRejected")}
          </label>
        }
      />

      {comparison.isError ? <ErrorBanner error={comparison.error} /> : null}

      {comparison.isLoading ? (
        <Skeleton className="h-[420px] w-full rounded-card" />
      ) : !data || data.columns.length === 0 ? (
        <Card>
          <EmptyState
            title={t("compare.emptyTitle")}
            hint={t("compare.emptyHint")}
            action={
              <Button size="sm" asChild>
                <Link to={`/proposals/upload?quote=${id}`}>{t("compare.uploadFirst")}</Link>
              </Button>
            }
          />
        </Card>
      ) : (
        <ComparisonGrid data={data} />
      )}
    </>
  );
}

// =============================================================================
// Grid
// =============================================================================

const COL_WIDTH = "min-w-[248px]";
const LABEL_CELL =
  "sticky left-0 z-20 w-[220px] min-w-[220px] border-r border-line bg-bg-surface px-4 py-2.5 text-left text-caption font-medium uppercase tracking-[0.06em] text-text-muted";

function ComparisonGrid({ data }: { data: ProposalComparison }) {
  const { t } = useTranslation("proposals");
  const perilLabel = usePerilLabel();
  const { highlights } = data;

  const moneyRows: {
    key: string;
    render: (col: ComparisonColumn) => React.ReactNode;
    best?: (col: ComparisonColumn) => boolean;
    bestLabel?: string;
    emphasis?: boolean;
  }[] = [
    {
      key: "total_premium",
      render: (c) => uf(c.total_premium_uf),
      best: (c) => c.proposal_id === highlights.lowest_total_premium_proposal_id,
      bestLabel: t("compare.bestPrice"),
      emphasis: true,
    },
    { key: "net_premium", render: (c) => uf(c.net_premium_uf) },
    { key: "taxable_premium", render: (c) => uf(c.taxable_premium_uf) },
    { key: "exempt_premium", render: (c) => uf(c.exempt_premium_uf) },
    { key: "vat", render: (c) => uf(c.vat_uf) },
    {
      key: "comprehensive_rate",
      render: (c) => permille(c.comprehensive_rate_permille),
      best: (c) => c.proposal_id === highlights.lowest_comprehensive_rate_proposal_id,
      bestLabel: t("compare.bestRate"),
      emphasis: true,
    },
    { key: "taxable_rate", render: (c) => permille(c.taxable_rate_permille) },
    { key: "exempt_rate", render: (c) => permille(c.exempt_rate_permille) },
    {
      key: "commission",
      render: (c) => pct(c.commission_pct),
      best: (c) => c.proposal_id === highlights.highest_commission_proposal_id,
      bestLabel: t("compare.bestCommission"),
    },
    {
      key: "validity",
      render: (c) =>
        c.validity_business_days === null
          ? "—"
          : t("compare.businessDays", { count: c.validity_business_days }),
    },
    {
      key: "coverage_period",
      render: (c) =>
        c.coverage_start || c.coverage_end
          ? `${formatDate(c.coverage_start)} → ${formatDate(c.coverage_end)}`
          : "—",
    },
    { key: "modality", render: (c) => c.modality || "—" },
    { key: "activity", render: (c) => c.activity_classification || "—" },
    { key: "received_at", render: (c) => formatDate(c.received_at) },
    {
      key: "counts",
      render: (c) => (
        <span className="flex flex-wrap items-center gap-1.5">
          <Badge variant="success">{t("compare.coverageCount", { count: c.coverage_count })}</Badge>
          <Badge variant="warn">{t("compare.exclusionCount", { count: c.exclusion_count })}</Badge>
        </span>
      ),
    },
    {
      key: "warranties",
      render: (c) =>
        c.warranties ? (
          <span className="block whitespace-pre-wrap text-caption text-text-secondary">
            {c.warranties}
          </span>
        ) : (
          "—"
        ),
    },
  ];

  return (
    <FadeUp delay={0.05}>
      <Card className="overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-body">
            {/* ── Header: one card per proposal ── */}
            <thead>
              <tr>
                <th className={cn(LABEL_CELL, "h-auto bg-bg-recessed py-4 align-bottom")}>
                  {t("compare.proposalsHeader", { count: data.columns.length })}
                </th>
                {data.columns.map((col) => (
                  <th
                    key={col.proposal_id}
                    className={cn(
                      COL_WIDTH,
                      "border-l border-line bg-bg-recessed p-4 text-left align-top",
                    )}
                  >
                    <ColumnHeader column={col} quoteId={data.quote.id} highlights={data.highlights} />
                  </th>
                ))}
              </tr>
            </thead>

            {/* ── Premiums & rates ── */}
            <SectionRow
              label={t("compare.sections.money")}
              icon={<TrendingDown className="h-3.5 w-3.5" />}
              span={data.columns.length + 1}
            />
            <tbody>
              {moneyRows.map((row) => (
                <tr key={row.key} className="border-t border-line">
                  <td className={LABEL_CELL}>{t(`compare.rows.${row.key}`)}</td>
                  {data.columns.map((col) => {
                    const isBest = row.best?.(col) ?? false;
                    return (
                      <td
                        key={col.proposal_id}
                        className={cn(
                          "border-l border-line px-4 py-2.5 align-top tabular-nums",
                          row.emphasis && "font-medium",
                          isBest &&
                            "bg-[color-mix(in_srgb,var(--lime)_12%,transparent)] text-lime-deep",
                        )}
                      >
                        <span className="flex flex-wrap items-center gap-1.5">
                          {row.render(col)}
                          {isBest && row.bestLabel ? (
                            <Badge variant="success" className="gap-1">
                              <Award className="h-3 w-3" />
                              {row.bestLabel}
                            </Badge>
                          ) : null}
                        </span>
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>

            {/* ── Deductibles per peril ── */}
            <SectionRow
              label={t("compare.sections.deductibles")}
              icon={<Percent className="h-3.5 w-3.5" />}
              span={data.columns.length + 1}
            />
            <tbody>
              {data.deductibles.length === 0 ? (
                <tr className="border-t border-line">
                  <td
                    colSpan={data.columns.length + 1}
                    className="px-4 py-6 text-center text-caption text-text-muted"
                  >
                    {t("compare.noDeductibles")}
                  </td>
                </tr>
              ) : (
                data.deductibles.map((row: DeductibleRow) => (
                  <tr key={row.peril} className="border-t border-line">
                    <td className={LABEL_CELL}>{perilLabel(row.peril)}</td>
                    {data.columns.map((col) => {
                      const cell = row.cells.find((c) => c.proposal_id === col.proposal_id);
                      const summary = formatDeductible(cell?.term, t);
                      const verbatim = deductibleText(cell?.term);
                      return (
                        <td
                          key={col.proposal_id}
                          className="border-l border-line px-4 py-2.5 align-top text-caption"
                        >
                          {summary ? (
                            <span className="flex flex-col gap-0.5">
                              <span className="font-medium text-text-primary">{summary}</span>
                              {verbatim && verbatim !== summary ? (
                                <span className="text-text-muted">{verbatim}</span>
                              ) : null}
                            </span>
                          ) : (
                            <span className="italic text-text-muted">
                              {t("compare.noInformation")}
                            </span>
                          )}
                        </td>
                      );
                    })}
                  </tr>
                ))
              )}
            </tbody>

            {/* ── Coverage matrix ── */}
            <CoverageBlock
              title={t("compare.sections.coverages")}
              rows={data.coverages}
              columns={data.columns}
              tone="success"
            />
            <CoverageBlock
              title={t("compare.sections.exclusions")}
              rows={data.exclusions}
              columns={data.columns}
              tone="warn"
            />
          </table>
        </div>
      </Card>
    </FadeUp>
  );
}

function SectionRow({
  label,
  icon,
  span,
}: {
  label: string;
  icon?: React.ReactNode;
  span: number;
}) {
  return (
    <tbody>
      <tr>
        <td
          colSpan={span}
          className="border-t border-line bg-[color-mix(in_srgb,var(--teal)_8%,transparent)] px-4 py-2 font-display text-caption uppercase tracking-[0.1em] text-teal-deep"
        >
          <span className="flex items-center gap-2">
            {icon}
            {label}
          </span>
        </td>
      </tr>
    </tbody>
  );
}

function CoverageBlock({
  title,
  rows,
  columns,
  tone,
}: {
  title: string;
  rows: CoverageRow[];
  columns: ComparisonColumn[];
  tone: "success" | "warn";
}) {
  const { t } = useTranslation("proposals");
  return (
    <>
      <SectionRow label={title} span={columns.length + 1} />
      <tbody>
        {rows.length === 0 ? (
          <tr className="border-t border-line">
            <td
              colSpan={columns.length + 1}
              className="px-4 py-6 text-center text-caption text-text-muted"
            >
              {t("compare.noRows")}
            </td>
          </tr>
        ) : (
          rows.map((row) => (
            <tr key={row.key} className="border-t border-line">
              <td className={cn(LABEL_CELL, "normal-case")} title={row.label}>
                <span className="block truncate normal-case">{row.label}</span>
              </td>
              {columns.map((col) => {
                const cell = row.cells.find((c) => c.proposal_id === col.proposal_id);
                const included = cell?.included ?? false;
                return (
                  <td
                    key={col.proposal_id}
                    className="border-l border-line px-4 py-2.5 align-top text-caption"
                  >
                    {included ? (
                      <span
                        className={cn(
                          "flex items-start gap-1.5",
                          tone === "success" ? "text-lime-deep" : "text-amber-deep",
                        )}
                      >
                        <Check className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                        <span className="text-text-secondary">{cell?.text ?? row.label}</span>
                      </span>
                    ) : (
                      <span className="flex items-center gap-1.5 text-text-muted">
                        <Minus className="h-3.5 w-3.5" />
                        {t("compare.notIncluded")}
                      </span>
                    )}
                  </td>
                );
              })}
            </tr>
          ))
        )}
      </tbody>
    </>
  );
}

// =============================================================================
// Column header + decisions
// =============================================================================

function ColumnHeader({
  column,
  quoteId,
  highlights,
}: {
  column: ComparisonColumn;
  quoteId: number;
  highlights: ProposalComparison["highlights"];
}) {
  const { t } = useTranslation("proposals");
  const isBestPrice = column.proposal_id === highlights.lowest_total_premium_proposal_id;

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="truncate font-display text-h3 text-text-primary">
            {column.insurer.trade_name || column.insurer.legal_name}
          </div>
          <div className="mt-1 flex flex-wrap items-center gap-1.5">
            <MonoChip>{column.insurer.cmf_code}</MonoChip>
            <Badge variant={column.origin === "native" ? "brand" : "neutral"}>
              {t(`origin.${column.origin}`)}
            </Badge>
          </div>
        </div>
        {isBestPrice ? (
          <Badge variant="success" className="gap-1">
            <Award className="h-3 w-3" />
            {t("compare.bestPriceShort")}
          </Badge>
        ) : null}
      </div>

      <div className="flex flex-wrap items-center gap-1.5">
        <StatusBadge value={column.status} label={t(`status.${column.status}`)} />
        {column.is_confirmed ? (
          <Badge variant="success" className="gap-1">
            <Check className="h-3 w-3" />
            {t("confirmed")}
          </Badge>
        ) : (
          <>
            <Badge variant="warn">{t("unconfirmed")}</Badge>
            <ConfidenceBadge value={column.extraction_confidence} />
          </>
        )}
      </div>

      <div className="mt-1 flex flex-wrap items-center gap-1.5">
        <Button variant="secondary" size="sm" asChild>
          <Link to={`/proposals/${column.proposal_id}`}>
            <ArrowUpRight className="h-4 w-4" />
            {t("compare.open")}
          </Link>
        </Button>
        <DecisionButtons column={column} quoteId={quoteId} />
      </div>
    </div>
  );
}

const CLOSED_STATUSES = ["accepted", "rejected", "withdrawn", "expired"];

function DecisionButtons({
  column,
  quoteId,
}: {
  column: ComparisonColumn;
  quoteId: number;
}) {
  const { t } = useTranslation("proposals");
  const { t: tc } = useTranslation("common");
  const navigate = useNavigate();

  const accept = useAcceptProposal(column.proposal_id);
  const reject = useRejectProposal(column.proposal_id);
  const createOffering = useCreateOffering();
  const canApprove = useCan("Proposals", "Approve");
  const canCreateOffering = useCan("Offerings", "Create");

  const [rejectOpen, setRejectOpen] = React.useState(false);
  const [reason, setReason] = React.useState("");

  const closed = CLOSED_STATUSES.includes(column.status);
  const needsConfirmation = !column.is_confirmed && column.extraction_confidence !== null;

  const acceptHint = !canApprove.allowed
    ? t("decisions.noPermission")
    : closed
      ? t("decisions.alreadyClosed", { status: t(`status.${column.status}`) })
      : needsConfirmation
        ? t("decisions.confirmFirst")
        : null;

  return (
    <>
      <DisabledHint hint={acceptHint}>
        <Button
          size="sm"
          disabled={!!acceptHint || accept.isPending}
          onClick={() =>
            accept.mutate(
              {},
              {
                onSuccess: (result) =>
                  toast.success(
                    t("decisions.accepted", { count: result.rejected_proposal_ids.length }),
                  ),
                onError: (error) => toast.error(apiError(error, tc("toast.error"))),
              },
            )
          }
        >
          <Check className="h-4 w-4" />
          {t("decisions.accept")}
        </Button>
      </DisabledHint>

      <DisabledHint
        hint={
          !canApprove.allowed
            ? t("decisions.noPermission")
            : closed
              ? t("decisions.alreadyClosed", { status: t(`status.${column.status}`) })
              : null
        }
      >
        <Button
          variant="secondary"
          size="sm"
          disabled={closed || !canApprove.allowed}
          onClick={() => setRejectOpen(true)}
        >
          <X className="h-4 w-4" />
          {t("decisions.reject")}
        </Button>
      </DisabledHint>

      <DisabledHint hint={canCreateOffering.allowed ? null : t("decisions.offeringNoPermission")}>
        <Button
          variant="ghost"
          size="sm"
          disabled={!canCreateOffering.allowed || createOffering.isPending}
          onClick={() =>
            createOffering.mutate(
              { quote_request_id: quoteId, selected_proposal_id: column.proposal_id },
              {
                onSuccess: (offering) => {
                  toast.success(t("decisions.offeringCreated"));
                  navigate(`/offerings/${offering.id}`);
                },
                onError: (error) => toast.error(apiError(error, tc("toast.error"))),
              },
            )
          }
        >
          <Share2 className="h-4 w-4" />
          {t("decisions.buildOffering")}
        </Button>
      </DisabledHint>

      <Dialog open={rejectOpen} onOpenChange={setRejectOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("decisions.rejectTitle")}</DialogTitle>
            <DialogDescription>
              {t("decisions.rejectDescription", {
                insurer: column.insurer.trade_name || column.insurer.legal_name,
              })}
            </DialogDescription>
          </DialogHeader>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor={`reason-${column.proposal_id}`}>{t("decisions.reason")}</Label>
            <Input
              id={`reason-${column.proposal_id}`}
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder={t("decisions.reasonPlaceholder")}
            />
          </div>
          {reject.isError ? <ErrorBanner error={reject.error} /> : null}
          <DialogFooter>
            <Button variant="secondary" onClick={() => setRejectOpen(false)}>
              {tc("actions.cancel")}
            </Button>
            <Button
              variant="destructive"
              disabled={reject.isPending}
              onClick={() =>
                reject.mutate(
                  { reason: reason.trim() || null },
                  {
                    onSuccess: () => {
                      toast.success(t("decisions.rejected"));
                      setRejectOpen(false);
                    },
                    onError: (error) => toast.error(apiError(error, tc("toast.error"))),
                  },
                )
              }
            >
              <X className="h-4 w-4" />
              {t("decisions.reject")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
