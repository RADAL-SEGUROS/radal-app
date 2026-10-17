/**
 * One "Respuesta por confirmar": an insurer reply (mail), an insurer report
 * through a share link, an insurer statement, or a payer's comprobante.
 *
 * Suggest → human confirm → commit: nothing here is booked until the broker
 * presses Confirmar. "Otra cuota" re-targets the suggestion before confirming;
 * Rechazar records the decision and books nothing. A sender Radal did not ask
 * (`sender_verified === false`) is called out before any button.
 */
import * as React from "react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { AlertTriangle, CheckCircle2, Loader2, Mail, Repeat2, XCircle } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { DisabledHint, ErrorBanner, LoadingRows } from "@/components/common/kit";
import { PreviewDocButton } from "@/components/common/DownloadDocButton";
import { formatDate, formatDateTime } from "@/lib/format";
import { useCan } from "@/lib/permissions";
import { cn } from "@/lib/utils";
import { num } from "@/api/types";
import {
  useConfirmPaymentEvidence,
  useInstallmentPayments,
  usePolicyInstallmentRows,
  useRejectPaymentEvidence,
  type EvidenceReasonEntry,
  type PaymentCurrencyCode,
  type PaymentEvidenceRead,
} from "@/api/collections";
import {
  EVIDENCE_REASON_LABEL,
  EVIDENCE_SOURCE_LABEL,
  EVIDENCE_STATUS_LABEL,
  EVIDENCE_STATUS_TONE,
  NEGATIVE_REASONS,
  PAYMENT_SOURCE_LABEL,
  REPORTED_STATUS_LABEL,
  STATE_LABEL,
  labelOf,
  toneOf,
} from "@/components/collections/labels";
import { Field, NativeSelect, money, todaySantiago } from "@/components/collections/shared";
import { AMOUNT_ERROR_KEY, UF_TOLERANCE, amountInputFor, parseAmount } from "@/components/collections/amount";
import { EvidenceLevelBadge } from "@/components/collections/PaymentsPanel";

const OPEN = new Set(["suggested", "needs_review"]);
/** A cuota with nothing left to receive — «Otra cuota» shows it, disabled. */
const CLOSED_STATES = new Set(["paid", "voided"]);
/**
 * Replies that explicitly say "not paid" carry no money. An `unknown` (or
 * unclassified) reply is NOT here: the server books a payment for it exactly
 * when the broker enters an amount, so the fields stay — optional.
 */
const NO_MONEY = new Set(["unpaid", "cancelled"]);

type Mode = "idle" | "confirm" | "other" | "reject";

interface ReasonChip {
  key: string;
  text: string;
  negative: boolean;
}

function useReasonChips(reasons: PaymentEvidenceRead["reasons"]): ReasonChip[] {
  const { t } = useTranslation("collections");
  const list: EvidenceReasonEntry[] = Array.isArray(reasons)
    ? reasons
    : reasons && typeof reasons === "object"
      ? [reasons as EvidenceReasonEntry]
      : [];
  const out: ReasonChip[] = [];
  list.forEach((entry, i) => {
    if (typeof entry === "string") {
      if (Object.prototype.hasOwnProperty.call(EVIDENCE_REASON_LABEL, entry)) {
        out.push({ key: `${entry}-${i}`, text: labelOf(t, EVIDENCE_REASON_LABEL, entry), negative: NEGATIVE_REASONS.has(entry) });
      }
      return;
    }
    if (entry && typeof entry === "object") {
      // `{code, detail}` — the server already wrote the Spanish `detail`.
      const code = typeof entry.code === "string" ? entry.code : "";
      const text =
        typeof entry.detail === "string" && entry.detail
          ? entry.detail
          : code && Object.prototype.hasOwnProperty.call(EVIDENCE_REASON_LABEL, code)
            ? labelOf(t, EVIDENCE_REASON_LABEL, code)
            : null;
      if (text) out.push({ key: `${code || "detail"}-${i}`, text, negative: NEGATIVE_REASONS.has(code) });
    }
  });
  return out;
}

