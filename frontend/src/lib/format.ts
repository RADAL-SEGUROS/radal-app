import { differenceInCalendarDays, format, parseISO } from "date-fns";
import { es as esLocale, enUS as enLocale } from "date-fns/locale";
import i18n from "@/i18n";

function dateLocale() {
  return i18n.language?.startsWith("en") ? enLocale : esLocale;
}

/** Format a UF numeric value with Chilean grouping (e.g. 5.440,50) + "UF" prefix. */
export function formatUF(
  value: number | null | undefined,
  opts: { withSymbol?: boolean; decimals?: number } = {},
): string {
  const { withSymbol = true, decimals = 2 } = opts;
  if (value === null || value === undefined || Number.isNaN(value)) {
    return withSymbol ? "UF —" : "—";
  }
  const formatted = new Intl.NumberFormat("es-CL", {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  }).format(value);
  return withSymbol ? `UF ${formatted}` : formatted;
}

/** Plain number with Chilean grouping, no decimals by default. */
export function formatNumber(
  value: number | null | undefined,
  decimals = 0,
): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return new Intl.NumberFormat("es-CL", {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  }).format(value);
}

/** Percentage 0-100 -> "100,0%". */
export function formatPct(
  value: number | null | undefined,
  decimals = 1,
): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return `${new Intl.NumberFormat("es-CL", {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  }).format(value)}%`;
}

function toDate(value: string | Date | null | undefined): Date | null {
  if (!value) return null;
  const d = typeof value === "string" ? parseISO(value) : value;
  return Number.isNaN(d.getTime()) ? null : d;
}

/** ISO date -> "10 ene 2024" (localized). */
export function formatDate(
  value: string | Date | null | undefined,
  pattern = "d MMM yyyy",
): string {
  const d = toDate(value);
  if (!d) return "—";
  return format(d, pattern, { locale: dateLocale() });
}

/** ISO datetime -> "10 ene 2024, 14:02". */
export function formatDateTime(value: string | Date | null | undefined): string {
  const d = toDate(value);
  if (!d) return "—";
  return format(d, "d MMM yyyy, HH:mm", { locale: dateLocale() });
}

/** Localized weekday e.g. "miércoles". */
export function weekday(value: string | Date = new Date()): string {
  const d = toDate(value) ?? new Date();
  return format(d, "EEEE", { locale: dateLocale() });
}

/** Capitalized "weekday, d month" e.g. "Miércoles, 8 de julio" / "Wednesday, July 8". */
export function weekdayLongDate(value: string | Date = new Date()): string {
  const d = toDate(value) ?? new Date();
  const isEs = !i18n.language?.startsWith("en");
  const pattern = isEs ? "EEEE, d 'de' MMMM" : "EEEE, MMMM d";
  const s = format(d, pattern, { locale: dateLocale() });
  return s.charAt(0).toUpperCase() + s.slice(1);
}

/** Time-of-day period for greeting. */
export function greetingPeriod(now: Date = new Date()): "manana" | "tarde" | "noche" {
  const h = now.getHours();
  if (h < 12) return "manana";
  if (h < 20) return "tarde";
  return "noche";
}

/** Whole days from today until a target date (positive = future). */
export function diasRestantes(
  value: string | Date | null | undefined,
  from: Date = new Date(),
): number | null {
  const d = toDate(value);
  if (!d) return null;
  return differenceInCalendarDays(d, from);
}

export type DiasColor = "rojo" | "ambar" | "gris";

/** Renovaciones rule: ámbar if <=60, else gris. */
export function diasColorRenovacion(dias: number | null): DiasColor {
  if (dias === null) return "gris";
  return dias <= 60 ? "ambar" : "gris";
}

/** Cotizaciones rule: rojo if <=4, else ámbar. */
export function diasColorCotizacion(dias: number | null): DiasColor {
  if (dias === null) return "ambar";
  return dias <= 4 ? "rojo" : "ambar";
}

export type CoberturaEstado = "infravalorada" | "optima" | "sobrevalorada";

/** Coverage analyzer: <95 infra, 95-105 óptima, >105 sobre. */
export function coberturaEstado(pct: number | null | undefined): CoberturaEstado {
  if (pct === null || pct === undefined) return "optima";
  if (pct < 95) return "infravalorada";
  if (pct > 105) return "sobrevalorada";
  return "optima";
}
