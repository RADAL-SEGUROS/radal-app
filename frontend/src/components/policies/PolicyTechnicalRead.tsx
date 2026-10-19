/**
 * "Datos técnicos leídos por IA" — the policy's second, technical read
 * (fast reading, docs/v12/11-fast-reading.md), as a SUGGESTION on the ficha.
 *
 * Driven by `GET /policies/{id}/technical-read` plus the in-flight
 * `POST …/technical-read` (fired by the upload dialog, which may be gone):
 *
 *   - reading  (mutation in flight, or server `running`) → skeleton + creeping
 *              bar "Leyendo datos técnicos…"; the GET polls every 4 s while
 *              the server says `running`;
 *   - ready    → counts per section with expandable tables (exclusions and
 *              particular clauses are number + title only), per-section
 *              errors, **Confirmar datos técnicos** / **Descartar**;
 *   - failed   → the server's own Spanish message + **Reintentar** (force);
 *   - none     → **Leer datos técnicos** when the policy has its PDF; nothing
 *              when it has none (the "Falta: archivo" block already says so);
 *   - confirmed → collapses to one "Datos técnicos confirmados" line.
 *
 * Suggest → human confirm → commit (rule 6): nothing here writes the policy
 * until the broker presses Confirmar. Actions are gated on `Policies.Create`,
 * the same permission `POST /policies/upload` requires.
 */
