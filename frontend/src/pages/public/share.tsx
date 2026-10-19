/**
 * THE PUBLIC SHARE VIEW — `/s/:token` (v12 lane F4).
 *
 * Unauthenticated and outside the app shell: the token IS the credential, so
 * every request goes through `publicShareApi` (api/share.ts), which carries the
 * OAC body hash and never an auth header. Mobile-first — these links arrive by
 * WhatsApp — and broker-branded (the broker's logo comes from a token-scoped
 * route, never a storage key).
 *
 * Two audiences, one projection each (server whitelist, `schemas/share.py`):
 *  · insured — policy summary, cuotas with state + amounts (UF/USD and CLP when
 *    a rate exists); per cuota `pay` → online checkout, otherwise the insurer's
 *    payment instructions plus "Ya pagué · subir comprobante";
 *  · insurer — policy + the listed cuotas + "Informar estado" per cuota.
 * Every write is a suggestion the broker confirms; the copy says so.
 * Unknown / revoked / expired tokens are one indistinguishable 404 → a calm page.
 */
import * as React from "react";
import { useParams, useSearchParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useForm, useWatch } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import {
  AlertTriangle,
  Banknote,
  Check,
  CheckCircle2,
  Clock,
  Copy,
  CreditCard,
  ExternalLink,
  FileUp,
  Link2Off,
  Loader2,
  RefreshCw,
  Send,
  ShieldCheck,
  X,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { apiError, resolveFileUrl } from "@/components/common/kit";
import { cn } from "@/lib/utils";
import { formatRut, isValidRut } from "@/pages/clients/rut";
import { formatDate, formatDateTime, formatNumber, formatUF } from "@/lib/format";
import { useTheme } from "@/providers/ThemeProvider";
import { num } from "@/api/types";
import {
  RECEIPT_ACCEPT,
  RECEIPT_MAX_BYTES,
  RECEIPT_MIME_TYPES,
  usePublicCheckout,
  usePublicReceipt,
  usePublicReport,
  usePublicShare,
  type PublicCurrency,
  type PublicInstallment,
  type PublicInstructions,
  type PublicPolicy,
  type PublicReportStatus,
  type PublicShareRead,
} from "@/api/share";

// =============================================================================
// Explicit label maps (no dynamic i18n keys)
// =============================================================================

type BadgeVariant = "neutral" | "muted" | "brand" | "success" | "warn" | "danger";

const STATE_LABEL: Record<string, string> = {
  upcoming: "share:public.state.upcoming",
  due_today: "share:public.state.due_today",
  overdue: "share:public.state.overdue",
  termination_risk: "share:public.state.termination_risk",
  promised: "share:public.state.promised",
  paid: "share:public.state.paid",
  voided: "share:public.state.voided",
  pending_review: "share:public.state.pending_review",
};

const STATE_VARIANT: Record<string, BadgeVariant> = {
  upcoming: "neutral",
  due_today: "warn",
  overdue: "danger",
  termination_risk: "danger",
  promised: "brand",
  paid: "success",
  voided: "muted",
  pending_review: "neutral",
};

const REPORT_LABEL: Record<PublicReportStatus, string> = {
  paid: "share:public.report.status.paid",
  unpaid: "share:public.report.status.unpaid",
  partial: "share:public.report.status.partial",
  cancelled: "share:public.report.status.cancelled",
};

const REPORT_HINT: Record<PublicReportStatus, string> = {
  paid: "share:public.report.statusHint.paid",
  unpaid: "share:public.report.statusHint.unpaid",
  partial: "share:public.report.statusHint.partial",
  cancelled: "share:public.report.statusHint.cancelled",
};

const ALL_REPORT_STATUSES: PublicReportStatus[] = ["paid", "unpaid", "partial", "cancelled"];

function reportLabelKey(status: string | null | undefined): string {
  if (status && status in REPORT_LABEL) return REPORT_LABEL[status as PublicReportStatus];
  return "share:public.report.status.unknown";
}

const CLOSED_STATES = new Set(["paid", "voided"]);

/** Instruction rows in display order; `copy` marks values worth one tap. */
const INSTRUCTION_ROWS: { key: keyof PublicInstructions; label: string; copy: boolean }[] = [
  { key: "bank_name", label: "share:public.instructions.bank_name", copy: false },
  { key: "account_type", label: "share:public.instructions.account_type", copy: false },
  { key: "account_number", label: "share:public.instructions.account_number", copy: true },
  { key: "account_holder", label: "share:public.instructions.account_holder", copy: true },
  { key: "account_rut", label: "share:public.instructions.account_rut", copy: true },
  { key: "transfer_email", label: "share:public.instructions.transfer_email", copy: true },
  { key: "receipt_email", label: "share:public.instructions.receipt_email", copy: true },
];

// =============================================================================
// Helpers
// =============================================================================

function statusOf(error: unknown): number | null {
  const err = error as { response?: { status?: number } } | null;
  return err?.response?.status ?? null;
}

function isHttpUrl(value: string | null | undefined): value is string {
  return !!value && /^https?:\/\//i.test(value.trim());
}

/** Amount in the cuota's own unit: "UF 12,34" or "USD 12,34". */
function money(value: string | number | null | undefined, currency: string): string {
  const parsed = typeof value === "number" ? value : num(value);
  if (currency === "USD") {
    return parsed === null ? "USD —" : `USD ${formatNumber(parsed, 2)}`;
  }
  return formatUF(parsed);
}

function clp(value: number | null | undefined): string | null {
  if (value === null || value === undefined) return null;
  return `$ ${formatNumber(value, 0)}`;
}

/** Strip spaces and, for pesos, a leading "$" the payer may copy from a bank receipt. */
function cleanAmount(raw: string | undefined, currency: PublicCurrency): string {
  const text = (raw ?? "").trim().replace(/\s/g, "");
  return currency === "CLP" ? text.replace(/^\$/, "") : text;
}

/** Pesos carry no decimals: "150000" or the Chilean grouped form "150.000". */
const CLP_PATTERN = /^\d+$|^\d{1,3}(\.\d{3})+$/;
/** Grouped thousands with no decimals: "1.234", "1.500.000". */
const THOUSANDS_PATTERN = /^\d{1,3}(\.\d{3})+$/;
/** UF / USD: "1.234,56", "12,5", "12.5", "1234". */
const DECIMAL_PATTERN = /^\d{1,3}(\.\d{3})*(,\d{1,4})?$|^\d+([.,]\d{1,4})?$/;

/** True when the text is a readable amount in the given currency (empty counts as valid). */
function isValidAmount(raw: string | undefined, currency: PublicCurrency): boolean {
  const text = cleanAmount(raw, currency);
  if (!text) return true;
  return currency === "CLP" ? CLP_PATTERN.test(text) : DECIMAL_PATTERN.test(text);
}

/**
 * The user's text → the dot-decimal string the server reads, interpreted in `currency`.
 * CLP: every dot is a thousands separator ("150.000" → "150000").
 * UF/USD: "1.234,56" → "1234.56"; "12,5" → "12.5"; "1.234" / "1.500.000" are thousands;
 * a lone dot with other than three digits after it ("12.5") stays a decimal point.
 */
function normalizeAmount(raw: string | undefined, currency: PublicCurrency): string | null {
  const text = cleanAmount(raw, currency);
  if (!text) return null;
  if (currency === "CLP") return text.replace(/\./g, "");
  if (text.includes(",")) return text.replace(/\./g, "").replace(",", ".");
  if (THOUSANDS_PATTERN.test(text)) return text.replace(/\./g, "");
  return text;
}

const AMOUNT_ERROR_KEY: Record<PublicCurrency, "public.form.errors.amount" | "public.form.errors.amountClp"> = {
  UF: "public.form.errors.amount",
  USD: "public.form.errors.amount",
  CLP: "public.form.errors.amountClp",
};

const CURRENCY_OPTIONS: PublicCurrency[] = ["UF", "CLP", "USD"];

async function copyText(value: string) {
  try {
    await navigator.clipboard.writeText(value);
  } catch {
    const el = document.createElement("textarea");
    el.value = value;
    document.body.appendChild(el);
    el.select();
    document.execCommand("copy");
    document.body.removeChild(el);
  }
}

// =============================================================================
// Page
// =============================================================================

export default function PublicSharePage() {
  const { token } = useParams<{ token: string }>();
  const { t } = useTranslation(["share", "common"]);
  const [searchParams, setSearchParams] = useSearchParams();
  const query = usePublicShare(token);
  const returned = searchParams.get("pago") === "retorno";

  const dismissReturn = () => {
    const next = new URLSearchParams(searchParams);
    next.delete("pago");
    setSearchParams(next, { replace: true });
  };

  return (
    <div className="flex min-h-[100dvh] flex-col bg-bg-app text-ink">
      <BrandHeader data={query.data} />

      <main className="mx-auto w-full max-w-[640px] flex-1 px-4 py-6 sm:py-10">
        {query.isLoading ? (
          <div className="flex flex-col gap-4">
            <Skeleton className="h-20 w-full rounded-card" />
            <Skeleton className="h-32 w-full rounded-card" />
            <Skeleton className="h-32 w-full rounded-card" />
          </div>
        ) : query.isError ? (
          statusOf(query.error) === 404 ? (
            <StateScreen
              tone="gone"
              title={t("share:public.gone.title")}
              hint={t("share:public.gone.hint")}
            />
          ) : (
            <StateScreen
              tone="error"
              title={t("share:public.error.title")}
              hint={apiError(query.error, t("share:public.error.hint"))}
              action={
                <Button variant="secondary" className="h-11" onClick={() => void query.refetch()}>
                  <RefreshCw className="h-4 w-4" />
                  {t("share:public.error.retry")}
                </Button>
              }
            />
          )
        ) : query.data ? (
          <div className="flex flex-col gap-5">
            {returned && query.data.audience === "insured" ? (
              <ReturnBanner onDismiss={dismissReturn} />
            ) : null}
            {query.data.audience === "insured" ? (
              <InsuredView token={token!} data={query.data} />
            ) : (
              <InsurerView token={token!} data={query.data} />
            )}
          </div>
        ) : null}
      </main>

      <footer className="mx-auto w-full max-w-[640px] px-4 pb-8 text-center text-caption text-ink-3">
        {query.data?.expires_at ? (
          <p>{t("share:public.footer.validUntil", { date: formatDate(query.data.expires_at) })}</p>
        ) : null}
        <p className="mt-1">
          {t("share:public.footer.poweredBy")}{" "}
          <span className="font-wordmark font-medium tracking-[-0.02em] text-ink-2">Radal.</span>
        </p>
      </footer>
    </div>
  );
}

function BrandHeader({ data }: { data: PublicShareRead | undefined }) {
  const { t } = useTranslation("share");
  const { theme } = useTheme();
  const [logoFailed, setLogoFailed] = React.useState(false);
  const logo = resolveFileUrl(data?.broker.logo_url);
  const brokerName = data?.broker.name ?? null;
  const radalMark = theme === "dark" ? "radal-mark-white" : "radal-mark-teal";

  return (
    <header className="border-b border-line bg-bone">
      <div className="mx-auto flex w-full max-w-[640px] items-center gap-3 px-4 py-3.5">
        {logo && !logoFailed ? (
          <img
            src={logo}
            alt={brokerName ?? ""}
            onError={() => setLogoFailed(true)}
            className="h-9 max-w-[140px] object-contain"
          />
        ) : brokerName ? (
          <span
            aria-hidden
            className="flex h-9 w-9 shrink-0 items-center justify-center rounded-sm bg-brand-soft text-label font-semibold text-brand-deep"
          >
            {brokerName.trim().charAt(0).toUpperCase()}
          </span>
        ) : (
          <img src={`/brand/${radalMark}.svg`} alt="" className="h-6 w-6" />
        )}
        <div className="min-w-0 flex-1">
          {brokerName ? (
            <>
              <div className="truncate text-body font-medium text-ink">{brokerName}</div>
              <div className="text-caption text-ink-3">{t("public.header.broker")}</div>
            </>
          ) : (
            <span className="font-wordmark text-[18px] font-medium tracking-[-0.02em]">Radal.</span>
          )}
        </div>
        <span className="hidden items-center gap-1 text-caption text-ink-3 sm:inline-flex">
          <ShieldCheck className="h-3.5 w-3.5" />
          {t("public.header.secure")}
        </span>
      </div>
    </header>
  );
}

function StateScreen({
  tone,
  title,
  hint,
  action,
}: {
  tone: "gone" | "error";
  title: string;
  hint: string;
  action?: React.ReactNode;
}) {
  return (
    <Card className="p-6 sm:p-8">
      <div className="flex flex-col items-center gap-3 text-center">
        <span
          className={cn(
            "flex h-12 w-12 items-center justify-center rounded-lg",
            tone === "gone" ? "bg-paper-2 text-ink-3" : "bg-warn-soft text-warn-text",
          )}
        >
          {tone === "gone" ? <Link2Off className="h-6 w-6" /> : <AlertTriangle className="h-6 w-6" />}
        </span>
        <h1 className="text-h2 tracking-tight text-ink">{title}</h1>
        <p className="max-w-md text-pretty text-body text-ink-2">{hint}</p>
        {action}
      </div>
    </Card>
  );
}

function ReturnBanner({ onDismiss }: { onDismiss: () => void }) {
  const { t } = useTranslation("share");
  return (
    <div role="status" className="flex items-start gap-3 rounded-lg bg-brand-soft px-4 py-3">
      <Clock className="mt-0.5 h-4 w-4 shrink-0 text-brand-deep" />
      <div className="min-w-0 flex-1">
        <p className="text-body font-medium text-ink">{t("public.return.title")}</p>
        <p className="text-caption text-ink-2">{t("public.return.hint")}</p>
      </div>
      <button
        type="button"
        onClick={onDismiss}
        aria-label={t("public.return.dismiss")}
        className="-m-1 flex h-9 w-9 shrink-0 items-center justify-center rounded-sm text-ink-3 transition-[background-color,color,transform] duration-150 ease-out hover:bg-bone hover:text-ink active:scale-[0.96]"
      >
        <X className="h-4 w-4" />
      </button>
    </div>
  );
}

// =============================================================================
// Shared blocks
// =============================================================================

function PolicyCard({ policy, showInsured }: { policy: PublicPolicy; showInsured: boolean }) {
  const { t } = useTranslation("share");
  const ramo = [policy.line_name, policy.subtype_name].filter(Boolean).join(" · ");
  return (
    <Card className="p-4 sm:p-5">
      <div className="flex flex-col gap-3">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div className="min-w-0">
            <div className="text-caption text-ink-3">{t("public.policy.title")}</div>
            <div className="text-h3 tracking-tight text-ink tabular-nums">
              {policy.policy_number
                ? t("public.policy.number", { number: policy.policy_number })
                : t("public.policy.noNumber")}
            </div>
          </div>
          {policy.insurer_name ? <Badge variant="neutral">{policy.insurer_name}</Badge> : null}
        </div>
        <dl className="grid grid-cols-1 gap-x-4 gap-y-2 sm:grid-cols-2">
          {showInsured && policy.insured_name ? (
            <Fact label={t("public.policy.insured")} value={policy.insured_name} />
          ) : null}
          {ramo ? <Fact label={t("public.policy.line")} value={ramo} /> : null}
          {policy.start_date || policy.end_date ? (
            <Fact
              label={t("public.policy.period")}
              value={t("public.policy.periodValue", {
                start: formatDate(policy.start_date),
                end: formatDate(policy.end_date),
              })}
            />
          ) : null}
        </dl>
      </div>
    </Card>
  );
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0">
      <dt className="text-caption text-ink-3">{label}</dt>
      <dd className="break-words text-body text-ink">{value}</dd>
    </div>
  );
}

