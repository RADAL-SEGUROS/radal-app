/**
 * `/groups/:groupId/accounts/:caseId/expediente` — **el expediente completo**.
 *
 * This is what "Ver expediente completo" opens. It used to jump to `/cases/:id`
 * — a row in the flat, now-deprecated Expedientes table — which answered a
 * different question entirely. What the broker wants there is the whole account
 * on one page: who it is, what vigencia, how far the journey has got and WHEN
 * each stage closed, plus every document, the comparación, the propuesta, the
 * pólizas and the money.
 *
 * Two rules the page is built around:
 *
 *  1. **Nothing blank without a reason.** A stage that has not happened prints
 *     the server's Spanish `pending_reason` ("Pronto, al cerrar Comparación")
 *     instead of an empty cell — the broker can always tell "not yet" from
 *     "broken".
 *  2. **The PDF is the page.** `GET /case-files/{id}/expediente/pdf` renders
 *     from the SAME aggregate this page reads, on demand, so the file the
 *     broker sends out can never disagree with the screen. It is not a stored
 *     artifact that can go stale.
 *
 * It lives inside `GroupShell`, so the rail, the crumbs and the journey context
 * stay put — this is a fuller view of the account, not a departure from it.
 */
import * as React from "react";
import { Link, useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import {
  ArrowLeft,
  Building2,
  CalendarRange,
  CheckCircle2,
  Circle,
  CircleDashed,
  Clock,
  Download,
  FileText,
  Loader2,
  ShieldCheck,
} from "lucide-react";

import { PageHeader } from "@/components/common/PageHeader";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { FadeUp, Stagger } from "@/components/common/motion";
import { EmptyState, ErrorBanner, MonoChip } from "@/components/common/kit";
import { GroupAvatar } from "@/components/groups/GroupAvatar";
import {
  CaseStatusBadge,
  GroupCrumbs,
  periodDates,
  useCaseId,
  useGroupId,
} from "@/pages/groups/shared";
import { downloadDocument } from "@/lib/download";
import { downloadExpedientePdf, useExpedienteCompleto } from "@/api/expediente";
import { formatDate, formatDateTime } from "@/lib/format";
import { formatRut } from "@/pages/clients/rut";
import { uf } from "@/components/common/kit";
import type {
  AccountExpediente,
  ExpedienteJourneyStep,
  ExpedienteStepStatus,
} from "@/api/types";

// =============================================================================
// Small shared pieces
// =============================================================================

/** A titled block of the overview. `pendingReason` replaces the body wholesale. */
function Block({
  title,
  hint,
  icon,
  pendingReason,
  children,
}: {
  title: string;
  hint?: string;
  icon?: React.ReactNode;
  /** Spanish "Pronto, al cerrar X"; non-null means there is nothing to show. */
  pendingReason?: string | null;
  children?: React.ReactNode;
}) {
  return (
    <FadeUp>
      <Card className="flex flex-col gap-3 p-5">
        <div className="flex min-w-0 items-start justify-between gap-3">
          <div className="min-w-0">
            <h2 className="flex items-center gap-2 text-h3 tracking-tight text-ink">
              {icon}
              {title}
            </h2>
            {hint ? <p className="mt-0.5 text-caption text-ink-3">{hint}</p> : null}
          </div>
        </div>
        {pendingReason ? (
          <p className="rounded-lg border border-dashed border-line px-3.5 py-3 text-caption text-ink-3">
            {pendingReason}
          </p>
        ) : (
          children
        )}
      </Card>
    </FadeUp>
  );
}

/** UF decimals cross the wire as strings; null prints an em dash. */
function money(value: string | null | undefined): string {
  if (value === null || value === undefined) return "—";
  const parsed = Number(value);
  return Number.isFinite(parsed) ? uf(parsed) : "—";
}

/** Label/value pair; a null value prints an em dash, never an empty gap. */
function Fact({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-caption text-ink-3">{label}</dt>
      <dd className="mt-0.5 truncate text-body text-ink">{value ?? "—"}</dd>
    </div>
  );
}

const STEP_ICON: Record<ExpedienteStepStatus, React.ComponentType<{ className?: string }>> = {
  complete: CheckCircle2,
  in_progress: CircleDashed,
  pending: Circle,
};

const STEP_TONE: Record<ExpedienteStepStatus, string> = {
  complete: "text-pos-text",
  in_progress: "text-brand",
  pending: "text-ink-3",
};

/**
 * The journey as a dated timeline — the part the user specifically asked for:
 * "the stages completed in the journey", each with its date.
 */
function JourneyTimeline({ steps }: { steps: ExpedienteJourneyStep[] }) {
  const { t } = useTranslation("accounts");
  if (steps.length === 0) return null;

  return (
    <ol className="flex flex-col">
      {steps.map((step, index) => {
        const Icon = STEP_ICON[step.status] ?? Circle;
        const last = index === steps.length - 1;
        return (
          <li key={step.key} className="flex gap-3">
            {/* Rail: the icon plus the connector down to the next milestone. */}
            <div className="flex flex-col items-center">
              <Icon className={`h-4 w-4 shrink-0 ${STEP_TONE[step.status]}`} />
              {!last ? (
                <span
                  aria-hidden
                  className={`w-px flex-1 ${
                    step.status === "complete" ? "bg-pos-line" : "bg-line"
                  }`}
                />
              ) : null}
            </div>

            <div className={`min-w-0 flex-1 ${last ? "pb-0" : "pb-4"}`}>
              <div className="flex flex-wrap items-center gap-2">
                <span
                  className={`text-label font-medium ${
                    step.status === "pending" ? "text-ink-3" : "text-ink"
                  }`}
                >
                  {step.label}
                </span>
                {step.completed_at ? (
                  <span className="inline-flex items-center gap-1 text-caption tabular-nums text-ink-3">
                    <Clock className="h-3 w-3" />
                    {formatDate(step.completed_at)}
                  </span>
                ) : null}
                {step.status === "in_progress" ? (
                  <Badge variant="brand">{t("expediente.inProgress")}</Badge>
                ) : null}
              </div>
              {/* Either what happened, or why it has not happened yet. */}
              {step.summary ? (
                <p className="mt-0.5 text-caption text-ink-2">{step.summary}</p>
              ) : null}
              {step.pending_reason ? (
                <p className="mt-0.5 text-caption text-ink-3">{step.pending_reason}</p>
              ) : null}
            </div>
          </li>
        );
      })}
    </ol>
  );
}

// =============================================================================
// The page
// =============================================================================

export default function ExpedienteCompletoPage() {
  const { t } = useTranslation("accounts");
  const { t: tc } = useTranslation("common");
  const groupId = useGroupId();
  const caseId = useCaseId();
  const navigate = useNavigate();
  const expediente = useExpedienteCompleto(caseId);
  const [downloading, setDownloading] = React.useState(false);

  const data: AccountExpediente | undefined = expediente.data;

  const download = async () => {
    if (!caseId) return;
    setDownloading(true);
    try {
      const stem = data?.case_file.reference || `expediente-${caseId}`;
      await downloadExpedientePdf(caseId, `${stem}.pdf`);
    } catch {
      toast.error(t("expediente.pdfFailed"));
    } finally {
      setDownloading(false);
    }
  };

  if (expediente.isLoading) {
    return (
      <div className="flex flex-col gap-4">
        <Skeleton className="h-16 w-full" />
        <Skeleton className="h-48 w-full rounded-card" />
        <Skeleton className="h-64 w-full rounded-card" />
      </div>
    );
  }

  if (expediente.isError || !data) {
    return <ErrorBanner error={expediente.error ?? t("expediente.notFound")} />;
  }

  const account = data.case_file;
  const accountPath = `/groups/${groupId}/accounts/${caseId}`;

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        eyebrow={
          <GroupCrumbs
            items={[
              { label: t("group.title"), to: "/groups" },
              {
                label: data.group?.name ?? t("group.one"),
                to: `/groups/${groupId}`,
              },
              { label: account.title, to: accountPath },
              { label: t("expediente.title") },
            ]}
          />
        }
        title={
          <span className="flex flex-wrap items-center gap-2.5">
            {data.group ? (
              <GroupAvatar name={data.group.name} icon={data.group.icon} size="sm" />
            ) : null}
            {t("expediente.title")}
          </span>
        }
        subtitle={
          <span className="flex flex-wrap items-center gap-2">
            <span>{account.title}</span>
            {account.insurance_line_name ? (
              <Badge variant="muted">{account.insurance_line_name}</Badge>
            ) : null}
            {account.period_label ? <MonoChip>{account.period_label}</MonoChip> : null}
            <CaseStatusBadge status={account.status} />
          </span>
        }
        actions={
          <>
            <Button variant="secondary" size="sm" onClick={() => navigate(accountPath)}>
              <ArrowLeft className="h-4 w-4" />
              {t("expediente.backToAccount")}
            </Button>
            <Button size="sm" disabled={downloading} onClick={() => void download()}>
              {downloading ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Download className="h-4 w-4" />
              )}
              {t("expediente.downloadPdf")}
            </Button>
          </>
        }
      />

      {/* The stamp matters: this view is computed per request, and the PDF
          carries the same one, so the two can be compared. */}
      <p className="text-caption text-ink-3">
        {t("expediente.generatedAt", { at: formatDateTime(data.generated_at) })}
      </p>

      <Stagger className="flex flex-col gap-4">
        {/* ---- Identity + vigencia ---------------------------------------- */}
        <Block title={t("expediente.identity")} icon={<Building2 className="h-4 w-4 text-brand" />}>
          <dl className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            <Fact label={t("group.one")} value={data.group?.name ?? "—"} />
            <Fact
              label={t("expediente.line")}
              value={account.insurance_line_name ?? "—"}
            />
            <Fact
              label={t("account.period")}
              value={
                <span className="tabular-nums">
                  {periodDates(account.period_start, account.period_end) || "—"}
                </span>
              }
            />
            <Fact
              label={t("expediente.reference")}
              value={account.reference ? <MonoChip>{account.reference}</MonoChip> : "—"}
            />
          </dl>

          {data.clients.length > 0 ? (
            <ul className="mt-1 flex flex-col divide-y divide-line rounded-lg border border-line">
              {data.clients.map((client) => (
                <li
                  key={client.id}
                  className="flex flex-wrap items-center gap-3 px-3.5 py-2.5"
                >
                  <Link
                    to={`/clients/${client.id}`}
                    className="min-w-0 flex-1 truncate text-body text-ink no-underline transition-colors duration-150 hover:text-brand-deep"
                  >
                    {client.legal_name}
                  </Link>
                  {client.role ? (
                    <Badge variant={client.is_contratante ? "brand" : "muted"}>
                      {t(`expediente.role.${client.role}`, { defaultValue: client.role })}
                    </Badge>
                  ) : null}
                  <span className="text-caption tabular-nums text-ink-3">
                    {formatRut(client.rut)}
                  </span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-caption text-ink-3">{t("empty.clients")}</p>
          )}
        </Block>

        {/* ---- The journey, with dates ------------------------------------ */}
        <Block
          title={t("expediente.journey")}
          hint={t("expediente.journeyHint")}
          icon={<CalendarRange className="h-4 w-4 text-brand" />}
        >
          <JourneyTimeline steps={data.journey} />
        </Block>

        {/* ---- Antecedentes ------------------------------------------------ */}
        <Block
          title={t("account.tabs.records")}
          pendingReason={data.antecedentes_pending_reason}
          hint={
            data.antecedentes
              ? t("expediente.antecedentesHint", {
                  documents: data.antecedentes.documents_count,
                  extractions: data.antecedentes.extractions_count,
                })
              : undefined
          }
        >
          {data.antecedentes ? (
            <>
              <ul className="grid gap-2 sm:grid-cols-2">
                {data.antecedentes.slots.map((slot) => (
                  <li
                    key={slot.key}
                    className="flex items-center gap-2 rounded-lg border border-line px-3 py-2"
                  >
                    {slot.filled ? (
                      <CheckCircle2 className="h-3.5 w-3.5 shrink-0 text-pos-text" />
                    ) : (
                      <Circle className="h-3.5 w-3.5 shrink-0 text-ink-3" />
                    )}
                    <span className="min-w-0 flex-1 truncate text-caption text-ink-2">
                      {slot.label}
                    </span>
                    {slot.required && !slot.filled ? (
                      <Badge variant="warn">{t("expediente.required")}</Badge>
                    ) : null}
                  </li>
                ))}
              </ul>
              <p className="text-caption text-ink-3">
                {t("expediente.slotsFilled", {
                  filled: data.antecedentes.recommended_filled,
                  total: data.antecedentes.recommended_total,
                })}
                {data.antecedentes.free_uploads_count > 0
                  ? ` · ${t("expediente.freeUploads", {
                      count: data.antecedentes.free_uploads_count,
                    })}`
                  : ""}
              </p>
            </>
          ) : null}
        </Block>

        {/* ---- Comparación ------------------------------------------------- */}
        <Block
          title={t("account.tabs.comparison")}
          pendingReason={data.comparison_pending_reason}
        >
          {data.comparison ? (
            <>
              <dl className="grid gap-3 sm:grid-cols-3">
                <Fact
                  label={t("expediente.quotesCompared")}
                  value={data.comparison.entry_count}
                />
                <Fact
                  label={t("expediente.recommendation")}
                  value={data.comparison.recommendation?.pick_label ?? "—"}
                />
                <Fact
                  label={t("expediente.status")}
                  value={t(`expediente.comparisonStatus.${data.comparison.status}`, {
                    defaultValue: data.comparison.status,
                  })}
                />
              </dl>

              {data.comparison.recommendation?.rationale ? (
                <p className="text-caption text-ink-2">
                  {data.comparison.recommendation.rationale}
                </p>
              ) : null}

              {/* A cotización read from the wrong file is the failure the
                  comparison flags; it must not be silent on the overview. */}
              {data.comparison.columns.length > 0 ? (
                <ul className="flex flex-col divide-y divide-line rounded-lg border border-line">
                  {data.comparison.columns.map((column, index) => (
                    <li
                      key={`${column.label}-${index}`}
                      className="flex flex-wrap items-center gap-3 px-3.5 py-2.5"
                    >
                      <span className="min-w-0 flex-1 truncate text-body text-ink">
                        {column.label}
                      </span>
                      {column.recommended ? (
                        <Badge variant="success">{t("expediente.recommended")}</Badge>
                      ) : null}
                      {column.wrong_file ? (
                        <Badge variant="danger">{t("expediente.wrongFile")}</Badge>
                      ) : null}
                      {column.total_premium_uf != null ? (
                        <span className="text-caption tabular-nums text-ink-2">
                          {uf(Number(column.total_premium_uf))}
                        </span>
                      ) : null}
                    </li>
                  ))}
                </ul>
              ) : null}

              <div>
                <Button variant="secondary" size="sm" asChild>
                  <Link to={`/comparisons/${caseId}`}>
                    {t("expediente.openComparison")}
                  </Link>
                </Button>
              </div>
            </>
          ) : null}
        </Block>

        {/* ---- Propuesta --------------------------------------------------- */}
        <Block
          title={t("account.tabs.propuesta")}
          pendingReason={data.proposal_pending_reason}
        >
          {data.proposal ? (
            <>
              <dl className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
                <Fact
                  label={t("expediente.insurer")}
                  value={data.proposal.insurer_name ?? "—"}
                />
                <Fact
                  label={t("expediente.insured")}
                  value={data.proposal.insured_name ?? "—"}
                />
                <Fact
                  label={t("account.period")}
                  value={
                    <span className="tabular-nums">
                      {data.proposal.coverage_start || data.proposal.coverage_end
                        ? `${data.proposal.coverage_start ?? "—"} → ${data.proposal.coverage_end ?? "—"}`
                        : "—"}
                    </span>
                  }
                />
                <Fact
                  label={t("expediente.premium")}
                  value={
                    data.proposal.total_premium_uf != null
                      ? uf(Number(data.proposal.total_premium_uf))
                      : "—"
                  }
                />
              </dl>
              {data.proposal.is_ratified ? (
                <Badge variant="success">{t("expediente.ratified")}</Badge>
              ) : null}
            </>
          ) : null}
        </Block>

        {/* ---- Pólizas ------------------------------------------------------ */}
        <Block
          title={t("account.tabs.policies")}
          icon={<ShieldCheck className="h-4 w-4 text-brand" />}
          pendingReason={data.policies_pending_reason}
        >
          <ul className="flex flex-col divide-y divide-line rounded-lg border border-line">
            {data.policies.map((policy) => (
              <li key={policy.id} className="flex flex-wrap items-center gap-3 px-3.5 py-2.5">
                <Link
                  to={`/groups/${groupId}/policies/${policy.id}`}
                  className="min-w-0 flex-1 truncate text-body text-ink no-underline transition-colors duration-150 hover:text-brand-deep"
                >
                  {policy.policy_number || t("expediente.policyNoNumber")}
                </Link>
                <span className="text-caption text-ink-3">{policy.insurer_name ?? "—"}</span>
                <span className="text-caption tabular-nums text-ink-3">
                  {periodDates(policy.start_date, policy.end_date)}
                </span>
                {policy.total_premium_uf != null ? (
                  <span className="text-caption tabular-nums text-ink-2">
                    {uf(Number(policy.total_premium_uf))}
                  </span>
                ) : null}
              </li>
            ))}
          </ul>
        </Block>

        {/* ---- Money ------------------------------------------------------- */}
        <Block title={t("expediente.money")} pendingReason={data.money_pending_reason}>
          {data.money ? (
            <>
              {/* net = afecto + exento; IVA = 19% del AFECTO (no del neto: la
                  cobertura de sismo es exenta); total = neto + IVA. */}
              <dl className="grid gap-3 sm:grid-cols-3 xl:grid-cols-5">
                <Fact
                  label={t("expediente.taxable")}
                  value={money(data.money.taxable_premium_uf)}
                />
                <Fact
                  label={t("expediente.exempt")}
                  value={money(data.money.exempt_premium_uf)}
                />
                <Fact label={t("expediente.net")} value={money(data.money.net_premium_uf)} />
                <Fact label={t("expediente.vat")} value={money(data.money.vat_uf)} />
                <Fact
                  label={t("expediente.total")}
                  value={money(data.money.total_premium_uf)}
                />
              </dl>

              <p className="text-caption text-ink-3">
                {t("expediente.moneySource", {
                  source: t(`expediente.moneySourceValue.${data.money.source}`, {
                    defaultValue: data.money.source,
                  }),
                })}
              </p>

              {/* A read never 422s over a money mismatch, so the disagreement
                  has to be visible here or it is invisible everywhere. */}
              {data.money.warnings.length > 0 ? (
                <ul className="flex flex-col gap-1 rounded-lg border border-warn-line bg-warn-soft px-3.5 py-2.5">
                  {data.money.warnings.map((warning, index) => (
                    <li key={index} className="text-caption text-warn-text">
                      {warning}
                    </li>
                  ))}
                </ul>
              ) : null}
            </>
          ) : null}
        </Block>

        {/* ---- Every document --------------------------------------------- */}
        <Block
          title={t("expediente.documents")}
          icon={<FileText className="h-4 w-4 text-brand" />}
          hint={t("expediente.documentsHint", { count: data.documents_count })}
        >
          {data.documents.length === 0 ? (
            <EmptyState
              icon={<FileText className="h-6 w-6" />}
              title={t("empty.records")}
              hint={t("empty.recordsHint")}
            />
          ) : (
            <ul className="flex flex-col divide-y divide-line rounded-lg border border-line">
              {data.documents.map((doc) => (
                <li key={doc.id} className="flex flex-wrap items-center gap-3 px-3.5 py-2.5">
                  <span className="min-w-0 flex-1 truncate text-body text-ink">
                    {doc.filename}
                  </span>
                  {doc.category_label ? (
                    <Badge variant="muted">{doc.category_label}</Badge>
                  ) : null}
                  <span className="text-caption tabular-nums text-ink-3">
                    {formatDate(doc.created_at)}
                  </span>
                  {/* Authenticated blob download — a plain link would 401. */}
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => void downloadDocument(doc.id, doc.filename)}
                  >
                    <Download className="h-3.5 w-3.5" />
                    {tc("actions.download")}
                  </Button>
                </li>
              ))}
            </ul>
          )}
        </Block>
      </Stagger>
    </div>
  );
}
