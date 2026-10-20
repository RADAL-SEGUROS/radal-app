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
  ComparisonEntryResult,
  ComparisonPremiumCore,
  DecimalString,
  ComparisonRecommendation,
} from "@/api/types";
import { useUploadDocument } from "@/api/documents";
import { PreviewDocButton } from "@/components/common/DownloadDocButton";
import {
  isProviderDown,
  pollComparison,
  useAddEntry,
  useAlign,
  useComparison,
  useEnsureComparison,
  useDeleteEntry,
  usePatchEntry,
  usePromoteEntry,
  useRetryEntry,
} from "@/api/comparisons";
import { qk } from "@/api/keys";
import {
  InsurerCatalogSelect,
  useInsurerCatalog,
} from "@/components/insurers/InsurerCatalogSelect";
import { formatNumber } from "@/lib/format";
import {
  DisabledHint,
  EmptyState,
  ErrorBanner,
  StatusBadge,
  apiError,
} from "@/components/common/kit";
import {
  COMPARISON_MISSING_CORE_LABEL,
  keyFor,
  COMPARISON_AI_ERROR_LABEL,
  COMPARISON_STATUS_LABEL,
  FACET_GROUP_LABEL,
  enumLabel,
} from "@/i18n/labels";
import { ReadingProgress } from "@/components/common/ReadingProgress";
import {
  aiErrorCode,
  aiServerMessage,
  isLostResponse,
  type AiErrorCode,
} from "@/lib/aiErrors";
import { useQueryClient } from "@tanstack/react-query";

/**
 * The honest copy for a failed comparison call: a TIMEOUT reads "La lectura
 * tardó más de lo esperado…", never "El proveedor de IA no está disponible"
 * (docs/v12/11 decision 7); anything else keeps the server's own message.
 */
function useComparisonErrorMessage() {
  const { t } = useTranslation("comparison");
  const { t: tc } = useTranslation("common");
  return React.useCallback(
    (error: unknown) => {
      const code = aiErrorCode(error);
      if (code) return aiServerMessage(error) ?? enumLabel(t, COMPARISON_AI_ERROR_LABEL, code);
      return apiError(error, tc("state.error"));
    },
    [t, tc],
  );
}

