/**
 * "Completar" dialogs of the policy ficha (team R19: filling an EMPTY field
 * needs no reason; `PATCH /policies/{id}`), plus "Cargar plan de pago"
 * (`POST /collections/plans`, Σ cuotas = prima bruta, checked again server-side).
 *
 * Every server refusal is shown verbatim (Spanish `detail`) — nothing is
 * corrected silently on the client.
 */
import * as React from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { addMonths, format, parseISO } from "date-fns";
import { Loader2, Plus, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { ErrorBanner, apiError } from "@/components/common/kit";
import { cn } from "@/lib/utils";
import { formatDate } from "@/lib/format";
import { useCan } from "@/lib/permissions";
import { Checkbox } from "@/components/ui/checkbox";
import {
  PAYMENT_METHODS,
  useCreatePolicyPlan,
  useUpdatePolicy,
  type PaymentMethod,
  type PolicyDetail,
  type PolicyUpdateV12,
} from "@/api/policies";
import { PAYMENT_METHOD_LABEL } from "@/components/policies/policyLabels";
import { money } from "@/components/policies/PolicyCells";
import { DECIMAL_ERROR_KEY, parseDecimalInput } from "@/components/policies/money";
import { amountInput } from "@/components/collections/amount";

/** A typed amount/percent → its decimal string, or an error code (shared es-CL parser). */
function parseField(raw: string | undefined, currency: string): ReturnType<typeof parseDecimalInput> {
  return parseDecimalInput(raw, currency);
}

// =============================================================================
// Completar — a small form of the EMPTY fields for one missing key
// =============================================================================

export type CompleteTarget = "policy_number" | "premiums" | "commission";

type FieldKey = "policy_number" | "taxable_premium_uf" | "exempt_premium_uf" | "total_premium_uf" | "commission_pct";

const FIELD_LABEL = {
  policy_number: "policies:complete.fields.policy_number",
  taxable_premium_uf: "policies:complete.fields.taxable_premium_uf",
  exempt_premium_uf: "policies:complete.fields.exempt_premium_uf",
  total_premium_uf: "policies:complete.fields.total_premium_uf",
  commission_pct: "policies:complete.fields.commission_pct",
} as const satisfies Record<FieldKey, string>;

const TARGET_TITLE = {
  policy_number: "policies:complete.title.policy_number",
  premiums: "policies:complete.title.premiums",
  commission: "policies:complete.title.commission",
} as const satisfies Record<CompleteTarget, string>;

function emptyFields(policy: PolicyDetail, target: CompleteTarget): FieldKey[] {
  if (target === "policy_number") return ["policy_number"];
  if (target === "commission") return ["commission_pct"];
  const candidates: FieldKey[] = ["taxable_premium_uf", "exempt_premium_uf", "total_premium_uf"];
  const empty = candidates.filter((key) => policy[key as keyof PolicyDetail] == null);
  return empty.length > 0 ? empty : candidates;
}

export function CompleteFieldDialog({
  policy,
  target,
  open,
  onOpenChange,
}: {
  policy: PolicyDetail;
  target: CompleteTarget | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { t } = useTranslation("policies");
  const update = useUpdatePolicy(policy.id);
  const fields = target ? emptyFields(policy, target) : [];
  const [values, setValues] = React.useState<Record<string, string>>({});
  const [error, setError] = React.useState<unknown>(null);

  React.useEffect(() => {
    if (open) {
      setValues({});
      setError(null);
    }
  }, [open, target]);

  // Premiums are in the policy's currency; a percentage carries decimals like UF.
  const currencyOf = (key: FieldKey) => (key === "commission_pct" ? "UF" : policy.currency);
  const fieldError = (key: FieldKey) =>
    key === "policy_number" ? null : parseField(values[key], currencyOf(key)).error;
  const pctTooHigh =
    fields.includes("commission_pct") &&
    (parseField(values.commission_pct, "UF").number ?? 0) > 100;
  const errorKey = fields.map(fieldError).find((e) => e !== null) ?? null;
  const invalid = errorKey !== null || pctTooHigh;
  const filled = fields.some((key) => (values[key] ?? "").trim() !== "");

  const submit = async () => {
    const payload: PolicyUpdateV12 = {};
    for (const key of fields) {
      const raw = (values[key] ?? "").trim();
      if (!raw) continue;
      (payload as Record<string, string | null>)[key] =
        key === "policy_number" ? raw : parseField(raw, currencyOf(key)).value;
    }
    try {
      await update.mutateAsync(payload);
      toast.success(t("complete.toast"));
      onOpenChange(false);
    } catch (e) {
      setError(e);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{target ? t(TARGET_TITLE[target]) : t("complete.action")}</DialogTitle>
          <DialogDescription>
            {target === "premiums" ? t("complete.premiumsHint") : t("complete.hint")}
          </DialogDescription>
        </DialogHeader>
        <div className="flex flex-col gap-3">
          {fields.map((key) => (
            <div key={key} className="flex flex-col gap-1.5">
              <Label htmlFor={`complete-${key}`}>{t(FIELD_LABEL[key])}</Label>
              <Input
                id={`complete-${key}`}
                className="h-10 tabular-nums"
                inputMode={key === "policy_number" ? "text" : "decimal"}
                value={values[key] ?? ""}
                onChange={(e) => setValues((prev) => ({ ...prev, [key]: e.target.value }))}
              />
            </div>
          ))}
          {errorKey ? (
            <p role="alert" className="text-caption text-neg-text">
              {t(DECIMAL_ERROR_KEY[errorKey])}
            </p>
          ) : pctTooHigh ? (
            <p role="alert" className="text-caption text-neg-text">
              {t("form.errors.pct")}
            </p>
          ) : null}
          <ErrorBanner error={error} />
        </div>
        <DialogFooter>
          <Button variant="secondary" onClick={() => onOpenChange(false)}>
            {t("form.cancel")}
          </Button>
          <Button disabled={!filled || invalid || update.isPending} onClick={() => void submit()}>
            {update.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
            {t("complete.save")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// =============================================================================
// Cargar plan de pago
// =============================================================================

interface DraftRow {
  due_date: string;
  amount: string;
}

/** Decimals a cuota is proposed with: UF/USD, in cents of the unit. */
function planDecimals(_currency: string): number {
  return 2;
}

/** Today as `YYYY-MM-DD` in America/Santiago — business dates are Chilean. */
function todayISO(): string {
  return new Date().toLocaleDateString("en-CA", { timeZone: "America/Santiago" });
}

/** Split `gross` into `n` cuotas in whole cents (UF 2 dp, CLP 0 dp) — no float
 *  drift, the last cuota absorbing the remainder so Σ stays on the gross (the
 *  server tolerates 0,01 UF). They used to be proposed with 4 decimals
 *  (`9,9166`), which nobody types and no insurer bills (review v12 #15).
 *  Returned as the broker would type them ("12,35"), so the same parser reads
 *  generated and hand-typed cuotas alike. */
function splitEvenly(gross: string, n: number, currency: string): string[] {
  const decimals = planDecimals(currency);
  const scale = 10 ** decimals;
  const units = Math.round(Number(gross) * scale);
  const base = Math.floor(units / n);
  const out: number[] = Array.from({ length: n }, () => base);
  out[n - 1] = units - base * (n - 1);
  return out.map((u) => amountInput((u / scale).toFixed(decimals)));
}

function sumUnits(rows: DraftRow[], currency: string): number {
  return rows.reduce(
    (acc, row) => acc + Math.round((parseField(row.amount, currency).number ?? 0) * 10_000),
    0,
  );
}

function buildRows(gross: string, n: number, first: string, currency: string): DraftRow[] {
  const amounts = splitEvenly(gross, n, currency);
  const start = first ? parseISO(first) : new Date();
  return amounts.map((amount, i) => ({
    amount,
    due_date: format(addMonths(start, i), "yyyy-MM-dd"),
  }));
}

export function PlanLoadDialog({
  policy,
  open,
  onOpenChange,
}: {
  policy: PolicyDetail;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { t } = useTranslation("policies");
  const create = useCreatePolicyPlan(policy.id);
  const gross = policy.total_premium_uf;
  const [method, setMethod] = React.useState<PaymentMethod>(policy.payment_method ?? "coupon_book");
  const [count, setCount] = React.useState("1");
  const [first, setFirst] = React.useState(policy.start_date ?? format(new Date(), "yyyy-MM-dd"));
  const [rows, setRows] = React.useState<DraftRow[]>([]);
  const [error, setError] = React.useState<unknown>(null);
  const canStart = useCan("Collections", "Edit");
  const [startCollection, setStartCollection] = React.useState(true);

  React.useEffect(() => {
    if (!open || !gross) return;
    setStartCollection(true);
    const start = policy.start_date ?? format(new Date(), "yyyy-MM-dd");
    setMethod(policy.payment_method ?? "coupon_book");
    setCount("1");
    setFirst(start);
    setRows(buildRows(gross, 1, start, policy.currency));
    setError(null);
  }, [open, gross, policy.payment_method, policy.start_date, policy.currency]);

  const regenerate = (nextCount: string, nextFirst: string) => {
    const n = Math.min(60, Math.max(1, Math.floor(Number(nextCount) || 1)));
    if (gross) setRows(buildRows(gross, n, nextFirst, policy.currency));
  };

  const grossUnits = gross ? Math.round(Number(gross) * 10_000) : 0;
  const diffUnits = sumUnits(rows, policy.currency) - grossUnits;
  // Server tolerance is 0.01 UF.
  const balanced = Math.abs(diffUnits) <= 100;
  const rowErrors = rows.map((row) => parseField(row.amount, policy.currency));
  const rowsValid = rows.every(
    (row, i) => !!row.due_date && rowErrors[i].error === null && (rowErrors[i].number ?? 0) > 0,
  );
  const firstRowError = rowErrors.find((r) => r.error !== null)?.error ?? null;
  // Business dates are Chilean: "today" is today in America/Santiago.
  const today = todayISO();
  const pastRows = rows.filter((row) => !!row.due_date && row.due_date < today);
  const unit = policy.currency || "UF";
  const startAllowed = canStart.allowed && !canStart.isLoading;
  const willStart = startAllowed && startCollection;

  const submit = async () => {
    try {
      const created = await create.mutateAsync({
        policy_id: policy.id,
        payment_mode: method,
        installment_count: rows.length,
        source: "manual",
        origin: "policy",
        start_collection: willStart,
        installments: rows.map((row, i) => ({
          number: i + 1,
          due_date: row.due_date,
          gross_amount_uf: parseField(row.amount, policy.currency).value as string,
        })),
      });
      toast.success(willStart ? t("plan.toastStarted") : t("plan.toastNotStarted"));
      // Non-blocking advice from the server (e.g. cuotas born overdue), verbatim.
      for (const warning of created.warnings ?? []) toast.warning(warning);
      onOpenChange(false);
    } catch (e) {
      setError(e);
      toast.error(apiError(e, t("plan.error")));
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{t("plan.title")}</DialogTitle>
          <DialogDescription>
            {t("plan.subtitle", { gross: money(gross, policy.currency) })}
          </DialogDescription>
        </DialogHeader>

        <div className="flex flex-col gap-3">
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            <div className="flex flex-col gap-1.5">
              <Label>{t("plan.method")}</Label>
              <Select value={method} onValueChange={(v) => setMethod(v as PaymentMethod)}>
                <SelectTrigger className="h-10">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {PAYMENT_METHODS.map((m) => (
                    <SelectItem key={m} value={m}>
                      {t(PAYMENT_METHOD_LABEL[m])}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="plan-count">{t("plan.count")}</Label>
              <Input
                id="plan-count"
                type="number"
                min={1}
                max={60}
                className="h-10 tabular-nums"
                value={count}
                onChange={(e) => {
                  setCount(e.target.value);
                  regenerate(e.target.value, first);
                }}
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="plan-first">{t("plan.first")}</Label>
              <Input
                id="plan-first"
                type="date"
                className="h-10"
                value={first}
                onChange={(e) => {
                  setFirst(e.target.value);
                  regenerate(count, e.target.value);
                }}
              />
            </div>
          </div>

          <div className="overflow-x-auto rounded-lg border border-line">
            <table className="w-full min-w-[320px] text-body">
              <thead>
                <tr className="border-b border-line text-left text-caption text-ink-3">
                  <th className="px-3 py-2 font-medium">{t("plan.columns.number")}</th>
                  <th className="px-3 py-2 font-medium">{t("plan.columns.due")}</th>
                  <th className="px-3 py-2 font-medium">
                    {t("plan.columns.amountUnit", { unit })}
                  </th>
                  <th className="w-10 px-1 py-2" />
                </tr>
              </thead>
              <tbody>
                {rows.map((row, i) => (
                  <tr key={i} className="border-b border-line last:border-b-0">
                    <td className="px-3 py-1.5 tabular-nums text-ink-3">{i + 1}</td>
                    <td className="px-2 py-1.5">
                      <Input
                        type="date"
                        className={cn(
                          "h-9",
                          row.due_date && row.due_date < today && "border-warn text-warn-text",
                        )}
                        aria-label={t("plan.columns.due")}
                        aria-invalid={row.due_date && row.due_date < today ? true : undefined}
                        value={row.due_date}
                        onChange={(e) =>
                          setRows((prev) =>
                            prev.map((r, j) => (j === i ? { ...r, due_date: e.target.value } : r)),
                          )
                        }
                      />
                    </td>
                    <td className="px-2 py-1.5">
                      <div className="relative">
                        <span
                          className="pointer-events-none absolute inset-y-0 left-2.5 flex items-center text-caption text-ink-3"
                          aria-hidden
                        >
                          {unit}
                        </span>
                        <Input
                          inputMode="decimal"
                          className="h-9 pl-9 text-right tabular-nums"
                          aria-label={t("plan.columns.amountUnit", { unit })}
                          value={row.amount}
                          onChange={(e) =>
                            setRows((prev) =>
                              prev.map((r, j) => (j === i ? { ...r, amount: e.target.value } : r)),
                            )
                          }
                        />
                      </div>
                    </td>
                    <td className="px-1 py-1.5">
                      <Button
                        variant="ghost"
                        size="icon"
                        className="h-9 w-9"
                        aria-label={t("plan.remove")}
                        disabled={rows.length <= 1}
                        onClick={() => setRows((prev) => prev.filter((_, j) => j !== i))}
                      >
                        <Trash2 className="h-4 w-4" />
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Button
            variant="ghost"
            size="sm"
            className="self-start"
            onClick={() =>
              setRows((prev) => {
                const last = prev[prev.length - 1];
                const due = last?.due_date
                  ? format(addMonths(parseISO(last.due_date), 1), "yyyy-MM-dd")
                  : first;
                return [...prev, { due_date: due, amount: "" }];
              })
            }
          >
            <Plus className="h-4 w-4" />
            {t("plan.add")}
          </Button>

          <div
            className={cn(
              "rounded-lg px-3 py-2 text-caption tabular-nums",
              balanced ? "bg-pos-soft text-pos-text" : "bg-warn-soft text-warn-text",
            )}
          >
            {balanced
              ? t("plan.balanced")
              : t("plan.unbalanced", { diff: money((diffUnits / 10_000).toFixed(4), policy.currency) })}
          </div>
          {pastRows.length > 0 ? (
            <p role="status" className="rounded-lg bg-warn-soft px-3 py-2 text-caption text-warn-text">
              {t("plan.pastDue", { count: pastRows.length, today: formatDate(today) })}
            </p>
          ) : null}

          <div className="flex items-start gap-2.5 rounded-lg border border-line px-3 py-2.5">
            <Checkbox
              id="plan-start"
              className="mt-0.5"
              checked={willStart}
              disabled={!startAllowed}
              onCheckedChange={(v) => setStartCollection(v === true)}
            />
            <div className="min-w-0">
              <Label htmlFor="plan-start" className="text-body text-ink">
                {t("plan.startCollection")}
              </Label>
              <p className="text-caption text-ink-3">
                {!startAllowed
                  ? t("plan.startNoPermission")
                  : willStart
                    ? t("plan.startHint")
                    : t("plan.notStartedHint")}
              </p>
            </div>
          </div>

          {firstRowError ? (
            <p role="alert" className="text-caption text-neg-text">
              {t(DECIMAL_ERROR_KEY[firstRowError])}
            </p>
          ) : null}
          <ErrorBanner error={error} />
        </div>

        <DialogFooter>
          <Button variant="secondary" onClick={() => onOpenChange(false)}>
            {t("form.cancel")}
          </Button>
          <Button
            disabled={!gross || !balanced || !rowsValid || create.isPending}
            onClick={() => void submit()}
          >
            {create.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
            {t("plan.save")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
