import { useTranslation } from "react-i18next";
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { formatPct } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { CoaseguroParticipacion, Ref } from "./types";

interface Props {
  participaciones: CoaseguroParticipacion[];
  tieneCoaseguro: boolean;
  lider?: Ref;
}

// Non-leader segment tints (subdivided teal family / neutrals per Aqua Spectrum).
const SEG_COLORS = ["bg-teal", "bg-blue", "bg-teal-deep", "bg-ink-3", "bg-signal-warn"];

/** Bloque 2 — Coaseguro proportional bar. Líder verde oscuro (pine); rest subdivided. */
export function CoaseguroBar({ participaciones, tieneCoaseguro, lider }: Props) {
  const { t } = useTranslation("polizas");

  const sorted = [...(participaciones ?? [])].sort(
    (a, b) => Number(b.es_lider) - Number(a.es_lider) || b.porcentaje - a.porcentaje,
  );

  // No coaseguro -> single 100% líder segment.
  const segments =
    !tieneCoaseguro || sorted.length === 0
      ? [
          {
            aseguradora: lider ?? { id: 0, nombre: t("detail.coaseguro.lider") },
            es_lider: true,
            porcentaje: 100,
          },
        ]
      : sorted;

  let nonLiderIdx = 0;

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("detail.coaseguro.title")}</CardTitle>
        <CardDescription>
          {tieneCoaseguro
            ? t("detail.coaseguro.subtitle")
            : t("detail.coaseguro.sinCoaseguroDetalle")}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex h-8 w-full overflow-hidden rounded-md">
          {segments.map((s, i) => {
            const color = s.es_lider
              ? "bg-teal-deep"
              : SEG_COLORS[nonLiderIdx++ % SEG_COLORS.length];
            return (
              <div
                key={`${s.aseguradora.id}-${i}`}
                className={cn("flex items-center justify-center", color)}
                style={{ width: `${s.porcentaje}%` }}
                title={`${s.aseguradora.nombre} · ${formatPct(s.porcentaje)}`}
              >
                {s.porcentaje >= 12 ? (
                  <span className="truncate px-1 font-mono text-mono-sm text-white">
                    {formatPct(s.porcentaje, 0)}
                  </span>
                ) : null}
              </div>
            );
          })}
        </div>

        <ul className="space-y-2">
          {segments.map((s, i) => (
            <li
              key={`row-${s.aseguradora.id}-${i}`}
              className="flex items-center justify-between gap-3"
            >
              <div className="flex min-w-0 items-center gap-2">
                <span
                  className={cn(
                    "h-2.5 w-2.5 shrink-0 rounded-full",
                    s.es_lider
                      ? "bg-teal-deep"
                      : SEG_COLORS[i % SEG_COLORS.length],
                  )}
                />
                <span className="truncate text-body text-text-primary">
                  {s.aseguradora.nombre}
                </span>
                {s.es_lider ? (
                  <Badge variant="brand">{t("detail.coaseguro.lider")}</Badge>
                ) : null}
              </div>
              <span className="font-mono text-mono text-text-primary">
                {formatPct(s.porcentaje)}
              </span>
            </li>
          ))}
        </ul>
      </CardContent>
    </Card>
  );
}
