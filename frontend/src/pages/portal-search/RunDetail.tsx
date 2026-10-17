/**
 * One portal search, in a side sheet (`?run=ID`): status, the sanitised step
 * timeline, the plans found and — on failure — the explanation by
 * `error_kind` (explicit map, 06-rpa §4) plus the server's alternatives:
 * Reintentar · Subir cotización PDF · Otros portales. Every alternative the
 * server marks unavailable renders disabled with ITS reason.
 *
 * The detail polls while the run is queued or running (see `usePortalRun`).
 */
import * as React from "react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import {
  ArrowUpRight,
  CircleCheck,
  CircleX,
  FileUp,
  Image as ImageIcon,
  Loader2,
  RotateCcw,
  TriangleAlert,
} from "lucide-react";

import {
  runIsLive,
  useCancelPortalRun,
  usePortalRun,
  usePortals,
  useRetryPortalRun,
  type PortalQuote,
  type PortalRun,
  type PortalRunAlternative,
  type PortalRunSummary,
} from "@/api/rpa";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import {
  DisabledHint,
  ErrorBanner,
  KeyValue,
  SoonButton,
  apiError,
  resolveFileUrl,
  uf,
} from "@/components/common/kit";
import { formatDate, formatDateTime, formatNumber } from "@/lib/format";
import { cn } from "@/lib/utils";
import {
  FIELD_LABEL_KEY,
  OPTION_LABEL_KEY,
  RUN_STATUS_LABEL_KEY,
  RUN_STATUS_TONE,
  errorMessageKey,
  humanizeField,
  own,
  stepOpLabelKey,
} from "@/pages/portal-search/labels";

export function RunStatusBadge({
  run,
}: {
  run: Pick<PortalRunSummary, "status" | "will_not_run" | "will_not_run_reason">;
}) {
  const { t } = useTranslation("rpa");
  if (run.will_not_run && (run.status === "queued" || run.status === "running")) {
    // Never "En cola" forever: nothing will pick it up in this environment.
    return (
      <Badge variant="muted" dot title={run.will_not_run_reason ?? undefined}>
        {t("status.willNotRun")}
      </Badge>
    );
  }
  const status = run.status;
  return (
    <Badge variant={own(RUN_STATUS_TONE, status) ?? "neutral"} dot>
      {runIsLive(run) ? <Loader2 className="h-3 w-3 animate-spin" aria-hidden /> : null}
      {t(own(RUN_STATUS_LABEL_KEY, status) ?? "rpa:status.unknown")}
    </Badge>
  );
}

/** Subtype code -> its name, from the portals catalogue ("VEH-01" -> "Vehículos particulares"). */
export function useSubtypeName() {
  const portals = usePortals();
  return React.useCallback(
    (run: Pick<PortalRunSummary, "insurance_subtype_code" | "insurance_subtype_name">): string | null => {
      if (run.insurance_subtype_name) return run.insurance_subtype_name;
      const code = run.insurance_subtype_code;
      if (!code) return null;
      for (const portal of portals.data ?? []) {
        const hit = portal.subtypes.find((s) => s.code === code);
        if (hit?.name) return hit.name;
      }
      return code;
    },
    [portals.data],
  );
}

const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;

/** "Subir cotización PDF": the account's comparison when filed, else the upload page. */
export function uploadQuotePath(caseFileId: number | null | undefined): string {
  return caseFileId ? `/comparisons/${caseFileId}` : "/proposals/upload";
}

export function useInputFormatter() {
  const { t } = useTranslation("rpa");
  return React.useCallback(
    (name: string, value: unknown): string => {
      if (value === null || value === undefined || value === "") return "—";
      const options = own(OPTION_LABEL_KEY, name);
      const optionKey = options ? own(options, String(value)) : null;
      if (optionKey) return t(optionKey);
      // ISO dates read as Chilean dates ("27 oct 2026"), never "2026-10-27".
      if (typeof value === "string" && ISO_DATE.test(value)) return formatDate(value);
      return String(value);
    },
    [t],
  );
}

