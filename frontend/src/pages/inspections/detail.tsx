import * as React from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { ArrowLeft, CopyPlus, FileText, Pencil, UserCog } from "lucide-react";
import { PageHeader } from "@/components/common/PageHeader";
import { FadeUp } from "@/components/common/motion";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useInspection, useInspectionRequest, useUpdateInspection } from "@/api/inspections";
import { useAsset } from "@/api/assets";
import { useUsers } from "@/api/users";
import { useAuth } from "@/providers/AuthProvider";
import { useCan, useModulePermissions } from "@/lib/permissions";
import { formatDate } from "@/lib/format";
import { cn } from "@/lib/utils";
import {
  EmptyState,
  GuardedButton,
  InfoItem,
  REPORT_STATUS_VARIANT,
  SectionCard,
  isFrozen,
  type Guard,
} from "./components/shared";
import { ScorePanel } from "./components/ScorePanel";
import { ScoresDialog } from "./components/ScoresDialog";
import { ChecklistCard } from "./components/ChecklistCard";
import { BoundariesCard } from "./components/BoundariesCard";
import { EvidenceGallery } from "./components/EvidenceGallery";
import { ReportCard } from "./components/ReportCard";
import {
  AssignInspectorDialog,
  NewVersionDialog,
  StatusActions,
} from "./components/DetailActions";

/**
 * One inspection report: scores, dynamic checklist, boundaries, evidence and
 * the signed PDF.
 *
 * Everything that writes is gated twice — by the RBAC matrix
 * (`GET /auth/permissions`) and by the report's own lifecycle, since an issued
 * or archived report is frozen server-side and must be amended with a new
 * version. Both reasons surface as a tooltip on a disabled control.
 */
