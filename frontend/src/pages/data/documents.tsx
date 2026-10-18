/**
 * Analítica — Documentos: the cross-entity file explorer over `GET /documents`.
 *
 * Filter chips exist only for parameters the endpoint honours server-side
 * (`entity_type`, `category` — the shared.tsx doctrine); pagination is the
 * endpoint's own `limit/offset`. Category labels come from the `documents`
 * namespace and entity labels from `settings:entities` — both enums are fully
 * enumerated there, nothing is minted here.
 *
 * Download goes through `GET /documents/{id}/download` (the `document` table is
 * the only place an S3 key lives — the list row carries no usable URL), fetched
 * on click and opened in a new tab.
 */
import * as React from "react";
import { useTranslation } from "react-i18next";
import type { ColumnDef } from "@tanstack/react-table";
import { Download, FileText, Loader2 } from "lucide-react";

import { DataTable } from "@/components/common/DataTable";
import { Button } from "@/components/ui/button";
import { toast } from "sonner";
import { EmptyState, ErrorBanner, apiError } from "@/components/common/kit";
import { useDocuments } from "@/api/documents";
import { downloadDocument } from "@/lib/download";
import { formatDate } from "@/lib/format";
import {
  DOCUMENT_CATEGORIES,
  ENTITY_TYPES,
  type DocumentCategory,
  type EntityType,
  type RadalDocument,
} from "@/api/types";
import {
  FilterChip,
  FilterRow,
  PAGE_SIZE,
  ResultCount,
  usePageIndex,
  useSetUrlParams,
  useUrlParam,
} from "./shared";
import { useScopeParams } from "@/components/common/ScopeFilter";
import { DOCUMENT_CATEGORY_LABEL, ENTITY_TYPE_LABEL, enumLabel } from "@/i18n/labels";

/** "1,2 MB" — compact size with the app's decimal comma. */
function formatBytes(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined) return "—";
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB"] as const;
  let value = bytes;
  let unit: string = "B";
  for (const next of units) {
    if (value < 1024) break;
    value /= 1024;
    unit = next;
  }
  return `${value.toLocaleString("de-DE", { maximumFractionDigits: 1 })} ${unit}`;
}

/** Per-row download: a fresh short-lived link on click (`lib/download.ts`). */
function DownloadCell({ documentId, filename }: { documentId: number; filename: string }) {
  const { t } = useTranslation("analytics");
  const [busy, setBusy] = React.useState(false);

  return (
    <Button
      variant="ghost"
      size="icon"
      className="h-7 w-7"
      aria-label={t("documents.download")}
      title={t("documents.download")}
      disabled={busy}
      onClick={(event) => {
        event.stopPropagation();
        setBusy(true);
        downloadDocument(documentId, filename)
          .catch((error: unknown) => toast.error(apiError(error, t("documents.download"))))
          .finally(() => setBusy(false));
      }}
    >
      {busy ? (
        <Loader2 className="h-4 w-4 animate-spin" />
      ) : (
        <Download className="h-4 w-4" />
      )}
    </Button>
  );
}

export default function DocumentsTab() {
  const { t } = useTranslation("analytics");
  const { t: tDocuments } = useTranslation("documents");
  const { t: tSettings } = useTranslation("settings");

  const [entityType] = useUrlParam("entity");
  const [category] = useUrlParam("category");
  const [pageIndex, setPageIndex] = usePageIndex();
  const setParams = useSetUrlParams();

  const scope = useScopeParams();
  const list = useDocuments({
    // The page-level scope (grupo · grupo-cuenta · fechas) is applied
    // SERVER-side, exactly like the export, so the table and the file the
    // broker downloads can never disagree about what was filtered.
    ...scope,
    entity_type: (entityType as EntityType) || undefined,
    category: (category as DocumentCategory) || undefined,
    limit: PAGE_SIZE,
    offset: pageIndex * PAGE_SIZE,
  });
  const items = list.data?.items ?? [];

  const entityOptions = React.useMemo(
    () =>
      ENTITY_TYPES.map((value) => ({
        value,
        label: enumLabel(tSettings, ENTITY_TYPE_LABEL, value),
      })),
    [tSettings],
  );
  const categoryOptions = React.useMemo(
    () =>
      DOCUMENT_CATEGORIES.map((value) => ({
        value,
        label: enumLabel(tDocuments, DOCUMENT_CATEGORY_LABEL, value),
      })),
    [tDocuments],
  );

  const columns = React.useMemo<ColumnDef<RadalDocument>[]>(
    () => [
      {
        accessorKey: "original_name",
        header: t("columns.name"),
        cell: ({ row }) => (
          <div className="min-w-0 max-w-[320px]">
            <p className="truncate font-medium text-ink">{row.original.original_name}</p>
            {row.original.document_code ? (
              <p className="truncate text-caption tabular-nums text-ink-3">
                {row.original.document_code}
              </p>
            ) : null}
          </div>
        ),
      },
      {
        accessorKey: "category",
        header: t("columns.category"),
        cell: ({ row }) =>
          enumLabel(tDocuments, DOCUMENT_CATEGORY_LABEL, row.original.category),
      },
      {
        accessorKey: "entity_type",
        header: t("columns.entityType"),
        cell: ({ row }) => (
          <span className="text-ink-2">
            {enumLabel(tSettings, ENTITY_TYPE_LABEL, row.original.entity_type)}
          </span>
        ),
      },
      {
        id: "size",
        header: () => <span className="block text-right">{t("columns.size")}</span>,
        accessorFn: (row) => row.size_bytes ?? 0,
        cell: ({ row }) => (
          <span className="cell-num block">{formatBytes(row.original.size_bytes)}</span>
        ),
      },
      {
        accessorKey: "created_at",
        header: t("columns.uploadedAt"),
        cell: ({ row }) => formatDate(row.original.created_at),
      },
      {
        id: "download",
        header: () => <span className="sr-only">{t("documents.download")}</span>,
        enableSorting: false,
        cell: ({ row }) => (
          <DownloadCell documentId={row.original.id} filename={row.original.original_name} />
        ),
      },
    ],
    [t, tDocuments, tSettings],
  );

  const isFiltered = !!(entityType || category);

  return (
    <div className="flex flex-col gap-3">
      <FilterRow
        isFiltered={isFiltered}
        onClear={() => setParams({ entity: null, category: null, page: null })}
        trailing={<ResultCount total={list.data?.total} />}
      >
        <FilterChip
          label={t("filters.entityType")}
          value={entityType}
          options={entityOptions}
          onChange={(value) => setParams({ entity: value, page: null })}
        />
        <FilterChip
          label={t("filters.category")}
          value={category}
          options={categoryOptions}
          onChange={(value) => setParams({ category: value, page: null })}
        />
      </FilterRow>

      <ErrorBanner error={list.error} />

      <DataTable
        columns={columns}
        data={items}
        isLoading={list.isLoading}
        pageIndex={pageIndex}
        pageSize={PAGE_SIZE}
        total={list.data?.total}
        onPageChange={setPageIndex}
        emptyMessage={
          <EmptyState
            icon={<FileText className="h-6 w-6" />}
            title={t("empty.documents")}
            hint={t("emptyHint.documents")}
          />
        }
      />
    </div>
  );
}
