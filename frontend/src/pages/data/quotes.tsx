/**
 * Analítica — Cotizaciones (spec v4 §4.2).
 *
 * `GET /quotes` through the existing `useQuotes` hook. The only filter chip is
 * `status` — the one server-side parameter the frontend hook currently
 * exposes. (`priority` IS honoured by the endpoint but is missing from
 * `QuoteListParams`; adding it is an `api/quotes.ts` change this package does
 * not own — raised to the API owner rather than pretend-filtered here.)
 */
import * as React from "react";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import type { ColumnDef } from "@tanstack/react-table";
import { FileSearch } from "lucide-react";

import { DataTable } from "@/components/common/DataTable";
import { DueBadge, EmptyState, ErrorBanner, MonoChip, StatusBadge, uf } from "@/pages/proposals/shared";
import { useQuotes } from "@/api/quotes";
import { diasRestantes, formatDate } from "@/lib/format";
import { QUOTE_STATUSES, type QuoteRequest, type QuoteRequestStatus } from "@/api/types";
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

export default function QuotesTab() {
  const { t } = useTranslation("analytics");
  const { t: tQuotes } = useTranslation("quotes");
  const navigate = useNavigate();

  const [status] = useUrlParam("status");
  const [pageIndex, setPageIndex] = usePageIndex();
  const setParams = useSetUrlParams();
  const setStatus = (value: string | null) => setParams({ status: value, page: null });

  const scope = useScopeParams();
  const list = useQuotes({
    // The page-level scope (grupo · grupo-cuenta · fechas) is applied
    // SERVER-side, exactly like the export, so the table and the file the
    // broker downloads can never disagree about what was filtered.
    ...scope,
    status: (status as QuoteRequestStatus) || undefined,
    limit: PAGE_SIZE,
    offset: pageIndex * PAGE_SIZE,
  });
  const items = list.data?.items ?? [];

  const statusOptions = React.useMemo(
    () =>
      QUOTE_STATUSES.map((s) => ({
        value: s,
        label: tQuotes(`status.${s}`, { defaultValue: s }),
      })),
    [tQuotes],
  );

  const columns = React.useMemo<ColumnDef<QuoteRequest>[]>(
    () => [
      {
        accessorKey: "id",
        header: t("columns.reference"),
        cell: ({ row }) => (
          <MonoChip>COT-{String(row.original.id).padStart(4, "0")}</MonoChip>
        ),
      },
      {
        accessorKey: "insured_object",
        header: t("columns.object"),
        cell: ({ row }) => (
          <div className="min-w-0">
            <p className="truncate font-medium text-text-primary">
              {row.original.insured_object || "—"}
            </p>
            <p className="truncate text-caption text-text-muted">
              {row.original.placement?.period ?? "—"}
            </p>
          </div>
        ),
      },
      {
        id: "declared",
        header: () => <span className="block text-right">{t("columns.declared")}</span>,
        accessorFn: (row) => Number(row.declared_value_uf ?? 0),
        cell: ({ row }) => (
          <span className="cell-num block">{uf(row.original.declared_value_uf)}</span>
        ),
      },
      {
        accessorKey: "sent_at",
        header: t("columns.sentAt"),
        cell: ({ row }) => formatDate(row.original.sent_at),
      },
      {
        id: "due",
        header: t("columns.dueAt"),
        accessorFn: (row) => row.due_at ?? "",
        cell: ({ row }) =>
          row.original.due_at ? (
            <DueBadge days={diasRestantes(row.original.due_at)} />
          ) : (
            <span className="text-text-muted">—</span>
          ),
      },
      {
        accessorKey: "status",
        header: t("columns.status"),
        cell: ({ row }) => (
          <StatusBadge
            value={row.original.status}
            label={tQuotes(`status.${row.original.status}`, {
              defaultValue: row.original.status,
            })}
          />
        ),
      },
      {
        id: "proposals",
        header: () => <span className="block text-right">{t("columns.proposalsCount")}</span>,
        accessorFn: (row) => row.proposal_count,
        cell: ({ row }) => (
          <span className="cell-num block">{row.original.proposal_count}</span>
        ),
      },
    ],
    [t, tQuotes],
  );

  return (
    <div className="flex flex-col gap-3">
      <FilterRow
        isFiltered={!!status}
        onClear={() => setStatus(null)}
        trailing={<ResultCount total={list.data?.total} />}
      >
        <FilterChip
          label={t("filters.status")}
          value={status}
          options={statusOptions}
          onChange={setStatus}
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
        onRowClick={(row) => navigate(`/quotes/${row.id}`)}
        emptyMessage={
          <EmptyState
            icon={<FileSearch className="h-6 w-6" />}
            title={t("empty.quotes")}
            hint={t("emptyHint.quotes")}
          />
        }
      />
    </div>
  );
}
