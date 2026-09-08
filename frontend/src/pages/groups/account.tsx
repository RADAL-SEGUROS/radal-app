/**
 * `/groups/:groupId/accounts/:caseId?tab=&folder=` — ONE ACCOUNT.
 *
 * An account is one insurance line × one validity period × N RUTs (spec §1), so
 * this page is the folder the broker works in, seen from inside its group. The
 * page is the CENTRAL VIEW of the journey: PageHeader, then the Journey hero
 * (the continuous-stroke visualization, wired to the transitions API exactly
 * like `CaseDetailBody`), then one underline tab bar for the stage categories.
 * It deliberately does NOT re-render `CaseDetailBody`: that component is the
 * standalone `/cases/:id` page — its own PageHeader, its own back link, its own
 * seven tabs keyed on the same `?tab=` param. What is reused instead are the
 * genuinely shareable pieces: `Journey`, `NotesPanel`, `DataTable`,
 * `SuggestionForm`, the query hooks and the `components/common/kit` atoms.
 * "Ver expediente completo" links out to the legacy page for everything this
 * view intentionally leaves out (packs, resumen IA).
 *
 * The ANTECEDENTES folders are NOT hardcoded: the buckets and the categories
 * that fall into each come from `GET /navigator`'s `record_folders` (spec
 * §4.2). Adding a category server-side must never need a frontend edit. The
 * per-folder counts of the summary strip come from the tree's `record_counts`.
 *
 * A historic vigencia is read-only: the upload CTA, the renewal CTA and the
 * "cambiar vigencia" door are hidden, and the "histórico" chip says why.
 */
import * as React from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import type { ColumnDef } from "@tanstack/react-table";
import {
  CalendarClock,
  Download,
  ExternalLink,
  FileStack,
  FileText,
  Lock,
  RefreshCw,
  ShieldCheck,
  Sparkles,
  Upload,
  Users,
} from "lucide-react";

import { PageHeader } from "@/components/common/PageHeader";
import { DataTable } from "@/components/common/DataTable";
import { NotesPanel } from "@/components/common/NotesPanel";
import { SuggestionForm } from "@/components/common/SuggestionForm";
import { AntecedentesReview } from "@/components/common/AntecedentesReview";
import { ExpedienteView } from "@/components/expedientes/ExpedienteView";
import { FadeUp } from "@/components/common/motion";
import { Journey } from "@/components/groups/Journey";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  ConfidenceBadge,
  DisabledHint,
  EmptyState,
  ErrorBanner,
  MonoChip,
  Section,
  resolveFileUrl,
  uf,
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
import { useNavigator } from "@/api/navigator";
import {
  useCaseFile,
  useCaseFileDocuments,
  useCaseFileTimeline,
  useCaseFileTransitions,
  useTransitionCaseFile,
} from "@/api/caseFiles";
import { useCategoryRegistry, useExtractDocument } from "@/api/ai";
import { useProcessAntecedentes, useRamoSchema } from "@/api/antecedentes";
import { useUploadDocument } from "@/api/documents";
import { useProposals } from "@/api/proposals";
import { useQuotes } from "@/api/quotes";
import { useCan } from "@/lib/permissions";
import { formatDate, formatDateTime } from "@/lib/format";
import { cn } from "@/lib/utils";
import { useTimelineCopy } from "@/components/groups/timelineCopy";
import type {
  AntecedentesSuggestion,
  CaseDocument,
  CaseDocumentGroups,
  CaseStage,
  DocumentCategory,
  DocumentExtractionResponse,
  GroupTree,
  Proposal,
  QuoteRequest,
  RecordFolder,
  TreeLineNode,
  TreePeriodNode,
  TreePolicyNode,
} from "@/api/types";

type TabKey =
  | "records"
  | "antecedentes"
  | "quotes"
  | "proposals"
  | "policies"
  | "renewal"
  | "notes"
  | "timeline";