export function useFieldLabel() {
  const { t } = useTranslation("rpa");
  return React.useCallback(
    (name: string) => {
      const key = own(FIELD_LABEL_KEY, name);
      return key ? t(key) : humanizeField(name);
    },
    [t],
  );
}

export function RunDetailSheet({
  runId,
  onClose,
  onOpenRun,
  onOtherPortal,
  onConfigure,
  canSubmit,
  canManageCredentials,
}: {
  runId: number | null;
  onClose: () => void;
  /** Show another run (a retry creates a NEW run). */
  onOpenRun: (id: number) => void;
  /** Start a search on another portal with this run's data. */
  onOtherPortal: (portalKey: string, run: PortalRun) => void;
  onConfigure: () => void;
  canSubmit: boolean;
  canManageCredentials: boolean;
}) {
  const { t } = useTranslation("rpa");
  const run = usePortalRun(runId);
  const data = run.data;
  const subtypeName = useSubtypeName();

  return (
    <Sheet open={runId !== null} onOpenChange={(open) => (open ? null : onClose())}>
      <SheetContent side="right" className="w-full overflow-y-auto p-4 sm:max-w-xl sm:p-6">
        <SheetHeader className="pe-8">
          <SheetTitle className="flex flex-wrap items-center gap-2">
            {t("detail.title", { id: runId ?? "" })}
            {data ? <RunStatusBadge run={data} /> : null}
          </SheetTitle>
          <SheetDescription>
            {data
              ? t("detail.description", {
                  portal: data.portal_name,
                  subtype: subtypeName(data) ?? "—",
                })
              : null}
          </SheetDescription>
        </SheetHeader>

        <div className="mt-5 flex flex-col gap-5">
          {run.isLoading ? (
            <Skeleton className="h-48 w-full" />
          ) : run.isError || !data ? (
            <ErrorBanner error={run.error ?? t("detail.loadError")} />
          ) : (
            <RunBody
              run={data}
              onOpenRun={onOpenRun}
              onOtherPortal={onOtherPortal}
              onConfigure={onConfigure}
              canSubmit={canSubmit}
              canManageCredentials={canManageCredentials}
            />
          )}
        </div>
      </SheetContent>
    </Sheet>
  );
}

