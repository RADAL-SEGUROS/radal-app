/**
 * The expediente detail BODY (spec §10.2), extracted verbatim from
 * `pages/cases/detail.tsx` so the group family can render the same expediente
 * inside `GroupShell` while the legacy `/cases/:caseId` route keeps working.
 * The page is now a thin wrapper; nothing in this file changed behaviourally.
 *
 * Header + the Journey hero + seven tabs. Two rules shape everything here:
 *   - the stage rail is driven by `GET /case-files/{id}/transitions`, so a
 *     button the server would refuse is disabled with the server's own reason;
 *   - every AI surface is suggest → edit → confirmar. The Resumen tab's summary
 *     card and the Documentos tab's "analizar con IA" both end in a human
 *     pressing "confirmar", never in an automatic write.
 */
import * as React from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import {
  ArrowLeft,
  ClipboardList,
  FileStack,
  History,
  MessageSquare,
  Package,
  Send,
  Shield,
  Sparkles,
} from "lucide-react";

import { PageHeader } from "@/components/common/PageHeader";
import { KpiCard } from "@/components/common/KpiCard";
import { FadeUp } from "@/components/common/motion";
import { Journey } from "@/components/groups/Journey";
import { SectionAccordion } from "@/components/common/SectionAccordion";
import { NotesPanel } from "@/components/common/NotesPanel";
import { SuggestionForm } from "@/components/common/SuggestionForm";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  DisabledHint,
  EmptyState,
  ErrorBanner,
  KeyValue,
  MonoChip,
  Section,
  StatusBadge,
  uf,
} from "@/pages/proposals/shared";
import {
  useCaseFile,
  useCaseFileDocuments,
  useCaseFileTimeline,
  useCaseFileTransitions,
  useTransitionCaseFile,
  useUpdateCaseFile,
} from "@/api/caseFiles";
import {
  useCategoryRegistry,
  useExtractDocument,
  useSummarizeCaseFile,
} from "@/api/ai";
import { useQuotes } from "@/api/quotes";
import { useProposals } from "@/api/proposals";
import { useCan } from "@/lib/permissions";
import { formatDate, formatDateTime } from "@/lib/format";
import type {
  CaseDocument,
  CaseStage,
  DocumentExtractionResponse,
} from "@/api/types";

