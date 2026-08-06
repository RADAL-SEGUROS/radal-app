import * as React from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import type { ColumnDef } from "@tanstack/react-table";
import {
  AlarmClock,
  Briefcase,
  ClipboardCheck,
  Plus,
  Search,
  Send,
  X,
} from "lucide-react";
import { usePlacements, usePlacementsSummary } from "@/api/placements";
import { useClients } from "@/api/clients";
import {
  PLACEMENT_STATUSES,
  type PlacementListItem,
  type PlacementStatus,
} from "@/api/types";
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
import { useCan } from "@/lib/permissions";
import { cn } from "@/lib/utils";
import { SoonButton } from "@/pages/clients/Soon";
import { DaysChip, PlacementStatusBadge } from "@/pages/placements/status";
import { CreatePlacementDialog } from "@/pages/placements/PlacementForm";
import { useInsuranceLines } from "@/pages/placements/useInsuranceLines";

const ALL = "__all__";
const PAGE_SIZE = 25;

function useDebounced<T>(value: T, delay = 300): T {
  const [debounced, setDebounced] = React.useState(value);
  React.useEffect(() => {
    const id = setTimeout(() => setDebounced(value), delay);
    return () => clearTimeout(id);
  }, [value, delay]);
  return debounced;
}

function numberParam(value: string | null): number | undefined {
  if (!value) return undefined;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : undefined;
}

/**
 * `/placements` — every operating folder the broker has open.
 *
 * Filters map straight onto `GET /placements` query params
 * (`q`, `status`, `client_id`, `asset_id`, `insurance_line_id`, `open_only`).
 * Deep links from the dashboard and the client page arrive as search params.
 */