function RunBody({
  run,
  onOpenRun,
  onOtherPortal,
  onConfigure,
  canSubmit,
  canManageCredentials,
}: {
  run: PortalRun;
  onOpenRun: (id: number) => void;
  onOtherPortal: (portalKey: string, run: PortalRun) => void;
  onConfigure: () => void;
  canSubmit: boolean;
  canManageCredentials: boolean;
}) {
  const { t } = useTranslation("rpa");
  const fieldLabel = useFieldLabel();
  const formatInput = useInputFormatter();
  const cancel = useCancelPortalRun();
  const failed = run.status === "failed";
  const cancelled = run.status === "cancelled";
  const inputEntries = Object.entries(run.input ?? {});
  const screenshot = resolveFileUrl(run.screenshot_url);

  return (
    <>
      {run.will_not_run && (run.status === "queued" || run.status === "running") ? (
        <section className="flex items-start gap-2.5 rounded-card border border-line bg-bone p-4 shadow-elev">
          <TriangleAlert className="mt-0.5 h-4 w-4 shrink-0 text-warn" strokeWidth={1.5} aria-hidden />
          <div className="min-w-0">
            <p className="text-caption font-medium text-ink-3">{t("detail.willNotRunTitle")}</p>
            <p className="mt-0.5 text-body text-ink [text-wrap:pretty]">
              {run.will_not_run_reason || t("detail.willNotRunFallback")}
            </p>
            <div className="mt-3">
              <UploadAlternative
                alt={run.alternatives.find((a) => a.kind === "upload_quote_pdf")}
                caseFileId={run.case_file_id}
              />
            </div>
          </div>
        </section>
      ) : null}

      {/* Failure explanation + what to do next */}
      {failed || cancelled ? (
        <section className="flex flex-col gap-3 rounded-card border border-line bg-bone p-4 shadow-elev">
          <div className="flex items-start gap-2.5">
            <TriangleAlert
              className={cn("mt-0.5 h-4 w-4 shrink-0", failed ? "text-neg" : "text-ink-3")}
              strokeWidth={1.5}
              aria-hidden
            />
            <div className="min-w-0">
              <p className="text-caption font-medium text-ink-3">{t("detail.whyFailed")}</p>
              <p className="mt-0.5 text-body text-ink [text-wrap:pretty]">
                {failed
                  ? t(errorMessageKey(run.user_message_key ?? run.error_kind), {
                      portal: run.portal_name,
                    })
                  : t("errors.cancelled")}
              </p>
            </div>
          </div>
          {run.user_message_key === "credentials_missing" || run.user_message_key === "login_failed" ? (
            canManageCredentials ? (
              <Button type="button" size="sm" variant="secondary" className="min-h-10 w-fit sm:min-h-8" onClick={onConfigure}>
                {t("portal.configure")}
              </Button>
            ) : (
              <p className="text-caption text-ink-3">{t("credentials.noPermission")}</p>
            )
          ) : null}
          {screenshot ? (
            <a
              href={screenshot}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex min-h-8 w-fit items-center gap-1.5 text-caption font-medium text-brand underline-offset-4 hover:text-brand-deep hover:underline"
            >
              <ImageIcon className="h-3.5 w-3.5" strokeWidth={1.5} aria-hidden />
              {t("detail.screenshot")}
            </a>
          ) : null}
          <Alternatives
            run={run}
            onOpenRun={onOpenRun}
            onOtherPortal={onOtherPortal}
            canSubmit={canSubmit}
          />
        </section>
      ) : null}

      {/* Times */}
      <section className="grid grid-cols-2 gap-3 sm:grid-cols-3">
        <KeyValue label={t("detail.created")} value={formatDateTime(run.created_at)} />
        <KeyValue label={t("detail.started")} value={formatDateTime(run.started_at)} />
        <KeyValue label={t("detail.finished")} value={formatDateTime(run.finished_at)} />
        {run.connector_version ? (
          <KeyValue label={t("detail.version")} value={run.connector_version} />
        ) : null}
        {run.case_file_id ? (
          <KeyValue label={t("detail.case")} value={t("detail.caseValue", { id: run.case_file_id })} />
        ) : null}
      </section>

      {run.can_cancel ? (
        <DisabledHint hint={canSubmit ? null : t("form.noPermission")} className="w-fit">
          <Button
            type="button"
            size="sm"
            variant="secondary"
            className="min-h-10 sm:min-h-8"
            disabled={!canSubmit || cancel.isPending}
            onClick={() =>
              cancel.mutate(run.id, {
                onSuccess: () => toast.success(t("detail.cancelled")),
                onError: (error) => toast.error(apiError(error)),
              })
            }
          >
            {t("detail.cancel")}
          </Button>
        </DisabledHint>
      ) : null}

      {/* Input */}
      {inputEntries.length > 0 ? (
        <section className="flex flex-col gap-2">
          <h3 className="text-caption font-medium text-ink-3">{t("detail.input")}</h3>
          <dl className="grid grid-cols-2 gap-x-4 gap-y-2 rounded-lg bg-bg-recessed px-3 py-2.5">
            {inputEntries.map(([name, value]) => (
              <div key={name} className="min-w-0">
                <dt className="text-[11px] text-ink-3">{fieldLabel(name)}</dt>
                <dd className="truncate text-body tabular-nums text-ink">{formatInput(name, value)}</dd>
              </div>
            ))}
          </dl>
        </section>
      ) : null}

      <StepsTimeline run={run} />
      <QuotesBlock quotes={run.quotes} />
    </>
  );
}

