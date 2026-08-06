import * as React from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { motion, useReducedMotion } from "framer-motion";
import { toast } from "sonner";
import { AxiosError } from "axios";
import {
  ArrowLeft,
  Building2,
  Check,
  ClipboardCheck,
  FileSignature,
  Loader2,
  Pencil,
  ShieldCheck,
  Trash2,
  Users,
} from "lucide-react";
import {
  useDeletePlacement,
  usePlacement,
  usePlacementTransitions,
} from "@/api/placements";
import { useQuotes } from "@/api/quotes";
import { useProposals } from "@/api/proposals";
import { useInspectionRequests } from "@/api/inspections";
import { num, type PlacementStatus } from "@/api/types";
import { Badge } from "@/components/ui/badge";
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
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { PageHeader } from "@/components/common/PageHeader";
import { FadeUp, Stagger } from "@/components/common/motion";
import { formatDate, formatDateTime, formatUF } from "@/lib/format";
import { useCan } from "@/lib/permissions";
import { cn } from "@/lib/utils";
import { SoonButton, SoonNote } from "@/pages/clients/Soon";
import { DocumentsPanel } from "@/pages/clients/DocumentsPanel";
import {
  DaysChip,
  PLACEMENT_PIPELINE,
  PlacementStatusBadge,
} from "@/pages/placements/status";
import {
  EditPlacementDialog,
  TransitionDialog,
} from "@/pages/placements/PlacementForm";

function serverMessage(error: unknown, fallback: string): string {
  if (error instanceof AxiosError) {
    const detail = error.response?.data?.detail;
    if (typeof detail === "string") return detail;
  }
  return fallback;
}

function InfoRow({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-4 border-b border-line py-2.5 last:border-0">
      <span className="text-caption text-text-muted">{label}</span>
      <span className="text-right text-body text-text-primary">{value || "—"}</span>
    </div>
  );
}

/**
 * The status pipeline.
 *
 * The rail shows every stage in machine order; the buttons underneath offer
 * ONLY what `GET /placements/{id}/transitions` returned. Every other stage is
 * rendered visibly disabled with a tooltip explaining why — the UI never
 * guesses at the state machine.
 */
