import * as React from "react";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import type { ColumnDef } from "@tanstack/react-table";
import {
  Ban,
  ClipboardCheck,
  ClipboardList,
  FileCheck2,
  Gauge,
  Pencil,
  Plus,
  Timer,
} from "lucide-react";
import { PageHeader } from "@/components/common/PageHeader";
import { KpiCard } from "@/components/common/KpiCard";
import { DataTable } from "@/components/common/DataTable";
import { FadeUp, Stagger } from "@/components/common/motion";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useInspectionRequests, useInspections } from "@/api/inspections";
import { useAssets } from "@/api/assets";
import { useUsers } from "@/api/users";
import { useModulePermissions, useCan } from "@/lib/permissions";
import { formatDate, formatNumber } from "@/lib/format";
import { num } from "@/api/types";
import {
  INSPECTION_REQUEST_STATUSES,
  INSPECTION_STATUSES,
  type Inspection,
  type InspectionRequest,
  type InspectionRequestStatus,
  type InspectionStatus,
} from "@/api/types";
import {
  GuardedButton,
  REPORT_STATUS_VARIANT,
  REQUEST_STATUS_VARIANT,
  URGENCY_VARIANT,
  scoreTone,
  type Guard,
} from "./components/shared";
import { RequestFormDialog } from "./components/RequestFormDialog";
import { CancelRequestDialog } from "./components/CancelRequestDialog";
import { InspectionFormDialog } from "./components/InspectionFormDialog";

/**
 * Inspections hub: the requests queue and the reports themselves, the two
 * halves of one workflow (`/inspection-requests` + `/inspections`).
 *
 * KPI totals come from cheap `limit=1` queries so the number is the server's
 * `total`, not a count of the page currently in memory.
 */

const ALL = "__all__";

