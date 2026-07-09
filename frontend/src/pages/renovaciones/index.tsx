import * as React from "react";
import { useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { AlertTriangle, Handshake, RefreshCw, Wallet } from "lucide-react";
import type { ColumnDef } from "@tanstack/react-table";

import api from "@/lib/api";
import { formatDate, formatPct, formatUF } from "@/lib/format";
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
import type {
  Paginated,
  Ref,
  RenovacionEstado,
  RenovacionListItem,
  RenovacionesSummary,
} from "./types";

const ESTADOS: RenovacionEstado[] = ["por_iniciar", "cotizando", "negociando"];
const ALL = "__all__";

function useDebounced<T>(value: T, delay = 250): T {
  const [debounced, setDebounced] = React.useState(value);
  React.useEffect(() => {
    const id = setTimeout(() => setDebounced(value), delay);
    return () => clearTimeout(id);
  }, [value, delay]);
  return debounced;
}

export default function RenovacionesPage() {
  const { t } = useTranslation("renovaciones");
  const { t: tc } = useTranslation("common");
  const navigate = useNavigate();

  const [q, setQ] = React.useState("");
  const [estado, setEstado] = React.useState<string>(ALL);
  const [aseguradoraId, setAseguradoraId] = React.useState<string>(ALL);
  const debouncedQ = useDebounced(q, 300);

  const summaryQuery = useQuery({
    queryKey: ["renovaciones", "summary"],
    queryFn: async () => {
      const res = await api.get<RenovacionesSummary>("/renovaciones/summary");
      return res.data;
    },
  });

  const aseguradorasQuery = useQuery({
    queryKey: ["aseguradoras"],
    queryFn: async () => {
      const res = await api.get<Paginated<Ref> | Ref[]>("/aseguradoras");
      return Array.isArray(res.data) ? res.data : res.data.items;
    },
  });

  const listQuery = useQuery({
    queryKey: ["renovaciones", "list", debouncedQ, estado, aseguradoraId],
    queryFn: async () => {
      const params: Record<string, string> = {};
      if (debouncedQ.trim()) params.q = debouncedQ.trim();
      if (estado !== ALL) params.estado = estado;
      if (aseguradoraId !== ALL) params.aseguradora_id = aseguradoraId;
      const res = await api.get<Paginated<RenovacionListItem>>("/renovaciones", {
        params,
      });
      return res.data;
    },
  });

  const summary = summaryQuery.data;

  const columns = React.useMemo<ColumnDef<RenovacionListItem>[]>(
    () => [
      {
        accessorKey: "codigo",
        header: t("columns.id"),
        cell: ({ row }) => (
          <span className="font-mono text-mono text-text-primary">
            {row.original.codigo}
          </span>
        ),
      },
      {
        id: "poliza",
        accessorFn: (r) => r.poliza?.numero_poliza ?? "",
        header: t("columns.poliza"),
        cell: ({ row }) => (
          <span className="font-mono text-mono text-text-secondary">
            {row.original.poliza?.numero_poliza ?? "—"}
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
        id: "aseguradora",
        accessorFn: (r) => r.aseguradora.nombre,
        header: t("columns.aseguradora"),
        cell: ({ row }) => (
          <span className="text-body text-text-secondary">
            {row.original.aseguradora.nombre}
          </span>
        ),
      },
      {
        id: "coaseguro",
        accessorFn: (r) => r.aplica_coaseguro,
        header: t("columns.coaseguro"),
        cell: ({ row }) => (
          <span className="text-body text-text-secondary">
            {row.original.aplica_coaseguro ? tc("units.si") : tc("units.no")}
          </span>
        ),
      },
      {
        accessorKey: "prima_defender_uf",
        header: () => (
          <span className="block text-right">{t("columns.primaDefender")}</span>
        ),
        cell: ({ row }) => (
          <span className="block text-right font-mono text-mono tabular-nums text-text-primary">
            {formatUF(row.original.prima_defender_uf, { withSymbol: false })}
          </span>
        ),
      },
      {
        accessorKey: "comision_pct",
        header: () => (
          <span className="block text-right">{t("columns.comision")}</span>
        ),
        cell: ({ row }) => (
          <span className="block text-right font-mono text-mono tabular-nums text-text-secondary">
            {formatPct(row.original.comision_pct)}
          </span>
        ),
      },
      {
        accessorKey: "fecha_vencimiento",
        header: t("columns.vencimiento"),
        cell: ({ row }) => (
          <span className="font-mono text-mono text-text-secondary">
            {formatDate(row.original.fecha_vencimiento)}
          </span>
        ),
      },
      {
        accessorKey: "dias_restantes",
        header: t("columns.diasRestantes"),
        cell: ({ row }) => (
          <DiasRestantes
            dias={row.original.dias_restantes}
            color={row.original.dias_color}
          />
        ),
      },
      {
        id: "ejecutivo",
        accessorFn: (r) => r.ejecutivo?.nombre ?? "",
        header: t("columns.ejecutivo"),
        cell: ({ row }) => (
          <span className="text-body text-text-secondary">
            {row.original.ejecutivo?.nombre ?? "—"}
          </span>
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
    [t, tc],
  );

  return (
    <div className="space-y-6">
      <PageHeader
        title={t("title")}
        subtitle={t("subtitle")}
        actions={<ExportButton />}
      />

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <KpiCard
          label={t("kpis.activas")}
          value={summary?.renovaciones_activas ?? "—"}
          icon={<RefreshCw className="h-5 w-5" />}
        />
        <KpiCard
          label={t("kpis.enNegociacion")}
          value={summary?.en_negociacion ?? "—"}
          icon={<Handshake className="h-5 w-5" />}
        />
        <KpiCard
          label={t("kpis.primaEnJuego")}
          value={
            summary ? formatUF(summary.prima_en_juego_uf, { decimals: 0 }) : "—"
          }
          icon={<Wallet className="h-5 w-5" />}
        />
        <KpiCard
          label={t("kpis.porVencer30d")}
          value={summary?.por_vencer_30d ?? "—"}
          hint={t("kpis.porVencer30dHint")}
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
        <Select value={aseguradoraId} onValueChange={setAseguradoraId}>
          <SelectTrigger className="sm:w-56">
            <SelectValue placeholder={t("filters.aseguradora")} />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL}>{t("filters.aseguradoraAll")}</SelectItem>
            {(aseguradorasQuery.data ?? []).map((a) => (
              <SelectItem key={a.id} value={String(a.id)}>
                {a.nombre}
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
        onRowClick={(row) => navigate(`/renovaciones/${row.id}`)}
      />
    </div>
  );
}