/** The target cuota's money as it stands now, from the server or the live board row. */
interface CuotaMoney {
  gross: number | null;
  paid: number;
  balance: number | null;
  state: string;
  currency: string;
  payments: {
    id: number;
    amount_uf: string;
    evidence_level: string;
    paid_on: string | null;
    source: string | null;
  }[];
}

/**
 * The cuota a confirmation will land on — the suggestion, or the one chosen
 * with «Otra cuota» — with what is ALREADY booked on it. The server's
 * `installment_summary` describes the SUGGESTED cuota only; a re-targeted one
 * is read from the live board row and its payments.
 */
function useTargetCuota(evidence: PaymentEvidenceRead, installmentId: number | undefined) {
  const targetId = installmentId ?? evidence.installment_id ?? undefined;
  const retargeted = installmentId !== undefined && installmentId !== evidence.installment_id;
  const server = !retargeted ? (evidence.installment_summary ?? null) : null;
  const rows = usePolicyInstallmentRows(evidence.policy_id, !!targetId);
  const row = rows.data?.items.find((r) => r.id === targetId) ?? null;
  const payments = useInstallmentPayments(targetId, !!targetId && !server);
  let summary: CuotaMoney | null = null;
  if (server) {
    summary = {
      gross: num(server.gross_uf),
      paid: num(server.paid_uf) ?? 0,
      balance: num(server.balance_uf),
      state: server.state,
      currency: server.currency,
      payments: (server.payments ?? []).map((p) => ({
        id: p.id,
        amount_uf: p.amount_uf,
        evidence_level: p.evidence_level,
        paid_on: p.paid_on,
        source: p.source,
      })),
    };
  } else if (row) {
    summary = {
      gross: num(row.gross_amount_uf),
      paid: num(row.paid_uf) ?? 0,
      balance: num(row.balance_uf),
      state: row.state,
      currency: row.currency,
      payments: (payments.data ?? [])
        .filter((p) => p.status === "active")
        .map((p) => ({
          id: p.id,
          amount_uf: p.amount_uf,
          evidence_level: p.evidence_level,
          paid_on: p.paid_on,
          source: p.source,
        })),
    };
  }
  return {
    targetId,
    retargeted,
    row,
    summary,
    ufRate: num(rows.data?.uf_rate ?? null),
    loading: !!targetId && (rows.isLoading || (!server && payments.isLoading)),
  };
}

/** What the amount field starts with: the BALANCE when the report exceeds it — never the gross. */
function initialAmount(
  evidence: PaymentEvidenceRead,
  summary: CuotaMoney | null,
  retargeted: boolean,
): { amount: string; currency: PaymentCurrencyCode } {
  const reportedCurrency: PaymentCurrencyCode = evidence.reported_currency ?? "UF";
  const cuotaCurrency: PaymentCurrencyCode = summary?.currency === "USD" ? "USD" : "UF";
  const suggested = num(evidence.suggested_amount_uf ?? null);
  if (!retargeted && suggested !== null && suggested > 0) {
    return { amount: amountInputFor(suggested, "UF"), currency: "UF" };
  }
  const reported = num(evidence.reported_amount);
  const balance = summary?.balance ?? null;
  if (reportedCurrency !== "CLP" && reported !== null && balance !== null && reported > balance + UF_TOLERANCE) {
    return { amount: amountInputFor(balance, cuotaCurrency), currency: cuotaCurrency };
  }
  return { amount: amountInputFor(reported, reportedCurrency), currency: reportedCurrency };
}