/** Number, due date, state and the amounts of one cuota. */
function InstallmentSummary({ inst, inReview = false }: { inst: PublicInstallment; inReview?: boolean }) {
  const { t } = useTranslation("share");
  const stateKey = STATE_LABEL[inst.state] ?? "share:public.state.unknown";
  const variant = STATE_VARIANT[inst.state] ?? "neutral";
  const open = !CLOSED_STATES.has(inst.state);
  const balanceClp = clp(inst.balance_clp);
  const amountClp = clp(inst.amount_clp);

  return (
    <div className="flex flex-col gap-2.5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex min-w-0 items-baseline gap-2">
          <span className="text-body font-medium text-ink">
            {t("public.installment.number", { number: inst.number })}
          </span>
          {inst.coupon_number ? (
            <span className="truncate text-caption tabular-nums text-ink-3">
              {t("public.installment.coupon", { coupon: inst.coupon_number })}
            </span>
          ) : null}
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          <Badge variant={variant} dot>
            {t(stateKey)}
          </Badge>
          {inst.partial ? <Badge variant="warn">{t("public.installment.partial")}</Badge> : null}
          {inReview && open ? <Badge variant="brand">{t("public.receipt.inReviewBadge")}</Badge> : null}
        </div>
      </div>
      <div className="flex flex-wrap items-end justify-between gap-x-4 gap-y-1">
        <div>
          <div className="text-caption text-ink-3">
            {inst.due_date
              ? t("public.installment.due", { date: formatDate(inst.due_date) })
              : t("public.installment.noDue")}
          </div>
          {inst.days_late > 0 ? (
            // A settled cuota keeps the delay it was paid with: say so neutrally,
            // never in alarm red next to a green "Pagada".
            open ? (
              <div className="text-caption text-neg-text">
                {t("public.installment.daysLate", { count: inst.days_late })}
              </div>
            ) : inst.state === "paid" ? (
              <div className="text-caption text-ink-3">
                {t("public.installment.paidLate", { count: inst.days_late })}
              </div>
            ) : null
          ) : null}
        </div>
        {/* ml-auto: when the row wraps on a phone the amount stays right-aligned. */}
        <div className="ml-auto text-right">
          {open && inst.partial ? (
            <>
              <div className="text-h3 tracking-tight text-ink tabular-nums">
                {money(inst.balance_uf, inst.currency)}
              </div>
              <div className="text-caption text-ink-3 tabular-nums">
                {t("public.installment.paidOf", {
                  paid: money(inst.paid_uf, inst.currency),
                  total: money(inst.amount_uf, inst.currency),
                })}
              </div>
              {balanceClp ? (
                <div className="text-caption text-ink-3 tabular-nums">
                  {t("public.installment.clp", { amount: balanceClp })}
                </div>
              ) : null}
            </>
          ) : (
            <>
              <div className="text-h3 tracking-tight text-ink tabular-nums">
                {money(inst.amount_uf, inst.currency)}
              </div>
              {open && amountClp ? (
                <div className="text-caption text-ink-3 tabular-nums">
                  {t("public.installment.clp", { amount: amountClp })}
                </div>
              ) : null}
            </>
          )}
        </div>
      </div>
    </div>
  );
}