/** Where one cotización upload stands (the stepped bar). */
interface UploadProgress {
  name: string;
  index: number;
  total: number;
  /** 0 = filing the PDF, 1 = the AI read (`POST …/entries`). */
  step: 0 | 1;
  /** The answer was lost (504 / network): re-reading the comparison. */
  recovering: boolean;
}

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
  // "Open or create" the account's comparison as a QUERY (POST /comparisons is
  // idempotent per account): unlike a mutation's state, a query survives React
  // StrictMode's simulated unmount, which left this page on its skeleton in dev.
  const ensured = useEnsureComparison(Number.isFinite(id) ? id : undefined);
  const comparisonId = ensured.data?.id ?? null;

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
            <StatusBadge value={data.status} label={enumLabel(t, COMPARISON_STATUS_LABEL, data.status)} dot />
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

  if (ensured.isError) {
    return (
      <>
        {header}
        <ErrorBanner error={ensured.error} />
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

  const canCreate = useCan("Proposals", "Create");
  const canEdit = useCan("Proposals", "Edit");

  const qc = useQueryClient();
  const upload = useUploadDocument();
  const addEntry = useAddEntry(comparison.id);
  const retryEntry = useRetryEntry(comparison.id);
  const align = useAlign(comparison.id);
  const errorMessage = useComparisonErrorMessage();

  const inputRef = React.useRef<HTMLInputElement>(null);
  const [dragOver, setDragOver] = React.useState(false);
  const alignTimer = React.useRef<number | null>(null);
  const [progress, setProgress] = React.useState<UploadProgress | null>(null);
  const [alignRecovering, setAlignRecovering] = React.useState(false);
  // Stops a recovery poll when the board unmounts.
  const alive = React.useRef({ cancelled: false });
  React.useEffect(() => {
    const signal = { cancelled: false };
    alive.current = signal;
    return () => {
      signal.cancelled = true;
    };
  }, []);

  const busy = upload.isPending || addEntry.isPending || retryEntry.isPending || progress !== null;
  const aligning = align.isPending || alignRecovering;

  /**
   * A lost align answer (504 at the edge / network) is not a failed align: the
   * server may still finish (the Lambda runs up to 120 s, the edge gives up at
   * 60 s). Re-read the comparison for ~2 min; if it comes back aligned, clear
   * the error instead of showing the blocked banner.
   */
  const onAlignError = React.useCallback(
    async (error: unknown) => {
      if (!isLostResponse(error)) {
        toast.error(errorMessage(error));
        return;
      }
      setAlignRecovering(true);
      const aligned = await pollComparison(
        qc,
        comparison.id,
        (c) => c.status === "aligned",
        { attempts: 26, signal: alive.current },
      );
      setAlignRecovering(false);
      if (aligned) {
        align.reset();
        toast.success(t("align.recovered"));
      } else {
        toast.error(errorMessage(error));
      }
    },
    [align, comparison.id, errorMessage, qc, t],
  );

  const runAlign = React.useCallback(
    () =>
      align.mutate(undefined, {
        onSuccess: (res) => {
          if (res.warnings.length) toast.message(res.warnings[0]);
        },
        onError: (error) => void onAlignError(error),
      }),
    [align, onAlignError],
  );

  // Auto-align once a batch of reads settles: a fresh column supersedes the
  // last alignment (Edit permission required). It runs the same visible path
  // as the button — progress shown, Realinear disabled WITH the reason.
  const scheduleAutoAlign = React.useCallback(() => {
    if (!canEdit.allowed) return;
    if (alignTimer.current) window.clearTimeout(alignTimer.current);
    alignTimer.current = window.setTimeout(runAlign, 600);
  }, [canEdit.allowed, runAlign]);

  React.useEffect(
    () => () => {
      if (alignTimer.current) window.clearTimeout(alignTimer.current);
    },
    [],
  );

  /** Toast the outcome of one read; true when it produced a usable column. */
  const announce = (name: string, entry: ComparisonEntry | undefined, wrongFile: boolean): boolean => {
    if (wrongFile) {
      toast.warning(t("upload.rejected", { name }));
      return false;
    }
    if (entry && isFailedEntry(entry)) {
      toast.error(t("failed.toast", { name }));
      return false;
    }
    toast.success(t("upload.added", { name }));
    return true;
  };

  /**
   * Read one already-filed document into a column. A lost answer (504 / a
   * dropped connection) is not a failed read: look for this document's column
   * before calling it lost. Either way the comparison is re-read, so the new
   * column shows at once — never only after a reload.
   */
  const readDocument = async (
    documentId: number,
    base: { name: string; index: number; total: number },
    read: () => Promise<ComparisonEntryResult>,
    /** Which column proves the read landed (default: any column of this document). */
    landed: (c: Comparison) => ComparisonEntry | undefined = (c) =>
      c.entries.find((e) => e.document_id === documentId),
  ): Promise<boolean> => {
    setProgress({ ...base, step: 1, recovering: false });
    try {
      const result = await read();
      return announce(base.name, result.entry, result.is_wrong_file);
    } catch (error) {
      if (!isLostResponse(error)) {
        toast.error(errorMessage(error));
        return false;
      }
      setProgress({ ...base, step: 1, recovering: true });
      const found = await pollComparison(qc, comparison.id, (c) => !!landed(c), {
        signal: alive.current,
      });
      const entry = found ? landed(found) : undefined;
      if (entry) return announce(base.name, entry, entry.is_wrong_file);
      toast.error(errorMessage(error));
      return false;
    } finally {
      void qc.invalidateQueries({ queryKey: qk.comparisons.detail(comparison.id) });
    }
  };

  const handleFiles = async (files: FileList | null) => {
    if (!files || files.length === 0) return;
    const list = Array.from(files);
    let added = 0;
    for (const [index, file] of list.entries()) {
      const base = { name: file.name, index: index + 1, total: list.length };
      setProgress({ ...base, step: 0, recovering: false });
      let documentId: number;
      try {
        const doc = await upload.mutateAsync({
          file,
          entity_type: "case_file",
          entity_id: caseId,
          category: "insurer_quotation",
        });
        documentId = doc.id;
      } catch (error) {
        toast.error(errorMessage(error));
        continue;
      }
      const ok = await readDocument(documentId, base, () =>
        addEntry.mutateAsync({ document_id: documentId }),
      );
      if (ok) added += 1;
    }
    setProgress(null);
    if (inputRef.current) inputRef.current.value = "";
    if (added > 0) scheduleAutoAlign();
  };

  /** *Reintentar* on a failed column: its document, read again into it. */
  const retry = async (entry: ComparisonEntry, label: string) => {
    if (entry.document_id == null || busy) return;
    const before = entry.source_extraction_id;
    const ok = await readDocument(
      entry.document_id,
      { name: label, index: 1, total: 1 },
      () => retryEntry.mutateAsync(entry.id),
      // The same column, pointing at a NEW read attempt.
      (c) => c.entries.find((e) => e.id === entry.id && e.source_extraction_id !== before),
    );
    setProgress(null);
    if (ok) scheduleAutoAlign();
  };

  const uploadHint = !canCreate.allowed ? t("upload.noPermission") : null;

  const entries = comparison.entries;
  const rejectedEntries = entries.filter((e) => e.is_wrong_file);
  const failedEntries = entries.filter((e) => !e.is_wrong_file && isFailedEntry(e));
  const liveEntries = entries.filter((e) => !e.is_wrong_file && !isFailedEntry(e));
  const matrix = comparison.aligned_matrix;
  const hasDimensions = !!matrix && (matrix.dimensions ?? []).length > 0;
  // Columns the last alignment has not seen yet (read after it ran).
  const alignedIds = new Set(
    (matrix?.columns ?? []).map((c) => c.comparison_source_id).filter((id) => id != null),
  );
  const pendingColumns = hasDimensions
    ? liveEntries.filter((e) =>
        typeof e.aligned === "boolean"
          ? !e.aligned
          : e.comparison_source_id != null && !alignedIds.has(e.comparison_source_id),
      ).length
    : 0;

  // BLOCK-ON-PROVIDER-DOWN: a 502/503/504 on align means the comparison was NOT
  // aligned — surface a persistent, retryable banner instead of an empty grid.
  const providerDown = align.isError && !alignRecovering && isProviderDown(align.error);
  const alignErrorCode: AiErrorCode | null = providerDown ? aiErrorCode(align.error) : null;
  // The readings were run in batches then merged (large input) — surfaced subtly.
  const batched = matrix?.batched ?? false;
  // The AI's recommended offer (first-class field, mirrored inside the matrix).
  const recommendation = comparison.recommendation ?? matrix?.recommendation ?? null;

  /** Why Realinear cannot run right now — always said, never a silent grey button. */
  const alignBlocked = !canEdit.allowed
    ? t("align.noPermission")
    : aligning
      ? t("align.running")
      : busy
        ? t("align.waitReading")
        : liveEntries.length === 0
          ? t("align.needColumns")
          : null;

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

            <div className="flex flex-col items-end gap-1">
              <div className="flex items-center gap-2">
                {batched ? (
                  <Badge variant="outline" className="gap-1 text-ink-3">
                    <Layers className="h-3 w-3" />
                    {t("align.batched")}
                  </Badge>
                ) : null}
                <DisabledHint hint={alignBlocked}>
                  <Button
                    variant="secondary"
                    size="sm"
                    disabled={!!alignBlocked}
                    onClick={runAlign}
                  >
                    {aligning ? (
                      <RefreshCw className="h-4 w-4 animate-spin motion-reduce:animate-none" />
                    ) : (
                      <Sparkles className="h-4 w-4" />
                    )}
                    {aligning ? t("align.runningShort") : t("align.action")}
                  </Button>
                </DisabledHint>
              </div>
              {/* Visible, not only a tooltip: on a phone the reason must show. */}
              {alignBlocked && !aligning && canEdit.allowed ? (
                <span className="max-w-[260px] text-right text-caption text-ink-3">
                  {alignBlocked}
                </span>
              ) : null}
            </div>
          </div>

          {/* ── The read in progress: a stepped bar, never a still screen ── */}
          {progress ? (
            <ReadingProgress
              key={`${progress.index}-${progress.name}`}
              className="mt-3"
              title={progress.recovering ? t("upload.recovering") : t("upload.working")}
              hint={
                progress.recovering
                  ? t("upload.recoveringHint")
                  : t("upload.progressHint", {
                      name: progress.name,
                      index: progress.index,
                      total: progress.total,
                    })
              }
              active={progress.step}
              steps={[
                { label: t("upload.steps.uploading"), expectedSeconds: 3 },
                {
                  label: progress.recovering
                    ? t("upload.steps.checking")
                    : t("upload.steps.reading"),
                  expectedSeconds: progress.recovering ? 30 : 60,
                },
                { label: t("upload.steps.added") },
              ]}
            />
          ) : aligning ? (
            <ReadingProgress
              className="mt-3"
              title={alignRecovering ? t("align.recovering") : t("align.running")}
              hint={t("align.runningHint", { count: liveEntries.length })}
              active={alignRecovering ? 1 : 0}
              steps={[
                { label: t("align.steps.standardizing"), expectedSeconds: 50 },
                { label: t("align.steps.checking"), expectedSeconds: 60 },
                { label: t("align.steps.done") },
              ]}
            />
          ) : null}
        </Card>
      </FadeUp>

      {/* ── Block-on-provider-down: retryable, never a broken grid ── */}
      {providerDown ? (
        <AlignBlockedBanner
          onRetry={runAlign}
          pending={align.isPending}
          timeout={alignErrorCode === "ai_timeout"}
        />
      ) : null}

      {/* ── AI recommendation ── */}
      {recommendation && liveEntries.length > 0 ? (
        <RecommendationCard
          recommendation={recommendation}
          liveEntries={liveEntries}
        />
      ) : null}

      {/* ── Failed reads (A2): visibly failed, with Reintentar ── */}
      {failedEntries.length > 0 ? (
        <div className="flex flex-col gap-2">
          {failedEntries.map((entry) => (
            <FailedEntryCard
              key={entry.id}
              entry={entry}
              comparisonId={comparison.id}
              busy={busy}
              onRetry={retry}
            />
          ))}
        </div>
      ) : null}

      {/* ── Rejected uploads ── */}
      {rejectedEntries.length > 0 ? (
        <div className="flex flex-col gap-2">
          {rejectedEntries.map((entry) => (
            <RejectionCard key={entry.id} entry={entry} comparisonId={comparison.id} />
          ))}
        </div>
      ) : null}

      {/* ── The grid: every read column shows at once, aligned or not ── */}
      {liveEntries.length === 0 ? (
        <Card>
          <EmptyState
            icon={<Plus />}
            title={t("empty.title")}
            hint={t("empty.hint")}
          />
        </Card>
      ) : (
        <ComparisonGrid
          comparison={comparison}
          liveEntries={liveEntries}
          matrix={hasDimensions ? matrix : null}
          recommendation={recommendation}
          busy={busy}
          onRetry={retry}
          alignNotice={
            !hasDimensions || pendingColumns > 0 ? (
              <AlignNotice
                pendingColumns={pendingColumns}
                aligning={aligning}
                blocked={alignBlocked}
                onAlign={runAlign}
                incompleteColumns={liveEntries.some((e) => (e.missing_core ?? []).length > 0)}
              />
            ) : null
          }
        />
      )}
    </div>
  );
}

