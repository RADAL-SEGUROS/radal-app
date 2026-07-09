import * as React from "react";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { type ColumnDef } from "@tanstack/react-table";
import { Plus, Search, Users } from "lucide-react";
import { toast } from "sonner";

import { PageHeader } from "@/components/common/PageHeader";
import { KpiCard } from "@/components/common/KpiCard";
import { DataTable } from "@/components/common/DataTable";
import { EstadoBadge } from "@/components/common/EstadoBadge";
import { ExportButton } from "@/components/common/ExportButton";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { formatUF, formatDate, formatNumber } from "@/lib/format";
import { cn } from "@/lib/utils";
import {
  useClientes,
  type ClienteEstado,
  type ClienteListItem,
} from "./api";

const ESTADOS: ClienteEstado[] = [
  "activo",
  "onboarding",
  "prospecto",
  "suspendido",
  "archivado",
];

const ALL = "__all__";

export default function ClientesPage() {
  const { t } = useTranslation("clientes");
  const navigate = useNavigate();

  const [q, setQ] = React.useState("");
  const [debouncedQ, setDebouncedQ] = React.useState("");
  const [estado, setEstado] = React.useState<string>(ALL);
  const [sector, setSector] = React.useState<string>(ALL);

  React.useEffect(() => {
    const h = setTimeout(() => setDebouncedQ(q.trim()), 300);
    return () => clearTimeout(h);
  }, [q]);

  const { data, isLoading, isError } = useClientes({
    q: debouncedQ || undefined,
    estado: estado === ALL ? undefined : estado,
    sector: sector === ALL ? undefined : sector,
  });

  const rows = React.useMemo(() => data ?? [], [data]);

  // KPIs derived from an unfiltered fetch so counts stay stable regardless of
  // the active estado/sector filters.
  const { data: allData } = useClientes({ q: debouncedQ || undefined });
  const kpiSource = React.useMemo(() => allData ?? rows, [allData, rows]);
  const kpis = React.useMemo(() => {
    const count = (e: ClienteEstado) =>
      kpiSource.filter((c) => c.estado === e).length;
    return {
      total: kpiSource.length,
      activos: count("activo"),
      onboarding: count("onboarding"),
      prospectos: count("prospecto"),
    };
  }, [kpiSource]);

  // Sectors present in the data, for the filter dropdown.
  const sectores = React.useMemo(() => {
    const set = new Set<string>();
    kpiSource.forEach((c) => c.sector && set.add(c.sector));
    return Array.from(set).sort((a, b) => a.localeCompare(b, "es"));
  }, [kpiSource]);

  const columns = React.useMemo<ColumnDef<ClienteListItem>[]>(
    () => [
      {
        accessorKey: "id",
        header: t("table.idExpediente"),
        cell: ({ row }) => (
          <span className="font-mono text-mono text-text-secondary">
            CLI-{String(row.original.id).padStart(4, "0")}
          </span>
        ),
      },
      {
        accessorKey: "nombre",
        header: t("table.cliente"),
        cell: ({ row }) => (
          <div className="min-w-0">
            <div className="truncate text-body font-medium text-text-primary">
              {row.original.nombre}
            </div>
            {row.original.sector ? (
              <div className="truncate text-caption text-text-muted">
                {row.original.sector}
              </div>
            ) : null}
          </div>
        ),
      },
      {
        accessorKey: "rut",
        header: t("table.rut"),
        cell: ({ row }) => (
          <span className="font-mono text-mono text-text-secondary">
            {row.original.rut || "—"}
          </span>
        ),
      },
      {
        accessorKey: "contacto_principal",
        header: t("table.contacto"),
        cell: ({ row }) => (
          <div className="min-w-0">
            <div className="truncate text-body text-text-primary">
              {row.original.contacto_principal || "—"}
            </div>
            {row.original.telefono ? (
              <div className="truncate font-mono text-mono-sm text-text-muted">
                {row.original.telefono}
              </div>
            ) : null}
          </div>
        ),
      },
      {
        accessorKey: "polizas_vigentes",
        header: t("table.polizas"),
        cell: ({ row }) => (
          <span className="font-mono text-mono tabular-nums">
            {formatNumber(row.original.polizas_vigentes)}
          </span>
        ),
      },
      {
        id: "asegurados_adicionales",
        header: t("table.aseguradosAdicionales"),
        accessorFn: (r) => r.asegurados_adicionales ?? 0,
        cell: ({ row }) => (
          <span className="font-mono text-mono tabular-nums">
            {formatNumber(row.original.asegurados_adicionales ?? 0)}
          </span>
        ),
      },
      {
        accessorKey: "prima_total_uf",
        header: () => (
          <span className="block w-full text-right">
            {t("table.primaIntermediada")}
          </span>
        ),
        cell: ({ row }) => (
          <span className="block w-full text-right font-mono text-mono tabular-nums">
            {formatUF(row.original.prima_total_uf)}
          </span>
        ),
      },
      {
        id: "ejecutivo",
        header: t("table.ejecutivo"),
        accessorFn: (r) => r.ejecutivo?.nombre ?? "",
        cell: ({ row }) =>
          row.original.ejecutivo ? (
            <span className="text-body text-text-primary">
              {row.original.ejecutivo.nombre}
            </span>
          ) : (
            <span className="text-body text-text-muted">
              {t("table.sinEjecutivo")}
            </span>
          ),
      },
      {
        accessorKey: "fecha_alta",
        header: t("table.fechaAlta"),
        cell: ({ row }) => (
          <span className="whitespace-nowrap text-body text-text-secondary">
            {formatDate(row.original.fecha_alta)}
          </span>
        ),
      },
      {
        accessorKey: "estado",
        header: t("table.estado"),
        cell: ({ row }) => (
          <EstadoBadge
            estado={row.original.estado}
            label={t(`estado.${row.original.estado}`)}
          />
        ),
      },
    ],
    [t],
  );

  const stubAlta = () =>
    toast(t("toast.underConstruction"), { description: t("actions.alta") });

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title={t("title")}
        subtitle={t("subtitle")}
        actions={
          <>
            <ExportButton />
            <Button variant="primary" onClick={stubAlta} className="font-semibold">
              <Plus className="h-4 w-4" />
              {t("actions.alta")}
            </Button>
          </>
        }
      />

      {/* KPI cards */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <KpiCard
          label={t("kpis.total")}
          value={formatNumber(kpis.total)}
          icon={<Users className="h-5 w-5" />}
        />
        <KpiCard
          label={t("kpis.activos")}
          value={formatNumber(kpis.activos)}
          tone="success"
        />
        <KpiCard
          label={t("kpis.onboarding")}
          value={formatNumber(kpis.onboarding)}
        />
        <KpiCard
          label={t("kpis.prospectos")}
          value={formatNumber(kpis.prospectos)}
        />
      </div>

      {/* Filters */}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
        <div className="relative w-full sm:max-w-xs">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-text-muted" />
          <Input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder={t("filters.searchPlaceholder")}
            className="pl-9"
          />
        </div>
        <Select value={estado} onValueChange={setEstado}>
          <SelectTrigger className="w-full sm:w-44">
            <SelectValue placeholder={t("filters.estado")} />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL}>{t("filters.todos")}</SelectItem>
            {ESTADOS.map((e) => (
              <SelectItem key={e} value={e}>
                {t(`estado.${e}`)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        {sectores.length ? (
          <Select value={sector} onValueChange={setSector}>
            <SelectTrigger className="w-full sm:w-52">
              <SelectValue placeholder={t("filters.sector")} />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL}>{t("filters.todos")}</SelectItem>
              {sectores.map((s) => (
                <SelectItem key={s} value={s}>
                  {s}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        ) : null}
      </div>

      {/* Table */}
      <DataTable
        columns={columns}
        data={rows}
        isLoading={isLoading}
        emptyMessage={isError ? t("detail.loadError") : t("table.empty")}
        onRowClick={(row) => navigate(`/clientes/${row.id}`)}
        className={cn(isError && "border-signal-danger/40")}
      />
    </div>
  );
}
