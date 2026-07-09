import { useTranslation } from "react-i18next";
import { FileCheck2, Users, RefreshCw, TrendingUp } from "lucide-react";
import { KpiCard } from "@/components/common/KpiCard";
import { Skeleton } from "@/components/ui/skeleton";
import { formatNumber, formatUF } from "@/lib/format";
import type { DashboardKpis } from "./types";

interface KpiGridProps {
  kpis?: DashboardKpis;
  loading?: boolean;
}

export function KpiGrid({ kpis, loading }: KpiGridProps) {
  const { t } = useTranslation("dashboard");

  if (loading || !kpis) {
    return (
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {Array.from({ length: 4 }).map((_, i) => (
          <Skeleton key={i} className="h-[92px]" />
        ))}
      </div>
    );
  }

  return (
    <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
      <KpiCard
        label={t("kpis.polizasVigentes")}
        value={formatNumber(kpis.polizas_vigentes)}
        icon={<FileCheck2 className="h-5 w-5" />}
      />
      <KpiCard
        label={t("kpis.clientesActivos")}
        value={formatNumber(kpis.clientes_activos)}
        icon={<Users className="h-5 w-5" />}
      />
      <KpiCard
        label={t("kpis.renovacionesActivas")}
        value={formatNumber(kpis.renovaciones_activas)}
        icon={<RefreshCw className="h-5 w-5" />}
      />
      <KpiCard
        label={t("kpis.pipelinePonderado")}
        value={formatUF(kpis.pipeline_ponderado_uf, { decimals: 0 })}
        hint={t("kpis.pipelineHint")}
        icon={<TrendingUp className="h-5 w-5" />}
      />
    </div>
  );
}