function groupByPolicy(data: PublicShareRead): { policy: PublicPolicy; installments: PublicInstallment[] }[] {
  const policies = data.policies.length ? data.policies : data.policy ? [data.policy] : [];
  return policies.map((policy) => ({
    policy,
    installments: data.installments
      .filter((inst) => inst.policy_id === policy.id)
      .sort((a, b) => a.number - b.number),
  }));
}

// =============================================================================
// Insured audience
// =============================================================================

function InsuredView({ token, data }: { token: string; data: PublicShareRead }) {
  const { t } = useTranslation("share");
  const groups = groupByPolicy(data);
  const open = data.installments.filter((inst) => !CLOSED_STATES.has(inst.state));
  const overdue = open.filter((inst) => inst.days_late > 0);
  // One filled primary per view: the earliest cuota payable online.
  const firstPayable = [...data.installments]
    .filter((inst) => inst.action === "pay" && !inst.receipt_pending)
    .sort((a, b) => (a.due_date ?? "").localeCompare(b.due_date ?? ""))[0];
  const insuredName = data.policy?.insured_name ?? null;
  const broker = data.broker.name ?? t("public.brokerFallback");

  return (
    <>
      <div className="flex flex-col gap-1.5">
        <h1 className="text-h1 tracking-tight text-ink">
          {insuredName ? t("public.insured.greeting", { name: insuredName }) : t("public.insured.title")}
        </h1>
        <p className="text-body text-ink-2">{t("public.insured.lead", { broker })}</p>
        {data.case_file?.title ? (
          <p className="text-caption text-ink-3">
            {[data.case_file.title, data.case_file.period_label].filter(Boolean).join(" · ")}
          </p>
        ) : null}
      </div>

      {data.installments.length > 0 ? (
        <div className="grid grid-cols-2 gap-3">
          <Card className="p-4">
            <div className="text-caption text-ink-3">{t("public.insured.openCount")}</div>
            <div className="text-h2 tracking-tight text-ink tabular-nums">{open.length}</div>
          </Card>
          <Card className="p-4">
            <div className="text-caption text-ink-3">{t("public.insured.overdueCount")}</div>
            <div
              className={cn(
                "text-h2 tracking-tight tabular-nums",
                overdue.length > 0 ? "text-neg-text" : "text-ink",
              )}
            >
              {overdue.length}
            </div>
          </Card>
        </div>
      ) : null}

      {groups.map(({ policy, installments }) => (
        <section key={policy.id} className="flex flex-col gap-3">
          <PolicyCard policy={policy} showInsured={groups.length > 1} />
          {policy.payment_instructions ? (
            <InstructionsCard policyId={policy.id} instructions={policy.payment_instructions} />
          ) : null}
          {installments.length === 0 ? (
            <Card className="p-5 text-center text-caption text-ink-3">{t("public.insured.noInstallments")}</Card>
          ) : (
            <div className="flex flex-col gap-3">
              <h2 className="text-h3 tracking-tight text-ink">{t("public.insured.installmentsTitle")}</h2>
              {installments.map((inst) => (
                <InsuredInstallmentCard
                  key={inst.id}
                  token={token}
                  inst={inst}
                  policy={policy}
                  today={data.today}
                  primary={firstPayable?.id === inst.id}
                />
              ))}
            </div>
          )}
        </section>
      ))}

      {groups.length === 0 ? (
        <StateScreen tone="gone" title={t("public.insured.emptyTitle")} hint={t("public.insured.emptyHint")} />
      ) : null}
    </>
  );
}

