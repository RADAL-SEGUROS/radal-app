/**
 * Offering — build and share.
 *
 * Three steps, each backed by a real endpoint:
 *   1. pick the recommended proposal  -> PATCH /offerings/{id}
 *   2. the branded PDF                -> GET/POST /offerings/{id}/pdf
 *   3. share it                       -> the broker opens WhatsApp / their mail
 *                                        client / the file, and we record which
 *                                        channel with POST /offerings/{id}/send
 *
 * The send endpoint 422s without a selected proposal, so every share control is
 * disabled with that exact reason until one is chosen.
 */
import * as React from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import {
  ArrowLeft,
  Check,
  Download,
  Eye,
  FileText,
  Link2,
  Mail,
  MessageCircle,
  RefreshCw,
} from "lucide-react";

import { PageHeader } from "@/components/common/PageHeader";
import { FadeUp } from "@/components/common/motion";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";
import { useCan } from "@/lib/permissions";
import { formatDate, formatDateTime } from "@/lib/format";
import {
  useOffering,
  useOfferingPdf,
  useRecordOfferingSent,
  useRegenerateOfferingPdf,
  useUpdateOffering,
} from "@/api/offerings";
import { useProposals } from "@/api/proposals";
import { useQuote } from "@/api/quotes";
import type { Offering, OfferingChannel, Proposal } from "@/api/types";
import {
  ConfidenceBadge,
  CopyButton,
  DisabledHint,
  ErrorBanner,
  KeyValue,
  MonoChip,
  Section,
  StatusBadge,
  apiError,
  permille,
  resolveFileUrl,
  uf,
} from "@/pages/proposals/shared";