function Pipeline({
  current,
  allowed,
  isTerminal,
  canEdit,
  onPick,
}: {
  current: PlacementStatus;
  allowed: PlacementStatus[];
  isTerminal: boolean;
  canEdit: boolean;
  onPick: (status: PlacementStatus) => void;
}) {
  const { t } = useTranslation(["placements", "common"]);
  const reduce = useReducedMotion();
  const currentIndex = PLACEMENT_PIPELINE.indexOf(current);
  const progress =
    PLACEMENT_PIPELINE.length > 1
      ? (currentIndex / (PLACEMENT_PIPELINE.length - 1)) * 100
      : 0;

  return (
    <Card className="p-5">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
        <p className="text-caption font-semibold uppercase tracking-[0.09em] text-text-muted">
          {t("placements:pipeline.title")}
        </p>
        {isTerminal ? (
          <Badge variant="muted">{t("placements:pipeline.terminal")}</Badge>
        ) : null}
      </div>

      {/* Rail */}
      <div className="relative mb-5">
        <div className="absolute left-0 right-0 top-[9px] h-[3px] rounded-full bg-bg-recessed" />
        <motion.div
          className="absolute left-0 top-[9px] h-[3px] rounded-full bg-gradient-to-r from-teal to-blue"
          initial={reduce ? false : { width: 0 }}
          animate={{ width: `${progress}%` }}
          transition={{ duration: 0.6, ease: [0.22, 0.72, 0.24, 1] }}
        />
        <ol className="relative flex justify-between">
          {PLACEMENT_PIPELINE.map((status, index) => {
            const done = index < currentIndex;
            const isCurrent = index === currentIndex;
            return (
              <li
                key={status}
                className="flex min-w-0 flex-1 flex-col items-center gap-2 first:items-start last:items-end"
              >
                <span
                  className={cn(
                    "grid h-[21px] w-[21px] place-items-center rounded-full border-2 transition-colors",
                    isCurrent
                      ? "border-teal bg-teal text-white shadow-[0_0_0_4px_color-mix(in_srgb,var(--teal)_20%,transparent)]"
                      : done
                        ? "border-teal bg-teal/90 text-white"
                        : "border-line bg-bg-surface",
                  )}
                >
                  {done ? <Check className="h-3 w-3" strokeWidth={3} /> : null}
                </span>
                <span
                  className={cn(
                    "text-center text-[11px] leading-tight",
                    isCurrent
                      ? "font-semibold text-text-primary"
                      : "text-text-muted",
                  )}
                >
                  {t(`placements:status.${status}`)}
                </span>
              </li>
            );
          })}
        </ol>
      </div>

      {/* Moves */}
      <div className="flex flex-wrap items-center gap-2">
        {PLACEMENT_PIPELINE.filter((status) => status !== current).map((status) => {
          const enabled = canEdit && allowed.includes(status);
          if (enabled) {
            return (
              <Button
                key={status}
                variant="secondary"
                size="sm"
                onClick={() => onPick(status)}
              >
                {t("placements:pipeline.moveTo", {
                  status: t(`placements:status.${status}`),
                })}
              </Button>
            );
          }
          return (
            <Tooltip key={status}>
              <TooltipTrigger asChild>
                <span className="inline-flex">
                  <Button variant="secondary" size="sm" disabled>
                    {t(`placements:status.${status}`)}
                  </Button>
                </span>
              </TooltipTrigger>
              <TooltipContent>
                {!canEdit
                  ? t("placements:permissions.noEdit")
                  : t("placements:pipeline.notAllowed", {
                      status: t(`placements:status.${current}`),
                    })}
              </TooltipContent>
            </Tooltip>
          );
        })}
      </div>
    </Card>
  );
}

function QuotesTab({ placementId }: { placementId: number }) {
  const { t } = useTranslation(["placements", "common"]);
  const quotes = useQuotes({ placement_id: placementId, limit: 50 });
  const items = quotes.data?.items ?? [];

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <SoonNote>{t("placements:quotes.readOnly")}</SoonNote>
        <SoonButton
          size="sm"
          label={t("placements:quotes.new")}
          reason={t("placements:quotes.moduleSoon")}
        />
      </div>
      {quotes.isLoading ? (
        <Skeleton className="h-24 w-full rounded-card" />
      ) : items.length === 0 ? (
        <Card className="p-10 text-center text-body text-text-muted">
          {t("placements:quotes.empty")}
        </Card>
      ) : (
        <Stagger className="flex flex-col gap-2">
          {items.map((quote) => (
            <FadeUp key={quote.id}>
              <Card className="flex flex-wrap items-center gap-3 p-4">
                <div className="min-w-0 flex-1">
                  <p className="truncate text-label font-medium text-text-primary">
                    {quote.insured_object || t("placements:quotes.untitled")}
                  </p>
                  <p className="truncate text-caption text-text-muted">
                    {t("placements:quotes.declaredValue")}:{" "}
                    {formatUF(num(quote.declared_value_uf))} ·{" "}
                    {t("placements:quotes.proposalsCount", {
                      count: quote.proposal_count,
                    })}
                  </p>
                </div>
                {quote.due_at ? (
                  <span className="font-mono text-mono-sm text-text-muted">
                    {formatDateTime(quote.due_at)}
                  </span>
                ) : null}
                <Badge variant="action">
                  {t(`placements:quoteStatus.${quote.status}`)}
                </Badge>
              </Card>
            </FadeUp>
          ))}
        </Stagger>
      )}
    </div>
  );
}

