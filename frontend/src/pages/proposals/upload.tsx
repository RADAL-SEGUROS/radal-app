/**
 * Proposal intake — upload a document, let the AI read it, edit, then commit.
 *
 * The write path is strictly SUGGEST -> HUMAN CONFIRM -> COMMIT:
 *   1. `POST /documents`            the file lands in S3 and becomes a row
 *   2. `POST /ai/proposals/extract` returns a suggestion + the persisted
 *                                   `extraction` (model, prompt version,
 *                                   confidence). It writes NO proposal.
 *   3. the broker edits every field here — nothing is trusted as read
 *   4. `POST /ai/proposals/{extraction_id}/confirm` commits what the HUMAN
 *      approved, not what the model said.
 *
 * The insurer is identified by rut / cmf_code only (never by name), so those two
 * fields gate the commit button.
 */
import * as React from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import {
  ArrowLeft,
  Bot,
  Check,
  FileText,
  Plus,
  RefreshCw,
  Search,
  Sparkles,
  Trash2,
  Upload,
} from "lucide-react";

import { PageHeader } from "@/components/common/PageHeader";
import { FadeUp } from "@/components/common/motion";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { cn } from "@/lib/utils";
import { formatNumber } from "@/lib/format";
import { useCan } from "@/lib/permissions";
import { useUploadDocument } from "@/api/documents";
import { useConfirmExtraction, useExtractProposal } from "@/api/ai";
import { useMatchInsurer } from "@/api/insurers";
import { useQuotes } from "@/api/quotes";
import {
  COVERAGE_KINDS,
  num,
  type CoverageSuggestion,
  type DeductibleTerm,
  type Extraction,
  type ProposalSuggestion,
  type RadalDocument,
} from "@/api/types";
import {
  ConfidenceBadge,
  DisabledHint,
  ErrorBanner,
  KeyValue,
  MonoChip,
  Section,
  StatusBadge,
  apiError,
  deriveMoney,
  differs,
  uf,
} from "@/pages/proposals/shared";

// =============================================================================
// Draft model
// =============================================================================

interface DeductibleDraft {
  key: string;
  peril: string;
  basis: string;
  pct: string;
  min_uf: string;
  max_uf: string;
  amount_uf: string;
  notes: string;
}

interface CoverageDraft extends CoverageSuggestion {
  key: string;
}

interface Draft {
  insurer_rut: string;
  insurer_cmf_code: string;
  insurer_legal_name: string;
  insurer_trade_name: string;
  modality: string;
  activity_classification: string;
  taxable_premium_uf: string;
  exempt_premium_uf: string;
  net_premium_uf: string;
  vat_uf: string;
  total_premium_uf: string;
  taxable_rate_permille: string;
  exempt_rate_permille: string;
  comprehensive_rate_permille: string;
  commission_pct: string;
  validity_business_days: string;
  coverage_start: string;
  coverage_end: string;
  received_at: string;
  warranties: string;
  notes: string;
  deductibles: DeductibleDraft[];
  coverages: CoverageDraft[];
}

const s = (value: string | number | null | undefined): string =>
  value === null || value === undefined ? "" : String(value);

let seq = 0;
const key = () => `k${++seq}`;

const EMPTY_DRAFT: Draft = {
  insurer_rut: "",
  insurer_cmf_code: "",
  insurer_legal_name: "",
  insurer_trade_name: "",
  modality: "",
  activity_classification: "",
  taxable_premium_uf: "",
  exempt_premium_uf: "",
  net_premium_uf: "",
  vat_uf: "",
  total_premium_uf: "",
  taxable_rate_permille: "",
  exempt_rate_permille: "",
  comprehensive_rate_permille: "",
  commission_pct: "",
  validity_business_days: "",
  coverage_start: "",
  coverage_end: "",
  received_at: "",
  warranties: "",
  notes: "",
  deductibles: [],
  coverages: [],
};

