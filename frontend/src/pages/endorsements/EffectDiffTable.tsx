/**
 * The before / after / delta table of an endorsement.
 *
 * `endorsement.effect` is JSON on purpose: the shape varies per kind (a vehicle
 * inclusion moves a fleet row, a pledge update moves a creditor block), so the
 * model refused to invent columns that only fit one of the fourteen kinds.
 *
 * This renderer therefore normalises whatever arrived into one honest shape —
 * dimension, before, after, delta — and accepts the two forms the corpus and
 * the extraction schemas actually produce:
 *
 *   [{ dimension|concept|item|field, before|state_before, after, delta }, …]
 *   { some_dimension: { before, after, delta }, other: "plain value" }
 *
 * Anything it cannot align is still shown, verbatim, instead of dropped: in
 * this domain the prose frequently IS the change.
 */
import { useTranslation } from "react-i18next";
import { Rows3 } from "lucide-react";

import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { EmptyState, Section } from "@/pages/proposals/shared";
import { asRows, humanizePath, pick, renderValue } from "@/pages/policies/shared";

interface EffectRow {
  dimension: string;
  before: unknown;
  after: unknown;
  delta: unknown;
}

const DIMENSION_KEYS = ["dimension", "concept", "item", "field", "name", "partida", "label"];
const BEFORE_KEYS = ["before", "state_before", "previous", "anterior", "from", "prior"];
const AFTER_KEYS = ["after", "state_after", "current", "nuevo", "to", "resulting"];
const DELTA_KEYS = ["delta", "variation", "difference", "change", "movement"];

function looksLikeDiff(value: Record<string, unknown>): boolean {
  return [...BEFORE_KEYS, ...AFTER_KEYS, ...DELTA_KEYS].some((key) => key in value);
}

/** Everything not consumed as dimension/before/after/delta, kept as one line. */
function remainder(row: Record<string, unknown>): string {
  const used = new Set([...DIMENSION_KEYS, ...BEFORE_KEYS, ...AFTER_KEYS, ...DELTA_KEYS]);
  const rest = Object.entries(row).filter(
    ([key, value]) => !used.has(key) && value !== null && value !== undefined && value !== "",
  );
  if (rest.length === 0) return "";
  return rest.map(([key, value]) => `${humanizePath(key)}: ${renderValue(value)}`).join(" · ");
}

export function normalizeEffect(
  effect: Record<string, unknown> | unknown[] | null | undefined,
): EffectRow[] {
  if (!effect) return [];

  // Form 1 — a list of rows, the shape the effect tables arrive in.
  if (Array.isArray(effect)) {
    return asRows(effect).map((row, index) => ({
      dimension:
        pick<string>(row, ...DIMENSION_KEYS) ?? `${index + 1}`,
      before: pick(row, ...BEFORE_KEYS),
      after: pick(row, ...AFTER_KEYS),
      delta: pick(row, ...DELTA_KEYS) ?? (remainder(row) || undefined),
    }));
  }

  // Form 2 — a dictionary keyed by dimension.
  const rows: EffectRow[] = [];
  for (const [key, value] of Object.entries(effect)) {
    if (Array.isArray(value)) {
      const nested = normalizeEffect(value);
      if (nested.length > 0) {
        rows.push(
          ...nested.map((row) => ({ ...row, dimension: `${humanizePath(key)} · ${row.dimension}` })),
        );
        continue;
      }
    }
    if (value && typeof value === "object" && looksLikeDiff(value as Record<string, unknown>)) {
      const inner = value as Record<string, unknown>;
      rows.push({
        dimension: humanizePath(key),
        before: pick(inner, ...BEFORE_KEYS),
        after: pick(inner, ...AFTER_KEYS),
        delta: pick(inner, ...DELTA_KEYS) ?? (remainder(inner) || undefined),
      });
      continue;
    }
    rows.push({
      dimension: humanizePath(key),
      before: undefined,
      after: value,
      delta: undefined,
    });
  }
  return rows;
}

export function EffectDiffTable({
  effect,
}: {
  effect: Record<string, unknown> | unknown[] | null | undefined;
}) {
  const { t } = useTranslation("postsale");
  const rows = normalizeEffect(effect);

  return (
    <Section title={t("effectDiff.title")} description={t("effectDiff.description")}>
      {rows.length === 0 ? (
        <EmptyState
          title={t("effectDiff.empty")}
          hint={t("effectDiff.emptyHint")}
          icon={<Rows3 className="h-6 w-6" />}
        />
      ) : (
        <div className="overflow-x-auto">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>{t("effectDiff.columns.dimension")}</TableHead>
                <TableHead>{t("effectDiff.columns.before")}</TableHead>
                <TableHead>{t("effectDiff.columns.after")}</TableHead>
                <TableHead>{t("effectDiff.columns.delta")}</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((row, index) => (
                <TableRow key={`${row.dimension}-${index}`}>
                  <TableCell className="align-top font-medium text-text-primary">
                    {row.dimension}
                  </TableCell>
                  <TableCell className="align-top text-text-secondary">
                    {renderValue(row.before)}
                  </TableCell>
                  <TableCell className="align-top text-text-primary">
                    {renderValue(row.after)}
                  </TableCell>
                  <TableCell className="align-top text-text-secondary">
                    {renderValue(row.delta)}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}
    </Section>
  );
}
