/**
 * Lead detail — the facts, the notes, and the one control that matters:
 * "convertir en expediente".
 *
 * Conversion is a single server transaction (`POST /leads/{id}/convert`) that
 * creates the insured, the client, an asset, the placement and the
 * `case_file(kind=account, stage=intake)`. The dialog therefore asks for the
 * one thing a lead may not have — a validated RUT — and hands the user
 * straight to the new expediente, where the next step is attaching the 00A.
 */
import * as React from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { ArrowLeft, ArrowRightLeft, CalendarClock, FolderOpen } from "lucide-react";

import { PageHeader } from "@/components/common/PageHeader";
import { NotesPanel } from "@/components/common/NotesPanel";
import { FadeUp } from "@/components/common/motion";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DisabledHint,
  ErrorBanner,
  KeyValue,
  MonoChip,
  Section,
  StatusBadge,
  uf,
} from "@/pages/proposals/shared";
import { useConvertLead, useLead, useUpdateLead } from "@/api/leads";
import { useCan } from "@/lib/permissions";
import { formatDate } from "@/lib/format";
import { LEAD_STATUSES, type Lead, type LeadStatus } from "@/api/types";

export default function LeadDetailPage() {
  const { leadId: leadIdParam } = useParams();
  const leadId = Number(leadIdParam);
  const { t } = useTranslation("leads");
  const { t: tc } = useTranslation("common");

  const lead = useLead(leadId);
  const update = useUpdateLead(leadId);
  const edit = useCan("Leads", "Edit");
  const comment = useCan("Leads", "Comment");
  const remove = useCan("Leads", "Delete");
  const createCase = useCan("CaseFiles", "Create");

  const [convertOpen, setConvertOpen] = React.useState(false);

  if (lead.isLoading) return <Skeleton className="h-64 w-full" />;
  if (lead.isError || !lead.data) return <ErrorBanner error={lead.error ?? tc("state.error")} />;

  const l = lead.data;
  const converted = l.status === "converted";
  const convertHint = converted
    ? t("convert.already")
    : !edit.allowed || !createCase.allowed
      ? t("convert.noPermission")
      : null;

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        eyebrow={
          <Link to="/leads" className="flex items-center gap-1.5 hover:text-brand-deep">
            <ArrowLeft className="h-3.5 w-3.5" />
            {t("title")}
          </Link>
        }
        title={l.name}
        subtitle={
          <span className="flex flex-wrap items-center gap-2">
            {l.rut ? <MonoChip>{l.rut}</MonoChip> : <span>{t("fields.noRut")}</span>}
            {l.insurance_line_name ? <span>· {l.insurance_line_name}</span> : null}
            {l.source ? <span>· {l.source}</span> : null}
          </span>
        }
        actions={
          <>
            <StatusBadge value={l.status} label={t(`statuses.${l.status}`)} />
            {l.converted_case_file_id ? (
              <Button size="sm" variant="secondary" asChild>
                <Link to={`/cases/${l.converted_case_file_id}`}>
                  <FolderOpen className="h-4 w-4" />
                  {t("convert.openCase")}
                </Link>
              </Button>
            ) : null}
            <DisabledHint hint={convertHint}>
              <Button size="sm" disabled={!!convertHint} onClick={() => setConvertOpen(true)}>
                <ArrowRightLeft className="h-4 w-4" />
                {t("convert.action")}
              </Button>
            </DisabledHint>
          </>
        }
      />

      <FadeUp>
        <Section title={t("detail.factsTitle")}>
          <div className="grid gap-5 sm:grid-cols-2 lg:grid-cols-4">
            <KeyValue label={t("fields.contactName")} value={l.contact_name ?? "—"} />
            <KeyValue label={t("fields.contactEmail")} value={l.contact_email ?? "—"} />
            <KeyValue label={t("fields.contactPhone")} value={l.contact_phone ?? "—"} />
            <KeyValue label={t("fields.source")} value={l.source ?? "—"} />
            <KeyValue
              label={t("fields.estimatedPremium")}
              value={uf(l.estimated_premium_uf)}
            />
            <KeyValue
              label={t("fields.followUp")}
              value={
                l.follow_up_on ? (
                  <Badge variant="action" className="gap-1">
                    <CalendarClock className="h-3 w-3" />
                    {formatDate(l.follow_up_on)}
                  </Badge>
                ) : (
                  "—"
                )
              }
            />
            <KeyValue label={t("fields.lostReason")} value={l.lost_reason ?? "—"} />
            <KeyValue label={t("fields.notes")} value={String(l.notes_count)} />
          </div>

          {l.summary ? (
            <p className="mt-4 whitespace-pre-wrap text-body text-text-secondary">{l.summary}</p>
          ) : null}
        </Section>
      </FadeUp>

      <FadeUp delay={0.04}>
        <Section title={t("detail.statusTitle")} description={t("detail.statusHint")}>
          <div className="flex flex-wrap gap-2">
            {LEAD_STATUSES.filter((s) => s !== "converted").map((status) => (
              <DisabledHint key={status} hint={edit.allowed ? null : t("noEditPermission")}>
                <Button
                  size="sm"
                  variant={l.status === status ? "teal" : "secondary"}
                  disabled={!edit.allowed || update.isPending || converted}
                  onClick={() => update.mutate({ status: status as LeadStatus })}
                >
                  {t(`statuses.${status}`)}
                </Button>
              </DisabledHint>
            ))}
          </div>
          {update.isError ? <ErrorBanner error={update.error} className="mt-3" /> : null}
        </Section>
      </FadeUp>

      <FadeUp delay={0.08}>
        <Section title={t("detail.notesTitle")}>
          <NotesPanel
            entityType="sales_lead"
            entityId={leadId}
            canComment={comment.allowed}
            canDelete={remove.allowed}
          />
        </Section>
      </FadeUp>

      <ConvertDialog
        lead={l}
        open={convertOpen}
        onOpenChange={setConvertOpen}
      />
    </div>
  );
}

