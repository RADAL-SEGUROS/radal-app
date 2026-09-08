import * as React from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import type { ColumnDef } from "@tanstack/react-table";
import { Building2, Plus, Search, UserCheck, Users, X } from "lucide-react";
import { useClients, useClientsSummary } from "@/api/clients";
import { useUsers } from "@/api/users";
import { CLIENT_STATUSES, type ClientListItem, type ClientStatus } from "@/api/types";
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
import { formatDate } from "@/lib/format";
import { ClientStatusBadge } from "@/pages/clients/status";
import { CreateClientDialog } from "@/pages/clients/ClientForm";
import { SoonButton } from "@/pages/clients/Soon";
import { formatRut } from "@/pages/clients/rut";

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

/**
 * `/clients` — the broker's book of business.
 *
 * Every filter maps 1:1 onto a query parameter that `GET /clients` actually
 * supports (`q`, `status`, `account_manager_id`, `page`, `page_size`); nothing
 * is filtered client-side behind the user's back.
 */
export default function ClientsPage() {
  const { t } = useTranslation(["clients", "common"]);
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();

  const [term, setTerm] = React.useState("");
  const [status, setStatus] = React.useState<string>(ALL);
  const [manager, setManager] = React.useState<string>(ALL);
  const [page, setPage] = React.useState(1);
  const debounced = useDebounced(term);

  const [createOpen, setCreateOpen] = React.useState(
    () => searchParams.get("new") === "1",
  );

  // The dashboard's quick action deep-links here with ?new=1.
  React.useEffect(() => {
    if (searchParams.get("new") === "1") {
      setCreateOpen(true);
      searchParams.delete("new");
      setSearchParams(searchParams, { replace: true });
    }
  }, [searchParams, setSearchParams]);

  React.useEffect(() => {
    setPage(1);
  }, [debounced, status, manager]);

  const summary = useClientsSummary();
  const managers = useUsers({ user_type: "broker", is_active: true, limit: 200 });
  const clients = useClients({
    q: debounced || undefined,
    status: status === ALL ? undefined : ([status] as ClientStatus[]),
    account_manager_id: manager === ALL ? undefined : Number(manager),
    page,
    page_size: PAGE_SIZE,
  });

  const canCreate = useCan("Clients", "Create");

  const columns = React.useMemo<ColumnDef<ClientListItem>[]>(
    () => [
      {
        id: "client",
        header: t("clients:columns.client") as string,
        accessorFn: (row) => row.insured.legal_name,
        cell: ({ row }) => (
          <div className="min-w-0">
            <p className="truncate text-label font-medium text-text-primary">
              {row.original.insured.trade_name || row.original.insured.legal_name}
            </p>
            <p className="truncate text-caption tabular-nums text-text-muted">
              {formatRut(row.original.insured.rut)}
            </p>
          </div>
        ),
      },
      {
        id: "status",
        header: t("clients:columns.status") as string,
        accessorFn: (row) => row.status,
        cell: ({ row }) => <ClientStatusBadge status={row.original.status} />,
      },
      {
        id: "sector",
        header: t("clients:columns.sector") as string,
        accessorFn: (row) => row.sector ?? "",
        cell: ({ row }) => (
          <span className="text-body text-text-secondary">
            {row.original.sector || "—"}
          </span>
        ),
      },
      {
        id: "manager",
        header: t("clients:columns.accountManager") as string,
        accessorFn: (row) => row.account_manager?.full_name ?? "",
        cell: ({ row }) =>
          row.original.account_manager ? (
            <span className="text-body text-text-secondary">
              {row.original.account_manager.full_name}
            </span>
          ) : (
            <span className="text-body text-text-muted">
              {t("clients:fields.unassigned")}
            </span>
          ),
      },
      {
        id: "assets",
        header: t("clients:columns.assets") as string,
        accessorFn: (row) => row.assets_count,
        cell: ({ row }) => (
          <span className="text-body tabular-nums text-text-secondary">
            {row.original.assets_count}
          </span>
        ),
      },
      {
        id: "placements",
        header: t("clients:columns.placements") as string,
        accessorFn: (row) => row.active_placements_count,
        cell: ({ row }) => (
          <span className="text-body tabular-nums text-text-secondary">
            {row.original.active_placements_count}
            <span className="text-text-muted"> / {row.original.placements_count}</span>
          </span>
        ),
      },
      {
        id: "since",
        header: t("clients:columns.since") as string,
        accessorFn: (row) => row.since ?? "",
        cell: ({ row }) => (
          <span className="text-body text-text-muted">
            {row.original.since ? formatDate(row.original.since) : "—"}
          </span>
        ),
      },
    ],
    [t],
  );

  const data = clients.data;
  const totalPages = data?.pages ?? 1;
  const hasFilters = term !== "" || status !== ALL || manager !== ALL;

  return (
    <div className="flex flex-col gap-[22px]">
      <PageHeader
        title={t("clients:title")}
        subtitle={t("clients:subtitle")}
        actions={
          canCreate.allowed ? (
            <Button onClick={() => setCreateOpen(true)}>
              <Plus />
              {t("clients:actions.new")}
            </Button>
          ) : (
            <SoonButton
              label={t("clients:actions.new")}
              reason={t("clients:permissions.noCreate")}
              icon={<Plus />}
            />
          )
        }
      />

      <Stagger className="grid gap-[18px] sm:grid-cols-2 xl:grid-cols-4">
        <KpiCard
          label={t("clients:kpi.total")}
          countTo={summary.data?.total ?? 0}
          icon={<Users />}
          tone="brand"
        />
        <KpiCard
          label={t("clients:kpi.active")}
          countTo={summary.data?.by_status?.active ?? 0}
          icon={<UserCheck />}
          tone="success"
        />
        <KpiCard
          label={t("clients:kpi.withPlacements")}
          countTo={summary.data?.with_active_placements ?? 0}
          icon={<Building2 />}
          tone="action"
        />
        <KpiCard
          label={t("clients:kpi.unassigned")}
          countTo={summary.data?.unassigned ?? 0}
          icon={<Users />}
          tone={summary.data?.unassigned ? "warn" : "default"}
        />
      </Stagger>

      <FadeUp>
        <Card className="flex flex-wrap items-center gap-3 p-3.5">
          <div className="relative min-w-[220px] flex-1">
            <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-text-muted" />
            <Input
              value={term}
              onChange={(event) => setTerm(event.target.value)}
              placeholder={t("clients:filters.searchPlaceholder")}
              aria-label={t("clients:filters.searchPlaceholder")}
              className="pl-9"
            />
          </div>

          <Select value={status} onValueChange={setStatus}>
            <SelectTrigger className="w-[190px]">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL}>{t("clients:filters.allStatuses")}</SelectItem>
              {CLIENT_STATUSES.map((value) => (
                <SelectItem key={value} value={value}>
                  {t(`clients:status.${value}`)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>

          <Select value={manager} onValueChange={setManager}>
            <SelectTrigger className="w-[210px]">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL}>{t("clients:filters.allManagers")}</SelectItem>
              {(managers.data?.items ?? []).map((user) => (
                <SelectItem key={user.id} value={String(user.id)}>
                  {user.full_name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>

          {hasFilters ? (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => {
                setTerm("");
                setStatus(ALL);
                setManager(ALL);
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
          isLoading={clients.isLoading}
          emptyMessage={
            clients.isError ? t("common:table.error") : t("clients:empty")
          }
          onRowClick={(row) => navigate(`/clients/${row.id}`)}
        />
      </FadeUp>

      <FadeUp className="flex items-center justify-between text-caption text-text-muted">
        <span>
          {t("clients:pagination.total", { count: data?.total ?? 0 })}
        </span>
        <div className="flex items-center gap-2">
          <Button
            variant="secondary"
            size="sm"
            disabled={page <= 1 || clients.isFetching}
            onClick={() => setPage((value) => Math.max(1, value - 1))}
          >
            {t("common:actions.previous")}
          </Button>
          <span className="text-caption tabular-nums">
            {page} / {Math.max(1, totalPages)}
          </span>
          <Button
            variant="secondary"
            size="sm"
            disabled={page >= totalPages || clients.isFetching}
            onClick={() => setPage((value) => value + 1)}
          >
            {t("common:actions.next")}
          </Button>
        </div>
      </FadeUp>

      <CreateClientDialog
        open={createOpen}
        onOpenChange={setCreateOpen}
        onCreated={(client) => navigate(`/clients/${client.id}`)}
      />
    </div>
  );
}
