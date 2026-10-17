/**
 * C1.2–C1.3 · Registrar pago. Proposes the open balance; the broker enters the
 * date, the method (the cuota's own by default) and, optionally, a comprobante.
 * "Confirmado por la compañía" when they already saw it on the insurer's portal.
 *
 * The server decides Pagada vs Parcial (tolerance 0,009 UF; CLP/USD at the
 * day's rate) and answers 422 with a Spanish reason on a paid/voided cuota, a
 * future date or a missing FX rate — shown verbatim.
 */
import * as React from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { Loader2, Paperclip } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { DisabledHint, ErrorBanner } from "@/components/common/kit";
import { useCan } from "@/lib/permissions";
import { PAYMENT_MODES } from "@/api/types";
import {
  useRegisterInstallmentPayment,
  useUploadPaymentReceipt,
  type BoardInstallmentRow,
  type PaymentCurrencyCode,
} from "@/api/collections";
import { METHOD_LABEL, labelOf } from "@/components/collections/labels";
import { Field, NativeSelect, clp, money, todaySantiago } from "@/components/collections/shared";
import { AMOUNT_ERROR_KEY, UF_TOLERANCE, amountInputFor, parseAmount } from "@/components/collections/amount";

const CURRENCIES: PaymentCurrencyCode[] = ["UF", "CLP", "USD"];