function InstructionsCard({ policyId, instructions }: { policyId: number; instructions: PublicInstructions }) {
  const { t } = useTranslation("share");
  const rows = INSTRUCTION_ROWS.filter((row) => !!instructions[row.key]?.trim());
  const portal = isHttpUrl(instructions.portal_url) ? instructions.portal_url.trim() : null;
  const empty = rows.length === 0 && !portal && !instructions.notes?.trim();

  return (
    <Card id={`instrucciones-${policyId}`} className="scroll-mt-4 p-4 sm:p-5">
      <div className="flex flex-col gap-3">
        <div className="flex items-start gap-3">
          <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-sm bg-brand-soft text-brand-deep">
            <Banknote className="h-4 w-4" />
          </span>
          <div className="min-w-0">
            <h2 className="text-body font-medium text-ink">{t("public.instructions.title")}</h2>
            <p className="text-caption text-ink-3">
              {instructions.insurer_name
                ? t("public.instructions.lead", { insurer: instructions.insurer_name })
                : t("public.instructions.leadNoInsurer")}
            </p>
          </div>
        </div>

        {empty ? (
          <p className="rounded-sm bg-paper-2 px-3 py-2.5 text-caption text-ink-2">{t("public.instructions.empty")}</p>
        ) : null}

        {rows.length > 0 ? (
          <dl className="flex flex-col divide-y divide-line rounded-lg border border-line">
            {rows.map((row) => {
              const raw = instructions[row.key]!.trim();
              // RUTs read dotted everywhere ("76.123.456-7").
              const value = row.key === "account_rut" && isValidRut(raw) ? formatRut(raw) : raw;
              return (
                <div key={row.key} className="flex items-center justify-between gap-3 px-3 py-2">
                  <div className="min-w-0">
                    <dt className="text-caption text-ink-3">{t(row.label)}</dt>
                    <dd className="break-all text-body text-ink tabular-nums">{value}</dd>
                  </div>
                  {row.copy ? <CopyValue value={value} label={t(row.label)} /> : null}
                </div>
              );
            })}
          </dl>
        ) : null}

        {instructions.notes?.trim() ? (
          <p className="whitespace-pre-line text-caption text-ink-2">{instructions.notes.trim()}</p>
        ) : null}

        {portal ? (
          <Button variant="secondary" className="h-11 w-full sm:w-auto sm:self-start" asChild>
            <a href={portal} target="_blank" rel="noopener noreferrer">
              <ExternalLink className="h-4 w-4" />
              {t("public.instructions.portal")}
            </a>
          </Button>
        ) : null}
      </div>
    </Card>
  );
}

