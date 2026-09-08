/**
 * `/groups` — the list of the broker's groups, and the app's front door.
 *
 * A group is the broker-private label above the expediente ("JO PASTELERÍA" is
 * not the legal "Pacto Food SpA"). A group has NO RUT of its own — the
 * companies inside it do — so the table shows the CONTRATANTE (the primary
 * company, whose own RUT prints as a quiet caption) and counts: how many
 * companies, how many accounts, how many are still open, and which vigencia is
 * the latest.
 *
 * Every filter maps 1:1 onto a parameter `GET /account-groups` really accepts
 * (`q`, `status`, `page`, `page_size`) — nothing is filtered behind the user's
 * back, so the counts on screen always match the page they came from.
 */
import * as React from "react";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import type { ColumnDef } from "@tanstack/react-table";
import { Building2, Plus, Search, X } from "lucide-react";

import { PageHeader } from "@/components/common/PageHeader";
import { DataTable } from "@/components/common/DataTable";
import { FadeUp } from "@/components/common/motion";
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
import { DisabledHint, EmptyState, ErrorBanner } from "@/components/common/kit";
import { NewGroupDialog } from "@/pages/groups/GroupForm";
import { useDebounced } from "@/pages/groups/shared";
import { useAccountGroups } from "@/api/accountGroups";
import { useCan } from "@/lib/permissions";
import { formatDate } from "@/lib/format";
import type { AccountGroup, AccountGroupStatus } from "@/api/types";

const ALL = "__all__";
const PAGE_SIZE = 25;

