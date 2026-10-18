/**
 * `/groups/:groupId/accounts/:caseId?tab=` — ONE ACCOUNT.
 *
 * An account is one insurance line × one validity period × N RUTs (spec §1), so
 * this page is the folder the broker works in, seen from inside its group. It is
 * the CENTRAL VIEW of the journey. The Journey hero lives ONLY on the Resumen
 * tab (v9); the underline tab bar follows the real journey — Resumen ·
 * Antecedentes · Bases Técnicas · Comparación · Propuesta · Pólizas ·
 * Renovación · Notas · Bitácora. Cotizaciones/Propuestas are not separate desks:
 * Comparación IS the cotizaciones step and there is ONE Propuesta.
 *
 * A historic vigencia is read-only: the upload CTA, the renewal CTA and the
 * "cambiar vigencia" door are hidden, and the "histórico" chip says why.
 *
 * v12 partial journeys — the account reads as a VIEWER first: a colocación may
 * enter from a policy PDF, portal quotes or the full market process, so the
 * rail draws only what happened (`stages[]`), "Saltar a…" / "Activar etapa"
 * sit next to Avanzar, and the tabs of stages the folder skipped (omitidas)
 * are folded behind "Ver etapas omitidas" — unless they hold content or the
 * URL deep-links into one. Policies and files lead the Resumen.
 */
import { formatRut } from "@/pages/clients/rut";
import * as React from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import {
  ArrowRight,
  Compass,
  EyeOff,
  CalendarClock,
  ExternalLink,
  GitCompareArrows,
  Lock,
  RefreshCw,
  Users,
} from "lucide-react";

import { PageHeader } from "@/components/common/PageHeader";
import { NotesPanel } from "@/components/common/NotesPanel";
import { FadeUp } from "@/components/common/motion";
import { Journey, skippedPhaseCount } from "@/components/groups/Journey";
import { AccountSummary } from "@/components/groups/AccountSummary";
import { AntecedentesFiles } from "@/components/groups/AntecedentesFiles";
import { GroupAvatar } from "@/components/groups/GroupAvatar";
import { ExpedienteView } from "@/components/expedientes/ExpedienteView";
import { PolicyOverview } from "@/components/policies/PolicyOverview";
import { PropuestaPanel } from "@/components/proposals/PropuestaPanel";
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
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { SoonButton } from "@/components/common/kit";
import { useFeature } from "@/api/meta";
import {
  DisabledHint,
  EmptyState,
  ErrorBanner,
  MonoChip,
  Section,
} from "@/pages/proposals/shared";
import {
  CaseStatusBadge,
  GroupCrumbs,
  OriginChip,
  StageBadge,
  accountErrorMessage,
  periodDates,
  useCaseId,
  useGroupId,
} from "@/pages/groups/shared";
import { useAccountGroup, useGroupTree } from "@/api/accountGroups";
import {
  useActivateCaseFileStage,
  useCaseFile,
  useCaseFileTimeline,
  useCaseFileTransitions,
  useSkipCaseFileTo,
  useTransitionCaseFile,
  type CaseFileDetailV12,
} from "@/api/caseFiles";
import { STAGE_LABEL_KEY } from "@/components/groups/stageLabels";
import { ROUTE_LABEL_KEY } from "@/components/catalog/RamoSubtypeSelect";
import { usePendingActions } from "@/api/pendingActions";
import { useCan } from "@/lib/permissions";
import {
  DATE_ONLY_TIMELINE_KINDS,
  collapseSkipped,
  formatTimelineMoment,
  useTimelineCopy,
} from "@/components/groups/timelineCopy";
import type {
  CaseStage,
  GroupTree,
  TreeLineNode,
  TreePeriodNode,
} from "@/api/types";
import { RENEWAL_REASON_LABEL, enumLabel } from "@/i18n/labels";