function CopyValue({ value, label }: { value: string; label: string }) {
  const { t } = useTranslation("share");
  const [copied, setCopied] = React.useState(false);
  return (
    <button
      type="button"
      onClick={() => {
        void copyText(value).then(() => {
          setCopied(true);
          window.setTimeout(() => setCopied(false), 2000);
        });
      }}
      aria-label={t("public.instructions.copyLabel", { field: label })}
      className="flex h-10 shrink-0 items-center gap-1.5 rounded-sm px-2.5 text-caption font-medium text-ink-2 transition-[background-color,color,transform] duration-150 ease-out hover:bg-paper-2 hover:text-ink active:scale-[0.96]"
    >
      {copied ? <Check className="h-4 w-4 text-pos-text" /> : <Copy className="h-4 w-4" />}
      {copied ? t("copiedShort") : t("copy")}
    </button>
  );
}

function InsuredInstallmentCard({
  token,
  inst,
  policy,
  today,
  primary,
}: {
  token: string;
  inst: PublicInstallment;
  policy: PublicPolicy;
  today: string;
  primary: boolean;
}) {
  const { t } = useTranslation("share");
  const checkout = usePublicCheckout(token);
  const [receiptOpen, setReceiptOpen] = React.useState(false);
  const [receiptDone, setReceiptDone] = React.useState<string | null>(null);
  const [redirectError, setRedirectError] = React.useState(false);
  const open = !CLOSED_STATES.has(inst.state);

  const pay = () => {
    setRedirectError(false);
    checkout.mutate([inst.id], {
      onSuccess: ({ checkout_url }) => {
        if (isHttpUrl(checkout_url)) window.location.assign(checkout_url);
        else setRedirectError(true);
      },
    });
  };
  // A receipt already sent (persisted server-side as `receipt_pending`, or
  // just now in this visit): paying again would pay twice — de-emphasise it.
  const inReview = !!inst.receipt_pending || !!receiptDone;
  const payFailed = checkout.isError || redirectError;
  const payable = open && inst.action === "pay";
  const instructionsHref = policy.payment_instructions ? `#instrucciones-${policy.id}` : null;

  return (
    <Card className="p-4 sm:p-5">
      <div className="flex flex-col gap-3">
        <InstallmentSummary inst={inst} inReview={inReview} />

        {inst.state === "paid" ? (
          <p className="flex items-center gap-2 text-caption text-pos-text">
            <CheckCircle2 className="h-4 w-4" />
            {t("public.insured.paidNote")}
          </p>
        ) : null}

        {open && inst.receipt_pending && !receiptDone ? (
          <p className="flex items-start gap-2 rounded-sm bg-brand-soft px-3 py-2.5 text-caption text-ink-2">
            <Clock className="mt-0.5 h-4 w-4 shrink-0 text-brand-deep" />
            <span>
              <span className="block font-medium text-ink">{t("public.receipt.inReviewTitle")}</span>
              {inst.receipt_pending.reported_at
                ? t("public.receipt.pending", { date: formatDateTime(inst.receipt_pending.reported_at) })
                : t("public.receipt.pendingNoDate")}
            </span>
          </p>
        ) : null}

        {payable ? (
          <div className="flex flex-col gap-2">
            <Button
              variant={primary && !inReview && !payFailed ? "primary" : inReview ? "ghost" : "secondary"}
              size="lg"
              className="h-11 w-full"
              onClick={pay}
              disabled={checkout.isPending}
            >
              {checkout.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <CreditCard className="h-4 w-4" />}
              {checkout.isPending ? t("public.pay.opening") : t("public.pay.button")}
            </Button>
            <p className="text-center text-caption text-ink-3">
              {inReview ? t("public.pay.inReviewHint") : t("public.pay.hint")}
            </p>
          </div>
        ) : null}

        {open && (inst.action === "instructions" || (payable && payFailed)) ? (
          instructionsHref ? (
            <a
              href={instructionsHref}
              className="flex items-center gap-2 rounded-sm bg-paper-2 px-3 py-2.5 text-caption text-ink-2 transition-[background-color] duration-150 hover:bg-line"
            >
              <Banknote className="h-4 w-4 shrink-0 text-brand" />
              <span>
                {policy.insurer_name
                  ? t("public.insured.payDirect", { insurer: policy.insurer_name })
                  : t("public.insured.payDirectNoInsurer")}
                {inst.can_upload_receipt && !inReview ? ` ${t("public.insured.thenUpload")}` : ""}
              </span>
            </a>
          ) : (
            <p className="flex items-center gap-2 rounded-sm bg-paper-2 px-3 py-2.5 text-caption text-ink-2">
              <Banknote className="h-4 w-4 shrink-0 text-brand" />
              <span>
                {policy.insurer_name
                  ? t("public.insured.payDirectNoInstructions", { insurer: policy.insurer_name })
                  : t("public.insured.payDirectNoInstructionsNoInsurer")}
                {inst.can_upload_receipt && !inReview ? ` ${t("public.insured.thenUpload")}` : ""}
              </span>
            </p>
          )
        ) : null}

        {checkout.isError ? <InlineError message={apiError(checkout.error, t("public.pay.error"))} /> : null}
        {redirectError ? <InlineError message={t("public.pay.error")} /> : null}

        {open && inst.can_upload_receipt ? (
          receiptDone ? (
            <SuccessNote message={receiptDone} />
          ) : receiptOpen ? (
            <ReceiptForm
              token={token}
              inst={inst}
              today={today}
              onCancel={() => setReceiptOpen(false)}
              onDone={(message) => {
                setReceiptDone(message);
                setReceiptOpen(false);
              }}
            />
          ) : (
            <Button variant="ghost" className="h-11 w-full" onClick={() => setReceiptOpen(true)}>
              <FileUp className="h-4 w-4" />
              {inst.receipt_pending ? t("public.receipt.openAnother") : t("public.receipt.open")}
            </Button>
          )
        ) : null}
      </div>
    </Card>
  );
}