export function CaseDetailBody({ caseId }: { caseId: number }) {
  const { t } = useTranslation("cases");
  const { t: tc } = useTranslation("common");
  const { t: td } = useTranslation("documents");
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();

  const detail = useCaseFile(caseId);
  const transitions = useCaseFileTransitions(caseId);
  const transition = useTransitionCaseFile(caseId);
  const [pendingStage, setPendingStage] = React.useState<CaseStage | null>(null);

  const perms = {
    edit: useCan("CaseFiles", "Edit"),
    submit: useCan("CaseFiles", "Submit"),
    manage: useCan("CaseFiles", "Manage"),
    comment: useCan("CaseFiles", "Comment"),
    upload: useCan("Documents", "Upload"),
    remove: useCan("CaseFiles", "Delete"),
  };

  const tab = params.get("tab") ?? "summary";
  const setTab = (value: string) => {
    const next = new URLSearchParams(params);
    next.set("tab", value);
    setParams(next, { replace: true });
  };

  const onTransition = (stage: CaseStage) => {
    setPendingStage(stage);
    transition.mutate(
      { to_stage: stage },
      { onSettled: () => setPendingStage(null) },
    );
  };

  if (detail.isLoading) {
    return (
      <div className="flex flex-col gap-4">
        <Skeleton className="h-16 w-full" />
        <Skeleton className="h-20 w-full" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }

  if (detail.isError || !detail.data) {
    return <ErrorBanner error={detail.error ?? tc("state.error")} />;
  }

  const c = detail.data;

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        eyebrow={
          <button
            type="button"
            onClick={() => navigate("/cases")}
            className="flex items-center gap-1.5 hover:text-brand-deep"
          >
            <ArrowLeft className="h-3.5 w-3.5" />
            {t("list.title")}
          </button>
        }
        title={c.title}
        subtitle={
          <span className="flex flex-wrap items-center gap-2">
            <MonoChip>{c.reference ?? `#${c.id}`}</MonoChip>
            <span>{c.client_legal_name ?? "—"}</span>
            {c.client_rut ? <MonoChip>{c.client_rut}</MonoChip> : null}
            {c.insurance_line_name ? <span>· {c.insurance_line_name}</span> : null}
            {c.period ? <span>· {c.period}</span> : null}
          </span>
        }
        actions={
          <>
            <Badge variant="brand">{t(`stages.${c.stage}`, { defaultValue: c.stage })}</Badge>
            <Badge variant="neutral">{t(`kinds.${c.kind}`, { defaultValue: c.kind })}</Badge>
            <StatusBadge
              value={c.status}
              label={t(`statuses.${c.status}`, { defaultValue: c.status })}
            />
            <Button size="sm" variant="secondary" asChild>
              <Link to={`/cases/${caseId}/packs`}>
                <Package className="h-4 w-4" />
                {t("tabs.packs")}
              </Link>
            </Button>
          </>
        }
      />

      <FadeUp>
        <Card className="p-5">
          <Journey
            variant="hero"
            kind={c.kind}
            stage={c.stage}
            transitions={transitions.data?.options ?? []}
            canTransition={perms.submit.allowed && !perms.submit.isLoading}
            isPending={transition.isPending}
            pendingStage={pendingStage}
            onTransition={onTransition}
          />
        </Card>
      </FadeUp>

      {transition.isError ? <ErrorBanner error={transition.error} /> : null}

      <Tabs value={tab} onValueChange={setTab}>
        <TabsList variant="underline" className="flex-wrap">
          <TabsTrigger value="summary">{t("tabs.summary")}</TabsTrigger>
          <TabsTrigger value="documents">{t("tabs.documents")}</TabsTrigger>
          <TabsTrigger value="quotes">{t("tabs.quotes")}</TabsTrigger>
          <TabsTrigger value="proposal">{t("tabs.proposal")}</TabsTrigger>
          <TabsTrigger value="postsale">{t("tabs.postsale")}</TabsTrigger>
          <TabsTrigger value="notes">{t("tabs.notes")}</TabsTrigger>
          <TabsTrigger value="timeline">{t("tabs.timeline")}</TabsTrigger>
        </TabsList>

        <TabsContent value="summary" className="mt-4">
          <SummaryTab caseId={caseId} canEdit={perms.edit.allowed} />
        </TabsContent>

        <TabsContent value="documents" className="mt-4">
          <DocumentsTab caseId={caseId} canAnalyze={perms.upload.allowed} />
        </TabsContent>

        <TabsContent value="quotes" className="mt-4">
          <QuotesTab placementId={c.placement_id} />
        </TabsContent>

        <TabsContent value="proposal" className="mt-4">
          <ProposalTab caseId={caseId} placementId={c.placement_id} />
        </TabsContent>

        <TabsContent value="postsale" className="mt-4">
          <PostSaleTab caseId={caseId} />
        </TabsContent>

        <TabsContent value="notes" className="mt-4">
          <NotesPanel
            entityType="case_file"
            entityId={caseId}
            canComment={perms.comment.allowed}
            canDelete={perms.remove.allowed}
          />
        </TabsContent>

        <TabsContent value="timeline" className="mt-4">
          <TimelineTab caseId={caseId} />
        </TabsContent>
      </Tabs>

      <p className="text-caption text-text-muted">
        {td("ai.disclaimer")}
      </p>
    </div>
  );
}

// =============================================================================
// Resumen
// =============================================================================

