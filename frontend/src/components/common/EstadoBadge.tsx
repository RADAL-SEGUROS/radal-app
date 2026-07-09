import * as React from "react";
import { Badge, type BadgeProps } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

type BadgeVariant = NonNullable<BadgeProps["variant"]>;

/**
 * Maps any domain estado enum value to a badge variant family
 * (success/warn/danger/brand/action/neutral) per design-system §4.4.
 * Module agents pass the localized label via `children` (through t()).
 */
const ESTADO_VARIANT: Record<string, BadgeVariant> = {
  // Clientes
  activo: "success",
  onboarding: "action",
  prospecto: "brand",
  suspendido: "warn",
  archivado: "muted",

  // Pólizas
  vigente: "success",
  no_vigente: "muted",

  // Renovaciones
  por_iniciar: "neutral",
  cotizando: "action",
  negociando: "warn",

  // Cotizaciones
  pendiente: "warn",
  respondida: "success",

  // Cotizaciones prioridad
  baja: "muted",
  media: "action",
  alta: "danger",

  // Siniestros
  reportado: "action",
  en_documentacion: "warn",
  en_evaluacion: "warn",
  pre_liquidado: "brand",
  liquidado: "success",
  cerrado: "muted",

  // Inspecciones
  solicitada: "neutral",
  asignada: "action",
  en_progreso: "warn",
  enviada: "action",
  observada: "danger",
  validada: "success",

  // Coverage analyzer
  optima: "success",
  infravalorada: "danger",
  sobrevalorada: "warn",

  // Ofertas
  borrador: "neutral",
  ajustada: "action",
  aceptada: "success",
  rechazada: "danger",
};

export function estadoVariant(estado: string): BadgeVariant {
  return ESTADO_VARIANT[estado] ?? "neutral";
}

interface EstadoBadgeProps extends Omit<BadgeProps, "variant"> {
  /** Raw enum value; drives the color family. */
  estado: string;
  /** Localized label; defaults to the raw estado if omitted. */
  label?: React.ReactNode;
}

export function EstadoBadge({
  estado,
  label,
  className,
  children,
  ...props
}: EstadoBadgeProps) {
  return (
    <Badge
      variant={estadoVariant(estado)}
      className={cn(className)}
      {...props}
    >
      {label ?? children ?? estado}
    </Badge>
  );
}