function ProposalsTab({ placementId }: { placementId: number }) {
  const { t } = useTranslation(["placements", "common"]);
  const proposals = useProposals({ placement_id: placementId, limit: 50 });
  const items = proposals.data?.items ?? [];

  return (
    <div className="flex flex-col gap-4">
      <SoonNote>{t("placements:proposals.readOnly")}</SoonNote>
      {proposals.isLoading ? (
        <Skeleton className="h-24 w-full rounded-card" />
      ) : items.length === 0 ? (
        <Card className="p-10 text-center text-body text-text-muted">
          {t("placements:proposals.empty")}
        </Card>
      ) : (
        <Stagger className="grid gap-3 md:grid-cols-2">
          {items.map((proposal) => (
            <FadeUp key={proposal.id}>
              <Card interactive className="h-full p-4">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="truncate text-label font-semibold text-text-primary">
                      {proposal.insurer?.trade_name ??
                        proposal.insurer?.legal_name ??
                        `#${proposal.insurer_id}`}
                    </p>
                    <p className="truncate font-mono text-mono-sm text-text-muted">
                      CMF {proposal.insurer?.cmf_code ?? "—"}
                    </p>
                  </div>
                  <Badge variant={proposal.origin === "native" ? "brand" : "neutral"}>
                    {t(`placements:proposals.origin.${proposal.origin}`)}
                  </Badge>
                </div>
                <div className="mt-3 flex items-end justify-between gap-3">
                  <div>
                    <p className="font-display text-h2 tabular-nums text-text-primary">
                      {formatUF(num(proposal.total_premium_uf))}
                    </p>
                    <p className="text-caption text-text-muted">
                      {t("placements:proposals.totalPremium")}
                    </p>
                  </div>
                  <div className="text-right">
                    <Badge
                      variant={
                        proposal.status === "accepted"
                          ? "success"
                          : proposal.status === "rejected"
                            ? "danger"
                            : "action"
                      }
                    >
                      {t(`placements:proposalStatus.${proposal.status}`)}
                    </Badge>
                    {proposal.is_confirmed ? (
                      <p className="mt-1 text-caption text-lime-deep">
                        {t("placements:proposals.confirmed")}
                      </p>
                    ) : (
                      <p className="mt-1 text-caption text-amber-deep">
                        {t("placements:proposals.unconfirmed")}
                      </p>
                    )}
                  </div>
                </div>
              </Card>
            </FadeUp>
          ))}
        </Stagger>
      )}
    </div>
  );
}

function InspectionsTab({ placementId }: { placementId: number }) {
  const { t } = useTranslation(["placements", "common"]);
  const requests = useInspectionRequests({ placement_id: placementId, limit: 50 });
  const items = requests.data?.items ?? [];

  return (
    <div className="flex flex-col gap-4">
      <SoonNote>{t("placements:inspections.readOnly")}</SoonNote>
      {requests.isLoading ? (
        <Skeleton className="h-24 w-full rounded-card" />
      ) : items.length === 0 ? (
        <Card className="p-10 text-center text-body text-text-muted">
          {t("placements:inspections.empty")}
        </Card>
      ) : (
        <Stagger className="flex flex-col gap-2">
          {items.map((request) => (
            <FadeUp key={request.id}>
              <Card className="flex flex-wrap items-center gap-3 p-4">
                <span className="grid h-9 w-9 shrink-0 place-items-center rounded-[10px] bg-[color-mix(in_srgb,var(--amber)_14%,transparent)] text-amber-deep">
                  <ClipboardCheck className="h-[18px] w-[18px]" strokeWidth={1.75} />
                </span>
                <div className="min-w-0 flex-1">
                  <p className="truncate text-label font-medium text-text-primary">
                    {request.reason || t("placements:inspections.untitled")}
                  </p>
                  <p className="text-caption text-text-muted">
                    {request.target_date
                      ? formatDate(request.target_date)
                      : t("placements:inspections.noDate")}
                  </p>
                </div>
                <Badge variant="warn">
                  {t(`placements:inspectionStatus.${request.status}`)}
                </Badge>
              </Card>
            </FadeUp>
          ))}
        </Stagger>
      )}
    </div>
  );
}

