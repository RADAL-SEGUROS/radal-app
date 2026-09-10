/**
 * THE COMPARISON EXPEDIENT — the account's COMPARISON-stage worktable.
 *
 * Offers arrive ONE AT A TIME. The broker drops a cotización PDF, it is uploaded
 * through the documents flow and read dynamically into a new column; the grid
 * live-updates as each arrives. `align` re-runs the alignment over a monotonic
 * canonical dictionary (a paid AI call with a deterministic server fallback),
 * and `promote` mints a real inbound proposal from a column so accept / offering
 * keep working — suggest → confirm → commit.
 *
 * Every control here is wired or rendered visibly disabled with the server's own
 * reason; a `not_a_proposal` upload is a VISIBLE rejection card, never a toast.
 */
import * as React from "react";
import { Link, useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import {
  ArrowLeft,
  ArrowUpRight,
  Check,
  FileText,
  Layers,
  Minus,
  Plus,
  RefreshCw,
  Sparkles,
  Star,
  Trash2,
  TriangleAlert,
  Upload,
} from "lucide-react";

import { PageHeader } from "@/components/common/PageHeader";
import { FadeUp } from "@/components/common/motion";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { useCan } from "@/lib/permissions";
import { cn } from "@/lib/utils";
import { num } from "@/api/types";
import type {
  Comparison,
  ComparisonDimension,
  ComparisonDimensionCell,
  ComparisonEntry,
  ComparisonPremiumCore,
  ComparisonRecommendation,
} from "@/api/types";
import { useDocumentDownload, useUploadDocument } from "@/api/documents";
import {
  isProviderDown,
  useAddEntry,
  useAlign,
  useComparison,
  useCreateComparison,
  useDeleteEntry,
  usePatchEntry,
  usePromoteEntry,
} from "@/api/comparisons";
import {
  DisabledHint,
  EmptyState,
  ErrorBanner,
  StatusBadge,
  apiError,
  permille,
  pct,
  resolveFileUrl,
  uf,
} from "@/components/common/kit";

/** The `detail.code` of a structured 422, when the server sent one. */
function errorCode(error: unknown): string | null {
  const detail = (
    error as { response?: { data?: { detail?: unknown } } } | undefined
  )?.response?.data?.detail;
  if (detail && typeof detail === "object" && !Array.isArray(detail)) {
    const code = (detail as { code?: unknown }).code;
    if (typeof code === "string") return code;
  }
  return null;
}

// =============================================================================
// Page
// =============================================================================

export default function ComparisonPage() {
  const { caseId } = useParams<{ caseId: string }>();
  const id = Number(caseId);
  const { t } = useTranslation("comparison");

  const canView = useCan("Proposals", "View");
  const create = useCreateComparison();
  const [comparisonId, setComparisonId] = React.useState<number | null>(null);
  const startedRef = React.useRef(false);

  React.useEffect(() => {
    if (!Number.isFinite(id) || startedRef.current) return;
    startedRef.current = true;
    create.mutate(
      { case_file_id: id },
      { onSuccess: (c) => setComparisonId(c.id) },
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  const comparison = useComparison(comparisonId ?? undefined);
  const data = comparison.data;

  const header = (
    <PageHeader
      eyebrow={
        <Link
          to={`/cases/${id}`}
          className="inline-flex items-center gap-1.5 no-underline"
        >
          <ArrowLeft className="h-3.5 w-3.5" />
          {t("backToAccount")}
        </Link>
      }
      title={t("title")}
      subtitle={t("subtitle")}
      actions={
        data ? (
          <span className="flex items-center gap-2">
            <StatusBadge value={data.status} label={t(`status.${data.status}`)} dot />
            <Badge variant="outline" className="tabular-nums">
              {t("versionShort", { version: data.canonical_version })}
            </Badge>
          </span>
        ) : null
      }
    />
  );

  if (!canView.isLoading && !canView.allowed) {
    return (
      <>
        {header}
        <Card>
          <EmptyState title={t("noPermission.title")} hint={t("noPermission.hint")} />
        </Card>
      </>
    );
  }

  if (create.isError) {
    return (
      <>
        {header}
        <ErrorBanner error={create.error} />
      </>
    );
  }

  return (
    <>
      {header}
      {comparison.isError ? <ErrorBanner error={comparison.error} /> : null}
      {!data || comparison.isLoading ? (
        <Skeleton className="h-[420px] w-full rounded-card" />
      ) : (
        <ComparisonBoard caseId={id} comparison={data} />
      )}
    </>
  );
}

// =============================================================================
// Board
// =============================================================================

function ComparisonBoard({
  caseId,
  comparison,
}: {
  caseId: number;
  comparison: Comparison;
}) {
  const { t } = useTranslation("comparison");
  const { t: tc } = useTranslation("common");

  const canCreate = useCan("Proposals", "Create");
  const canEdit = useCan("Proposals", "Edit");

  const upload = useUploadDocument();
  const addEntry = useAddEntry(comparison.id);
  const align = useAlign(comparison.id);

  const inputRef = React.useRef<HTMLInputElement>(null);
  const [dragOver, setDragOver] = React.useState(false);
  const alignTimer = React.useRef<number | null>(null);

  const busy = upload.isPending || addEntry.isPending;

  // Debounced auto-align: a fresh column supersedes the last alignment, so we
  // re-run it shortly after an upload settles (Edit permission required).
  const scheduleAutoAlign = React.useCallback(() => {
    if (!canEdit.allowed) return;
    if (alignTimer.current) window.clearTimeout(alignTimer.current);
    alignTimer.current = window.setTimeout(() => {
      align.mutate(undefined, {
        onError: (error) => toast.error(apiError(error, tc("state.error"))),
      });
    }, 900);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [canEdit.allowed, align]);

  React.useEffect(
    () => () => {
      if (alignTimer.current) window.clearTimeout(alignTimer.current);
    },
    [],
  );

  const runAlign = () =>
    align.mutate(undefined, {
      onSuccess: (res) => {
        if (res.warnings.length) toast.message(res.warnings[0]);
      },
      onError: (error) => toast.error(apiError(error, tc("state.error"))),
    });

  const handleFiles = async (files: FileList | null) => {
    if (!files || files.length === 0) return;
    for (const file of Array.from(files)) {
      try {
        const doc = await upload.mutateAsync({
          file,
          entity_type: "case_file",
          entity_id: caseId,
          category: "insurer_quotation",
        });
        const result = await addEntry.mutateAsync({ document_id: doc.id });
        if (result.is_wrong_file) {
          toast.warning(t("upload.rejected", { name: file.name }));
        } else {
          toast.success(t("upload.added", { name: file.name }));
          scheduleAutoAlign();
        }
      } catch (error) {
        toast.error(apiError(error, tc("state.error")));
      }
    }
    if (inputRef.current) inputRef.current.value = "";
  };

  const uploadHint = !canCreate.allowed ? t("upload.noPermission") : null;

  const entries = comparison.entries;
  const liveEntries = entries.filter((e) => !e.is_wrong_file);
  const rejectedEntries = entries.filter((e) => e.is_wrong_file);
  const matrix = comparison.aligned_matrix;

  // BLOCK-ON-PROVIDER-DOWN: a 502/503/504 on align means the comparison was NOT
  // aligned — surface a persistent, retryable banner instead of an empty grid.
  const providerDown = align.isError && isProviderDown(align.error);
  // The readings were run in batches then merged (large input) — surfaced subtly.
  const batched = matrix?.batched ?? false;
  // The AI's recommended offer (first-class field, mirrored inside the matrix).
  const recommendation = comparison.recommendation ?? matrix?.recommendation ?? null;

  return (
    <div className="flex flex-col gap-5">
      {/* ── Upload dropzone + actions ── */}
      <FadeUp>
        <Card className="p-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <input
              ref={inputRef}
              type="file"
              accept="application/pdf,.pdf"
              multiple
              className="hidden"
              onChange={(e) => void handleFiles(e.target.files)}
            />
            <DisabledHint hint={uploadHint} className="min-w-0 flex-1">
              <button
                type="button"
                disabled={!canCreate.allowed || busy}
                onClick={() => inputRef.current?.click()}
                onDragOver={(e) => {
                  e.preventDefault();
                  setDragOver(true);
                }}
                onDragLeave={() => setDragOver(false)}
                onDrop={(e) => {
                  e.preventDefault();
                  setDragOver(false);
                  if (canCreate.allowed && !busy) void handleFiles(e.dataTransfer.files);
                }}
                className={cn(
                  "flex w-full items-center gap-3 rounded-lg border border-dashed border-line px-4 py-3 text-left transition-colors",
                  "hover:border-brand-line hover:bg-brand-soft/40",
                  dragOver && "border-brand-line bg-brand-soft/60",
                  (!canCreate.allowed || busy) && "cursor-not-allowed opacity-60",
                )}
              >
                <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-brand-soft text-brand-deep">
                  {busy ? (
                    <RefreshCw className="h-4 w-4 animate-spin" />
                  ) : (
                    <Upload className="h-4 w-4" />
                  )}
                </span>
                <span className="min-w-0">
                  <span className="block text-body font-medium text-ink">
                    {busy ? t("upload.working") : t("upload.title")}
                  </span>
                  <span className="block text-caption text-ink-3">
                    {t("upload.hint")}
                  </span>
                </span>
              </button>
            </DisabledHint>

            <div className="flex items-center gap-2">
              {batched ? (
                <Badge variant="outline" className="gap-1 text-ink-3">
                  <Layers className="h-3 w-3" />
                  {t("align.batched")}
                </Badge>
              ) : null}
              <DisabledHint
                hint={
                  !canEdit.allowed
                    ? t("align.noPermission")
                    : liveEntries.length === 0
                      ? t("align.needColumns")
                      : null
                }
              >
                <Button
                  variant="secondary"
                  size="sm"
                  disabled={!canEdit.allowed || liveEntries.length === 0 || align.isPending}
                  onClick={runAlign}
                >
                  <Sparkles className={cn("h-4 w-4", align.isPending && "animate-pulse")} />
                  {t("align.action")}
                </Button>
              </DisabledHint>
            </div>
          </div>
        </Card>
      </FadeUp>

      {/* ── Block-on-provider-down: retryable, never a broken grid ── */}
      {providerDown ? (
        <AlignBlockedBanner onRetry={runAlign} pending={align.isPending} />
      ) : null}

      {/* ── AI recommendation ── */}
      {recommendation ? (
        <RecommendationCard
          recommendation={recommendation}
          liveEntries={liveEntries}
        />
      ) : null}

      {/* ── Rejected uploads ── */}
      {rejectedEntries.length > 0 ? (
        <div className="flex flex-col gap-2">
          {rejectedEntries.map((entry) => (
            <RejectionCard key={entry.id} entry={entry} comparisonId={comparison.id} />
          ))}
        </div>
      ) : null}

      {/* ── The grid ── */}
      {liveEntries.length === 0 ? (
        <Card>
          <EmptyState
            icon={<Plus />}
            title={t("empty.title")}
            hint={t("empty.hint")}
          />
        </Card>
      ) : !matrix || matrix.columns.length === 0 ? (
        <Card>
          <EmptyState
            icon={<Sparkles />}
            title={t("needAlign.title")}
            hint={t("needAlign.hint")}
            action={
              <DisabledHint hint={!canEdit.allowed ? t("align.noPermission") : null}>
                <Button
                  size="sm"
                  disabled={!canEdit.allowed || align.isPending}
                  onClick={runAlign}
                >
                  <Sparkles className="h-4 w-4" />
                  {t("align.action")}
                </Button>
              </DisabledHint>
            }
          />
        </Card>
      ) : (
        <ComparisonGrid
          comparison={comparison}
          liveEntries={liveEntries}
          matrix={matrix}
          recommendation={recommendation}
        />
      )}
    </div>
  );
}

// =============================================================================
// AI recommendation
// =============================================================================

/** The label the grid header shows for an entry — reused so the recommendation
 *  card names the exact same column. */
function useColumnLabel() {
  const { t } = useTranslation("comparison");
  return React.useCallback(
    (entry: ComparisonEntry | undefined, index: number) =>
      entry?.proposal_id != null
        ? t("column.proposal")
        : t("column.quotation", { n: index + 1 }),
    [t],
  );
}

/**
 * The calm, prominent recommendation card. Resolves the recommended column to its
 * grid label, shows the rationale, and lists the caveats. Hidden when the server
 * sent no recommendation.
 */
function RecommendationCard({
  recommendation,
  liveEntries,
}: {
  recommendation: ComparisonRecommendation;
  liveEntries: ComparisonEntry[];
}) {
  const { t } = useTranslation("comparison");
  const columnLabel = useColumnLabel();

  const idx = liveEntries.findIndex(
    (e) =>
      (recommendation.recommended_comparison_source_id != null &&
        e.comparison_source_id === recommendation.recommended_comparison_source_id) ||
      (recommendation.recommended_proposal_id != null &&
        e.proposal_id === recommendation.recommended_proposal_id),
  );
  const label = idx >= 0 ? columnLabel(liveEntries[idx], idx) : null;

  return (
    <FadeUp>
      <Card className="border-brand-line bg-brand-soft/30 p-5">
        <div className="flex items-start gap-3">
          <span className="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-brand-soft text-brand-deep">
            <Sparkles className="h-4 w-4" />
          </span>
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-caption font-medium uppercase tracking-wide text-brand-deep">
                {t("recommendation.eyebrow")}
              </span>
              {label ? (
                <Badge variant="brand" dot>
                  {label}
                </Badge>
              ) : null}
            </div>
            {recommendation.rationale ? (
              <p className="mt-1.5 text-body text-ink-2">{recommendation.rationale}</p>
            ) : (
              <p className="mt-1.5 text-body text-ink-3">
                {t("recommendation.noRationale")}
              </p>
            )}
            {recommendation.caveats.length > 0 ? (
              <div className="mt-3">
                <div className="text-caption font-medium text-ink-3">
                  {t("recommendation.caveats")}
                </div>
                <ul className="mt-1 flex flex-col gap-1">
                  {recommendation.caveats.map((caveat, i) => (
                    <li
                      key={i}
                      className="flex items-start gap-1.5 text-caption text-ink-2"
                    >
                      <TriangleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0 text-warn-text" />
                      <span>{caveat}</span>
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}
          </div>
        </div>
      </Card>
    </FadeUp>
  );
}

/** The block-on-provider-down state for the align action: clear and retryable. */
function AlignBlockedBanner({
  onRetry,
  pending,
}: {
  onRetry: () => void;
  pending: boolean;
}) {
  const { t } = useTranslation("comparison");
  return (
    <FadeUp>
      <Card className="border-warn-line bg-warn-soft/40 p-4">
        <div className="flex items-start justify-between gap-3">
          <div className="flex items-start gap-2.5">
            <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-warn-soft text-warn-text">
              <TriangleAlert className="h-4 w-4" />
            </span>
            <div className="min-w-0">
              <div className="text-body font-medium text-ink">
                {t("align.blocked.title")}
              </div>
              <p className="mt-0.5 text-caption text-ink-2">
                {t("align.blocked.hint")}
              </p>
            </div>
          </div>
          <Button variant="secondary" size="sm" disabled={pending} onClick={onRetry}>
            <RefreshCw className={cn("h-4 w-4", pending && "animate-spin")} />
            {t("align.blocked.retry")}
          </Button>
        </div>
      </Card>
    </FadeUp>
  );
}

// =============================================================================
// Grid
// =============================================================================

const LABEL_CELL =
  "sticky left-0 z-20 w-[220px] min-w-[220px] border-r border-line bg-bg-surface px-4 py-2.5 text-left text-caption font-medium text-ink-3";
const COL_WIDTH = "min-w-[248px]";

/** Render a standardized dimension cell value for display. */
function formatDimValue(value: string | number | null | undefined): string | null {
  if (value === null || value === undefined) return null;
  if (typeof value === "number") return num(value) != null ? String(value) : null;
  const trimmed = value.trim();
  return trimmed.length > 0 ? trimmed : null;
}

function ComparisonGrid({
  comparison,
  liveEntries,
  matrix,
  recommendation,
}: {
  comparison: Comparison;
  liveEntries: ComparisonEntry[];
  matrix: NonNullable<Comparison["aligned_matrix"]>;
  recommendation: ComparisonRecommendation | null;
}) {
  const { t } = useTranslation("comparison");

  const columns = liveEntries.map((entry, index) => ({ entry, index }));

  // The AI's recommended column — marked with a pine chip in its header.
  const recSource = recommendation?.recommended_comparison_source_id ?? null;
  const recProposal = recommendation?.recommended_proposal_id ?? null;
  const isRecommendedByAi = (entry: ComparisonEntry) =>
    (recSource != null && entry.comparison_source_id === recSource) ||
    (recProposal != null && entry.proposal_id === recProposal);

  // The standardized table: dimensions split into COMMON (every offer carries it)
  // and EXTRAS (scope=extra, or unclassified) sections.
  const dimensions = matrix.dimensions ?? [];
  const commonDims = dimensions.filter((d) => d.scope === "common");
  const extraDims = dimensions.filter((d) => d.scope !== "common");

  // The premium/rate highlight: the money core each column extracted, read
  // pre-promotion. Null for a wrong-file / not-yet-parsed column.
  const anyPremium = columns.some((c) => c.entry.premium != null);
  const totals = columns.map((c) => num(c.entry.premium?.total_premium_uf ?? null));
  let lowestTotalIndex = -1;
  {
    let lowest = Infinity;
    let priced = 0;
    totals.forEach((n, i) => {
      if (n === null) return;
      priced += 1;
      if (n < lowest) {
        lowest = n;
        lowestTotalIndex = i;
      }
    });
    // "menor" only makes sense once at least two columns carry a total.
    if (priced < 2) lowestTotalIndex = -1;
  }

  return (
    <FadeUp delay={0.05}>
      <Card className="overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-body">
            <thead>
              <tr>
                <th className={cn(LABEL_CELL, "h-auto py-4 align-bottom")}>
                  {t("grid.columnsHeader", { count: columns.length })}
                </th>
                {columns.map(({ entry, index }) => (
                  <th
                    key={entry.id}
                    className={cn(
                      COL_WIDTH,
                      "border-l border-line bg-bg-surface p-4 text-left align-top",
                    )}
                  >
                    <ColumnHeader
                      comparison={comparison}
                      entry={entry}
                      index={index}
                      recommendedByAi={isRecommendedByAi(entry)}
                    />
                  </th>
                ))}
              </tr>
            </thead>

            {/* ── Premium & rates ── */}
            {anyPremium ? (
              <>
                <SectionRow
                  label={t("grid.sections.premium")}
                  span={columns.length + 1}
                />
                <tbody>
                  <PremiumRow
                    label={t("grid.premium.total")}
                    columns={columns}
                    render={(p) => uf(p.total_premium_uf)}
                    lowestIndex={lowestTotalIndex}
                    lowestLabel={t("grid.lowest")}
                  />
                  <PremiumRow
                    label={t("grid.premium.net")}
                    columns={columns}
                    render={(p) => uf(p.net_premium_uf)}
                  />
                  <PremiumRow
                    label={t("grid.premium.comprehensiveRate")}
                    columns={columns}
                    render={(p) => permille(p.comprehensive_rate_permille)}
                  />
                  <PremiumRow
                    label={t("grid.premium.commission")}
                    columns={columns}
                    render={(p) => pct(p.commission_pct)}
                  />
                </tbody>
              </>
            ) : null}

            {/* ── Common dimensions ── */}
            <SectionRow
              label={t("grid.sections.common")}
              span={columns.length + 1}
            />
            <DimensionBlock
              dimensions={commonDims}
              columns={columns}
              emptyLabel={t("grid.noCommon")}
            />

            {/* ── Extra dimensions ── */}
            <SectionRow
              label={t("grid.sections.extras")}
              span={columns.length + 1}
              hint={extraDims.length > 0 ? t("grid.extrasHint") : undefined}
            />
            <DimensionBlock
              dimensions={extraDims}
              columns={columns}
              emptyLabel={t("grid.noExtras")}
            />
          </table>
        </div>
      </Card>
    </FadeUp>
  );
}

type GridColumn = {
  entry: ComparisonEntry;
  index: number;
};

/** One row of the premium/rate highlight: one cell per column, muted em-dash
 *  placeholder for a column with no extracted premium (wrong-file / not parsed). */
function PremiumRow({
  label,
  columns,
  render,
  lowestIndex,
  lowestLabel,
}: {
  label: string;
  columns: GridColumn[];
  render: (premium: ComparisonPremiumCore) => string;
  lowestIndex?: number;
  lowestLabel?: string;
}) {
  return (
    <tr className="border-t border-line bg-brand-soft/25">
      <td className={cn(LABEL_CELL, "bg-brand-soft/25 font-medium text-ink")}>
        {label}
      </td>
      {columns.map((c, i) => {
        const premium = c.entry.premium ?? null;
        const isLowest =
          lowestIndex != null && lowestIndex >= 0 && lowestIndex === i;
        return (
          <td
            key={c.entry.id}
            className={cn(
              "border-l border-line px-4 py-2.5 align-top tabular-nums",
              isLowest && "bg-pos-soft text-pos-text",
            )}
          >
            {premium == null ? (
              <span className="text-ink-3">—</span>
            ) : (
              <span className="flex flex-wrap items-center gap-1.5 font-medium">
                {render(premium)}
                {isLowest && lowestLabel ? (
                  <Badge variant="success">{lowestLabel}</Badge>
                ) : null}
              </span>
            )}
          </td>
        );
      })}
    </tr>
  );
}

/** One section of the standardized table: one row per dimension, one column per
 *  insurer, each cell resolved by `comparison_source_id`. */
function DimensionBlock({
  dimensions,
  columns,
  emptyLabel,
}: {
  dimensions: ComparisonDimension[];
  columns: GridColumn[];
  emptyLabel: string;
}) {
  const { t } = useTranslation("comparison");
  return (
    <tbody>
      {dimensions.length === 0 ? (
        <tr className="border-t border-line">
          <td
            colSpan={columns.length + 1}
            className="px-4 py-6 text-center text-caption text-ink-3"
          >
            {emptyLabel}
          </td>
        </tr>
      ) : (
        dimensions.map((dim) => {
          const byCol = new Map(
            dim.cells.map((c) => [c.comparison_source_id, c] as const),
          );
          return (
            <tr key={dim.key} className="border-t border-line">
              <td className={LABEL_CELL} title={dim.label ?? dim.key}>
                <span className="flex flex-col gap-0.5">
                  <span className="block truncate text-ink">
                    {dim.label ?? dim.key}
                  </span>
                  <Badge variant="outline" className="w-fit">
                    {t(`facetGroups.${dim.group}`, { defaultValue: String(dim.group) })}
                  </Badge>
                </span>
              </td>
              {columns.map((c) => {
                const cell =
                  c.entry.comparison_source_id != null
                    ? byCol.get(c.entry.comparison_source_id)
                    : undefined;
                return (
                  <td
                    key={c.entry.id}
                    className="border-l border-line px-4 py-2.5 align-top text-caption"
                  >
                    <DimensionCell cell={cell} />
                  </td>
                );
              })}
            </tr>
          );
        })
      )}
    </tbody>
  );
}

function DimensionCell({ cell }: { cell: ComparisonDimensionCell | undefined }) {
  const { t } = useTranslation("comparison");

  if (!cell) {
    return (
      <span className="flex items-center gap-1.5 text-ink-3">
        <Minus className="h-3.5 w-3.5" />
        {t("grid.absent")}
      </span>
    );
  }

  const value = formatDimValue(cell.value);

  if (cell.present === false) {
    return (
      <span className="flex items-start gap-1.5 text-neg-text">
        <Minus className="mt-0.5 h-3.5 w-3.5 shrink-0" />
        <span>{value ?? t("grid.excluded")}</span>
      </span>
    );
  }

  const verbatim = cell.verbatim && cell.verbatim !== value ? cell.verbatim : null;

  return (
    <span className="flex items-start gap-1.5 text-pos-text">
      <Check className="mt-0.5 h-3.5 w-3.5 shrink-0" />
      <span className="flex min-w-0 flex-col gap-0.5">
        <span className="text-ink">{value ?? t("grid.included")}</span>
        {verbatim ? (
          <TooltipProvider delayDuration={150}>
            <Tooltip>
              <TooltipTrigger asChild>
                <span className="cursor-help truncate text-ink-3 underline decoration-dotted underline-offset-2">
                  {t("grid.verbatim")}
                </span>
              </TooltipTrigger>
              <TooltipContent side="top" className="max-w-[320px] text-left">
                {verbatim}
              </TooltipContent>
            </Tooltip>
          </TooltipProvider>
        ) : null}
      </span>
    </span>
  );
}

function SectionRow({
  label,
  span,
  hint,
}: {
  label: string;
  span: number;
  hint?: string;
}) {
  return (
    <tbody>
      <tr>
        <td
          colSpan={span}
          className="border-t border-line bg-brand-soft px-4 py-2 text-caption font-medium text-brand-deep"
        >
          <span className="flex flex-wrap items-center gap-2">
            {label}
            {hint ? <span className="font-normal text-ink-3">{hint}</span> : null}
          </span>
        </td>
      </tr>
    </tbody>
  );
}

// =============================================================================
// Column header
// =============================================================================

function ColumnHeader({
  comparison,
  entry,
  index,
  recommendedByAi,
}: {
  comparison: Comparison;
  entry: ComparisonEntry;
  index: number;
  recommendedByAi?: boolean;
}) {
  const { t } = useTranslation("comparison");
  const { t: tc } = useTranslation("common");

  const canEdit = useCan("Proposals", "Edit");
  const canApprove = useCan("Proposals", "Approve");

  const patch = usePatchEntry(comparison.id);
  const remove = useDeleteEntry(comparison.id);

  const [promoteOpen, setPromoteOpen] = React.useState(false);

  const promoted = entry.proposal_id != null;

  const toggleRecommend = () =>
    patch.mutate(
      { entryId: entry.id, is_recommended: !entry.is_recommended },
      { onError: (error) => toast.error(apiError(error, tc("state.error"))) },
    );

  const doRemove = () =>
    remove.mutate(entry.id, {
      onSuccess: () => toast.success(t("remove.done")),
      onError: (error) => toast.error(apiError(error, tc("state.error"))),
    });

  const promoteHint = !canApprove.allowed
    ? t("promote.noPermission")
    : promoted
      ? t("promote.already")
      : null;

  return (
    <div className="flex flex-col gap-2.5">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="truncate text-h3 text-ink">
            {promoted ? t("column.proposal") : t("column.quotation", { n: index + 1 })}
          </div>
          <div className="mt-1 flex flex-wrap items-center gap-1.5">
            {recommendedByAi ? (
              <Badge variant="brand" dot className="gap-1">
                <Sparkles className="h-3 w-3" />
                {t("column.recommendedByAi")}
              </Badge>
            ) : null}
            {entry.is_recommended ? (
              <Badge variant="brand" dot>
                {t("column.recommended")}
              </Badge>
            ) : null}
            {promoted ? (
              <Badge variant="success">{t("column.promoted")}</Badge>
            ) : null}
          </div>
        </div>
        <DisabledHint hint={!canEdit.allowed ? t("recommend.noPermission") : null}>
          <Button
            variant={entry.is_recommended ? "accent-soft" : "ghost"}
            size="icon"
            className="h-8 w-8"
            disabled={!canEdit.allowed || patch.isPending}
            aria-pressed={entry.is_recommended}
            aria-label={t("recommend.action")}
            onClick={toggleRecommend}
          >
            <Star className={cn("h-4 w-4", entry.is_recommended && "fill-current")} />
          </Button>
        </DisabledHint>
      </div>

      <div className="flex flex-wrap items-center gap-1.5">
        {promoted ? (
          <Button variant="secondary" size="sm" asChild>
            <Link to={`/proposals/${entry.proposal_id}`}>
              <ArrowUpRight className="h-4 w-4" />
              {t("column.openProposal")}
            </Link>
          </Button>
        ) : null}
        <SourceDocLink documentId={entry.document_id} />
      </div>

      <div className="flex flex-wrap items-center gap-1.5">
        <DisabledHint hint={promoteHint}>
          <Button
            variant="accent-soft"
            size="sm"
            disabled={!canApprove.allowed || promoted}
            onClick={() => setPromoteOpen(true)}
          >
            <Sparkles className="h-4 w-4" />
            {t("promote.action")}
          </Button>
        </DisabledHint>
        {canApprove.allowed && !promoted ? (
          <PromoteDialog
            comparisonId={comparison.id}
            entry={entry}
            open={promoteOpen}
            onOpenChange={setPromoteOpen}
          />
        ) : null}
        <DisabledHint hint={!canEdit.allowed ? t("remove.noPermission") : null}>
          <Button
            variant="ghost"
            size="sm"
            disabled={!canEdit.allowed || remove.isPending}
            onClick={doRemove}
          >
            <Trash2 className="h-4 w-4" />
            {t("remove.action")}
          </Button>
        </DisabledHint>
      </div>
    </div>
  );
}

// =============================================================================
// Promote dialog — supply the insurer's identity
// =============================================================================

/**
 * A cotización PDF carries only the INSURED's RUT, so the extractor usually
 * cannot fill the insurer's RUT/CMF and `promote` 422s. This confirm dialog lets
 * the reviewer supply the insurer's identity (RUT and/or CMF code) before minting
 * the proposal. Both fields are optional here — the server decides whether the
 * parsed extraction already resolved an insurer — but when it did not, the 422 is
 * surfaced inline and the dialog stays open so the reviewer can supply it (no
 * dead-end). A `money_inconsistent` refusal is surfaced the same way.
 */
function PromoteDialog({
  comparisonId,
  entry,
  open,
  onOpenChange,
}: {
  comparisonId: number;
  entry: ComparisonEntry;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { t } = useTranslation("comparison");
  const { t: tc } = useTranslation("common");

  const promote = usePromoteEntry(comparisonId);

  const [rut, setRut] = React.useState("");
  const [cmf, setCmf] = React.useState("");
  const [error, setError] = React.useState<string | null>(null);

  // Reset the form each time the dialog opens for a fresh attempt.
  React.useEffect(() => {
    if (open) {
      setRut("");
      setCmf("");
      setError(null);
    }
  }, [open]);

  const submit = () => {
    setError(null);
    const insurer_rut = rut.trim() || null;
    const insurer_cmf_code = cmf.trim() || null;
    promote.mutate(
      { entryId: entry.id, insurer_rut, insurer_cmf_code },
      {
        onSuccess: () => {
          toast.success(t("promote.done"));
          onOpenChange(false);
        },
        onError: (err) => {
          const code = errorCode(err);
          if (
            code === "insurer_unresolved" ||
            code === "insurer_identity_incomplete"
          ) {
            setError(t("promote.dialog.errors.insurerUnresolved"));
          } else if (code === "money_inconsistent") {
            setError(t("promote.dialog.errors.moneyInconsistent"));
          } else {
            setError(apiError(err, tc("state.error")));
          }
        },
      },
    );
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>{t("promote.dialog.title")}</DialogTitle>
          <DialogDescription>{t("promote.dialog.description")}</DialogDescription>
        </DialogHeader>

        <form
          className="flex flex-col gap-4"
          onSubmit={(e) => {
            e.preventDefault();
            if (!promote.isPending) submit();
          }}
        >
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="promote-insurer-rut">
              {t("promote.dialog.rutLabel")}
            </Label>
            <Input
              id="promote-insurer-rut"
              value={rut}
              onChange={(e) => setRut(e.target.value)}
              placeholder={t("promote.dialog.rutPlaceholder")}
              autoComplete="off"
            />
          </div>

          <div className="flex flex-col gap-1.5">
            <Label htmlFor="promote-insurer-cmf">
              {t("promote.dialog.cmfLabel")}
            </Label>
            <Input
              id="promote-insurer-cmf"
              value={cmf}
              onChange={(e) => setCmf(e.target.value)}
              placeholder={t("promote.dialog.cmfPlaceholder")}
              autoComplete="off"
            />
            <span className="text-caption text-ink-3">
              {t("promote.dialog.helper")}
            </span>
          </div>

          {error ? (
            <div className="flex items-start gap-2 rounded-sm border border-neg-line bg-neg-soft/50 px-3 py-2 text-caption text-neg-text">
              <TriangleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              <span>{error}</span>
            </div>
          ) : null}

          <DialogFooter className="gap-2">
            <DialogClose asChild>
              <Button type="button" variant="ghost" size="sm">
                {tc("actions.cancel")}
              </Button>
            </DialogClose>
            <Button type="submit" size="sm" disabled={promote.isPending}>
              <Sparkles
                className={cn("h-4 w-4", promote.isPending && "animate-pulse")}
              />
              {t("promote.dialog.confirm")}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function SourceDocLink({ documentId }: { documentId: number | null }) {
  const { t } = useTranslation("comparison");
  const download = useDocumentDownload(documentId ?? undefined, documentId != null);
  const url = resolveFileUrl(download.data?.url);

  if (documentId == null) return null;
  if (!url) {
    return (
      <Button variant="ghost" size="sm" disabled>
        <FileText className="h-4 w-4" />
        {t("column.document")}
      </Button>
    );
  }
  return (
    <Button variant="ghost" size="sm" asChild>
      <a href={url} target="_blank" rel="noreferrer">
        <FileText className="h-4 w-4" />
        {t("column.document")}
      </a>
    </Button>
  );
}

// =============================================================================
// Rejection card
// =============================================================================

function RejectionCard({
  entry,
  comparisonId,
}: {
  entry: ComparisonEntry;
  comparisonId: number;
}) {
  const { t } = useTranslation("comparison");
  const { t: tc } = useTranslation("common");
  const canEdit = useCan("Proposals", "Edit");
  const remove = useDeleteEntry(comparisonId);

  return (
    <Card className="border-warn-line bg-warn-soft/40 p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-start gap-2.5">
          <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-warn-soft text-warn-text">
            <TriangleAlert className="h-4 w-4" />
          </span>
          <div className="min-w-0">
            <div className="text-body font-medium text-ink">{t("rejection.title")}</div>
            <p className="mt-0.5 text-caption text-ink-2">
              {entry.wrong_file_reason || t("rejection.reasonFallback")}
            </p>
            <SourceDocLink documentId={entry.document_id} />
          </div>
        </div>
        <DisabledHint hint={!canEdit.allowed ? t("remove.noPermission") : null}>
          <Button
            variant="ghost"
            size="sm"
            disabled={!canEdit.allowed || remove.isPending}
            onClick={() =>
              remove.mutate(entry.id, {
                onSuccess: () => toast.success(t("remove.done")),
                onError: (error) => toast.error(apiError(error, tc("state.error"))),
              })
            }
          >
            <Trash2 className="h-4 w-4" />
            {t("remove.action")}
          </Button>
        </DisabledHint>
      </div>
    </Card>
  );
}