type TabKey =
  | "summary"
  | "antecedentes"
  | "bases-tecnicas"
  | "comparison"
  | "propuesta"
  | "policies"
  | "renewal"
  | "notes"
  | "timeline";

/**
 * The `?tab=` URL vocabulary — the vigencia submenu and pending rows deep-link
 * with these values. `summary` is FIRST and the default.
 */
const TABS: TabKey[] = [
  "summary",
  "antecedentes",
  "bases-tecnicas",
  "comparison",
  "propuesta",
  "policies",
  "renewal",
  "notes",
  "timeline",
];

/** Explicit tab labels (no dynamic keys). */
const TAB_LABEL_KEY: Record<TabKey, string> = {
  summary: "account.tabs.summary",
  antecedentes: "account.tabs.antecedentes",
  "bases-tecnicas": "account.tabs.bases-tecnicas",
  comparison: "account.tabs.comparison",
  propuesta: "account.tabs.propuesta",
  policies: "account.tabs.policies",
  renewal: "account.tabs.renewal",
  notes: "account.tabs.notes",
  timeline: "account.tabs.timeline",
};

/**
 * The journey stages each stage-tab belongs to. A tab folds behind "Ver etapas
 * omitidas" only when EVERY one of its stages the server reports is skipped.
 * Resumen, Pólizas, Renovación, Notas and Bitácora never fold.
 */
const TAB_STAGES: Partial<Record<TabKey, readonly CaseStage[]>> = {
  antecedentes: ["lead", "intake", "pre_underwriting"],
  "bases-tecnicas": ["technical_basis"],
  comparison: ["market_submission", "quotes_received", "comparison", "insured_decision"],
  propuesta: ["proposal_issued", "ratified"],
};

/** Tabs whose stages were all *omitidas* — and that hold nothing to show. */
function skippedTabs(
  detail: CaseFileDetailV12 | undefined,
  line: TreeLineNode | undefined,
): Set<TabKey> {
  const out = new Set<TabKey>();
  const rail = detail?.stages ?? [];
  if (rail.length === 0) return out;
  const stateOf = new Map(rail.map((row) => [row.stage, row.state] as const));
  const recordFiles = Object.values(line?.account.record_counts ?? {}).reduce(
    (sum, count) => sum + (count ?? 0),
    0,
  );
  const hasContent: Partial<Record<TabKey, boolean>> = {
    antecedentes: recordFiles > 0,
    comparison:
      (line?.account.quotes_count ?? 0) > 0 || (detail?.proposals_count ?? 0) > 0,
  };
  for (const [tab, stages] of Object.entries(TAB_STAGES) as [TabKey, readonly CaseStage[]][]) {
    // `lead` is the entry state, not a milestone — same rule as the rail.
    const meaningful = stages.length > 1 ? stages.filter((stage) => stage !== "lead") : stages;
    const states = meaningful
      .map((stage) => stateOf.get(stage))
      .filter((value): value is NonNullable<typeof value> => !!value);
    if (states.length > 0 && states.every((value) => value === "skipped") && !hasContent[tab]) {
      out.add(tab);
    }
  }
  return out;
}

/** The stages a skip to `target` would mark *omitida* (never-entered, in between). */
function stagesSkippedBy(detail: CaseFileDetailV12, target: CaseStage): CaseStage[] {
  const rail = detail.stages;
  const from = rail.findIndex((row) => row.stage === detail.stage);
  const to = rail.findIndex((row) => row.stage === target);
  if (from < 0 || to <= from) return [];
  return rail
    .slice(from + 1, to)
    .filter((row) => row.state !== "done")
    .map((row) => row.stage);
}

type StageMove = { kind: "skip" | "activate"; stage: CaseStage };

/** Where this folder sits in the tree — its period and its line node. */
function locate(
  tree: GroupTree | undefined,
  caseId: number,
): { period: TreePeriodNode; line: TreeLineNode } | null {
  for (const period of tree?.periods ?? []) {
    for (const line of period.lines) {
      if (line.account.case_file_id === caseId) return { period, line };
    }
  }
  return null;
}

