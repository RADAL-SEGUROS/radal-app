import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { AlertTriangle, Flame, ListChecks, Wallet } from "lucide-react";
import type { ColumnDef } from "@tanstack/react-table";

import api from "@/lib/api";
import { formatDate, formatNumber, formatUF } from "@/lib/format";
import { PageHeader } from "@/components/common/PageHeader";
import { KpiCard } from "@/components/common/KpiCard";
import { DataTable } from "@/components/common/DataTable";
import { EstadoBadge } from "@/components/common/EstadoBadge";
import { ExportButton } from "@/components/common/ExportButton";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

import { DiasRestantes } from "./DiasRestantes";
import { CreateDialog } from "./CreateDialog";
import type {
  CotizacionEstado,
  CotizacionListItem,
  CotizacionesSummary,
  Paginated,
  Prioridad,
} from "./types";

const ESTADOS: CotizacionEstado[] = ["pendiente", "respondida"];
const PRIORIDADES: Prioridad[] = ["baja", "media", "alta"];
const ALL = "__all__";

function useDebounced<T>(value: T, delay = 300): T {
  const [debounced, setDebounced] = React.useState(value);
  React.useEffect(() => {
    const id = setTimeout(() => setDebounced(value), delay);
    return () => clearTimeout(id);
  }, [value, delay]);
  return debounced;
}

export default function CotizacionesPage() {
  const { t } = useTranslation("cotizaciones");

  const [q, setQ] = React.useState("");
  const [estado, setEstado] = React.useState<string>(ALL);
  const [prioridad, setPrioridad] = React.useState<string>(ALL);
  const debouncedQ = useDebounced(q, 300);

  const summaryQuery = useQuery({
    queryKey: ["cotizaciones", "summary"],
    queryFn: async () => {
      const res = await api.get<CotizacionesSummary>("/cotizaciones/summary");
      return res.data;
    },
  });

  const listQuery = useQuery({
    queryKey: ["cotizaciones", "list", debouncedQ, estado, prioridad],
    queryFn: async () => {
      const params: Record<string, string> = {};
      if (debouncedQ.trim()) params.q = debouncedQ.trim();
      if (estado !== ALL) params.estado = estado;
      if (prioridad !== ALL) params.prioridad = prioridad;
      const res = await api.get<Paginated<CotizacionListItem>>(
        "/cotizaciones",
        { params },
      );
      return res.data;
    },
  });

  const summary = summaryQuery.data;

  const columns = React.useMemo<ColumnDef<CotizacionListItem>[]>(
    () => [
      {
        accessorKey: "id",
        header: t("columns.id"),
        cell: ({ row }) => (
          <span className="font-mono text-mono text-text-secondary">
            COT-{String(row.original.id).padStart(4, "0")}
          </span>
        ),
      },
      {
        id: "cliente",
        accessorFn: (r) => r.cliente.nombre,
        header: t("columns.cliente"),
        cell: ({ row }) => (
          <span className="text-body text-text-primary">
            {row.original.cliente.nombre}
          </span>
        ),
      },
      {
        id: "ramo",
        accessorFn: (r) => r.ramo.nombre,
        header: t("columns.ramo"),
        cell: ({ row }) => (
          <span className="text-body text-text-secondary">
            {row.original.ramo.nombre}
          </span>
        ),
      },
      {
        accessorKey: "bien_asegurar",
        header: t("columns.bienAsegurar"),
        cell: ({ row }) => (
          <span className="text-body text-text-primary">
            {row.original.bien_asegurar || "—"}
          </span>
        ),
      },
      {
        accessorKey: "valor_declarado_uf",
        header: () => (
          <span className="block text-right">{t("columns.valorDeclarado")}</span>
        ),
        cell: ({ row }) => (
          <span className="block text-right font-mono text-mono tabular-nums text-text-primary">
            {formatUF(row.original.valor_declarado_uf, { withSymbol: false })}
          </span>
        ),
      },
      {
        accessorKey: "fecha_envio",
        header: t("columns.fechaEnvio"),
        cell: ({ row }) => (
          <span className="whitespace-nowrap font-mono text-mono text-text-secondary">
            {formatDate(row.original.fecha_envio)}
          </span>
        ),
      },
      {
        accessorKey: "fecha_vence",
        header: t("columns.vence"),
        cell: ({ row }) => (
          <div className="flex flex-col gap-0.5">
            <span className="whitespace-nowrap font-mono text-mono text-text-secondary">
              {formatDate(row.original.fecha_vence)}
            </span>
            <DiasRestantes
              dias={row.original.dias_restantes}
              color={row.original.dias_color}
              className="text-mono-sm"
            />
          </div>
        ),
      },
      {
        accessorKey: "prioridad",
        header: t("columns.prioridad"),
        cell: ({ row }) => (
          <EstadoBadge
            estado={row.original.prioridad}
            label={t(`prioridad.${row.original.prioridad}`)}
          />
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
            <CreateDialog />
          </>
        }
      />

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <KpiCard
          label={t("kpis.enCurso")}
          value={summary ? formatNumber(summary.en_curso) : "—"}
          icon={<ListChecks className="h-5 w-5" />}
        />
        <KpiCard
          label={t("kpis.valorDeclarado")}
          value={
            summary
              ? formatUF(summary.valor_declarado_total_uf, { decimals: 0 })
              : "—"
          }
          icon={<Wallet className="h-5 w-5" />}
        />
        <KpiCard
          label={t("kpis.altaPrioridad")}
          value={summary ? formatNumber(summary.alta_prioridad) : "—"}
          icon={<Flame className="h-5 w-5" />}
        />
        <KpiCard
          label={t("kpis.porVencerL7d")}
          value={summary ? formatNumber(summary.por_vencer_l7d) : "—"}
          hint={t("kpis.porVencerL7dHint")}
          tone="danger"
          icon={<AlertTriangle className="h-5 w-5" />}
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
        <Select value={prioridad} onValueChange={setPrioridad}>
          <SelectTrigger className="sm:w-52">
            <SelectValue placeholder={t("filters.prioridad")} />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL}>{t("filters.prioridadAll")}</SelectItem>
            {PRIORIDADES.map((p) => (
              <SelectItem key={p} value={p}>
                {t(`prioridad.${p}`)}
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
      />
    </div>
  );
}
