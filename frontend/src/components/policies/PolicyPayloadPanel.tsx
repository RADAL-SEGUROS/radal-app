/**
 * Policy inspector — the dynamic side (v8).
 *
 * A validate-then-dynamic policy carries a `payload`: the FULL confirmed parse
 * beyond the typed money/vigencia columns (vehicles, drivers, workers, sublimits,
 * clauses… — the shape VARIES per ramo and per carrier). This panel renders it
 * DEFENSIVELY as grouped key/values, tolerating unknown shapes, alongside the
 * fixed-core verdict and a prominent link to the ORIGINAL issued file.
 *
 * Review r2 C5: every label comes from the explicit `POLICY_PAYLOAD_FIELD_LABEL`
 * map (Spanish); a key the map does not know is HIDDEN, never printed raw.
 * Values are Chilean (UF 1.097,84 · 10 % · 15-10-2026), deductibles read as
 * sentences.
 *
 * Copy: `postsale` namespace (`policy.extracted.*`). Reuses the loose-payload
 * helpers from `pages/policies/shared`.
 */
import * as React from "react";
import { useTranslation } from "react-i18next";
import { AlertTriangle, CheckCircle2, ExternalLink, FileText } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { Policy } from "@/api/types";
import { DisabledHint, Section } from "@/components/common/kit";
import { PreviewDocButton } from "@/components/common/DownloadDocButton";
import { asRows } from "@/pages/policies/shared";
import {
  POLICY_CORE_FIELD_LABEL,
  POLICY_PAYLOAD_FIELD_LABEL,
  enumLabel,
  keyFor,
} from "@/i18n/labels";
import { formatNumber, formatUF } from "@/lib/format";
import { num, type DecimalString } from "@/api/types";
import { DeductiblesList } from "@/components/policies/DeductiblesList";

const HIDDEN_KEYS = new Set([
  // The typed columns already own these — no need to echo them dynamically.
  "policy_number",
  "insurer",
  "corredor",
  // A self-reported quality signal, not policy content.
  "confidence",
]);

type TFn = (key: string, opts?: Record<string, unknown>) => string;

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === "object" && !Array.isArray(value);
}

function isEmpty(value: unknown): boolean {
  if (value === null || value === undefined || value === "") return true;
  if (Array.isArray(value)) return value.length === 0;
  if (isPlainObject(value)) return Object.keys(value).length === 0;
  return false;
}

/** The Spanish label of a payload key, or null — an unknown key is HIDDEN (C5). */
function fieldLabel(t: TFn, key: string): string | null {
  const labelKey = keyFor(POLICY_PAYLOAD_FIELD_LABEL, key);
  return labelKey ? t(labelKey) : null;
}

const ISO_DATE = /^(\d{4})-(\d{2})-(\d{2})$/;
const ISO_DATETIME = /^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})/;

/**
 * One value in Chilean style, by what its key says it is: `*_uf` → "UF 1.097,84",
 * `pct` / `*_pct` → "10 %", `*_permille` → "2,31 ‰", contractual datetimes
 * (`*_at`, the 12:00 convention — wall clock, no zone shift) → "15-10-2026
 * 12:00", dates → "15-10-2026", booleans → Sí / No.
 */
function formatField(t: TFn, key: string, value: unknown): string | null {
  if (isEmpty(value)) return null;
  if (typeof value === "boolean") return value ? t("policy.extracted.yes") : t("policy.extracted.no");
  if (Array.isArray(value)) {
    const parts = value.map((v) => formatField(t, key, v)).filter((v): v is string => !!v);
    return parts.length ? parts.join(" · ") : null;
  }
  if (isPlainObject(value)) {
    const parts = Object.entries(value)
      .map(([k, v]) => {
        const label = fieldLabel(t, k);
        const shown = label ? formatField(t, k, v) : null;
        return label && shown ? `${label}: ${shown}` : null;
      })
      .filter((v): v is string => !!v);
    return parts.length ? parts.join(" · ") : null;
  }
  // Money / rates arrive as decimal strings too; any other numeric-looking
  // string (a policy or coupon number, a RUT body) stays verbatim.
  const measured =
    key.endsWith("_uf") || key === "pct" || key.endsWith("_pct") || key.endsWith("_permille");
  const n = num(value as DecimalString | number);
  const isNumeric =
    typeof value === "number" ||
    (measured && typeof value === "string" && /^-?\d+(\.\d+)?$/.test(value.trim()));
  if (n !== null && isNumeric) {
    const decimals = Number.isInteger(n) ? 0 : 2;
    if (key.endsWith("_uf")) return formatUF(n, { decimals });
    if (key === "pct" || key.endsWith("_pct")) return `${formatNumber(n, decimals)} %`;
    if (key.endsWith("_permille")) return `${formatNumber(n, Number.isInteger(n) ? 0 : 3)} ‰`;
    if (key === "year" || key === "number" || key === "coupon_number") return String(value);
    return formatNumber(n, decimals);
  }
  const text = String(value).trim();
  const dt = ISO_DATETIME.exec(text);
  if (dt) return `${dt[3]}-${dt[2]}-${dt[1]} ${dt[4]}:${dt[5]}`;
  const d = ISO_DATE.exec(text);
  if (d) return `${d[3]}-${d[2]}-${d[1]}`;
  return text;
}

