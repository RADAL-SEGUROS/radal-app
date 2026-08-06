/**
 * Proposals — list.
 *
 * Every row is an insurer's standardised offer against one quote request.
 * Filters map 1:1 to the query parameters `GET /proposals` accepts, so nothing
 * here is filtered client-side behind the user's back.
 */
import * as React from "react";
import { Link, useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import type { ColumnDef } from "@tanstack/react-table";
import { Check, FileUp, Layers, Sparkles, Wallet } from "lucide-react";

import { PageHeader } from "@/components/common/PageHeader";
import { KpiCard } from "@/components/common/KpiCard";
import { DataTable } from "@/components/common/DataTable";
import { FadeUp, Stagger } from "@/components/common/motion";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { formatDate, formatUF } from "@/lib/format";
import { useCan } from "@/lib/permissions";
import { useProposals } from "@/api/proposals";
import {
  PROPOSAL_STATUSES,
  PROPOSAL_ORIGINS,
  num,
  type Proposal,
  type ProposalOrigin,
  type ProposalStatus,
} from "@/api/types";
import {
  ConfidenceBadge,
  DisabledHint,
  EmptyState,
  ErrorBanner,
  MonoChip,
  StatusBadge,
  permille,
  pct,
  uf,
} from "@/pages/proposals/shared";

export default function ProposalsListPage() {
  const { t } = useTranslation("proposals");
  const { t: tc } = useTranslation("common");
  const navigate = useNavigate();

  const [status, setStatus] = React.useState<ProposalStatus | "all">("all");
  const [origin, setOrigin] = React.useState<ProposalOrigin | "all">("all");

  const proposals = useProposals({
    status: status === "all" ? undefined : status,
    limit: 100,
  });

  const canCreate = useCan("Proposals", "Create");

  const items = React.useMemo(() => {
    const rows = proposals.data?.items ?? [];
    return origin === "all" ? rows : rows.filter((p) => p.origin === origin);
  }, [proposals.data?.items, origin]);

  const kpis = React.useMemo(() => {
    const rows = proposals.data?.items ?? [];
    const pending = rows.filter((p) => !p.is_confirmed).length;
    const accepted = rows.filter((p) => p.status === "accepted").length;
    const totals = rows
      .map((p) => num(p.total_premium_uf))
      .filter((v): v is number => v !== null);
    const average = totals.length ? totals.reduce((a, b) => a + b, 0) / totals.length : 0;
    return { total: proposals.data?.total ?? rows.length, pending, accepted, average };
  }, [proposals.data]);

  const columns = React.useMemo<ColumnDef<Proposal>[]>(
    () => [
      {
        id: "insurer",
        header: () => t("table.insurer"),
        accessorFn: (row) => row.insurer?.legal_name ?? "",
        cell: ({ row }) => (
          <div className="min-w-0">
            <div className="truncate font-medium text-text-primary">
              {row.original.insurer?.trade_name ||
                row.original.insurer?.legal_name ||
                `#${row.original.insurer_id}`}
            </div>
            <div className="mt-0.5 flex items-center gap-1.5">
              <MonoChip>{row.original.insurer?.cmf_code ?? "—"}</MonoChip>
              <Badge variant={row.original.origin === "native" ? "brand" : "neutral"}>
                {t(`origin.${row.original.origin}`)}
              </Badge>
            </div>
          </div>
        ),
      },
      {
        id: "quote",
        header: () => t("table.quote"),
        accessorFn: (row) => row.quote_request_id,
        cell: ({ row }) => (
          <Link
            to={`/quotes/${row.original.quote_request_id}`}
            onClick={(e) => e.stopPropagation()}
            className="font-mono text-mono-sm"
          >
            COT-{String(row.original.quote_request_id).padStart(4, "0")}
          </Link>
        ),
      },
      {
        id: "total",
        header: () => t("table.total"),
        accessorFn: (row) => num(row.total_premium_uf) ?? 0,
        cell: ({ row }) => (
          <span className="tabular-nums font-medium">{uf(row.original.total_premium_uf)}</span>
        ),
      },
      {
        id: "rate",
        header: () => t("table.rate"),
        accessorFn: (row) => num(row.comprehensive_rate_permille) ?? 0,
        cell: ({ row }) => (
          <span className="tabular-nums">{permille(row.original.comprehensive_rate_permille)}</span>
        ),
      },
      {
        id: "commission",
        header: () => t("table.commission"),
        accessorFn: (row) => num(row.commission_pct) ?? 0,
        cell: ({ row }) => <span className="tabular-nums">{pct(row.original.commission_pct)}</span>,
      },
      {
        accessorKey: "status",
        header: () => t("table.status"),
        cell: ({ row }) => (
          <StatusBadge value={row.original.status} label={t(`status.${row.original.status}`)} />
        ),
      },
      {
        id: "confirmed",
        header: () => t("table.review"),
        accessorFn: (row) => (row.is_confirmed ? 1 : 0),
        cell: ({ row }) =>
          row.original.is_confirmed ? (
            <Badge variant="success" className="gap-1">
              <Check className="h-3 w-3" />
              {t("confirmed")}
            </Badge>
          ) : (
            <div className="flex flex-wrap items-center gap-1.5">
              <Badge variant="warn">{t("unconfirmed")}</Badge>
              <ConfidenceBadge value={row.original.extraction_confidence} />
            </div>
          ),
      },
      {
        id: "received",
        header: () => t("table.received"),
        accessorFn: (row) => row.received_at ?? "",
        cell: ({ row }) => (
          <span className="text-caption text-text-muted">
            {formatDate(row.original.received_at)}
          </span>
        ),
      },
    ],
    [t],
  );

  return (
    <>
      <PageHeader
        title={t("title")}
        subtitle={t("subtitle")}
        actions={
          canCreate.allowed ? (
            <Button asChild>
              <Link to="/proposals/upload">
                <Sparkles className="h-4 w-4" />
                {t("upload.action")}
              </Link>
            </Button>
          ) : (
            <DisabledHint hint={t("upload.noPermission")}>
              <Button disabled>
                <Sparkles className="h-4 w-4" />
                {t("upload.action")}
              </Button>
            </DisabledHint>
          )
        }
      />

      <Stagger className="grid grid-cols-2 gap-[18px] lg:grid-cols-4">
        <KpiCard label={t("kpi.total")} countTo={kpis.total} icon={<Layers />} tone="brand" />
        <KpiCard label={t("kpi.pending")} countTo={kpis.pending} icon={<FileUp />} tone="warn" />
        <KpiCard label={t("kpi.accepted")} countTo={kpis.accepted} icon={<Check />} tone="success" />
        <KpiCard
          label={t("kpi.average")}
          countTo={kpis.average}
          format={(n) => formatUF(n, { decimals: 0 })}
          icon={<Wallet />}
          tone="action"
        />
      </Stagger>

      <FadeUp delay={0.08}>
        <Card className="flex flex-wrap items-center gap-3 p-4">
          <Select value={status} onValueChange={(v) => setStatus(v as ProposalStatus | "all")}>
            <SelectTrigger className="h-9 w-[190px]">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">{t("filters.allStatuses")}</SelectItem>
              {PROPOSAL_STATUSES.map((s) => (
                <SelectItem key={s} value={s}>
                  {t(`status.${s}`)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>

          <Select value={origin} onValueChange={(v) => setOrigin(v as ProposalOrigin | "all")}>
            <SelectTrigger className="h-9 w-[180px]">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">{t("filters.allOrigins")}</SelectItem>
              {PROPOSAL_ORIGINS.map((o) => (
                <SelectItem key={o} value={o}>
                  {t(`origin.${o}`)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>

          {status !== "all" || origin !== "all" ? (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => {
                setStatus("all");
                setOrigin("all");
              }}
            >
              {tc("actions.clear")}
            </Button>
          ) : null}

          <span className="ml-auto text-caption text-text-muted">
            {t("filters.showing", { shown: items.length, total: proposals.data?.total ?? 0 })}
          </span>
        </Card>
      </FadeUp>

      {proposals.isError ? <ErrorBanner error={proposals.error} /> : null}

      <FadeUp delay={0.12}>
        {!proposals.isLoading && items.length === 0 ? (
          <Card>
            <EmptyState
              title={t("empty.title")}
              hint={t("empty.hint")}
              action={
                canCreate.allowed ? (
                  <Button size="sm" asChild>
                    <Link to="/proposals/upload">
                      <Sparkles className="h-4 w-4" />
                      {t("upload.action")}
                    </Link>
                  </Button>
                ) : undefined
              }
            />
          </Card>
        ) : (
          <DataTable
            columns={columns}
            data={items}
            isLoading={proposals.isLoading}
            onRowClick={(row) => navigate(`/proposals/${row.id}`)}
          />
        )}
      </FadeUp>
    </>
  );
}