export default function PlacementsPage() {
  const { t } = useTranslation(["placements", "common"]);
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();

  const clientParam = numberParam(searchParams.get("client_id"));
  const assetParam = numberParam(searchParams.get("asset_id"));
  const statusParam = searchParams.get("status");

  const [term, setTerm] = React.useState("");
  const [status, setStatus] = React.useState<string>(
    statusParam && (PLACEMENT_STATUSES as readonly string[]).includes(statusParam)
      ? statusParam
      : ALL,
  );
  const [client, setClient] = React.useState<string>(
    clientParam ? String(clientParam) : ALL,
  );
  const [line, setLine] = React.useState<string>(ALL);
  const [openOnly, setOpenOnly] = React.useState(false);
  const [page, setPage] = React.useState(1);
  const debounced = useDebounced(term);

  const [createOpen, setCreateOpen] = React.useState(
    () => searchParams.get("new") === "1",
  );

  React.useEffect(() => {
    if (searchParams.get("new") === "1") {
      setCreateOpen(true);
      searchParams.delete("new");
      setSearchParams(searchParams, { replace: true });
    }
  }, [searchParams, setSearchParams]);

  React.useEffect(() => {
    setPage(1);
  }, [debounced, status, client, line, openOnly]);

  const summary = usePlacementsSummary(
    client === ALL ? undefined : Number(client),
  );
  const clients = useClients({ page_size: 200 });
  const { lines } = useInsuranceLines();

  const placements = usePlacements({
    q: debounced || undefined,
    status: status === ALL ? undefined : ([status] as PlacementStatus[]),
    client_id: client === ALL ? undefined : Number(client),
    asset_id: assetParam,
    insurance_line_id: line === ALL ? undefined : Number(line),
    open_only: openOnly || undefined,
    page,
    page_size: PAGE_SIZE,
  });

  const canCreate = useCan("Placements", "Create");

  const columns = React.useMemo<ColumnDef<PlacementListItem>[]>(
    () => [
      {
        id: "client",
        header: t("placements:columns.client") as string,
        accessorFn: (row) => row.client?.legal_name ?? "",
        cell: ({ row }) => (
          <div className="min-w-0">
            <p className="truncate text-label font-medium text-text-primary">
              {row.original.client?.legal_name ?? "—"}
            </p>
            <p className="truncate text-caption text-text-muted">
              {row.original.asset?.name ?? `#${row.original.asset_id}`}
            </p>
          </div>
        ),
      },
      {
        id: "line",
        header: t("placements:columns.line") as string,
        accessorFn: (row) => row.insurance_line?.name ?? "",
        cell: ({ row }) => (
          <span className="text-body text-text-secondary">
            {row.original.insurance_line?.name ?? "—"}
          </span>
        ),
      },
      {
        id: "period",
        header: t("placements:columns.period") as string,
        accessorFn: (row) => row.period ?? "",
        cell: ({ row }) => (
          <span className="font-mono text-mono-sm text-text-secondary">
            {row.original.period ?? "—"}
          </span>
        ),
      },
      {
        id: "status",
        header: t("placements:columns.status") as string,
        accessorFn: (row) => row.status,
        cell: ({ row }) => <PlacementStatusBadge status={row.original.status} />,
      },
      {
        id: "days",
        header: t("placements:columns.daysToEnd") as string,
        accessorFn: (row) => row.days_to_period_end ?? 99999,
        cell: ({ row }) => <DaysChip days={row.original.days_to_period_end} />,
      },
      {
        id: "market",
        header: t("placements:columns.market") as string,
        accessorFn: (row) => row.proposals_count,
        cell: ({ row }) => (
          <span className="whitespace-nowrap font-mono text-mono-sm tabular-nums text-text-secondary">
            {t("placements:columns.quotesShort", {
              count: row.original.quote_requests_count,
            })}{" "}
            ·{" "}
            {t("placements:columns.proposalsShort", {
              count: row.original.proposals_count,
            })}
          </span>
        ),
      },
    ],
    [t],
  );

  const data = placements.data;
  const totalPages = data?.pages ?? 1;
  const hasFilters =
    term !== "" || status !== ALL || client !== ALL || line !== ALL || openOnly;

  return (
    <div className="flex flex-col gap-[22px]">
      <PageHeader
        title={t("placements:title")}
        subtitle={t("placements:subtitle")}
        actions={
          canCreate.allowed ? (
            <Button onClick={() => setCreateOpen(true)}>
              <Plus />
              {t("placements:actions.new")}
            </Button>
          ) : (
            <SoonButton
              label={t("placements:actions.new")}
              reason={t("placements:permissions.noCreate")}
              icon={<Plus />}
            />
          )
        }
      />

      <Stagger className="grid gap-[18px] sm:grid-cols-2 xl:grid-cols-4">
        <KpiCard
          label={t("placements:kpi.open")}
          countTo={summary.data?.open ?? 0}
          icon={<Briefcase />}
          tone="brand"
        />
        <KpiCard
          label={t("placements:kpi.inMarket")}
          countTo={summary.data?.in_market ?? 0}
          icon={<Send />}
          tone="action"
        />
        <KpiCard
          label={t("placements:kpi.awaitingInspection")}
          countTo={summary.data?.awaiting_inspection ?? 0}
          icon={<ClipboardCheck />}
          tone="warn"
        />
        <KpiCard
          label={t("placements:kpi.expiring")}
          countTo={summary.data?.expiring_within_60_days ?? 0}
          icon={<AlarmClock />}
          tone={summary.data?.expiring_within_60_days ? "danger" : "default"}
        />
      </Stagger>

      <FadeUp>
        <Card className="flex flex-wrap items-center gap-3 p-3.5">
          <div className="relative min-w-[200px] flex-1">
            <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-text-muted" />
            <Input
              value={term}
              onChange={(event) => setTerm(event.target.value)}
              placeholder={t("placements:filters.searchPlaceholder")}
              aria-label={t("placements:filters.searchPlaceholder")}
              className="pl-9"
            />
          </div>

          <Select value={status} onValueChange={setStatus}>
            <SelectTrigger className="w-[180px]">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL}>{t("placements:filters.allStatuses")}</SelectItem>
              {PLACEMENT_STATUSES.map((value) => (
                <SelectItem key={value} value={value}>
                  {t(`placements:status.${value}`)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>

          <Select value={client} onValueChange={setClient}>
            <SelectTrigger className="w-[210px]">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL}>{t("placements:filters.allClients")}</SelectItem>
              {(clients.data?.items ?? []).map((item) => (
                <SelectItem key={item.id} value={String(item.id)}>
                  {item.insured.trade_name || item.insured.legal_name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>

          <Select value={line} onValueChange={setLine}>
            <SelectTrigger className="w-[190px]">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL}>{t("placements:filters.allLines")}</SelectItem>
              {lines.map((item) => (
                <SelectItem key={item.id} value={String(item.id)}>
                  {item.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>

          <button
            type="button"
            onClick={() => setOpenOnly((value) => !value)}
            aria-pressed={openOnly}
            className={cn(
              "h-10 rounded-[10px] border px-3.5 text-label font-medium transition-colors",
              openOnly
                ? "border-teal bg-[color-mix(in_srgb,var(--teal)_12%,transparent)] text-teal-deep"
                : "border-line bg-bg-surface text-text-tertiary hover:text-text-primary",
            )}
          >
            {t("placements:filters.openOnly")}
          </button>

          {assetParam ? (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => {
                searchParams.delete("asset_id");
                setSearchParams(searchParams, { replace: true });
              }}
            >
              <X />
              {t("placements:filters.assetFilter")}
            </Button>
          ) : null}

          {hasFilters ? (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => {
                setTerm("");
                setStatus(ALL);
                setClient(ALL);
                setLine(ALL);
                setOpenOnly(false);
              }}
            >
              <X />
              {t("common:actions.clear")}
            </Button>
          ) : null}
        </Card>
      </FadeUp>

      <FadeUp>
        <DataTable
          columns={columns}
          data={data?.items ?? []}
          isLoading={placements.isLoading}
          emptyMessage={
            placements.isError ? t("common:table.error") : t("placements:empty")
          }
          onRowClick={(row) => navigate(`/placements/${row.id}`)}
        />
      </FadeUp>

      <FadeUp className="flex items-center justify-between text-caption text-text-muted">
        <span>{t("placements:pagination.total", { count: data?.total ?? 0 })}</span>
        <div className="flex items-center gap-2">
          <Button
            variant="secondary"
            size="sm"
            disabled={page <= 1 || placements.isFetching}
            onClick={() => setPage((value) => Math.max(1, value - 1))}
          >
            {t("common:actions.previous")}
          </Button>
          <span className="font-mono text-mono-sm tabular-nums">
            {page} / {Math.max(1, totalPages)}
          </span>
          <Button
            variant="secondary"
            size="sm"
            disabled={page >= totalPages || placements.isFetching}
            onClick={() => setPage((value) => value + 1)}
          >
            {t("common:actions.next")}
          </Button>
        </div>
      </FadeUp>

      <CreatePlacementDialog
        open={createOpen}
        onOpenChange={setCreateOpen}
        clientId={client === ALL ? undefined : Number(client)}
        assetId={assetParam}
        onCreated={(placement) => navigate(`/placements/${placement.id}`)}
      />
    </div>
  );
}