/** A read that failed or came back empty (A2) — never a normal-looking column. */
function isFailedEntry(entry: ComparisonEntry): boolean {
  return entry.status === "failed" || entry.status === "empty";
}

/**
 * The columns are visible but not compared yet (no alignment, or columns read
 * after the last one): say so inside the grid, with the action and its state.
 */
function AlignNotice({
  pendingColumns,
  aligning,
  blocked,
  onAlign,
  incompleteColumns = false,
}: {
  pendingColumns: number;
  aligning: boolean;
  blocked: string | null;
  onAlign: () => void;
  /** Some column read without compañía / prima: never claim they show them (r3). */
  incompleteColumns?: boolean;
}) {
  const { t } = useTranslation("comparison");
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 px-4 py-3">
      <div className="flex min-w-0 items-start gap-2">
        <Sparkles className="mt-0.5 h-4 w-4 shrink-0 text-brand" />
        <div className="min-w-0">
          <div className="text-body font-medium text-ink">
            {aligning
              ? t("align.running")
              : pendingColumns > 0
                ? t("needAlign.pendingTitle", { count: pendingColumns })
                : t("needAlign.title")}
          </div>
          <p className="text-caption text-ink-3">
            {aligning
              ? t("align.runningInline")
              : incompleteColumns
                ? t("needAlign.hintIncomplete")
                : t("needAlign.hint")}
          </p>
        </div>
      </div>
      <DisabledHint hint={blocked}>
        <Button size="sm" variant="secondary" disabled={!!blocked} onClick={onAlign}>
          {aligning ? (
            <RefreshCw className="h-4 w-4 animate-spin motion-reduce:animate-none" />
          ) : (
            <Sparkles className="h-4 w-4" />
          )}
          {aligning ? t("align.runningShort") : t("align.action")}
        </Button>
      </DisabledHint>
    </div>
  );
}

