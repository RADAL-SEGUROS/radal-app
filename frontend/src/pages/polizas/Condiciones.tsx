import { useTranslation } from "react-i18next";
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from "@/components/ui/card";
import { formatUF } from "@/lib/format";
import type { PlanPago } from "./types";

interface Props {
  deducibleTexto: string | null;
  limiteIndemnizacionUf: number | null;
  planPago: PlanPago;
}

function Field({
  label,
  value,
  mono,
}: {
  label: string;
  value: React.ReactNode;
  mono?: boolean;
}) {
  return (
    <div className="space-y-1">
      <p className="text-label text-text-muted">{label}</p>
      <p
        className={
          mono
            ? "font-mono text-mono text-text-primary"
            : "text-body text-text-primary"
        }
      >
        {value ?? "—"}
      </p>
    </div>
  );
}

/** Bloque 4 — Condiciones: deducible, límite, plan de pago. */
export function Condiciones({
  deducibleTexto,
  limiteIndemnizacionUf,
  planPago,
}: Props) {
  const { t } = useTranslation("polizas");

  const cuotas =
    planPago?.cuotas != null
      ? t("detail.condiciones.cuotasValor", { count: planPago.cuotas })
      : "—";

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("detail.condiciones.title")}</CardTitle>
        <CardDescription>{t("detail.condiciones.subtitle")}</CardDescription>
      </CardHeader>
      <CardContent className="grid grid-cols-1 gap-5 sm:grid-cols-2">
        <Field
          label={t("detail.condiciones.deducible")}
          value={deducibleTexto || "—"}
        />
        <Field
          label={t("detail.condiciones.limite")}
          value={
            limiteIndemnizacionUf != null ? formatUF(limiteIndemnizacionUf) : "—"
          }
          mono
        />
        <Field label={t("detail.condiciones.cuotas")} value={cuotas} />
        <Field
          label={t("detail.condiciones.metodo")}
          value={planPago?.metodo || "—"}
        />
      </CardContent>
    </Card>
  );
}
