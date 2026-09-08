/**
 * Proposal — detail.
 *
 * Shows the standardised offer exactly as the comparator reads it, keeps the
 * AI provenance visible (extraction confidence + the source document that is
 * mandatory for a proposal to exist at all), and exposes the three human
 * decisions the model may never take on its own: confirm, accept, reject.
 *
 * The chat panel is scoped to this proposal — the server assembles the context
 * from the tenant's own rows, the client cannot widen it.
 */
import * as React from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import {
  ArrowLeft,
  Bot,
  Check,
  Download,
  FileText,
  Layers,
  Plus,
  Send,
  ShieldCheck,
  Trash2,
  X,
} from "lucide-react";

import { PageHeader } from "@/components/common/PageHeader";
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
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { cn } from "@/lib/utils";
import { useCan } from "@/lib/permissions";
import { formatDate, formatDateTime } from "@/lib/format";
import {
  useAcceptProposal,
  useAddProposalCoverage,
  useConfirmProposal,
  useDeleteProposalCoverage,
  useProposal,
  useRejectProposal,
} from "@/api/proposals";
import { useDocument, useDocumentDownload } from "@/api/documents";
import {
  useAgentMessages,
  useAgentThreads,
  useCreateAgentThread,
  useSendAgentMessage,
} from "@/api/ai";
import { COVERAGE_KINDS, type CoverageKind, type Proposal } from "@/api/types";
import {
  ConfidenceBadge,
  DisabledHint,
  ErrorBanner,
  KeyValue,
  MonoChip,
  Section,
  StatusBadge,
  apiError,
  deductibleText,
  formatDeductible,
  resolveFileUrl,
  permille,
  pct,
  uf,
  usePerilLabel,
} from "@/pages/proposals/shared";

const CLOSED_STATUSES = ["accepted", "rejected", "withdrawn", "expired"];