/** A failed column (A2): the honest reason, the source PDF, Reintentar and Quitar. */
function FailedEntryCard({
  entry,
  comparisonId,
  busy,
  onRetry,
}: {
  entry: ComparisonEntry;
  comparisonId: number;
  busy: boolean;
  onRetry: (entry: ComparisonEntry, label: string) => void;
}) {
  const { t } = useTranslation("comparison");
  const { t: tc } = useTranslation("common");
  const canCreate = useCan("Proposals", "Create");
  const canEdit = useCan("Proposals", "Edit");
  const remove = useDeleteEntry(comparisonId);

  const code = entry.error_code ?? null;
  const reason =
    entry.error_message ||
    (code && Object.prototype.hasOwnProperty.call(COMPARISON_AI_ERROR_LABEL, code)
      ? enumLabel(t, COMPARISON_AI_ERROR_LABEL, code)
      : entry.status === "empty"
        ? t("failed.emptyReason")
        : t("failed.reasonFallback"));
  const label = entry.insurer_name || t("failed.unnamed");

  const retryBlocked =
    !canCreate.allowed
      ? t("failed.noPermission")
      : entry.document_id == null
        ? t("failed.noDocument")
        : busy
          ? t("align.waitReading")
          : null;

  return (
    <Card className="border-neg-line bg-neg-soft/30 p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex min-w-0 items-start gap-2.5">
          <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-neg-soft text-neg-text">
            <TriangleAlert className="h-4 w-4" />
          </span>
          <div className="min-w-0">
            <div className="text-body font-medium text-ink">
              {t("failed.title", { name: label })}
            </div>
            <p className="mt-0.5 text-caption text-ink-2">{reason}</p>
            <SourceDocLink documentId={entry.document_id} />
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          <DisabledHint hint={retryBlocked}>
            <Button
              variant="secondary"
              size="sm"
              disabled={!!retryBlocked}
              onClick={() => onRetry(entry, label)}
            >
              <RefreshCw className="h-4 w-4" />
              {t("failed.retry")}
            </Button>
          </DisabledHint>
          <DisabledHint hint={!canEdit.allowed ? t("remove.noPermission") : null}>
            <Button
              variant="ghost"
              size="sm"
              disabled={!canEdit.allowed || remove.isPending || busy}
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
      </div>
    </Card>
  );
}

// =============================================================================
// AI recommendation
// =============================================================================

/** The label the grid header shows for an entry — reused so the recommendation
 *  card names the exact same column: the insurer read from the cotización,
 *  else "Propuesta" / "Cotización N". */
