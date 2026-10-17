/**
 * Money and percentages typed into the policy forms (lane F1).
 *
 * One parser for the whole app: `components/collections/amount.ts::parseAmount`
 * (es-CL writes thousands with dots, decimals with a comma). A currency-blind
 * "drop the dots only when there is a comma" rule turned "150.000" into 150 —
 * a premium booked 1000x too small. Here a value is parsed BY CURRENCY, an
 * ambiguous "12.345" is rejected with a reason instead of guessed, and what
 * reaches the API is a plain decimal string (never a float, never "1e-7").
 */
import { parseAmount, type AmountError } from "@/components/collections/amount";

export type DecimalInput =
  | { value: string | null; number: number | null; error: null }
  | { value: null; number: null; error: Exclude<AmountError, "empty"> };

/** Up to 4 decimals, no trailing zeros, no exponent. */
function toDecimalString(n: number): string {
  const fixed = n.toFixed(4);
  return fixed.includes(".") ? fixed.replace(/\.?0+$/, "") : fixed;
}

/**
 * Parse one typed amount. Empty input is valid and yields `value: null`
 * (the field is optional); anything else must parse for `currency`
 * (`UF` / `USD` carry decimals; `CLP` is an integer).
 */
export function parseDecimalInput(
  raw: string | null | undefined,
  currency: string | null | undefined = "UF",
): DecimalInput {
  const text = (raw ?? "").trim();
  if (!text) return { value: null, number: null, error: null };
  const parsed = parseAmount(text, currency ?? "UF");
  if (parsed.error === "empty") return { value: null, number: null, error: null };
  if (parsed.error !== null) return { value: null, number: null, error: parsed.error };
  return { value: toDecimalString(parsed.value), number: parsed.value, error: null };
}

/** The policies i18n key for a parse error — explicit map, never a dynamic key. */
export const DECIMAL_ERROR_KEY = {
  invalid: "policies:form.errors.number",
  ambiguous: "policies:form.errors.ambiguous",
  clpInteger: "policies:form.errors.number",
} as const satisfies Record<Exclude<AmountError, "empty">, string>;
