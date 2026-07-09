import * as React from "react";
import { useTranslation } from "react-i18next";
import { Badge } from "@/components/ui/badge";
import { EstadoBadge, estadoVariant } from "@/components/common/EstadoBadge";
import { coberturaEstado, type CoberturaEstado } from "@/lib/format";
import { cn } from "@/lib/utils";

/** Coverage analyzer badge (§6.1): infra rojo / óptima verde / sobre ámbar. */
export function CoberturaBadge({
  tipo,
  pct,
}: {
  tipo?: CoberturaEstado | null;
  pct?: number | null;
}) {
  const { t } = useTranslation("clientes");
  const estado: CoberturaEstado = tipo ?? coberturaEstado(pct ?? null);
  return (
    <EstadoBadge estado={estado} label={t(`cobertura.${estado}`)} />
  );
}

/** Small role-colored dot for activity rows (§6.4). */
export function RoleDot({ rol }: { rol?: string | null }) {
  const color = rol?.includes("aseguradora")
    ? "bg-blue"
    : rol?.includes("asegurado")
      ? "bg-lime"
      : "bg-teal";
  return (
    <span
      className={cn("mt-1.5 inline-block h-2 w-2 shrink-0 rounded-full", color)}
      aria-hidden
    />
  );
}

/** Priority badge for cotizaciones (baja/media/alta). */
export function PrioridadBadge({ prioridad }: { prioridad: string }) {
  const { t } = useTranslation("clientes");
  return (
    <Badge variant={estadoVariant(prioridad)}>{t(`prioridad.${prioridad}`)}</Badge>
  );
}

/** Labeled info line used inside detail info cards. */
export function InfoRow({
  label,
  children,
  mono,
}: {
  label: React.ReactNode;
  children: React.ReactNode;
  mono?: boolean;
}) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-caption text-text-muted">{label}</span>
      <span
        className={cn(
          "text-body text-text-primary",
          mono && "font-mono text-mono",
        )}
      >
        {children}
      </span>
    </div>
  );
}

/** Empty-state text used across accordion sections. */
export function EmptyLine({ children }: { children: React.ReactNode }) {
  return (
    <p className="py-2 text-body text-text-muted">{children}</p>
  );
}