/** `/placements/:id` — the operating folder and its status machine. */
export default function PlacementDetailPage() {
  const { t } = useTranslation(["placements", "common"]);
  const params = useParams();
  const navigate = useNavigate();
  const placementId = Number(params.id);

  const { data: placement, isLoading, isError } = usePlacement(placementId);
  const transitions = usePlacementTransitions(placementId);
  const remove = useDeletePlacement();
  const canEdit = useCan("Placements", "Edit");
  const canDelete = useCan("Placements", "Delete");

  const [editOpen, setEditOpen] = React.useState(false);
  const [deleteOpen, setDeleteOpen] = React.useState(false);
  const [target, setTarget] = React.useState<PlacementStatus | null>(null);

  const onDelete = async () => {
    try {
      await remove.mutateAsync(placementId);
      toast.success(t("common:toast.deleted"));
      navigate("/placements");
    } catch (error) {
      toast.error(serverMessage(error, t("common:toast.error")));
    }
  };

  if (isLoading) {
    return (
      <div className="flex flex-col gap-4">
        <Skeleton className="h-10 w-72" />
        <Skeleton className="h-32 w-full rounded-card" />
        <Skeleton className="h-64 w-full rounded-card" />
      </div>
    );
  }

  if (isError || !placement) {
    return (
      <Card className="flex flex-col items-center gap-3 p-14 text-center">
        <p className="text-body text-text-muted">{t("placements:notFound")}</p>
        <Button variant="secondary" onClick={() => navigate("/placements")}>
          <ArrowLeft />
          {t("common:actions.back")}
        </Button>
      </Card>
    );
  }

  const allowed = transitions.data?.allowed ?? placement.allowed_transitions ?? [];

  return (
    <div className="flex flex-col gap-[22px]">
      <PageHeader
        eyebrow={
          <Link
            to="/placements"
            className="inline-flex items-center gap-1.5 hover:text-teal-deep"
          >
            <ArrowLeft className="h-3.5 w-3.5" />
            {t("placements:title")}
          </Link>
        }
        title={
          <span className="flex flex-wrap items-center gap-3">
            {placement.asset?.name ?? `#${placement.asset_id}`}
            <PlacementStatusBadge status={placement.status} />
          </span>
        }
        subtitle={
          <span className="flex flex-wrap items-center gap-2">
            {placement.insurance_line?.name ?? "—"}
            {placement.period ? <span>· {placement.period}</span> : null}
            <DaysChip days={placement.days_to_period_end} />
          </span>
        }
        actions={
          <>
            {placement.client ? (
              <Button
                variant="secondary"
                onClick={() => navigate(`/clients/${placement.client_id}`)}
              >
                <Users />
                {placement.client.legal_name}
              </Button>
            ) : null}
            {canEdit.allowed ? (
              <Button variant="secondary" onClick={() => setEditOpen(true)}>
                <Pencil />
                {t("common:actions.edit")}
              </Button>
            ) : (
              <SoonButton
                label={t("common:actions.edit")}
                reason={t("placements:permissions.noEdit")}
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
                reason={t("placements:permissions.noDelete")}
                icon={<Trash2 />}
              />
            )}
          </>
        }
      />

      <FadeUp>
        <Pipeline
          current={placement.status}
          allowed={allowed}
          isTerminal={transitions.data?.is_terminal ?? allowed.length === 0}
          canEdit={canEdit.allowed}
          onPick={setTarget}
        />
      </FadeUp>

      <Stagger className="grid gap-[18px] lg:grid-cols-3">
        <FadeUp>
          <Card className="h-full p-5">
            <p className="mb-3 text-caption font-semibold uppercase tracking-[0.09em] text-text-muted">
              {t("placements:detail.folder")}
            </p>
            <InfoRow
              label={t("placements:fields.client")}
              value={placement.client?.legal_name}
            />
            <InfoRow
              label={t("placements:fields.asset")}
              value={placement.asset?.name}
            />
            <InfoRow
              label={t("placements:fields.insuranceLine")}
              value={placement.insurance_line?.name}
            />
            <InfoRow label={t("placements:fields.period")} value={placement.period} />
            <InfoRow
              label={t("placements:fields.periodStart")}
              value={placement.period_start ? formatDate(placement.period_start) : null}
            />
            <InfoRow
              label={t("placements:fields.periodEnd")}
              value={placement.period_end ? formatDate(placement.period_end) : null}
            />
          </Card>
        </FadeUp>

        <FadeUp>
          <Card className="h-full p-5">
            <p className="mb-3 text-caption font-semibold uppercase tracking-[0.09em] text-text-muted">
              {t("placements:detail.market")}
            </p>
            <div className="grid grid-cols-2 gap-4">
              <div>
                <p className="font-display text-h2 tabular-nums text-text-primary">
                  {placement.quote_requests_count}
                </p>
                <p className="text-caption text-text-muted">
                  {t("placements:detail.quoteRequests")}
                </p>
              </div>
              <div>
                <p className="font-display text-h2 tabular-nums text-text-primary">
                  {placement.proposals_count}
                </p>
                <p className="text-caption text-text-muted">
                  {t("placements:detail.proposals")}
                </p>
              </div>
              <div>
                <p className="font-display text-h2 tabular-nums text-text-primary">
                  {placement.inspection_requests_count}
                </p>
                <p className="text-caption text-text-muted">
                  {t("placements:detail.inspections")}
                </p>
              </div>
              <div>
                <p className="font-display text-h2 tabular-nums text-text-primary">
                  {placement.policies_count}
                </p>
                <p className="text-caption text-text-muted">
                  {t("placements:detail.policies")}
                </p>
              </div>
            </div>
          </Card>
        </FadeUp>

        <FadeUp>
          <Card className="h-full p-5">
            <p className="mb-3 text-caption font-semibold uppercase tracking-[0.09em] text-text-muted">
              {t("placements:detail.notes")}
            </p>
            <p className="whitespace-pre-line text-body text-text-secondary">
              {placement.notes || t("placements:detail.noNotes")}
            </p>
            <p className="mt-4 text-caption text-text-muted">
              {t("placements:detail.updatedAt", {
                at: formatDateTime(placement.updated_at),
              })}
            </p>
          </Card>
        </FadeUp>
      </Stagger>

      <FadeUp>
        <Tabs defaultValue="quotes">
          <TabsList className="h-auto flex-wrap">
            <TabsTrigger value="quotes">
              <FileSignature className="mr-1.5 h-4 w-4" />
              {t("placements:tabs.quotes")}
            </TabsTrigger>
            <TabsTrigger value="proposals">
              <ShieldCheck className="mr-1.5 h-4 w-4" />
              {t("placements:tabs.proposals")}
            </TabsTrigger>
            <TabsTrigger value="inspections">
              <ClipboardCheck className="mr-1.5 h-4 w-4" />
              {t("placements:tabs.inspections")}
            </TabsTrigger>
            <TabsTrigger value="documents">
              <Building2 className="mr-1.5 h-4 w-4" />
              {t("placements:tabs.documents")}
            </TabsTrigger>
          </TabsList>

          <TabsContent value="quotes" className="mt-5">
            <QuotesTab placementId={placement.id} />
          </TabsContent>
          <TabsContent value="proposals" className="mt-5">
            <ProposalsTab placementId={placement.id} />
          </TabsContent>
          <TabsContent value="inspections" className="mt-5">
            <InspectionsTab placementId={placement.id} />
          </TabsContent>
          <TabsContent value="documents" className="mt-5">
            <DocumentsPanel
              entityType="placement"
              entityId={placement.id}
              defaultCategory="technical_brief"
            />
          </TabsContent>
        </Tabs>
      </FadeUp>

      <EditPlacementDialog
        placement={placement}
        open={editOpen}
        onOpenChange={setEditOpen}
      />

      <TransitionDialog
        placementId={placement.id}
        target={target}
        onOpenChange={(open) => !open && setTarget(null)}
      />

      <Dialog open={deleteOpen} onOpenChange={setDeleteOpen}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>{t("placements:delete.title")}</DialogTitle>
            <DialogDescription>{t("placements:delete.description")}</DialogDescription>
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
