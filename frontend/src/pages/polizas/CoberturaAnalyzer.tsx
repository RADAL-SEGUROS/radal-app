import { useTranslation } from "react-i18next";
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { estadoVariant } from "@/components/common/EstadoBadge";
import { coberturaEstado, formatPct, formatUF } from "@/lib/format";
import { cn } from "@/lib/utils";

interface Props {
  coberturaPct: number;
  sumaAseguradaUf: number;
}

/** Bloque 1 — Analizador de cobertura. Green (lime) when óptima 95–105%. */
export function CoberturaAnalyzer({ coberturaPct, sumaAseguradaUf }: Props) {
  const { t } = useTranslation("polizas");
  const estado = coberturaEstado(coberturaPct);

  const barColor =
    estado === "optima"
      ? "bg-lime"
      : estado === "infravalorada"
        ? "bg-signal-danger"
        : "bg-signal-warn";

  // Bar scale 0–140% so the 95–105 band sits mid-track.
  const scaleMax = 140;
  const clampedPct = Math.max(0, Math.min(coberturaPct, scaleMax));
  const width = (clampedPct / scaleMax) * 100;
  const optimoStart = (95 / scaleMax) * 100;
  const optimoWidth = ((105 - 95) / scaleMax) * 100;

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("detail.analizador.title")}</CardTitle>
        <CardDescription>{t("detail.analizador.subtitle")}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-5">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div>
            <p className="text-label text-text-muted">
              {t("detail.analizador.coberturaPct")}
            </p>
            <p
              className={cn(
                "font-display text-kpi tabular-nums",
                estado === "optima"
                  ? "text-lime"
                  : estado === "infravalorada"
                    ? "text-signal-danger"
                    : "text-signal-warn",
              )}
            >
              {formatPct(coberturaPct)}
            </p>
          </div>
          <div className="text-right">
            <p className="text-label text-text-muted">
              {t("detail.analizador.sumaAsegurada")}
            </p>
            <p className="font-mono text-mono text-text-primary">
              {formatUF(sumaAseguradaUf)}
            </p>
          </div>
        </div>

        <div>
          <div className="relative h-3 w-full overflow-hidden rounded-full bg-bg-recessed">
            {/* Óptima band marker */}
            <div
              className="absolute inset-y-0 bg-lime/25"
              style={{ left: `${optimoStart}%`, width: `${optimoWidth}%` }}
            />
            {/* Value fill */}
            <div
              className={cn("absolute inset-y-0 left-0 rounded-full", barColor)}
              style={{ width: `${width}%` }}
            />
          </div>
          <p className="mt-1.5 text-caption text-text-muted">
            {t("detail.analizador.rangoOptimo")}
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <span className="text-label text-text-muted">
            {t("detail.analizador.tipoCobertura")}:
          </span>
          <Badge variant={estadoVariant(estado)}>
            {t(`cobertura.${estado}`)}
          </Badge>
        </div>

        <p className="text-body text-text-secondary">
          {t(`detail.analizador.rules.${estado}`)}
        </p>
      </CardContent>
    </Card>
  );
}
