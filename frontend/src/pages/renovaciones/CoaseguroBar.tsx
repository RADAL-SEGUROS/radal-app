import { useTranslation } from "react-i18next";
import { formatPct } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { CoaseguroParticipacion } from "./types";

/** Palette cycled across coaseguro participants (líder first, teal). */
const SEGMENT_CLASSES = [
  "bg-teal",
  "bg-blue",
  "bg-signal-warn",
  "bg-teal-deep",
  "bg-blue-deep",
];

interface CoaseguroBarProps {
  participaciones: CoaseguroParticipacion[];
}

/** Stacked 100% bar of coaseguro shares + a legend, per pólizas coaseguro block. */
export function CoaseguroBar({ participaciones }: CoaseguroBarProps) {
  const { t } = useTranslation("renovaciones");

  const ordered = [...participaciones].sort(
    (a, b) => Number(b.es_lider) - Number(a.es_lider) || b.porcentaje - a.porcentaje,
  );

  return (
    <div className="space-y-3">
      <div className="flex h-3 w-full overflow-hidden rounded-full bg-bg-recessed">
        {ordered.map((p, i) => (
          <div
            key={p.id ?? `${p.aseguradora.id}-${i}`}
            className={cn(SEGMENT_CLASSES[i % SEGMENT_CLASSES.length])}
            style={{ width: `${p.porcentaje}%` }}
            title={`${p.aseguradora.nombre} · ${formatPct(p.porcentaje)}`}
          />
        ))}
      </div>
      <ul className="space-y-1.5">
        {ordered.map((p, i) => (
          <li
            key={p.id ?? `${p.aseguradora.id}-${i}`}
            className="flex items-center justify-between gap-3 text-body"
          >
            <span className="flex items-center gap-2 min-w-0">
              <span
                className={cn(
                  "h-2.5 w-2.5 shrink-0 rounded-full",
                  SEGMENT_CLASSES[i % SEGMENT_CLASSES.length],
                )}
              />
              <span className="truncate text-text-primary">
                {p.aseguradora.nombre}
              </span>
              {p.es_lider ? (
                <span className="shrink-0 rounded-full bg-teal-soft px-2 py-0.5 font-mono text-mono-sm text-teal-deep">
                  {t("detail.coaseguro.lider")}
                </span>
              ) : null}
            </span>
            <span className="shrink-0 font-mono text-mono tabular-nums text-text-secondary">
              {formatPct(p.porcentaje)}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
