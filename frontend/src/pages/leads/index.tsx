/**
 * The lead pipeline.
 *
 * A lead is a data point, not a client: no files, no placement, an optional RUT
 * and one follow-up date. The board is one column per `LeadStatus`; the status
 * itself is moved with a PATCH from the card, because there is no lead stage
 * machine to ask — `Leads.Edit` is the whole gate.
 */
import * as React from "react";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { AlarmClock, CalendarClock, Plus, Sparkles, Users } from "lucide-react";

import { PageHeader, type EmbeddablePageProps } from "@/components/common/PageHeader";
import { KpiCard } from "@/components/common/KpiCard";
import { FadeUp } from "@/components/common/motion";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
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
import { DisabledHint, ErrorBanner, MonoChip, uf } from "@/pages/proposals/shared";
import { useCreateLead, useLeads, useLeadsSummary, useUpdateLead } from "@/api/leads";
import { useCan } from "@/lib/permissions";
import { formatDate } from "@/lib/format";
import { LEAD_STATUSES, type Lead, type LeadStatus } from "@/api/types";

const TONE: Record<LeadStatus, "neutral" | "action" | "brand" | "success" | "muted"> = {
  new: "neutral",
  contacted: "action",
  qualified: "brand",
  converted: "success",
  lost: "muted",
};

export default function LeadsPage({ embedded }: EmbeddablePageProps = {}) {
  const { t } = useTranslation("leads");
  const navigate = useNavigate();

  const summary = useLeadsSummary();
  const list = useLeads({ limit: 200 });
  const create = useCan("Leads", "Create");
  const edit = useCan("Leads", "Edit");

  const [open, setOpen] = React.useState(false);

  const byStatus = React.useMemo(() => {
    const map = new Map<LeadStatus, Lead[]>();
    for (const status of LEAD_STATUSES) map.set(status, []);
    for (const lead of list.data?.items ?? []) map.get(lead.status)?.push(lead);
    return map;
  }, [list.data]);

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        embedded={embedded}
        title={t("title")}
        subtitle={t("subtitle")}
        actions={
          <DisabledHint hint={create.allowed ? null : t("noCreatePermission")}>
            <Button size="sm" disabled={!create.allowed} onClick={() => setOpen(true)}>
              <Plus className="h-4 w-4" />
              {t("actions.new")}
            </Button>
          </DisabledHint>
        }
      />

      <FadeUp>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <KpiCard label={t("kpi.total")} countTo={summary.data?.total ?? 0} icon={<Users />} />
          <KpiCard
            label={t("kpi.qualified")}
            countTo={summary.data?.by_status?.qualified ?? 0}
            tone="brand"
            icon={<Sparkles />}
          />
          <KpiCard
            label={t("kpi.dueThisWeek")}
            countTo={summary.data?.due_this_week ?? 0}
            tone="action"
            icon={<CalendarClock />}
          />
          <KpiCard
            label={t("kpi.overdue")}
            countTo={summary.data?.overdue ?? 0}
            tone="danger"
            icon={<AlarmClock />}
          />
        </div>
      </FadeUp>

      {list.isError ? <ErrorBanner error={list.error} /> : null}

      <FadeUp delay={0.05}>
        <div className="grid gap-3 md:grid-cols-3 xl:grid-cols-5">
          {LEAD_STATUSES.map((status) => {
            const items = byStatus.get(status) ?? [];
            return (
              <div key={status} className="flex min-w-0 flex-col gap-2">
                <div className="flex items-center gap-2 px-1">
                  <Badge variant={TONE[status]}>{t(`statuses.${status}`)}</Badge>
                  <span className="text-caption tabular-nums text-text-muted">
                    {items.length}
                  </span>
                </div>

                {list.isLoading ? (
                  <Skeleton className="h-24 w-full" />
                ) : items.length === 0 ? (
                  <Card className="p-3 text-caption text-text-muted">{t("board.empty")}</Card>
                ) : (
                  items.map((lead) => (
                    <Card
                      key={lead.id}
                      interactive
                      className="cursor-pointer p-3"
                      onClick={() => navigate(`/leads/${lead.id}`)}
                    >
                      <p className="truncate text-body font-semibold text-text-primary">
                        {lead.name}
                      </p>
                      <p className="mt-0.5 truncate text-caption text-text-muted">
                        {lead.contact_name ?? lead.contact_email ?? t("board.noContact")}
                      </p>
                      <div className="mt-2 flex flex-wrap items-center gap-1.5">
                        {lead.rut ? <MonoChip>{lead.rut}</MonoChip> : null}
                        {lead.estimated_premium_uf ? (
                          <Badge variant="neutral">{uf(lead.estimated_premium_uf)}</Badge>
                        ) : null}
                        {lead.follow_up_on ? (
                          <Badge variant="action" className="gap-1">
                            <CalendarClock className="h-3 w-3" />
                            {formatDate(lead.follow_up_on)}
                          </Badge>
                        ) : null}
                      </div>
                      <MoveRow lead={lead} canEdit={edit.allowed} />
                    </Card>
                  ))
                )}
              </div>
            );
          })}
        </div>
      </FadeUp>

      <NewLeadDialog open={open} onOpenChange={setOpen} />
      <p className="text-caption text-text-muted">{t("board.dragHint")}</p>
    </div>
  );
}