/** One scalar entry as label-above-value. */
function ScalarRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0">
      <div className="text-caption font-medium text-ink-3">{label}</div>
      <div className="mt-0.5 text-body text-ink [overflow-wrap:anywhere]">{value}</div>
    </div>
  );
}

/** An array of records as a compact table — only the columns we can label. */
function RowsTable({ rows }: { rows: Record<string, unknown>[] }) {
  const { t } = useTranslation("postsale");
  const cols = React.useMemo(() => {
    const seen: string[] = [];
    for (const row of rows) {
      for (const key of Object.keys(row)) {
        if (!seen.includes(key) && !isEmpty(row[key]) && fieldLabel(t, key)) seen.push(key);
      }
    }
    return seen.slice(0, 6);
  }, [rows, t]);

  if (cols.length === 0) return null;

  const numericCol = (col: string) =>
    col.endsWith("_uf") || col === "pct" || col.endsWith("_pct") || col.endsWith("_permille");

  return (
    <div className="overflow-x-auto rounded-lg border border-line">
      <table className="w-full border-collapse text-caption">
        <thead>
          <tr className="bg-bone">
            {cols.map((col) => (
              <th
                key={col}
                className={cn(
                  "border-b border-line px-3 py-2 font-medium text-ink-3",
                  numericCol(col) ? "text-right" : "text-left",
                )}
              >
                {fieldLabel(t, col)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i} className={cn(i > 0 && "border-t border-line")}>
              {cols.map((col) => (
                <td
                  key={col}
                  className={cn(
                    "px-3 py-2 align-top text-ink-2",
                    numericCol(col) && "whitespace-nowrap text-right tabular-nums",
                  )}
                >
                  {formatField(t, col, row[col]) ?? "—"}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Does this value have anything we can show (a known key with a value)? */
function hasShowable(t: TFn, key: string, value: unknown): boolean {
  if (isEmpty(value)) return false;
  if (Array.isArray(value)) {
    const rows = asRows(value);
    if (rows.length > 0) return rows.some((row) => Object.entries(row).some(([k, v]) => fieldLabel(t, k) && !isEmpty(v)));
    return formatField(t, key, value) !== null;
  }
  if (isPlainObject(value)) {
    return Object.entries(value).some(([k, v]) => fieldLabel(t, k) !== null && hasShowable(t, k, v));
  }
  return true;
}

/** One top-level payload group: scalars in a grid, arrays-of-records as tables. */
function PayloadGroup({ fieldKey, label, value }: { fieldKey: string; label: string; value: unknown }) {
  const { t } = useTranslation("postsale");

  if (fieldKey === "deductibles") {
    return (
      <div className="flex flex-col gap-1.5">
        <div className="text-label font-medium text-ink">{label}</div>
        <DeductiblesList value={value} />
      </div>
    );
  }

  if (Array.isArray(value)) {
    const rows = asRows(value);
    if (rows.length > 0) {
      return (
        <div className="flex flex-col gap-1.5">
          <div className="text-label font-medium text-ink">{label}</div>
          <RowsTable rows={rows} />
        </div>
      );
    }
    // A list of texts (clauses, notes): one per line.
    const items = value.map((v) => formatField(t, fieldKey, v)).filter((v): v is string => !!v);
    if (items.length > 1 && items.some((item) => item.length > 40)) {
      return (
        <div className="flex flex-col gap-1.5">
          <div className="text-label font-medium text-ink">{label}</div>
          <ul className="list-disc space-y-1 ps-5 text-body text-ink-2 marker:text-ink-3">
            {items.map((item, i) => (
              <li key={i}>{item}</li>
            ))}
          </ul>
        </div>
      );
    }
    return items.length ? <ScalarRow label={label} value={items.join(" · ")} /> : null;
  }

  if (isPlainObject(value)) {
    const entries = Object.entries(value).filter(
      ([k, v]) => fieldLabel(t, k) !== null && hasShowable(t, k, v),
    );
    if (entries.length === 0) return null;
    return (
      <div className="flex flex-col gap-2">
        <div className="text-label font-medium text-ink">{label}</div>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {entries.map(([k, v]) =>
            isPlainObject(v) || (Array.isArray(v) && asRows(v).length > 0) ? (
              <div key={k} className="sm:col-span-2 lg:col-span-3">
                <PayloadGroup fieldKey={k} label={fieldLabel(t, k) as string} value={v} />
              </div>
            ) : (
              <ScalarRow key={k} label={fieldLabel(t, k) as string} value={formatField(t, k, v) ?? "—"} />
            ),
          )}
        </div>
      </div>
    );
  }

  const shown = formatField(t, fieldKey, value);
  return shown ? <ScalarRow label={label} value={shown} /> : null;
}

export function PolicyPayloadPanel({ policy }: { policy: Policy }) {
  const { t } = useTranslation("postsale");

  // Only the keys with a Spanish label (C5: never a raw English key), scalars
  // first so the identity/money facts lead and the long tables follow.
  const entries = Object.entries(policy.payload ?? {})
    .filter(
      ([key, value]) =>
        !HIDDEN_KEYS.has(key) && fieldLabel(t, key) !== null && hasShowable(t, key, value),
    )
    .sort(([, a], [, b]) => Number(typeof a === "object") - Number(typeof b === "object"));
  const scalars = entries.filter(([, value]) => typeof value !== "object");
  const groups = entries.filter(([, value]) => typeof value === "object");

  const missing = policy.core_validation?.missing ?? [];

  return (
    <Section
      title={t("policy.extracted.title")}
      description={t("policy.extracted.subtitle")}
      actions={<OpenSourceLink documentId={policy.source_document_id} />}
    >
      {/* Fixed-core verdict */}
      <div className="mb-4 flex flex-wrap items-center gap-2">
        {policy.is_core_valid == null ? (
          <Badge variant="muted">{t("policy.extracted.noVerdict")}</Badge>
        ) : policy.is_core_valid ? (
          <Badge variant="success" dot className="gap-1">
            <CheckCircle2 className="h-3 w-3" />
            {t("policy.extracted.coreValid")}
          </Badge>
        ) : (
          <Badge variant="warn" dot className="gap-1">
            <AlertTriangle className="h-3 w-3" />
            {t("policy.extracted.coreInvalid")}
          </Badge>
        )}
        {missing.map((field) => (
          <Badge key={field} variant="outline">
            {enumLabel(t, POLICY_CORE_FIELD_LABEL, field)}
          </Badge>
        ))}
        {policy.extraction_id ? (
          <span className="text-caption tabular-nums text-ink-3">
            {t("policy.extracted.extractionRef", { id: policy.extraction_id })}
          </span>
        ) : null}
      </div>

      {entries.length === 0 ? (
        <p className="text-caption text-ink-3">{t("policy.extracted.empty")}</p>
      ) : (
        <div className="flex flex-col gap-5">
          {scalars.length > 0 ? (
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {scalars.map(([key, value]) => (
                <ScalarRow
                  key={key}
                  label={fieldLabel(t, key) as string}
                  value={formatField(t, key, value) ?? "—"}
                />
              ))}
            </div>
          ) : null}
          {groups.map(([key, value]) => (
            <PayloadGroup key={key} fieldKey={key} label={fieldLabel(t, key) as string} value={value} />
          ))}
        </div>
      )}
    </Section>
  );
}

/** Prominent "Abrir archivo original" — a fresh link on click (`lib/download.ts`). */
export function OpenSourceLink({ documentId }: { documentId: number | null }) {
  const { t } = useTranslation("postsale");

  if (documentId == null) {
    return (
      <DisabledHint hint={t("policy.overview.noSource")}>
        <Button size="sm" variant="secondary" disabled>
          <FileText className="h-4 w-4" />
          {t("policy.extracted.openSource")}
        </Button>
      </DisabledHint>
    );
  }

  return (
    <PreviewDocButton
      documentId={documentId}
      variant="secondary"
      size="sm"
      icon={<ExternalLink className="h-4 w-4" aria-hidden />}
      label={t("policy.extracted.openSource")}
    />
  );
}