function InlineError({ message }: { message: string }) {
  return (
    <div role="alert" className="flex items-start gap-2.5 rounded-lg bg-neg-soft px-3.5 py-2.5">
      <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-neg" />
      <p className="text-caption text-ink-2">{message}</p>
    </div>
  );
}

function SuccessNote({ message }: { message: string }) {
  return (
    <div role="status" className="flex items-start gap-2.5 rounded-lg bg-pos-soft px-3.5 py-2.5">
      <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-pos-text" />
      <p className="text-caption text-ink-2">{message}</p>
    </div>
  );
}

// --- Receipt upload ---------------------------------------------------------

function useReceiptSchema(today: string) {
  const { t } = useTranslation("share");
  return React.useMemo(
    () =>
      z
        .object({
        file: z
          .custom<File | null>((value) => value === null || value instanceof File)
          .refine((file): file is File => file instanceof File, t("public.receipt.errors.fileRequired"))
          .refine(
            (file) => !(file instanceof File) || RECEIPT_MIME_TYPES.includes(file.type) || /\.(pdf|jpe?g|png)$/i.test(file.name),
            t("public.receipt.errors.fileType"),
          )
          .refine(
            (file) => !(file instanceof File) || file.size <= RECEIPT_MAX_BYTES,
            t("public.receipt.errors.fileSize"),
          ),
        paid_on: z
          .string()
          .optional()
          .refine((value) => !value || value <= today, t("public.form.errors.future")),
        amount: z.string().optional(),
        currency: z.enum(["UF", "USD", "CLP"]),
        comment: z.string().max(2000).optional(),
      })
      .superRefine((values, ctx) => {
        if (!isValidAmount(values.amount, values.currency)) {
          ctx.addIssue({ code: z.ZodIssueCode.custom, path: ["amount"], message: t(AMOUNT_ERROR_KEY[values.currency]) });
        }
      }),
    [t, today],
  );
}

type ReceiptValues = {
  file: File | null;
  paid_on?: string;
  amount?: string;
  currency: PublicCurrency;
  comment?: string;
};