function SummaryTab({ caseId, canEdit }: { caseId: number; canEdit: boolean }) {
  const { t } = useTranslation("cases");
  const { t: tc } = useTranslation("common");
  const detail = useCaseFile(caseId);
  const summarize = useSummarizeCaseFile(caseId);
  const update = useUpdateCaseFile(caseId);
  const [draft, setDraft] = React.useState<string | null>(null);

  const c = detail.data;
  if (!c) return <Skeleton className="h-48 w-full" />;

  const text = draft ?? summarize.data?.text ?? c.summary ?? "";
  const isSuggestion = draft !== null || (!!summarize.data && !c.summary);

  const sectionTotal = c.documents_by_section.reduce((acc, s) => acc + s.count, 0);

  return (
    <div className="flex flex-col gap-4">
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <KpiCard label={t("kpi.documents")} countTo={sectionTotal} icon={<FileStack />} />
        <KpiCard
          label={t("kpi.proposals")}
          countTo={c.proposals_count}
          tone="action"
          icon={<Send />}
        />
        <KpiCard
          label={t("kpi.packs")}
          countTo={c.packs_count}
          tone="brand"
          icon={<Package />}
        />
        <KpiCard
          label={t("kpi.notes")}
          countTo={c.notes_count}
          tone="warn"
          icon={<MessageSquare />}
        />
      </div>

      <Section
        title={t("summary.title")}
        description={t("summary.description")}
        actions={
          <>
            <DisabledHint hint={canEdit ? null : t("summary.noPermission")}>
              <Button
                size="sm"
                variant="secondary"
                disabled={!canEdit || summarize.isPending}
                onClick={() => {
                  setDraft(null);
                  summarize.mutate();
                }}
              >
                <Sparkles className="h-4 w-4" />
                {summarize.isPending ? tc("actions.loading") : t("summary.suggest")}
              </Button>
            </DisabledHint>
            <DisabledHint hint={canEdit ? null : t("summary.noPermission")}>
              <Button
                size="sm"
                disabled={!canEdit || !text.trim() || update.isPending}
                onClick={() =>
                  update.mutate(
                    { summary: text },
                    { onSuccess: () => setDraft(null) },
                  )
                }
              >
                {update.isPending ? tc("actions.loading") : t("summary.confirm")}
              </Button>
            </DisabledHint>
          </>
        }
      >
        {isSuggestion ? (
          <div className="mb-3 flex flex-wrap items-center gap-2">
            <Badge variant="action" className="gap-1">
              <Sparkles className="h-3 w-3" />
              {t("summary.unconfirmed")}
            </Badge>
            {summarize.data ? (
              <>
                <MonoChip>{summarize.data.model}</MonoChip>
                <MonoChip>{summarize.data.prompt_version}</MonoChip>
              </>
            ) : null}
          </div>
        ) : null}

        <textarea
          rows={8}
          value={text}
          disabled={!canEdit}
          onChange={(e) => setDraft(e.target.value)}
          placeholder={t("summary.placeholder")}
          className="w-full rounded-lg border border-line bg-bone px-3 py-2 text-body text-ink outline-none focus-visible:border-brand focus-visible:ring-2 focus-visible:ring-brand-ring disabled:opacity-60"
        />
        {summarize.isError ? <ErrorBanner error={summarize.error} className="mt-3" /> : null}
        {update.isError ? <ErrorBanner error={update.error} className="mt-3" /> : null}
      </Section>

      <Section title={t("summary.factsTitle")}>
        <div className="grid gap-5 sm:grid-cols-2 lg:grid-cols-4">
          <KeyValue label={t("table.reference")} value={c.reference ?? "—"} mono />
          <KeyValue
            label={t("table.kind")}
            value={t(`kinds.${c.kind}`, { defaultValue: c.kind })}
          />
          <KeyValue label={t("facts.sequence")} value={`${c.sequence_no} · v${c.version}`} />
          <KeyValue label={t("facts.opened")} value={formatDate(c.opened_at)} />
          <KeyValue label={t("table.due")} value={formatDate(c.due_at)} />
          <KeyValue label={t("facts.closed")} value={formatDate(c.closed_at)} />
          <KeyValue label={t("facts.placementStatus")} value={c.placement_status ?? "—"} />
          <KeyValue label={t("facts.policyNumber")} value={c.policy_number ?? "—"} mono />
        </div>
      </Section>
    </div>
  );
}

