/**
 * Analítica — Propuestas (spec v4 §4.2).
 *
 * `GET /proposals` through `useProposals`. Server-side filter chips: `status`
 * and `insurer_id` (options from `/insurers`, rendered only when the caller
 * holds `Insurers:View` — no chip whose popover the server would 403).
 * `origin` / `is_confirmed` are honoured by the endpoint but missing from
 * `ProposalListParams` — an `api/proposals.ts` gap raised to its owner, not
 * pretend-filtered here.
 */
import * as React from "react";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import type { ColumnDef } from "@tanstack/react-table";
import { FileText } from "lucide-react";

import { DataTable } from "@/components/common/DataTable";
import { Badge } from "@/components/ui/badge";
import {
  ConfidenceBadge,
  EmptyState,
  ErrorBanner,
  MonoChip,
  OriginBadge,
  StatusBadge,
  permille,
  uf,
} from "@/pages/proposals/shared";
import { useProposals } from "@/api/proposals";
import { useInsurers } from "@/api/insurers";
import { useCan } from "@/lib/permissions";
import { PROPOSAL_STATUSES, type Proposal, type ProposalStatus } from "@/api/types";
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

export default function ProposalsTab() {
  const { t } = useTranslation("analytics");
  const { t: tProposals } = useTranslation("proposals");
  const navigate = useNavigate();

  const [status] = useUrlParam("status");
  const [insurer] = useUrlParam("insurer");
  const [pageIndex, setPageIndex] = usePageIndex();
  const setParams = useSetUrlParams();
  const setStatus = (value: string | null) => setParams({ status: value, page: null });
  const setInsurer = (value: string | null) => setParams({ insurer: value, page: null });

  const canSeeInsurers = useCan("Insurers", "View");
  const insurers = useInsurers({ limit: 100 }, canSeeInsurers.allowed);

  const scope = useScopeParams();
  const list = useProposals({
    // The page-level scope (grupo · grupo-cuenta · fechas) is applied
    // SERVER-side, exactly like the export, so the table and the file the
    // broker downloads can never disagree about what was filtered.
    ...scope,
    status: (status as ProposalStatus) || undefined,
    insurer_id: insurer ? Number(insurer) : undefined,
    limit: PAGE_SIZE,
    offset: pageIndex * PAGE_SIZE,
  });
  const items = list.data?.items ?? [];

  const statusOptions = React.useMemo(
    () =>
      PROPOSAL_STATUSES.map((s) => ({
        value: s,
        label: tProposals(`status.${s}`, { defaultValue: s }),
      })),
    [tProposals],
  );
  const insurerOptions = React.useMemo(
    () =>
      (insurers.data?.items ?? []).map((i) => ({
        value: String(i.id),
        label: i.trade_name ?? i.legal_name,
      })),
    [insurers.data],
  );

  const columns = React.useMemo<ColumnDef<Proposal>[]>(
    () => [
      {
        id: "insurer",
        header: t("columns.insurer"),
        accessorFn: (row) => row.insurer?.legal_name ?? "",
        cell: ({ row }) => (
          <div className="flex min-w-0 items-center gap-2">
            <span className="truncate font-medium text-text-primary">
              {row.original.insurer?.trade_name ??
                row.original.insurer?.legal_name ??
                "—"}
            </span>
            {row.original.insurer ? (
              <OriginBadge isNative={row.original.insurer.is_native} />
            ) : null}
          </div>
        ),
      },
      {
        accessorKey: "quote_request_id",
        header: t("columns.quote"),
        cell: ({ row }) => (
          <MonoChip>COT-{String(row.original.quote_request_id).padStart(4, "0")}</MonoChip>
        ),
      },
      {
        id: "total",
        header: () => <span className="block text-right">{t("columns.totalPremium")}</span>,
        accessorFn: (row) => Number(row.total_premium_uf ?? 0),
        cell: ({ row }) => (
          <span className="cell-num block">{uf(row.original.total_premium_uf)}</span>
        ),
      },
      {
        id: "rate",
        header: () => <span className="block text-right">{t("columns.rate")}</span>,
        accessorFn: (row) => Number(row.comprehensive_rate_permille ?? 0),
        cell: ({ row }) => (
          <span className="cell-num block">
            {permille(row.original.comprehensive_rate_permille)}
          </span>
        ),
      },
      {
        id: "confirmed",
        header: t("columns.confirmed"),
        accessorFn: (row) => row.is_confirmed,
        cell: ({ row }) =>
          row.original.is_confirmed ? (
            <Badge variant="success">{tProposals("confirmed")}</Badge>
          ) : row.original.extraction_confidence != null ? (
            <ConfidenceBadge value={row.original.extraction_confidence} />
          ) : (
            <Badge variant="neutral">{tProposals("unconfirmed")}</Badge>
          ),
      },
      {
        accessorKey: "status",
        header: t("columns.status"),
        cell: ({ row }) => (
          <StatusBadge
            value={row.original.status}
            label={tProposals(`status.${row.original.status}`, {
              defaultValue: row.original.status,
            })}
          />
        ),
      },
    ],
    [t, tProposals],
  );

  const isFiltered = !!(status || insurer);

  return (
    <div className="flex flex-col gap-3">
      <FilterRow
        isFiltered={isFiltered}
        onClear={() => setParams({ status: null, insurer: null, page: null })}
        trailing={<ResultCount total={list.data?.total} />}
      >
        <FilterChip
          label={t("filters.status")}
          value={status}
          options={statusOptions}
          onChange={setStatus}
        />
        {canSeeInsurers.allowed ? (
          <FilterChip
            label={t("filters.insurer")}
            value={insurer}
            options={insurerOptions}
            onChange={setInsurer}
          />
        ) : null}
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
        onRowClick={(row) => navigate(`/proposals/${row.id}`)}
        emptyMessage={
          <EmptyState
            icon={<FileText className="h-6 w-6" />}
            title={t("empty.proposals")}
            hint={t("emptyHint.proposals")}
          />
        }
      />
    </div>
  );
}
