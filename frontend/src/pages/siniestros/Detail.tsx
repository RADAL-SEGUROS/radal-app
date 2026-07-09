import { Link, useNavigate, useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import {
  ArrowLeft,
  ChevronRight,
  FileText,
  MessageSquare,
} from "lucide-react";
import { toast } from "sonner";

import { PageHeader } from "@/components/common/PageHeader";
import { EstadoBadge } from "@/components/common/EstadoBadge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from "@/components/ui/accordion";
import { formatUF, formatDate, formatDateTime } from "@/lib/format";
import { EstadoTimeline } from "./EstadoTimeline";
import { EmptyLine, InfoRow, RoleDot, SectionHeader } from "./components";
import { useSiniestro, type SiniestroDetail as SiniestroDetailType } from "./api";

export default function SiniestroDetail() {
  const { id } = useParams();
  const { t } = useTranslation("siniestros");
  const navigate = useNavigate();
  const { data, isLoading, isError } = useSiniestro(id);

  const stub = (label: string) =>
    toast(t("detail.stub"), { description: label });

  const backLink = (
    <Link
      to="/siniestros"
      className="inline-flex items-center gap-1.5 text-label text-text-muted transition-colors hover:text-text-primary"
    >
      <ArrowLeft className="h-3.5 w-3.5" />
      {t("detail.back")}
    </Link>
  );

  if (isLoading) {
    return (
      <div className="flex flex-col gap-6">
        {backLink}
        <Skeleton className="h-10 w-64" />
        <Skeleton className="h-24 w-full" />
        <div className="grid gap-4 lg:grid-cols-2">
          <Skeleton className="h-48 w-full" />
          <Skeleton className="h-48 w-full" />
        </div>
      </div>
    );
  }

  if (isError || !data) {
    return (
      <div className="flex flex-col gap-6">
        {backLink}
        <Card className="p-8 text-center text-body text-text-muted">
          {t("detail.loadError")}
        </Card>
      </div>
    );
  }

  const s = data;
  const codigo = `SIN-${String(s.id).padStart(4, "0")}`;

  return (
    <div className="flex flex-col gap-6">
      {/* Cabecera */}
      <PageHeader
        eyebrow={
          <span className="flex items-center gap-3">
            {t("detail.eyebrow")}
            <span className="font-mono text-blue">{codigo}</span>
          </span>
        }
        title={s.cliente?.nombre ?? codigo}
        subtitle={backLink}
        actions={
          <EstadoBadge estado={s.estado} label={t(`estado.${s.estado}`)} />
        }
      />

      {/* Quick actions */}
      <div className="flex flex-wrap items-center gap-2">
        <Button variant="primary" onClick={() => stub(t("actions.avanzarEstado"))}>
          <ChevronRight className="h-4 w-4" />
          {t("actions.avanzarEstado")}
        </Button>
        <Button variant="secondary" onClick={() => stub(t("actions.cargarDocumento"))}>
          <FileText className="h-4 w-4" />
          {t("actions.cargarDocumento")}
        </Button>
        <Button variant="secondary" onClick={() => stub(t("actions.registrarLiquidacion"))}>
          <MessageSquare className="h-4 w-4" />
          {t("actions.registrarLiquidacion")}
        </Button>
      </div>

      {/* Estado timeline */}
      <EstadoTimeline estado={s.estado} />

      {/* Resumen + Evaluación/liquidación */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Card className="p-5">
          <p className="mb-4 text-h3 font-display text-text-primary">
            {t("detail.resumen.title")}
          </p>
          <div className="grid grid-cols-2 gap-4">
            <InfoRow label={t("detail.resumen.cliente")}>
              {s.cliente ? (
                <Link
                  to={`/clientes/${s.cliente.id}`}
                  className="text-blue hover:text-blue-deep hover:underline"
                >
                  {s.cliente.nombre}
                </Link>
              ) : (
                "—"
              )}
            </InfoRow>
            <InfoRow label={t("detail.resumen.poliza")} mono>
              {s.poliza ? (
                <Link
                  to={`/polizas/${s.poliza.id}`}
                  className="text-blue hover:text-blue-deep hover:underline"
                >
                  {s.poliza.numero_poliza}
                </Link>
              ) : (
                "—"
              )}
            </InfoRow>
            <InfoRow label={t("detail.resumen.tipo")}>
              {s.tipo ?? "—"}
            </InfoRow>
            <InfoRow label={t("detail.resumen.fechaEvento")} mono>
              {formatDate(s.fecha_evento)}
            </InfoRow>
            <div className="col-span-2">
              <InfoRow label={t("detail.resumen.descripcion")}>
                {s.descripcion ?? "—"}
              </InfoRow>
            </div>
          </div>
        </Card>

        <Card className="p-5">
          <p className="mb-4 text-h3 font-display text-text-primary">
            {t("detail.liquidacion.title")}
          </p>
          <div className="grid grid-cols-2 gap-4">
            <InfoRow label={t("detail.liquidacion.montoEstimado")} mono>
              {formatUF(s.monto_estimado_uf)}
            </InfoRow>
            <InfoRow label={t("detail.liquidacion.montoLiquidado")} mono>
              <span
                className={
                  s.monto_liquidado_uf != null ? "text-lime" : undefined
                }
              >
                {formatUF(s.monto_liquidado_uf)}
              </span>
            </InfoRow>
            <InfoRow label={t("detail.liquidacion.fechaLiquidacion")} mono>
              {formatDate(s.fecha_liquidacion)}
            </InfoRow>
            <InfoRow label={t("detail.liquidacion.estado")}>
              <EstadoBadge
                estado={s.estado}
                label={t(`estado.${s.estado}`)}
              />
            </InfoRow>
          </div>
        </Card>
      </div>

      {/* Póliza / activo context */}
      <ActivoContext siniestro={s} onOpenPoliza={(pid) => navigate(`/polizas/${pid}`)} />

      {/* Accordions: documentos / observaciones / actividad */}
      <Card className="px-5">
        <Accordion type="multiple" defaultValue={["documentos"]}>
          <DocumentosSection siniestro={s} />
          <ObservacionesSection siniestro={s} />
          <ActividadSection siniestro={s} />
        </Accordion>
      </Card>
    </div>
  );
}

/* ------------------------------------------------------------------ */

function ActivoContext({
  siniestro,
  onOpenPoliza,
}: {
  siniestro: SiniestroDetailType;
  onOpenPoliza: (id: number) => void;
}) {
  const { t } = useTranslation("siniestros");
  const activo = siniestro.activo as
    | { id: number; nombre: string; tipo_activo?: string | null; direccion?: string | null; estado?: string | null }
    | null
    | undefined;

  return (
    <Card className="p-5">
      <div className="mb-4 flex items-center justify-between gap-3">
        <p className="text-h3 font-display text-text-primary">
          {t("detail.contexto.title")}
        </p>
        {siniestro.poliza ? (
          <Button
            variant="ghost"
            size="sm"
            onClick={() => onOpenPoliza(siniestro.poliza!.id)}
          >
            {t("detail.contexto.verPoliza")}
            <ChevronRight className="h-4 w-4" />
          </Button>
        ) : null}
      </div>
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-3">
        <InfoRow label={t("detail.contexto.poliza")} mono>
          {siniestro.poliza?.numero_poliza ?? "—"}
        </InfoRow>
        <InfoRow label={t("detail.contexto.activo")}>
          {activo?.nombre ?? "—"}
        </InfoRow>
        <InfoRow label={t("detail.contexto.tipoActivo")}>
          {activo?.tipo_activo ?? "—"}
        </InfoRow>
        <InfoRow label={t("detail.contexto.direccion")}>
          {activo?.direccion ?? "—"}
        </InfoRow>
      </div>
    </Card>
  );
}

function DocumentosSection({ siniestro }: { siniestro: SiniestroDetailType }) {
  const { t } = useTranslation("siniestros");
  const items = siniestro.documentos ?? [];
  return (
    <AccordionItem value="documentos">
      <AccordionTrigger>
        <SectionHeader
          title={t("detail.sections.documentos")}
          count={items.length}
        />
      </AccordionTrigger>
      <AccordionContent>
        {items.length ? (
          <ul className="flex flex-col divide-y divide-line">
            {items.map((d) => (
              <li
                key={d.id}
                className="flex items-center justify-between gap-3 py-2.5"
              >
                <div className="flex min-w-0 items-center gap-3">
                  <FileText className="h-4 w-4 shrink-0 text-text-muted" />
                  <div className="min-w-0">
                    <div className="truncate text-body text-text-primary">
                      {d.nombre}
                    </div>
                    <div className="truncate font-mono text-mono-sm text-text-muted">
                      {d.tipo ?? "—"}
                      {d.version ? ` · v${d.version}` : ""} ·{" "}
                      {formatDate(d.created_at)}
                    </div>
                  </div>
                </div>
                {d.url ? (
                  <a
                    href={d.url}
                    target="_blank"
                    rel="noreferrer"
                    className="shrink-0 text-label text-blue hover:text-blue-deep hover:underline"
                  >
                    {t("detail.documentos.ver")}
                  </a>
                ) : null}
              </li>
            ))}
          </ul>
        ) : (
          <EmptyLine>{t("detail.documentos.empty")}</EmptyLine>
        )}
      </AccordionContent>
    </AccordionItem>
  );
}

function ObservacionesSection({
  siniestro,
}: {
  siniestro: SiniestroDetailType;
}) {
  const { t } = useTranslation("siniestros");
  const items = siniestro.observaciones ?? [];
  return (
    <AccordionItem value="observaciones">
      <AccordionTrigger>
        <SectionHeader
          title={t("detail.sections.observaciones")}
          count={items.length}
        />
      </AccordionTrigger>
      <AccordionContent>
        {items.length ? (
          <ul className="flex flex-col gap-3">
            {items.map((o) => (
              <li key={o.id} className="flex items-start gap-3">
                <MessageSquare className="mt-0.5 h-4 w-4 shrink-0 text-text-muted" />
                <div className="min-w-0">
                  <p className="text-body text-text-primary">{o.texto}</p>
                  <p className="font-mono text-mono-sm text-text-muted">
                    {formatDateTime(o.created_at)}
                    {o.autor ? ` · ${o.autor}` : ""}
                  </p>
                </div>
              </li>
            ))}
          </ul>
        ) : (
          <EmptyLine>{t("detail.observaciones.empty")}</EmptyLine>
        )}
      </AccordionContent>
    </AccordionItem>
  );
}

function ActividadSection({ siniestro }: { siniestro: SiniestroDetailType }) {
  const { t } = useTranslation("siniestros");
  const items = siniestro.actividad ?? [];
  return (
    <AccordionItem value="actividad" className="border-b-0">
      <AccordionTrigger>
        <SectionHeader
          title={t("detail.sections.actividad")}
          count={items.length}
        />
      </AccordionTrigger>
      <AccordionContent>
        {items.length ? (
          <ul className="flex flex-col gap-3">
            {items.map((a) => (
              <li key={a.id} className="flex items-start gap-3">
                <RoleDot rol={a.rol} />
                <div className="min-w-0">
                  <p className="text-body text-text-primary">
                    <span className="font-medium">{a.accion}</span>
                    {a.descripcion ? (
                      <span className="text-text-secondary">
                        {" "}
                        — {a.descripcion}
                      </span>
                    ) : null}
                  </p>
                  <p className="font-mono text-mono-sm text-text-muted">
                    {formatDateTime(a.created_at)}
                    {a.usuario ? ` · ${t("detail.actividad.por")} ${a.usuario}` : ""}
                  </p>
                </div>
              </li>
            ))}
          </ul>
        ) : (
          <EmptyLine>{t("detail.actividad.empty")}</EmptyLine>
        )}
      </AccordionContent>
    </AccordionItem>
  );
}
