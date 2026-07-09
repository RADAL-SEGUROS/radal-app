import { useTranslation } from "react-i18next";
import { cn } from "@/lib/utils";
import type { DiasColor } from "./types";

const colorClass: Record<DiasColor, string> = {
  rojo: "text-signal-danger",
  ambar: "text-signal-warn",
  gris: "text-text-muted",
};

interface DiasRestantesProps {
  dias: number | null;
  /** Server-provided color; falls back to the renovaciones rule (ámbar <=60 else gris). */
  color?: DiasColor;
  className?: string;
}

/** Renders the días-restantes label colored per design-system §6.2. */
export function DiasRestantes({ dias, color, className }: DiasRestantesProps) {
  const { t } = useTranslation("renovaciones");

  const resolved: DiasColor =
    color ?? (dias !== null && dias <= 60 ? "ambar" : "gris");

  let label: string;
  if (dias === null) label = "—";
  else if (dias < 0) label = t("dias.vencida");
  else if (dias === 0) label = t("dias.vence_hoy");
  else label = t("dias.restantes", { count: dias });

  return (
    <span
      className={cn(
        "font-mono text-mono tabular-nums",
        colorClass[resolved],
        className,
      )}
    >
      {label}
    </span>
  );
}