function TargetCuotaBlock({
  evidence,
  target,
}: {
  evidence: PaymentEvidenceRead;
  target: ReturnType<typeof useTargetCuota>;
}) {
  const { t } = useTranslation(["collections", "common"]);
  const { summary, row } = target;
  if (!target.targetId) return null;
  if (target.loading && !summary) return <LoadingRows rows={1} />;
  const number = row?.number ?? evidence.installment_number;
  const count = row?.installment_count ?? null;
  const cur = summary?.currency ?? "UF";
  return (
    <div className="flex flex-col gap-2 rounded-lg bg-paper-2 px-3 py-2.5">
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <span className="text-caption font-medium text-ink">
          {count
            ? t("collections:board.installmentOf", { number: number ?? "—", count })
            : t("collections:evidence.installmentN", { number: number ?? "—" })}
          {row?.due_date ?? evidence.installment_due_date ? (
            <span className="font-normal text-ink-3"> · {formatDate(row?.due_date ?? evidence.installment_due_date)}</span>
          ) : null}
        </span>
        {summary ? (
          <span className="text-caption tabular-nums text-ink-2">
            {t("collections:evidence.balanceLine", {
              balance: money(summary.balance, cur),
              paid: money(summary.paid, cur),
              gross: money(summary.gross, cur),
            })}
          </span>
        ) : null}
      </div>
      {summary && summary.payments.length > 0 ? (
        <div className="flex flex-col gap-1">
          <span className="text-caption text-ink-3">{t("collections:evidence.existingPayments")}</span>
          <ul className="flex flex-col gap-1">
            {summary.payments.map((p) => (
              <li key={p.id} className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-caption">
                <span className="font-medium tabular-nums text-ink">{money(p.amount_uf, cur)}</span>
                {p.paid_on ? <span className="text-ink-3">{formatDate(p.paid_on)}</span> : null}
                {p.source ? (
                  <span className="text-ink-3">· {labelOf(t, PAYMENT_SOURCE_LABEL, p.source, "")}</span>
                ) : null}
                <span className="ml-auto">
                  <EvidenceLevelBadge level={p.evidence_level} />
                </span>
              </li>
            ))}
          </ul>
        </div>
      ) : summary ? (
        <span className="text-caption text-ink-3">{t("collections:evidence.noExistingPayments")}</span>
      ) : null}
    </div>
  );
}

