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
 */
import * as React from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import {
  ArrowRight,
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
import { Journey } from "@/components/groups/Journey";
import { AccountSummary } from "@/components/groups/AccountSummary";
import { AntecedentesFiles } from "@/components/groups/AntecedentesFiles";
import { GroupAvatar } from "@/components/groups/GroupAvatar";
import { ExpedienteView } from "@/components/expedientes/ExpedienteView";
import { PolicyOverview } from "@/components/policies/PolicyOverview";
import { PropuestaPanel } from "@/components/proposals/PropuestaPanel";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
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
  useCaseFile,
  useCaseFileTimeline,
  useCaseFileTransitions,
  useTransitionCaseFile,
} from "@/api/caseFiles";
import { usePendingActions } from "@/api/pendingActions";
import { useCan } from "@/lib/permissions";
import { formatDateTime } from "@/lib/format";
import { useTimelineCopy } from "@/components/groups/timelineCopy";
import type {
  CaseStage,
  GroupTree,
  TreeLineNode,
  TreePeriodNode,
} from "@/api/types";

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
  const groupId = useGroupId();
  const caseId = useCaseId();
  const [params, setParams] = useSearchParams();

  const group = useAccountGroup(groupId);
  const tree = useGroupTree(groupId);
  const detail = useCaseFile(caseId);
  const transitions = useCaseFileTransitions(caseId);
  const transition = useTransitionCaseFile(caseId);
  const pending = usePendingActions(caseId);
  const [pendingStage, setPendingStage] = React.useState<CaseStage | null>(null);

  const canUpload = useCan("Documents", "Upload");
  const canCreateCase = useCan("CaseFiles", "Create");
  const canComment = useCan("CaseFiles", "Comment");
  const canManage = useCan("CaseFiles", "Manage");
  const canSubmit = useCan("CaseFiles", "Submit");
  const canViewProposals = useCan("Proposals", "View");
  const canCreatePolicy = useCan("Policies", "Create");

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
            <Button variant="secondary" size="sm" asChild>
              <Link to={`/cases/${caseId}`}>
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
        <TabsList variant="underline" className="flex-wrap">
          {TABS.map((key) => (
            <TabsTrigger key={key} value={key}>
              {t(`account.tabs.${key}`)}
            </TabsTrigger>
          ))}
        </TabsList>

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
                />
              </Card>
            </FadeUp>
          ) : (
            <Skeleton className="h-28 w-full" />
          )}

          <AccountSummary
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
    </div>
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
            {contratanteRut ? <MonoChip>{contratanteRut}</MonoChip> : null}
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
        ? t(`renewal.reason.${renewal.reason}`, {
            defaultValue: t("renewal.reason.period_open"),
          })
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

  const entries = timeline.data?.entries ?? [];
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
                  {formatDateTime(entry.occurred_at)}
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