function Alternatives({
  run,
  onOpenRun,
  onOtherPortal,
  canSubmit,
}: {
  run: PortalRun;
  onOpenRun: (id: number) => void;
  onOtherPortal: (portalKey: string, run: PortalRun) => void;
  canSubmit: boolean;
}) {
  const { t } = useTranslation("rpa");
  const retry = useRetryPortalRun();
  const retryAlt = run.alternatives.find((a) => a.kind === "retry");
  const uploadAlt = run.alternatives.find((a) => a.kind === "upload_quote_pdf");
  const others = run.alternatives.filter((a) => a.kind === "other_portal");

  const retryAvailable = (retryAlt?.available ?? run.can_retry) && canSubmit;
  const retryReason = !canSubmit
    ? t("form.noPermission")
    : retryAlt && !retryAlt.available
      ? retryAlt.reason
      : null;

  const doRetry = () =>
    retry.mutate(run.id, {
      onSuccess: (next) => {
        toast.success(t("detail.retried"));
        onOpenRun(next.id);
      },
      onError: (error) => toast.error(apiError(error)),
    });

  return (
    <div className="flex flex-col gap-2 border-t border-line pt-3">
      <p className="text-caption font-medium text-ink-3">{t("detail.whatNext")}</p>
      <div className="flex flex-wrap gap-2">
        <DisabledHint hint={retryAvailable ? null : retryReason}>
          <Button
            type="button"
            size="sm"
            className="min-h-10 sm:min-h-8"
            disabled={!retryAvailable || retry.isPending}
            onClick={doRetry}
          >
            {retry.isPending ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
            ) : (
              <RotateCcw className="h-3.5 w-3.5" strokeWidth={1.5} aria-hidden />
            )}
            {t("detail.retry")}
          </Button>
        </DisabledHint>
        <UploadAlternative alt={uploadAlt} caseFileId={run.case_file_id} />
      </div>
      {/* Visible on touch, where the DisabledHint tooltip never opens. */}
      {!retryAvailable && retryReason ? (
        <p className="text-caption text-ink-3">{retryReason}</p>
      ) : null}
      {others.length > 0 ? (
        <div className="flex flex-col gap-1.5 pt-1">
          <p className="text-caption text-ink-3">{t("detail.otherPortals")}</p>
          <div className="flex flex-wrap gap-2">
            {others.map((alt) => (
              <OtherPortal
                key={alt.portal ?? alt.portal_name ?? "portal"}
                alt={alt}
                onUse={() => alt.portal && onOtherPortal(alt.portal, run)}
              />
            ))}
          </div>
        </div>
      ) : null}
    </div>
  );
}

function UploadAlternative({
  alt,
  caseFileId,
}: {
  alt: PortalRunAlternative | undefined;
  caseFileId: number | null;
}) {
  const { t } = useTranslation("rpa");
  if (alt && !alt.available) {
    return (
      <DisabledHint hint={alt.reason}>
        <Button type="button" size="sm" variant="secondary" disabled className="min-h-10 sm:min-h-8">
          <FileUp className="h-3.5 w-3.5" strokeWidth={1.5} aria-hidden />
          {t("detail.uploadPdf")}
        </Button>
      </DisabledHint>
    );
  }
  return (
    <Button asChild size="sm" variant="secondary" className="min-h-10 sm:min-h-8" title={alt?.reason ?? undefined}>
      <Link to={uploadQuotePath(alt?.case_file_id ?? caseFileId)}>
        <FileUp className="h-3.5 w-3.5" strokeWidth={1.5} aria-hidden />
        {t("detail.uploadPdf")}
      </Link>
    </Button>
  );
}

