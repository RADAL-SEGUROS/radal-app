import * as React from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import type { AxiosError } from "axios";
import {
  ArrowLeft,
  Building2,
  CalendarClock,
  Eye,
  FileText,
  MapPin,
  MessageSquare,
  Play,
  Send,
  ShieldCheck,
  UserRound,
} from "lucide-react";

import api from "@/lib/api";
import { formatDate, formatDateTime } from "@/lib/format";
import { PageHeader } from "@/components/common/PageHeader";
import { EstadoBadge } from "@/components/common/EstadoBadge";
import { Button } from "@/components/ui/button";
import { Badge, type BadgeProps } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

import { Checklist } from "./Checklist";
import type {
  InspeccionDetail as Inspeccion,
  InspeccionEstado,
  Urgencia,
} from "./types";

const URGENCIA_TONE: Record<string, NonNullable<BadgeProps["variant"]>> = {
  alta: "danger",
  media: "action",
  baja: "muted",
};

/** Lifecycle transitions: current estado -> next actionable estados. */
const NEXT_ESTADOS: Record<InspeccionEstado, InspeccionEstado[]> = {
  solicitada: ["asignada"],
  asignada: ["en_progreso"],
  en_progreso: ["enviada"],
  enviada: ["observada", "validada"],
  observada: ["en_progreso"],
  validada: ["cerrada"],
  cerrada: [],
};

const ACTION_ICON: Partial<Record<InspeccionEstado, React.ReactNode>> = {
  asignada: <UserRound className="h-4 w-4" />,
  en_progreso: <Play className="h-4 w-4" />,
  enviada: <Send className="h-4 w-4" />,
  observada: <Eye className="h-4 w-4" />,
  validada: <ShieldCheck className="h-4 w-4" />,
  cerrada: <ShieldCheck className="h-4 w-4" />,
};

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

function nombreOf(ref: unknown): string | null {
  if (!ref) return null;
  if (typeof ref === "string") return ref;
  if (typeof ref === "object" && "nombre" in (ref as Record<string, unknown>)) {
    return String((ref as { nombre: unknown }).nombre);
  }
  return null;
}