function ConfirmForm({
  evidence,
  installmentId,
  onDone,
  onCancel,
}: {
  evidence: PaymentEvidenceRead;
  /** Re-targeted cuota ("Otra cuota"); `undefined` keeps the suggestion. */
  installmentId?: number;
  onDone: () => void;
  onCancel: () => void;
}) {
  const { t } = useTranslation(["collections", "common"]);
  const confirm = useConfirmPaymentEvidence();
  const today = todaySantiago();
  const target = useTargetCuota(evidence, installmentId);
  const { summary } = target;
  const noMoney = NO_MONEY.has(evidence.reported_status ?? "unknown") && evidence.source !== "payer_upload";
  const unclassified = (evidence.reported_status ?? "unknown") === "unknown" && evidence.source !== "payer_upload";
  const first = initialAmount(evidence, summary, target.retargeted);
  const [amount, setAmount] = React.useState(first.amount);
  const [currency, setCurrency] = React.useState<PaymentCurrencyCode>(first.currency);
  // The cuota's money loads after the form mounts: re-seed the amount ONCE it
  // arrives, unless the broker already typed something.
  const touched = React.useRef(false);
  const seeded = React.useRef(!!summary);
  React.useEffect(() => {
    if (seeded.current || !summary || touched.current) return;
    seeded.current = true;
    const next = initialAmount(evidence, summary, target.retargeted);
    setAmount(next.amount);
    setCurrency(next.currency);
  }, [summary, evidence, target.retargeted]);
  const [paidOn, setPaidOn] = React.useState(evidence.reported_paid_on ?? "");
  // The insurer itself reported it (its share link / its inquiry reply from a
  // verified sender): "Confirmado por la compañía" starts on.
  const [confirmed, setConfirmed] = React.useState(
    (evidence.source === "insurer_share_link" || evidence.source === "insurer_reply") &&
      evidence.sender_verified !== false,
  );
  const [note, setNote] = React.useState("");

  const cuotaCurrency: PaymentCurrencyCode = summary?.currency === "USD" ? "USD" : "UF";
  const balance = summary?.balance ?? null;
  const settled = balance !== null && balance <= UF_TOLERANCE;

  // Parsed BY CURRENCY: "150.000" in CLP is 150 mil pesos, not 150.
  const parsed = amount.trim() ? parseAmount(amount, currency) : null;
  const n = parsed?.value ?? NaN;
  // Same currency as the cuota: an exact over-balance check (the server
  // refuses it with a 409 anyway). CLP: an estimate at today's UF, a warning.
  const overBalance =
    balance !== null && Number.isFinite(n) && currency === cuotaCurrency && n > balance + UF_TOLERANCE;
  const clpOver =
    balance !== null &&
    Number.isFinite(n) &&
    currency === "CLP" &&
    !!target.ufRate &&
    n / target.ufRate > balance + UF_TOLERANCE;
  const amountError = !parsed
    ? null
    : parsed.error !== null
      ? t(AMOUNT_ERROR_KEY[parsed.error])
      : n <= 0
        ? t("collections:form.positiveAmount")
        : overBalance
          ? t("collections:evidence.overBalance", { balance: money(balance, cuotaCurrency) })
          : null;
  const dateError = paidOn && paidOn > today ? t("collections:form.noFutureDate") : null;
  const balanceInput = balance !== null && balance > UF_TOLERANCE ? amountInputFor(balance, cuotaCurrency) : "";
  const showUseBalance = !!balanceInput && !(currency === cuotaCurrency && amount.trim() === balanceInput);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (amountError || dateError) return;
    try {
      const result = await confirm.mutateAsync({
        evidenceId: evidence.id,
        installment_id: installmentId ?? null,
        amount: parsed ? String(n) : null,
        currency: parsed ? currency : null,
        paid_on: paidOn || null,
        confirmed_by_insurer: confirmed,
        note: note.trim() || null,
      });
      toast.success(
        result.payment_id ? t("collections:evidence.confirmedPayment") : t("collections:evidence.confirmedNoMoney"),
      );
      onDone();
    } catch {
      // ErrorBanner — a 409 over the balance carries the server's Spanish reason.
    }
  };

  return (
    <form onSubmit={(e) => void submit(e)} className="flex flex-col gap-3 border-t border-line pt-3" noValidate>
      <TargetCuotaBlock evidence={evidence} target={target} />
      {noMoney ? (
        <p className="text-caption text-ink-3">{t("collections:evidence.noMoneyHint")}</p>
      ) : (
        <>
          {settled ? (
            <div role="status" className="flex items-start gap-2 rounded-lg bg-warn-soft px-3 py-2 text-caption text-ink-2">
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-warn" />
              {t("collections:evidence.alreadySettled")}
            </div>
          ) : null}
          <div className="grid grid-cols-[1fr_88px] gap-3 sm:grid-cols-[1fr_88px_1fr]">
            <Field
              label={t("collections:payment.amount")}
              htmlFor={`ev-amount-${evidence.id}`}
              error={amountError}
              hint={
                Number.isFinite(n) && n > 0
                  ? clpOver
                    ? t("collections:evidence.clpOverBalance", { balance: money(balance, cuotaCurrency) })
                    : t("collections:payment.willRegister", { amount: money(n, currency) })
                  : unclassified
                    ? null
                    : t("collections:evidence.amountHint")
              }
            >
              <Input
                id={`ev-amount-${evidence.id}`}
                inputMode="decimal"
                value={amount}
                onChange={(e) => {
                  touched.current = true;
                  setAmount(e.target.value);
                }}
              />
            </Field>
            <Field label={t("collections:payment.currency")} htmlFor={`ev-cur-${evidence.id}`}>
              <NativeSelect
                id={`ev-cur-${evidence.id}`}
                value={currency}
                onChange={(e) => {
                  touched.current = true;
                  setCurrency(e.target.value as PaymentCurrencyCode);
                }}
              >
                <option value="UF">UF</option>
                <option value="CLP">CLP</option>
                <option value="USD">USD</option>
              </NativeSelect>
            </Field>
            <Field
              label={t("collections:payment.paidOn")}
              htmlFor={`ev-date-${evidence.id}`}
              error={dateError}
              className="col-span-2 sm:col-span-1"
            >
              <Input
                id={`ev-date-${evidence.id}`}
                type="date"
                max={today}
                value={paidOn}
                onChange={(e) => setPaidOn(e.target.value)}
              />
            </Field>
          </div>
          {showUseBalance ? (
            <button
              type="button"
              onClick={() => {
                touched.current = true;
                setAmount(balanceInput);
                setCurrency(cuotaCurrency);
              }}
              className="-mt-1 self-start text-caption font-medium text-brand-deep underline-offset-2 transition-[color] duration-150 hover:underline"
            >
              {t("collections:evidence.useBalance", { balance: money(balance, cuotaCurrency) })}
            </button>
          ) : null}
          {unclassified ? (
            <p className="-mt-1 text-caption text-ink-3">{t("collections:evidence.unknownAmountHint")}</p>
          ) : null}
          {evidence.source !== "insurer_statement" ? (
            <label className="flex cursor-pointer items-start gap-2.5">
              <Checkbox checked={confirmed} onCheckedChange={(v) => setConfirmed(v === true)} className="mt-0.5" />
              <span className="text-caption text-ink-2">{t("collections:payment.confirmedByInsurer")}</span>
            </label>
          ) : null}
        </>
      )}
      <Field label={t("collections:payment.note")} htmlFor={`ev-note-${evidence.id}`}>
        <Textarea id={`ev-note-${evidence.id}`} rows={2} value={note} onChange={(e) => setNote(e.target.value)} />
      </Field>
      <ErrorBanner error={confirm.error} />
      <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
        <Button type="button" variant="ghost" size="sm" onClick={onCancel} disabled={confirm.isPending}>
          {t("common:actions.cancel")}
        </Button>
        <Button type="submit" variant="success" size="sm" disabled={confirm.isPending || !!amountError || !!dateError}>
          {confirm.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <CheckCircle2 className="h-4 w-4" />}
          {t("collections:evidence.confirmSubmit")}
        </Button>
      </div>
    </form>
  );
}