export default function InspectionsPage() {
  const { t } = useTranslation("inspections");
  const { t: tc } = useTranslation("common");
  const navigate = useNavigate();

  const perms = useModulePermissions("Inspections");
  const canViewUsers = useCan("Users", "View");

  const createGuard: Guard = {
    allowed: perms.create,
    reason: t("guard.noPermission"),
  };
  const editGuard: Guard = { allowed: perms.edit, reason: t("guard.noPermission") };

  const [requestStatus, setRequestStatus] = React.useState<string>(ALL);
  const [reportStatus, setReportStatus] = React.useState<string>(ALL);
  const [latestOnly, setLatestOnly] = React.useState(false);

  const [requestDialogOpen, setRequestDialogOpen] = React.useState(false);
  const [editingRequest, setEditingRequest] = React.useState<InspectionRequest | null>(null);
  const [cancelling, setCancelling] = React.useState<InspectionRequest | null>(null);
  const [reportDialogOpen, setReportDialogOpen] = React.useState(false);
  const [reportFromRequest, setReportFromRequest] = React.useState<InspectionRequest | null>(
    null,
  );

  const requestsQuery = useInspectionRequests({
    status: requestStatus === ALL ? undefined : (requestStatus as InspectionRequestStatus),
    limit: 200,
  });
  const reportsQuery = useInspections({
    status: reportStatus === ALL ? undefined : (reportStatus as InspectionStatus),
    latest_only: latestOnly || undefined,
    limit: 200,
  });

  // KPI totals — one row each, we only read `total`.
  const pendingTotal = useInspectionRequests({ status: "pending", limit: 1 });
  const inProgressTotal = useInspectionRequests({ status: "in_progress", limit: 1 });
  const issuedTotal = useInspections({ status: "issued", limit: 1 });

  const assetsQuery = useAssets({ page_size: 200 });
  const assetName = React.useMemo(() => {
    const map = new Map<number, string>();
    for (const asset of assetsQuery.data?.items ?? []) map.set(asset.id, asset.name);
    return map;
  }, [assetsQuery.data]);

  const usersQuery = useUsers({ user_type: "broker", limit: 200 }, canViewUsers.allowed);
  const inspectorName = React.useMemo(() => {
    const map = new Map<number, string>();
    for (const user of usersQuery.data?.items ?? []) map.set(user.id, user.full_name);
    return map;
  }, [usersQuery.data]);

  const reports = reportsQuery.data?.items ?? [];
  const scored = reports.map((r) => num(r.overall_score)).filter((v): v is number => v !== null);
  const avgScore = scored.length
    ? scored.reduce((sum, value) => sum + value, 0) / scored.length
    : 0;

  const requestColumns = React.useMemo<ColumnDef<InspectionRequest>[]>(
    () => [
      {
        accessorKey: "id",
        header: t("requests.columns.id"),
        cell: ({ row }) => (
          <span className="font-mono text-mono text-text-tertiary">#{row.original.id}</span>
        ),
      },
      {
        id: "asset",
        header: t("requests.columns.asset"),
        accessorFn: (row) => assetName.get(row.asset_id) ?? `#${row.asset_id}`,
        cell: ({ row }) => (
          <span className="font-medium text-text-primary">
            {assetName.get(row.original.asset_id) ?? `#${row.original.asset_id}`}
          </span>
        ),
      },
      {
        accessorKey: "urgency",
        header: t("requests.columns.urgency"),
        cell: ({ row }) => (
          <Badge variant={URGENCY_VARIANT[row.original.urgency] ?? "neutral"}>
            {t(`urgency.${row.original.urgency}`)}
          </Badge>
        ),
      },
      {
        accessorKey: "target_date",
        header: t("requests.columns.targetDate"),
        cell: ({ row }) => formatDate(row.original.target_date),
      },
      {
        accessorKey: "status",
        header: t("requests.columns.status"),
        cell: ({ row }) => (
          <Badge variant={REQUEST_STATUS_VARIANT[row.original.status]}>
            {t(`status.request.${row.original.status}`)}
          </Badge>
        ),
      },
      {
        id: "actions",
        header: "",
        enableSorting: false,
        cell: ({ row }) => {
          const request = row.original;
          const closed = request.status === "cancelled" || request.status === "completed";
          return (
            <div className="flex items-center justify-end gap-1">
              <GuardedButton
                guard={createGuard}
                variant="ghost"
                size="sm"
                onClick={() => {
                  setReportFromRequest(request);
                  setReportDialogOpen(true);
                }}
              >
                <FileCheck2 /> {t("requests.createReport")}
              </GuardedButton>
              <GuardedButton
                guard={editGuard}
                variant="ghost"
                size="icon"
                className="h-8 w-8"
                aria-label={tc("actions.edit")}
                onClick={() => {
                  setEditingRequest(request);
                  setRequestDialogOpen(true);
                }}
              >
                <Pencil />
              </GuardedButton>
              <GuardedButton
                guard={
                  closed
                    ? { allowed: false, reason: t("requests.cancel.completed") }
                    : editGuard
                }
                variant="ghost"
                size="icon"
                className="h-8 w-8"
                aria-label={t("requests.cancel.action")}
                onClick={() => setCancelling(request)}
              >
                <Ban />
              </GuardedButton>
            </div>
          );
        },
      },
    ],
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [assetName, t, tc, createGuard.allowed, editGuard.allowed],
  );

  const reportColumns = React.useMemo<ColumnDef<Inspection>[]>(
    () => [
      {
        accessorKey: "folio",
        header: t("reports.columns.folio"),
        cell: ({ row }) => (
          <span className="font-mono text-mono text-text-tertiary">
            {row.original.folio ?? `#${row.original.id}`}
          </span>
        ),
      },
      {
        id: "asset",
        header: t("reports.columns.asset"),
        accessorFn: (row) => assetName.get(row.asset_id) ?? `#${row.asset_id}`,
        cell: ({ row }) => (
          <span className="font-medium text-text-primary">
            {assetName.get(row.original.asset_id) ?? `#${row.original.asset_id}`}
          </span>
        ),
      },
      {
        accessorKey: "version",
        header: t("reports.columns.version"),
        cell: ({ row }) => `v${row.original.version}`,
      },
      {
        id: "inspector",
        header: t("reports.columns.inspector"),
        accessorFn: (row) =>
          row.inspector_id ? (inspectorName.get(row.inspector_id) ?? `#${row.inspector_id}`) : "",
        cell: ({ row }) =>
          row.original.inspector_id
            ? (inspectorName.get(row.original.inspector_id) ??
              `#${row.original.inspector_id}`)
            : t("detail.unassigned"),
      },
      {
        accessorKey: "visit_date",
        header: t("reports.columns.visitDate"),
        cell: ({ row }) => formatDate(row.original.visit_date),
      },
      {
        id: "score",
        header: t("reports.columns.score"),
        accessorFn: (row) => num(row.overall_score) ?? -1,
        cell: ({ row }) => {
          const value = num(row.original.overall_score);
          return value === null ? (
            <span className="text-text-muted">—</span>
          ) : (
            <Badge variant={scoreTone(value)}>{formatNumber(value, 0)}</Badge>
          );
        },
      },
      {
        accessorKey: "status",
        header: t("reports.columns.status"),
        cell: ({ row }) => (
          <Badge variant={REPORT_STATUS_VARIANT[row.original.status]}>
            {t(`status.report.${row.original.status}`)}
          </Badge>
        ),
      },
    ],
    [assetName, inspectorName, t],
  );

  return (
    <>
      <PageHeader
        title={t("title")}
        subtitle={t("subtitle")}
        actions={
          <>
            <GuardedButton
              guard={createGuard}
              variant="secondary"
              onClick={() => {
                setEditingRequest(null);
                setRequestDialogOpen(true);
              }}
            >
              <Plus /> {t("requests.new")}
            </GuardedButton>
            <GuardedButton
              guard={createGuard}
              onClick={() => {
                setReportFromRequest(null);
                setReportDialogOpen(true);
              }}
            >
              <Plus /> {t("reports.new")}
            </GuardedButton>
          </>
        }
      />

      <Stagger className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <KpiCard
          label={t("kpi.pendingRequests")}
          hint={t("kpi.pendingRequestsHint")}
          countTo={pendingTotal.data?.total ?? 0}
          format={(n) => formatNumber(n, 0)}
          icon={<ClipboardList />}
          tone="warn"
        />
        <KpiCard
          label={t("kpi.inProgress")}
          hint={t("kpi.inProgressHint")}
          countTo={inProgressTotal.data?.total ?? 0}
          format={(n) => formatNumber(n, 0)}
          icon={<Timer />}
          tone="brand"
        />
        <KpiCard
          label={t("kpi.issued")}
          hint={t("kpi.issuedHint")}
          countTo={issuedTotal.data?.total ?? 0}
          format={(n) => formatNumber(n, 0)}
          icon={<ClipboardCheck />}
          tone="success"
        />
        <KpiCard
          label={t("kpi.avgScore")}
          hint={t("kpi.avgScoreHint")}
          countTo={avgScore}
          format={(n) => formatNumber(n, 0)}
          icon={<Gauge />}
          tone="action"
        />
      </Stagger>

      <FadeUp>
        <Tabs defaultValue="requests">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <TabsList>
              <TabsTrigger value="requests">{t("tabs.requests")}</TabsTrigger>
              <TabsTrigger value="reports">{t("tabs.reports")}</TabsTrigger>
            </TabsList>
          </div>

          <TabsContent value="requests" className="mt-4 flex flex-col gap-3">
            <div className="flex flex-wrap items-center gap-2">
              <Select value={requestStatus} onValueChange={setRequestStatus}>
                <SelectTrigger className="h-9 w-[200px]">
                  <SelectValue placeholder={t("filters.status")} />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={ALL}>{t("filters.all")}</SelectItem>
                  {INSPECTION_REQUEST_STATUSES.map((status) => (
                    <SelectItem key={status} value={status}>
                      {t(`status.request.${status}`)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <DataTable
              columns={requestColumns}
              data={requestsQuery.data?.items ?? []}
              isLoading={requestsQuery.isLoading}
              emptyMessage={t("requests.empty")}
            />
          </TabsContent>

          <TabsContent value="reports" className="mt-4 flex flex-col gap-3">
            <div className="flex flex-wrap items-center gap-2">
              <Select value={reportStatus} onValueChange={setReportStatus}>
                <SelectTrigger className="h-9 w-[200px]">
                  <SelectValue placeholder={t("filters.status")} />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={ALL}>{t("filters.all")}</SelectItem>
                  {INSPECTION_STATUSES.map((status) => (
                    <SelectItem key={status} value={status}>
                      {t(`status.report.${status}`)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <Button
                variant={latestOnly ? "teal" : "secondary"}
                size="sm"
                onClick={() => setLatestOnly((prev) => !prev)}
              >
                {t("filters.latestOnly")}
              </Button>
            </div>
            <DataTable
              columns={reportColumns}
              data={reports}
              isLoading={reportsQuery.isLoading}
              emptyMessage={t("reports.empty")}
              onRowClick={(row) => navigate(`/inspections/${row.id}`)}
            />
          </TabsContent>
        </Tabs>
      </FadeUp>

      <RequestFormDialog
        request={editingRequest}
        open={requestDialogOpen}
        onOpenChange={(open) => {
          setRequestDialogOpen(open);
          if (!open) setEditingRequest(null);
        }}
      />
      {cancelling ? (
        <CancelRequestDialog request={cancelling} onDone={() => setCancelling(null)} />
      ) : null}
      <InspectionFormDialog
        open={reportDialogOpen}
        onOpenChange={(open) => {
          setReportDialogOpen(open);
          if (!open) setReportFromRequest(null);
        }}
        fromRequest={reportFromRequest}
        inspectors={usersQuery.data?.items ?? []}
        inspectorsAvailable={canViewUsers.allowed}
      />
    </>
  );
}
