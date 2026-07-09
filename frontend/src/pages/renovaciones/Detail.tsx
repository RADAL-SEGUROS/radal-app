import * as React from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import {
  ArrowLeft,
  CalendarClock,
  ExternalLink,
  FileWarning,
  Handshake,
  ShieldCheck,
  UserRound,
} from "lucide-react";

import api from "@/lib/api";
import { formatDate, formatPct, formatUF } from "@/lib/format";
import { PageHeader } from "@/components/common/PageHeader";
import { EstadoBadge } from "@/components/common/EstadoBadge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

import { CoaseguroBar } from "./CoaseguroBar";
import { DiasRestantes } from "./DiasRestantes";
import { NegociacionEditor } from "./NegociacionEditor";
import type { RenovacionDetail as Renovacion } from "./types";

/** Label + value stacked row used across the detail blocks. */
function Field({
  label,
  children,
  mono,
}: {
  label: string;
  children: React.ReactNode;
  mono?: boolean;
}) {
  return (
    <div className="space-y-1">
      <dt className="text-label text-text-muted">{label}</dt>
      <dd
        className={cn(
          "text-body text-text-primary",
          mono && "font-mono text-mono tabular-nums",
        )}
      >
        {children}
      </dd>
    </div>
  );
}

export default function RenovacionDetail() {
  const { id } = useParams();
  const navigate = useNavigate();
  const { t } = useTranslation("renovaciones");
  const { t: tc } = useTranslation("common");

  const { data, isLoading, isError } = useQuery({
    queryKey: ["renovacion", Number(id)],
    enabled: Boolean(id),
    queryFn: async () => {
      const res = await api.get<Renovacion>(`/renovaciones/${id}`);
      return res.data;
    },
  });

  if (isLoading) {
    return (
      <div className="space-y-6">
        <Skeleton className="h-10 w-64" />
        <div className="grid gap-6 lg:grid-cols-3">
          <Skeleton className="h-64 lg:col-span-2" />
          <Skeleton className="h-64" />
        </div>
      </div>
    );
  }

  if (isError || !data) {
    return (
      <div className="space-y-6">
        <Button variant="ghost" size="sm" onClick={() => navigate("/renovaciones")}>
          <ArrowLeft className="h-4 w-4" />
          {t("detail.back")}
        </Button>
        <Card>
          <CardContent className="py-16 text-center text-body text-text-muted">
            {t("detail.notFound")}
          </CardContent>
        </Card>
      </div>
    );
  }

  const poliza = data.poliza_resumen ?? null;
  const coaseguro = poliza?.coaseguro_participaciones ?? [];
  const ubicaciones = poliza?.ubicaciones ?? [];
  const coberturas = poliza?.coberturas ?? [];
  const exclusiones = poliza?.exclusiones ?? [];

  return (
    <div className="space-y-6">
      <Button variant="ghost" size="sm" onClick={() => navigate("/renovaciones")}>
        <ArrowLeft className="h-4 w-4" />
        {t("detail.back")}
      </Button>

      <PageHeader
        eyebrow={`${t("detail.eyebrow")} · ${data.codigo}`}
        title={data.cliente.nombre}
        subtitle={`${data.ramo.nombre} · ${data.aseguradora.nombre}`}
        actions={
          <>
            <Button variant="secondary" size="sm">
              <FileWarning className="h-4 w-4" />
              {t("detail.actions.solicitarCotizacion")}
            </Button>
            <Button variant="secondary" size="sm">
              <ShieldCheck className="h-4 w-4" />
              {t("detail.actions.marcarRenovada")}
            </Button>
            <Button variant="primary" size="sm">
              <Handshake className="h-4 w-4" />
              {t("detail.actions.iniciarNegociacion")}
            </Button>
          </>
        }
      />

      {/* Cabecera — quick facts strip */}
      <Card>
        <CardContent className="grid grid-cols-2 gap-4 p-5 sm:grid-cols-4">
          <div className="flex items-start gap-2">
            <CalendarClock className="mt-0.5 h-4 w-4 shrink-0 text-teal" />
            <div className="space-y-0.5">
              <p className="text-label text-text-muted">{t("detail.header.vence")}</p>
              <p className="font-mono text-mono text-text-primary">
                {formatDate(data.fecha_vencimiento)}
              </p>
            </div>
          </div>
          <div className="space-y-0.5">
            <p className="text-label text-text-muted">
              {t("detail.header.diasRestantes")}
            </p>
            <DiasRestantes dias={data.dias_restantes} color={data.dias_color} />
          </div>
          <div className="space-y-0.5">
            <p className="text-label text-text-muted">
              {t("detail.resumen.primaDefender")}
            </p>
            <p className="font-mono text-mono tabular-nums text-text-primary">
              {formatUF(data.prima_defender_uf)}
            </p>
          </div>
          <div className="flex items-start gap-2">
            <UserRound className="mt-0.5 h-4 w-4 shrink-0 text-teal" />
            <div className="space-y-0.5">
              <p className="text-label text-text-muted">
                {t("detail.header.ejecutivo")}
              </p>
              <p className="text-body text-text-primary">
                {data.ejecutivo?.nombre ?? t("detail.header.sinEjecutivo")}
              </p>
            </div>
          </div>
        </CardContent>
      </Card>

      <div className="grid gap-6 lg:grid-cols-3">
        {/* Left column */}
        <div className="space-y-6 lg:col-span-2">
          {/* Resumen de la renovación */}
          <Card>
            <CardHeader>
              <CardTitle>{t("detail.resumen.title")}</CardTitle>
            </CardHeader>
            <CardContent>
              <dl className="grid grid-cols-1 gap-x-6 gap-y-4 sm:grid-cols-2">
                <Field label={t("detail.resumen.polizaOrigen")} mono>
                  {data.poliza?.numero_poliza ?? "—"}
                </Field>
                <Field label={t("detail.resumen.cliente")}>
                  {data.cliente.nombre}
                </Field>
                <Field label={t("detail.resumen.ramo")}>{data.ramo.nombre}</Field>
                <Field label={t("detail.resumen.aseguradora")}>
                  {data.aseguradora.nombre}
                </Field>
                <Field label={t("detail.resumen.aplicaCoaseguro")}>
                  {data.aplica_coaseguro ? tc("units.si") : tc("units.no")}
                </Field>
                <Field label={t("detail.resumen.comision")} mono>
                  {formatPct(data.comision_pct)}
                </Field>
                <Field label={t("detail.resumen.vencimiento")} mono>
                  {formatDate(data.fecha_vencimiento)}
                </Field>
                <Field label={t("detail.resumen.estado")}>
                  <EstadoBadge
                    estado={data.estado}
                    label={t(`estados.${data.estado}`)}
                  />
                </Field>
              </dl>
            </CardContent>
          </Card>

          {/* Resumen póliza vigente */}
          <Card>
            <CardHeader className="flex-row items-center justify-between space-y-0">
              <CardTitle>{t("detail.poliza.title")}</CardTitle>
              {poliza ? (
                <Button variant="link" size="sm" asChild>
                  <Link to={`/polizas/${poliza.id}`}>
                    {t("detail.poliza.verPoliza")}
                    <ExternalLink className="h-4 w-4" />
                  </Link>
                </Button>
              ) : null}
            </CardHeader>
            <CardContent className="space-y-5">
              {poliza ? (
                <>
                  <dl className="grid grid-cols-1 gap-x-6 gap-y-4 sm:grid-cols-2">
                    <Field label={t("detail.poliza.numero")} mono>
                      {poliza.numero_poliza}
                    </Field>
                    {poliza.bien_asegurar ? (
                      <Field label={t("detail.poliza.bien")}>
                        {poliza.bien_asegurar}
                      </Field>
                    ) : null}
                    <Field label={t("detail.poliza.suma")} mono>
                      {formatUF(poliza.suma_asegurada_uf)}
                    </Field>
                    <Field label={t("detail.poliza.limite")} mono>
                      {formatUF(poliza.limite_indemnizacion_uf)}
                    </Field>
                    {poliza.deducible_texto ? (
                      <Field label={t("detail.poliza.deducible")}>
                        {poliza.deducible_texto}
                      </Field>
                    ) : null}
                    {poliza.cobertura_estado ? (
                      <Field label={t("detail.poliza.cobertura")}>
                        <span className="flex items-center gap-2">
                          <EstadoBadge
                            estado={poliza.cobertura_estado}
                            label={tc(`cobertura.${poliza.cobertura_estado}`)}
                          />
                          {poliza.cobertura_pct != null ? (
                            <span className="font-mono text-mono tabular-nums text-text-secondary">
                              {formatPct(poliza.cobertura_pct)}
                            </span>
                          ) : null}
                        </span>
                      </Field>
                    ) : null}
                    {poliza.vigencia_inicio || poliza.vigencia_fin ? (
                      <Field label={t("detail.poliza.vigencia")} mono>
                        {formatDate(poliza.vigencia_inicio)} –{" "}
                        {formatDate(poliza.vigencia_fin)}
                      </Field>
                    ) : null}
                  </dl>

                  {/* Plan de pago */}
                  {poliza.plan_pago ? (
                    <div className="rounded-md border border-line bg-bg-recessed/60 p-4">
                      <p className="mb-3 text-label text-text-muted">
                        {t("detail.planPago.title")}
                      </p>
                      <div className="flex flex-wrap gap-x-8 gap-y-2">
                        <div>
                          <span className="text-caption text-text-muted">
                            {t("detail.planPago.cuotas")}:{" "}
                          </span>
                          <span className="font-mono text-mono text-text-primary">
                            {poliza.plan_pago.cuotas != null
                              ? t("detail.planPago.cuotas", {
                                  count: poliza.plan_pago.cuotas,
                                })
                              : "—"}
                          </span>
                        </div>
                        <div>
                          <span className="text-caption text-text-muted">
                            {t("detail.planPago.metodo")}:{" "}
                          </span>
                          <span className="text-body text-text-primary">
                            {poliza.plan_pago.metodo ?? "—"}
                          </span>
                        </div>
                      </div>
                    </div>
                  ) : null}
                </>
              ) : (
                <p className="text-body text-text-muted">
                  {t("detail.poliza.sinPoliza")}
                </p>
              )}
            </CardContent>
          </Card>

          {/* Coaseguro */}
          <Card>
            <CardHeader>
              <CardTitle>{t("detail.coaseguro.title")}</CardTitle>
            </CardHeader>
            <CardContent>
              {coaseguro.length > 0 ? (
                <CoaseguroBar participaciones={coaseguro} />
              ) : (
                <p className="text-body text-text-muted">
                  {t("detail.coaseguro.sinCoaseguro")}
                </p>
              )}
            </CardContent>
          </Card>

          {/* Ubicaciones */}
          <Card>
            <CardHeader>
              <CardTitle>{t("detail.ubicaciones.title")}</CardTitle>
            </CardHeader>
            <CardContent>
              {ubicaciones.length > 0 ? (
                <ul className="divide-y divide-line">
                  {ubicaciones.map((u, i) => (
                    <li
                      key={u.id ?? i}
                      className="flex items-start justify-between gap-4 py-3 first:pt-0 last:pb-0"
                    >
                      <div className="min-w-0">
                        <p className="text-body text-text-primary">{u.nombre}</p>
                        {u.direccion ? (
                          <p className="text-caption text-text-muted">
                            {u.direccion}
                          </p>
                        ) : null}
                      </div>
                      <div className="shrink-0 text-right">
                        <p className="font-mono text-mono tabular-nums text-text-primary">
                          {formatUF(u.suma_asegurada_uf)}
                        </p>
                        <p className="font-mono text-mono-sm tabular-nums text-text-muted">
                          {formatPct(u.porcentaje)}
                        </p>
                      </div>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="text-body text-text-muted">
                  {t("detail.ubicaciones.sinUbicaciones")}
                </p>
              )}
            </CardContent>
          </Card>

          {/* Coberturas y exclusiones */}
          <Card>
            <CardHeader>
              <CardTitle>{t("detail.coberturas.title")}</CardTitle>
            </CardHeader>
            <CardContent className="grid grid-cols-1 gap-6 sm:grid-cols-2">
              <div>
                <p className="mb-2 text-label text-text-muted">
                  {t("detail.coberturas.coberturas")}
                </p>
                {coberturas.length > 0 ? (
                  <ul className="space-y-1.5">
                    {coberturas.map((c, i) => (
                      <li
                        key={i}
                        className="flex items-start gap-2 text-body text-text-primary"
                      >
                        <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-lime" />
                        {c}
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="text-caption text-text-muted">
                    {t("detail.coberturas.sinCoberturas")}
                  </p>
                )}
              </div>
              <div>
                <p className="mb-2 text-label text-text-muted">
                  {t("detail.coberturas.exclusiones")}
                </p>
                {exclusiones.length > 0 ? (
                  <ul className="space-y-1.5">
                    {exclusiones.map((c, i) => (
                      <li
                        key={i}
                        className="flex items-start gap-2 text-body text-text-secondary"
                      >
                        <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-signal-danger" />
                        {c}
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="text-caption text-text-muted">
                    {t("detail.coberturas.sinExclusiones")}
                  </p>
                )}
              </div>
            </CardContent>
          </Card>
        </div>

        {/* Right column — Estado de negociación */}
        <div className="space-y-6">
          <NegociacionEditor
            renovacionId={data.id}
            value={data.estado_negociacion_texto ?? ""}
          />
        </div>
      </div>
    </div>
  );
}
