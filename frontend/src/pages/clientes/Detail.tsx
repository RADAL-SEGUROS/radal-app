import { Link, useNavigate, useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import {
  ArrowLeft,
  FileText,
  Mail,
  MessageCircle,
  Pencil,
  Phone,
  Plus,
  ShieldAlert,
  User,
  Wallet,
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
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { formatUF, formatDate, formatDateTime, formatNumber } from "@/lib/format";
import { useCliente, type ClienteDetail as ClienteDetailType } from "./api";
import {
  CoberturaBadge,
  EmptyLine,
  InfoRow,
  PrioridadBadge,
  RoleDot,
} from "./components";

/** Build a wa.me link from a raw phone string. */
function whatsappHref(phone: string | null | undefined): string | null {
  if (!phone) return null;
  const digits = phone.replace(/[^\d+]/g, "").replace(/^\+/, "");
  return digits ? `https://wa.me/${digits}` : null;
}

export default function ClienteDetail() {
  const { id } = useParams();
  const { t } = useTranslation("clientes");
  const navigate = useNavigate();
  const { data, isLoading, isError } = useCliente(id);

  const stub = (label: string) =>
    toast(t("toast.underConstruction"), { description: label });

  if (isLoading) {
    return (
      <div className="flex flex-col gap-6">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-24 w-full" />
        <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
          {Array.from({ length: 5 }).map((_, i) => (
            <Skeleton key={i} className="h-28 w-full" />
          ))}
        </div>
      </div>
    );
  }

  if (isError || !data) {
    return (
      <div className="flex flex-col gap-6">
        <PageHeader
          eyebrow={
            <Link to="/clientes" className="inline-flex items-center gap-1 hover:text-blue">
              <ArrowLeft className="h-3.5 w-3.5" />
              {t("detail.back")}
            </Link>
          }
          title={t("detail.notFound")}
        />
        <Card className="p-8 text-center text-body text-text-muted">
          {t("detail.loadError")}
        </Card>
      </div>
    );
  }

  const cliente = data;
  const wa = whatsappHref(cliente.telefono);

  return (
    <div className="flex flex-col gap-6">
      {/* Cabecera */}
      <PageHeader
        eyebrow={
          <Link
            to="/clientes"
            className="inline-flex items-center gap-1 hover:text-blue"
          >
            <ArrowLeft className="h-3.5 w-3.5" />
            {t("detail.back")}
          </Link>
        }
        title={
          <span className="flex flex-wrap items-center gap-3">
            {cliente.nombre}
            <EstadoBadge
              estado={cliente.estado}
              label={t(`estado.${cliente.estado}`)}
            />
          </span>
        }
        subtitle={
          <span className="flex flex-wrap items-center gap-x-3 gap-y-1">
            <span className="font-mono text-mono text-text-secondary">
              {cliente.rut || "—"}
            </span>
            {cliente.sector ? (
              <span className="text-text-muted">· {cliente.sector}</span>
            ) : null}
          </span>
        }
      />

      {/* Quick actions */}
      <div className="flex flex-wrap gap-2">
        <Button
          variant="primary"
          onClick={() => stub(t("actions.agregarPoliza"))}
        >
          <Plus className="h-4 w-4" />
          {t("actions.agregarPoliza")}
        </Button>
        <Button
          variant="secondary"
          onClick={() => stub(t("actions.reportarSiniestro"))}
        >
          <ShieldAlert className="h-4 w-4" />
          {t("actions.reportarSiniestro")}
        </Button>
        <Button
          variant="secondary"
          onClick={() => stub(t("actions.editarDatos"))}
        >
          <Pencil className="h-4 w-4" />
          {t("actions.editarDatos")}
        </Button>
      </div>

      {/* Info cards */}
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
        <Card className="flex items-start gap-3 p-5">
          <div className="rounded-md bg-bg-recessed p-2 text-teal">
            <User className="h-4 w-4" />
          </div>
          <InfoRow label={t("detail.info.contacto")}>
            {cliente.contacto_principal || "—"}
          </InfoRow>
        </Card>

        <Card className="flex items-start gap-3 p-5">
          <div className="rounded-md bg-bg-recessed p-2 text-teal">
            <Phone className="h-4 w-4" />
          </div>
          <InfoRow label={t("detail.info.telefono")}>
            {wa ? (
              <a
                href={wa}
                target="_blank"
                rel="noreferrer"
                className="inline-flex items-center gap-1.5 font-mono text-mono text-blue hover:text-blue-deep hover:underline"
                title={t("actions.whatsapp")}
              >
                <MessageCircle className="h-3.5 w-3.5 text-lime" />
                {cliente.telefono}
              </a>
            ) : (
              "—"
            )}
          </InfoRow>
        </Card>

        <Card className="flex items-start gap-3 p-5">
          <div className="rounded-md bg-bg-recessed p-2 text-teal">
            <Mail className="h-4 w-4" />
          </div>
          <InfoRow label={t("detail.info.email")}>
            {cliente.email ? (
              <a
                href={`mailto:${cliente.email}`}
                className="text-blue hover:text-blue-deep hover:underline"
              >
                {cliente.email}
              </a>
            ) : (
              "—"
            )}
          </InfoRow>
        </Card>

        <Card className="flex items-start gap-3 p-5">
          <div className="rounded-md bg-bg-recessed p-2 text-teal">
            <User className="h-4 w-4" />
          </div>
          <InfoRow label={t("detail.info.ejecutivo")}>
            {cliente.ejecutivo?.nombre || t("detail.info.sinEjecutivo")}
          </InfoRow>
        </Card>

        <Card className="flex items-start gap-3 p-5 md:col-span-2 xl:col-span-2">
          <div className="rounded-md bg-bg-recessed p-2 text-teal">
            <Wallet className="h-4 w-4" />
          </div>
          <div className="min-w-0 flex-1">
            <p className="mb-3 text-caption text-text-muted">
              {t("detail.info.cartera")}
            </p>
            <div className="grid grid-cols-2 gap-4 sm:grid-cols-3">
              <InfoRow label={t("detail.info.polizasVigentes")} mono>
                {formatNumber(cliente.kpis.polizas_vigentes)}
              </InfoRow>
              <InfoRow label={t("detail.info.primaCartera")} mono>
                {formatUF(cliente.kpis.prima_total_uf)}
              </InfoRow>
              <InfoRow label={t("detail.info.sumaAsegurada")} mono>
                {formatUF(cliente.kpis.suma_asegurada_uf)}
              </InfoRow>
              <InfoRow label={t("detail.info.activos")} mono>
                {formatNumber(cliente.kpis.activos_count)}
              </InfoRow>
              <InfoRow label={t("detail.info.siniestrosAbiertos")} mono>
                {formatNumber(cliente.kpis.siniestros_abiertos)}
              </InfoRow>
            </div>
          </div>
        </Card>
      </div>

      {/* Accordions */}
      <Card className="px-5">
        <Accordion type="multiple" defaultValue={["polizas"]}>
          <PolizasSection cliente={cliente} onAdd={() => stub(t("detail.polizas.agregar"))} />
          <AseguradosSection cliente={cliente} />
          <CotizacionesSection cliente={cliente} />
          <DocumentosSection cliente={cliente} />
          <SiniestrosSection cliente={cliente} onOpen={(sid) => navigate(`/siniestros/${sid}`)} />
          <ActividadSection cliente={cliente} />
        </Accordion>
      </Card>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Accordion sections                                                  */
/* ------------------------------------------------------------------ */

function SectionHeader({
  title,
  count,
}: {
  title: string;
  count?: number;
}) {
  return (
    <span className="flex items-center gap-2">
      {title}
      {typeof count === "number" ? (
        <span className="font-mono text-mono-sm text-text-muted">({count})</span>
      ) : null}
    </span>
  );
}

function PolizasSection({
  cliente,
  onAdd,
}: {
  cliente: ClienteDetailType;
  onAdd: () => void;
}) {
  const { t } = useTranslation("clientes");
  const navigate = useNavigate();
  const items = cliente.polizas ?? [];
  return (
    <AccordionItem value="polizas">
      <AccordionTrigger>
        <SectionHeader
          title={t("detail.sections.polizas")}
          count={items.length}
        />
      </AccordionTrigger>
      <AccordionContent>
        <div className="mb-3 flex justify-end">
          <Button variant="primary" size="sm" onClick={onAdd}>
            <Plus className="h-4 w-4" />
            {t("detail.polizas.agregar")}
          </Button>
        </div>
        {items.length ? (
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow className="hover:bg-transparent">
                  <TableHead>{t("detail.polizas.numero")}</TableHead>
                  <TableHead>{t("detail.polizas.ramo")}</TableHead>
                  <TableHead>{t("detail.polizas.aseguradora")}</TableHead>
                  <TableHead className="text-right">
                    {t("detail.polizas.prima")}
                  </TableHead>
                  <TableHead>{t("detail.polizas.cobertura")}</TableHead>
                  <TableHead>{t("detail.polizas.vigencia")}</TableHead>
                  <TableHead>{t("detail.polizas.estado")}</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {items.map((p) => (
                  <TableRow
                    key={p.id}
                    className="cursor-pointer"
                    onClick={() => navigate(`/polizas/${p.id}`)}
                  >
                    <TableCell className="font-mono text-mono text-text-secondary">
                      {p.numero_poliza}
                    </TableCell>
                    <TableCell>{p.ramo?.nombre ?? "—"}</TableCell>
                    <TableCell>{p.aseguradora?.nombre ?? "—"}</TableCell>
                    <TableCell className="text-right font-mono text-mono tabular-nums">
                      {formatUF(p.prima_uf)}
                    </TableCell>
                    <TableCell>
                      <CoberturaBadge
                        tipo={p.tipo_cobertura ?? p.cobertura_estado}
                        pct={p.cobertura_pct}
                      />
                    </TableCell>
                    <TableCell className="whitespace-nowrap font-mono text-mono-sm text-text-muted">
                      {formatDate(p.vigencia_inicio)} – {formatDate(p.vigencia_fin)}
                    </TableCell>
                    <TableCell>
                      <EstadoBadge estado={p.estado} label={t(`common:estados.${p.estado}`)} />
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        ) : (
          <EmptyLine>{t("detail.polizas.empty")}</EmptyLine>
        )}
      </AccordionContent>
    </AccordionItem>
  );
}

function AseguradosSection({ cliente }: { cliente: ClienteDetailType }) {
  const { t } = useTranslation("clientes");
  const items = cliente.asegurados_adicionales_items ?? [];
  return (
    <AccordionItem value="asegurados">
      <AccordionTrigger>
        <SectionHeader
          title={t("detail.sections.aseguradosAdicionales")}
          count={items.length}
        />
      </AccordionTrigger>
      <AccordionContent>
        {items.length ? (
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow className="hover:bg-transparent">
                  <TableHead>{t("detail.aseguradosAdicionales.rut")}</TableHead>
                  <TableHead>{t("detail.aseguradosAdicionales.entidad")}</TableHead>
                  <TableHead>{t("detail.aseguradosAdicionales.tipoSeguro")}</TableHead>
                  <TableHead>{t("detail.aseguradosAdicionales.relacionBien")}</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {items.map((a) => (
                  <TableRow key={a.id} className="hover:bg-transparent">
                    <TableCell className="font-mono text-mono text-text-secondary">
                      {a.rut}
                    </TableCell>
                    <TableCell>{a.entidad ?? "—"}</TableCell>
                    <TableCell>{a.tipo_seguro ?? "—"}</TableCell>
                    <TableCell>{a.relacion_bien ?? "—"}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        ) : (
          <EmptyLine>{t("detail.aseguradosAdicionales.empty")}</EmptyLine>
        )}
      </AccordionContent>
    </AccordionItem>
  );
}

function CotizacionesSection({ cliente }: { cliente: ClienteDetailType }) {
  const { t } = useTranslation("clientes");
  const items = cliente.cotizaciones ?? [];
  return (
    <AccordionItem value="cotizaciones">
      <AccordionTrigger>
        <SectionHeader
          title={t("detail.sections.cotizaciones")}
          count={items.length}
        />
      </AccordionTrigger>
      <AccordionContent>
        {items.length ? (
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow className="hover:bg-transparent">
                  <TableHead>{t("detail.cotizaciones.bien")}</TableHead>
                  <TableHead>{t("detail.cotizaciones.ramo")}</TableHead>
                  <TableHead className="text-right">
                    {t("detail.cotizaciones.valorDeclarado")}
                  </TableHead>
                  <TableHead>{t("detail.cotizaciones.fechaVence")}</TableHead>
                  <TableHead>{t("detail.cotizaciones.prioridad")}</TableHead>
                  <TableHead>{t("detail.cotizaciones.estado")}</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {items.map((c) => (
                  <TableRow key={c.id} className="hover:bg-transparent">
                    <TableCell>{c.bien_asegurar ?? "—"}</TableCell>
                    <TableCell>{c.ramo?.nombre ?? "—"}</TableCell>
                    <TableCell className="text-right font-mono text-mono tabular-nums">
                      {formatUF(c.valor_declarado_uf)}
                    </TableCell>
                    <TableCell className="whitespace-nowrap font-mono text-mono-sm text-text-muted">
                      {formatDate(c.fecha_vence)}
                    </TableCell>
                    <TableCell>
                      <PrioridadBadge prioridad={c.prioridad} />
                    </TableCell>
                    <TableCell>
                      <EstadoBadge estado={c.estado} label={t(`common:estados.${c.estado}`, c.estado)} />
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        ) : (
          <EmptyLine>{t("detail.cotizaciones.empty")}</EmptyLine>
        )}
      </AccordionContent>
    </AccordionItem>
  );
}

function DocumentosSection({ cliente }: { cliente: ClienteDetailType }) {
  const { t } = useTranslation("clientes");
  const items = cliente.documentos ?? [];
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

function SiniestrosSection({
  cliente,
  onOpen,
}: {
  cliente: ClienteDetailType;
  onOpen: (id: number) => void;
}) {
  const { t } = useTranslation("clientes");
  const items = cliente.siniestros ?? [];
  return (
    <AccordionItem value="siniestros">
      <AccordionTrigger>
        <SectionHeader
          title={t("detail.sections.siniestros")}
          count={items.length}
        />
      </AccordionTrigger>
      <AccordionContent>
        {items.length ? (
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow className="hover:bg-transparent">
                  <TableHead>{t("detail.siniestros.id")}</TableHead>
                  <TableHead>{t("detail.siniestros.poliza")}</TableHead>
                  <TableHead>{t("detail.siniestros.tipo")}</TableHead>
                  <TableHead>{t("detail.siniestros.fechaEvento")}</TableHead>
                  <TableHead>{t("detail.siniestros.estado")}</TableHead>
                  <TableHead className="text-right">
                    {t("detail.siniestros.montoEstimado")}
                  </TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {items.map((s) => (
                  <TableRow
                    key={s.id}
                    className="cursor-pointer"
                    onClick={() => onOpen(s.id)}
                  >
                    <TableCell className="font-mono text-mono text-text-secondary">
                      SIN-{String(s.id).padStart(4, "0")}
                    </TableCell>
                    <TableCell className="font-mono text-mono-sm text-text-muted">
                      {s.poliza?.numero_poliza ?? "—"}
                    </TableCell>
                    <TableCell>{s.tipo ?? "—"}</TableCell>
                    <TableCell className="whitespace-nowrap font-mono text-mono-sm text-text-muted">
                      {formatDate(s.fecha_evento)}
                    </TableCell>
                    <TableCell>
                      <EstadoBadge estado={s.estado} label={t(`common:estados.${s.estado}`, s.estado)} />
                    </TableCell>
                    <TableCell className="text-right font-mono text-mono tabular-nums">
                      {formatUF(s.monto_estimado_uf)}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        ) : (
          <EmptyLine>{t("detail.siniestros.empty")}</EmptyLine>
        )}
      </AccordionContent>
    </AccordionItem>
  );
}

function ActividadSection({ cliente }: { cliente: ClienteDetailType }) {
  const { t } = useTranslation("clientes");
  const items = cliente.actividad ?? [];
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
                      <span className="text-text-secondary"> — {a.descripcion}</span>
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
