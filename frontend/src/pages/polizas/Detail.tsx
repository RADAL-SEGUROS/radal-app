import { useParams, useNavigate, Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import {
  ArrowLeft,
  CreditCard,
  RefreshCw,
  AlertTriangle,
  Pencil,
} from "lucide-react";
import api from "@/lib/api";
import { formatUF, formatPct, formatDate } from "@/lib/format";
import { PageHeader } from "@/components/common/PageHeader";
import { EstadoBadge } from "@/components/common/EstadoBadge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { CoberturaAnalyzer } from "./CoberturaAnalyzer";
import { CoaseguroBar } from "./CoaseguroBar";
import { UbicacionesTable } from "./UbicacionesTable";
import { Condiciones } from "./Condiciones";
import { CoberturasExclusiones } from "./CoberturasExclusiones";
import type { PolizaDetail } from "./types";

function HeaderField({
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

export default function PolizaDetail() {
  const { id } = useParams();
  const navigate = useNavigate();
  const { t } = useTranslation(["polizas", "common"]);

  const { data, isLoading, isError } = useQuery({
    queryKey: ["polizas", "detail", id],
    queryFn: async () => {
      const { data } = await api.get<PolizaDetail>(`/polizas/${id}`);
      return data;
    },
    enabled: !!id,
  });

  const backLink = (
    <Link
      to="/polizas"
      className="inline-flex items-center gap-1.5 text-label text-text-muted transition-colors hover:text-text-primary"
    >
      <ArrowLeft className="h-3.5 w-3.5" />
      {t("polizas:detail.back")}
    </Link>
  );

  if (isLoading) {
    return (
      <div className="flex flex-col gap-6">
        {backLink}
        <Skeleton className="h-10 w-64" />
        <Skeleton className="h-32 w-full" />
        <div className="grid gap-4 lg:grid-cols-2">
          <Skeleton className="h-64 w-full" />
          <Skeleton className="h-64 w-full" />
        </div>
      </div>
    );
  }

  if (isError || !data) {
    return (
      <div className="flex flex-col gap-6">
        {backLink}
        <Card>
          <CardContent className="p-8 text-center text-body text-text-muted">
            {t("polizas:detail.notFound")}
          </CardContent>
        </Card>
      </div>
    );
  }

  const pagoUrl = data.pago_url;

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        eyebrow={
          <span className="flex items-center gap-3">
            {t("polizas:detail.eyebrow")}
            <span className="font-mono text-blue">{data.numero_poliza}</span>
          </span>
        }
        title={data.cliente?.nombre ?? data.numero_poliza}
        subtitle={backLink}
        actions={
          <EstadoBadge
            estado={data.estado}
            label={t(`polizas:estado.${data.estado}`)}
          />
        }
      />

      {/* Cabecera: cliente / asegurado / compañía */}
      <Card>
        <CardContent className="grid grid-cols-2 gap-5 p-5 sm:grid-cols-3 lg:grid-cols-4">
          <HeaderField
            label={t("polizas:detail.header.cliente")}
            value={data.cliente?.nombre}
          />
          <HeaderField
            label={t("polizas:detail.header.asegurado")}
            value={data.cliente?.nombre}
          />
          <HeaderField
            label={t("polizas:detail.header.compania")}
            value={data.aseguradora?.nombre}
          />
          <HeaderField
            label={t("polizas:detail.header.ramo")}
            value={data.ramo?.nombre}
          />
          <HeaderField
            label={t("polizas:detail.header.vigencia")}
            value={`${formatDate(data.vigencia_inicio)} – ${formatDate(
              data.vigencia_fin,
            )}`}
          />
          <HeaderField
            label={t("polizas:detail.header.prima")}
            value={formatUF(data.prima_uf)}
            mono
          />
          <HeaderField
            label={t("polizas:detail.header.comision")}
            value={formatPct(data.comision_pct)}
            mono
          />
          <HeaderField
            label={t("polizas:detail.header.sumaAsegurada")}
            value={formatUF(data.suma_asegurada_uf)}
            mono
          />
        </CardContent>
      </Card>

      {/* Quick actions */}
      <div className="flex flex-wrap items-center gap-2">
        {pagoUrl ? (
          <Button variant="primary" asChild>
            <a href={pagoUrl} target="_blank" rel="noopener noreferrer">
              <CreditCard className="h-4 w-4" />
              {t("polizas:detail.actions.pagarCuota")}
            </a>
          </Button>
        ) : (
          <Button
            variant="primary"
            disabled
            title={t("polizas:detail.actions.sinPagoUrl")}
          >
            <CreditCard className="h-4 w-4" />
            {t("polizas:detail.actions.pagarCuota")}
          </Button>
        )}
        <Button variant="secondary" onClick={() => navigate("/renovaciones")}>
          <RefreshCw className="h-4 w-4" />
          {t("polizas:detail.actions.iniciarRenovacion")}
        </Button>
        <Button variant="secondary" onClick={() => navigate("/siniestros")}>
          <AlertTriangle className="h-4 w-4" />
          {t("polizas:detail.actions.reportarSiniestro")}
        </Button>
        <Button variant="ghost">
          <Pencil className="h-4 w-4" />
          {t("polizas:detail.actions.editarPoliza")}
        </Button>
      </div>

      {/* Bloque 1 + Bloque 2 */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <CoberturaAnalyzer
          coberturaPct={data.cobertura_pct}
          sumaAseguradaUf={data.suma_asegurada_uf}
        />
        <CoaseguroBar
          participaciones={data.coaseguro_participaciones ?? []}
          tieneCoaseguro={data.tiene_coaseguro}
          lider={data.aseguradora}
        />
      </div>

      {/* Bloque 3 */}
      <UbicacionesTable ubicaciones={data.ubicaciones ?? []} />

      {/* Bloque 4 */}
      <Condiciones
        deducibleTexto={data.deducible_texto}
        limiteIndemnizacionUf={data.limite_indemnizacion_uf}
        planPago={data.plan_pago ?? { cuotas: null, metodo: null }}
      />

      {/* Bloque 5 */}
      <CoberturasExclusiones
        coberturas={data.coberturas ?? []}
        exclusiones={data.exclusiones ?? []}
      />
    </div>
  );
}
