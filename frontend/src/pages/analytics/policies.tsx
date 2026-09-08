/**
 * Analítica — Pólizas (spec v4 §4.2).
 *
 * `GET /policies` through `usePolicies`. Server-side filter chips: `status`
 * and `insurer_id` (insurer options gated on `Insurers:View`).
 * The period column renders the contractual datetimes' dates (`period_*_at`,
 * the 12:00 convention) and falls back to the legacy dates.
 */
import * as React from "react";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import type { ColumnDef } from "@tanstack/react-table";
import { ShieldCheck } from "lucide-react";

import { DataTable } from "@/components/common/DataTable";
import { EmptyState, ErrorBanner, MonoChip, StatusBadge, uf } from "@/pages/proposals/shared";
import { usePolicies } from "@/api/policies";
import { useInsurers } from "@/api/insurers";
import { useCan } from "@/lib/permissions";
import { formatDate } from "@/lib/format";
import { POLICY_STATUSES, type Policy, type PolicyStatus } from "@/api/types";
import {
  FilterChip,
  FilterRow,
  PAGE_SIZE,
  ResultCount,
  usePageIndex,
  useSetUrlParams,
  useUrlParam,
} from "./shared";

export default function PoliciesTab() {
  const { t } = useTranslation("analytics");
  const { t: tPostsale } = useTranslation("postsale");
  const navigate = useNavigate();

  const [status] = useUrlParam("status");
  const [insurer] = useUrlParam("insurer");
  const [pageIndex, setPageIndex] = usePageIndex();
  const setParams = useSetUrlParams();
  const setStatus = (value: string | null) => setParams({ status: value, page: null });
  const setInsurer = (value: string | null) => setParams({ insurer: value, page: null });

  const canSeeInsurers = useCan("Insurers", "View");
  const insurers = useInsurers({ limit: 100 }, canSeeInsurers.allowed);

  const list = usePolicies({
    status: (status as PolicyStatus) || undefined,
    insurer_id: insurer ? Number(insurer) : undefined,
    limit: PAGE_SIZE,
    offset: pageIndex * PAGE_SIZE,
  });
  const items = list.data?.items ?? [];

  const statusOptions = React.useMemo(
    () =>
      POLICY_STATUSES.map((s) => ({
        value: s,
        label: tPostsale(`policy.status.${s}`, { defaultValue: s }),
      })),
    [tPostsale],
  );
  const insurerOptions = React.useMemo(
    () =>
      (insurers.data?.items ?? []).map((i) => ({
        value: String(i.id),
        label: i.trade_name ?? i.legal_name,
      })),
    [insurers.data],
  );

  const columns = React.useMemo<ColumnDef<Policy>[]>(
    () => [
      {
        accessorKey: "policy_number",
        header: t("columns.policyNumber"),
        cell: ({ row }) => <MonoChip>{row.original.policy_number}</MonoChip>,
      },
      {
        accessorKey: "client_legal_name",
        header: t("columns.client"),
        cell: ({ row }) => (
          <span className="truncate font-medium text-text-primary">
            {row.original.client_legal_name ?? "—"}
          </span>
        ),
      },
      {
        accessorKey: "insurer_name",
        header: t("columns.insurer"),
        cell: ({ row }) => row.original.insurer_name ?? "—",
      },
      {
        id: "period",
        header: t("columns.period"),
        accessorFn: (row) => row.period_start_at ?? row.start_date ?? "",
        cell: ({ row }) => {
          const start = row.original.period_start_at ?? row.original.start_date;
          const end = row.original.period_end_at ?? row.original.end_date;
          return (
            <span className="tabular-nums text-text-secondary">
              {start ? `${formatDate(start)} – ${formatDate(end)}` : "—"}
            </span>
          );
        },
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
        accessorKey: "status",
        header: t("columns.status"),
        cell: ({ row }) => (
          <StatusBadge
            value={row.original.status}
            label={tPostsale(`policy.status.${row.original.status}`, {
              defaultValue: row.original.status,
            })}
          />
        ),
      },
    ],
    [t, tPostsale],
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
        onRowClick={(row) => navigate(`/policies/${row.id}`)}
        emptyMessage={
          <EmptyState
            icon={<ShieldCheck className="h-6 w-6" />}
            title={t("empty.policies")}
            hint={t("emptyHint.policies")}
          />
        }
      />
    </div>
  );
}