export default function GroupAccountPage() {
  const { t } = useTranslation("accounts");
  const { t: tCases } = useTranslation("cases");
  const { t: tCat } = useTranslation("catalog");
  const groupId = useGroupId();
  const caseId = useCaseId();
  const [params, setParams] = useSearchParams();

  const group = useAccountGroup(groupId);
  const tree = useGroupTree(groupId);
  const detail = useCaseFile(caseId);
  const transitions = useCaseFileTransitions(caseId);
  const transition = useTransitionCaseFile(caseId);
  const skipTo = useSkipCaseFileTo(caseId);
  const activateStage = useActivateCaseFileStage(caseId);
  const pending = usePendingActions(caseId);
  const [pendingStage, setPendingStage] = React.useState<CaseStage | null>(null);
  const [stageMove, setStageMove] = React.useState<StageMove | null>(null);
  const [showSkipped, setShowSkipped] = React.useState(false);

  const canUpload = useCan("Documents", "Upload");
  const canCreateCase = useCan("CaseFiles", "Create");
  const canComment = useCan("CaseFiles", "Comment");
  const canManage = useCan("CaseFiles", "Manage");
  const canSubmit = useCan("CaseFiles", "Submit");
  const canViewProposals = useCan("Proposals", "View");
  const canCreatePolicy = useCan("Policies", "Create");
  const canEdit = useCan("CaseFiles", "Edit");

  const raw = params.get("tab") as TabKey | null;
  const tab: TabKey = raw && TABS.includes(raw) ? raw : "summary";
  const setTab = (value: string) => {
    const next = new URLSearchParams(params);
    next.set("tab", value);
    next.delete("folder");
    setParams(next, { replace: true });
  };

  const onTransition = (stage: CaseStage) => {
    setPendingStage(stage);
    transition.mutate(
      { to_stage: stage },
      { onSettled: () => setPendingStage(null) },
    );
  };

  const node = locate(tree.data, caseId);
  const isHistoric = node ? !node.period.is_latest : false;

  const hiddenTabs = skippedTabs(detail.data, node?.line);
  // A deep link into a folded tab still opens it (the URL is the truth).
  const visibleTabs = TABS.filter(
    (key) => showSkipped || !hiddenTabs.has(key) || key === tab,
  );
  const foldedCount = TABS.filter((key) => hiddenTabs.has(key) && key !== tab).length;
  // The button quotes the rail's own "omitida" ticks (one shared function),
  // not the number of folded tabs — two phases (Mercado, Comparación) share
  // one tab, so a tab count read as a contradiction (review v12 #8).
  const omittedPhases = detail.data
    ? skippedPhaseCount(detail.data.kind, detail.data.stage, detail.data.stages)
    : 0;

  const stageMoveBusy = skipTo.isPending || activateStage.isPending;
  const confirmStageMove = (note: string) => {
    if (!stageMove) return;
    const stageLabel = tCases(STAGE_LABEL_KEY[stageMove.stage]);
    const options = {
      onSuccess: () => {
        toast.success(
          stageMove.kind === "skip"
            ? t("journey.skipDialog.success", { stage: stageLabel })
            : t("journey.activateDialog.success", { stage: stageLabel }),
        );
        setStageMove(null);
      },
      onError: (error: unknown) => toast.error(accountErrorMessage(error, t)),
    };
    if (stageMove.kind === "skip") {
      skipTo.mutate({ target_stage: stageMove.stage, note: note || null }, options);
    } else {
      activateStage.mutate({ stage: stageMove.stage, note: note || null }, options);
    }
  };

  if (detail.isError) {
    return (
      <div className="flex flex-col gap-4">
        <PageHeader title={t("account.one")} />
        <ErrorBanner error={detail.error} />
        <Button variant="secondary" size="sm" asChild>
          <Link to={`/groups/${groupId}`}>{t("group.actions.open")}</Link>
        </Button>
      </div>
    );
  }

  const c = detail.data;
  const lineName = c?.insurance_line_name ?? node?.line.name ?? t("tree.line");
  const label = c?.period_label ?? node?.period.label ?? "";

  const uploadHint = isHistoric
    ? t("tree.readOnly")
    : !canUpload.allowed
      ? t("account.records.noUploadPermission")
      : null;

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        eyebrow={
          <GroupCrumbs
            items={[
              { label: t("group.title"), to: "/groups" },
              {
                label: (
                  <span className="inline-flex items-center gap-1.5">
                    <GroupAvatar
                      name={group.data?.name ?? ""}
                      icon={group.data?.icon ?? null}
                      size="sm"
                    />
                    {group.data?.name ?? t("group.one")}
                  </span>
                ),
                to: `/groups/${groupId}`,
              },
              ...(label
                ? [
                    {
                      label,
                      to: `/groups/${groupId}/periods/${encodeURIComponent(label)}`,
                    },
                  ]
                : []),
              { label: lineName },
            ]}
          />
        }
        title={
          c ? (
            label ? (
              t("account.title", { line: lineName, period: label })
            ) : (
              lineName
            )
          ) : (
            <Skeleton className="h-7 w-64" />
          )
        }
        subtitle={
          c ? (
            <span className="flex flex-wrap items-center gap-2">
              <MonoChip>{periodDates(c.period_start, c.period_end)}</MonoChip>
              {c.insurance_subtype_name ? (
                <Badge variant="neutral" title={t("viewer.subtype")}>
                  {c.insurance_subtype_name}
                </Badge>
              ) : null}
              {c.route ? (
                <Badge variant="brand" title={tCat("route.label")}>
                  <Compass className="mr-1 h-3 w-3" aria-hidden />
                  {tCat(ROUTE_LABEL_KEY[c.route])}
                </Badge>
              ) : null}
              <OriginChip origin={c.origin} t={t} />
              <StageBadge stage={c.stage} />
              <CaseStatusBadge status={c.status} />
              {c.period_locked ? (
                <DisabledHint hint={t("tree.locked")}>
                  <span className="inline-flex items-center gap-1 text-caption text-ink-3">
                    <Lock className="h-3.5 w-3.5" />
                    {t("tree.locked")}
                  </span>
                </DisabledHint>
              ) : null}
              {isHistoric ? <Badge variant="muted">{t("tree.historic")}</Badge> : null}
            </span>
          ) : null
        }
        actions={
          <>
            {/* The super-overview, INSIDE the group. This used to point at
                /cases/:id — a row in the deprecated flat Expedientes table,
                which answered a different question than "show me everything
                about this account". */}
            <Button variant="secondary" size="sm" asChild>
              <Link to={`/groups/${groupId}/accounts/${caseId}/expediente`}>
                <ExternalLink className="h-4 w-4" />
                {t("account.openCase")}
              </Link>
            </Button>
            {!isHistoric && c?.period_locked ? (
              canCreateCase.allowed ? (
                <Button variant="secondary" size="sm" asChild>
                  <Link to={`/groups/${groupId}/accounts/${caseId}/reperiod`}>
                    <CalendarClock className="h-4 w-4" />
                    {t("reperiod.title")}
                  </Link>
                </Button>
              ) : (
                <DisabledHint hint={t("account.noCreatePermission")}>
                  <Button variant="secondary" size="sm" disabled>
                    <CalendarClock className="h-4 w-4" />
                    {t("reperiod.title")}
                  </Button>
                </DisabledHint>
              )
            ) : null}
          </>
        }
      />

      {transition.isError ? (
        <ErrorBanner error={accountErrorMessage(transition.error, t)} />
      ) : null}
      {tree.isError ? <ErrorBanner error={tree.error} /> : null}

      <AccountMeta
        clientCount={c?.client_ids?.length ?? 0}
        contratante={c?.client_legal_name ?? null}
        contratanteRut={c?.client_rut ?? null}
        documentsCount={c?.documents_count ?? 0}
        policiesCount={node?.line.policies.length ?? 0}
      />

      <Tabs value={tab} onValueChange={setTab}>
        <div className="flex flex-wrap items-end gap-x-2 gap-y-1">
          <div className="-mx-4 min-w-0 flex-1 overflow-x-auto px-4 sm:mx-0 sm:px-0">
            <TabsList variant="underline" className="w-max min-w-full sm:w-auto sm:flex-wrap">
              {visibleTabs.map((key) => (
                <TabsTrigger key={key} value={key}>
                  {t(TAB_LABEL_KEY[key])}
                  {hiddenTabs.has(key) ? (
                    <span className="ml-1.5 text-[10px] font-normal uppercase tracking-wide text-ink-3">
                      {t("hiddenTabs.skippedMark")}
                    </span>
                  ) : null}
                </TabsTrigger>
              ))}
            </TabsList>
          </div>
          {foldedCount > 0 || (showSkipped && hiddenTabs.size > 0) ? (
            <Button
              variant="ghost"
              size="sm"
              className="h-10 shrink-0 sm:h-8"
              aria-pressed={showSkipped}
              onClick={() => setShowSkipped((value) => !value)}
            >
              <EyeOff className="h-3.5 w-3.5" aria-hidden />
              {showSkipped
                ? t("hiddenTabs.hide")
                : t("hiddenTabs.show", { count: omittedPhases || foldedCount })}
            </Button>
          ) : null}
        </div>

        <TabsContent value="summary" className="mt-4 flex flex-col gap-5">
          {/* The hero lives here only (v9): where the case is on the journey and
              what comes next. Enabled/disabled comes verbatim from the
              transitions API — the Journey never re-implements a stage guard. */}
          {c ? (
            <FadeUp>
              <Card className="p-5">
                <Journey
                  variant="hero"
                  kind={c.kind}
                  stage={c.stage}
                  transitions={transitions.data?.options ?? []}
                  pendingActions={pending.data?.actions ?? []}
                  canTransition={canSubmit.allowed && !canSubmit.isLoading}
                  isPending={transition.isPending}
                  pendingStage={pendingStage}
                  onTransition={onTransition}
                  stages={c.stages}
                  skipTargets={c.skip_targets}
                  hasPolicy={c.has_policy}
                  canEdit={canEdit.allowed && !canEdit.isLoading && !isHistoric}
                  onSkipTo={(stage) => setStageMove({ kind: "skip", stage })}
                  onActivateStage={(stage) => setStageMove({ kind: "activate", stage })}
                />
              </Card>
            </FadeUp>
          ) : (
            <Skeleton className="h-28 w-full" />
          )}

          <AccountSummary
            groupId={groupId}
            caseId={caseId}
            line={node?.line}
            detail={c}
            onSelectTab={setTab}
          />
        </TabsContent>

        <TabsContent value="antecedentes" className="mt-4">
          <AntecedentesFiles
            caseId={caseId}
            insuranceLineId={c?.insurance_line_id ?? null}
            canUpload={canUpload.allowed && !isHistoric}
            uploadHint={uploadHint}
          />
        </TabsContent>

        <TabsContent value="bases-tecnicas" className="mt-4">
          <ExpedienteView caseId={caseId} />
        </TabsContent>

        <TabsContent value="comparison" className="mt-4">
          {canViewProposals.allowed ? (
            <ComparisonTab caseId={caseId} />
          ) : (
            <Card>
              <EmptyState title={t("account.comparison.noPermission")} />
            </Card>
          )}
        </TabsContent>

        <TabsContent value="propuesta" className="mt-4">
          <PropuestaPanel caseId={caseId} stage={c?.stage} />
        </TabsContent>

        <TabsContent value="policies" className="mt-4">
          <PolicyOverview
            caseId={caseId}
            canUpload={!isHistoric && canCreatePolicy.allowed && canUpload.allowed}
            uploadHint={
              isHistoric
                ? t("tree.readOnly")
                : !canCreatePolicy.allowed
                  ? t("account.policies.noCreatePermission")
                  : !canUpload.allowed
                    ? t("account.records.noUploadPermission")
                    : null
            }
          />
        </TabsContent>

        <TabsContent value="renewal" className="mt-4">
          <RenewalTab
            groupId={groupId}
            caseId={caseId}
            line={node?.line}
            isLoading={tree.isLoading}
            isHistoric={isHistoric}
            canCreate={canCreateCase.allowed}
          />
        </TabsContent>

        <TabsContent value="notes" className="mt-4">
          <NotesPanel
            entityType="case_file"
            entityId={caseId}
            canComment={canComment.allowed}
            canDelete={canManage.allowed}
          />
        </TabsContent>

        <TabsContent value="timeline" className="mt-4">
          <TimelineTab caseId={caseId} />
        </TabsContent>
      </Tabs>

      {stageMove && c ? (
        <StageMoveDialog
          move={stageMove}
          detail={c}
          busy={stageMoveBusy}
          onCancel={() => setStageMove(null)}
          onConfirm={confirmStageMove}
        />
      ) : null}
    </div>
  );
}

