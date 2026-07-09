import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { CalendarClock } from "lucide-react";
import { EstadoBadge } from "@/components/common/EstadoBadge";
import { cn } from "@/lib/utils";
import { formatDate, formatUF, diasColorRenovacion } from "@/lib/format";
import { SectionCard, EmptyState } from "./SectionCard";
import type { ProximaRenovacion } from "./types";

const DIAS_COLOR_CLASS: Record<string, string> = {
  ambar: "text-signal-warn",
  gris: "text-text-muted",
  rojo: "text-signal-danger",
};

interface ProximasRenovacionesProps {
  items?: ProximaRenovacion[];
}

export function ProximasRenovaciones({ items }: ProximasRenovacionesProps) {
  const { t } = useTranslation("dashboard");
  const { t: tc } = useTranslation("common");
  const navigate = useNavigate();
  // 2-5 items sorted by fecha_vencimiento.
  const list = (items ?? [])
    .slice()
    .sort((a, b) => a.fecha_vencimiento.localeCompare(b.fecha_vencimiento))
    .slice(0, 5);

  return (
    <SectionCard
      title={t("proximasRenovaciones.title")}
      icon={<CalendarClock className="h-4 w-4" />}
      linkTo="/renovaciones"
      linkLabel={tc("actions.viewAllF")}
    >
      {list.length === 0 ? (
        <EmptyState>{t("proximasRenovaciones.empty")}</EmptyState>
      ) : (
        <ul className="divide-y divide-line">
          {list.map((ren) => {
            const color = diasColorRenovacion(ren.dias_restantes);
            return (
              <li key={ren.id}>
                <button
                  type="button"
                  onClick={() => navigate(`/renovaciones/${ren.id}`)}
                  className="flex w-full items-center gap-3 px-4 py-2.5 text-left transition-colors hover:bg-bg-recessed"
                >
                  <span className="min-w-0 flex-1">
                    <span className="flex items-center gap-2">
                      <span className="shrink-0 font-mono text-mono text-text-primary">
                        {ren.codigo}
                      </span>
                      <span className="truncate text-body text-text-secondary">
                        {ren.cliente}
                      </span>
                    </span>
                    <span className="mt-0.5 flex items-center gap-2">
                      <EstadoBadge
                        estado={ren.estado}
                        label={tc(`estados.${ren.estado}`, {
                          defaultValue: ren.estado,
                        })}
                      />
                      <span className="truncate text-caption text-text-muted">
                        {t("proximasRenovaciones.vence", {
                          fecha: formatDate(ren.fecha_vencimiento),
                        })}
                      </span>
                    </span>
                  </span>
                  <span className="shrink-0 text-right">
                    <span className="block font-mono text-mono text-text-primary tabular-nums">
                      {formatUF(ren.prima_defender_uf, { decimals: 0 })}
                    </span>
                    <span
                      className={cn(
                        "block font-mono text-mono-sm tabular-nums",
                        DIAS_COLOR_CLASS[color],
                      )}
                    >
                      {t("proximasRenovaciones.diasRestantes", {
                        count: ren.dias_restantes,
                      })}
                    </span>
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </SectionCard>
  );
}