function OtherInstallmentPicker({
  evidence,
  onPick,
  onCancel,
}: {
  evidence: PaymentEvidenceRead;
  onPick: (installmentId: number) => void;
  onCancel: () => void;
}) {
  const { t } = useTranslation(["collections", "common"]);
  // Every cuota of the policy (shared cache with the sheet), live state.
  const rows = usePolicyInstallmentRows(evidence.policy_id, !!evidence.policy_id);
  const [choice, setChoice] = React.useState<string>("");
  if (!evidence.policy_id) {
    return <p className="border-t border-line pt-3 text-caption text-ink-3">{t("collections:evidence.noPolicy")}</p>;
  }
  const items = (rows.data?.items ?? []).filter((r) => r.id !== evidence.installment_id);
  const chosen = items.find((r) => String(r.id) === choice) ?? null;
  const openCount = items.filter((r) => !CLOSED_STATES.has(r.state)).length;
  return (
    <div className="flex flex-col gap-3 border-t border-line pt-3">
      {rows.isLoading ? (
        <LoadingRows rows={2} />
      ) : items.length === 0 ? (
        <p className="text-caption text-ink-3">{t("collections:evidence.noOtherInstallments")}</p>
      ) : (
        <Field
          label={t("collections:evidence.pickInstallment")}
          htmlFor={`ev-other-${evidence.id}`}
          hint={openCount === 0 ? t("collections:evidence.allOthersPaid") : t("collections:evidence.paidDisabledHint")}
        >
          <NativeSelect id={`ev-other-${evidence.id}`} value={choice} onChange={(e) => setChoice(e.target.value)}>
            <option value="">—</option>
            {items.map((r) => {
              const closed = CLOSED_STATES.has(r.state);
              return (
                <option key={r.id} value={r.id} disabled={closed}>
                  {t("collections:board.installmentOf", { number: r.number, count: r.installment_count ?? r.number })}{" "}
                  · {formatDate(r.due_date)} ·{" "}
                  {closed
                    ? labelOf(t, STATE_LABEL, r.state, t("collections:state.unknown"))
                    : t("collections:evidence.balanceShort", { balance: money(r.balance_uf, r.currency) })}
                </option>
              );
            })}
          </NativeSelect>
        </Field>
      )}
      {chosen ? (
        <p className="text-caption tabular-nums text-ink-2">
          {t("collections:evidence.balanceLine", {
            balance: money(chosen.balance_uf, chosen.currency),
            paid: money(chosen.paid_uf, chosen.currency),
            gross: money(chosen.gross_amount_uf, chosen.currency),
          })}
        </p>
      ) : null}
      <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
        <Button type="button" variant="ghost" size="sm" onClick={onCancel}>
          {t("common:actions.cancel")}
        </Button>
        <Button type="button" variant="secondary" size="sm" disabled={!chosen} onClick={() => onPick(Number(choice))}>
          {t("common:actions.next")}
        </Button>
      </div>
    </div>
  );
}

