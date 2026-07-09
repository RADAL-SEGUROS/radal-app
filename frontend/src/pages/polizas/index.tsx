import * as React from "react";
import { useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { type ColumnDef } from "@tanstack/react-table";
import { FileText, Layers, Coins, Users } from "lucide-react";
import api from "@/lib/api";
import { formatUF, formatPct, formatDate } from "@/lib/format";
import { PageHeader } from "@/components/common/PageHeader";
import { KpiCard } from "@/components/common/KpiCard";
import { DataTable } from "@/components/common/DataTable";
import { EstadoBadge, estadoVariant } from "@/components/common/EstadoBadge";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import type { PolizaListItem, PolizasList, PolizasSummary } from "./types";

function useDebounced<T>(value: T, delay = 300): T {
  const [debounced, setDebounced] = React.useState(value);
  React.useEffect(() => {
    const id = setTimeout(() => setDebounced(value), delay);
    return () => clearTimeout(id);
  }, [value, delay]);
  return debounced;
}

export default function PolizasPage() {
  const { t } = useTranslation(["polizas", "common"]);
  const navigate = useNavigate();
  const [search, setSearch] = React.useState("");
  const q = useDebounced(search);

  const summaryQuery = useQuery({
    queryKey: ["polizas", "summary"],
    queryFn: async () => {
      const { data } = await api.get<PolizasSummary>("/polizas/summary");
      return data;
    },
  });

  const listQuery = useQuery({
    queryKey: ["polizas", "list", q],
    queryFn: async () => {
      const { data } = await api.get<PolizasList>("/polizas", {
        params: { q: q || undefined, page_size: 100 },
      });
      return data;
    },
  });

  const columns = React.useMemo<ColumnDef<PolizaListItem>[]>(
    () => [
      {
        accessorKey: "numero_poliza",
        header: t("polizas:columns.numero"),
        cell: ({ row }) => (
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              navigate(`/polizas/${row.original.id}`);
            }}
            className="font-mono text-mono text-blue transition-colors hover:text-blue-deep hover:underline"
          >
            {row.original.numero_poliza}
          </button>
        ),
      },
      {
        id: "cliente",
        accessorFn: (r) => r.cliente?.nombre,
        header: t("polizas:columns.cliente"),
        cell: ({ row }) => (
          <span className="text-body text-text-primary">
            {row.original.cliente?.nombre ?? "—"}
          </span>
        ),
      },
      {
        id: "ramo",
        accessorFn: (r) => r.ramo?.nombre,
        header: t("polizas:columns.ramo"),
        cell: ({ row }) => (
          <span className="text-body text-text-secondary">
            {row.original.ramo?.nombre ?? "—"}
          </span>
        ),
      },
      {
        id: "aseguradora",
        accessorFn: (r) => r.aseguradora?.nombre,
        header: t("polizas:columns.aseguradora"),
        cell: ({ row }) => (
          <div className="flex flex-col gap-0.5">
            <span className="text-body text-text-primary">
              {row.original.aseguradora?.nombre ?? "—"}
            </span>
            {row.original.tiene_coaseguro ? (
              <Badge variant="brand" className="w-fit">
                {t("polizas:coaseguro.conCoaseguro")}
              </Badge>
            ) : null}
          </div>
        ),
      },
      {
        accessorKey: "suma_asegurada_uf",
        header: () => (
          <span className="block text-right">
            {t("polizas:columns.limites")}
          </span>
        ),
        cell: ({ row }) => (
          <span className="block text-right font-mono text-mono text-text-primary">
            {formatUF(row.original.suma_asegurada_uf)}
          </span>
        ),
      },
      {
        accessorKey: "prima_uf",
        header: () => (
          <span className="block text-right">
            {t("polizas:columns.prima")}
          </span>
        ),
        cell: ({ row }) => (
          <span className="block text-right font-mono text-mono text-text-primary">
            {formatUF(row.original.prima_uf)}
          </span>
        ),
      },
      {
        accessorKey: "comision_pct",
        header: () => (
          <span className="block text-right">
            {t("polizas:columns.comision")}
          </span>
        ),
        cell: ({ row }) => (
          <span className="block text-right font-mono text-mono text-text-secondary">
            {formatPct(row.original.comision_pct)}
          </span>
        ),
      },
      {
        id: "cobertura",
        accessorFn: (r) => r.cobertura_estado,
        header: t("polizas:columns.cobertura"),
        cell: ({ row }) => {
          const est =
            row.original.cobertura_estado ?? row.original.tipo_cobertura;
          return (
            <Badge variant={estadoVariant(est)}>
              {t(`polizas:cobertura.${est}`)}
            </Badge>
          );
        },
      },
      {
        id: "vigencia",
        accessorFn: (r) => r.vigencia_inicio,
        header: t("polizas:columns.vigencia"),
        cell: ({ row }) => (
          <span className="whitespace-nowrap text-caption text-text-secondary">
            {formatDate(row.original.vigencia_inicio)} –{" "}
            {formatDate(row.original.vigencia_fin)}
          </span>
        ),
      },
      {
        accessorKey: "estado",
        header: t("polizas:columns.estado"),
        cell: ({ row }) => (
          <EstadoBadge
            estado={row.original.estado}
            label={t(`polizas:estado.${row.original.estado}`)}
          />
        ),
      },
    ],
    [navigate, t],
  );

  const summary = summaryQuery.data;

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title={t("polizas:list.title")}
        subtitle={t("polizas:list.subtitle")}
      />

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <KpiCard
          label={t("polizas:kpis.polizasVigentes")}
          value={summary?.polizas_vigentes ?? "—"}
          icon={<FileText className="h-5 w-5" />}
        />
        <KpiCard
          label={t("polizas:kpis.sumaAseguradaTotal")}
          value={formatUF(summary?.suma_asegurada_total_uf, { decimals: 0 })}
          icon={<Layers className="h-5 w-5" />}
        />
        <KpiCard
          label={t("polizas:kpis.primaTotal")}
          value={formatUF(summary?.prima_total_uf, { decimals: 0 })}
          icon={<Coins className="h-5 w-5" />}
        />
        <KpiCard
          label={t("polizas:kpis.conCoaseguro")}
          value={summary?.con_coaseguro ?? "—"}
          icon={<Users className="h-5 w-5" />}
        />
      </div>

      <div className="max-w-md">
        <Input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder={t("polizas:list.searchPlaceholder")}
        />
      </div>

      <DataTable
        columns={columns}
        data={listQuery.data?.items ?? []}
        isLoading={listQuery.isLoading}
        emptyMessage={t("polizas:empty")}
        onRowClick={(row) => navigate(`/polizas/${row.id}`)}
      />
    </div>
  );
}