export default function InspeccionDetail() {
  const { id } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { t } = useTranslation("inspecciones");
  const { t: tc } = useTranslation("common");

  const { data, isLoading, isError } = useQuery({
    queryKey: ["inspeccion", Number(id)],
    enabled: Boolean(id),
    queryFn: async () => {
      const res = await api.get<Inspeccion>(`/inspecciones/${id}`);
      return res.data;
    },
  });

  const advance = useMutation({
    mutationFn: async (estado: InspeccionEstado) => {
      const res = await api.patch(`/inspecciones/${id}`, { estado });
      return res.data;
    },
    onSuccess: (_res, estado) => {
      toast.success(
        t("detail.estadoAvanzado", { estado: t(`estados.${estado}`) }),
      );
      queryClient.invalidateQueries({ queryKey: ["inspeccion", Number(id)] });
      queryClient.invalidateQueries({ queryKey: ["inspecciones"] });
    },
    onError: (err: AxiosError<{ detail?: string }>) => {
      toast.error(err.response?.data?.detail ?? t("detail.estadoError"));
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
        <Button
          variant="ghost"
          size="sm"
          onClick={() => navigate("/inspecciones")}
        >
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

  const codigo = `INS-${String(data.id).padStart(4, "0")}`;
  const activo = data.activo_resumen ?? null;
  const solicitud = data.solicitud ?? null;
  const documentos = data.documentos ?? [];
  const observaciones = data.observaciones ?? [];
  const nextEstados = NEXT_ESTADOS[data.estado] ?? [];
  const urgencia = (data.urgencia ?? solicitud?.urgencia ?? null) as
    | Urgencia
    | null;

  return (
    <div className="space-y-6">
      <Button
        variant="ghost"
        size="sm"
        onClick={() => navigate("/inspecciones")}
      >
        <ArrowLeft className="h-4 w-4" />
        {t("detail.back")}
      </Button>

      <PageHeader
        eyebrow={`${t("detail.eyebrow")} · ${codigo} · v${data.version}`}
        title={data.activo.nombre}
        subtitle={`${data.cliente.nombre}${
          data.ramo ? ` · ${data.ramo.nombre}` : ""
        }`}
        actions={
          <div className="flex flex-wrap items-center gap-2">
            {nextEstados.map((next, i) => (
              <Button
                key={next}
                variant={i === nextEstados.length - 1 ? "primary" : "secondary"}
                size="sm"
                disabled={advance.isPending}
                onClick={() => advance.mutate(next)}
              >
                {ACTION_ICON[next]}
                {t(`detail.actions.${next}`)}
              </Button>
            ))}
          </div>
        }
      />

      {/* Cabecera — quick facts strip */}
      <Card>
        <CardContent className="grid grid-cols-2 gap-4 p-5 sm:grid-cols-4">
          <div className="space-y-0.5">
            <p className="text-label text-text-muted">
              {t("detail.header.estado")}
            </p>
            <EstadoBadge
              estado={data.estado}
              label={t(`estados.${data.estado}`)}
            />
          </div>
          <div className="flex items-start gap-2">
            <UserRound className="mt-0.5 h-4 w-4 shrink-0 text-teal" />
            <div className="space-y-0.5">
              <p className="text-label text-text-muted">
                {t("detail.header.inspector")}
              </p>
              <p className="text-body text-text-primary">
                {data.inspector?.nombre ?? t("sinInspector")}
              </p>
            </div>
          </div>
          <div className="flex items-start gap-2">
            <CalendarClock className="mt-0.5 h-4 w-4 shrink-0 text-teal" />
            <div className="space-y-0.5">
              <p className="text-label text-text-muted">
                {t("detail.header.objetivo")}
              </p>
              <p className="font-mono text-mono text-text-primary">
                {formatDate(data.fecha_objetivo ?? solicitud?.fecha_objetivo)}
              </p>
            </div>
          </div>
          <div className="space-y-0.5">
            <p className="text-label text-text-muted">
              {t("detail.header.urgencia")}
            </p>
            {urgencia ? (
              <Badge
                variant={URGENCIA_TONE[urgencia] ?? "neutral"}
                className="w-fit"
              >
                {t(`urgencia.${urgencia}`)}
              </Badge>
            ) : (
              <span className="text-body text-text-muted">—</span>
            )}
          </div>
        </CardContent>
      </Card>

      <div className="grid gap-6 lg:grid-cols-3">
        {/* Left column */}
        <div className="space-y-6 lg:col-span-2">
          {/* Checklist */}
          <Card>
            <CardHeader>
              <CardTitle>{t("detail.checklist.title")}</CardTitle>
            </CardHeader>
            <CardContent>
              <Checklist checklist={data.checklist ?? null} />
            </CardContent>
          </Card>

          {/* Evidencia / documentos */}
          <Card>
            <CardHeader>
              <CardTitle>{t("detail.evidencia.title")}</CardTitle>
            </CardHeader>
            <CardContent>
              {documentos.length > 0 ? (
                <ul className="divide-y divide-line">
                  {documentos.map((doc) => (
                    <li
                      key={doc.id}
                      className="flex items-center justify-between gap-4 py-3 first:pt-0 last:pb-0"
                    >
                      <div className="flex min-w-0 items-start gap-2">
                        <FileText className="mt-0.5 h-4 w-4 shrink-0 text-text-muted" />
                        <div className="min-w-0">
                          <p className="truncate text-body text-text-primary">
                            {doc.nombre}
                          </p>
                          <p className="text-caption text-text-muted">
                            {[
                              doc.tipo,
                              doc.version != null
                                ? t("detail.evidencia.version", {
                                    version: doc.version,
                                  })
                                : null,
                              nombreOf(doc.autor),
                              doc.created_at ? formatDate(doc.created_at) : null,
                            ]
                              .filter(Boolean)
                              .join(" · ")}
                          </p>
                        </div>
                      </div>
                      {doc.url ? (
                        <Button variant="link" size="sm" asChild>
                          <a
                            href={doc.url}
                            target="_blank"
                            rel="noopener noreferrer"
                          >
                            {tc("actions.download")}
                          </a>
                        </Button>
                      ) : null}
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="text-body text-text-muted">
                  {t("detail.evidencia.empty")}
                </p>
              )}
            </CardContent>
          </Card>

          {/* Observaciones */}
          <Card>
            <CardHeader>
              <CardTitle>{t("detail.observaciones.title")}</CardTitle>
            </CardHeader>
            <CardContent>
              {observaciones.length > 0 ? (
                <ul className="space-y-4">
                  {observaciones.map((o) => (
                    <li key={o.id} className="flex items-start gap-3">
                      <MessageSquare className="mt-0.5 h-4 w-4 shrink-0 text-teal" />
                      <div className="min-w-0 space-y-0.5">
                        <p className="text-body text-text-primary">{o.texto}</p>
                        <p className="text-caption text-text-muted">
                          {[
                            nombreOf(o.autor),
                            o.created_at ? formatDateTime(o.created_at) : null,
                          ]
                            .filter(Boolean)
                            .join(" · ")}
                        </p>
                      </div>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="text-body text-text-muted">
                  {t("detail.observaciones.empty")}
                </p>
              )}
            </CardContent>
          </Card>
        </div>

        {/* Right column — Activo context + Solicitud */}
        <div className="space-y-6">
          {/* Activo context */}
          <Card>
            <CardHeader>
              <CardTitle>{t("detail.activo.title")}</CardTitle>
            </CardHeader>
            <CardContent>
              <dl className="space-y-4">
                <Field label={t("detail.activo.nombre")}>
                  {activo?.nombre ?? data.activo.nombre}
                </Field>
                <Field label={t("detail.activo.cliente")}>
                  {data.cliente.nombre}
                </Field>
                {activo?.tipo_activo ? (
                  <Field label={t("detail.activo.tipo")}>
                    <span className="inline-flex items-center gap-2">
                      <Building2 className="h-4 w-4 text-text-muted" />
                      {activo.tipo_activo}
                    </span>
                  </Field>
                ) : null}
                {activo?.direccion ? (
                  <Field label={t("detail.activo.direccion")}>
                    <span className="inline-flex items-start gap-2">
                      <MapPin className="mt-0.5 h-4 w-4 shrink-0 text-text-muted" />
                      {activo.direccion}
                    </span>
                  </Field>
                ) : null}
                {activo?.estado ? (
                  <Field label={t("detail.activo.estado")}>
                    <EstadoBadge
                      estado={activo.estado}
                      label={t(`activoEstados.${activo.estado}`, {
                        defaultValue: activo.estado,
                      })}
                    />
                  </Field>
                ) : null}
              </dl>
            </CardContent>
          </Card>

          {/* Solicitud */}
          <Card>
            <CardHeader>
              <CardTitle>{t("detail.solicitud.title")}</CardTitle>
            </CardHeader>
            <CardContent>
              {solicitud ? (
                <dl className="space-y-4">
                  <Field label={t("detail.solicitud.motivo")}>
                    {solicitud.motivo ?? "—"}
                  </Field>
                  <Field label={t("detail.solicitud.urgencia")}>
                    {solicitud.urgencia
                      ? t(`urgencia.${solicitud.urgencia}`, {
                          defaultValue: String(solicitud.urgencia),
                        })
                      : "—"}
                  </Field>
                  <Field label={t("detail.solicitud.fechaObjetivo")} mono>
                    {formatDate(solicitud.fecha_objetivo)}
                  </Field>
                  {solicitud.estado ? (
                    <Field label={t("detail.solicitud.estado")}>
                      <EstadoBadge
                        estado={String(solicitud.estado)}
                        label={t(`solicitudEstados.${solicitud.estado}`, {
                          defaultValue: String(solicitud.estado),
                        })}
                      />
                    </Field>
                  ) : null}
                  {nombreOf(solicitud.created_by) ? (
                    <Field label={t("detail.solicitud.createdBy")}>
                      {nombreOf(solicitud.created_by)}
                    </Field>
                  ) : null}
                </dl>
              ) : (
                <p className="text-body text-text-muted">
                  {t("detail.solicitud.empty")}
                </p>
              )}
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}