function RejectForm({
  evidence,
  onDone,
  onCancel,
}: {
  evidence: PaymentEvidenceRead;
  onDone: () => void;
  onCancel: () => void;
}) {
  const { t } = useTranslation(["collections", "common"]);
  const reject = useRejectPaymentEvidence();
  const [note, setNote] = React.useState("");
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      await reject.mutateAsync({ evidenceId: evidence.id, note: note.trim() || null });
      toast.success(t("collections:evidence.rejected"));
      onDone();
    } catch {
      // ErrorBanner.
    }
  };
  return (
    <form onSubmit={(e) => void submit(e)} className="flex flex-col gap-3 border-t border-line pt-3">
      <Field label={t("collections:evidence.rejectNote")} htmlFor={`ev-rej-${evidence.id}`}>
        <Textarea id={`ev-rej-${evidence.id}`} rows={2} value={note} onChange={(e) => setNote(e.target.value)} />
      </Field>
      <ErrorBanner error={reject.error} />
      <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
        <Button type="button" variant="ghost" size="sm" onClick={onCancel} disabled={reject.isPending}>
          {t("common:actions.cancel")}
        </Button>
        <Button type="submit" variant="destructive" size="sm" disabled={reject.isPending}>
          {reject.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <XCircle className="h-4 w-4" />}
          {t("collections:evidence.rejectSubmit")}
        </Button>
      </div>
    </form>
  );
}

