import * as React from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { AxiosError } from "axios";
import {
  ArrowLeft,
  Briefcase,
  Building2,
  ChevronRight,
  Loader2,
  Mail,
  MapPin,
  Pencil,
  Phone,
  Plus,
  Trash2,
} from "lucide-react";
import { useClient, useDeleteClient } from "@/api/clients";
import { usePlacements } from "@/api/placements";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { PageHeader } from "@/components/common/PageHeader";
import { FadeUp, Stagger } from "@/components/common/motion";
import { formatDate } from "@/lib/format";
import { useCan } from "@/lib/permissions";
import { ClientStatusBadge } from "@/pages/clients/status";
import { EditClientDialog } from "@/pages/clients/ClientForm";
import { AssetsPanel } from "@/pages/clients/AssetsPanel";
import { DocumentsPanel } from "@/pages/clients/DocumentsPanel";
import { ActivityPanel } from "@/pages/clients/ActivityPanel";
import { SoonButton } from "@/pages/clients/Soon";
import { formatRut } from "@/pages/clients/rut";
import { DaysChip, PlacementStatusBadge } from "@/pages/placements/status";

function serverMessage(error: unknown, fallback: string): string {
  if (error instanceof AxiosError) {
    const detail = error.response?.data?.detail;
    if (typeof detail === "string") return detail;
  }
  return fallback;
}

function Stat({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div>
      <p className="font-display text-h2 tabular-nums text-text-primary">{value}</p>
      <p className="mt-0.5 text-caption text-text-muted">{label}</p>
    </div>
  );
}

function InfoRow({
  icon,
  label,
  value,
}: {
  icon?: React.ReactNode;
  label: string;
  value: React.ReactNode;
}) {
  return (
    <div className="flex items-start justify-between gap-4 border-b border-line py-2.5 last:border-0">
      <span className="flex items-center gap-2 text-caption text-text-muted">
        {icon}
        {label}
      </span>
      <span className="min-w-0 text-right text-body text-text-primary">
        {value || "—"}
      </span>
    </div>
  );
}

/** The placements of this client, as a compact list linking to their folders. */
function ClientPlacements({ clientId }: { clientId: number }) {
  const { t } = useTranslation(["clients", "placements"]);
  const placements = usePlacements({ client_id: clientId, page_size: 100 });
  const items = placements.data?.items ?? [];

  if (placements.isLoading) {
    return (
      <div className="flex flex-col gap-2">
        {[0, 1].map((i) => (
          <Skeleton key={i} className="h-16 w-full rounded-card" />
        ))}
      </div>
    );
  }

  if (items.length === 0) {
    return (
      <Card className="p-10 text-center text-body text-text-muted">
        {t("clients:placements.empty")}
      </Card>
    );
  }

  return (
    <Stagger className="flex flex-col gap-2">
      {items.map((placement) => (
        <FadeUp key={placement.id}>
          <Link to={`/placements/${placement.id}`} className="block">
            <Card interactive className="flex flex-wrap items-center gap-3 p-4">
              <span className="grid h-9 w-9 shrink-0 place-items-center rounded-[10px] bg-[color-mix(in_srgb,var(--blue)_12%,transparent)] text-blue-deep">
                <Briefcase className="h-[18px] w-[18px]" strokeWidth={1.75} />
              </span>
              <div className="min-w-0 flex-1">
                <p className="truncate text-label font-medium text-text-primary">
                  {placement.asset?.name ?? `#${placement.asset_id}`}
                </p>
                <p className="truncate text-caption text-text-muted">
                  {[placement.insurance_line?.name, placement.period]
                    .filter(Boolean)
                    .join(" · ") || "—"}
                </p>
              </div>
              <DaysChip days={placement.days_to_period_end} />
              <PlacementStatusBadge status={placement.status} />
              <ChevronRight className="h-4 w-4 text-text-muted" />
            </Card>
          </Link>
        </FadeUp>
      ))}
    </Stagger>
  );
}

/**
 * `/clients/:id` — one client folder: identity, assets, placements, documents
 * and a derived timeline.
 */