function OtherPortal({ alt, onUse }: { alt: PortalRunAlternative; onUse: () => void }) {
  const { t } = useTranslation("rpa");
  const label = t("detail.useOtherPortal", { portal: alt.portal_name ?? alt.portal ?? "" });
  if (!alt.available) {
    return (
      <SoonButton reason={alt.reason ?? undefined} className="min-h-10 sm:min-h-8">
        {alt.portal_name ?? alt.portal}
      </SoonButton>
    );
  }
  return (
    <Button type="button" size="sm" variant="secondary" className="min-h-10 sm:min-h-8" onClick={onUse}>
      {label}
      <ArrowUpRight className="h-3.5 w-3.5" strokeWidth={1.5} aria-hidden />
    </Button>
  );
}

function StepsTimeline({ run }: { run: PortalRun }) {
  const { t } = useTranslation("rpa");
  const working = runIsLive(run);
  return (
    <section className="flex flex-col gap-2">
      <h3 className="text-caption font-medium text-ink-3">{t("detail.steps")}</h3>
      {run.steps.length === 0 && !working ? (
        <p className="text-caption text-ink-3">{t("detail.noSteps")}</p>
      ) : (
        <ol className="relative flex flex-col gap-0 ps-1">
          {run.steps.map((step, index) => (
            <li key={`${step.name}-${index}`} className="relative flex gap-3 pb-3 last:pb-0">
              <span className="relative z-10 mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-bone">
                {step.ok ? (
                  <CircleCheck className="h-4 w-4 text-pos" strokeWidth={1.75} aria-hidden />
                ) : (
                  <CircleX className="h-4 w-4 text-neg" strokeWidth={1.75} aria-hidden />
                )}
              </span>
              {index < run.steps.length - 1 || working ? (
                <span className="absolute start-[10px] top-6 h-[calc(100%-18px)] w-px bg-line" aria-hidden />
              ) : null}
              <div className="min-w-0 flex-1">
                <p className="flex flex-wrap items-baseline gap-x-2 text-caption">
                  {/* The server's Spanish label; the raw step id is never shown. */}
                  <span className="font-medium text-ink">{step.label || t(stepOpLabelKey(step.op))}</span>
                  {step.elapsed_ms !== null && step.elapsed_ms !== undefined ? (
                    <span className="tabular-nums text-[11px] text-ink-3">
                      {t("detail.elapsed", { ms: formatNumber(step.elapsed_ms) })}
                    </span>
                  ) : null}
                </p>
                {step.detail ? (
                  <p className={cn("text-caption [text-wrap:pretty]", step.ok ? "text-ink-2" : "text-neg-text")}>
                    {step.detail}
                  </p>
                ) : null}
              </div>
            </li>
          ))}
          {working ? (
            <li className="relative flex gap-3">
              <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-brand-soft">
                <Loader2 className="h-3 w-3 animate-spin text-brand-deep" aria-hidden />
              </span>
              <p className="text-caption text-ink-3" aria-live="polite">
                {run.status === "queued" ? t("detail.waiting") : t("detail.working")}
              </p>
            </li>
          ) : null}
        </ol>
      )}
    </section>
  );
}

function coverageLabels(coverages: PortalQuote["coverages"]): string[] {
  if (!coverages) return [];
  const items = Array.isArray(coverages) ? coverages : Object.entries(coverages).map(([k, v]) => ({ [k]: v }));
  return items.map((item) => {
    if (typeof item === "string") return item;
    if (item && typeof item === "object") {
      const obj = item as Record<string, unknown>;
      const named = obj.name ?? obj.label ?? obj.coverage ?? obj.title;
      if (typeof named === "string") return named;
      const [first] = Object.entries(obj);
      return first ? `${first[0]}: ${String(first[1])}` : "";
    }
    return String(item);
  }).filter(Boolean);
}

function Coverages({ coverages }: { coverages: PortalQuote["coverages"] }) {
  const { t } = useTranslation("rpa");
  const labels = coverageLabels(coverages);
  if (labels.length === 0) return <span className="text-ink-3">—</span>;
  const shown = labels.slice(0, 3);
  return (
    <span className="text-caption text-ink-2" title={labels.join(" · ")}>
      {shown.join(" · ")}
      {labels.length > shown.length ? (
        <span className="text-ink-3"> {t("quotes.more", { count: labels.length - shown.length })}</span>
      ) : null}
    </span>
  );
}

