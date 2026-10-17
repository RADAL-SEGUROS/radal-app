/** Shared cadence copy helper for the Cobranza settings tab and its template editor. */
export type TFn = (key: string, options?: Record<string, unknown>) => string;

/** "15 días antes" / "Día del vencimiento" / "3 días después" — explicit keys, plural-aware. */
export function cadenceLabel(t: TFn, day: number): string {
  if (day === 0) return t("settings:collectionsTab.cadence.dayOf");
  if (day < 0) return t("settings:collectionsTab.cadence.dayBefore", { count: -day });
  return t("settings:collectionsTab.cadence.dayAfter", { count: day });
}
