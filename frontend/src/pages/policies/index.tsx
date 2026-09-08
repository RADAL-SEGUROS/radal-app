/**
 * Policies — list.
 *
 * The entry point to everything post-sale. Filters map 1:1 to what
 * `GET /policies` accepts (`status`, `q`), so nothing is quietly filtered
 * client-side. The period column shows the DATETIME: the contract runs to
 * 12:00 of the stated day and that hour decides claims.
 */
import * as React from "react";
import { Link, useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import type { ColumnDef } from "@tanstack/react-table";
import { CalendarClock, FileWarning, ShieldCheck, Wallet } from "lucide-react";

import { PageHeader } from "@/components/common/PageHeader";
import { KpiCard } from "@/components/common/KpiCard";
import { DataTable } from "@/components/common/DataTable";
import { FadeUp, Stagger } from "@/components/common/motion";
import { Badge } from "@/components/ui/badge";
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
import { formatUF } from "@/lib/format";
import { usePolicies } from "@/api/policies";
import { POLICY_STATUSES, num, type Policy, type PolicyStatus } from "@/api/types";
import {
  EmptyState,
  ErrorBanner,
  MonoChip,
  uf,
} from "@/pages/proposals/shared";
import { DateTimeValue, PostsaleBadge, daysBetween } from "@/pages/policies/shared";

export default function PoliciesListPage() {
  const { t } = useTranslation("postsale");
  const { t: tc } = useTranslation("common");
  const navigate = useNavigate();

  const [status, setStatus] = React.useState<PolicyStatus | "all">("all");
  const [term, setTerm] = React.useState("");
  const [query, setQuery] = React.useState("");

  // Debounce the search so a keystroke is not a request.
  React.useEffect(() => {
    const id = window.setTimeout(() => setQuery(term.trim()), 350);
    return () => window.clearTimeout(id);
  }, [term]);

  const policies = usePolicies({
    status: status === "all" ? undefined : status,
    q: query || undefined,
    limit: 100,
  });

  const rows = React.useMemo(() => policies.data?.items ?? [], [policies.data?.items]);

  const kpis = React.useMemo(() => {
    const active = rows.filter((p) => p.status === "active");
    const now = new Date().toISOString();
    const expiring = active.filter((p) => {
      const days = daysBetween(now, p.period_end_at ?? p.end_date);
      return days !== null && days >= 0 && days <= 60;
    }).length;
    const gross = active.reduce((sum, p) => sum + (num(p.total_premium_uf) ?? 0), 0);
    return {
      total: policies.data?.total ?? rows.length,
      active: active.length,
      expiring,
      gross,
    };
  }, [rows, policies.data?.total]);

  const columns = React.useMemo<ColumnDef<Policy>[]>(
    () => [
      {
        id: "number",
        header: () => t("policy.columns.number"),
        accessorFn: (row) => row.policy_number,
        cell: ({ row }) => (
          <div className="min-w-0">
            <div className="truncate font-medium text-text-primary">
              {row.original.policy_number}
            </div>
            <div className="mt-0.5 flex flex-wrap items-center gap-1.5">
              {row.original.cmf_policy_code ? (
                <MonoChip>{row.original.cmf_policy_code}</MonoChip>
              ) : null}
              {row.original.cover_mode ? (
                <span className="text-caption text-text-muted">{row.original.cover_mode}</span>
              ) : null}
            </div>
          </div>
        ),
      },
      {
        id: "client",
        header: () => t("policy.columns.client"),
        accessorFn: (row) => row.client_legal_name ?? "",
        cell: ({ row }) => (
          <span className="truncate">{row.original.client_legal_name ?? "—"}</span>
        ),
      },
      {
        id: "insurer",
        header: () => t("policy.columns.insurer"),
        accessorFn: (row) => row.insurer_name ?? "",
        cell: ({ row }) => <span className="truncate">{row.original.insurer_name ?? "—"}</span>,
      },
      {
        id: "period",
        header: () => t("policy.columns.period"),
        accessorFn: (row) => row.period_start_at ?? row.start_date ?? "",
        cell: ({ row }) => (
          <div className="whitespace-nowrap text-caption text-text-secondary">
            <DateTimeValue
              value={row.original.period_start_at}
              fallbackDate={row.original.start_date}
            />
            {" → "}
            <DateTimeValue
              value={row.original.period_end_at}
              fallbackDate={row.original.end_date}
            />
          </div>
        ),
      },
      {
        id: "insuredAmount",
        header: () => t("policy.columns.insuredAmount"),
        accessorFn: (row) => num(row.insured_amount_uf) ?? 0,
        cell: ({ row }) => (
          <span className="tabular-nums">{uf(row.original.insured_amount_uf, 0)}</span>
        ),
      },
      {
        id: "gross",
        header: () => t("policy.columns.gross"),
        accessorFn: (row) => num(row.total_premium_uf) ?? 0,
        cell: ({ row }) => (
          <span className="tabular-nums font-medium">{uf(row.original.total_premium_uf)}</span>
        ),
      },
      {
        accessorKey: "status",
        header: () => t("policy.columns.status"),
        cell: ({ row }) => (
          <PostsaleBadge
            value={row.original.status}
            label={t(`policy.status.${row.original.status}`)}
          />
        ),
      },
      {
        id: "postSale",
        header: () => t("policy.columns.postSale"),
        accessorFn: (row) => row.endorsements_count + row.claims_count,
        cell: ({ row }) => (
          <div className="flex flex-wrap items-center gap-1.5">
            {row.original.endorsements_count > 0 ? (
              <Badge variant="action">
                {t("subFunnel.kind.endorsement")} {row.original.endorsements_count}
              </Badge>
            ) : null}
            {row.original.claims_count > 0 ? (
              <Badge variant="danger">
                {t("subFunnel.kind.claim")} {row.original.claims_count}
              </Badge>
            ) : null}
            {row.original.warranties_count > 0 ? (
              <Badge variant="warn">
                {t("warranty.title")} {row.original.warranties_count}
              </Badge>
            ) : null}
            {row.original.endorsements_count +
              row.original.claims_count +
              row.original.warranties_count ===
            0 ? (
              <span className="text-text-muted">—</span>
            ) : null}
          </div>
        ),
      },
    ],
    [t],
  );

  return (
    <>
      <PageHeader title={t("policy.title")} subtitle={t("policy.subtitle")} />

      <Stagger className="grid grid-cols-2 gap-[18px] lg:grid-cols-4">
        <KpiCard
          label={t("policy.kpi.total")}
          countTo={kpis.total}
          icon={<ShieldCheck />}
          tone="brand"
        />
        <KpiCard
          label={t("policy.kpi.active")}
          countTo={kpis.active}
          icon={<ShieldCheck />}
          tone="success"
        />
        <KpiCard
          label={t("policy.kpi.expiring")}
          hint={t("policy.kpi.expiringHint")}
          countTo={kpis.expiring}
          icon={<CalendarClock />}
          tone="warn"
        />
        <KpiCard
          label={t("policy.kpi.gross")}
          countTo={kpis.gross}
          format={(n) => formatUF(n, { decimals: 0 })}
          icon={<Wallet />}
          tone="action"
        />
      </Stagger>

      <FadeUp delay={0.08}>
        <Card className="flex flex-wrap items-center gap-3 p-4">
          <Input
            value={term}
            onChange={(e) => setTerm(e.target.value)}
            placeholder={t("policy.searchPlaceholder")}
            className="h-9 w-full max-w-xs"
          />
          <Select value={status} onValueChange={(v) => setStatus(v as PolicyStatus | "all")}>
            <SelectTrigger className="h-9 w-[190px]">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">{t("policy.filters.allStatuses")}</SelectItem>
              {POLICY_STATUSES.map((s) => (
                <SelectItem key={s} value={s}>
                  {t(`policy.status.${s}`)}
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
            {t("policy.resultCount", { count: policies.data?.total ?? rows.length })}
          </span>
        </Card>
      </FadeUp>

      <ErrorBanner error={policies.error} />

      {!policies.isLoading && rows.length === 0 ? (
        <Card>
          <EmptyState
            title={t("policy.empty")}
            hint={t("policy.emptyHint")}
            icon={<FileWarning className="h-6 w-6" />}
          />
        </Card>
      ) : (
        <FadeUp delay={0.12}>
          <DataTable
            columns={columns}
            data={rows}
            isLoading={policies.isLoading}
            emptyMessage={t("policy.empty")}
            onRowClick={(row) => navigate(`/policies/${row.id}`)}
          />
        </FadeUp>
      )}

      <p className="text-caption text-text-muted">
        {t("policy.noonHint")}{" "}
        <Link to="/cases" className="underline">
          {t("policy.actions.openCase")}
        </Link>
      </p>
    </>
  );
}