export function RegisterPaymentForm({
  row,
  ufRate,
  onDone,
  onCancel,
}: {
  row: BoardInstallmentRow;
  ufRate: number | null;
  onDone: () => void;
  onCancel: () => void;
}) {
  const { t } = useTranslation(["collections", "common"]);
  const register = useRegisterInstallmentPayment(row.id);
  const upload = useUploadPaymentReceipt();
  const canUpload = useCan("Documents", "Upload");
  const [file, setFile] = React.useState<File | null>(null);
  const today = todaySantiago();
  const balance = Number(row.balance_uf);
  const cuotaCurrency: PaymentCurrencyCode = row.currency === "USD" ? "USD" : "UF";
  const paidSoFar = Number(row.paid_uf);

  const schema = React.useMemo(
    () =>
      z
        .object({
          amount: z.string().min(1, t("collections:form.required")),
          currency: z.enum(["UF", "CLP", "USD"]),
          paid_on: z
            .string()
            .min(1, t("collections:form.required"))
            .refine((v) => v <= today, t("collections:form.noFutureDate")),
          method: z.string(),
          confirmed_by_insurer: z.boolean(),
          note: z.string().max(4000),
        })
        .superRefine((values, ctx) => {
          // Parsed BY CURRENCY: "150.000" in CLP is 150 mil pesos, not 150.
          const parsed = parseAmount(values.amount, values.currency);
          if (parsed.error !== null) {
            ctx.addIssue({ code: "custom", path: ["amount"], message: t(AMOUNT_ERROR_KEY[parsed.error]) });
          } else if (parsed.value <= 0) {
            ctx.addIssue({ code: "custom", path: ["amount"], message: t("collections:form.positiveAmount") });
          } else if (
            values.currency === cuotaCurrency &&
            Number.isFinite(balance) &&
            parsed.value > balance + UF_TOLERANCE
          ) {
            // The server refuses it too (409) — say it before the round trip.
            ctx.addIssue({
              code: "custom",
              path: ["amount"],
              message: t("collections:payment.overBalance", { balance: money(balance, cuotaCurrency) }),
            });
          }
        }),
    [t, today, balance, cuotaCurrency],
  );
  type Values = z.infer<typeof schema>;

  const defaultCurrency = cuotaCurrency;
  /** The balance in the chosen currency — CLP from the server's own conversion. */
  const balanceFor = (cur: PaymentCurrencyCode): string =>
    cur === cuotaCurrency
      ? amountInputFor(row.balance_uf, cur)
      : cur === "CLP" && row.balance_clp !== null
        ? amountInputFor(row.balance_clp, "CLP")
        : "";
  const form = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: {
      amount: balanceFor(defaultCurrency),
      currency: defaultCurrency,
      paid_on: today,
      method: row.payment_mode ?? "",
      confirmed_by_insurer: false,
      note: "",
    },
  });
  const { errors } = form.formState;
  const currency = form.watch("currency");
  const amountRaw = form.watch("amount");
  const parsed = parseAmount(amountRaw || "", currency);
  const amount = parsed.value ?? NaN;
  const confirmed = form.watch("confirmed_by_insurer");

  const pending = register.isPending || upload.isPending;

  const onSubmit = form.handleSubmit(async (values) => {
    try {
      let receiptId: number | null = null;
      if (file) {
        const doc = await upload.mutateAsync({ file, planId: row.plan_id });
        receiptId = doc.id;
      }
      const value = parseAmount(values.amount, values.currency).value;
      if (value === null) return; // unreachable — the schema already rejected it
      const result = await register.mutateAsync({
        amount: String(value),
        currency: values.currency,
        paid_on: values.paid_on,
        method: (values.method || null) as BoardInstallmentRow["payment_mode"],
        receipt_document_id: receiptId,
        confirmed_by_insurer: values.confirmed_by_insurer,
        note: values.note.trim() || null,
      });
      toast.success(
        result.state.state === "paid"
          ? t("collections:payment.savedPaid")
          : t("collections:payment.savedPartial"),
      );
      onDone();
    } catch {
      // Shown inline by <ErrorBanner/> — the server's Spanish reason, verbatim.
    }
  });

  const currencyField = form.register("currency");

  return (
    <form onSubmit={(e) => void onSubmit(e)} className="flex flex-col gap-4" noValidate>
      {/* What is still owed, before a figure is typed: a partial payment on top
          of an existing one must never push the cuota over its gross. */}
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1 rounded-lg bg-paper-2 px-3 py-2.5 text-caption tabular-nums">
        <span className="font-medium text-ink">
          {t("collections:payment.balanceLabel")}: {money(row.balance_uf, row.currency)}
          {row.balance_clp !== null && balance > 0 ? (
            <span className="ml-1.5 font-normal text-ink-3">{clp(row.balance_clp)}</span>
          ) : null}
        </span>
        {paidSoFar > 0 ? (
          <span className="text-ink-3">
            {t("collections:board.paidOf", {
              paid: money(row.paid_uf, row.currency),
              total: money(row.gross_amount_uf, row.currency),
            })}
          </span>
        ) : null}
      </div>
      <div className="grid grid-cols-[1fr_96px] gap-3">
        <Field label={t("collections:payment.amount")} htmlFor="pay-amount" error={errors.amount?.message}>
          <Input id="pay-amount" inputMode="decimal" autoComplete="off" {...form.register("amount")} />
        </Field>
        <Field label={t("collections:payment.currency")} htmlFor="pay-currency">
          <NativeSelect
            id="pay-currency"
            {...currencyField}
            onChange={(e) => {
              void currencyField.onChange(e);
              // An untouched pre-fill follows the currency (UF balance -> its CLP).
              if (!form.formState.dirtyFields.amount) {
                form.setValue("amount", balanceFor(e.target.value as PaymentCurrencyCode));
              }
            }}
          >
            {CURRENCIES.map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </NativeSelect>
        </Field>
      </div>
      {/* Echo what will actually be booked, so a mis-typed figure shows before submit. */}
      {Number.isFinite(amount) && amount > 0 ? (
        <p className="-mt-2 text-caption tabular-nums text-ink-2" aria-live="polite">
          <span className="font-medium text-ink">
            {t("collections:payment.willRegister", { amount: money(amount, currency) })}
          </span>
          {currency === "UF" && ufRate ? (
            <span className="text-ink-3">
              {" "}
              ·{" "}
              {t("collections:payment.clpHint", {
                // The pre-filled (cent-rounded) balance shows the SERVER's CLP
                // of the balance, so the sheet and this form never disagree by pesos.
                amount: clp(
                  row.balance_clp !== null && Math.abs(amount - balance) <= 0.005
                    ? row.balance_clp
                    : amount * ufRate,
                ),
              })}
            </span>
          ) : currency !== "UF" ? (
            <span className="text-ink-3"> · {t("collections:payment.fxHint")}</span>
          ) : null}
        </p>
      ) : currency !== "UF" ? (
        <p className="-mt-2 text-caption text-ink-3">{t("collections:payment.fxHint")}</p>
      ) : null}

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <Field label={t("collections:payment.paidOn")} htmlFor="pay-date" error={errors.paid_on?.message}>
          <Input id="pay-date" type="date" max={today} {...form.register("paid_on")} />
        </Field>
        <Field label={t("collections:payment.method")} htmlFor="pay-method">
          <NativeSelect id="pay-method" {...form.register("method")}>
            <option value="">{t("collections:payment.methodNone")}</option>
            {PAYMENT_MODES.map((m) => (
              <option key={m} value={m}>
                {labelOf(t, METHOD_LABEL, m, m)}
              </option>
            ))}
          </NativeSelect>
        </Field>
      </div>

      <Field
        label={t("collections:payment.receipt")}
        hint={file ? file.name : t("collections:payment.receiptHint")}
      >
        <DisabledHint hint={canUpload.allowed ? null : t("collections:payment.receiptNoPermission")}>
          <label
            className={
              canUpload.allowed
                ? "inline-flex h-9 cursor-pointer items-center gap-2 rounded-sm border border-dashed border-line px-3 text-caption text-ink-2 transition-[border-color,color] duration-150 hover:border-line-strong hover:text-ink"
                : "pointer-events-none inline-flex h-9 items-center gap-2 rounded-sm border border-dashed border-line px-3 text-caption text-ink-3 opacity-50"
            }
          >
            <Paperclip className="h-4 w-4" />
            {file ? t("collections:payment.receiptChange") : t("collections:payment.receiptPick")}
            <input
              type="file"
              className="sr-only"
              accept="application/pdf,image/*"
              disabled={!canUpload.allowed}
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            />
          </label>
        </DisabledHint>
      </Field>

      <label className="flex cursor-pointer items-start gap-2.5 rounded-lg bg-paper-2 px-3 py-2.5">
        <Checkbox
          checked={confirmed}
          onCheckedChange={(v) => form.setValue("confirmed_by_insurer", v === true)}
          className="mt-0.5"
        />
        <span className="text-caption text-ink-2">
          <span className="block font-medium text-ink">{t("collections:payment.confirmedByInsurer")}</span>
          {t("collections:payment.confirmedByInsurerHint")}
        </span>
      </label>

      <Field label={t("collections:payment.note")} htmlFor="pay-note">
        <Textarea id="pay-note" rows={2} {...form.register("note")} />
      </Field>

      <ErrorBanner error={register.error ?? upload.error} />

      <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
        <Button type="button" variant="ghost" onClick={onCancel} disabled={pending}>
          {t("common:actions.cancel")}
        </Button>
        <Button type="submit" disabled={pending}>
          {pending ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
          {/* "Marcar pagada" only when it will: UF covering the whole balance. */}
          {currency === cuotaCurrency && Number.isFinite(amount) && amount >= balance - UF_TOLERANCE
            ? t("collections:payment.submit")
            : t("collections:payment.submitPartial")}
        </Button>
      </div>
    </form>
  );
}