function ReceiptForm({
  token,
  inst,
  today,
  onCancel,
  onDone,
}: {
  token: string;
  inst: PublicInstallment;
  today: string;
  onCancel: () => void;
  onDone: (message: string) => void;
}) {
  const { t } = useTranslation("share");
  const schema = useReceiptSchema(today);
  const receipt = usePublicReceipt(token);
  const fileInput = React.useRef<HTMLInputElement>(null);
  const form = useForm<ReceiptValues>({
    resolver: zodResolver(schema),
    // A payer in Chile transfers pesos, and that is the figure on the bank receipt: default a
    // UF cuota to CLP. A USD cuota keeps USD.
    defaultValues: {
      file: null,
      paid_on: "",
      amount: "",
      currency: inst.currency === "USD" ? "USD" : "CLP",
      comment: "",
    },
  });
  const file = useWatch({ control: form.control, name: "file" });
  const currency = useWatch({ control: form.control, name: "currency" });
  const errors = form.formState.errors;
  const ids = `receipt-${inst.id}`;

  const submit = form.handleSubmit((values) => {
    if (!values.file) return;
    receipt.mutate(
      {
        installment_id: inst.id,
        file: values.file,
        paid_on: values.paid_on || null,
        amount: normalizeAmount(values.amount, values.currency),
        currency: values.currency,
        comment: values.comment?.trim() || null,
      },
      { onSuccess: (result) => onDone(result.message) },
    );
  });

  return (
    <form onSubmit={submit} className="flex flex-col gap-3 rounded-lg border border-line p-3" noValidate>
      <div className="flex flex-col gap-1">
        <p className="text-body font-medium text-ink">{t("public.receipt.title")}</p>
        <p className="text-caption text-ink-3">{t("public.receipt.hint")}</p>
      </div>

      <div className="flex flex-col gap-1.5">
        <Label htmlFor={`${ids}-file`}>{t("public.receipt.file")}</Label>
        <input
          ref={fileInput}
          id={`${ids}-file`}
          type="file"
          accept={RECEIPT_ACCEPT}
          className="sr-only"
          onChange={(e) => {
            const picked = e.target.files?.[0] ?? null;
            form.setValue("file", picked, { shouldValidate: true });
          }}
        />
        <Button
          type="button"
          variant="secondary"
          className="h-11 w-full justify-start"
          onClick={() => fileInput.current?.click()}
        >
          <FileUp className="h-4 w-4" />
          <span className="min-w-0 truncate">{file ? file.name : t("public.receipt.pick")}</span>
        </Button>
        <p className="text-caption text-ink-3">{t("public.receipt.limits")}</p>
        {errors.file ? <FieldError message={errors.file.message} /> : null}
      </div>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <div className="flex flex-col gap-1.5">
          <Label htmlFor={`${ids}-date`}>{t("public.form.paidOn")}</Label>
          <Input id={`${ids}-date`} type="date" max={today} className="h-11" {...form.register("paid_on")} />
          {errors.paid_on ? <FieldError message={errors.paid_on.message} /> : null}
        </div>
        <AmountField
          id={`${ids}-amount`}
          label={t("public.form.amountPaid")}
          currency={currency}
          onCurrencyChange={(next) => {
            form.setValue("currency", next);
            if (form.formState.isSubmitted) void form.trigger("amount");
          }}
          balanceClp={inst.balance_clp}
          error={errors.amount?.message}
          inputProps={form.register("amount")}
        />
      </div>
      <p className="-mt-1 text-caption text-ink-3">{t("public.receipt.currencyHint")}</p>

      <div className="flex flex-col gap-1.5">
        <Label htmlFor={`${ids}-comment`}>{t("public.form.comment")}</Label>
        <Textarea id={`${ids}-comment`} rows={2} maxLength={2000} {...form.register("comment")} />
      </div>

      {receipt.isError ? <InlineError message={apiError(receipt.error, t("public.form.errors.send"))} /> : null}

      <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
        <Button type="button" variant="ghost" className="h-11" onClick={onCancel} disabled={receipt.isPending}>
          {t("cancel")}
        </Button>
        <Button type="submit" variant="secondary" className="h-11" disabled={receipt.isPending}>
          {receipt.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
          {t("public.receipt.submit")}
        </Button>
      </div>
    </form>
  );
}

function FieldError({ message }: { message?: string }) {
  if (!message) return null;
  return <p className="text-caption text-neg-text">{message}</p>;
}

/**
 * Amount + its currency (UF · CLP · USD). The currency decides how the text is read, so
 * "150.000" in CLP is a hundred and fifty thousand pesos, never 150.
 */
function AmountField({
  id,
  label,
  currency,
  onCurrencyChange,
  balanceClp,
  error,
  inputProps,
}: {
  id: string;
  label: string;
  currency: PublicCurrency;
  onCurrencyChange: (next: PublicCurrency) => void;
  balanceClp: number | null;
  error?: string;
  inputProps: React.InputHTMLAttributes<HTMLInputElement> & { ref?: React.Ref<HTMLInputElement> };
}) {
  const { t } = useTranslation("share");
  const placeholder =
    currency === "CLP"
      ? t("public.form.amountPlaceholderClp", {
          amount: balanceClp !== null && balanceClp > 0 ? formatNumber(balanceClp, 0) : "150.000",
        })
      : t("public.form.amountPlaceholder");
  return (
    <div className="flex flex-col gap-1.5">
      <Label htmlFor={id}>{label}</Label>
      <div className="flex gap-2">
        <div
          className="flex shrink-0 rounded-sm bg-paper-2 p-0.5"
          role="radiogroup"
          aria-label={t("public.form.currency")}
        >
          {CURRENCY_OPTIONS.map((option) => (
            <button
              key={option}
              type="button"
              role="radio"
              aria-checked={currency === option}
              onClick={() => onCurrencyChange(option)}
              className={cn(
                "h-10 rounded-[6px] px-2.5 text-caption font-medium transition-[background-color,color,transform] duration-150 ease-out active:scale-[0.96]",
                currency === option ? "bg-bone text-ink shadow-elev" : "text-ink-3 hover:text-ink",
              )}
            >
              {option}
            </button>
          ))}
        </div>
        <Input
          id={id}
          inputMode={currency === "CLP" ? "numeric" : "decimal"}
          autoComplete="off"
          placeholder={placeholder}
          className="h-11 min-w-0"
          {...inputProps}
        />
      </div>
      {error ? <FieldError message={error} /> : null}
    </div>
  );
}

// =============================================================================
// Insurer audience
// =============================================================================

function InsurerView({ token, data }: { token: string; data: PublicShareRead }) {
  const { t } = useTranslation("share");
  const groups = groupByPolicy(data);
  const broker = data.broker.name ?? t("public.brokerFallback");
  const statuses: PublicReportStatus[] = data.report_statuses.length
    ? data.report_statuses.filter((s): s is PublicReportStatus => ALL_REPORT_STATUSES.includes(s))
    : ALL_REPORT_STATUSES;
  // One report form open at a time: one filled primary per view.
  const [openId, setOpenId] = React.useState<number | null>(() => {
    const only = data.installments.length === 1 ? data.installments[0] : null;
    return only && only.can_report && !only.reported ? only.id : null;
  });

  return (
    <>
      <div className="flex flex-col gap-1.5">
        <h1 className="text-h1 tracking-tight text-ink">{t("public.insurer.title")}</h1>
        <p className="text-body text-ink-2">
          {data.permissions.can_report_status
            ? t("public.insurer.lead", { broker })
            : t("public.insurer.leadReadOnly", { broker })}
        </p>
        {data.inquiry?.reference ? (
          <p className="text-caption text-ink-3 tabular-nums">
            {t("public.insurer.inquiry", { reference: data.inquiry.reference })}
          </p>
        ) : null}
      </div>

      {groups.map(({ policy, installments }) => (
        <section key={policy.id} className="flex flex-col gap-3">
          <PolicyCard policy={policy} showInsured />
          {installments.length === 0 ? (
            <Card className="p-5 text-center text-caption text-ink-3">{t("public.insurer.noInstallments")}</Card>
          ) : (
            <div className="flex flex-col gap-3">
              <h2 className="text-h3 tracking-tight text-ink">{t("public.insurer.installmentsTitle")}</h2>
              {installments.map((inst) => (
                <InsurerInstallmentCard
                  key={inst.id}
                  token={token}
                  inst={inst}
                  today={data.today}
                  statuses={statuses}
                  open={openId === inst.id}
                  onOpenChange={(next) => setOpenId(next ? inst.id : null)}
                />
              ))}
            </div>
          )}
        </section>
      ))}

      {groups.length === 0 ? (
        <StateScreen tone="gone" title={t("public.insurer.emptyTitle")} hint={t("public.insurer.emptyHint")} />
      ) : null}
    </>
  );
}

function InsurerInstallmentCard({
  token,
  inst,
  today,
  statuses,
  open,
  onOpenChange,
}: {
  token: string;
  inst: PublicInstallment;
  today: string;
  statuses: PublicReportStatus[];
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { t } = useTranslation("share");
  const setOpen = onOpenChange;
  const [done, setDone] = React.useState<string | null>(null);

  return (
    <Card className="p-4 sm:p-5">
      <div className="flex flex-col gap-3">
        <InstallmentSummary inst={inst} />

        {inst.reported?.status ? (
          <p className="flex items-center gap-2 rounded-sm bg-paper-2 px-3 py-2 text-caption text-ink-2">
            <Check className="h-4 w-4 shrink-0 text-pos-text" />
            {t("public.report.last", {
              status: t(reportLabelKey(inst.reported.status)),
              date: formatDateTime(inst.reported.reported_at),
            })}
          </p>
        ) : null}

        {done ? <SuccessNote message={done} /> : null}

        {inst.can_report ? (
          open ? (
            <ReportForm
              token={token}
              inst={inst}
              today={today}
              statuses={statuses}
              onCancel={() => setOpen(false)}
              onDone={(message) => {
                setDone(message);
                setOpen(false);
              }}
            />
          ) : (
            <Button
              variant="secondary"
              className="h-11 w-full"
              onClick={() => {
                setDone(null);
                setOpen(true);
              }}
            >
              <Send className="h-4 w-4" />
              {inst.reported ? t("public.report.again") : t("public.report.open")}
            </Button>
          )
        ) : null}
      </div>
    </Card>
  );
}

function useReportSchema(today: string) {
  const { t } = useTranslation("share");
  return React.useMemo(
    () =>
      z
        .object({
          status: z.enum(["paid", "unpaid", "partial", "cancelled"], {
            errorMap: () => ({ message: t("public.report.errors.status") }),
          }),
          amount: z.string().optional(),
          currency: z.enum(["UF", "USD", "CLP"]),
          paid_on: z
            .string()
            .optional()
            .refine((value) => !value || value <= today, t("public.form.errors.future")),
          comment: z.string().max(2000).optional(),
        })
        .superRefine((values, ctx) => {
          if (!isValidAmount(values.amount, values.currency)) {
            ctx.addIssue({
              code: z.ZodIssueCode.custom,
              path: ["amount"],
              message: t(AMOUNT_ERROR_KEY[values.currency]),
            });
          } else if (values.status === "partial" && !values.amount?.trim()) {
            ctx.addIssue({
              code: z.ZodIssueCode.custom,
              path: ["amount"],
              message: t("public.report.errors.partialAmount"),
            });
          }
        }),
    [t, today],
  );
}

type ReportValues = {
  status: PublicReportStatus | undefined;
  amount?: string;
  currency: PublicCurrency;
  paid_on?: string;
  comment?: string;
};

function ReportForm({
  token,
  inst,
  today,
  statuses,
  onCancel,
  onDone,
}: {
  token: string;
  inst: PublicInstallment;
  today: string;
  statuses: PublicReportStatus[];
  onCancel: () => void;
  onDone: (message: string) => void;
}) {
  const { t } = useTranslation("share");
  const schema = useReportSchema(today);
  const report = usePublicReport(token);
  const form = useForm<ReportValues>({
    resolver: zodResolver(schema),
    defaultValues: {
      status: undefined,
      amount: "",
      currency: inst.currency === "USD" ? "USD" : "UF",
      paid_on: "",
      comment: "",
    },
  });
  const status = useWatch({ control: form.control, name: "status" });
  const currency = useWatch({ control: form.control, name: "currency" });
  const errors = form.formState.errors;
  const ids = `report-${inst.id}`;
  const showPayment = status === "paid" || status === "partial";

  const submit = form.handleSubmit((values) => {
    if (!values.status) return;
    const withPayment = values.status === "paid" || values.status === "partial";
    const amount = withPayment ? normalizeAmount(values.amount, values.currency) : null;
    report.mutate(
      {
        installment_id: inst.id,
        status: values.status,
        amount,
        currency: values.currency,
        paid_on: withPayment ? values.paid_on || null : null,
        comment: values.comment?.trim() || null,
      },
      { onSuccess: (result) => onDone(result.message) },
    );
  });

  return (
    <form onSubmit={submit} className="flex flex-col gap-3 rounded-lg border border-line p-3" noValidate>
      <div className="flex flex-col gap-1.5">
        <Label id={`${ids}-status`}>{t("public.report.statusLabel")}</Label>
        <div role="radiogroup" aria-labelledby={`${ids}-status`} className="grid grid-cols-1 gap-2 sm:grid-cols-2">
          {statuses.map((option) => {
            const active = status === option;
            return (
              <button
                key={option}
                type="button"
                role="radio"
                aria-checked={active}
                onClick={() => form.setValue("status", option, { shouldValidate: true })}
                className={cn(
                  "flex min-h-[44px] flex-col items-start justify-center rounded-sm border px-3 py-2 text-left transition-[background-color,border-color,box-shadow,transform] duration-150 ease-out active:scale-[0.96]",
                  active ? "border-brand bg-brand-soft shadow-elev" : "border-line bg-bone hover:border-line-strong",
                )}
              >
                <span className={cn("text-body font-medium", active ? "text-brand-deep" : "text-ink")}>
                  {t(REPORT_LABEL[option])}
                </span>
                <span className="text-caption text-ink-3">{t(REPORT_HINT[option])}</span>
              </button>
            );
          })}
        </div>
        {errors.status ? <FieldError message={errors.status.message} /> : null}
      </div>

      {showPayment ? (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor={`${ids}-date`}>{t("public.form.paidOn")}</Label>
            <Input id={`${ids}-date`} type="date" max={today} className="h-11" {...form.register("paid_on")} />
            {errors.paid_on ? <FieldError message={errors.paid_on.message} /> : null}
          </div>
          <AmountField
            id={`${ids}-amount`}
            label={status === "partial" ? t("public.report.amountRequired") : t("public.report.amount")}
            currency={currency}
            onCurrencyChange={(next) => {
              form.setValue("currency", next);
              if (form.formState.isSubmitted) void form.trigger("amount");
            }}
            balanceClp={inst.balance_clp}
            error={errors.amount?.message}
            inputProps={form.register("amount")}
          />
        </div>
      ) : null}

      <div className="flex flex-col gap-1.5">
        <Label htmlFor={`${ids}-comment`}>{t("public.form.comment")}</Label>
        <Textarea
          id={`${ids}-comment`}
          rows={2}
          maxLength={2000}
          placeholder={t("public.report.commentPlaceholder")}
          {...form.register("comment")}
        />
      </div>

      {report.isError ? <InlineError message={apiError(report.error, t("public.form.errors.send"))} /> : null}

      <p className="text-caption text-ink-3">{t("public.report.note")}</p>

      <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
        <Button type="button" variant="ghost" className="h-11" onClick={onCancel} disabled={report.isPending}>
          {t("cancel")}
        </Button>
        <Button type="submit" className="h-11" disabled={report.isPending}>
          {report.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
          {t("public.report.submit")}
        </Button>
      </div>
    </form>
  );
}