export default function InspectionDetailPage() {
  const { t } = useTranslation("inspections");
  const { t: tc } = useTranslation("common");
  const params = useParams();
  const navigate = useNavigate();
  const inspectionId = Number(params.id);

  const { user } = useAuth();
  const perms = useModulePermissions("Inspections");
  const canViewUsers = useCan("Users", "View");
  const canUploadDocs = useCan("Documents", "Upload");
  const canDeleteDocs = useCan("Documents", "Delete");

  const { data: inspection, isLoading, isError } = useInspection(inspectionId);
  const { data: asset } = useAsset(inspection?.asset_id);
  const { data: request } = useInspectionRequest(
    inspection?.inspection_request_id ?? undefined,
  );
  const usersQuery = useUsers({ user_type: "broker", limit: 200 }, canViewUsers.allowed);
  const update = useUpdateInspection(inspectionId);

  const [assignOpen, setAssignOpen] = React.useState(false);
  const [versionOpen, setVersionOpen] = React.useState(false);
  const [scoresOpen, setScoresOpen] = React.useState(false);
  const [findings, setFindings] = React.useState("");
  const [findingsDirty, setFindingsDirty] = React.useState(false);

  React.useEffect(() => {
    if (findingsDirty) return;
    setFindings(inspection?.findings_summary ?? "");
  }, [inspection?.findings_summary, findingsDirty]);

  const frozen = inspection ? isFrozen(inspection.status) : false;

  const noPermission = t("guard.noPermission");
  const readOnly = t("guard.readOnly");

  const editGuard: Guard = !perms.edit
    ? { allowed: false, reason: noPermission }
    : frozen
      ? { allowed: false, reason: readOnly }
      : { allowed: true, reason: "" };

  const submitGuard: Guard = perms.submit
    ? { allowed: true, reason: "" }
    : { allowed: false, reason: noPermission };

  const createGuard: Guard = perms.create
    ? { allowed: true, reason: "" }
    : { allowed: false, reason: noPermission };

  const uploadGuard: Guard = !canUploadDocs.allowed
    ? { allowed: false, reason: noPermission }
    : frozen
      ? { allowed: false, reason: readOnly }
      : { allowed: true, reason: "" };

  const deleteGuard: Guard = !canDeleteDocs.allowed
    ? { allowed: false, reason: noPermission }
    : frozen
      ? { allowed: false, reason: readOnly }
      : { allowed: true, reason: "" };

  const assignGuard: Guard = !canViewUsers.allowed
    ? { allowed: false, reason: t("guard.needsUsers") }
    : editGuard;

  const inspectorLabel = React.useMemo(() => {
    if (!inspection?.inspector_id) return t("detail.unassigned");
    if (user?.id === inspection.inspector_id) return user.full_name;
    const found = usersQuery.data?.items.find((u) => u.id === inspection.inspector_id);
    return found?.full_name ?? `#${inspection.inspector_id}`;
  }, [inspection?.inspector_id, usersQuery.data, user, t]);

  if (isLoading) {
    return (
      <div className="flex flex-col gap-4">
        <Skeleton className="h-10 w-72" />
        <Skeleton className="h-40 w-full rounded-card" />
        <Skeleton className="h-72 w-full rounded-card" />
      </div>
    );
  }

  if (isError || !inspection) {
    return (
      <Card className="p-10">
        <EmptyState>{t("detail.notFound")}</EmptyState>
        <div className="mt-4 flex justify-center">
          <Button variant="secondary" onClick={() => navigate("/inspections")}>
            <ArrowLeft /> {t("detail.backToList")}
          </Button>
        </div>
      </Card>
    );
  }

  return (
    <>
      <PageHeader
        eyebrow={
          <Link
            to="/inspections"
            className="inline-flex items-center gap-1.5 transition-colors hover:text-teal-deep"
          >
            <ArrowLeft className="h-3.5 w-3.5" /> {t("detail.eyebrow")}
          </Link>
        }
        title={
          <span className="flex flex-wrap items-center gap-3">
            {inspection.folio ?? `#${inspection.id}`}
            <Badge variant={REPORT_STATUS_VARIANT[inspection.status]}>
              {t(`status.report.${inspection.status}`)}
            </Badge>
            <Badge variant="outline">{t("detail.version", { n: inspection.version })}</Badge>
          </span>
        }
        subtitle={asset?.name ?? `#${inspection.asset_id}`}
        actions={
          <>
            <GuardedButton
              guard={assignGuard}
              variant="secondary"
              onClick={() => setAssignOpen(true)}
            >
              <UserCog /> {t("detail.assign")}
            </GuardedButton>
            <GuardedButton
              guard={createGuard}
              variant="secondary"
              onClick={() => setVersionOpen(true)}
            >
              <CopyPlus /> {t("detail.newVersion")}
            </GuardedButton>
          </>
        }
      />

      {frozen ? (
        <FadeUp>
          <div className="rounded-card border border-line bg-[color-mix(in_srgb,var(--amber)_10%,transparent)] px-4 py-3 text-body text-amber-deep">
            {t("detail.frozen", { status: t(`status.report.${inspection.status}`) })}
          </div>
        </FadeUp>
      ) : null}

      <FadeUp>
        <Card className="p-5">
          <div className="grid grid-cols-2 gap-5 lg:grid-cols-5">
            <InfoItem label={t("detail.asset")}>
              {asset?.name ?? `#${inspection.asset_id}`}
            </InfoItem>
            <InfoItem label={t("detail.inspector")}>{inspectorLabel}</InfoItem>
            <InfoItem label={t("detail.visitDate")}>
              {formatDate(inspection.visit_date)}
            </InfoItem>
            <InfoItem label={t("detail.reportDate")}>
              {formatDate(inspection.report_date)}
            </InfoItem>
            <InfoItem label={t("detail.request")} mono>
              {request ? `#${request.id}` : "—"}
            </InfoItem>
          </div>
          <div className="mt-5 flex flex-wrap items-center gap-2 border-t border-line pt-4">
            <span className="mr-1 text-caption text-text-muted">
              {t("detail.statusActions")}
            </span>
            <StatusActions inspection={inspection} submitGuard={submitGuard} />
          </div>
        </Card>
      </FadeUp>

      <div className="grid grid-cols-1 gap-[22px] xl:grid-cols-2">
        <ScorePanel
          inspection={inspection}
          actions={
            <GuardedButton
              guard={editGuard}
              variant="secondary"
              size="sm"
              onClick={() => setScoresOpen(true)}
            >
              <Pencil /> {t("scores.edit")}
            </GuardedButton>
          }
        />

        <SectionCard
          title={t("detail.findings")}
          icon={<FileText />}
          actions={
            <GuardedButton
              guard={editGuard}
              variant="secondary"
              size="sm"
              disabled={update.isPending || !findingsDirty}
              onClick={() =>
                update.mutate(
                  { findings_summary: findings.trim() || null },
                  {
                    onSuccess: () => {
                      toast.success(tc("toast.saved"));
                      setFindingsDirty(false);
                    },
                    onError: () => toast.error(t("error.save")),
                  },
                )
              }
            >
              {t("detail.saveFindings")}
            </GuardedButton>
          }
        >
          {editGuard.allowed ? (
            <textarea
              value={findings}
              onChange={(e) => {
                setFindings(e.target.value);
                setFindingsDirty(true);
              }}
              rows={6}
              placeholder={t("detail.findingsPlaceholder")}
              className={cn(
                "w-full resize-y rounded-[10px] border border-line bg-bg-surface px-3.5 py-2.5 text-body text-text-primary",
                "placeholder:text-text-muted",
                "focus-visible:border-teal focus-visible:outline-none focus-visible:shadow-[0_0_0_3px_color-mix(in_srgb,var(--teal)_18%,transparent)]",
              )}
            />
          ) : inspection.findings_summary ? (
            <p className="whitespace-pre-line text-body text-text-secondary">
              {inspection.findings_summary}
            </p>
          ) : (
            <EmptyState>{t("detail.findingsEmpty")}</EmptyState>
          )}
        </SectionCard>
      </div>

      <ChecklistCard inspection={inspection} editGuard={editGuard} />

      <div className="grid grid-cols-1 gap-[22px] xl:grid-cols-2">
        <BoundariesCard inspectionId={inspection.id} editGuard={editGuard} />
        <ReportCard inspection={inspection} uploadGuard={uploadGuard} />
      </div>

      <EvidenceGallery
        inspectionId={inspection.id}
        uploadGuard={uploadGuard}
        deleteGuard={deleteGuard}
      />

      <AssignInspectorDialog
        inspection={inspection}
        inspectors={usersQuery.data?.items ?? []}
        open={assignOpen}
        onOpenChange={setAssignOpen}
      />
      <NewVersionDialog
        inspection={inspection}
        open={versionOpen}
        onOpenChange={setVersionOpen}
      />
      <ScoresDialog
        inspection={inspection}
        open={scoresOpen}
        onOpenChange={setScoresOpen}
      />
    </>
  );
}