export function EvidenceCard({ evidence }: { evidence: PaymentEvidenceRead }) {
  const { t } = useTranslation(["collections", "common"]);
  const canEdit = useCan("Collections", "Edit");
  const [mode, setMode] = React.useState<Mode>("idle");
  const [target, setTarget] = React.useState<number | undefined>(undefined);
  const chips = useReasonChips(evidence.reasons);
  const open = OPEN.has(evidence.status);
  const score = evidence.score !== null ? Math.round(Number(evidence.score)) : null;
  const gate = canEdit.isLoading
    ? t("common:state.loading")
    : canEdit.allowed
      ? null
      : t("collections:sheet.noEditPermission");
  const noTarget = !evidence.installment_id && target === undefined;
  const reset = () => {
    setMode("idle");
    setTarget(undefined);
  };

  return (
    <Card className="flex flex-col gap-3 p-4 sm:p-5">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="flex flex-wrap items-center gap-1.5">
          <Badge variant="neutral">
            {labelOf(t, EVIDENCE_SOURCE_LABEL, evidence.source, t("collections:evidence.source.unknown"))}
          </Badge>
          <Badge variant={toneOf(EVIDENCE_STATUS_TONE, evidence.status)} dot>
            {labelOf(t, EVIDENCE_STATUS_LABEL, evidence.status)}
          </Badge>
          <span className="text-caption text-ink-3">{formatDateTime(evidence.created_at)}</span>
        </div>
        {score !== null ? (
          <div className="flex items-center gap-2" title={t("collections:evidence.scoreHint")}>
            <span className="text-caption text-ink-3">{t("collections:evidence.score")}</span>
            <div className="h-1.5 w-16 overflow-hidden rounded-full bg-paper-2" aria-hidden>
              <div
                className={cn("h-full rounded-full", score >= 60 ? "bg-pos" : score >= 35 ? "bg-warn" : "bg-neg")}
                style={{ width: `${Math.min(100, Math.max(0, score))}%` }}
              />
            </div>
            <span className="text-caption font-medium tabular-nums text-ink">{score}</span>
          </div>
        ) : null}
      </div>

      <div className="min-w-0">
        <div className="text-body font-medium text-ink">
          {evidence.policy_id ? (
            <Link to={`/policies/${evidence.policy_id}`} className="hover:underline">
              {t("collections:board.policyShort")} {evidence.policy_number ?? `#${evidence.policy_id}`}
            </Link>
          ) : (
            t("collections:evidence.noPolicy")
          )}
          {evidence.insurer_name ? <span className="font-normal text-ink-3"> · {evidence.insurer_name}</span> : null}
        </div>
        {evidence.mail_from || evidence.mail_subject ? (
          <div className="mt-1 flex min-w-0 items-center gap-1.5 text-caption text-ink-3">
            <Mail className="h-3.5 w-3.5 shrink-0" />
            <span className="truncate">
              {evidence.mail_from}
              {evidence.mail_subject ? ` — ${evidence.mail_subject}` : ""}
            </span>
          </div>
        ) : null}
      </div>

      {evidence.sender_verified === false ? (
        <div role="alert" className="flex items-start gap-2 rounded-lg bg-warn-soft px-3 py-2 text-caption text-ink-2">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-warn" />
          {t("collections:evidence.senderUnverified")}
        </div>
      ) : null}

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <div className="rounded-lg bg-paper-2 px-3 py-2.5">
          <div className="text-caption font-medium text-ink-3">
            {evidence.source === "payer_upload"
              ? t("collections:evidence.payerReports")
              : t("collections:evidence.reports")}
          </div>
          <div className="mt-0.5 text-body font-medium text-ink">
            {labelOf(t, REPORTED_STATUS_LABEL, evidence.reported_status, t("collections:evidence.reported.unknown"))}
          </div>
          <div className="text-caption tabular-nums text-ink-2">
            {[
              evidence.reported_amount ? money(evidence.reported_amount, evidence.reported_currency ?? "UF") : null,
              evidence.reported_paid_on ? formatDate(evidence.reported_paid_on) : null,
            ]
              .filter(Boolean)
              .join(" · ") || "—"}
          </div>
          {evidence.note ? <p className="mt-1.5 text-caption text-ink-2">“{evidence.note}”</p> : null}
        </div>
        <div className={cn("rounded-lg px-3 py-2.5", evidence.installment_id ? "bg-brand-soft" : "bg-warn-soft")}>
          <div className="text-caption font-medium text-ink-3">{t("collections:evidence.suggested")}</div>
          {evidence.installment_id ? (
            <>
              <div className="mt-0.5 text-body font-medium text-ink">
                {t("collections:evidence.installmentN", { number: evidence.installment_number ?? "—" })}
              </div>
              <div className="text-caption tabular-nums text-ink-2">
                {formatDate(evidence.installment_due_date)} · {money(evidence.installment_amount_uf, "UF")}
              </div>
              {evidence.installment_summary ? (
                <div className="text-caption tabular-nums text-ink-2">
                  {t("collections:evidence.balanceShort", {
                    balance: money(evidence.installment_summary.balance_uf, evidence.installment_summary.currency),
                  })}
                  {(num(evidence.installment_summary.paid_uf) ?? 0) > 0
                    ? ` · ${t("collections:evidence.paidShort", {
                        paid: money(evidence.installment_summary.paid_uf, evidence.installment_summary.currency),
                      })}`
                    : ""}
                </div>
              ) : null}
            </>
          ) : (
            <div className="mt-0.5 text-body text-ink-2">{t("collections:evidence.noInstallment")}</div>
          )}
        </div>
      </div>

      {chips.length > 0 ? (
        <div className="flex flex-wrap gap-1.5">
          {chips.map((c) => (
            <Badge key={c.key} variant={c.negative ? "warn" : "outline"} className="px-1.5 py-0 text-[11px]">
              {c.text}
            </Badge>
          ))}
        </div>
      ) : null}

      {evidence.document_id || open ? (
        <div className="flex flex-wrap gap-2">
          {evidence.document_id ? (
            <PreviewDocButton documentId={evidence.document_id} label={t("collections:evidence.viewDocument")} />
          ) : null}
          {open && mode === "idle" ? (
            <>
              <DisabledHint hint={gate ?? (noTarget ? t("collections:evidence.pickFirst") : null)}>
                <Button
                  variant="success"
                  size="sm"
                  disabled={!canEdit.allowed || noTarget}
                  onClick={() => setMode("confirm")}
                >
                  <CheckCircle2 className="h-4 w-4" />
                  {t("collections:evidence.confirm")}
                </Button>
              </DisabledHint>
              <DisabledHint hint={gate}>
                <Button variant="secondary" size="sm" disabled={!canEdit.allowed} onClick={() => setMode("other")}>
                  <Repeat2 className="h-4 w-4" />
                  {t("collections:evidence.other")}
                </Button>
              </DisabledHint>
              <DisabledHint hint={gate}>
                <Button variant="ghost" size="sm" disabled={!canEdit.allowed} onClick={() => setMode("reject")}>
                  <XCircle className="h-4 w-4" />
                  {t("collections:evidence.reject")}
                </Button>
              </DisabledHint>
            </>
          ) : null}
        </div>
      ) : null}
      {/* The buttons' WHY, visible — Radix tooltips never open on touch. */}
      {open && mode === "idle" && (gate || noTarget) ? (
        <p className="-mt-1 text-caption text-ink-3">{gate ?? t("collections:evidence.pickFirst")}</p>
      ) : null}

      {target !== undefined && mode === "confirm" ? (
        <p className="text-caption text-brand-deep">{t("collections:evidence.retargeted")}</p>
      ) : null}
      {mode === "confirm" ? (
        <ConfirmForm evidence={evidence} installmentId={target} onDone={reset} onCancel={reset} />
      ) : null}
      {mode === "other" ? (
        <OtherInstallmentPicker
          evidence={evidence}
          onCancel={reset}
          onPick={(id) => {
            setTarget(id);
            setMode("confirm");
          }}
        />
      ) : null}
      {mode === "reject" ? <RejectForm evidence={evidence} onDone={reset} onCancel={reset} /> : null}
      {!open && evidence.decided_at ? (
        <p className="text-caption text-ink-3">
          {t("collections:evidence.decidedAt", { date: formatDateTime(evidence.decided_at) })}
        </p>
      ) : null}
    </Card>
  );
}