export default function ProposalDetailPage() {
  const { proposalId } = useParams<{ proposalId: string }>();
  const id = Number(proposalId);
  const { t } = useTranslation("proposals");
  const { t: tc } = useTranslation("common");
  const navigate = useNavigate();

  const proposal = useProposal(Number.isFinite(id) ? id : undefined);

  if (proposal.isLoading) {
    return (
      <div className="flex flex-col gap-4">
        <Skeleton className="h-9 w-72" />
        <Skeleton className="h-32 w-full" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }

  if (proposal.isError || !proposal.data) {
    return (
      <>
        <PageHeader title={t("detail.title")} />
        <ErrorBanner error={proposal.error ?? t("detail.notFound")} />
        <Button variant="secondary" size="sm" onClick={() => navigate("/proposals")}>
          <ArrowLeft className="h-4 w-4" />
          {tc("actions.back")}
        </Button>
      </>
    );
  }

  const p = proposal.data;

  return (
    <>
      <ProposalHeader proposal={p} />
      <MoneyPanel proposal={p} />
      <TermsPanel proposal={p} />
      <DeductiblesPanel proposal={p} />
      <CoveragesPanel proposal={p} />
      <SourceDocumentPanel proposal={p} />
      <ProposalChat proposalId={p.id} />
    </>
  );
}

// =============================================================================
// Header + decisions
// =============================================================================

function ProposalHeader({ proposal: p }: { proposal: Proposal }) {
  const { t } = useTranslation("proposals");
  const { t: tc } = useTranslation("common");

  const confirm = useConfirmProposal(p.id);
  const accept = useAcceptProposal(p.id);
  const reject = useRejectProposal(p.id);
  const canApprove = useCan("Proposals", "Approve");

  const [rejectOpen, setRejectOpen] = React.useState(false);
  const [reason, setReason] = React.useState("");

  const closed = CLOSED_STATUSES.includes(p.status);
  const needsConfirmation = p.extraction_id !== null && !p.is_confirmed;

  const acceptHint = !canApprove.allowed
    ? t("decisions.noPermission")
    : closed
      ? t("decisions.alreadyClosed", { status: t(`status.${p.status}`) })
      : needsConfirmation
        ? t("decisions.confirmFirst")
        : null;

  return (
    <>
      <PageHeader
        eyebrow={
          <Link to="/proposals" className="inline-flex items-center gap-1.5 no-underline">
            <ArrowLeft className="h-3.5 w-3.5" />
            {t("title")}
          </Link>
        }
        title={p.insurer?.trade_name || p.insurer?.legal_name || `#${p.insurer_id}`}
        subtitle={
          <span className="flex flex-wrap items-center gap-2">
            <MonoChip>PROP-{String(p.id).padStart(4, "0")}</MonoChip>
            {p.insurer ? (
              <>
                <MonoChip>{p.insurer.rut}</MonoChip>
                <MonoChip>CMF {p.insurer.cmf_code}</MonoChip>
              </>
            ) : null}
            <StatusBadge value={p.status} label={t(`status.${p.status}`)} />
            <Badge variant={p.origin === "native" ? "brand" : "neutral"}>
              {t(`origin.${p.origin}`)}
            </Badge>
            {p.is_confirmed ? (
              <Badge variant="success" className="gap-1">
                <Check className="h-3 w-3" />
                {t("confirmed")}
              </Badge>
            ) : (
              <>
                <Badge variant="warn">{t("unconfirmed")}</Badge>
                <ConfidenceBadge value={p.extraction_confidence} />
              </>
            )}
          </span>
        }
        actions={
          <>
            <Button variant="secondary" size="sm" asChild>
              <Link to={`/quotes/${p.quote_request_id}/comparison`}>
                <Layers className="h-4 w-4" />
                {t("detail.compare")}
              </Link>
            </Button>

            <DisabledHint
              hint={
                !canApprove.allowed
                  ? t("decisions.noPermission")
                  : p.is_confirmed
                    ? t("decisions.alreadyConfirmed")
                    : null
              }
            >
              <Button
                variant="secondary"
                size="sm"
                disabled={p.is_confirmed || !canApprove.allowed || confirm.isPending}
                onClick={() =>
                  confirm.mutate(
                    { is_confirmed: true },
                    {
                      onSuccess: () => toast.success(t("decisions.confirmed")),
                      onError: (error) => toast.error(apiError(error, tc("toast.error"))),
                    },
                  )
                }
              >
                <ShieldCheck className="h-4 w-4" />
                {t("decisions.confirm")}
              </Button>
            </DisabledHint>

            <DisabledHint
              hint={
                !canApprove.allowed
                  ? t("decisions.noPermission")
                  : closed
                    ? t("decisions.alreadyClosed", { status: t(`status.${p.status}`) })
                    : null
              }
            >
              <Button
                variant="secondary"
                size="sm"
                disabled={closed || !canApprove.allowed}
                onClick={() => setRejectOpen(true)}
              >
                <X className="h-4 w-4" />
                {t("decisions.reject")}
              </Button>
            </DisabledHint>

            <DisabledHint hint={acceptHint}>
              <Button
                size="sm"
                disabled={!!acceptHint || accept.isPending}
                onClick={() =>
                  accept.mutate(
                    {},
                    {
                      onSuccess: (result) =>
                        toast.success(
                          t("decisions.accepted", {
                            count: result.rejected_proposal_ids.length,
                          }),
                        ),
                      onError: (error) => toast.error(apiError(error, tc("toast.error"))),
                    },
                  )
                }
              >
                <Check className="h-4 w-4" />
                {t("decisions.accept")}
              </Button>
            </DisabledHint>
          </>
        }
      />

      <Dialog open={rejectOpen} onOpenChange={setRejectOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("decisions.rejectTitle")}</DialogTitle>
            <DialogDescription>
              {t("decisions.rejectDescription", {
                insurer: p.insurer?.trade_name || p.insurer?.legal_name || "",
              })}
            </DialogDescription>
          </DialogHeader>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="reject-reason">{t("decisions.reason")}</Label>
            <Input
              id="reject-reason"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder={t("decisions.reasonPlaceholder")}
            />
          </div>
          {reject.isError ? <ErrorBanner error={reject.error} /> : null}
          <DialogFooter>
            <Button variant="secondary" onClick={() => setRejectOpen(false)}>
              {tc("actions.cancel")}
            </Button>
            <Button
              variant="destructive"
              disabled={reject.isPending}
              onClick={() =>
                reject.mutate(
                  { reason: reason.trim() || null },
                  {
                    onSuccess: () => {
                      toast.success(t("decisions.rejected"));
                      setRejectOpen(false);
                    },
                    onError: (error) => toast.error(apiError(error, tc("toast.error"))),
                  },
                )
              }
            >
              <X className="h-4 w-4" />
              {t("decisions.reject")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}

// =============================================================================
// Panels
// =============================================================================

function MoneyPanel({ proposal: p }: { proposal: Proposal }) {
  const { t } = useTranslation("proposals");
  return (
    <FadeUp delay={0.06}>
      <Section title={t("detail.moneyTitle")} description={t("upload.formula")}>
        <div className="grid grid-cols-2 gap-5 md:grid-cols-3 lg:grid-cols-5">
          <KeyValue label={t("fields.totalPremium")} value={uf(p.total_premium_uf)} />
          <KeyValue label={t("fields.netPremium")} value={uf(p.net_premium_uf)} />
          <KeyValue label={t("fields.taxablePremium")} value={uf(p.taxable_premium_uf)} />
          <KeyValue label={t("fields.exemptPremium")} value={uf(p.exempt_premium_uf)} />
          <KeyValue label={t("fields.vat")} value={uf(p.vat_uf)} />
          <KeyValue
            label={t("fields.comprehensiveRate")}
            value={permille(p.comprehensive_rate_permille)}
          />
          <KeyValue label={t("fields.taxableRate")} value={permille(p.taxable_rate_permille)} />
          <KeyValue label={t("fields.exemptRate")} value={permille(p.exempt_rate_permille)} />
          <KeyValue label={t("fields.commission")} value={pct(p.commission_pct)} />
          <KeyValue
            label={t("fields.validityDays")}
            value={
              p.validity_business_days === null
                ? "—"
                : t("compare.businessDays", { count: p.validity_business_days })
            }
          />
        </div>
      </Section>
    </FadeUp>
  );
}

function TermsPanel({ proposal: p }: { proposal: Proposal }) {
  const { t } = useTranslation("proposals");
  return (
    <FadeUp delay={0.08}>
      <Section title={t("detail.termsTitle")}>
        <div className="grid grid-cols-2 gap-5 md:grid-cols-4">
          <KeyValue label={t("fields.coverageStart")} value={formatDate(p.coverage_start)} />
          <KeyValue label={t("fields.coverageEnd")} value={formatDate(p.coverage_end)} />
          <KeyValue label={t("fields.receivedAt")} value={formatDate(p.received_at)} />
          <KeyValue
            label={t("fields.confirmedAt")}
            value={p.confirmed_at ? formatDateTime(p.confirmed_at) : "—"}
          />
          <KeyValue label={t("fields.modality")} value={p.modality || "—"} />
          <KeyValue label={t("fields.activity")} value={p.activity_classification || "—"} />
          <div className="col-span-2">
            <KeyValue
              label={t("fields.warranties")}
              value={
                <span className="whitespace-pre-wrap text-body">{p.warranties || "—"}</span>
              }
            />
          </div>
          {p.notes ? (
            <div className="col-span-2 md:col-span-4">
              <KeyValue
                label={t("fields.notes")}
                value={<span className="whitespace-pre-wrap text-body">{p.notes}</span>}
              />
            </div>
          ) : null}
        </div>
      </Section>
    </FadeUp>
  );
}

function DeductiblesPanel({ proposal: p }: { proposal: Proposal }) {
  const { t } = useTranslation("proposals");
  const perilLabel = usePerilLabel();
  const entries = Object.entries(p.deductibles ?? {});

  return (
    <FadeUp delay={0.1}>
      <Section
        title={t("detail.deductiblesTitle")}
        description={t("detail.deductiblesDescription")}
        bodyClassName={entries.length ? "p-0" : undefined}
      >
        {entries.length === 0 ? (
          <p className="text-body text-text-muted">{t("upload.noDeductibles")}</p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow className="hover:bg-transparent">
                <TableHead>{t("fields.peril")}</TableHead>
                <TableHead>{t("detail.deductibleTerm")}</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {entries.map(([peril, term]) => (
                <TableRow key={peril} className="hover:bg-transparent">
                  <TableCell className="font-medium">{perilLabel(peril)}</TableCell>
                  <TableCell>
                    {formatDeductible(term, t) ? (
                      <span className="flex flex-col gap-0.5">
                        <span className="text-text-primary">{formatDeductible(term, t)}</span>
                        {deductibleText(term) && deductibleText(term) !== formatDeductible(term, t) ? (
                          <span className="text-caption text-text-muted">
                            {deductibleText(term)}
                          </span>
                        ) : null}
                      </span>
                    ) : (
                      <span className="italic text-text-muted">{t("compare.noInformation")}</span>
                    )}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </Section>
    </FadeUp>
  );
}

function CoveragesPanel({ proposal: p }: { proposal: Proposal }) {
  const { t } = useTranslation("proposals");
  const { t: tc } = useTranslation("common");
  const canEdit = useCan("Proposals", "Edit");
  const add = useAddProposalCoverage(p.id);
  const remove = useDeleteProposalCoverage(p.id);

  const [kind, setKind] = React.useState<CoverageKind>("coverage");
  const [text, setText] = React.useState("");

  const coverages = p.coverages.filter((c) => c.kind === "coverage");
  const exclusions = p.coverages.filter((c) => c.kind === "exclusion");

  const submit = () => {
    if (!text.trim()) return;
    add.mutate(
      { kind, text: text.trim(), sort_order: p.coverages.length },
      {
        onSuccess: () => {
          setText("");
          toast.success(t("detail.coverageAdded"));
        },
        onError: (error) => toast.error(apiError(error, tc("toast.error"))),
      },
    );
  };

  const renderList = (rows: typeof p.coverages, tone: "success" | "warn") =>
    rows.length === 0 ? (
      <p className="text-caption text-text-muted">{t("detail.none")}</p>
    ) : (
      <ul className="flex flex-col gap-1.5">
        {rows.map((c) => (
          <li
            key={c.id}
            className="group flex items-start gap-2 rounded-lg border border-line px-3 py-2"
          >
            <span
              className={cn(
                "mt-0.5 h-2 w-2 shrink-0 rounded-full",
                tone === "success" ? "bg-pos" : "bg-warn",
              )}
            />
            <span className="min-w-0 flex-1 text-body text-text-secondary">{c.text}</span>
            {c.normalized_code ? <MonoChip>{c.normalized_code}</MonoChip> : null}
            <DisabledHint hint={canEdit.allowed ? null : t("detail.editNoPermission")}>
              <Button
                variant="ghost"
                size="icon"
                className="h-7 w-7 shrink-0"
                aria-label={tc("actions.delete")}
                disabled={!canEdit.allowed || remove.isPending}
                onClick={() =>
                  remove.mutate(c.id, {
                    onSuccess: () => toast.success(t("detail.coverageRemoved")),
                    onError: (error) => toast.error(apiError(error, tc("toast.error"))),
                  })
                }
              >
                <Trash2 className="h-3.5 w-3.5 text-signal-danger" />
              </Button>
            </DisabledHint>
          </li>
        ))}
      </ul>
    );

  return (
    <FadeUp delay={0.12}>
      <Section title={t("detail.coveragesTitle")} description={t("detail.coveragesDescription")}>
        <div className="grid gap-6 md:grid-cols-2">
          <div>
            <h3 className="mb-2.5 text-label font-semibold text-text-primary">
              {t("coverageKind.coverage")} ({coverages.length})
            </h3>
            {renderList(coverages, "success")}
          </div>
          <div>
            <h3 className="mb-2.5 text-label font-semibold text-text-primary">
              {t("coverageKind.exclusion")} ({exclusions.length})
            </h3>
            {renderList(exclusions, "warn")}
          </div>
        </div>

        <div className="mt-5 flex flex-wrap items-end gap-2.5 border-t border-line pt-5">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="coverage-kind">{t("detail.addKind")}</Label>
            <Select value={kind} onValueChange={(v) => setKind(v as CoverageKind)}>
              <SelectTrigger id="coverage-kind" className="h-9 w-[160px]">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {COVERAGE_KINDS.map((k) => (
                  <SelectItem key={k} value={k}>
                    {t(`coverageKind.${k}`)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="flex min-w-[240px] flex-1 flex-col gap-1.5">
            <Label htmlFor="coverage-text">{t("detail.addText")}</Label>
            <Input
              id="coverage-text"
              value={text}
              className="h-9"
              disabled={!canEdit.allowed}
              placeholder={t("upload.coveragePlaceholder")}
              onChange={(e) => setText(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") submit();
              }}
            />
          </div>
          <DisabledHint
            hint={
              !canEdit.allowed
                ? t("detail.editNoPermission")
                : text.trim()
                  ? null
                  : t("detail.addTextRequired")
            }
          >
            <Button
              size="sm"
              className="h-9"
              disabled={!canEdit.allowed || !text.trim() || add.isPending}
              onClick={submit}
            >
              <Plus className="h-4 w-4" />
              {tc("actions.add")}
            </Button>
          </DisabledHint>
        </div>

        {add.isError ? <ErrorBanner error={add.error} className="mt-3" /> : null}
      </Section>
    </FadeUp>
  );
}

function SourceDocumentPanel({ proposal: p }: { proposal: Proposal }) {
  const { t } = useTranslation("proposals");
  const doc = useDocument(p.source_document_id);
  const download = useDocumentDownload(p.source_document_id);
  const fileUrl = resolveFileUrl(download.data?.url);

  return (
    <FadeUp delay={0.14}>
      <Section title={t("detail.sourceTitle")} description={t("detail.sourceDescription")}>
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div className="flex min-w-0 items-center gap-3">
            <span className="rounded-lg bg-brand-soft p-2.5 text-brand-deep">
              <FileText className="h-5 w-5" />
            </span>
            <div className="min-w-0">
              <p className="truncate text-body font-medium text-text-primary">
                {doc.data?.original_name ?? `DOC-${p.source_document_id}`}
              </p>
              <p className="flex items-center gap-2 text-caption text-text-muted">
                <MonoChip>DOC-{p.source_document_id}</MonoChip>
                {doc.data?.mime_type ?? ""}
                {p.extraction_id ? <MonoChip>EXT-{p.extraction_id}</MonoChip> : null}
              </p>
            </div>
          </div>

          <DisabledHint hint={fileUrl ? null : t("detail.downloadUnavailable")}>
            {fileUrl ? (
              <Button variant="secondary" size="sm" asChild>
                <a href={fileUrl} target="_blank" rel="noreferrer">
                  <Download className="h-4 w-4" />
                  {t("detail.openDocument")}
                </a>
              </Button>
            ) : (
              <Button variant="secondary" size="sm" disabled>
                <Download className="h-4 w-4" />
                {t("detail.openDocument")}
              </Button>
            )}
          </DisabledHint>
        </div>
      </Section>
    </FadeUp>
  );
}

// =============================================================================
// Chat
// =============================================================================

function ProposalChat({ proposalId }: { proposalId: number }) {
  const { t } = useTranslation("proposals");
  const { t: tc } = useTranslation("common");

  const threads = useAgentThreads();
  const existing = (threads.data?.items ?? []).find(
    (thread) => thread.scope === "proposal" && thread.entity_id === proposalId,
  );
  const [threadId, setThreadId] = React.useState<number | undefined>(undefined);
  React.useEffect(() => {
    if (existing?.id) setThreadId(existing.id);
  }, [existing?.id]);

  const create = useCreateAgentThread();
  const messages = useAgentMessages(threadId);
  const send = useSendAgentMessage(threadId ?? 0);
  const [question, setQuestion] = React.useState("");

  const startThread = () =>
    create.mutate(
      { scope: "proposal", entity_id: proposalId },
      {
        onSuccess: (thread) => setThreadId(thread.id),
        onError: (error) => toast.error(apiError(error, tc("toast.error"))),
      },
    );

  const ask = () => {
    if (!threadId || !question.trim()) return;
    send.mutate(
      { content: question.trim() },
      {
        onSuccess: () => setQuestion(""),
        onError: (error) => toast.error(apiError(error, tc("toast.error"))),
      },
    );
  };

  const rows = (messages.data?.items ?? []).filter((m) => m.role !== "system");

  return (
    <FadeUp delay={0.16}>
      <Section
        title={
          <span className="flex items-center gap-2">
            <Bot className="h-4 w-4 text-brand" />
            {t("chat.title")}
          </span>
        }
        description={t("chat.description")}
        actions={
          threadId ? (
            <Badge variant="brand">{t("chat.threadOpen", { id: threadId })}</Badge>
          ) : (
            <Button
              variant="secondary"
              size="sm"
              disabled={create.isPending || threads.isLoading}
              onClick={startThread}
            >
              <Bot className="h-4 w-4" />
              {create.isPending ? tc("actions.loading") : t("chat.start")}
            </Button>
          )
        }
      >
        {!threadId ? (
          <p className="text-body text-text-muted">{t("chat.notStarted")}</p>
        ) : (
          <div className="flex flex-col gap-3">
            <div className="flex max-h-[380px] flex-col gap-2.5 overflow-y-auto">
              {messages.isLoading ? (
                <Skeleton className="h-20 w-full" />
              ) : rows.length === 0 ? (
                <p className="text-caption text-text-muted">{t("chat.empty")}</p>
              ) : (
                rows.map((m) => (
                  <div
                    key={m.id}
                    className={cn(
                      "max-w-[85%] rounded-card px-3.5 py-2.5 text-body",
                      m.role === "user"
                        ? "self-end bg-brand-soft text-text-primary"
                        : "self-start border border-line bg-bg-recessed text-text-secondary",
                    )}
                  >
                    <p className="whitespace-pre-wrap">{m.content}</p>
                    <p className="mt-1 text-[11px] text-text-muted">
                      {m.role === "user" ? t("chat.you") : t("chat.assistant")}
                      {m.created_at ? ` · ${formatDateTime(m.created_at)}` : ""}
                    </p>
                  </div>
                ))
              )}
              {send.isPending ? (
                <div className="self-start rounded-card border border-line bg-bg-recessed px-3.5 py-2.5 text-caption text-text-muted">
                  {t("chat.thinking")}
                </div>
              ) : null}
            </div>

            <div className="flex items-center gap-2.5">
              <Input
                value={question}
                onChange={(e) => setQuestion(e.target.value)}
                placeholder={t("chat.placeholder")}
                disabled={send.isPending}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    ask();
                  }
                }}
              />
              <DisabledHint hint={question.trim() ? null : t("chat.typeSomething")}>
                <Button onClick={ask} disabled={!question.trim() || send.isPending}>
                  <Send className="h-4 w-4" />
                  {t("chat.send")}
                </Button>
              </DisabledHint>
            </div>

            {send.isError ? <ErrorBanner error={send.error} /> : null}
          </div>
        )}
      </Section>
    </FadeUp>
  );
}