function ConvertDialog({
  lead,
  open,
  onOpenChange,
}: {
  lead: Lead;
  open: boolean;
  onOpenChange: (value: boolean) => void;
}) {
  const { t } = useTranslation("leads");
  const { t: tc } = useTranslation("common");
  const navigate = useNavigate();
  const convert = useConvertLead(lead.id);

  const [rut, setRut] = React.useState(lead.rut ?? "");
  const [legalName, setLegalName] = React.useState(lead.name);
  const [title, setTitle] = React.useState("");
  const [assetName, setAssetName] = React.useState("");
  const [periodStart, setPeriodStart] = React.useState("");
  const [periodEnd, setPeriodEnd] = React.useState("");

  const submit = () => {
    convert.mutate(
      {
        insured_rut: rut.trim(),
        insured_legal_name: legalName.trim(),
        insurance_line_id: lead.insurance_line_id,
        title: title.trim() || null,
        period_start: periodStart || null,
        period_end: periodEnd || null,
        asset: assetName.trim() ? { name: assetName.trim() } : null,
      },
      {
        onSuccess: (result) => {
          onOpenChange(false);
          navigate(`/cases/${result.case_file_id}`);
        },
      },
    );
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>{t("convert.title")}</DialogTitle>
          <DialogDescription>{t("convert.description")}</DialogDescription>
        </DialogHeader>

        <div className="grid gap-3 sm:grid-cols-2">
          <div>
            <Label htmlFor="cv-rut">{t("convert.rut")}</Label>
            <Input id="cv-rut" value={rut} onChange={(e) => setRut(e.target.value)} />
            <p className="mt-1 text-caption text-text-muted">{t("convert.rutHint")}</p>
          </div>
          <div>
            <Label htmlFor="cv-name">{t("convert.legalName")}</Label>
            <Input
              id="cv-name"
              value={legalName}
              onChange={(e) => setLegalName(e.target.value)}
            />
          </div>
          <div className="sm:col-span-2">
            <Label htmlFor="cv-title">{t("convert.caseTitle")}</Label>
            <Input id="cv-title" value={title} onChange={(e) => setTitle(e.target.value)} />
          </div>
          <div className="sm:col-span-2">
            <Label htmlFor="cv-asset">{t("convert.assetName")}</Label>
            <Input
              id="cv-asset"
              value={assetName}
              onChange={(e) => setAssetName(e.target.value)}
            />
          </div>
          <div>
            <Label htmlFor="cv-start">{t("convert.periodStart")}</Label>
            <Input
              id="cv-start"
              type="date"
              value={periodStart}
              onChange={(e) => setPeriodStart(e.target.value)}
            />
          </div>
          <div>
            <Label htmlFor="cv-end">{t("convert.periodEnd")}</Label>
            <Input
              id="cv-end"
              type="date"
              value={periodEnd}
              onChange={(e) => setPeriodEnd(e.target.value)}
            />
          </div>
        </div>

        <p className="text-caption text-text-muted">{t("convert.periodRequired")}</p>

        {convert.isError ? <ErrorBanner error={convert.error} /> : null}

        <DialogFooter>
          <Button variant="secondary" onClick={() => onOpenChange(false)}>
            {tc("actions.cancel")}
          </Button>
          <Button
            disabled={
              !rut.trim() ||
              !legalName.trim() ||
              // Rule 1 — the vigencia IS the folder: the server refuses a
              // period-less account, so the button must too rather than send
              // the user into a 422 they cannot read.
              !periodStart ||
              !periodEnd ||
              convert.isPending
            }
            onClick={submit}
          >
            {convert.isPending ? tc("actions.loading") : t("convert.action")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