function toDraft(suggestion: ProposalSuggestion): Draft {
  return {
    insurer_rut: s(suggestion.insurer?.rut),
    insurer_cmf_code: s(suggestion.insurer?.cmf_code),
    insurer_legal_name: s(suggestion.insurer?.legal_name),
    insurer_trade_name: s(suggestion.insurer?.trade_name),
    modality: s(suggestion.modality),
    activity_classification: s(suggestion.activity_classification),
    taxable_premium_uf: s(suggestion.taxable_premium_uf),
    exempt_premium_uf: s(suggestion.exempt_premium_uf),
    net_premium_uf: s(suggestion.net_premium_uf),
    vat_uf: s(suggestion.vat_uf),
    total_premium_uf: s(suggestion.total_premium_uf),
    taxable_rate_permille: s(suggestion.taxable_rate_permille),
    exempt_rate_permille: s(suggestion.exempt_rate_permille),
    comprehensive_rate_permille: s(suggestion.comprehensive_rate_permille),
    commission_pct: s(suggestion.commission_pct),
    validity_business_days: s(suggestion.validity_business_days),
    coverage_start: s(suggestion.coverage_start),
    coverage_end: s(suggestion.coverage_end),
    received_at: s(suggestion.received_at),
    warranties: s(suggestion.warranties),
    notes: s(suggestion.notes),
    deductibles: Object.entries(suggestion.deductibles ?? {}).map(([peril, term]) => ({
      key: key(),
      peril,
      basis: s(term?.basis),
      pct: s(term?.pct as string | number | null),
      min_uf: s(term?.min_uf as string | number | null),
      max_uf: s(term?.max_uf as string | number | null),
      amount_uf: s(term?.amount_uf as string | number | null),
      notes: s(term?.notes as string | null),
    })),
    coverages: (suggestion.coverages ?? []).map((c, i) => ({
      key: key(),
      kind: c.kind,
      text: c.text,
      normalized_code: c.normalized_code,
      sort_order: c.sort_order ?? i,
    })),
  };
}

const numOrNull = (value: string): string | null => (value.trim() === "" ? null : value.trim());

function toSuggestion(draft: Draft): ProposalSuggestion {
  const deductibles: Record<string, DeductibleTerm> = {};
  for (const d of draft.deductibles) {
    const peril = d.peril.trim();
    if (!peril) continue;
    const term: DeductibleTerm = {};
    if (d.basis.trim()) term.basis = d.basis.trim();
    if (d.pct.trim()) term.pct = d.pct.trim();
    if (d.min_uf.trim()) term.min_uf = d.min_uf.trim();
    if (d.max_uf.trim()) term.max_uf = d.max_uf.trim();
    if (d.amount_uf.trim()) term.amount_uf = d.amount_uf.trim();
    if (d.notes.trim()) term.notes = d.notes.trim();
    deductibles[peril] = term;
  }
  return {
    insurer: {
      rut: numOrNull(draft.insurer_rut),
      cmf_code: numOrNull(draft.insurer_cmf_code),
      legal_name: numOrNull(draft.insurer_legal_name),
      trade_name: numOrNull(draft.insurer_trade_name),
    },
    modality: numOrNull(draft.modality),
    activity_classification: numOrNull(draft.activity_classification),
    taxable_premium_uf: numOrNull(draft.taxable_premium_uf),
    exempt_premium_uf: numOrNull(draft.exempt_premium_uf),
    net_premium_uf: numOrNull(draft.net_premium_uf),
    vat_uf: numOrNull(draft.vat_uf),
    total_premium_uf: numOrNull(draft.total_premium_uf),
    taxable_rate_permille: numOrNull(draft.taxable_rate_permille),
    exempt_rate_permille: numOrNull(draft.exempt_rate_permille),
    comprehensive_rate_permille: numOrNull(draft.comprehensive_rate_permille),
    commission_pct: numOrNull(draft.commission_pct),
    validity_business_days:
      draft.validity_business_days.trim() === "" ? null : Number(draft.validity_business_days),
    coverage_start: numOrNull(draft.coverage_start),
    coverage_end: numOrNull(draft.coverage_end),
    received_at: numOrNull(draft.received_at),
    deductibles,
    warranties: numOrNull(draft.warranties),
    notes: numOrNull(draft.notes),
    coverages: draft.coverages
      .filter((c) => c.text.trim())
      .map((c, i) => ({
        kind: c.kind,
        text: c.text.trim(),
        normalized_code: c.normalized_code,
        sort_order: i,
      })),
  };
}

// =============================================================================
// Page
// =============================================================================