// =============================================================================
// Documentos
// =============================================================================

function DocumentsTab({ caseId, canAnalyze }: { caseId: number; canAnalyze: boolean }) {
  const { t } = useTranslation("cases");
  const groups = useCaseFileDocuments(caseId);
  const registry = useCategoryRegistry();
  const extract = useExtractDocument();

  const [active, setActive] = React.useState<CaseDocument | null>(null);
  const [result, setResult] = React.useState<DocumentExtractionResponse | null>(null);

  const analyze = (doc: CaseDocument) => {
    setActive(doc);
    setResult(null);
    extract.mutate(
      { document_id: doc.id, category: doc.category },
      { onSuccess: (data) => setResult(data) },
    );
  };

  const spec = result
    ? registry.data?.items.find(
        (s) =>
          s.category === result.category || s.canonical_category === result.canonical_category,
      )
    : undefined;

  return (
    <div className="flex flex-col gap-4">
      <Section title={t("documents.title")} description={t("documents.description")}>
        {groups.isLoading ? (
          <Skeleton className="h-40 w-full" />
        ) : (
          <SectionAccordion
            groups={groups.data}
            specs={registry.data?.items}
            canAnalyze={canAnalyze}
            analyzingDocumentId={extract.isPending ? (active?.id ?? null) : null}
            onAnalyze={analyze}
          />
        )}
      </Section>

      {extract.isError ? <ErrorBanner error={extract.error} /> : null}

      {result ? (
        <SuggestionForm
          result={result}
          spec={spec}
          documentName={active?.original_name ?? null}
          canConfirm={canAnalyze}
          onConfirmed={() => {
            setResult(null);
            setActive(null);
            void groups.refetch();
          }}
        />
      ) : null}
    </div>
  );
}

// =============================================================================
// Cotizaciones
// =============================================================================