export default function ClientDetailPage() {
  const { t } = useTranslation(["clients", "common"]);
  const params = useParams();
  const navigate = useNavigate();
  const clientId = Number(params.id);

  const { data: client, isLoading, isError } = useClient(clientId);
  const remove = useDeleteClient();
  const canEdit = useCan("Clients", "Edit");
  const canDelete = useCan("Clients", "Delete");

  const [editOpen, setEditOpen] = React.useState(false);
  const [deleteOpen, setDeleteOpen] = React.useState(false);

  const onDelete = async () => {
    try {
      await remove.mutateAsync(clientId);
      toast.success(t("common:toast.deleted"));
      navigate("/clients");
    } catch (error) {
      toast.error(serverMessage(error, t("common:toast.error")));
    }
  };

  if (isLoading) {
    return (
      <div className="flex flex-col gap-4">
        <Skeleton className="h-10 w-72" />
        <Skeleton className="h-40 w-full rounded-card" />
        <Skeleton className="h-64 w-full rounded-card" />
      </div>
    );
  }

  if (isError || !client) {
    return (
      <Card className="flex flex-col items-center gap-3 p-14 text-center">
        <p className="text-body text-text-muted">{t("clients:notFound")}</p>
        <Button variant="secondary" onClick={() => navigate("/clients")}>
          <ArrowLeft />
          {t("common:actions.back")}
        </Button>
      </Card>
    );
  }

  const insured = client.insured;

  return (
    <div className="flex flex-col gap-[22px]">
      <PageHeader
        eyebrow={
          <Link to="/clients" className="inline-flex items-center gap-1.5 hover:text-teal-deep">
            <ArrowLeft className="h-3.5 w-3.5" />
            {t("clients:title")}
          </Link>
        }
        title={
          <span className="flex flex-wrap items-center gap-3">
            {insured.trade_name || insured.legal_name}
            <ClientStatusBadge status={client.status} />
          </span>
        }
        subtitle={
          <span className="font-mono text-mono">{formatRut(insured.rut)}</span>
        }
        actions={
          <>
            <Button
              variant="secondary"
              onClick={() => navigate(`/placements?client_id=${client.id}`)}
            >
              <Briefcase />
              {t("clients:actions.viewPlacements")}
            </Button>
            {canEdit.allowed ? (
              <Button variant="secondary" onClick={() => setEditOpen(true)}>
                <Pencil />
                {t("common:actions.edit")}
              </Button>
            ) : (
              <SoonButton
                label={t("common:actions.edit")}
                reason={t("clients:permissions.noEdit")}
                icon={<Pencil />}
              />
            )}
            {canDelete.allowed ? (
              <Button variant="ghost" onClick={() => setDeleteOpen(true)}>
                <Trash2 />
                {t("common:actions.delete")}
              </Button>
            ) : (
              <SoonButton
                variant="ghost"
                label={t("common:actions.delete")}
                reason={t("clients:permissions.noDelete")}
                icon={<Trash2 />}
              />
            )}
          </>
        }
      />

      <Stagger className="grid gap-[18px] lg:grid-cols-3">
        <FadeUp className="lg:col-span-1">
          <Card className="h-full p-5">
            <p className="mb-3 text-caption font-semibold uppercase tracking-[0.09em] text-text-muted">
              {t("clients:detail.insured")}
            </p>
            <InfoRow
              label={t("clients:fields.legalName")}
              value={insured.legal_name}
            />
            <InfoRow
              label={t("clients:fields.personType")}
              value={t(`clients:personType.${insured.person_type}`)}
            />
            <InfoRow
              label={t("clients:fields.taxActivity")}
              value={insured.tax_activity}
            />
            <InfoRow
              icon={<Mail className="h-3.5 w-3.5" />}
              label={t("clients:fields.insuredEmail")}
              value={insured.email}
            />
            <InfoRow
              icon={<Phone className="h-3.5 w-3.5" />}
              label={t("clients:fields.insuredPhone")}
              value={insured.phone}
            />
            <InfoRow
              icon={<MapPin className="h-3.5 w-3.5" />}
              label={t("clients:fields.address")}
              value={[insured.address, insured.commune, insured.region]
                .filter(Boolean)
                .join(", ")}
            />
          </Card>
        </FadeUp>

        <FadeUp className="lg:col-span-1">
          <Card className="h-full p-5">
            <p className="mb-3 text-caption font-semibold uppercase tracking-[0.09em] text-text-muted">
              {t("clients:detail.crm")}
            </p>
            <InfoRow
              label={t("clients:fields.accountManager")}
              value={
                client.account_manager?.full_name ?? t("clients:fields.unassigned")
              }
            />
            <InfoRow label={t("clients:fields.sector")} value={client.sector} />
            <InfoRow label={t("clients:fields.source")} value={client.source} />
            <InfoRow
              label={t("clients:fields.since")}
              value={client.since ? formatDate(client.since) : null}
            />
            <InfoRow
              label={t("clients:fields.contactName")}
              value={client.contact_name}
            />
            <InfoRow
              label={t("clients:fields.contactEmail")}
              value={client.contact_email}
            />
            <InfoRow
              label={t("clients:fields.contactPhone")}
              value={client.contact_phone}
            />
          </Card>
        </FadeUp>

        <FadeUp className="lg:col-span-1">
          <Card className="flex h-full flex-col p-5">
            <p className="mb-3 text-caption font-semibold uppercase tracking-[0.09em] text-text-muted">
              {t("clients:detail.overview")}
            </p>
            <div className="grid flex-1 grid-cols-2 gap-4">
              <Stat label={t("clients:kpi.assets")} value={client.assets_count} />
              <Stat
                label={t("clients:kpi.activePlacements")}
                value={client.active_placements_count}
              />
              <Stat
                label={t("clients:kpi.placements")}
                value={client.placements_count}
              />
              <Stat label={t("clients:kpi.policies")} value={client.policies_count} />
            </div>
            {client.internal_notes ? (
              <p className="mt-4 rounded-[10px] bg-bg-recessed p-3 text-caption text-text-secondary">
                {client.internal_notes}
              </p>
            ) : null}
          </Card>
        </FadeUp>
      </Stagger>

      <FadeUp>
        <Tabs defaultValue="assets">
          <TabsList className="h-auto flex-wrap">
            <TabsTrigger value="assets">
              <Building2 className="mr-1.5 h-4 w-4" />
              {t("clients:tabs.assets")}
            </TabsTrigger>
            <TabsTrigger value="placements">
              <Briefcase className="mr-1.5 h-4 w-4" />
              {t("clients:tabs.placements")}
            </TabsTrigger>
            <TabsTrigger value="documents">{t("clients:tabs.documents")}</TabsTrigger>
            <TabsTrigger value="activity">{t("clients:tabs.activity")}</TabsTrigger>
          </TabsList>

          <TabsContent value="assets" className="mt-5">
            <AssetsPanel clientId={client.id} />
          </TabsContent>
          <TabsContent value="placements" className="mt-5">
            <div className="mb-4 flex justify-end">
              <Button
                size="sm"
                variant="secondary"
                onClick={() => navigate(`/placements?client_id=${client.id}&new=1`)}
              >
                <Plus />
                {t("clients:placements.new")}
              </Button>
            </div>
            <ClientPlacements clientId={client.id} />
          </TabsContent>
          <TabsContent value="documents" className="mt-5">
            <DocumentsPanel entityType="client" entityId={client.id} />
          </TabsContent>
          <TabsContent value="activity" className="mt-5">
            <ActivityPanel client={client} />
          </TabsContent>
        </Tabs>
      </FadeUp>

      <EditClientDialog
        client={client}
        open={editOpen}
        onOpenChange={setEditOpen}
      />

      <Dialog open={deleteOpen} onOpenChange={setDeleteOpen}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>{t("clients:delete.title")}</DialogTitle>
            <DialogDescription>
              {t("clients:delete.description", {
                name: insured.legal_name,
              })}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="secondary" onClick={() => setDeleteOpen(false)}>
              {t("common:actions.cancel")}
            </Button>
            <Button
              variant="destructive"
              onClick={() => void onDelete()}
              disabled={remove.isPending}
            >
              {remove.isPending ? <Loader2 className="animate-spin" /> : <Trash2 />}
              {t("common:actions.delete")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
