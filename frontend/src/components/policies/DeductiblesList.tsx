/**
 * The confirmed deductibles as a readable list (review r2 C5):
 * "Incendio: 10 % de la pérdida, mínimo UF 10" — never the old
 * `pct: 10 · text: … · peril: … · min uf: 10` dump.
 *
 * Accepts every shape the policy carries: the `policy.deductibles` JSON column
 * (peril → term, written by the technical-read confirm), a list of rows (the
 * payload's `deductibles`), or plain strings. The literal clause (`text`)
 * wins — it is the insurer's own wording; without it the structured terms are
 * composed through the shared `formatDeductible`.
 */
import * as React from "react";
import { useTranslation } from "react-i18next";

import { formatDeductible, usePerilLabel } from "@/components/common/kit";
import type { DeductibleTerm } from "@/api/types";

interface DeductibleLine {
  key: string;
  peril: string | null;
  text: string;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === "object" && !Array.isArray(value);
}

/** A stored key that is not a peril name (`item_3`, written when the row had none). */
const SYNTHETIC_KEY = /^item_\d+$/;

export function useDeductibleLines(raw: unknown): DeductibleLine[] {
  const { t } = useTranslation("proposals");
  const perilLabel = usePerilLabel();

  return React.useMemo(() => {
    const pairs: [string | null, unknown][] = Array.isArray(raw)
      ? raw.map((row) => [null, row])
      : isRecord(raw)
        ? Object.entries(raw)
        : [];
    const lines: DeductibleLine[] = [];
    pairs.forEach(([key, term], index) => {
      if (typeof term === "string") {
        if (term.trim()) {
          lines.push({
            key: `${index}`,
            peril: key && !SYNTHETIC_KEY.test(key) ? perilLabel(key) : null,
            text: term.trim(),
          });
        }
        return;
      }
      if (!isRecord(term)) return;
      const perilRaw =
        (typeof term.peril === "string" && term.peril.trim()) ||
        (key && !SYNTHETIC_KEY.test(key) ? key : null);
      const literal = typeof term.text === "string" ? term.text.trim() : "";
      const text = literal || formatDeductible(term as DeductibleTerm, t) || "";
      if (!text && !perilRaw) return;
      lines.push({
        key: `${index}-${perilRaw ?? ""}`,
        peril: perilRaw ? perilLabel(perilRaw) : null,
        text: text || "—",
      });
    });
    return lines;
  }, [perilLabel, raw, t]);
}

export function DeductiblesList({ value }: { value: unknown }) {
  const lines = useDeductibleLines(value);
  if (lines.length === 0) return null;
  return (
    <ul className="flex flex-col divide-y divide-line rounded-[12px] border border-line">
      {lines.map((line) => (
        <li key={line.key} className="px-3 py-2 text-body text-ink-2 [text-wrap:pretty]">
          {line.peril ? <span className="font-medium text-ink">{line.peril}: </span> : null}
          {line.text}
        </li>
      ))}
    </ul>
  );
}
