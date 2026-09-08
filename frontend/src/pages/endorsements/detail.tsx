/**
 * Endorsement detail.
 *
 * Issuing is the moment that matters: `POST /endorsements/{id}/issue` attaches
 * the carrier's document AND applies the deltas to the policy and to the
 * collection plan inside one transaction. The dialog therefore asks for the
 * issued document, and the result toast reports what moved.
 *
 * `motive` is shown verbatim — the capitalised verb (INCLUYE / EXCLUYE /
 * AUMENTA) is the signal a broker reads first.
 */
import * as React from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { ArrowLeft, FileCheck2, FileText, FolderOpen, Stamp } from "lucide-react";

import { PageHeader } from "@/components/common/PageHeader";
import { FadeUp } from "@/components/common/motion";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { formatDate } from "@/lib/format";
import { useCan } from "@/lib/permissions";
import { useEndorsement, useIssueEndorsement } from "@/api/endorsements";
import { usePolicy } from "@/api/policies";
import type { Endorsement } from "@/api/types";
import {
  ConfidenceBadge,
  DisabledHint,
  ErrorBanner,
  KeyValue,
  MonoChip,
  Section,
  apiError,
  uf,
} from "@/pages/proposals/shared";
import { DateTimeValue, PostsaleBadge, renderValue } from "@/pages/policies/shared";
import { PremiumDeltaCard } from "@/pages/endorsements/PremiumDeltaCard";
import { EffectDiffTable } from "@/pages/endorsements/EffectDiffTable";

const textareaClass =
  "w-full rounded-lg border border-line bg-bone px-3.5 py-2 text-body text-ink outline-none transition-[border-color,box-shadow] duration-150 focus-visible:border-brand focus-visible:ring-2 focus-visible:ring-brand-ring";

