/**
 * Claims — list.
 *
 * Filters map 1:1 to `GET /claims` (`status`, `q`). The occurrence column keeps
 * the hour when the API sends one: a claim's time of day decides whether an
 * hourly franchise applies, so it is not rounded away to a date.
 */
import * as React from "react";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import type { ColumnDef } from "@tanstack/react-table";
import { Coins, Gavel, Siren, TriangleAlert } from "lucide-react";

import { PageHeader } from "@/components/common/PageHeader";
import { KpiCard } from "@/components/common/KpiCard";
import { DataTable } from "@/components/common/DataTable";
import { FadeUp, Stagger } from "@/components/common/motion";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { formatDate, formatUF } from "@/lib/format";
import { useClaims } from "@/api/claims";
import { CLAIM_STATUSES, num, type Claim, type ClaimStatus } from "@/api/types";
import { EmptyState, ErrorBanner, MonoChip, uf } from "@/pages/proposals/shared";
import { DateTimeValue, PostsaleBadge } from "@/pages/policies/shared";

export default function ClaimsListPage() {
  const { t } = useTranslation("postsale");
  const { t: tc } = useTranslation("common");
  const navigate = useNavigate();

  const [status, setStatus] = React.useState<ClaimStatus | "all">("all");
  const [term, setTerm] = React.useState("");
  const [query, setQuery] = React.useState("");

  React.useEffect(() => {
    const id = window.setTimeout(() => setQuery(term.trim()), 350);
    return () => window.clearTimeout(id);
  }, [term]);

  const claims = useClaims({
    status: status === "all" ? undefined : status,
    q: query || undefined,
    limit: 100,
  });

  const rows = React.useMemo(() => claims.data?.items ?? [], [claims.data?.items]);

  const kpis = React.useMemo(() => {
    const open = rows.filter((c) => c.status !== "closed" && c.status !== "rejected").length;
    const pendingRuling = rows.filter((c) => c.coverage_ruling === "pending").length;
    const paid = rows.reduce((sum, c) => sum + (num(c.paid_amount_uf) ?? 0), 0);
    return { total: claims.data?.total ?? rows.length, open, pendingRuling, paid };
  }, [rows, claims.data?.total]);

  const columns = React.useMemo<ColumnDef<Claim>[]>(
    () => [
      {
        id: "number",
        header: () => t("claim.columns.number"),
        accessorFn: (row) => row.claim_number ?? String(row.id),
        cell: ({ row }) => (
          <div className="min-w-0">
            <div className="truncate font-medium text-text-primary">
              {row.original.claim_number ?? `#${row.original.id}`}
            </div>
            {row.original.kind ? (
              <div className="mt-0.5 text-caption text-text-muted">{row.original.kind}</div>
            ) : null}
          </div>
        ),
      },
      {
        id: "policy",
        header: () => t("claim.columns.policy"),
        accessorFn: (row) => row.policy_number ?? "",
        cell: ({ row }) =>
          row.original.policy_number ? (
            <MonoChip>{row.original.policy_number}</MonoChip>
          ) : (
            <span className="text-text-muted">—</span>
          ),
      },
      {
        id: "occurred",
        header: () => t("claim.columns.occurred"),
        accessorFn: (row) => row.occurred_at ?? row.event_date ?? "",
        cell: ({ row }) => (
          <span className="whitespace-nowrap text-caption text-text-secondary">
            <DateTimeValue
              value={row.original.occurred_at}
              fallbackDate={row.original.event_date}
            />
          </span>
        ),
      },
      {
        id: "reported",
        header: () => t("claim.columns.reported"),
        accessorFn: (row) => row.reported_at ?? row.reported_date ?? "",
        cell: ({ row }) => (
          <span className="whitespace-nowrap text-caption text-text-muted">
            {formatDate(row.original.reported_date ?? row.original.reported_at)}
          </span>
        ),
      },
      {
        accessorKey: "status",
        header: () => t("claim.columns.status"),
        cell: ({ row }) => (
          <PostsaleBadge
            value={row.original.status}
            label={t(`claim.status.${row.original.status}`)}
          />
        ),
      },
      {
        id: "ruling",
        header: () => t("claim.columns.ruling"),
        accessorFn: (row) => row.coverage_ruling,
        cell: ({ row }) => (
          <PostsaleBadge
            value={row.original.coverage_ruling}
            label={t(`claim.ruling.${row.original.coverage_ruling}`)}
          />
        ),
      },
      {
        id: "indemnity",
        header: () => t("claim.columns.indemnity"),
        accessorFn: (row) => num(row.paid_amount_uf) ?? 0,
        cell: ({ row }) => (
          <span className="tabular-nums font-medium">{uf(row.original.paid_amount_uf)}</span>
        ),
      },
    ],
    [t],
  );

  return (
    <>
      <PageHeader title={t("claim.title")} subtitle={t("claim.subtitle")} />

      <Stagger className="grid grid-cols-2 gap-[18px] lg:grid-cols-4">
        <KpiCard label={t("claim.kpi.total")} countTo={kpis.total} icon={<Siren />} tone="brand" />
        <KpiCard
          label={t("claim.kpi.open")}
          countTo={kpis.open}
          icon={<TriangleAlert />}
          tone="warn"
        />
        <KpiCard
          label={t("claim.kpi.pendingRuling")}
          countTo={kpis.pendingRuling}
          icon={<Gavel />}
          tone="danger"
        />
        <KpiCard
          label={t("claim.kpi.paid")}
          countTo={kpis.paid}
          format={(n) => formatUF(n, { decimals: 0 })}
          icon={<Coins />}
          tone="success"
        />
      </Stagger>

      <FadeUp delay={0.08}>
        <Card className="flex flex-wrap items-center gap-3 p-4">
          <Input
            value={term}
            onChange={(e) => setTerm(e.target.value)}
            placeholder={t("claim.searchPlaceholder")}
            className="h-9 w-full max-w-xs"
          />
          <Select value={status} onValueChange={(v) => setStatus(v as ClaimStatus | "all")}>
            <SelectTrigger className="h-9 w-[190px]">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">{t("policy.filters.allStatuses")}</SelectItem>
              {CLAIM_STATUSES.map((s) => (
                <SelectItem key={s} value={s}>
                  {t(`claim.status.${s}`)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          {status !== "all" || term ? (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => {
                setStatus("all");
                setTerm("");
              }}
            >
              {tc("actions.clear")}
            </Button>
          ) : null}
          <span className="ml-auto text-caption text-text-muted">
            {t("claim.resultCount", { count: claims.data?.total ?? rows.length })}
          </span>
        </Card>
      </FadeUp>

      <ErrorBanner error={claims.error} />

      {!claims.isLoading && rows.length === 0 ? (
        <Card>
          <EmptyState
            title={t("claim.empty")}
            hint={t("claim.emptyHint")}
            icon={<Siren className="h-6 w-6" />}
          />
        </Card>
      ) : (
        <FadeUp delay={0.12}>
          <DataTable
            columns={columns}
            data={rows}
            isLoading={claims.isLoading}
            emptyMessage={t("claim.empty")}
            onRowClick={(row) => navigate(`/claims/${row.id}`)}
          />
        </FadeUp>
      )}
    </>
  );
}
