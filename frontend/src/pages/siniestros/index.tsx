import * as React from "react";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { type ColumnDef } from "@tanstack/react-table";
import { AlertTriangle, Search, ClipboardCheck, Coins, HandCoins } from "lucide-react";

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
import { formatUF, formatDate } from "@/lib/format";
import { cn } from "@/lib/utils";
import { ReportarDialog } from "./ReportarDialog";
import {
  SINIESTRO_ESTADOS,
  useSiniestros,
  useSiniestrosSummary,
  type SiniestroListItem,
} from "./api";

const ALL = "__all__";

export default function SiniestrosPage() {
  const { t } = useTranslation("siniestros");
  const navigate = useNavigate();

  const [q, setQ] = React.useState("");
  const [debouncedQ, setDebouncedQ] = React.useState("");
  const [estado, setEstado] = React.useState<string>(ALL);

  React.useEffect(() => {
    const h = setTimeout(() => setDebouncedQ(q.trim()), 300);
    return () => clearTimeout(h);
  }, [q]);

  const { data, isLoading, isError } = useSiniestros({
    q: debouncedQ || undefined,
    estado: estado === ALL ? undefined : estado,
  });
  const summaryQuery = useSiniestrosSummary();
  const summary = summaryQuery.data;

  const rows = React.useMemo(() => data ?? [], [data]);

  const columns = React.useMemo<ColumnDef<SiniestroListItem>[]>(
    () => [
      {
        accessorKey: "id",
        header: t("table.id"),
        cell: ({ row }) => (
          <span className="font-mono text-mono text-text-secondary">
            SIN-{String(row.original.id).padStart(4, "0")}
          </span>
        ),
      },
      {
        id: "cliente",
        accessorFn: (r) => r.cliente?.nombre ?? "",
        header: t("table.cliente"),
        cell: ({ row }) => (
          <span className="text-body text-text-primary">
            {row.original.cliente?.nombre ?? "—"}
          </span>
        ),
      },
      {
        id: "poliza",
        accessorFn: (r) => r.poliza?.numero_poliza ?? "",
        header: t("table.poliza"),
        cell: ({ row }) => (
          <span className="font-mono text-mono-sm text-text-muted">
            {row.original.poliza?.numero_poliza ?? "—"}
          </span>
        ),
      },
      {
        accessorKey: "tipo",
        header: t("table.tipo"),
        cell: ({ row }) => (
          <span className="text-body text-text-secondary">
            {row.original.tipo ?? "—"}
          </span>
        ),
      },
      {
        accessorKey: "fecha_evento",
        header: t("table.fechaEvento"),
        cell: ({ row }) => (
          <span className="whitespace-nowrap font-mono text-mono-sm text-text-muted">
            {formatDate(row.original.fecha_evento)}
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
      {
        accessorKey: "monto_estimado_uf",
        header: () => (
          <span className="block w-full text-right">
            {t("table.montoEstimado")}
          </span>
        ),
        cell: ({ row }) => (
          <span className="block w-full text-right font-mono text-mono tabular-nums text-text-primary">
            {formatUF(row.original.monto_estimado_uf)}
          </span>
        ),
      },
      {
        accessorKey: "monto_liquidado_uf",
        header: () => (
          <span className="block w-full text-right">
            {t("table.montoLiquidado")}
          </span>
        ),
        cell: ({ row }) => (
          <span
            className={cn(
              "block w-full text-right font-mono text-mono tabular-nums",
              row.original.monto_liquidado_uf != null
                ? "text-lime"
                : "text-text-muted",
            )}
          >
            {formatUF(row.original.monto_liquidado_uf)}
          </span>
        ),
      },
    ],
    [t],
  );

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title={t("list.title")}
        subtitle={t("list.subtitle")}
        actions={
          <>
            <ExportButton />
            <ReportarDialog />
          </>
        }
      />

      {/* KPI cards */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <KpiCard
          label={t("kpis.abiertos")}
          value={summary?.abiertos ?? "—"}
          icon={<AlertTriangle className="h-5 w-5" />}
        />
        <KpiCard
          label={t("kpis.enEvaluacion")}
          value={summary?.en_evaluacion ?? "—"}
          tone="warn"
          icon={<ClipboardCheck className="h-5 w-5" />}
        />
        <KpiCard
          label={t("kpis.montoEstimadoTotal")}
          value={formatUF(summary?.monto_estimado_total_uf, { decimals: 0 })}
          icon={<Coins className="h-5 w-5" />}
        />
        <KpiCard
          label={t("kpis.montoLiquidadoTotal")}
          value={formatUF(summary?.monto_liquidado_total_uf, { decimals: 0 })}
          tone="success"
          icon={<HandCoins className="h-5 w-5" />}
        />
      </div>

      {/* Filters */}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
        <div className="relative w-full sm:max-w-xs">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-text-muted" />
          <Input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder={t("list.searchPlaceholder")}
            className="pl-9"
          />
        </div>
        <Select value={estado} onValueChange={setEstado}>
          <SelectTrigger className="w-full sm:w-52">
            <SelectValue placeholder={t("filters.estado")} />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL}>{t("filters.todos")}</SelectItem>
            {SINIESTRO_ESTADOS.map((e) => (
              <SelectItem key={e} value={e}>
                {t(`estado.${e}`)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      {/* Table */}
      <DataTable
        columns={columns}
        data={rows}
        isLoading={isLoading}
        emptyMessage={isError ? t("list.loadError") : t("empty")}
        onRowClick={(row) => navigate(`/siniestros/${row.id}`)}
        className={cn(isError && "border-signal-danger/40")}
      />
    </div>
  );
}