function IssueDialog({
  endorsement,
  open,
  onOpenChange,
}: {
  endorsement: Endorsement;
  open: boolean;
  onOpenChange: (value: boolean) => void;
}) {
  const { t } = useTranslation("postsale");
  const { t: tc } = useTranslation("common");
  const issue = useIssueEndorsement(endorsement.id);

  const [documentId, setDocumentId] = React.useState(
    endorsement.issued_document_id ? String(endorsement.issued_document_id) : "",
  );
  const [number, setNumber] = React.useState(endorsement.endorsement_number ?? "");
  const [issuedAt, setIssuedAt] = React.useState(endorsement.issued_at ?? "");
  const [effectiveAt, setEffectiveAt] = React.useState(
    endorsement.effective_at ? endorsement.effective_at.slice(0, 16) : "",
  );
  const [note, setNote] = React.useState("");

  const submit = async () => {
    try {
      const result = await issue.mutateAsync({
        issued_document_id: documentId ? Number(documentId) : null,
        endorsement_number: number.trim() || null,
        issued_at: issuedAt || null,
        effective_at: effectiveAt ? new Date(effectiveAt).toISOString() : null,
        note: note.trim() || null,
      });
      toast.success(t("endorsement.actions.issueDone"), {
        description: `${t("policy.money.gross")}: ${uf(result.policy_total_premium_uf)}`,
      });
      onOpenChange(false);
    } catch (error) {
      toast.error(apiError(error, t("shared.notFound")));
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t("endorsement.issueDialog.title")}</DialogTitle>
          <DialogDescription>{t("endorsement.actions.issueHint")}</DialogDescription>
        </DialogHeader>

        <div className="flex flex-col gap-3">
          <div className="flex flex-col gap-1.5">
            <Label>{t("endorsement.issueDialog.documentId")}</Label>
            <Input
              inputMode="numeric"
              value={documentId}
              onChange={(e) => setDocumentId(e.target.value.replace(/\D/g, ""))}
            />
            <span className="text-caption text-text-muted">
              {t("endorsement.issueDialog.documentHint")}
            </span>
          </div>

          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <div className="flex flex-col gap-1.5">
              <Label>{t("endorsement.issueDialog.number")}</Label>
              <Input value={number} onChange={(e) => setNumber(e.target.value)} />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label>{t("endorsement.issueDialog.issuedAt")}</Label>
              <Input
                type="date"
                value={issuedAt}
                onChange={(e) => setIssuedAt(e.target.value)}
              />
            </div>
          </div>

          <div className="flex flex-col gap-1.5">
            <Label>{t("endorsement.issueDialog.effectiveAt")}</Label>
            <Input
              type="datetime-local"
              value={effectiveAt}
              onChange={(e) => setEffectiveAt(e.target.value)}
            />
            <span className="text-caption text-text-muted">{t("policy.noonHint")}</span>
          </div>

          <div className="flex flex-col gap-1.5">
            <Label>{t("endorsement.issueDialog.note")}</Label>
            <textarea
              rows={2}
              className={textareaClass}
              value={note}
              onChange={(e) => setNote(e.target.value)}
            />
          </div>
        </div>

        <DialogFooter>
          <Button variant="secondary" onClick={() => onOpenChange(false)}>
            {tc("actions.cancel")}
          </Button>
          <Button disabled={issue.isPending || !documentId} onClick={() => void submit()}>
            <Stamp className="h-4 w-4" />
            {issue.isPending ? tc("actions.loading") : t("endorsement.issueDialog.submit")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export default function EndorsementDetailPage() {
  const { endorsementId } = useParams<{ endorsementId: string }>();
  const id = Number(endorsementId);
  const { t } = useTranslation("postsale");
  const { t: tc } = useTranslation("common");
  const navigate = useNavigate();

  const endorsement = useEndorsement(Number.isFinite(id) ? id : undefined);
  const policy = usePolicy(endorsement.data?.policy_id);
  const canSubmit = useCan("Endorsements", "Submit");

  const [issueOpen, setIssueOpen] = React.useState(false);

  if (endorsement.isLoading) {
    return (
      <div className="flex flex-col gap-4">
        <Skeleton className="h-9 w-72" />
        <Skeleton className="h-40 w-full" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }

  if (endorsement.isError || !endorsement.data) {
    return (
      <>
        <PageHeader title={t("endorsement.title")} />
        <ErrorBanner error={endorsement.error ?? t("endorsement.detail.notFound")} />
        <Button variant="secondary" size="sm" onClick={() => navigate("/policies")}>
          <ArrowLeft className="h-4 w-4" />
          {tc("actions.back")}
        </Button>
      </>
    );
  }

  const e = endorsement.data;
  const alreadyIssued = e.status === "issued" || e.status === "applied";
  const issueGate = !canSubmit.allowed
    ? t("endorsement.noPermission")
    : alreadyIssued
      ? t("endorsement.actions.alreadyIssued")
      : null;

  return (
    <>
      <PageHeader
        eyebrow={
          <Link
            to={`/policies/${e.policy_id}`}
            className="inline-flex items-center gap-1.5 no-underline"
          >
            <ArrowLeft className="h-3.5 w-3.5" />
            {policy.data?.policy_number ?? t("endorsement.actions.backToPolicy")}
          </Link>
        }
        title={
          e.endorsement_number
            ? t("endorsement.detail.heading", { number: e.endorsement_number })
            : t("endorsement.detail.untitled", { n: e.sequence_no })
        }
        subtitle={
          <span className="flex flex-wrap items-center gap-2">
            <PostsaleBadge value={e.status} label={t(`endorsement.status.${e.status}`)} />
            <Badge variant="action">{t(`endorsement.kind.${e.kind}`)}</Badge>
            <MonoChip>E{e.sequence_no}</MonoChip>
            {e.is_confirmed ? (
              <Badge variant="success">{t("endorsement.fields.confirmed")}</Badge>
            ) : (
              <ConfidenceBadge value={e.extraction_confidence} />
            )}
          </span>
        }
        actions={
          <>
            {e.case_file_id ? (
              <Button asChild variant="secondary" size="sm">
                <Link to={`/cases/${e.case_file_id}`}>
                  <FolderOpen className="h-4 w-4" />
                  {t("endorsement.fields.caseFile")}
                </Link>
              </Button>
            ) : null}
            <DisabledHint hint={issueGate}>
              <Button
                size="sm"
                disabled={!!issueGate}
                onClick={() => setIssueOpen(true)}
              >
                <Stamp className="h-4 w-4" />
                {t("endorsement.actions.issue")}
              </Button>
            </DisabledHint>
          </>
        }
      />

      <IssueDialog endorsement={e} open={issueOpen} onOpenChange={setIssueOpen} />

      <FadeUp>
        <Section title={t("endorsement.detail.identity")}>
          <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
            <KeyValue
              label={t("endorsement.fields.number")}
              value={e.endorsement_number ?? "—"}
              mono
            />
            <KeyValue label={t("endorsement.fields.sequence")} value={`E${e.sequence_no}`} />
            <KeyValue
              label={t("endorsement.fields.policy")}
              value={
                <Link to={`/policies/${e.policy_id}`}>
                  {policy.data?.policy_number ?? `#${e.policy_id}`}
                </Link>
              }
            />
            <KeyValue
              label={t("endorsement.fields.issuedAt")}
              value={formatDate(e.issued_at)}
            />
            <KeyValue
              label={t("endorsement.fields.effectiveAt")}
              value={<DateTimeValue value={e.effective_at} />}
            />
            <KeyValue
              label={t("endorsement.fields.endsAt")}
              value={<DateTimeValue value={e.ends_at} />}
            />
            <KeyValue
              label={t("endorsement.fields.contractualBasis")}
              value={e.contractual_basis ?? "—"}
            />
            <KeyValue
              label={t("endorsement.fields.prorataDays")}
              value={
                e.prorata_days === null
                  ? "—"
                  : t("shared.days", { count: e.prorata_days })
              }
            />
          </div>
          <p className="mt-3 text-caption text-text-muted">{t("policy.noonHint")}</p>
        </Section>
      </FadeUp>

      {e.motive ? (
        <FadeUp delay={0.04}>
          <Section title={t("endorsement.detail.motive")}>
            <p className="whitespace-pre-line text-body text-text-primary">{e.motive}</p>
          </Section>
        </FadeUp>
      ) : null}

      <FadeUp delay={0.08}>
        <PremiumDeltaCard endorsement={e} />
      </FadeUp>

      <FadeUp delay={0.12}>
        <EffectDiffTable effect={e.effect} />
      </FadeUp>

      {e.deductibles ? (
        <FadeUp delay={0.16}>
          <Section title={t("policy.fields.deductibles")}>
            <p className="text-body text-text-secondary">{renderValue(e.deductibles)}</p>
          </Section>
        </FadeUp>
      ) : null}

      <FadeUp delay={0.2}>
        <Section title={t("endorsement.detail.documents")}>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <KeyValue
              label={t("endorsement.fields.proposalDocument")}
              value={
                e.proposal_document_id ? (
                  <span className="inline-flex items-center gap-1.5">
                    <FileText className="h-3.5 w-3.5" />#{e.proposal_document_id}
                  </span>
                ) : (
                  "—"
                )
              }
            />
            <KeyValue
              label={t("endorsement.fields.issuedDocument")}
              value={
                e.issued_document_id ? (
                  <span className="inline-flex items-center gap-1.5">
                    <FileCheck2 className="h-3.5 w-3.5" />#{e.issued_document_id}
                  </span>
                ) : (
                  t("endorsement.actions.needsDocument")
                )
              }
              tone={e.issued_document_id ? "default" : "warn"}
            />
          </div>
        </Section>
      </FadeUp>
    </>
  );
}