function QuotesTab({ placementId }: { placementId: number | null }) {
  const { t } = useTranslation("cases");
  const { t: tq } = useTranslation("quotes");
  const { t: tp } = useTranslation("proposals");

  const quotes = useQuotes({ placement_id: placementId ?? undefined }, !!placementId);
  const proposals = useProposals({ placement_id: placementId ?? undefined }, !!placementId);

  if (!placementId) {
    return (
      <EmptyState
        title={t("quotes.noPlacement")}
        hint={t("quotes.noPlacementHint")}
        icon={<ClipboardList className="h-6 w-6" />}
      />
    );
  }

  const quoteItems = quotes.data?.items ?? [];
  const proposalItems = proposals.data?.items ?? [];

  return (
    <div className="flex flex-col gap-4">
      <Section title={t("quotes.requestsTitle")}>
        {quotes.isLoading ? (
          <Skeleton className="h-24 w-full" />
        ) : quoteItems.length === 0 ? (
          <EmptyState title={t("quotes.noRequests")} />
        ) : (
          <ul className="flex flex-col divide-y divide-line">
            {quoteItems.map((q) => (
              <li key={q.id} className="flex flex-wrap items-center gap-3 py-2.5">
                <MonoChip>QR-{q.id}</MonoChip>
                <StatusBadge
                  value={q.status}
                  label={tq(`status.${q.status}`, { defaultValue: q.status })}
                />
                <span className="text-caption text-text-muted">
                  {t("quotes.due")}: {formatDate(q.due_at)}
                </span>
                <div className="ml-auto flex gap-2">
                  <Button size="sm" variant="secondary" asChild>
                    <Link to={`/quotes/${q.id}`}>{t("quotes.open")}</Link>
                  </Button>
                  <Button size="sm" variant="secondary" asChild>
                    <Link to={`/quotes/${q.id}/comparison`}>{t("quotes.compare")}</Link>
                  </Button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </Section>

      <Section title={t("quotes.proposalsTitle")}>
        {proposals.isLoading ? (
          <Skeleton className="h-24 w-full" />
        ) : proposalItems.length === 0 ? (
          <EmptyState title={t("quotes.noProposals")} />
        ) : (
          <ul className="flex flex-col divide-y divide-line">
            {proposalItems.map((p) => (
              <li key={p.id} className="flex flex-wrap items-center gap-3 py-2.5">
                <MonoChip>P-{p.id}</MonoChip>
                <span className="min-w-0 flex-1 truncate font-medium text-text-primary">
                  {p.insurer?.legal_name ?? `#${p.insurer_id}`}
                </span>
                <span className="tabular-nums">{uf(p.total_premium_uf)}</span>
                <StatusBadge
                  value={p.status}
                  label={tp(`status.${p.status}`, { defaultValue: p.status })}
                />
                <Button size="sm" variant="secondary" asChild>
                  <Link to={`/proposals/${p.id}`}>{t("quotes.open")}</Link>
                </Button>
              </li>
            ))}
          </ul>
        )}
      </Section>
    </div>
  );
}

// =============================================================================
// Propuesta
// =============================================================================

function ProposalTab({
  caseId,
  placementId,
}: {
  caseId: number;
  placementId: number | null;
}) {
  const { t } = useTranslation("cases");
  const proposals = useProposals(
    { placement_id: placementId ?? undefined, status: "accepted" },
    !!placementId,
  );
  const accepted = proposals.data?.items ?? [];

  return (
    <div className="flex flex-col gap-4">
      <Section
        title={t("proposal.title")}
        description={t("proposal.description")}
        actions={
          <Button size="sm" variant="secondary" asChild>
            <Link to={`/cases/${caseId}/packs`}>
              <Package className="h-4 w-4" />
              {t("tabs.packs")}
            </Link>
          </Button>
        }
      >
        {proposals.isLoading ? (
          <Skeleton className="h-24 w-full" />
        ) : accepted.length === 0 ? (
          <EmptyState title={t("proposal.none")} hint={t("proposal.noneHint")} />
        ) : (
          <ul className="flex flex-col divide-y divide-line">
            {accepted.map((p) => (
              <li key={p.id} className="flex flex-wrap items-center gap-3 py-2.5">
                <MonoChip>P-{p.id}</MonoChip>
                <span className="min-w-0 flex-1 truncate font-medium text-text-primary">
                  {p.insurer?.legal_name ?? `#${p.insurer_id}`}
                </span>
                <span className="tabular-nums">{uf(p.total_premium_uf)}</span>
                <Button size="sm" variant="secondary" asChild>
                  <Link to={`/proposals/${p.id}`}>{t("quotes.open")}</Link>
                </Button>
              </li>
            ))}
          </ul>
        )}
      </Section>
    </div>
  );
}

// =============================================================================
// Post-venta
// =============================================================================

function PostSaleTab({ caseId }: { caseId: number }) {
  const { t } = useTranslation("cases");
  const detail = useCaseFile(caseId);
  const c = detail.data;
  if (!c) return <Skeleton className="h-40 w-full" />;

  const children = c.children ?? [];
  const versions = c.versions ?? [];

  return (
    <div className="flex flex-col gap-4">
      <Section title={t("postsale.policyTitle")}>
        {c.policy_id ? (
          <div className="flex flex-wrap items-center gap-4">
            <KeyValue label={t("facts.policyNumber")} value={c.policy_number ?? "—"} mono />
            <KeyValue label={t("facts.period")} value={c.period ?? "—"} />
            <Button size="sm" variant="secondary" className="ml-auto" asChild>
              <Link to={`/policies/${c.policy_id}`}>
                <Shield className="h-4 w-4" />
                {t("postsale.openPolicy")}
              </Link>
            </Button>
          </div>
        ) : (
          <EmptyState title={t("postsale.noPolicy")} hint={t("postsale.noPolicyHint")} />
        )}
      </Section>

      <Section title={t("postsale.subFunnelTitle")} description={t("postsale.subFunnelHint")}>
        {children.length === 0 ? (
          <EmptyState title={t("postsale.noChildren")} />
        ) : (
          <ul className="flex flex-col divide-y divide-line">
            {children.map((child) => (
              <li key={child.id} className="flex flex-wrap items-center gap-3 py-2.5">
                <Badge variant="neutral">
                  {t(`kinds.${child.kind}`, { defaultValue: child.kind })}
                </Badge>
                <MonoChip>
                  {child.reference ?? `#${child.id}`} · {child.sequence_no}.v{child.version}
                </MonoChip>
                <span className="min-w-0 flex-1 truncate">{child.title}</span>
                <Badge variant="brand">
                  {t(`stages.${child.stage}`, { defaultValue: child.stage })}
                </Badge>
                <span className="text-caption text-text-muted">
                  {formatDate(child.opened_at)}
                  {child.closed_at ? ` → ${formatDate(child.closed_at)}` : ""}
                </span>
                <Button size="sm" variant="secondary" asChild>
                  <Link to={`/cases/${child.id}`}>{t("quotes.open")}</Link>
                </Button>
              </li>
            ))}
          </ul>
        )}
      </Section>

      {versions.length ? (
        <Section title={t("postsale.versionsTitle")}>
          <ul className="flex flex-col divide-y divide-line">
            {versions.map((v) => (
              <li key={v.id} className="flex flex-wrap items-center gap-3 py-2.5">
                <MonoChip>v{v.version}</MonoChip>
                <span className="min-w-0 flex-1 truncate">{v.title}</span>
                <span className="text-caption text-text-muted">{formatDate(v.opened_at)}</span>
                <Button size="sm" variant="secondary" asChild>
                  <Link to={`/cases/${v.id}`}>{t("quotes.open")}</Link>
                </Button>
              </li>
            ))}
          </ul>
        </Section>
      ) : null}
    </div>
  );
}

// =============================================================================
// Bitácora
// =============================================================================

function TimelineTab({ caseId }: { caseId: number }) {
  const { t } = useTranslation("cases");
  const timeline = useCaseFileTimeline(caseId);
  const entries = timeline.data?.entries ?? [];

  if (timeline.isLoading) return <Skeleton className="h-48 w-full" />;
  if (!entries.length) {
    return (
      <EmptyState
        title={t("timeline.empty")}
        hint={t("timeline.emptyHint")}
        icon={<History className="h-6 w-6" />}
      />
    );
  }

  const tone = (kind: string) =>
    kind === "stage" ? "brand" : kind === "note" ? "warn" : "neutral";

  return (
    <Card className="p-5">
      <ol className="flex flex-col gap-4">
        {entries.map((entry, i) => (
          <li key={`${entry.occurred_at}-${i}`} className="flex gap-3">
            <div className="flex flex-col items-center">
              <span className="mt-1.5 h-2 w-2 shrink-0 rounded-full bg-brand" />
              {i < entries.length - 1 ? <span className="w-px flex-1 bg-line" /> : null}
            </div>
            <div className="min-w-0 flex-1 pb-1">
              <div className="flex flex-wrap items-center gap-2">
                <Badge variant={tone(entry.kind)}>
                  {t(`timeline.kinds.${entry.kind}`, { defaultValue: entry.kind })}
                </Badge>
                <span className="font-medium text-text-primary">{entry.title}</span>
                <span className="text-caption text-text-muted">
                  {formatDateTime(entry.occurred_at)}
                </span>
              </div>
              {entry.detail ? (
                <p className="mt-1 whitespace-pre-wrap text-caption text-text-secondary">
                  {entry.detail}
                </p>
              ) : null}
            </div>
          </li>
        ))}
      </ol>
    </Card>
  );
}