export default function OfferingDetailPage() {
  const { offeringId } = useParams<{ offeringId: string }>();
  const id = Number(offeringId);
  const { t } = useTranslation("offerings");
  const { t: tc } = useTranslation("common");
  const navigate = useNavigate();

  const offering = useOffering(Number.isFinite(id) ? id : undefined);

  if (offering.isLoading) {
    return (
      <div className="flex flex-col gap-4">
        <Skeleton className="h-9 w-72" />
        <Skeleton className="h-40 w-full" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }

  if (offering.isError || !offering.data) {
    return (
      <>
        <PageHeader title={t("detail.title")} />
        <ErrorBanner error={offering.error ?? t("detail.notFound")} />
        <Button variant="secondary" size="sm" onClick={() => navigate("/offerings")}>
          <ArrowLeft className="h-4 w-4" />
          {tc("actions.back")}
        </Button>
      </>
    );
  }

  const o = offering.data;

  return (
    <>
      <PageHeader
        eyebrow={
          <Link to="/offerings" className="inline-flex items-center gap-1.5 no-underline">
            <ArrowLeft className="h-3.5 w-3.5" />
            {t("title")}
          </Link>
        }
        title={t("detail.heading", { id: String(o.id).padStart(4, "0") })}
        subtitle={
          <span className="flex flex-wrap items-center gap-2">
            <StatusBadge value={o.status} label={t(`status.${o.status}`)} />
            {o.sent_via ? <Badge variant="action">{t(`channels.${o.sent_via}`)}</Badge> : null}
            <Link to={`/quotes/${o.quote_request_id}`} className="text-caption tabular-nums">
              COT-{String(o.quote_request_id).padStart(4, "0")}
            </Link>
          </span>
        }
      />

      <FadeUp delay={0.05}>
        <Card className="grid grid-cols-2 gap-5 p-5 md:grid-cols-4">
          <KeyValue
            label={t("detail.createdAt")}
            value={o.created_at ? formatDateTime(o.created_at) : "—"}
          />
          <KeyValue
            label={t("detail.sentAt")}
            value={o.sent_at ? formatDateTime(o.sent_at) : t("detail.notSent")}
          />
          <KeyValue
            label={t("detail.viewedAt")}
            value={
              o.viewed_at ? (
                <span className="flex items-center gap-1.5">
                  <Eye className="h-4 w-4 text-pos-text" />
                  {formatDateTime(o.viewed_at)}
                </span>
              ) : (
                t("detail.notViewed")
              )
            }
          />
          <KeyValue
            label={t("detail.expiresAt")}
            value={o.expires_at ? formatDate(o.expires_at) : t("detail.noExpiry")}
          />
        </Card>
      </FadeUp>

      <ProposalPicker offering={o} />
      <PdfPanel offering={o} />
      <SharePanel offering={o} />
      <ExpiryPanel offering={o} />
    </>
  );
}

// =============================================================================
// 1. Recommended proposal
// =============================================================================

function ProposalPicker({ offering }: { offering: Offering }) {
  const { t } = useTranslation("offerings");
  const { t: tp } = useTranslation("proposals");
  const { t: tc } = useTranslation("common");
  const proposals = useProposals({ quote_request_id: offering.quote_request_id, limit: 50 });
  const quote = useQuote(offering.quote_request_id);
  const update = useUpdateOffering(offering.id);
  const canEdit = useCan("Offerings", "Edit");

  const items = proposals.data?.items ?? [];

  const select = (proposal: Proposal) =>
    update.mutate(
      { selected_proposal_id: proposal.id },
      {
        onSuccess: () => toast.success(t("detail.selectionSaved")),
        onError: (error) => toast.error(apiError(error, tc("toast.error"))),
      },
    );

  return (
    <FadeUp delay={0.08}>
      <Section
        title={t("detail.step1Title")}
        description={t("detail.step1Description", {
          object: quote.data?.insured_object ?? "",
        })}
        actions={
          <Button variant="secondary" size="sm" asChild>
            <Link to={`/quotes/${offering.quote_request_id}/comparison`}>
              {t("detail.openComparison")}
            </Link>
          </Button>
        }
      >
        {proposals.isLoading ? (
          <Skeleton className="h-24 w-full" />
        ) : items.length === 0 ? (
          <p className="text-body text-text-muted">{t("detail.noProposals")}</p>
        ) : (
          <div className="grid gap-3 md:grid-cols-2 lg:grid-cols-3">
            {items.map((p) => {
              const selected = offering.selected_proposal_id === p.id;
              return (
                <button
                  key={p.id}
                  type="button"
                  disabled={!canEdit.allowed || update.isPending}
                  onClick={() => select(p)}
                  className={cn(
                    "flex flex-col gap-2 rounded-card border p-4 text-left transition-all duration-150",
                    selected
                      ? "border-brand bg-brand-soft shadow-elev"
                      : "border-line bg-bg-surface hover:-translate-y-[2px] hover:border-brand-line",
                    (!canEdit.allowed || update.isPending) && "cursor-not-allowed opacity-70",
                  )}
                >
                  <span className="flex items-start justify-between gap-2">
                    <span className="min-w-0">
                      <span className="block truncate font-display text-h3 text-text-primary">
                        {p.insurer?.trade_name || p.insurer?.legal_name || `#${p.insurer_id}`}
                      </span>
                      <span className="mt-1 flex flex-wrap items-center gap-1.5">
                        <MonoChip>{p.insurer?.cmf_code ?? "—"}</MonoChip>
                        <Badge variant={p.origin === "native" ? "brand" : "neutral"}>
                          {tp(`origin.${p.origin}`)}
                        </Badge>
                      </span>
                    </span>
                    {selected ? (
                      <Badge variant="success" className="gap-1">
                        <Check className="h-3 w-3" />
                        {t("detail.selected")}
                      </Badge>
                    ) : null}
                  </span>

                  <span className="mt-1 flex flex-wrap items-baseline gap-3">
                    <span className="font-display text-h2 tabular-nums text-text-primary">
                      {uf(p.total_premium_uf)}
                    </span>
                    <span className="text-caption text-text-muted">
                      {permille(p.comprehensive_rate_permille)}
                    </span>
                  </span>

                  <span className="flex flex-wrap items-center gap-1.5">
                    <StatusBadge value={p.status} label={tp(`status.${p.status}`)} />
                    {p.is_confirmed ? null : <ConfidenceBadge value={p.extraction_confidence} />}
                  </span>
                </button>
              );
            })}
          </div>
        )}
        {!canEdit.allowed ? (
          <p className="mt-3 text-caption text-warn-text">{t("detail.editNoPermission")}</p>
        ) : null}
        {update.isError ? <ErrorBanner error={update.error} className="mt-3" /> : null}
      </Section>
    </FadeUp>
  );
}

// =============================================================================
// 2. PDF
// =============================================================================

function PdfPanel({ offering }: { offering: Offering }) {
  const { t } = useTranslation("offerings");
  const { t: tc } = useTranslation("common");
  const pdf = useOfferingPdf(offering.id, offering.pdf_document_id !== null);
  const regenerate = useRegenerateOfferingPdf(offering.id);
  const canEdit = useCan("Offerings", "Edit");
  const fileUrl = resolveFileUrl(pdf.data?.url);

  return (
    <FadeUp delay={0.1}>
      <Section
        title={t("detail.step2Title")}
        description={t("detail.step2Description")}
        actions={
          <DisabledHint hint={canEdit.allowed ? null : t("detail.editNoPermission")}>
            <Button
              variant="secondary"
              size="sm"
              disabled={!canEdit.allowed || regenerate.isPending}
              onClick={() =>
                regenerate.mutate(undefined, {
                  onSuccess: () => toast.success(t("detail.pdfRegenerated")),
                  onError: (error) => toast.error(apiError(error, tc("toast.error"))),
                })
              }
            >
              <RefreshCw className={cn("h-4 w-4", regenerate.isPending && "animate-spin")} />
              {t("detail.regeneratePdf")}
            </Button>
          </DisabledHint>
        }
      >
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div className="flex items-center gap-3">
            <span className="rounded-lg bg-brand-soft p-2.5 text-brand-deep">
              <FileText className="h-5 w-5" />
            </span>
            <div>
              <p className="text-body font-medium text-text-primary">
                {pdf.data?.original_name ?? t("detail.pdfPending")}
              </p>
              <p className="text-caption text-text-muted">{t("detail.pdfStub")}</p>
            </div>
          </div>
          {fileUrl ? (
            <Button variant="secondary" size="sm" asChild>
              <a href={fileUrl} target="_blank" rel="noreferrer">
                <Download className="h-4 w-4" />
                {t("detail.openPdf")}
              </a>
            </Button>
          ) : (
            <DisabledHint hint={t("detail.pdfUnavailable")}>
              <Button variant="secondary" size="sm" disabled>
                <Download className="h-4 w-4" />
                {t("detail.openPdf")}
              </Button>
            </DisabledHint>
          )}
        </div>
      </Section>
    </FadeUp>
  );
}

// =============================================================================
// 3. Share
// =============================================================================

function SharePanel({ offering }: { offering: Offering }) {
  const { t } = useTranslation("offerings");
  const { t: tc } = useTranslation("common");
  const record = useRecordOfferingSent(offering.id);
  const pdf = useOfferingPdf(offering.id, offering.pdf_document_id !== null);
  const canSubmit = useCan("Offerings", "Submit");

  const shareUrl = offering.share_url ?? "";
  const noSelection = offering.selected_proposal_id === null;

  const blockedHint = !canSubmit.allowed
    ? t("detail.sendNoPermission")
    : noSelection
      ? t("detail.selectFirst")
      : null;

  const stamp = (channel: OfferingChannel) =>
    record.mutate(
      { channel },
      {
        onSuccess: () => toast.success(t("detail.recorded", { channel: t(`channels.${channel}`) })),
        onError: (error) => toast.error(apiError(error, tc("toast.error"))),
      },
    );

  const openWhatsApp = () => {
    const text = t("detail.whatsappMessage", { url: shareUrl });
    window.open(`https://wa.me/?text=${encodeURIComponent(text)}`, "_blank", "noopener");
    stamp("whatsapp");
  };

  const openEmail = () => {
    const subject = encodeURIComponent(t("detail.emailSubject"));
    const body = encodeURIComponent(t("detail.emailBody", { url: shareUrl }));
    window.open(`mailto:?subject=${subject}&body=${body}`, "_self");
    stamp("email");
  };

  const pdfUrl = resolveFileUrl(pdf.data?.url);

  const openDownload = () => {
    if (pdfUrl) window.open(pdfUrl, "_blank", "noopener");
    stamp("download");
  };

  return (
    <FadeUp delay={0.12}>
      <Section title={t("detail.step3Title")} description={t("detail.step3Description")}>
        <div className="flex flex-col gap-4">
          <div className="flex flex-wrap items-center gap-2.5">
            <div className="flex min-w-[260px] flex-1 items-center gap-2 rounded-lg border border-line bg-bg-recessed px-3.5 py-2.5">
              <Link2 className="h-4 w-4 shrink-0 text-brand" />
              <span className="truncate text-caption text-text-secondary">
                {shareUrl || t("detail.noShareUrl")}
              </span>
            </div>
            {shareUrl ? (
              <CopyButton
                value={shareUrl}
                label={t("detail.copyLink")}
                onCopied={() => {
                  if (!blockedHint) stamp("link");
                }}
              />
            ) : (
              <DisabledHint hint={t("detail.noShareUrl")}>
                <Button variant="secondary" size="sm" disabled>
                  {t("detail.copyLink")}
                </Button>
              </DisabledHint>
            )}
          </div>

          <div className="flex flex-wrap items-center gap-2.5">
            <DisabledHint hint={blockedHint}>
              <Button
                size="sm"
                disabled={!!blockedHint || !shareUrl || record.isPending}
                onClick={openWhatsApp}
              >
                <MessageCircle className="h-4 w-4" />
                {t("detail.shareWhatsapp")}
              </Button>
            </DisabledHint>

            <DisabledHint hint={blockedHint}>
              <Button
                variant="secondary"
                size="sm"
                disabled={!!blockedHint || !shareUrl || record.isPending}
                onClick={openEmail}
              >
                <Mail className="h-4 w-4" />
                {t("detail.shareEmail")}
              </Button>
            </DisabledHint>

            <DisabledHint hint={blockedHint ?? (pdfUrl ? null : t("detail.pdfUnavailable"))}>
              <Button
                variant="secondary"
                size="sm"
                disabled={!!blockedHint || !pdfUrl || record.isPending}
                onClick={openDownload}
              >
                <Download className="h-4 w-4" />
                {t("detail.shareDownload")}
              </Button>
            </DisabledHint>
          </div>

          <p className="text-caption text-text-muted">{t("detail.manualNote")}</p>
          {record.isError ? <ErrorBanner error={record.error} /> : null}
        </div>
      </Section>
    </FadeUp>
  );
}

// =============================================================================
// Expiry
// =============================================================================

function ExpiryPanel({ offering }: { offering: Offering }) {
  const { t } = useTranslation("offerings");
  const { t: tc } = useTranslation("common");
  const update = useUpdateOffering(offering.id);
  const canEdit = useCan("Offerings", "Edit");

  const initial = offering.expires_at ? offering.expires_at.slice(0, 10) : "";
  const [value, setValue] = React.useState(initial);
  React.useEffect(() => setValue(initial), [initial]);

  const dirty = value !== initial;

  return (
    <FadeUp delay={0.14}>
      <Section title={t("detail.expiryTitle")} description={t("detail.expiryDescription")}>
        <div className="flex flex-wrap items-end gap-3">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="expires">{t("detail.expiresAt")}</Label>
            <Input
              id="expires"
              type="date"
              value={value}
              disabled={!canEdit.allowed}
              onChange={(e) => setValue(e.target.value)}
              className="w-[200px]"
            />
          </div>
          <DisabledHint
            hint={
              !canEdit.allowed
                ? t("detail.editNoPermission")
                : dirty
                  ? null
                  : t("detail.noChanges")
            }
          >
            <Button
              disabled={!canEdit.allowed || !dirty || update.isPending}
              onClick={() =>
                update.mutate(
                  {
                    expires_at: value ? new Date(`${value}T23:59:00`).toISOString() : null,
                  },
                  {
                    onSuccess: () => toast.success(tc("toast.saved")),
                    onError: (error) => toast.error(apiError(error, tc("toast.error"))),
                  },
                )
              }
            >
              {tc("actions.save")}
            </Button>
          </DisabledHint>
        </div>
        {update.isError ? <ErrorBanner error={update.error} className="mt-3" /> : null}
      </Section>
    </FadeUp>
  );
}
