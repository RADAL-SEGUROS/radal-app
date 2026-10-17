/**
 * Small pieces every Cobranza surface shares: the state + flags badges, the
 * Santiago business date, a labelled form field and the "pronto" chip.
 */
import * as React from "react";
import { useTranslation } from "react-i18next";
import { Badge } from "@/components/ui/badge";
import { Label } from "@/components/ui/label";
import { cn } from "@/lib/utils";
import { formatNumber } from "@/lib/format";
import { uf } from "@/components/common/kit";
import {
  DEBIT_REJECTED_FLAG_LABEL,
  FLAG_LABEL,
  FLAG_TONE,
  STATE_LABEL,
  STATE_TONE,
  labelOf,
  toneOf,
} from "@/components/collections/labels";

/** Today as `YYYY-MM-DD` in America/Santiago — business dates are Chilean. */
export function todaySantiago(): string {
  return new Date().toLocaleDateString("en-CA", { timeZone: "America/Santiago" });
}

/** "$ 1.234.567" — CLP is always an integer. */
export function clp(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return `$ ${formatNumber(Math.round(value))}`;
}

/** A cuota amount in its own currency (UF, or USD for dollar policies). */
export function money(value: string | number | null | undefined, currency: string | null | undefined): string {
  if (currency === "USD") {
    const n = typeof value === "string" ? Number(value) : value;
    if (n === null || n === undefined || Number.isNaN(n)) return "US$ —";
    return `US$ ${formatNumber(n, 2)}`;
  }
  if (currency === "CLP") {
    const n = typeof value === "string" ? Number(value) : value;
    return clp(n ?? null);
  }
  return uf(value ?? null);
}

export type DebitKind = "pac" | "pat";

/**
 * PAC (bank account) or PAT (card): the server's `direct_debit_kind` when it
 * sends one, else the cuota's method. `null` = neither is known.
 */
export function debitKindOf(row: {
  direct_debit_kind?: string | null;
  payment_mode?: string | null;
}): DebitKind | null {
  if (row.direct_debit_kind === "pac" || row.direct_debit_kind === "pat") return row.direct_debit_kind;
  if (row.payment_mode === "direct_debit") return "pac";
  if (row.payment_mode === "card_debit") return "pat";
  return null;
}

/** The computed state badge, then the marks that never change the state. */
export function InstallmentStateBadges({
  state,
  flags,
  daysLate,
  debitKind,
  className,
}: {
  state: string;
  flags: string[];
  daysLate?: number;
  /** Names the rejected charge (PAC vs PAT); omitted = a neutral label. */
  debitKind?: DebitKind | null;
  className?: string;
}) {
  const { t } = useTranslation("collections");
  return (
    <div className={cn("flex flex-wrap items-center gap-1", className)}>
      <Badge variant={toneOf(STATE_TONE, state)} dot className="whitespace-nowrap">
        {labelOf(t, STATE_LABEL, state, t("state.unknown"))}
        {daysLate && daysLate > 0 ? (
          <span className="tabular-nums opacity-80">· {t("board.daysLate", { count: daysLate })}</span>
        ) : null}
      </Badge>
      {flags
        .filter((flag) => Object.prototype.hasOwnProperty.call(FLAG_LABEL, flag))
        .map((flag) => (
          <Badge key={flag} variant={toneOf(FLAG_TONE, flag)} className="whitespace-nowrap px-1.5 py-0 text-[11px]">
            {flag === "direct_debit_rejected"
              ? t(DEBIT_REJECTED_FLAG_LABEL[debitKind ?? "generic"])
              : labelOf(t, FLAG_LABEL, flag)}
          </Badge>
        ))}
    </div>
  );
}

/** Label + control + error/hint, the unit every lane form is built from. */
export function Field({
  label,
  htmlFor,
  error,
  hint,
  children,
  className,
}: {
  label: React.ReactNode;
  htmlFor?: string;
  error?: string | null;
  hint?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("flex min-w-0 flex-col gap-1.5", className)}>
      <Label htmlFor={htmlFor} className="text-caption font-medium text-ink-2">
        {label}
      </Label>
      {children}
      {error ? (
        <p role="alert" className="text-caption text-neg-text">
          {error}
        </p>
      ) : hint ? (
        <p className="text-caption text-ink-3">{hint}</p>
      ) : null}
    </div>
  );
}

/** The quiet "pronto" chip that rides on a visibly disabled option. */
export function SoonChip() {
  const { t } = useTranslation("common");
  return (
    <Badge variant="muted" className="px-1.5 py-0 text-[11px]">
      {t("nav.soon")}
    </Badge>
  );
}

/** A native `<select>` styled like the Signal input — small forms, touch-friendly. */
export const NativeSelect = React.forwardRef<
  HTMLSelectElement,
  React.SelectHTMLAttributes<HTMLSelectElement>
>(({ className, children, ...props }, ref) => (
  <select
    ref={ref}
    className={cn(
      "flex h-9 w-full rounded-sm border border-line bg-bone px-3 text-base text-ink transition-[border-color,box-shadow] duration-150 ease-out hover:border-line-strong focus-visible:border-brand focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-ring disabled:cursor-not-allowed disabled:opacity-50 sm:text-body",
      className,
    )}
    {...props}
  >
    {children}
  </select>
));
NativeSelect.displayName = "NativeSelect";