import * as React from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import {
  AlertTriangle,
  CheckCircle2,
  ChevronDown,
  RefreshCw,
  Sparkles,
  X,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { FadeUp } from "@/components/common/motion";
import { ReadingProgress } from "@/components/common/ReadingProgress";
import {
  DisabledHint,
  KeyValue,
  Section,
  apiError,
  permille,
  uf,
} from "@/components/common/kit";
import { useCan } from "@/lib/permissions";
import { formatDate, formatNumber } from "@/lib/format";
import { formatTimelineMoment } from "@/components/groups/timelineCopy";
import { num, type DecimalString } from "@/api/types";
import { aiErrorCode, aiServerMessage, isLostResponse } from "@/lib/aiErrors";
import { cn } from "@/lib/utils";
import type { PolicyDetail } from "@/api/policies";
import {
  TECHNICAL_SECTIONS,
  technicalCount,
  technicalSectionErrors,
  useConfirmTechnicalRead,
  useDiscardTechnicalRead,
  useLastTechnicalReadError,
  useStartTechnicalRead,
  useTechnicalRead,
  useTechnicalReadInFlight,
  type TechnicalRead,
  type TechnicalSection,
  type TechnicalTitle,
} from "@/api/technicalRead";
import {
  TECHNICAL_READ_ERROR_LABEL,
  TECHNICAL_SECTION_LABEL,
  enumLabel,
} from "@/i18n/labels";

function text(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  return String(value);
}

/** A UF cell: "UF 1.500" — an empty cell is "—", never "UF —" (review r2 C5). */
function ufCell(value: DecimalString | number | null | undefined, decimals = 2): string {
  return num(value) === null ? "—" : uf(value, decimals);
}

/** A percentage cell: 10 → "10 %", 2.5 → "2,5 %". */
function pctCell(value: DecimalString | number | null | undefined): string {
  const n = num(value);
  return n === null ? "—" : `${formatNumber(n, Number.isInteger(n) ? 0 : 2)} %`;
}

/** True when at least one row carries a non-empty `code`. */
function anyCode(rows: { code?: unknown }[]): boolean {
  return rows.some((row) => row.code !== null && row.code !== undefined && String(row.code).trim() !== "");
}

export function PolicyTechnicalRead({ policy }: { policy: PolicyDetail }) {
  const { t } = useTranslation("policies");
  const read = useTechnicalRead(policy.id);
  const inFlight = useTechnicalReadInFlight(policy.id);
  const lastError = useLastTechnicalReadError(policy.id);
  const start = useStartTechnicalRead();
  const canAct = useCan("Policies", "Create");
  const [confirmWarnings, setConfirmWarnings] = React.useState<string[]>([]);

  const hasPdf =
    policy.has_source_document || policy.source_document_id != null;
  const noPermission =
    !canAct.isLoading && !canAct.allowed ? t("technical.noPermission") : null;
  const data = read.data;
  const status = data?.status;

  const run = (force: boolean) =>
    start.mutate(
      { policyId: policy.id, force },
      { onError: (error) => toast.error(startErrorMessage(error, t)) },
    );

  if (inFlight || status === "running") {
    return <ReadingCard />;
  }

  if (read.isLoading) return null;
  // The GET failing must not hide the PDF's only reading path — but a 404 on
  // a dev server without the endpoint yet is not worth a red banner either.
  if (read.isError || !data) return null;

  if (status === "confirmed") {
    return (
      <FadeUp>
        <div className="flex flex-col gap-1.5 rounded-[12px] bg-pos-soft/40 px-4 py-2.5">
          <p className="flex items-center gap-2 text-caption text-ink-2">
            <CheckCircle2 className="h-4 w-4 shrink-0 text-pos-text" />
            {t("technical.confirmedLine")}
            {data.finished_at ? (
              <span className="text-ink-3">
                · {formatTimelineMoment(data.finished_at)}
              </span>
            ) : null}
          </p>
          {confirmWarnings.length > 0 ? (
            <WarningList warnings={confirmWarnings} />
          ) : null}
        </div>
      </FadeUp>
    );
  }

  if (status === "failed") {
    return (
      <FadeUp>
        <Section
          title={t("technical.title")}
          actions={
            <DisabledHint
              hint={noPermission ?? (!hasPdf ? t("technical.noPdf") : null)}
            >
              <Button
                size="sm"
                variant="secondary"
                disabled={!!noPermission || !hasPdf}
                onClick={() => run(true)}
              >
                <RefreshCw className="h-4 w-4" />
                {t("technical.retry")}
              </Button>
            </DisabledHint>
          }
        >
          <p className="flex items-start gap-2 text-body text-ink-2">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-warn-text" />
            <span>{failureMessage(data, t)}</span>
          </p>
        </Section>
      </FadeUp>
    );
  }

  if (status === "ready") {
    return (
      <ReadyCard
        policyId={policy.id}
        read={data}
        noPermission={noPermission}
        onConfirmed={setConfirmWarnings}
      />
    );
  }

  // status "none" (or anything the contract does not name).
  if (!hasPdf) return null;
  const lost = lastError && isLostResponse(lastError);
  return (
    <FadeUp>
      <Card className="flex flex-wrap items-center justify-between gap-3 p-4">
        <div className="flex min-w-0 items-start gap-2.5">
          <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-brand-soft text-brand-deep">
            <Sparkles className="h-4 w-4" />
          </span>
          <div className="min-w-0">
            <div className="text-body font-medium text-ink">
              {t("technical.title")}
            </div>
            <p className="mt-0.5 text-caption text-ink-3">
              {t("technical.noneHint")}
            </p>
            {lastError ? (
              <p className="mt-1.5 flex items-start gap-1.5 text-caption text-ink-2">
                <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-warn-text" />
                {lost
                  ? t("technical.errors.ai_timeout")
                  : startErrorMessage(lastError, t)}
              </p>
            ) : null}
          </div>
        </div>
        <DisabledHint hint={noPermission}>
          <Button
            size="sm"
            variant="secondary"
            disabled={!!noPermission}
            onClick={() => run(!!lastError)}
          >
            {lastError ? (
              <RefreshCw className="h-4 w-4" />
            ) : (
              <Sparkles className="h-4 w-4" />
            )}
            {lastError ? t("technical.retry") : t("technical.read")}
          </Button>
        </DisabledHint>
      </Card>
    </FadeUp>
  );
}

type T = (key: string, opts?: Record<string, unknown>) => string;

function startErrorMessage(error: unknown, t: T): string {
  const code = aiErrorCode(error);
  if (code)
    return (
      aiServerMessage(error) ?? enumLabel(t, TECHNICAL_READ_ERROR_LABEL, code)
    );
  return apiError(error, t("technical.errors.generic"));
}

function failureMessage(read: TechnicalRead, t: T): string {
  if (read.message) return read.message;
  return enumLabel(
    t,
    TECHNICAL_READ_ERROR_LABEL,
    read.error_code ?? null,
    t("technical.errors.generic"),
  );
}

// =============================================================================
// Reading
// =============================================================================

function ReadingCard() {
  const { t } = useTranslation("policies");
  return (
    <FadeUp>
      <Section title={t("technical.title")}>
        <div className="flex flex-col gap-4">
          <ReadingProgress
            title={t("technical.reading")}
            hint={t("technical.readingHint")}
            active={0}
            steps={[
              { label: t("technical.steps.reading"), expectedSeconds: 25 },
              { label: t("technical.steps.review") },
            ]}
          />
          <div className="grid grid-cols-2 gap-3 md:grid-cols-3">
            {TECHNICAL_SECTIONS.map((s) => (
              <Skeleton key={s} className="h-10 w-full rounded-lg" />
            ))}
          </div>
        </div>
      </Section>
    </FadeUp>
  );
}

// =============================================================================
// Ready — review, then confirm or discard
// =============================================================================

function ReadyCard({
  policyId,
  read,
  noPermission,
  onConfirmed,
}: {
  policyId: number;
  read: TechnicalRead;
  noPermission: string | null;
  onConfirmed: (warnings: string[]) => void;
}) {
  const { t } = useTranslation("policies");
  const confirm = useConfirmTechnicalRead(policyId);
  const discard = useDiscardTechnicalRead(policyId);
  const [open, setOpen] = React.useState<TechnicalSection | null>(null);
  const errors = technicalSectionErrors(read);
  const limits = read.sections?.limits ?? null;
  const busy = confirm.isPending || discard.isPending;
  const extractionId = read.extraction_id ?? null;
  const missingId = extractionId == null ? t("technical.noExtraction") : null;
  const blocked = noPermission ?? missingId;

  const hasLimits =
    !!limits &&
    [
      limits.indemnity_limit,
      limits.aggregate_limit_uf,
      limits.valuation_basis,
      limits.average_rate_permille,
      limits.cmf_policy_code,
    ].some((v) => v !== null && v !== undefined && v !== "");

  // A read that found nothing (no limits, every section empty) has nothing to
  // confirm: say so and offer only Descartar.
  const isEmpty =
    !hasLimits && Object.values(read.counts ?? {}).every((n) => !n);

  const onConfirm = () => {
    if (extractionId == null) return;
    confirm.mutate(
      { extractionId },
      {
        onSuccess: (result) => {
          const warnings =
            result.warnings ?? result.technical_read?.warnings ?? [];
          onConfirmed(warnings);
          toast.success(t("technical.confirmed"));
        },
        onError: (error) =>
          toast.error(apiError(error, t("technical.errors.generic"))),
      },
    );
  };

  const onDiscard = () => {
    if (extractionId == null) return;
    discard.mutate(extractionId, {
      onSuccess: () => toast.success(t("technical.discarded")),
      onError: (error) =>
        toast.error(apiError(error, t("technical.errors.generic"))),
    });
  };

  return (
    <FadeUp>
      <Section
        title={
          <span className="flex flex-wrap items-center gap-2">
            {t("technical.title")}
            <Badge variant="brand" dot>
              {t("technical.suggestion")}
            </Badge>
          </span>
        }
        description={
          read.finished_at
            ? t("technical.readyHintAt", {
                when: formatTimelineMoment(read.finished_at),
              })
            : t("technical.readyHint")
        }
        actions={
          <>
            <DisabledHint hint={blocked}>
              <Button
                size="sm"
                variant="ghost"
                disabled={!!blocked || busy}
                onClick={onDiscard}
              >
                <X className="h-4 w-4" />
                {t("technical.discard")}
              </Button>
            </DisabledHint>
            {isEmpty ? null : (
              <DisabledHint hint={blocked}>
                <Button
                  size="sm"
                  variant="success"
                  disabled={!!blocked || busy}
                  onClick={onConfirm}
                >
                  {confirm.isPending ? (
                    <RefreshCw className="h-4 w-4 animate-spin motion-reduce:animate-none" />
                  ) : (
                    <CheckCircle2 className="h-4 w-4" />
                  )}
                  {t("technical.confirm")}
                </Button>
              </DisabledHint>
            )}
          </>
        }
      >
        <div className="flex flex-col gap-4">
          {/* e.g. "No se pudo leer: Deducibles. El resto … listo para revisar." */}
          {read.message ? <SectionError message={read.message} /> : null}
          {isEmpty ? (
            <p className="text-body text-ink-2">{t("technical.empty")}</p>
          ) : null}
          {hasLimits && limits ? (
            <div className="grid grid-cols-2 gap-4 md:grid-cols-5">
              <KeyValue
                label={t("technical.limits.indemnity")}
                value={text(limits.indemnity_limit)}
              />
              <KeyValue
                label={t("technical.limits.aggregate")}
                value={ufCell(limits.aggregate_limit_uf, 0)}
              />
              <KeyValue
                label={t("technical.limits.valuation")}
                value={text(limits.valuation_basis)}
              />
              <KeyValue
                label={t("technical.limits.rate")}
                value={permille(limits.average_rate_permille)}
              />
              <KeyValue
                label={t("technical.limits.cmf")}
                value={text(limits.cmf_policy_code)}
              />
            </div>
          ) : null}
          {errors.limits ? <SectionError message={errors.limits} /> : null}

          <div className="flex flex-col divide-y divide-line rounded-[12px] border border-line">
            {TECHNICAL_SECTIONS.map((section) => {
              const count = technicalCount(read, section);
              const error = errors[section];
              const expanded = open === section;
              const canExpand = count > 0;
              return (
                <div key={section}>
                  <button
                    type="button"
                    disabled={!canExpand}
                    aria-expanded={canExpand ? expanded : undefined}
                    onClick={() => setOpen(expanded ? null : section)}
                    className={cn(
                      "flex w-full items-center justify-between gap-3 px-4 py-2.5 text-left transition-[background-color] duration-150 ease-out",
                      canExpand ? "hover:bg-paper-2/60" : "cursor-default",
                    )}
                  >
                    <span className="flex min-w-0 items-center gap-2">
                      <span className="text-body font-medium text-ink">
                        {enumLabel(t, TECHNICAL_SECTION_LABEL, section)}
                      </span>
                      <Badge
                        variant={count > 0 ? "neutral" : "muted"}
                        className="tabular-nums"
                      >
                        {count}
                      </Badge>
                      {error ? (
                        <Badge variant="warn" className="gap-1">
                          <AlertTriangle className="h-3 w-3" />
                          {t("technical.sectionFailed")}
                        </Badge>
                      ) : null}
                    </span>
                    {canExpand ? (
                      <ChevronDown
                        className={cn(
                          "h-4 w-4 shrink-0 text-ink-3 transition-transform duration-150 ease-out",
                          expanded && "rotate-180",
                        )}
                      />
                    ) : null}
                  </button>
                  {error ? (
                    <div className="px-4 pb-2.5">
                      <SectionError message={error} />
                    </div>
                  ) : null}
                  {expanded ? (
                    <div className="overflow-x-auto border-t border-line">
                      <SectionTable read={read} section={section} />
                    </div>
                  ) : null}
                </div>
              );
            })}
          </div>
        </div>
      </Section>
    </FadeUp>
  );
}

function SectionError({ message }: { message: string }) {
  return (
    <p className="flex items-start gap-1.5 text-caption text-warn-text">
      <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
      {message}
    </p>
  );
}

function WarningList({ warnings }: { warnings: string[] }) {
  return (
    <ul className="flex flex-col gap-1">
      {warnings.map((w, i) => (
        <li
          key={i}
          className="flex items-start gap-1.5 text-caption text-warn-text"
        >
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          {w}
        </li>
      ))}
    </ul>
  );
}

function TitlesTable({ rows }: { rows: TechnicalTitle[] }) {
  const { t } = useTranslation("policies");
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead className="w-20">
            {t("technical.columns.number")}
          </TableHead>
          <TableHead>{t("technical.columns.title")}</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.map((row, i) => (
          <TableRow key={i}>
            <TableCell className="tabular-nums text-ink-3">
              {text(row.number)}
            </TableCell>
            <TableCell>{text(row.title)}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

function SectionTable({
  read,
  section,
}: {
  read: TechnicalRead;
  section: TechnicalSection;
}) {
  const { t } = useTranslation("policies");
  const s = read.sections ?? {};

  if (section === "exclusions")
    return <TitlesTable rows={s.exclusions ?? []} />;
  if (section === "particular_clauses")
    return <TitlesTable rows={s.particular_clauses ?? []} />;

  if (section === "items") {
    return (
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>{t("technical.columns.type")}</TableHead>
            <TableHead>{t("technical.columns.description")}</TableHead>
            <TableHead>{t("technical.columns.addressOrPlate")}</TableHead>
            <TableHead className="text-right">
              {t("technical.columns.insuredAmount")}
            </TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {(s.items ?? []).map((row, i) => (
            <TableRow key={i}>
              <TableCell>{text(row.type)}</TableCell>
              <TableCell>{text(row.description)}</TableCell>
              <TableCell>{text(row.address_or_plate)}</TableCell>
              <TableCell className="text-right tabular-nums">
                {ufCell(row.insured_amount_uf, 0)}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    );
  }

  if (section === "coverages") {
    // "Código" only when some row has one (it was always empty and ate the
    // phone's width — review r2 C5).
    const showCode = anyCode(s.coverages ?? []);
    return (
      <Table>
        <TableHeader>
          <TableRow>
            {showCode ? <TableHead>{t("technical.columns.code")}</TableHead> : null}
            <TableHead>{t("technical.columns.coverage")}</TableHead>
            <TableHead className="text-right">
              {t("technical.columns.limit")}
            </TableHead>
            <TableHead>{t("technical.columns.sublimit")}</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {(s.coverages ?? []).map((row, i) => (
            <TableRow key={i}>
              {showCode ? (
                <TableCell className="tabular-nums text-ink-3">
                  {text(row.code)}
                </TableCell>
              ) : null}
              <TableCell>{text(row.name)}</TableCell>
              <TableCell className="text-right tabular-nums">
                {ufCell(row.limit_uf, 0)}
              </TableCell>
              <TableCell className="text-caption text-ink-2">
                {text(row.sublimit_text)}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    );
  }

  if (section === "deductibles") {
    return (
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>{t("technical.columns.peril")}</TableHead>
            <TableHead className="text-right">
              {t("technical.columns.pct")}
            </TableHead>
            <TableHead className="text-right">
              {t("technical.columns.min")}
            </TableHead>
            <TableHead className="text-right">
              {t("technical.columns.max")}
            </TableHead>
            <TableHead className="text-right">
              {t("technical.columns.days")}
            </TableHead>
            <TableHead>{t("technical.columns.text")}</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {(s.deductibles ?? []).map((row, i) => (
            <TableRow key={i}>
              <TableCell>{text(row.peril)}</TableCell>
              <TableCell className="text-right tabular-nums">
                {pctCell(row.pct)}
              </TableCell>
              <TableCell className="text-right tabular-nums">
                {ufCell(row.min_uf)}
              </TableCell>
              <TableCell className="text-right tabular-nums">
                {ufCell(row.max_uf)}
              </TableCell>
              <TableCell className="text-right tabular-nums">
                {text(row.days)}
              </TableCell>
              <TableCell className="text-caption text-ink-2">
                {text(row.text)}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    );
  }

  // warranties
  const showCode = anyCode(s.warranties ?? []);
  return (
    <Table>
      <TableHeader>
        <TableRow>
          {showCode ? <TableHead>{t("technical.columns.code")}</TableHead> : null}
          <TableHead>{t("technical.columns.warranty")}</TableHead>
          <TableHead>{t("technical.columns.requirement")}</TableHead>
          <TableHead className="text-right">
            {t("technical.columns.deadlineDays")}
          </TableHead>
          <TableHead>{t("technical.columns.dueDate")}</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {(s.warranties ?? []).map((row, i) => (
          <TableRow key={i}>
            {showCode ? (
              <TableCell className="tabular-nums text-ink-3">
                {text(row.code)}
              </TableCell>
            ) : null}
            <TableCell>{text(row.title)}</TableCell>
            <TableCell className="text-caption text-ink-2">
              {text(row.requirement)}
            </TableCell>
            <TableCell className="text-right tabular-nums">
              {text(row.deadline_days)}
            </TableCell>
            <TableCell className="tabular-nums">
              {row.due_date ? formatDate(row.due_date) : "—"}
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
