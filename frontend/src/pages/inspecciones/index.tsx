import * as React from "react";
import { useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { AlertCircle, ClipboardList, Eye, Loader } from "lucide-react";
import type { ColumnDef } from "@tanstack/react-table";

import api from "@/lib/api";
import { formatDate, formatNumber } from "@/lib/format";
import { PageHeader } from "@/components/common/PageHeader";
import { KpiCard } from "@/components/common/KpiCard";
import { DataTable } from "@/components/common/DataTable";
import { EstadoBadge } from "@/components/common/EstadoBadge";
import { ExportButton } from "@/components/common/ExportButton";
import { Input } from "@/components/ui/input";
import { Badge, type BadgeProps } from "@/components/ui/badge";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

import { SolicitarDialog } from "./SolicitarDialog";
import type {
  InspeccionEstado,
  InspeccionListItem,
  InspeccionesSummary,
  Paginated,
  Ref,
  Urgencia,
} from "./types";

const ESTADOS: InspeccionEstado[] = [
  "solicitada",
  "asignada",
  "en_progreso",
  "enviada",
  "observada",
  "validada",
  "cerrada",
];
const ALL = "__all__";

const URGENCIA_TONE: Record<Urgencia, NonNullable<BadgeProps["variant"]>> = {
  alta: "danger",
  media: "action",
  baja: "muted",
};

function useDebounced<T>(value: T, delay = 300): T {
  const [debounced, setDebounced] = React.useState(value);
  React.useEffect(() => {
    const id = setTimeout(() => setDebounced(value), delay);
    return () => clearTimeout(id);
  }, [value, delay]);
  return debounced;
}

export default function InspeccionesPage() {
  const { t } = useTranslation("inspecciones");
  const navigate = useNavigate();

  const [q, setQ] = React.useState("");
  const [estado, setEstado] = React.useState<string>(ALL);
  const [inspectorId, setInspectorId] = React.useState<string>(ALL);
  const debouncedQ = useDebounced(q, 300);

  const summaryQuery = useQuery({
    queryKey: ["inspecciones", "summary"],
    queryFn: async () => {
      const res = await api.get<InspeccionesSummary>("/inspecciones/summary");
      return res.data;
    },
  });

  const listQuery = useQuery({
    queryKey: ["inspecciones", "list", debouncedQ, estado, inspectorId],
    queryFn: async () => {
      const params: Record<string, string> = {};
      if (debouncedQ.trim()) params.q = debouncedQ.trim();
      if (estado !== ALL) params.estado = estado;
      if (inspectorId !== ALL) params.inspector_id = inspectorId;
      const res = await api.get<Paginated<InspeccionListItem>>("/inspecciones", {
        params,
      });
      return res.data;
    },
  });

  const summary = summaryQuery.data;

  // Derive the inspector filter options from the loaded rows.
  const inspectores = React.useMemo<Ref[]>(() => {
    const map = new Map<number, string>();
    for (const row of listQuery.data?.items ?? []) {
      if (row.inspector) map.set(row.inspector.id, row.inspector.nombre);
    }
    return Array.from(map.entries()).map(([id, nombre]) => ({ id, nombre }));
  }, [listQuery.data]);

  const columns = React.useMemo<ColumnDef<InspeccionListItem>[]>(
    () => [
      {
        accessorKey: "id",
        header: t("columns.id"),
        cell: ({ row }) => (
          <span className="font-mono text-mono text-text-secondary">
            INS-{String(row.original.id).padStart(4, "0")}
          </span>
        ),
      },
      {
        id: "activo",
        accessorFn: (r) => r.activo.nombre,
        header: t("columns.activoCliente"),
        cell: ({ row }) => (
          <div className="flex flex-col gap-0.5">
            <span className="text-body text-text-primary">
              {row.original.activo.nombre}
            </span>
            <span className="text-caption text-text-muted">
              {row.original.cliente.nombre}
            </span>
          </div>
        ),
      },
      {
        id: "ramo",
        accessorFn: (r) => r.ramo?.nombre ?? "",
        header: t("columns.ramo"),
        cell: ({ row }) => (
          <span className="text-body text-text-secondary">
            {row.original.ramo?.nombre ?? "—"}
          </span>
        ),
      },
      {
        id: "inspector",
        accessorFn: (r) => r.inspector?.nombre ?? "",
        header: t("columns.inspector"),
        cell: ({ row }) =>
          row.original.inspector ? (
            <span className="text-body text-text-secondary">
              {row.original.inspector.nombre}
            </span>
          ) : (
            <span className="text-body text-text-muted">{t("sinInspector")}</span>
          ),
      },
      {
        accessorKey: "estado",
        header: t("columns.estado"),
        cell: ({ row }) => (
          <EstadoBadge
            estado={row.original.estado}
            label={t(`estados.${row.original.estado}`)}
          />
        ),
      },
      {
        accessorKey: "version",
        header: () => (
          <span className="block text-right">{t("columns.version")}</span>
        ),
        cell: ({ row }) => (
          <span className="block text-right font-mono text-mono tabular-nums text-text-secondary">
            v{row.original.version}
          </span>
        ),
      },
      {
        id: "objetivo",
        accessorFn: (r) => r.fecha_objetivo ?? "",
        header: t("columns.objetivo"),
        cell: ({ row }) => (
          <div className="flex flex-col gap-1">
            <span className="whitespace-nowrap font-mono text-mono text-text-secondary">
              {formatDate(row.original.fecha_objetivo)}
            </span>
            {row.original.urgencia ? (
              <Badge
                variant={URGENCIA_TONE[row.original.urgencia] ?? "neutral"}
                className="w-fit"
              >
                {t(`urgencia.${row.original.urgencia}`)}
              </Badge>
            ) : null}
          </div>
        ),
      },
    ],
    [t],
  );

  return (
    <div className="space-y-6">
      <PageHeader
        title={t("title")}
        subtitle={t("subtitle")}
        actions={
          <>
            <ExportButton />
            <SolicitarDialog />
          </>
        }
      />

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <KpiCard
          label={t("kpis.activas")}
          value={summary ? formatNumber(summary.abiertas_total) : "—"}
          icon={<ClipboardList className="h-5 w-5" />}
        />
        <KpiCard
          label={t("kpis.solicitadas")}
          value={summary ? formatNumber(summary.solicitadas) : "—"}
          icon={<AlertCircle className="h-5 w-5" />}
        />
        <KpiCard
          label={t("kpis.enProgreso")}
          value={summary ? formatNumber(summary.en_progreso) : "—"}
          icon={<Loader className="h-5 w-5" />}
        />
        <KpiCard
          label={t("kpis.validadas")}
          value={summary ? formatNumber(summary.validadas) : "—"}
          tone="success"
          icon={<Eye className="h-5 w-5" />}
        />
      </div>

      <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
        <Input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder={t("filters.searchPlaceholder")}
          className="sm:max-w-xs"
        />
        <Select value={estado} onValueChange={setEstado}>
          <SelectTrigger className="sm:w-52">
            <SelectValue placeholder={t("filters.estado")} />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL}>{t("filters.estadoAll")}</SelectItem>
            {ESTADOS.map((e) => (
              <SelectItem key={e} value={e}>
                {t(`estados.${e}`)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Select value={inspectorId} onValueChange={setInspectorId}>
          <SelectTrigger className="sm:w-56">
            <SelectValue placeholder={t("filters.inspector")} />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL}>{t("filters.inspectorAll")}</SelectItem>
            {inspectores.map((i) => (
              <SelectItem key={i.id} value={String(i.id)}>
                {i.nombre}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      <DataTable
        columns={columns}
        data={listQuery.data?.items ?? []}
        isLoading={listQuery.isLoading}
        emptyMessage={listQuery.isError ? t("error") : t("empty")}
        onRowClick={(row) => navigate(`/inspecciones/${row.id}`)}
      />
    </div>
  );
}