/** Status moves are a plain PATCH — there is no lead stage machine to consult. */
function MoveRow({ lead, canEdit }: { lead: Lead; canEdit: boolean }) {
  const { t } = useTranslation("leads");
  const update = useUpdateLead(lead.id);
  const index = LEAD_STATUSES.indexOf(lead.status);
  const next = index >= 0 && index < 2 ? LEAD_STATUSES[index + 1] : null;

  if (!next) return null;

  return (
    <div className="mt-2 flex" onClick={(e) => e.stopPropagation()}>
      <DisabledHint hint={canEdit ? null : t("noEditPermission")}>
        <Button
          size="sm"
          variant="secondary"
          disabled={!canEdit || update.isPending}
          onClick={() => update.mutate({ status: next })}
        >
          {t("actions.moveTo", { status: t(`statuses.${next}`) })}
        </Button>
      </DisabledHint>
    </div>
  );
}

function NewLeadDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (value: boolean) => void;
}) {
  const { t } = useTranslation("leads");
  const { t: tc } = useTranslation("common");
  const create = useCreateLead();

  const [name, setName] = React.useState("");
  const [rut, setRut] = React.useState("");
  const [contactName, setContactName] = React.useState("");
  const [contactEmail, setContactEmail] = React.useState("");
  const [source, setSource] = React.useState("");
  const [premium, setPremium] = React.useState("");
  const [followUp, setFollowUp] = React.useState("");

  const submit = () => {
    create.mutate(
      {
        name: name.trim(),
        // The RUT is optional BY DESIGN — a broker is never blocked (rule 7).
        rut: rut.trim() || null,
        contact_name: contactName.trim() || null,
        contact_email: contactEmail.trim() || null,
        source: source.trim() || null,
        estimated_premium_uf: premium.trim() ? Number(premium) : null,
        follow_up_on: followUp || null,
      },
      {
        onSuccess: () => {
          onOpenChange(false);
          setName("");
          setRut("");
          setContactName("");
          setContactEmail("");
          setSource("");
          setPremium("");
          setFollowUp("");
        },
      },
    );
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>{t("dialog.newTitle")}</DialogTitle>
          <DialogDescription>{t("dialog.newDescription")}</DialogDescription>
        </DialogHeader>

        <div className="grid gap-3 sm:grid-cols-2">
          <div className="sm:col-span-2">
            <Label htmlFor="lead-name">{t("fields.name")}</Label>
            <Input id="lead-name" value={name} onChange={(e) => setName(e.target.value)} />
          </div>
          <div>
            <Label htmlFor="lead-rut">{t("fields.rutOptional")}</Label>
            <Input id="lead-rut" value={rut} onChange={(e) => setRut(e.target.value)} />
          </div>
          <div>
            <Label htmlFor="lead-source">{t("fields.source")}</Label>
            <Input id="lead-source" value={source} onChange={(e) => setSource(e.target.value)} />
          </div>
          <div>
            <Label htmlFor="lead-contact">{t("fields.contactName")}</Label>
            <Input
              id="lead-contact"
              value={contactName}
              onChange={(e) => setContactName(e.target.value)}
            />
          </div>
          <div>
            <Label htmlFor="lead-email">{t("fields.contactEmail")}</Label>
            <Input
              id="lead-email"
              type="email"
              value={contactEmail}
              onChange={(e) => setContactEmail(e.target.value)}
            />
          </div>
          <div>
            <Label htmlFor="lead-premium">{t("fields.estimatedPremium")}</Label>
            <Input
              id="lead-premium"
              type="number"
              step="0.01"
              value={premium}
              onChange={(e) => setPremium(e.target.value)}
              className="text-right tabular-nums"
            />
          </div>
          <div>
            <Label htmlFor="lead-followup">{t("fields.followUp")}</Label>
            <Input
              id="lead-followup"
              type="date"
              value={followUp}
              onChange={(e) => setFollowUp(e.target.value)}
            />
          </div>
        </div>

        {create.isError ? <ErrorBanner error={create.error} /> : null}

        <DialogFooter>
          <Button variant="secondary" onClick={() => onOpenChange(false)}>
            {tc("actions.cancel")}
          </Button>
          <Button disabled={!name.trim() || create.isPending} onClick={submit}>
            {create.isPending ? tc("actions.loading") : tc("actions.create")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