function useColumnLabel() {
  const { t } = useTranslation("comparison");
  return React.useCallback(
    (entry: ComparisonEntry | undefined, index: number) =>
      entry?.insurer_name?.trim()
        ? entry.insurer_name.trim()
        : entry?.proposal_id != null
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
      <Card className="border-brand-line bg-brand-soft/30 p-4 sm:p-5">
        <div className="flex items-start gap-3">
          {/* Below sm the icon would eat ~50 px of a 330 px card (review r3). */}
          <span className="mt-0.5 hidden h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-brand-soft text-brand-deep sm:flex">
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
  timeout,
}: {
  onRetry: () => void;
  pending: boolean;
  /** A timeout, not an outage — different, honest copy. */
  timeout: boolean;
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
                {timeout ? t("align.blocked.timeoutTitle") : t("align.blocked.title")}
              </div>
              <p className="mt-0.5 text-caption text-ink-2">
                {timeout ? t("align.blocked.timeoutHint") : t("align.blocked.hint")}
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

// The sticky label column is OPAQUE on every row (review r2: at ~546 px the
// tinted premium rows let the scrolled values show through the labels).
//
// Review r3 (F1): it is also NARROW and its labels WRAP. In an auto-layout
// table a cell's width is only a preference — a `truncate` (nowrap) label made
// the column as wide as its longest label (~460 px inside a 330 px phone
// card), so the sticky column covered every value. Now nothing in it is
// nowrap: ~35 % of a 375 px screen, 220 px from `sm`, 260 px from `lg`.
const LABEL_CELL =
  "sticky left-0 z-20 w-[128px] min-w-[128px] max-w-[128px] border-r border-line bg-bg-surface px-2.5 py-2.5 text-left align-top text-caption font-medium text-ink-3 whitespace-normal [overflow-wrap:anywhere] sm:w-[220px] sm:min-w-[220px] sm:max-w-[220px] sm:px-4 lg:w-[260px] lg:min-w-[260px] lg:max-w-[260px]";
const COL_WIDTH = "min-w-[184px] sm:min-w-[248px]";

/** A display that is only a quantity ("10", "UF 5", "15 %") — no condition words. */
const QUANTITY_ONLY = /^(?:UF\s|US\$\s|\$\s)?-?[\d.]+(?:,\d+)?(?:\s?(?:%|‰))?$/;
/** Wording that qualifies a sublimit amount ("máx UF 25.000", "otros UF 3", "por pieza"). */
const QUALIFIER = /(?:m[aá]x|m[ií]n|otros?\b|por\s|hasta|tope|evento|agregado)/i;
/** Verbatims longer than this are clamped in the cell, full text in the tooltip. */
const LONG_VERBATIM = 140;

/**
 * What a standardized cell SAYS (review r3, F2 / R2). A deductible is its
 * condition — "10 % de la pérdida, mínimo UF 25" — never a bare "10": two
 * offers with different minimums must not read the same. When the value is
 * only a quantity and the verbatim carries the condition (deductibles, a
 * qualified sublimit, or any unit-less number), the verbatim IS the display.
 */
function cellDisplay(
  cell: ComparisonDimensionCell,
  group: string,
  t: (key: string, opts?: Record<string, unknown>) => string,
): { text: string | null; verbatim: string | null; clamp: boolean } {
  const formatted = formatDimValue(cell.value, cell.unit, t);
  const verbatim = cell.verbatim?.trim() || null;
  const quantityOnly = formatted == null || QUANTITY_ONLY.test(formatted);
  const unitless = formatted != null && /^-?[\d.]+(?:,\d+)?$/.test(formatted);
  const prefersVerbatim =
    !!verbatim &&
    verbatim !== formatted &&
    ((group === "deductible" && quantityOnly) ||
      (group === "sublimit" && quantityOnly && QUALIFIER.test(verbatim)) ||
      unitless);
  if (prefersVerbatim && verbatim) {
    const text = formatDimValue(verbatim, null, t) ?? verbatim;
    const long = verbatim.length > LONG_VERBATIM;
    // A clamped verbatim keeps "Texto original" for the full wording.
    return { text, verbatim: long ? verbatim : null, clamp: long };
  }
  return {
    text: formatted,
    verbatim: verbatim && verbatim !== formatted ? verbatim : null,
    clamp: false,
  };
}

/** Chilean number with only the decimals it needs: 15000 → "15.000", 57.03 → "57,03". */
function chileanNumber(value: number): string {
  return formatNumber(value, Number.isInteger(value) ? 0 : 2);
}

/** A money/rate cell of the premium rows: "UF 1.181,28", "—" when absent (never "UF —"). */
function ufCell(value: DecimalString | number | null | undefined): string {
  const n = num(value);
  return n === null ? "—" : `UF ${formatNumber(n, 2)}`;
}

function pctCell(value: DecimalString | number | null | undefined): string {
  const n = num(value);
  return n === null ? "—" : `${chileanNumber(n)} %`;
}

function permilleCell(value: DecimalString | number | null | undefined): string {
  const n = num(value);
  return n === null ? "—" : `${formatNumber(n, Number.isInteger(n) ? 0 : 3)} ‰`;
}

/**
 * Render a standardized dimension value: numbers in Chilean style with their
 * unit ("UF 15.000", "25 %"); a bare-number string the same way; "UF 20000"
 * inside a phrase gets its thousands separator. Free prose stays verbatim.
 */
function formatDimValue(
  value: string | number | null | undefined,
  unit: string | null | undefined,
  t: (key: string, opts?: Record<string, unknown>) => string,
): string | null {
  if (value === null || value === undefined) return null;
  const withUnit = (n: number): string => {
    switch ((unit ?? "").toLowerCase()) {
      case "uf":
        return `UF ${chileanNumber(n)}`;
      case "pct":
      case "%":
        return `${chileanNumber(n)} %`;
      case "permille":
      case "‰":
        return `${chileanNumber(n)} ‰`;
      case "days":
        return t("grid.units.days", { count: n });
      case "hours":
        return t("grid.units.hours", { count: n });
      case "clp":
        return `$ ${formatNumber(n, 0)}`;
      case "usd":
        return `US$ ${chileanNumber(n)}`;
      default:
        return chileanNumber(n);
    }
  };
  if (typeof value === "number") return num(value) != null ? withUnit(value) : null;
  const trimmed = value.trim();
  if (!trimmed) return null;
  // A bare number sent as text ("15000", "57.03").
  if (/^-?\d+(\.\d+)?$/.test(trimmed)) return withUnit(Number(trimmed));
  // "25%" / "25 %".
  const pctMatch = /^(-?\d+(?:[.,]\d+)?)\s*%$/.exec(trimmed);
  if (pctMatch) return `${chileanNumber(Number(pctMatch[1].replace(",", ".")))} %`;
  // "UF 20000" / "UF20000.5" anywhere in a phrase → "UF 20.000".
  return trimmed.replace(/\bUF\s?(\d{4,})(\.\d{1,2})?\b/gi, (_m, int: string, dec?: string) =>
    `UF ${chileanNumber(Number(`${int}${dec ?? ""}`))}`,
  );
}

function ComparisonGrid({
  comparison,
  liveEntries,
  matrix,
  recommendation,
  alignNotice,
  busy,
  onRetry,
}: {
  comparison: Comparison;
  liveEntries: ComparisonEntry[];
  /** Null until an alignment produced dimensions — the columns still show. */
  matrix: Comparison["aligned_matrix"];
  recommendation: ComparisonRecommendation | null;
  /** "Listo para alinear" / "N columnas sin alinear" — shown inside the grid card. */
  alignNotice?: React.ReactNode;
  /** A read / retry is running: Reintentar waits for it (said, not greyed silently). */
  busy: boolean;
  /** *Reintentar* a column that read but lacks its core (r3 R5/F5). */
  onRetry: (entry: ComparisonEntry, label: string) => void;
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
  const dimensions = matrix?.dimensions ?? [];
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
        {alignNotice ? <div className="border-b border-line">{alignNotice}</div> : null}
        {/* On a phone each swipe lands a company column flush against the
            sticky labels (scroll-padding = the label width), never half
            under them (review r3 F1). */}
        <div className="overflow-x-auto max-sm:snap-x max-sm:snap-mandatory max-sm:scroll-pl-[128px]">
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
                      "border-l border-line bg-bg-surface p-4 text-left align-top max-sm:snap-start",
                    )}
                  >
                    <ColumnHeader
                      comparison={comparison}
                      entry={entry}
                      index={index}
                      recommendedByAi={isRecommendedByAi(entry)}
                      busy={busy}
                      onRetry={onRetry}
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
                    render={(p) => ufCell(p.total_premium_uf)}
                    lowestIndex={lowestTotalIndex}
                    lowestLabel={t("grid.lowest")}
                  />
                  <PremiumRow
                    label={t("grid.premium.net")}
                    columns={columns}
                    render={(p) => ufCell(p.net_premium_uf)}
                  />
                  <PremiumRow
                    label={t("grid.premium.comprehensiveRate")}
                    columns={columns}
                    render={(p) => permilleCell(p.comprehensive_rate_permille)}
                  />
                  <PremiumRow
                    label={t("grid.premium.commission")}
                    columns={columns}
                    render={(p) => pctCell(p.commission_pct)}
                  />
                </tbody>
              </>
            ) : null}

            {/* ── The standardized dimensions, once an alignment produced them ── */}
            {matrix ? (
              <>
                <SectionRow
                  label={t("grid.sections.common")}
                  span={columns.length + 1}
                />
                <DimensionBlock
                  dimensions={commonDims}
                  columns={columns}
                  emptyLabel={t("grid.noCommon")}
                />
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
              </>
            ) : null}
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
      <td className={cn(LABEL_CELL, "font-medium text-ink")}>
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
                <span className="flex flex-col gap-1">
                  {/* Wraps (never nowrap): see LABEL_CELL. */}
                  <span className="block text-ink [text-wrap:pretty]">
                    {dim.label ?? dim.key}
                  </span>
                  <Badge variant="outline" className="w-fit max-w-full">
                    {enumLabel(t, FACET_GROUP_LABEL, dim.group, String(dim.group))}
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
                    <DimensionCell cell={cell} group={String(dim.group ?? "")} />
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