/** The `?tab=` URL vocabulary — links from elsewhere depend on these values. */
const TABS: TabKey[] = [
  "records",
  "antecedentes",
  "quotes",
  "proposals",
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
  const [pendingStage, setPendingStage] = React.useState<CaseStage | null>(null);

  const canUpload = useCan("Documents", "Upload");
  const canCreateCase = useCan("CaseFiles", "Create");
  const canComment = useCan("CaseFiles", "Comment");
  const canManage = useCan("CaseFiles", "Manage");
  const canSubmit = useCan("CaseFiles", "Submit");
  const canViewQuotes = useCan("Quotes", "View");
  const canViewProposals = useCan("Proposals", "View");

  const raw = params.get("tab") as TabKey | null;
  const tab: TabKey = raw && TABS.includes(raw) ? raw : "records";
  const setTab = (value: string) => {
    const next = new URLSearchParams(params);
    next.set("tab", value);
    if (value !== "records") next.delete("folder");
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

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        eyebrow={
          <GroupCrumbs
            items={[
              { label: t("group.title"), to: "/groups" },
              { label: group.data?.name ?? t("group.one"), to: `/groups/${groupId}` },
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

      {/* The hero: where the case is on the broker journey and what comes
          next. Enabled/disabled comes verbatim from GET /case-files/{id}/
          transitions — the Journey never re-implements a stage guard. */}
      {c ? (
        <FadeUp>
          <Card className="p-5">
            <Journey
              variant="hero"
              kind={c.kind}
              stage={c.stage}
              transitions={transitions.data?.options ?? []}
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

        <TabsContent value="records" className="mt-4">
          <RecordsTab
            caseId={caseId}
            insuranceLineId={c?.insurance_line_id ?? null}
            recordCounts={node?.line.account.record_counts}
            onRecordsChanged={() => void tree.refetch()}
            canUpload={canUpload.allowed && !isHistoric}
            uploadHint={
              isHistoric ? t("tree.readOnly") : t("account.records.noUploadPermission")
            }
            canAnalyze={canUpload.allowed}
            onRegistered={() => setTab("antecedentes")}
          />
        </TabsContent>

        <TabsContent value="antecedentes" className="mt-4">
          <ExpedienteView caseId={caseId} />
        </TabsContent>

        <TabsContent value="quotes" className="mt-4">
          {canViewQuotes.allowed ? (
            <QuotesTab caseId={caseId} />
          ) : (
            <Card>
              <EmptyState title={t("account.quotes.noPermission")} />
            </Card>
          )}
        </TabsContent>

        <TabsContent value="proposals" className="mt-4">
          {canViewProposals.allowed ? (
            <ProposalsTab caseId={caseId} />
          ) : (
            <Card>
              <EmptyState title={t("account.proposals.noPermission")} />
            </Card>
          )}
        </TabsContent>

        <TabsContent value="policies" className="mt-4">
          <PoliciesTab groupId={groupId} line={node?.line} isLoading={tree.isLoading} />
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
// ANTECEDENTES — summary strip + the server-owned folders + the file explorer
// =============================================================================

function sizeLabel(bytes: number | null): string {
  if (!bytes) return "—";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function RecordsTab({
  caseId,
  insuranceLineId,
  recordCounts,
  onRecordsChanged,
  canUpload,
  uploadHint,
  canAnalyze,
  onRegistered,
}: {
  caseId: number;
  /** The account's ramo — resolves whether an antecedentes schema exists. */
  insuranceLineId: number | null;
  /** Per-folder counts from the tree (`record_counts`, keyed by folder key). */
  recordCounts: Record<string, number> | undefined;
  /** Refetches the tree so `record_counts` follows an upload. */
  onRecordsChanged: () => void;
  canUpload: boolean;
  uploadHint: string;
  canAnalyze: boolean;
  /** Fired after the expediente is registered — switches to the expediente tab. */
  onRegistered: () => void;
}) {
  const { t } = useTranslation("accounts");
  const { t: ta } = useTranslation("antecedentes");
  const [params, setParams] = useSearchParams();

  const navigator = useNavigator();
  const documents = useCaseFileDocuments(caseId);
  const registry = useCategoryRegistry();
  const extract = useExtractDocument();

  // ── Antecedentes: consolidate every doc into the ramo schema (rule 6:
  // suggest → the human validates & completes → register). The CTA is gated by
  // the upload grant and disabled with a reason when the ramo has no schema.
  const ramoSchema = useRamoSchema(insuranceLineId);
  const process = useProcessAntecedentes();
  const [antecedentes, setAntecedentes] = React.useState<AntecedentesSuggestion | null>(null);

  const processHint = !canAnalyze
    ? ta("process.noPermission")
    : ramoSchema.isLoading
      ? ta("process.loadingSchema")
      : !ramoSchema.data
        ? ta("process.noSchema")
        : null;

  const onProcess = () => {
    setAntecedentes(null);
    process.mutate(
      { case_file_id: caseId },
      { onSuccess: (data) => setAntecedentes(data) },
    );
  };

  // Suggest -> human edit -> confirm, through the ONE shared component
  // (`components/common/SuggestionForm`); this page never grows a second copy.
  // The provenance card inside it surfaces the extraction's status and
  // confidence — there is no list-extractions endpoint, so what the response
  // carries is what gets rendered.
  const [analyzed, setAnalyzed] = React.useState<CaseDocument | null>(null);
  const [suggestion, setSuggestion] = React.useState<DocumentExtractionResponse | null>(
    null,
  );

  const analyze = (doc: CaseDocument) => {
    setAnalyzed(doc);
    setSuggestion(null);
    extract.mutate(
      { document_id: doc.id, category: doc.category },
      { onSuccess: (data) => setSuggestion(data) },
    );
  };

  const spec = suggestion
    ? registry.data?.items.find(
        (item) =>
          item.category === suggestion.category ||
          item.canonical_category === suggestion.canonical_category,
      )
    : undefined;

  // "Analizar" is offered only when the category has a schema in the registry
  // (`GET /ai/categories`) — otherwise it renders disabled with the reason.
  const extractable = React.useMemo(() => {
    const set = new Set<string>();
    for (const item of registry.data?.items ?? []) {
      set.add(item.category);
      set.add(item.canonical_category);
    }
    return set;
  }, [registry.data]);

  const folders: RecordFolder[] = navigator.data?.record_folders ?? [];
  const active = params.get("folder");

  const setFolder = (key: string | null) => {
    const next = new URLSearchParams(params);
    next.set("tab", "records");
    if (key) next.set("folder", key);
    else next.delete("folder");
    setParams(next, { replace: true });
  };

  // Every document of the folder, bucketed by the SERVER's category mapping.
  const all: CaseDocument[] = React.useMemo(
    () => (documents.data?.sections ?? []).flatMap((section) => section.documents),
    [documents.data],
  );

  const byFolder = React.useMemo(() => {
    const map = new Map<string, CaseDocument[]>();
    for (const folder of folders) {
      const wanted = new Set<string>(folder.categories);
      map.set(
        folder.key,
        all.filter((doc) => wanted.has(doc.category)),
      );
    }
    return map;
  }, [folders, all]);

  const activeFolder = folders.find((folder) => folder.key === active) ?? null;

  const onUploaded = () => {
    void documents.refetch();
    onRecordsChanged();
  };

  if (documents.isLoading || navigator.isLoading) {
    return <Skeleton className="h-64 w-full" />;
  }

  return (
    <div className="flex flex-col gap-4">
      {documents.isError ? <ErrorBanner error={documents.error} /> : null}

      {/* Consolidate the whole account into the ramo antecedentes expediente. */}
      <Card className="flex flex-wrap items-center justify-between gap-3 p-4">
        <div className="min-w-0">
          <h3 className="text-h3 tracking-tight text-ink">{ta("process.title")}</h3>
          <p className="text-caption text-ink-3">{ta("process.description")}</p>
        </div>
        <DisabledHint hint={processHint}>
          <Button
            disabled={!!processHint || process.isPending}
            onClick={onProcess}
          >
            <FileStack className="h-4 w-4" />
            {process.isPending
              ? ta("process.processing")
              : antecedentes
                ? ta("process.reprocess")
                : ta("process.cta")}
          </Button>
        </DisabledHint>
      </Card>

      {process.isError ? <ErrorBanner error={process.error} /> : null}

      {antecedentes ? (
        <AntecedentesReview
          caseId={caseId}
          suggestion={antecedentes}
          canConfirm={canAnalyze}
          onRegistered={() => {
            setAntecedentes(null);
            onRecordsChanged();
            onRegistered();
          }}
        />
      ) : null}

      {/* Summary strip — one small stat tile per server folder. */}
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6">
        <StatTile label={t("account.records.totalFiles")} value={all.length} />
        {folders.map((folder) => (
          <StatTile
            key={folder.key}
            label={t(`folders.${folder.key}`, { defaultValue: folder.key })}
            value={recordCounts?.[folder.key] ?? byFolder.get(folder.key)?.length ?? 0}
          />
        ))}
      </div>

      {/* Folder sub-tabs (server-owned) + the always-present file explorer. */}
      <Tabs
        value={activeFolder ? activeFolder.key : "all"}
        onValueChange={(value) => setFolder(value === "all" ? null : value)}
      >
        <div className="overflow-x-auto">
          <TabsList variant="segmented">
            {folders.map((folder) => (
              <TabsTrigger key={folder.key} value={folder.key}>
                {t(`folders.${folder.key}`, { defaultValue: folder.key })}
                <span className="ml-1.5 text-caption tabular-nums text-ink-3">
                  {byFolder.get(folder.key)?.length ?? 0}
                </span>
              </TabsTrigger>
            ))}
            <TabsTrigger value="all">
              {t("account.records.allFiles")}
              <span className="ml-1.5 text-caption tabular-nums text-ink-3">
                {all.length}
              </span>
            </TabsTrigger>
          </TabsList>
        </div>
      </Tabs>

      {activeFolder ? (
        <FolderPane
          caseId={caseId}
          folder={activeFolder}
          documents={byFolder.get(activeFolder.key) ?? []}
          canUpload={canUpload}
          uploadHint={uploadHint}
          onUploaded={onUploaded}
          extractable={extractable}
          canAnalyze={canAnalyze}
          analyzingDocumentId={extract.isPending ? (analyzed?.id ?? null) : null}
          onAnalyze={analyze}
        />
      ) : (
        <AllFilesExplorer
          groups={documents.data}
          extractable={extractable}
          canAnalyze={canAnalyze}
          analyzingDocumentId={extract.isPending ? (analyzed?.id ?? null) : null}
          onAnalyze={analyze}
        />
      )}

      {extract.isError ? <ErrorBanner error={extract.error} /> : null}

      {suggestion ? (
        <SuggestionForm
          result={suggestion}
          spec={spec}
          documentName={analyzed?.original_name ?? null}
          canConfirm={canAnalyze}
          onConfirmed={() => {
            setSuggestion(null);
            setAnalyzed(null);
            onUploaded();
          }}
        />
      ) : null}
    </div>
  );
}

/** Small stat tile — label above a tabular number; never a heavy card. */
function StatTile({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-lg border border-line bg-bone px-3 py-2">
      <div className="truncate text-caption text-ink-3">{label}</div>
      <div className="text-h2 tabular-nums text-ink">{value}</div>
    </div>
  );
}

/**
 * One document as a quiet row: icon, name (opens the file), category label,
 * size/date caption, then descargar + analizar. The AI gate mirrors
 * `SectionAccordion`: no schema or no permission renders the reason, never a
 * dead button.
 */
function DocumentRow({
  doc,
  extractable,
  canAnalyze,
  busy,
  onAnalyze,
}: {
  doc: CaseDocument;
  extractable: Set<string>;
  canAnalyze: boolean;
  busy: boolean;
  onAnalyze: (doc: CaseDocument) => void;
}) {
  const { t: td } = useTranslation("documents");
  const { t: tc } = useTranslation("common");
  const href = resolveFileUrl(doc.url);
  const canExtract = extractable.has(doc.category);

  return (
    <li className="flex flex-wrap items-center gap-3 px-5 py-2.5">
      <FileText className="h-4 w-4 shrink-0 text-ink-3" aria-hidden />
      <div className="min-w-0 flex-1">
        {href ? (
          <a
            href={href}
            target="_blank"
            rel="noreferrer"
            className="block truncate text-body font-medium text-ink no-underline hover:text-brand-deep"
          >
            {doc.original_name}
          </a>
        ) : (
          <span className="block truncate text-body font-medium text-ink">
            {doc.original_name}
          </span>
        )}
        <p className="truncate text-caption text-ink-3">
          {td(`categories.${doc.category}`, { defaultValue: doc.category_label })}
          {" · "}
          {sizeLabel(doc.size_bytes)}
          {doc.created_at ? ` · ${formatDate(doc.created_at)}` : ""}
        </p>
      </div>
      {doc.document_code ? <MonoChip>{doc.document_code}</MonoChip> : null}
      {href ? (
        <Button size="sm" variant="ghost" asChild>
          <a href={href} target="_blank" rel="noreferrer">
            <Download className="h-3.5 w-3.5" />
            {tc("actions.download")}
          </a>
        </Button>
      ) : null}
      <DisabledHint
        hint={
          !canExtract ? td("ai.noSchema") : !canAnalyze ? td("ai.noPermission") : null
        }
      >
        <Button
          size="sm"
          variant="secondary"
          disabled={!canExtract || !canAnalyze || busy}
          onClick={() => onAnalyze(doc)}
        >
          <Sparkles className="h-3.5 w-3.5" />
          {busy ? tc("actions.loading") : td("ai.analyze")}
        </Button>
      </DisabledHint>
    </li>
  );
}

/**
 * "Todos los archivos" — the raw file explorer: every document of the case,
 * grouped by sub-expediente section, as quiet rows inside one bordered panel.
 */
function AllFilesExplorer({
  groups,
  extractable,
  canAnalyze,
  analyzingDocumentId,
  onAnalyze,
}: {
  groups: CaseDocumentGroups | undefined;
  extractable: Set<string>;
  canAnalyze: boolean;
  analyzingDocumentId: number | null;
  onAnalyze: (doc: CaseDocument) => void;
}) {
  const { t } = useTranslation("accounts");
  const { t: td } = useTranslation("documents");

  const sections = (groups?.sections ?? []).filter((s) => s.documents.length > 0);

  if (!sections.length) {
    return (
      <Card>
        <EmptyState
          icon={<FileText className="h-6 w-6" />}
          title={t("empty.records")}
          hint={t("empty.recordsHint")}
        />
      </Card>
    );
  }

  return (
    <Card className="overflow-hidden">
      {sections.map((section, index) => {
        const key = section.section ?? "unfiled";
        return (
          <div key={key}>
            <div
              className={cn(
                "flex items-baseline gap-2 border-b border-line bg-paper-2/60 px-5 py-2",
                index > 0 && "border-t",
              )}
            >
              <span className="text-label font-medium text-ink">
                {section.section
                  ? td(`sections.${section.section}`, { defaultValue: section.label })
                  : td("sections.unfiled", { defaultValue: section.label })}
              </span>
              <span className="text-caption tabular-nums text-ink-3">
                {section.documents.length}
              </span>
            </div>
            <ul className="divide-y divide-line">
              {section.documents.map((doc) => (
                <DocumentRow
                  key={doc.id}
                  doc={doc}
                  extractable={extractable}
                  canAnalyze={canAnalyze}
                  busy={analyzingDocumentId === doc.id}
                  onAnalyze={onAnalyze}
                />
              ))}
            </ul>
          </div>
        );
      })}
    </Card>
  );
}

/**
 * One ANTECEDENTES folder. The upload posts `entity_type=case_file`, which the
 * documents router turns into `case_file_id = entity_id` and then derives the
 * sub-expediente from the category registry — so the file lands INSIDE this
 * folder, not loose next to it. The upload category is `folder.categories[0]`,
 * the server-declared default for the bucket.
 */
function FolderPane({
  caseId,
  folder,
  documents,
  canUpload,
  uploadHint,
  onUploaded,
  extractable,
  canAnalyze,
  analyzingDocumentId,
  onAnalyze,
}: {
  caseId: number;
  folder: RecordFolder;
  documents: CaseDocument[];
  canUpload: boolean;
  uploadHint: string;
  onUploaded: () => void;
  extractable: Set<string>;
  canAnalyze: boolean;
  analyzingDocumentId: number | null;
  onAnalyze: (doc: CaseDocument) => void;
}) {
  const { t } = useTranslation("accounts");
  const { t: td } = useTranslation("documents");
  const upload = useUploadDocument();
  const inputRef = React.useRef<HTMLInputElement | null>(null);

  const category = folder.categories[0] as DocumentCategory | undefined;

  const onPick = (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file || !category) return;
    upload.mutate(
      { file, entity_type: "case_file", entity_id: caseId, category },
      { onSuccess: onUploaded },
    );
  };

  return (
    <Section
      title={t(`folders.${folder.key}`, { defaultValue: folder.key })}
      description={t("account.records.folderHint", {
        categories: folder.categories
          .map((value) => td(`categories.${value}`, { defaultValue: value }))
          .join(" · "),
      })}
      bodyClassName="p-0"
      actions={
        <>
          <input
            ref={inputRef}
            type="file"
            className="hidden"
            onChange={onPick}
            aria-hidden
          />
          <DisabledHint hint={canUpload && category ? null : uploadHint}>
            <Button
              size="sm"
              variant="secondary"
              disabled={!canUpload || !category || upload.isPending}
              onClick={() => inputRef.current?.click()}
            >
              <Upload className="h-4 w-4" />
              {upload.isPending ? t("account.records.uploading") : t("account.records.upload")}
            </Button>
          </DisabledHint>
        </>
      }
    >
      {upload.isError ? (
        <ErrorBanner error={upload.error} className="m-5 mb-0" />
      ) : null}

      {documents.length === 0 ? (
        <EmptyState
          icon={<FileText className="h-6 w-6" />}
          title={t("empty.records")}
          hint={t("empty.recordsHint")}
        />
      ) : (
        <ul className="divide-y divide-line">
          {documents.map((doc) => (
            <DocumentRow
              key={doc.id}
              doc={doc}
              extractable={extractable}
              canAnalyze={canAnalyze}
              busy={analyzingDocumentId === doc.id}
              onAnalyze={onAnalyze}
            />
          ))}
        </ul>
      )}
    </Section>
  );
}

// =============================================================================
// Cotizaciones / Propuestas
// =============================================================================

function QuotesTab({ caseId }: { caseId: number }) {
  const { t } = useTranslation("accounts");
  const { t: tq } = useTranslation("quotes");
  const navigate = useNavigate();
  const quotes = useQuotes({ case_file_id: caseId, limit: 100 });

  const columns = React.useMemo<ColumnDef<QuoteRequest, unknown>[]>(
    () => [
      {
        id: "object",
        header: tq("table.object"),
        accessorFn: (row) => row.insured_object ?? "—",
      },
      {
        id: "declared",
        header: tq("table.declared"),
        accessorFn: (row) => row.declared_value_uf ?? "",
        cell: ({ row }) => (
          <span className="tabular-nums">{uf(row.original.declared_value_uf)}</span>
        ),
      },
      {
        id: "proposals",
        header: tq("table.proposals"),
        accessorFn: (row) => row.proposal_count,
      },
      {
        id: "status",
        header: tq("table.status"),
        cell: ({ row }) => (
          <Badge variant="neutral">
            {tq(`status.${row.original.status}`, { defaultValue: row.original.status })}
          </Badge>
        ),
      },
    ],
    [t, tq],
  );

  if (quotes.isError) return <ErrorBanner error={quotes.error} />;

  return (
    <DataTable
      columns={columns}
      data={quotes.data?.items ?? []}
      isLoading={quotes.isLoading}
      emptyMessage={t("account.quotes.empty")}
      onRowClick={(row) => navigate(`/quotes/${row.id}`)}
    />
  );
}

function ProposalsTab({ caseId }: { caseId: number }) {
  const { t } = useTranslation("accounts");
  const { t: tp } = useTranslation("proposals");
  const navigate = useNavigate();
  const proposals = useProposals({ case_file_id: caseId, limit: 100 });

  const columns = React.useMemo<ColumnDef<Proposal, unknown>[]>(
    () => [
      {
        id: "insurer",
        header: tp("table.insurer"),
        accessorFn: (row) => row.insurer?.legal_name ?? `#${row.insurer_id}`,
      },
      {
        id: "total",
        header: tp("table.total"),
        accessorFn: (row) => row.total_premium_uf ?? "",
        cell: ({ row }) => (
          <span className="tabular-nums">{uf(row.original.total_premium_uf)}</span>
        ),
      },
      {
        id: "status",
        header: tp("table.status"),
        cell: ({ row }) => (
          <Badge variant="neutral">
            {tp(`status.${row.original.status}`, { defaultValue: row.original.status })}
          </Badge>
        ),
      },
      {
        // A proposal born from an AI read carries its extraction's confidence
        // on the row itself — surfaced here; there is no list-extractions
        // endpoint to ask for more.
        id: "ai",
        header: t("account.proposals.ai"),
        cell: ({ row }) =>
          row.original.extraction_id ? (
            <ConfidenceBadge value={row.original.extraction_confidence} />
          ) : (
            <span className="text-caption text-ink-3">—</span>
          ),
      },
      {
        id: "confirmed",
        header: t("account.proposals.confirmed"),
        cell: ({ row }) =>
          row.original.is_confirmed ? (
            <Badge variant="success">{t("account.proposals.yes")}</Badge>
          ) : (
            <Badge variant="outline">{t("account.proposals.no")}</Badge>
          ),
      },
    ],
    [t, tp],
  );

  if (proposals.isError) return <ErrorBanner error={proposals.error} />;

  return (
    <DataTable
      columns={columns}
      data={proposals.data?.items ?? []}
      isLoading={proposals.isLoading}
      emptyMessage={t("account.proposals.empty")}
      onRowClick={(row) => navigate(`/proposals/${row.id}`)}
    />
  );
}

// =============================================================================
// Pólizas
// =============================================================================

function PoliciesTab({
  groupId,
  line,
  isLoading,
}: {
  groupId: number;
  line: TreeLineNode | undefined;
  isLoading: boolean;
}) {
  const { t } = useTranslation("accounts");

  if (isLoading) return <Skeleton className="h-40 w-full" />;

  const policies: TreePolicyNode[] = line?.policies ?? [];
  if (policies.length === 0) {
    return (
      <Card>
        <EmptyState
          icon={<ShieldCheck className="h-6 w-6" />}
          title={t("empty.policies")}
        />
      </Card>
    );
  }

  return (
    <Card className="overflow-hidden">
      <ul className="divide-y divide-line">
        {policies.map((policy, index) => {
          const children = [
            ...policy.children.endorsement,
            ...policy.children.collection,
            ...policy.children.claim,
          ];
          return (
            <li key={policy.id} className="px-5 py-3.5">
              <Link
                to={`/groups/${groupId}/policies/${policy.id}`}
                className="flex flex-wrap items-center gap-2 no-underline"
              >
                <ShieldCheck className="h-4 w-4 shrink-0 text-brand" />
                <span className="text-label tabular-nums text-ink">
                  {t("tree.policyLabel", { n: index + 1, number: policy.policy_number })}
                </span>
                <span className="text-caption text-ink-3">
                  {policy.insurer_name ?? "—"}
                </span>
                <span className="ml-auto text-caption text-ink-3">
                  {policy.extended_end_date
                    ? t("tree.extendedUntil", {
                        date: formatDate(policy.extended_end_date),
                      })
                    : periodDates(policy.start_date, policy.end_date)}
                </span>
              </Link>
              {children.length > 0 ? (
                <div className="mt-2 flex flex-wrap gap-1.5 pl-6">
                  {children.map((child) => (
                    <Link
                      key={child.case_file_id}
                      to={`/cases/${child.case_file_id}`}
                      className="no-underline"
                    >
                      <Badge variant="outline">
                        {child.reference ?? `#${child.case_file_id}`}
                      </Badge>
                    </Link>
                  ))}
                </div>
              ) : null}
            </li>
          );
        })}
      </ul>
    </Card>
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

  // Until the tree answers we do not know WHY renewal is or is not allowed, and
  // a disabled button with a guessed reason is worse than a skeleton.
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
