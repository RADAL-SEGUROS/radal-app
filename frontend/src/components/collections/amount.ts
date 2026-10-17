/**
 * Money typed by a Chilean broker, parsed BY CURRENCY.
 *
 * es-CL writes thousands with dots and decimals with a comma: "150.000" is
 * ciento cincuenta mil pesos, never 150. A currency-blind `Number()` books that
 * payment 1000x too small, so:
 *
 * - **CLP** is an integer: dots grouped in threes are thousands
 *   ("1.234.567" -> 1234567), a trailing ",00" is tolerated, any other comma
 *   is a decimal and rejected.
 * - **UF / USD** carry decimals: "1.234,56" (es) and "1,234.56" (en) both
 *   parse; a lone comma is the decimal ("12,5"). A single dot followed by
 *   exactly three digits ("12.345") is ambiguous — 12,345 or 12.345? — and is
 *   rejected rather than guessed. Two or more dot groups ("1.234.567") are
 *   unambiguously thousands.
 */
import type { PaymentCurrencyCode } from "@/api/collections";

export type AmountError = "empty" | "invalid" | "clpInteger" | "ambiguous";
export type AmountParse = { value: number; error: null } | { value: null; error: AmountError };

const GROUPED = /^\d{1,3}(\.\d{3})+$/;
const SINGLE_GROUP = /^\d{1,3}\.\d{3}$/;

const ok = (value: number): AmountParse =>
  Number.isFinite(value) ? { value, error: null } : { value: null, error: "invalid" };
const fail = (error: AmountError): AmountParse => ({ value: null, error });

export function parseAmount(raw: string, currency: PaymentCurrencyCode | string): AmountParse {
  const v = raw.trim().replace(/[\s$]/g, "").replace(/^(US|UF)/i, "");
  if (!v) return fail("empty");
  if (!/^[\d.,]+$/.test(v)) return fail("invalid");

  if (currency === "CLP") {
    const s = v.replace(/,0+$/, "");
    if (s.includes(",")) return fail("clpInteger");
    if (GROUPED.test(s)) return ok(Number(s.replace(/\./g, "")));
    if ((s.match(/\./g) ?? []).length > 1) return fail("invalid");
    const n = Number(s);
    if (!Number.isFinite(n)) return fail("invalid");
    return Number.isInteger(n) ? ok(n) : fail("clpInteger");
  }

  const hasComma = v.includes(",");
  const hasDot = v.includes(".");
  if (hasComma && hasDot) {
    // Whichever separator comes last is the decimal one.
    if (v.lastIndexOf(",") > v.lastIndexOf(".")) {
      const [int, dec, ...rest] = v.split(",");
      if (rest.length || !GROUPED.test(int)) return fail("invalid");
      return ok(Number(`${int.replace(/\./g, "")}.${dec}`));
    }
    const [int, dec, ...rest] = v.split(".");
    if (rest.length || !/^\d{1,3}(,\d{3})+$/.test(int)) return fail("invalid");
    return ok(Number(`${int.replace(/,/g, "")}.${dec}`));
  }
  if (hasComma) {
    const parts = v.split(",");
    if (parts.length !== 2) return fail("invalid");
    return ok(Number(`${parts[0]}.${parts[1]}`));
  }
  if (SINGLE_GROUP.test(v)) return fail("ambiguous");
  if (GROUPED.test(v)) return ok(Number(v.replace(/\./g, "")));
  if ((v.match(/\./g) ?? []).length > 1) return fail("invalid");
  return ok(Number(v));
}

/** A server decimal ("12.3450") as the broker would type it ("12,345"). */
export function amountInput(value: string | number | null | undefined): string {
  const n = typeof value === "string" ? Number(value) : value;
  if (n === null || n === undefined || !Number.isFinite(n) || n === 0) return "";
  return String(n).replace(".", ",");
}

/**
 * A server decimal pre-filled into an amount input IN ITS CURRENCY, the way a
 * Chilean broker would type it and the way `parseAmount` reads it back:
 *
 * - CLP: an integer with thousand dots — `72375` -> `"72.375"`;
 * - UF / USD: rounded to 2 decimals with a decimal comma — `24.9977` ->
 *   `"25"`, `3.8` -> `"3,8"`; from 1 000 up the grouping always carries the
 *   decimals (`"1.234,50"`) so it never reads as the ambiguous `"1.234"`.
 *
 * Rounding UF to cents moves at most 0,005 UF — inside the server's 0,009 UF
 * settlement tolerance, so a pre-filled balance still closes the cuota.
 */
export function amountInputFor(
  value: string | number | null | undefined,
  currency: PaymentCurrencyCode | string | null | undefined,
): string {
  const n = typeof value === "string" ? Number(value) : value;
  if (n === null || n === undefined || !Number.isFinite(n) || n <= 0) return "";
  if (currency === "CLP") {
    return new Intl.NumberFormat("es-CL", { maximumFractionDigits: 0, useGrouping: true }).format(Math.round(n));
  }
  const cents = Math.round(n * 100) / 100;
  if (cents >= 1000) {
    return new Intl.NumberFormat("es-CL", {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
      useGrouping: true,
    }).format(cents);
  }
  return String(cents).replace(".", ",");
}

/** UF the settlement treats as "the same" (server tolerance, `ledger.py`). */
export const UF_TOLERANCE = 0.009;

/** The i18n key of a parse error (explicit map — never a dynamic key). */
export const AMOUNT_ERROR_KEY: Record<AmountError, string> = {
  empty: "collections:form.required",
  invalid: "collections:form.positiveAmount",
  clpInteger: "collections:form.clpInteger",
  ambiguous: "collections:form.ambiguousAmount",
};
