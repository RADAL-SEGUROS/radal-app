/**
 * Policy inspector — the dynamic side (v8).
 *
 * A validate-then-dynamic policy carries a `payload`: the FULL confirmed parse
 * beyond the typed money/vigencia columns (vehicles, drivers, workers, sublimits,
 * clauses… — the shape VARIES per ramo and per carrier). This panel renders it
 * DEFENSIVELY as grouped key/values, tolerating unknown shapes, alongside the
 * fixed-core verdict and a prominent link to the ORIGINAL issued file.
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
import { useDocumentDownload } from "@/api/documents";
import {
  DisabledHint,
  Section,
  resolveFileUrl,
} from "@/components/common/kit";
import { asRows, humanizePath, renderValue } from "@/pages/policies/shared";

const HIDDEN_KEYS = new Set([
  // The typed columns already own these — no need to echo them dynamically.
  "policy_number",
  "insurer",
  "corredor",
]);

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === "object" && !Array.isArray(value);
}

function isEmpty(value: unknown): boolean {
  if (value === null || value === undefined || value === "") return true;
  if (Array.isArray(value)) return value.length === 0;
  if (isPlainObject(value)) return Object.keys(value).length === 0;
  return false;
}

/** One scalar entry as label-above-value. */
function ScalarRow({ label, value }: { label: string; value: unknown }) {
  return (
    <div className="min-w-0">
      <div className="text-caption font-medium text-ink-3">{label}</div>
      <div className="mt-0.5 truncate text-body text-ink" title={renderValue(value)}>
        {renderValue(value)}
      </div>
    </div>
  );
}

/** An array of records as a compact table — the adjuster/vehicle/worker lists. */
function RowsTable({ rows }: { rows: Record<string, unknown>[] }) {
  const cols = React.useMemo(() => {
    const seen: string[] = [];
    for (const row of rows) {
      for (const key of Object.keys(row)) {
        if (!seen.includes(key) && !isEmpty(row[key])) seen.push(key);
      }
    }
    return seen.slice(0, 6);
  }, [rows]);

  if (cols.length === 0) return null;

  return (
    <div className="overflow-x-auto rounded-lg border border-line">
      <table className="w-full border-collapse text-caption">
        <thead>
          <tr className="bg-bone">
            {cols.map((col) => (
              <th
                key={col}
                className="border-b border-line px-3 py-2 text-left font-medium text-ink-3"
              >
                {humanizePath(col)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i} className={cn(i > 0 && "border-t border-line")}>
              {cols.map((col) => (
                <td key={col} className="px-3 py-2 align-top text-ink-2">
                  {renderValue(row[col])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** One top-level payload group: scalars in a grid, arrays-of-records as tables. */
function PayloadGroup({ label, value }: { label: string; value: unknown }) {
  // Array of records → a table. Array of scalars → a joined line.
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
    return <ScalarRow label={label} value={value} />;
  }

  if (isPlainObject(value)) {
    const entries = Object.entries(value).filter(([, v]) => !isEmpty(v));
    return (
      <div className="flex flex-col gap-2">
        <div className="text-label font-medium text-ink">{label}</div>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {entries.map(([k, v]) =>
            isPlainObject(v) || (Array.isArray(v) && asRows(v).length > 0) ? (
              <div key={k} className="sm:col-span-2 lg:col-span-3">
                <PayloadGroup label={humanizePath(k)} value={v} />
              </div>
            ) : (
              <ScalarRow key={k} label={humanizePath(k)} value={v} />
            ),
          )}
        </div>
      </div>
    );
  }

  return <ScalarRow label={label} value={value} />;
}

export function PolicyPayloadPanel({ policy }: { policy: Policy }) {
  const { t } = useTranslation("postsale");

  const entries = Object.entries(policy.payload ?? {}).filter(
    ([key, value]) => !HIDDEN_KEYS.has(key) && !isEmpty(value),
  );

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
            {t(`policy.upload.coreField.${field}`, { defaultValue: field })}
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
          {entries.map(([key, value]) => (
            <PayloadGroup key={key} label={humanizePath(key)} value={value} />
          ))}
        </div>
      )}
    </Section>
  );
}

/** Prominent "Abrir archivo original" — resolves the presigned URL, opens it. */
export function OpenSourceLink({ documentId }: { documentId: number | null }) {
  const { t } = useTranslation("postsale");
  const download = useDocumentDownload(documentId ?? undefined, documentId != null);
  const url = resolveFileUrl(download.data?.url);

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

  if (!url) {
    return (
      <Button size="sm" variant="secondary" disabled>
        <FileText className="h-4 w-4" />
        {download.isError ? t("policy.overview.noSource") : t("policy.overview.opening")}
      </Button>
    );
  }

  return (
    <Button size="sm" variant="secondary" asChild>
      <a href={url} target="_blank" rel="noreferrer">
        <ExternalLink className="h-4 w-4" />
        {t("policy.extracted.openSource")}
      </a>
    </Button>
  );
}
