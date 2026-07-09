import { useTranslation } from "react-i18next";
import { weekday, greetingPeriod } from "@/lib/format";
import type { DashboardSaludo } from "./types";

interface GreetingHeaderProps {
  saludo?: DashboardSaludo;
  loading?: boolean;
}

export function GreetingHeader({ saludo, loading }: GreetingHeaderProps) {
  const { t } = useTranslation("dashboard");

  // Prefer server-provided greeting; fall back to client-derived values so the
  // header still renders while loading or if the payload omits fields.
  const periodo = saludo?.periodo ?? greetingPeriod();
  const nombre = saludo?.nombre ?? "";
  const dia = saludo?.dia_semana ?? weekday();

  const saludoTexto = t(`greeting.${periodo}`);

  return (
    <div className="min-w-0">
      <h1 className="truncate font-display text-display text-text-primary">
        {loading || !nombre
          ? saludoTexto
          : t("greeting.line", { saludo: saludoTexto, nombre })}
      </h1>
      <p className="mt-0.5 text-body capitalize text-text-muted">
        {t("greeting.subtitle", { dia })}
      </p>
    </div>
  );
}