function DimensionCell({
  cell,
  group,
}: {
  cell: ComparisonDimensionCell | undefined;
  /**
   * The row's facet group. An EXCLUSION row (A4): `present=true` means the
   * offer INCLUDES the exclusion — it applies. Read the same way in every
   * column. A DEDUCTIBLE row shows its full condition (r3 F2).
   */
  group: string;
}) {
  const { t } = useTranslation("comparison");

  if (!cell) {
    return (
      <span className="flex items-center gap-1.5 text-ink-3">
        <Minus className="h-3.5 w-3.5" />
        {t("grid.absent")}
      </span>
    );
  }

  const exclusion = group === "exclusion";
  const display = cellDisplay(cell, group, t);
  const { text: value, clamp } = display;
  const valueClass = clamp ? "line-clamp-4" : undefined;
  // A cover priced at "UF 0" is a cover the offer does not include (r3: PxP
  // "UF 0" read as included); its wording stays one tap away.
  const zeroCover =
    (group === "coverage" || group === "sublimit") && num(cell.value ?? null) === 0;
  const verbatim = zeroCover ? cell.verbatim?.trim() || null : display.verbatim;
  const verbatimLink = verbatim ? (
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
  ) : null;

  if (exclusion) {
    if (cell.present === false) {
      return (
        <span className="flex items-start gap-1.5 text-pos-text">
          <Check className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          <span className="flex min-w-0 flex-col gap-0.5">
            <span className="text-ink">{t("grid.exclusionNotApplied")}</span>
            {verbatimLink}
          </span>
        </span>
      );
    }
    return (
      <span className="flex items-start gap-1.5 text-warn-text">
        <TriangleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" />
        <span className="flex min-w-0 flex-col gap-0.5">
          <span className={cn("text-ink", valueClass)}>{value ?? t("grid.exclusionApplies")}</span>
          {verbatimLink}
        </span>
      </span>
    );
  }

  if (cell.present === false || zeroCover) {
    return (
      <span className="flex items-start gap-1.5 text-neg-text">
        <Minus className="mt-0.5 h-3.5 w-3.5 shrink-0" />
        <span className="flex min-w-0 flex-col gap-0.5">
          <span className={valueClass}>
            {zeroCover ? t("grid.notIncluded") : (value ?? t("grid.notIncluded"))}
          </span>
          {verbatimLink}
        </span>
      </span>
    );
  }

  return (
    <span className="flex items-start gap-1.5 text-pos-text">
      <Check className="mt-0.5 h-3.5 w-3.5 shrink-0" />
      <span className="flex min-w-0 flex-col gap-0.5">
        <span className={cn("text-ink [text-wrap:pretty]", valueClass)}>
          {value ?? t("grid.included")}
        </span>
        {verbatimLink}
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
          {/* Sticky inside the full-width cell: in view while the grid scrolls. */}
          <span className="sticky left-4 inline-flex max-w-[calc(100vw-4rem)] flex-wrap items-center gap-x-2 gap-y-0.5">
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
  busy,
  onRetry,
}: {
  comparison: Comparison;
  entry: ComparisonEntry;
  index: number;
  recommendedByAi?: boolean;
  busy: boolean;
  onRetry: (entry: ComparisonEntry, label: string) => void;
}) {
  const { t } = useTranslation("comparison");
  const { t: tc } = useTranslation("common");

  const canEdit = useCan("Proposals", "Edit");
  const canApprove = useCan("Proposals", "Approve");
  const canCreate = useCan("Proposals", "Create");

  const patch = usePatchEntry(comparison.id);
  const remove = useDeleteEntry(comparison.id);

  const [promoteOpen, setPromoteOpen] = React.useState(false);
  const columnLabel = useColumnLabel();

  const promoted = entry.proposal_id != null;
  const hasInsurerName = !!entry.insurer_name?.trim();
  // A hint, not a block: what the read did not find (only labelled keys).
  const missingCore = (entry.missing_core ?? [])
    .map((key) => keyFor(COMPARISON_MISSING_CORE_LABEL, key))
    .filter((key): key is NonNullable<typeof key> => !!key)
    .map((key) => t(key));

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

  // r3 R5/F5: a column that READ but lacks its core (old reads without
  // compañía / prima) can be read again — the server says so with `can_retry`.
  const canRetry = entry.can_retry === true;
  const retryBlocked = !canCreate.allowed
    ? t("failed.noPermission")
    : entry.document_id == null
      ? t("failed.noDocument")
      : busy
        ? t("align.waitReading")
        : null;

  const promoteHint = !canApprove.allowed
    ? t("promote.noPermission")
    : promoted
      ? t("promote.already")
      : null;

  return (
    <div className="flex flex-col gap-2.5">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="truncate text-h3 text-ink" title={columnLabel(entry, index)}>
            {columnLabel(entry, index)}
          </div>
          {hasInsurerName ? (
            <div className="text-caption text-ink-3">
              {t("column.quotation", { n: index + 1 })}
            </div>
          ) : null}
          {missingCore.length > 0 ? (
            <div className="mt-0.5 flex items-start gap-1 text-caption text-warn-text">
              <TriangleAlert className="mt-0.5 h-3 w-3 shrink-0" />
              <span>{t("missingCore.hint", { fields: missingCore.join(", ") })}</span>
            </div>
          ) : null}
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
        {canRetry ? (
          <DisabledHint hint={retryBlocked}>
            <Button
              variant="secondary"
              size="sm"
              disabled={!!retryBlocked}
              onClick={() => onRetry(entry, columnLabel(entry, index))}
            >
              <RefreshCw className="h-4 w-4" />
              {t("failed.retry")}
            </Button>
          </DisabledHint>
        ) : null}
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
// Promote dialog — pick the insurer (review r2 C3 / D3)
// =============================================================================

/** The server's own Spanish explanation of a structured 4xx (`reason`, else `message`). */
function serverReason(error: unknown): string | null {
  const detail = (
    error as { response?: { data?: { detail?: unknown } } } | undefined
  )?.response?.data?.detail;
  if (detail && typeof detail === "object" && !Array.isArray(detail)) {
    const { reason, message } = detail as { reason?: unknown; message?: unknown };
    if (typeof reason === "string" && reason.trim()) return reason;
    if (typeof message === "string" && message.trim()) return message;
  }
  return null;
}

/**
 * Promote a column into a proposal. The insurer is CHOSEN, like in Subir
 * póliza: the server's `insurer_suggestions` for the name read come first
 * (the resolved `insurer_id`, else the best match, preselected), then the
 * catalogue. Typing the RUT / CMF code is only the "¿No está en la lista?"
 * fallback. The account's colocación is created on demand server-side (A5),
 * and every refusal shows the server's own Spanish reason inline — the dialog
 * stays open, never a dead end.
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
  const { t: tp } = useTranslation("policies");
  const { t: tc } = useTranslation("common");

  const promote = usePromoteEntry(comparisonId);

  const suggestions = React.useMemo(
    () => (entry.insurer_suggestions ?? []).map((s) => ({ id: s.id, name: s.name })),
    [entry.insurer_suggestions],
  );
  const catalog = useInsurerCatalog(suggestions);
  const preselected =
    entry.insurer_id != null ? String(entry.insurer_id) : suggestions[0] ? String(suggestions[0].id) : "";

  const [selected, setSelected] = React.useState(preselected);
  const [manual, setManual] = React.useState(false);
  const [rut, setRut] = React.useState("");
  const [cmf, setCmf] = React.useState("");
  const [error, setError] = React.useState<string | null>(null);

  // Reset the form each time the dialog opens for a fresh attempt.
  React.useEffect(() => {
    if (open) {
      setSelected(preselected);
      setManual(false);
      setRut("");
      setCmf("");
      setError(null);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  // No catalogue to pick from (no permission, empty): typing is the only path.
  const typing = manual || !!catalog.blocked;
  const submitBlocked = typing
    ? rut.trim() || cmf.trim()
      ? null
      : t("promote.dialog.typeFirst")
    : selected
      ? null
      : tp("upload.insurer.pickFirst");

  const submit = () => {
    if (submitBlocked) return;
    setError(null);
    const payload = typing
      ? { insurer_rut: rut.trim() || null, insurer_cmf_code: cmf.trim() || null }
      : { insurer_id: Number(selected) };
    promote.mutate(
      { entryId: entry.id, ...payload },
      {
        onSuccess: (result) => {
          toast.success(
            result.placement_created ? t("promote.doneWithPlacement") : t("promote.done"),
          );
          result.warnings?.forEach((warning) => toast.message(warning));
          onOpenChange(false);
        },
        onError: (err) => {
          const reason = serverReason(err);
          const code = errorCode(err);
          if (reason) setError(reason);
          else if (code === "insurer_unresolved" || code === "insurer_identity_incomplete")
            setError(t("promote.dialog.errors.insurerUnresolved"));
          else if (code === "insurer_not_found") setError(tp("upload.insurer.notFound"));
          else if (code === "money_inconsistent") setError(t("promote.dialog.errors.moneyInconsistent"));
          else setError(apiError(err, tc("state.error")));
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
          {(entry.insurer_read_name ?? entry.insurer_name)?.trim() ? (
            <div className="flex flex-col gap-1 rounded-lg border border-line bg-bone p-3">
              <div className="text-caption text-ink-3">{t("promote.dialog.readName")}</div>
              <div className="text-body font-medium text-ink">
                {entry.insurer_read_name ?? entry.insurer_name}
              </div>
            </div>
          ) : null}

          {!catalog.blocked ? (
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="promote-insurer">{t("promote.dialog.insurerLabel")}</Label>
              <InsurerCatalogSelect
                id="promote-insurer"
                catalog={catalog}
                value={manual ? "" : selected}
                onChange={(value) => {
                  setSelected(value);
                  setManual(false);
                }}
              />
              <button
                type="button"
                className="self-start text-caption font-medium text-brand underline-offset-4 hover:text-brand-deep hover:underline"
                aria-expanded={manual}
                onClick={() => setManual((v) => !v)}
              >
                {manual ? t("promote.dialog.backToList") : t("promote.dialog.notInList")}
              </button>
            </div>
          ) : (
            <p className="text-caption text-ink-3">{catalog.blocked}</p>
          )}

          {typing ? (
            <div className="flex flex-col gap-3 rounded-lg border border-line p-3">
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="promote-insurer-rut">{t("promote.dialog.rutLabel")}</Label>
                <Input
                  id="promote-insurer-rut"
                  value={rut}
                  onChange={(e) => setRut(e.target.value)}
                  placeholder={t("promote.dialog.rutPlaceholder")}
                  autoComplete="off"
                />
              </div>
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="promote-insurer-cmf">{t("promote.dialog.cmfLabel")}</Label>
                <Input
                  id="promote-insurer-cmf"
                  value={cmf}
                  onChange={(e) => setCmf(e.target.value)}
                  placeholder={t("promote.dialog.cmfPlaceholder")}
                  autoComplete="off"
                />
                <span className="text-caption text-ink-3">{t("promote.dialog.helper")}</span>
              </div>
            </div>
          ) : null}

          {error ? (
            <div
              role="alert"
              className="flex items-start gap-2 rounded-sm border border-neg-line bg-neg-soft/50 px-3 py-2 text-caption text-neg-text"
            >
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
            <DisabledHint hint={submitBlocked}>
              <Button type="submit" size="sm" disabled={promote.isPending || !!submitBlocked}>
                <Sparkles
                  className={cn("h-4 w-4", promote.isPending && "animate-pulse")}
                />
                {t("promote.dialog.confirm")}
              </Button>
            </DisabledHint>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

/** The column's source PDF — a fresh link on click (`lib/download.ts`). */
function SourceDocLink({ documentId }: { documentId: number | null }) {
  const { t } = useTranslation("comparison");
  if (documentId == null) return null;
  return (
    <PreviewDocButton
      documentId={documentId}
      variant="ghost"
      size="sm"
      icon={<FileText className="h-4 w-4" aria-hidden />}
      label={t("column.document")}
    />
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