export default function GroupsPage() {
  const { t } = useTranslation("accounts");
  const { t: tc } = useTranslation("common");
  const navigate = useNavigate();

  const canCreate = useCan("Groups", "Create");

  const [term, setTerm] = React.useState("");
  const [status, setStatus] = React.useState<string>(ALL);
  const [page, setPage] = React.useState(1);
  const [createOpen, setCreateOpen] = React.useState(false);
  const debounced = useDebounced(term);

  React.useEffect(() => {
    setPage(1);
  }, [debounced, status]);

  const groups = useAccountGroups({
    q: debounced || undefined,
    status: status === ALL ? undefined : (status as AccountGroupStatus),
    page,
    page_size: PAGE_SIZE,
  });

  const columns = React.useMemo<ColumnDef<AccountGroup, unknown>[]>(
    () => [
      {
        id: "name",
        header: t("group.fields.name"),
        accessorFn: (row) => row.name,
        cell: ({ row }) => (
          <div className="min-w-0">
            <div className="truncate text-body font-medium text-ink">
              {row.original.name}
            </div>
            <div className="mt-0.5 text-caption text-ink-3">
              {row.original.slug}
            </div>
          </div>
        ),
      },
      {
        // The CONTRATANTE — the primary company. The RUT below the legal name
        // is the COMPANY's identifier (legitimate), never the group's.
        id: "primary_client",
        header: t("group.fields.primaryClient"),
        accessorFn: (row) => row.primary_client?.legal_name ?? "",
        cell: ({ row }) => {
          const client = row.original.primary_client;
          if (!client) return <span className="text-ink-3">—</span>;
          return (
            <div className="min-w-0">
              <div className="truncate text-body text-ink-2">{client.legal_name}</div>
              <div className="mt-0.5 text-caption tabular-nums text-ink-3">
                {client.rut}
              </div>
            </div>
          );
        },
      },
      {
        id: "clients_count",
        header: t("group.fields.clients"),
        accessorFn: (row) => row.clients_count,
        cell: ({ row }) => (
          <span className="tabular-nums text-ink-2">
            {row.original.clients_count}
          </span>
        ),
      },
      {
        id: "accounts_count",
        header: t("group.fields.accounts"),
        accessorFn: (row) => row.accounts_count,
        cell: ({ row }) => (
          <span className="tabular-nums text-ink-2">
            {row.original.accounts_count}
          </span>
        ),
      },
      {
        id: "open_count",
        header: t("group.fields.openAccounts"),
        accessorFn: (row) => row.open_count,
        cell: ({ row }) =>
          row.original.open_count > 0 ? (
            <Badge variant="brand">{row.original.open_count}</Badge>
          ) : (
            <span className="tabular-nums text-ink-3">0</span>
          ),
      },
      {
        id: "latest_period_label",
        header: t("group.fields.latestPeriod"),
        accessorFn: (row) => row.latest_period_start ?? "",
        cell: ({ row }) => (
          <span className="text-caption tabular-nums text-ink-2">
            {row.original.latest_period_label ?? "—"}
          </span>
        ),
      },
      {
        id: "status",
        header: t("group.fields.status"),
        accessorFn: (row) => row.status,
        cell: ({ row }) => (
          <Badge variant={row.original.status === "active" ? "success" : "muted"} dot>
            {t(`group.status.${row.original.status}`)}
          </Badge>
        ),
      },
      {
        id: "updated_at",
        header: t("group.fields.updatedAt"),
        accessorFn: (row) => row.updated_at ?? "",
        cell: ({ row }) => (
          <span className="text-caption text-ink-3">
            {row.original.updated_at ? formatDate(row.original.updated_at) : "—"}
          </span>
        ),
      },
    ],
    [t],
  );

  const data = groups.data;
  const hasFilters = term !== "" || status !== ALL;
  const isEmpty = !groups.isLoading && (data?.items.length ?? 0) === 0 && !hasFilters;

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        title={t("group.title")}
        subtitle={t("group.subtitle")}
        actions={
          <DisabledHint hint={canCreate.allowed ? null : t("group.noCreatePermission")}>
            <Button
              size="sm"
              disabled={!canCreate.allowed || canCreate.isLoading}
              onClick={() => setCreateOpen(true)}
            >
              <Plus className="h-4 w-4" />
              {t("group.actions.new")}
            </Button>
          </DisabledHint>
        }
      />

      {groups.isError ? <ErrorBanner error={groups.error} /> : null}

      <FadeUp>
        <Card className="flex flex-wrap items-center gap-2.5 p-3.5">
          <div className="relative min-w-[220px] flex-1">
            <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-ink-3" />
            <Input
              value={term}
              onChange={(e) => setTerm(e.target.value)}
              placeholder={t("group.search")}
              className="pl-9"
            />
          </div>

          <Select value={status} onValueChange={setStatus}>
            <SelectTrigger className="w-[190px]">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL}>{t("group.filters.allStatuses")}</SelectItem>
              <SelectItem value="active">{t("group.status.active")}</SelectItem>
              <SelectItem value="archived">{t("group.status.archived")}</SelectItem>
            </SelectContent>
          </Select>

          {hasFilters ? (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => {
                setTerm("");
                setStatus(ALL);
              }}
            >
              <X className="h-4 w-4" />
              {tc("actions.clear")}
            </Button>
          ) : null}
        </Card>
      </FadeUp>

      {isEmpty ? (
        <FadeUp>
          <Card>
            <EmptyState
              icon={<Building2 className="h-6 w-6" />}
              title={t("empty.groups")}
              hint={t("empty.groupsHint")}
              action={
                <DisabledHint
                  hint={canCreate.allowed ? null : t("group.noCreatePermission")}
                >
                  <Button
                    size="sm"
                    disabled={!canCreate.allowed}
                    onClick={() => setCreateOpen(true)}
                  >
                    <Plus className="h-4 w-4" />
                    {t("group.actions.new")}
                  </Button>
                </DisabledHint>
              }
            />
          </Card>
        </FadeUp>
      ) : (
        <FadeUp>
          <DataTable
            columns={columns}
            data={data?.items ?? []}
            isLoading={groups.isLoading}
            emptyMessage={t("empty.groups")}
            onRowClick={(row) => navigate(`/groups/${row.id}`)}
            pageIndex={page - 1}
            pageSize={PAGE_SIZE}
            total={data?.total}
            onPageChange={(index) => setPage(index + 1)}
          />
        </FadeUp>
      )}

      <NewGroupDialog
        open={createOpen}
        onOpenChange={setCreateOpen}
        onCreated={(group) => navigate(`/groups/${group.id}`)}
      />
    </div>
  );
}