export default function ProposalUploadPage() {
  const { t } = useTranslation("proposals");
  const { t: tc } = useTranslation("common");
  const navigate = useNavigate();
  const [params] = useSearchParams();

  const preselected = params.get("quote");
  const [quoteId, setQuoteId] = React.useState<string>(preselected ?? "");
  const [file, setFile] = React.useState<File | null>(null);
  const [document, setDocument] = React.useState<RadalDocument | null>(null);
  const [extraction, setExtraction] = React.useState<Extraction | null>(null);
  const [warnings, setWarnings] = React.useState<string[]>([]);
  const [draft, setDraft] = React.useState<Draft>(EMPTY_DRAFT);
  const [hasSuggestion, setHasSuggestion] = React.useState(false);

  const quotes = useQuotes({ limit: 100 });
  const upload = useUploadDocument();
  const extract = useExtractProposal();
  const canCreate = useCan("Proposals", "Create");
  const canApprove = useCan("Proposals", "Approve");

  const quote = (quotes.data?.items ?? []).find((q) => String(q.id) === quoteId);

  const runPipeline = async () => {
    if (!file || !quoteId) return;
    try {
      const doc = await upload.mutateAsync({
        file,
        entity_type: "quote_request",
        entity_id: Number(quoteId),
        category: "proposal",
        phase: "quoting",
      });
      setDocument(doc);
      const result = await extract.mutateAsync({ document_id: doc.id });
      setExtraction(result.extraction);
      setWarnings(result.warnings ?? []);
      if (result.suggestion) {
        setDraft(toDraft(result.suggestion));
        setHasSuggestion(true);
        toast.success(t("ai.extracted"));
      } else {
        setHasSuggestion(true);
        setDraft(EMPTY_DRAFT);
        toast.warning(t("ai.noSuggestion"));
      }
    } catch (error) {
      toast.error(apiError(error, tc("toast.error")));
    }
  };

  const retryExtraction = async () => {
    if (!document) return;
    try {
      const result = await extract.mutateAsync({ document_id: document.id });
      setExtraction(result.extraction);
      setWarnings(result.warnings ?? []);
      if (result.suggestion) setDraft(toDraft(result.suggestion));
      setHasSuggestion(true);
      toast.success(t("ai.extracted"));
    } catch (error) {
      toast.error(apiError(error, tc("toast.error")));
    }
  };

  const busy = upload.isPending || extract.isPending;

  return (
    <>
      <PageHeader
        eyebrow={
          <Link
            to={quoteId ? `/quotes/${quoteId}` : "/proposals"}
            className="inline-flex items-center gap-1.5 no-underline"
          >
            <ArrowLeft className="h-3.5 w-3.5" />
            {quoteId ? t("upload.backToQuote") : t("title")}
          </Link>
        }
        title={t("upload.title")}
        subtitle={t("upload.subtitle")}
      />

      <Stepper
        step={hasSuggestion ? 3 : document ? 2 : 1}
        labels={[t("upload.step1"), t("upload.step2"), t("upload.step3")]}
      />

      {/* ── Step 1: quote + file ── */}
      <FadeUp delay={0.05}>
        <Section title={t("upload.sourceTitle")} description={t("upload.sourceDescription")}>
          <div className="flex flex-col gap-4">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="quote">{t("upload.quote")}</Label>
              <Select value={quoteId} onValueChange={setQuoteId} disabled={!!document}>
                <SelectTrigger id="quote" className="max-w-xl">
                  <SelectValue
                    placeholder={
                      quotes.isLoading ? tc("actions.loading") : t("upload.quotePlaceholder")
                    }
                  />
                </SelectTrigger>
                <SelectContent>
                  {(quotes.data?.items ?? []).map((q) => (
                    <SelectItem key={q.id} value={String(q.id)}>
                      COT-{String(q.id).padStart(4, "0")} ·{" "}
                      {q.insured_object || t("upload.noObject")} · {uf(q.declared_value_uf)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              {quote ? (
                <p className="text-caption text-text-muted">
                  {t("upload.quoteHint", { count: quote.proposal_count })}
                </p>
              ) : null}
            </div>

            <DropZone file={file} onFile={setFile} disabled={!!document || busy} />

            <div className="flex flex-wrap items-center gap-2">
              <DisabledHint
                hint={
                  !canCreate.allowed
                    ? t("upload.noPermission")
                    : !quoteId
                      ? t("upload.pickQuote")
                      : !file
                        ? t("upload.pickFile")
                        : null
                }
              >
                <Button
                  onClick={() => void runPipeline()}
                  disabled={!file || !quoteId || busy || !!document || !canCreate.allowed}
                >
                  <Sparkles className="h-4 w-4" />
                  {upload.isPending
                    ? t("upload.uploading")
                    : extract.isPending
                      ? t("upload.analysing")
                      : t("upload.analyse")}
                </Button>
              </DisabledHint>

              {document ? (
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={() => void retryExtraction()}
                  disabled={extract.isPending}
                >
                  <RefreshCw className={cn("h-4 w-4", extract.isPending && "animate-spin")} />
                  {t("upload.reanalyse")}
                </Button>
              ) : null}

              {document ? (
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => {
                    setDocument(null);
                    setFile(null);
                    setExtraction(null);
                    setHasSuggestion(false);
                    setDraft(EMPTY_DRAFT);
                    setWarnings([]);
                  }}
                >
                  {t("upload.startOver")}
                </Button>
              ) : null}
            </div>

            {upload.isError ? <ErrorBanner error={upload.error} /> : null}
            {extract.isError ? <ErrorBanner error={extract.error} /> : null}
          </div>
        </Section>
      </FadeUp>

      {/* ── Extraction provenance ── */}
      {extraction ? (
        <FadeUp delay={0.08}>
          <Card className="grid grid-cols-2 gap-5 p-5 md:grid-cols-5">
            <KeyValue
              label={t("ai.model")}
              value={<span className="font-mono text-mono-sm">{extraction.model}</span>}
            />
            <KeyValue
              label={t("ai.promptVersion")}
              value={<MonoChip>{extraction.prompt_version}</MonoChip>}
            />
            <KeyValue
              label={t("ai.status")}
              value={
                <StatusBadge
                  value={extraction.status}
                  label={t(`ai.statuses.${extraction.status}`)}
                />
              }
            />
            <KeyValue
              label={t("ai.confidenceLabel")}
              value={<ConfidenceBadge value={extraction.confidence} />}
            />
            <KeyValue
              label={t("ai.tokens")}
              value={formatNumber(
                (extraction.prompt_tokens ?? 0) + (extraction.completion_tokens ?? 0),
              )}
            />
            {document ? (
              <div className="col-span-2 md:col-span-5">
                <KeyValue
                  label={t("ai.sourceDocument")}
                  value={
                    <span className="flex items-center gap-2">
                      <FileText className="h-4 w-4 text-teal" />
                      {document.original_name}
                      <MonoChip>DOC-{document.id}</MonoChip>
                    </span>
                  }
                />
              </div>
            ) : null}
            {warnings.length ? (
              <div className="col-span-2 md:col-span-5 flex flex-col gap-1.5">
                {warnings.map((w) => (
                  <p key={w} className="text-caption text-amber-deep">
                    {w}
                  </p>
                ))}
              </div>
            ) : null}
          </Card>
        </FadeUp>
      ) : null}

      {/* ── Step 3: editable suggestion ── */}
      {hasSuggestion && extraction ? (
        <SuggestionForm
          draft={draft}
          setDraft={setDraft}
          extractionId={extraction.id}
          quoteId={Number(quoteId)}
          canApprove={canApprove.allowed}
          onCommitted={(proposalId) => navigate(`/proposals/${proposalId}`)}
        />
      ) : null}
    </>
  );
}

// =============================================================================
// Stepper
// =============================================================================

function Stepper({ step, labels }: { step: number; labels: string[] }) {
  return (
    <FadeUp delay={0.02}>
      <div className="flex flex-wrap items-center gap-3">
        {labels.map((label, i) => {
          const index = i + 1;
          const done = step > index;
          const active = step === index;
          return (
            <React.Fragment key={label}>
              <div
                className={cn(
                  "flex items-center gap-2 rounded-full border px-3.5 py-1.5 text-caption font-medium transition-colors",
                  active
                    ? "border-teal bg-[color-mix(in_srgb,var(--teal)_12%,transparent)] text-teal-deep"
                    : done
                      ? "border-[color-mix(in_srgb,var(--lime)_35%,transparent)] bg-[color-mix(in_srgb,var(--lime)_12%,transparent)] text-lime-deep"
                      : "border-line text-text-muted",
                )}
              >
                <span className="flex h-5 w-5 items-center justify-center rounded-full bg-[color-mix(in_srgb,var(--ink)_8%,transparent)] font-mono text-mono-sm">
                  {done ? <Check className="h-3 w-3" /> : index}
                </span>
                {label}
              </div>
              {index < labels.length ? (
                <span className="h-px w-6 bg-line" aria-hidden />
              ) : null}
            </React.Fragment>
          );
        })}
      </div>
    </FadeUp>
  );
}

// =============================================================================
// Drop zone
// =============================================================================

function DropZone({
  file,
  onFile,
  disabled,
}: {
  file: File | null;
  onFile: (file: File | null) => void;
  disabled?: boolean;
}) {
  const { t } = useTranslation("proposals");
  const inputRef = React.useRef<HTMLInputElement>(null);
  const [over, setOver] = React.useState(false);

  return (
    <div
      onDragOver={(e) => {
        if (disabled) return;
        e.preventDefault();
        setOver(true);
      }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => {
        if (disabled) return;
        e.preventDefault();
        setOver(false);
        const dropped = e.dataTransfer.files?.[0];
        if (dropped) onFile(dropped);
      }}
      className={cn(
        "flex flex-col items-center justify-center gap-2.5 rounded-[14px] border-2 border-dashed px-6 py-10 text-center transition-colors",
        over
          ? "border-teal bg-[color-mix(in_srgb,var(--teal)_8%,transparent)]"
          : "border-line bg-bg-recessed",
        disabled && "opacity-60",
      )}
    >
      <input
        ref={inputRef}
        type="file"
        className="hidden"
        accept=".pdf,.doc,.docx,.xls,.xlsx,.png,.jpg,.jpeg,.txt"
        disabled={disabled}
        onChange={(e) => onFile(e.target.files?.[0] ?? null)}
      />
      <div className="rounded-full bg-bg-surface p-3 text-teal">
        <Upload className="h-5 w-5" />
      </div>
      {file ? (
        <>
          <p className="text-body font-medium text-text-primary">{file.name}</p>
          <p className="text-caption text-text-muted">
            {formatNumber(file.size / 1024, 0)} KB
          </p>
        </>
      ) : (
        <>
          <p className="text-body font-medium text-text-primary">{t("upload.dropTitle")}</p>
          <p className="text-caption text-text-muted">{t("upload.dropHint")}</p>
        </>
      )}
      <Button
        variant="secondary"
        size="sm"
        disabled={disabled}
        onClick={() => inputRef.current?.click()}
      >
        {file ? t("upload.changeFile") : t("upload.chooseFile")}
      </Button>
    </div>
  );
}

// =============================================================================
// Suggestion form
// =============================================================================

function Field({
  label,
  value,
  onChange,
  type = "text",
  placeholder,
  hint,
  tone,
  disabled,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  type?: string;
  placeholder?: string;
  hint?: React.ReactNode;
  tone?: "warn" | "danger";
  disabled?: boolean;
}) {
  const id = React.useId();
  return (
    <div className="flex flex-col gap-1.5">
      <Label htmlFor={id}>{label}</Label>
      <Input
        id={id}
        type={type}
        value={value}
        placeholder={placeholder}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value)}
        step={type === "number" ? "0.0001" : undefined}
        className={cn(
          type === "number" && "text-right tabular-nums",
          tone === "danger" && "border-signal-danger",
          tone === "warn" && "border-amber",
        )}
      />
      {hint ? <p className="text-caption text-text-muted">{hint}</p> : null}
    </div>
  );
}

function SuggestionForm({
  draft,
  setDraft,
  extractionId,
  quoteId,
  canApprove,
  onCommitted,
}: {
  draft: Draft;
  setDraft: React.Dispatch<React.SetStateAction<Draft>>;
  extractionId: number;
  quoteId: number;
  canApprove: boolean;
  onCommitted: (proposalId: number) => void;
}) {
  const { t } = useTranslation("proposals");
  const { t: tc } = useTranslation("common");
  const confirm = useConfirmExtraction(extractionId);
  const match = useMatchInsurer();

  const set = <K extends keyof Draft>(field: K, value: Draft[K]) =>
    setDraft((prev) => ({ ...prev, [field]: value }));

  // Live Chilean premium arithmetic — a preview of what the server will check.
  const taxable = num(draft.taxable_premium_uf);
  const exempt = num(draft.exempt_premium_uf);
  const derived = deriveMoney(
    taxable,
    exempt,
    num(draft.taxable_rate_permille),
    num(draft.exempt_rate_permille),
  );
  const netMismatch = differs(num(draft.net_premium_uf), derived.net);
  const vatMismatch = differs(num(draft.vat_uf), derived.vat);
  const totalMismatch = differs(num(draft.total_premium_uf), derived.total);
  const rateMismatch = differs(
    num(draft.comprehensive_rate_permille),
    derived.comprehensiveRate,
  );

  const applyDerived = () =>
    setDraft((prev) => ({
      ...prev,
      net_premium_uf: derived.net.toFixed(4),
      vat_uf: derived.vat.toFixed(4),
      total_premium_uf: derived.total.toFixed(4),
      comprehensive_rate_permille:
        derived.comprehensiveRate === null
          ? prev.comprehensive_rate_permille
          : derived.comprehensiveRate.toFixed(4),
    }));

  const missingIdentity = !draft.insurer_rut.trim() || !draft.insurer_cmf_code.trim();

  const blockedReason = !canApprove
    ? t("upload.confirmNoPermission")
    : missingIdentity
      ? t("upload.identityRequired")
      : null;

  const commit = () => {
    confirm.mutate(
      {
        quote_request_id: quoteId,
        proposal: toSuggestion(draft),
        status: "submitted",
        is_confirmed: true,
      },
      {
        onSuccess: (proposal) => {
          toast.success(t("upload.committed"));
          onCommitted(proposal.id);
        },
        onError: (error) => toast.error(apiError(error, tc("toast.error"))),
      },
    );
  };

  return (
    <>
      {/* Insurer identity */}
      <FadeUp delay={0.1}>
        <Section
          title={t("upload.insurerTitle")}
          description={t("upload.insurerDescription")}
          actions={
            <Button
              variant="secondary"
              size="sm"
              disabled={missingIdentity || match.isPending}
              onClick={() =>
                match.mutate(
                  {
                    rut: draft.insurer_rut.trim() || null,
                    cmf_code: draft.insurer_cmf_code.trim() || null,
                    legal_name: draft.insurer_legal_name.trim() || null,
                  },
                  {
                    onSuccess: (result) => {
                      setDraft((prev) => ({
                        ...prev,
                        insurer_rut: result.insurer.rut,
                        insurer_cmf_code: result.insurer.cmf_code,
                        insurer_legal_name: result.insurer.legal_name,
                        insurer_trade_name: result.insurer.trade_name ?? prev.insurer_trade_name,
                      }));
                      toast.success(
                        result.created ? t("upload.insurerCreated") : t("upload.insurerMatched"),
                      );
                    },
                    onError: (error) => toast.error(apiError(error, tc("toast.error"))),
                  },
                )
              }
            >
              <Search className="h-4 w-4" />
              {t("upload.identify")}
            </Button>
          }
        >
          <div className="grid gap-4 md:grid-cols-4">
            <Field
              label={t("fields.insurerRut")}
              value={draft.insurer_rut}
              onChange={(v) => set("insurer_rut", v)}
              placeholder="99999999-9"
              tone={draft.insurer_rut.trim() ? undefined : "danger"}
            />
            <Field
              label={t("fields.insurerCmf")}
              value={draft.insurer_cmf_code}
              onChange={(v) => set("insurer_cmf_code", v)}
              placeholder="0000"
              tone={draft.insurer_cmf_code.trim() ? undefined : "danger"}
            />
            <Field
              label={t("fields.insurerLegalName")}
              value={draft.insurer_legal_name}
              onChange={(v) => set("insurer_legal_name", v)}
            />
            <Field
              label={t("fields.insurerTradeName")}
              value={draft.insurer_trade_name}
              onChange={(v) => set("insurer_trade_name", v)}
            />
          </div>
          {missingIdentity ? (
            <p className="mt-3 text-caption text-signal-danger">{t("upload.identityRequired")}</p>
          ) : null}
          {match.data ? (
            <p className="mt-3 flex items-center gap-2 text-caption text-text-secondary">
              <Badge variant={match.data.insurer.is_native ? "brand" : "neutral"}>
                {t(`origin.${match.data.insurer.is_native ? "native" : "external"}`)}
              </Badge>
              {match.data.insurer.legal_name}
              {match.data.matched_on ? (
                <MonoChip>{t(`upload.matchedOn.${match.data.matched_on}`)}</MonoChip>
              ) : null}
            </p>
          ) : null}
          {match.isError ? <ErrorBanner error={match.error} className="mt-3" /> : null}
        </Section>
      </FadeUp>

      {/* Money */}
      <FadeUp delay={0.12}>
        <Section
          title={t("upload.moneyTitle")}
          description={t("upload.moneyDescription")}
          actions={
            <Button variant="secondary" size="sm" onClick={applyDerived}>
              <RefreshCw className="h-4 w-4" />
              {t("upload.recalculate")}
            </Button>
          }
        >
          <div className="grid gap-4 md:grid-cols-3 lg:grid-cols-5">
            <Field
              label={t("fields.taxablePremium")}
              value={draft.taxable_premium_uf}
              onChange={(v) => set("taxable_premium_uf", v)}
              type="number"
            />
            <Field
              label={t("fields.exemptPremium")}
              value={draft.exempt_premium_uf}
              onChange={(v) => set("exempt_premium_uf", v)}
              type="number"
            />
            <Field
              label={t("fields.netPremium")}
              value={draft.net_premium_uf}
              onChange={(v) => set("net_premium_uf", v)}
              type="number"
              tone={netMismatch ? "warn" : undefined}
              hint={netMismatch ? t("upload.expected", { value: uf(derived.net) }) : undefined}
            />
            <Field
              label={t("fields.vat")}
              value={draft.vat_uf}
              onChange={(v) => set("vat_uf", v)}
              type="number"
              tone={vatMismatch ? "warn" : undefined}
              hint={vatMismatch ? t("upload.expected", { value: uf(derived.vat) }) : undefined}
            />
            <Field
              label={t("fields.totalPremium")}
              value={draft.total_premium_uf}
              onChange={(v) => set("total_premium_uf", v)}
              type="number"
              tone={totalMismatch ? "warn" : undefined}
              hint={totalMismatch ? t("upload.expected", { value: uf(derived.total) }) : undefined}
            />
            <Field
              label={t("fields.taxableRate")}
              value={draft.taxable_rate_permille}
              onChange={(v) => set("taxable_rate_permille", v)}
              type="number"
            />
            <Field
              label={t("fields.exemptRate")}
              value={draft.exempt_rate_permille}
              onChange={(v) => set("exempt_rate_permille", v)}
              type="number"
            />
            <Field
              label={t("fields.comprehensiveRate")}
              value={draft.comprehensive_rate_permille}
              onChange={(v) => set("comprehensive_rate_permille", v)}
              type="number"
              tone={rateMismatch ? "warn" : undefined}
            />
            <Field
              label={t("fields.commission")}
              value={draft.commission_pct}
              onChange={(v) => set("commission_pct", v)}
              type="number"
            />
            <Field
              label={t("fields.validityDays")}
              value={draft.validity_business_days}
              onChange={(v) => set("validity_business_days", v)}
              type="number"
            />
          </div>
          <p className="mt-4 rounded-[10px] bg-bg-recessed px-3.5 py-2.5 font-mono text-mono-sm text-text-muted">
            {t("upload.formula")}
          </p>
        </Section>
      </FadeUp>

      {/* Terms */}
      <FadeUp delay={0.14}>
        <Section title={t("upload.termsTitle")}>
          <div className="grid gap-4 md:grid-cols-3">
            <Field
              label={t("fields.coverageStart")}
              value={draft.coverage_start}
              onChange={(v) => set("coverage_start", v)}
              type="date"
            />
            <Field
              label={t("fields.coverageEnd")}
              value={draft.coverage_end}
              onChange={(v) => set("coverage_end", v)}
              type="date"
            />
            <Field
              label={t("fields.receivedAt")}
              value={draft.received_at}
              onChange={(v) => set("received_at", v)}
              type="date"
            />
            <Field
              label={t("fields.modality")}
              value={draft.modality}
              onChange={(v) => set("modality", v)}
            />
            <Field
              label={t("fields.activity")}
              value={draft.activity_classification}
              onChange={(v) => set("activity_classification", v)}
            />
            <Field
              label={t("fields.warranties")}
              value={draft.warranties}
              onChange={(v) => set("warranties", v)}
            />
            <div className="md:col-span-3">
              <Field
                label={t("fields.notes")}
                value={draft.notes}
                onChange={(v) => set("notes", v)}
              />
            </div>
          </div>
        </Section>
      </FadeUp>

      {/* Deductibles */}
      <FadeUp delay={0.16}>
        <Section
          title={t("upload.deductiblesTitle")}
          description={t("upload.deductiblesDescription")}
          actions={
            <Button
              variant="secondary"
              size="sm"
              onClick={() =>
                set("deductibles", [
                  ...draft.deductibles,
                  {
                    key: key(),
                    peril: "",
                    basis: "",
                    pct: "",
                    min_uf: "",
                    max_uf: "",
                    amount_uf: "",
                    notes: "",
                  },
                ])
              }
            >
              <Plus className="h-4 w-4" />
              {t("upload.addPeril")}
            </Button>
          }
        >
          {draft.deductibles.length === 0 ? (
            <p className="text-body text-text-muted">{t("upload.noDeductibles")}</p>
          ) : (
            <div className="flex flex-col gap-3">
              {draft.deductibles.map((d) => (
                <div
                  key={d.key}
                  className="grid gap-3 rounded-[12px] border border-line p-3.5 md:grid-cols-7"
                >
                  <Field
                    label={t("fields.peril")}
                    value={d.peril}
                    onChange={(v) =>
                      set(
                        "deductibles",
                        draft.deductibles.map((x) => (x.key === d.key ? { ...x, peril: v } : x)),
                      )
                    }
                    placeholder="fire"
                  />
                  <Field
                    label={t("fields.basis")}
                    value={d.basis}
                    onChange={(v) =>
                      set(
                        "deductibles",
                        draft.deductibles.map((x) => (x.key === d.key ? { ...x, basis: v } : x)),
                      )
                    }
                    placeholder="loss"
                  />
                  <Field
                    label={t("fields.pct")}
                    value={d.pct}
                    type="number"
                    onChange={(v) =>
                      set(
                        "deductibles",
                        draft.deductibles.map((x) => (x.key === d.key ? { ...x, pct: v } : x)),
                      )
                    }
                  />
                  <Field
                    label={t("fields.minUf")}
                    value={d.min_uf}
                    type="number"
                    onChange={(v) =>
                      set(
                        "deductibles",
                        draft.deductibles.map((x) => (x.key === d.key ? { ...x, min_uf: v } : x)),
                      )
                    }
                  />
                  <Field
                    label={t("fields.maxUf")}
                    value={d.max_uf}
                    type="number"
                    onChange={(v) =>
                      set(
                        "deductibles",
                        draft.deductibles.map((x) => (x.key === d.key ? { ...x, max_uf: v } : x)),
                      )
                    }
                  />
                  <Field
                    label={t("fields.amountUf")}
                    value={d.amount_uf}
                    type="number"
                    onChange={(v) =>
                      set(
                        "deductibles",
                        draft.deductibles.map((x) =>
                          x.key === d.key ? { ...x, amount_uf: v } : x,
                        ),
                      )
                    }
                  />
                  <div className="flex items-end">
                    <Button
                      variant="ghost"
                      size="icon"
                      aria-label={tc("actions.delete")}
                      onClick={() =>
                        set(
                          "deductibles",
                          draft.deductibles.filter((x) => x.key !== d.key),
                        )
                      }
                    >
                      <Trash2 className="h-4 w-4 text-signal-danger" />
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </Section>
      </FadeUp>

      {/* Coverages / exclusions */}
      <FadeUp delay={0.18}>
        <Section
          title={t("upload.coveragesTitle")}
          description={t("upload.coveragesDescription")}
          actions={
            <Button
              variant="secondary"
              size="sm"
              onClick={() =>
                set("coverages", [
                  ...draft.coverages,
                  {
                    key: key(),
                    kind: "coverage",
                    text: "",
                    normalized_code: null,
                    sort_order: draft.coverages.length,
                  },
                ])
              }
            >
              <Plus className="h-4 w-4" />
              {t("upload.addCoverage")}
            </Button>
          }
        >
          {draft.coverages.length === 0 ? (
            <p className="text-body text-text-muted">{t("upload.noCoverages")}</p>
          ) : (
            <div className="flex flex-col gap-2.5">
              {draft.coverages.map((c) => (
                <div key={c.key} className="flex items-center gap-2.5">
                  <Select
                    value={c.kind}
                    onValueChange={(v) =>
                      set(
                        "coverages",
                        draft.coverages.map((x) =>
                          x.key === c.key ? { ...x, kind: v as CoverageSuggestion["kind"] } : x,
                        ),
                      )
                    }
                  >
                    <SelectTrigger className="h-9 w-[150px]">
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
                  <Input
                    value={c.text}
                    className="h-9"
                    placeholder={t("upload.coveragePlaceholder")}
                    onChange={(e) =>
                      set(
                        "coverages",
                        draft.coverages.map((x) =>
                          x.key === c.key ? { ...x, text: e.target.value } : x,
                        ),
                      )
                    }
                  />
                  <Button
                    variant="ghost"
                    size="icon"
                    className="h-9 w-9 shrink-0"
                    aria-label={tc("actions.delete")}
                    onClick={() =>
                      set(
                        "coverages",
                        draft.coverages.filter((x) => x.key !== c.key),
                      )
                    }
                  >
                    <Trash2 className="h-4 w-4 text-signal-danger" />
                  </Button>
                </div>
              ))}
            </div>
          )}
        </Section>
      </FadeUp>

      {/* Commit */}
      <FadeUp delay={0.2}>
        <Card className="flex flex-wrap items-center justify-between gap-4 p-5">
          <div className="flex items-start gap-3">
            <span className="rounded-full bg-[color-mix(in_srgb,var(--teal)_12%,transparent)] p-2 text-teal-deep">
              <Bot className="h-5 w-5" />
            </span>
            <div>
              <p className="text-body font-medium text-text-primary">{t("upload.commitTitle")}</p>
              <p className="text-caption text-text-muted">{t("upload.commitHint")}</p>
            </div>
          </div>
          <DisabledHint hint={blockedReason}>
            <Button size="lg" disabled={!!blockedReason || confirm.isPending} onClick={commit}>
              <Check className="h-4 w-4" />
              {confirm.isPending ? tc("actions.loading") : t("upload.commit")}
            </Button>
          </DisabledHint>
          {confirm.isError ? (
            <div className="w-full">
              <ErrorBanner error={confirm.error} />
            </div>
          ) : null}
        </Card>
      </FadeUp>
    </>
  );
}