function QuotesBlock({ quotes }: { quotes: PortalQuote[] }) {
  const { t } = useTranslation("rpa");
  return (
    <section className="flex flex-col gap-2">
      <h3 className="text-caption font-medium text-ink-3">{t("detail.quotes")}</h3>
      {quotes.length === 0 ? (
        <p className="text-caption text-ink-3">{t("detail.noQuotes")}</p>
      ) : (
        <>
          {/* Cards on a phone */}
          <ul className="flex flex-col divide-y divide-line rounded-lg border border-line sm:hidden">
            {quotes.map((quote) => (
              <li key={quote.id} className="flex flex-col gap-1 p-3">
                <div className="flex items-baseline justify-between gap-2">
                  <span className="min-w-0 truncate text-label font-medium text-ink">
                    {quote.plan_name ?? quote.insurer_name ?? "—"}
                  </span>
                  <span className="shrink-0 tabular-nums text-label text-ink">{uf(quote.premium_uf)}</span>
                </div>
                <p className="text-caption text-ink-3">
                  {quote.insurer_name ?? "—"}
                  {quote.premium_clp !== null ? ` · $${formatNumber(quote.premium_clp)}` : ""}
                  {quote.deductible ? ` · ${t("quotes.deductible")}: ${quote.deductible}` : ""}
                </p>
                <Coverages coverages={quote.coverages} />
                {quote.proposal_id ? (
                  <Link
                    to={`/proposals/${quote.proposal_id}`}
                    className="inline-flex min-h-8 w-fit items-center gap-1 text-caption font-medium text-brand hover:underline"
                  >
                    {t("quotes.openProposal")}
                    <ArrowUpRight className="h-3.5 w-3.5" strokeWidth={1.5} aria-hidden />
                  </Link>
                ) : null}
              </li>
            ))}
          </ul>
          {/* Table from sm */}
          <div className="hidden overflow-x-auto rounded-lg border border-line sm:block">
            <table className="w-full min-w-[520px] text-left text-caption">
              <thead className="border-b border-line bg-paper-2/60 text-ink-3">
                <tr>
                  <th className="px-3 py-2 font-medium">{t("quotes.insurer")}</th>
                  <th className="px-3 py-2 font-medium">{t("quotes.plan")}</th>
                  <th className="px-3 py-2 text-right font-medium">{t("quotes.premiumUf")}</th>
                  <th className="px-3 py-2 text-right font-medium">{t("quotes.premiumClp")}</th>
                  <th className="px-3 py-2 font-medium">{t("quotes.deductible")}</th>
                  <th className="px-3 py-2 font-medium">{t("quotes.coverages")}</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line">
                {quotes.map((quote) => (
                  <tr key={quote.id} className="align-top">
                    <td className="px-3 py-2 text-ink">{quote.insurer_name ?? "—"}</td>
                    <td className="px-3 py-2 text-ink">
                      {quote.plan_name ?? "—"}
                      {quote.proposal_id ? (
                        <Link
                          to={`/proposals/${quote.proposal_id}`}
                          className="ms-1.5 inline-flex items-center gap-0.5 text-brand hover:underline"
                        >
                          {t("quotes.openProposal")}
                          <ArrowUpRight className="h-3 w-3" strokeWidth={1.5} aria-hidden />
                        </Link>
                      ) : null}
                    </td>
                    <td className="px-3 py-2 text-right tabular-nums text-ink">{uf(quote.premium_uf)}</td>
                    <td className="px-3 py-2 text-right tabular-nums text-ink">
                      {quote.premium_clp !== null ? `$${formatNumber(quote.premium_clp)}` : "—"}
                    </td>
                    <td className="px-3 py-2 text-ink-2">{quote.deductible ?? "—"}</td>
                    <td className="px-3 py-2">
                      <Coverages coverages={quote.coverages} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </section>
  );
}