// =============================================================================
// Saltar a… / Activar etapa — the confirmation (the server decides)
// =============================================================================

function StageMoveDialog({
  move,
  detail,
  busy,
  onCancel,
  onConfirm,
}: {
  move: StageMove;
  detail: CaseFileDetailV12;
  busy: boolean;
  onCancel: () => void;
  onConfirm: (note: string) => void;
}) {
  const { t } = useTranslation("accounts");
  const { t: tc } = useTranslation("common");
  const { t: tCases } = useTranslation("cases");
  const [note, setNote] = React.useState("");
  const stageLabel = tCases(STAGE_LABEL_KEY[move.stage]);
  const skipped = move.kind === "skip" ? stagesSkippedBy(detail, move.stage) : [];

  return (
    <Dialog open onOpenChange={(open) => (open || busy ? undefined : onCancel())}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>
            {move.kind === "skip"
              ? t("journey.skipDialog.title", { stage: stageLabel })
              : t("journey.activateDialog.title", { stage: stageLabel })}
          </DialogTitle>
          <DialogDescription>
            {move.kind === "skip"
              ? t("journey.skipDialog.description")
              : t("journey.activateDialog.description")}
          </DialogDescription>
        </DialogHeader>

        {move.kind === "skip" ? (
          skipped.length > 0 ? (
            <div className="flex flex-col gap-1.5">
              <span className="text-caption font-medium text-ink-2">
                {t("journey.skipDialog.willSkip")}
              </span>
              <div className="flex flex-wrap gap-1.5">
                {skipped.map((stage) => (
                  <Badge key={stage} variant="muted">
                    {tCases(STAGE_LABEL_KEY[stage])}
                  </Badge>
                ))}
              </div>
            </div>
          ) : (
            <p className="text-caption text-ink-3">{t("journey.skipDialog.noneSkipped")}</p>
          )
        ) : null}

        <div className="flex flex-col gap-1.5">
          <Label htmlFor="stage-move-note">{t("journey.skipDialog.note")}</Label>
          <Textarea
            id="stage-move-note"
            rows={2}
            maxLength={2000}
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder={t("journey.skipDialog.notePlaceholder")}
          />
        </div>

        <DialogFooter className="gap-2">
          <Button variant="secondary" size="sm" className="h-10 sm:h-8" disabled={busy} onClick={onCancel}>
            {tc("actions.cancel")}
          </Button>
          <Button
            size="sm"
            className="h-10 sm:h-8"
            disabled={busy}
            onClick={() => onConfirm(note.trim())}
          >
            {busy ? tc("actions.loading") : move.kind === "skip"
              ? t("journey.skipDialog.confirm")
              : t("journey.activateDialog.confirm")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// =============================================================================
// Meta line — quiet, inline, under the hero (no boxed card; Signal §3)
// =============================================================================

function AccountMeta({
  clientCount,
  contratante,
  contratanteRut,
  documentsCount,
  policiesCount,
}: {
  clientCount: number;
  contratante: string | null;
  contratanteRut: string | null;
  documentsCount: number;
  policiesCount: number;
}) {
  const { t } = useTranslation("accounts");
  const item = (label: string, value: React.ReactNode) => (
    <span className="inline-flex items-center gap-1.5">
      <span className="text-ink-3">{label}</span>
      <span className="font-medium tabular-nums text-ink">{value}</span>
    </span>
  );
  return (
    <div className="flex flex-wrap items-center gap-x-5 gap-y-1.5 text-caption">
      {item(
        t("account.contratante"),
        contratante ? (
          <span className="inline-flex flex-wrap items-center gap-1.5">
            {contratante}
            {contratanteRut ? <MonoChip>{formatRut(contratanteRut)}</MonoChip> : null}
          </span>
        ) : (
          "—"
        ),
      )}
      <span className="inline-flex items-center gap-1.5">
        <Users className="h-3.5 w-3.5 text-ink-3" aria-hidden />
        {item(t("account.members"), clientCount)}
      </span>
      {item(t("tree.records"), documentsCount)}
      {item(t("tree.policies"), policiesCount)}
    </div>
  );
}

// =============================================================================
// Comparación — links out to the standalone comparison worktable
// =============================================================================

function ComparisonTab({ caseId }: { caseId: number }) {
  const { t } = useTranslation("accounts");
  // Portal quotes (RPA) land in the same comparison: the run is filed under this
  // account through `?case=` and its "Subir cotización PDF" comes back here.
  const canPortals = useCan("Integrations", "View");
  // The RPA is coming-soon while the server flag is off: visibly disabled with
  // the server's reason, never a link to a page that cannot search.
  const portal = useFeature("portal_search");
  const { t: tc } = useTranslation("common");
  return (
    <Section
      title={t("account.comparison.title")}
      description={t("account.comparison.description")}
    >
      <div className="flex flex-wrap items-center gap-3">
        <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-brand-soft text-brand-deep">
          <GitCompareArrows className="h-5 w-5" />
        </span>
        <p className="min-w-0 flex-1 text-caption text-ink-3">
          {t("account.comparison.hint")}
        </p>
        {!portal.enabled ? (
          <SoonButton reason={portal.reason ?? tc("nav.portalSearchSoonReason")}>
            {t("account.comparison.portalSearch")}
          </SoonButton>
        ) : canPortals.allowed ? (
          <Button size="sm" variant="secondary" asChild>
            <Link to={`/portal-search?case=${caseId}`}>{t("account.comparison.portalSearch")}</Link>
          </Button>
        ) : (
          <DisabledHint hint={t("account.comparison.portalSearchNoPermission")}>
            <Button size="sm" variant="secondary" disabled>
              {t("account.comparison.portalSearch")}
            </Button>
          </DisabledHint>
        )}
        <Button size="sm" asChild>
          <Link to={`/comparisons/${caseId}`}>
            {t("account.comparison.open")}
            <ArrowRight className="h-4 w-4" />
          </Link>
        </Button>
      </div>
    </Section>
  );
}

// =============================================================================
// Renovación
// =============================================================================

function RenewalTab({
  groupId,
  caseId,
  line,
  isLoading,
  isHistoric,
  canCreate,
}: {
  groupId: number;
  caseId: number;
  line: TreeLineNode | undefined;
  isLoading: boolean;
  isHistoric: boolean;
  canCreate: boolean;
}) {
  const { t } = useTranslation("accounts");
  const renewal = line?.renewal;

  if (isLoading) return <Skeleton className="h-40 w-full" />;

  const blockedHint = !canCreate
    ? t("account.noCreatePermission")
    : isHistoric
      ? t("tree.readOnly")
      : renewal?.reason
        ? enumLabel(t, RENEWAL_REASON_LABEL, renewal.reason, t("renewal.reason.period_open"))
        : t("renewal.reason.period_open");

  return (
    <div className="flex flex-col gap-4">
      <Section title={t("renewal.title")} description={t("renew.subtitle")}>
        {renewal?.case_file_id ? (
          <div className="flex flex-wrap items-center gap-3">
            <Badge variant="brand">{t("renewal.title")}</Badge>
            <Button variant="secondary" size="sm" asChild>
              <Link to={`/groups/${groupId}/accounts/${renewal.case_file_id}`}>
                <RefreshCw className="h-4 w-4" />
                {t("renewal.open")}
              </Link>
            </Button>
          </div>
        ) : renewal?.allowed && canCreate && !isHistoric ? (
          <Button size="sm" asChild>
            <Link to={`/groups/${groupId}/accounts/${caseId}/renew`}>
              <RefreshCw className="h-4 w-4" />
              {t("renewal.start")}
            </Link>
          </Button>
        ) : (
          <DisabledHint hint={blockedHint}>
            <Button size="sm" disabled>
              <RefreshCw className="h-4 w-4" />
              {t("renewal.start")}
            </Button>
          </DisabledHint>
        )}
      </Section>

      <Section title={t("reperiod.title")} description={t("reperiod.subtitle")}>
        {isHistoric ? (
          <p className="text-caption text-ink-3">{t("tree.readOnly")}</p>
        ) : canCreate ? (
          <Button variant="secondary" size="sm" asChild>
            <Link to={`/groups/${groupId}/accounts/${caseId}/reperiod`}>
              <CalendarClock className="h-4 w-4" />
              {t("reperiod.submit")}
            </Link>
          </Button>
        ) : (
          <DisabledHint hint={t("account.noCreatePermission")}>
            <Button variant="secondary" size="sm" disabled>
              <CalendarClock className="h-4 w-4" />
              {t("reperiod.submit")}
            </Button>
          </DisabledHint>
        )}
      </Section>
    </div>
  );
}

// =============================================================================
// Bitácora
// =============================================================================

function TimelineTab({ caseId }: { caseId: number }) {
  const { t } = useTranslation("accounts");
  const timelineCopy = useTimelineCopy();
  const timeline = useCaseFileTimeline(caseId);

  if (timeline.isLoading) return <Skeleton className="h-56 w-full" />;
  if (timeline.isError) return <ErrorBanner error={timeline.error} />;

  const entries = collapseSkipped(timeline.data?.entries ?? []);
  if (entries.length === 0) {
    return (
      <Card>
        <EmptyState title={t("timeline.empty")} />
      </Card>
    );
  }

  return (
    <Card className="overflow-hidden">
      <ol className="divide-y divide-line">
        {entries.map((entry, index) => (
          <li key={`${entry.occurred_at}-${index}`} className="flex gap-3 px-5 py-3">
            <Badge variant="neutral" className="mt-0.5 h-fit shrink-0">
              {timelineCopy(entry).kindLabel}
            </Badge>
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-baseline gap-2">
                <span className="text-body font-medium text-ink">
                  {timelineCopy(entry).label}
                </span>
                <span className="text-caption text-ink-3">
                  {formatTimelineMoment(entry.occurred_at, {
                        dateOnly: entry.date_only ?? DATE_ONLY_TIMELINE_KINDS.has(entry.kind),
                      })}
                </span>
              </div>
              {timelineCopy(entry).detail ? (
                <p className="mt-0.5 text-caption text-ink-2">
                  {timelineCopy(entry).detail}
                </p>
              ) : null}
            </div>
          </li>
        ))}
      </ol>
    </Card>
  );
}
