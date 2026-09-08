/**
 * Claim detail.
 *
 * Reading order matches how a claim is actually argued:
 *   1. WHEN it happened and WHEN it was noticed — with the hour, because the
 *      notice deadline and the hourly franchise are both contractual;
 *   2. what it cost, per partida (ClaimItemsTable);
 *   3. what the adjuster ruled, and whether a warranty is in the causal chain
 *      (AdjusterReportPanel);
 *   4. what it would have cost under the prior program (CounterfactualCard) —
 *      the renewal conversation.
 *
 * Closing is `Claims.Approve` only: it fixes the final ruling and the account
 * loss ratio.
 */
import * as React from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { ArrowLeft, CheckCircle2, Clock, FolderOpen } from "lucide-react";

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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { formatDate } from "@/lib/format";
import { useCan } from "@/lib/permissions";
import { useClaim, useCloseClaim } from "@/api/claims";
import { usePolicy } from "@/api/policies";
import { CLAIM_RULINGS, type Claim, type ClaimRuling } from "@/api/types";
import {
  DisabledHint,
  ErrorBanner,
  KeyValue,
  MonoChip,
  Section,
  apiError,
  uf,
} from "@/pages/proposals/shared";
import { DateTimeValue, PostsaleBadge, daysBetween } from "@/pages/policies/shared";
import { ClaimItemsTable } from "@/pages/claims/ClaimItemsTable";
import { AdjusterReportPanel } from "@/pages/claims/AdjusterReportPanel";
import { CounterfactualCard } from "@/pages/claims/CounterfactualCard";

const textareaClass =
  "w-full rounded-lg border border-line bg-bone px-3.5 py-2 text-body text-ink outline-none transition-[border-color,box-shadow] duration-150 focus-visible:border-brand focus-visible:ring-2 focus-visible:ring-brand-ring";

function CloseDialog({
  claim,
  open,
  onOpenChange,
}: {
  claim: Claim;
  open: boolean;
  onOpenChange: (value: boolean) => void;
}) {
  const { t } = useTranslation("postsale");
  const { t: tc } = useTranslation("common");
  const close = useCloseClaim(claim.id);

  const [ruling, setRuling] = React.useState<ClaimRuling>(
    claim.coverage_ruling === "pending" ? "covered" : claim.coverage_ruling,
  );
  const [settled, setSettled] = React.useState(claim.settled_amount_uf ?? "");
  const [paid, setPaid] = React.useState(claim.paid_amount_uf ?? "");
  const [deductible, setDeductible] = React.useState(claim.deductible_uf ?? "");
  const [recovery, setRecovery] = React.useState(claim.recovery_uf ?? "");
  const [lossRatio, setLossRatio] = React.useState(claim.loss_ratio_pct ?? "");
  const [note, setNote] = React.useState("");

  const submit = async () => {
    try {
      await close.mutateAsync({
        coverage_ruling: ruling,
        settled_amount_uf: settled === "" ? null : settled,
        paid_amount_uf: paid === "" ? null : paid,
        deductible_uf: deductible === "" ? null : deductible,
        recovery_uf: recovery === "" ? null : recovery,
        loss_ratio_pct: lossRatio === "" ? null : lossRatio,
        note: note.trim() || null,
      });
      toast.success(t("shared.saved"));
      onOpenChange(false);
    } catch (error) {
      toast.error(apiError(error, t("shared.notFound")));
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t("claim.closeDialog.title")}</DialogTitle>
          <DialogDescription>{t("claim.actions.closeHint")}</DialogDescription>
        </DialogHeader>

        <div className="flex flex-col gap-3">
          <div className="flex flex-col gap-1.5">
            <Label>{t("claim.closeDialog.ruling")}</Label>
            <Select value={ruling} onValueChange={(v) => setRuling(v as ClaimRuling)}>
              <SelectTrigger className="h-9">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {CLAIM_RULINGS.map((r) => (
                  <SelectItem key={r} value={r}>
                    {t(`claim.ruling.${r}`)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div className="flex flex-col gap-1.5">
              <Label>{t("claim.closeDialog.settled")}</Label>
              <Input
                inputMode="decimal"
                value={settled}
                onChange={(e) => setSettled(e.target.value)}
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label>{t("claim.closeDialog.paid")}</Label>
              <Input inputMode="decimal" value={paid} onChange={(e) => setPaid(e.target.value)} />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label>{t("claim.closeDialog.deductible")}</Label>
              <Input
                inputMode="decimal"
                value={deductible}
                onChange={(e) => setDeductible(e.target.value)}
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label>{t("claim.closeDialog.recovery")}</Label>
              <Input
                inputMode="decimal"
                value={recovery}
                onChange={(e) => setRecovery(e.target.value)}
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label>{t("claim.closeDialog.lossRatio")}</Label>
              <Input
                inputMode="decimal"
                value={lossRatio}
                onChange={(e) => setLossRatio(e.target.value)}
              />
            </div>
          </div>

          <div className="flex flex-col gap-1.5">
            <Label>{t("claim.closeDialog.note")}</Label>
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
          <Button disabled={close.isPending} onClick={() => void submit()}>
            <CheckCircle2 className="h-4 w-4" />
            {close.isPending ? tc("actions.loading") : t("claim.closeDialog.submit")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export default function ClaimDetailPage() {
  const { claimId } = useParams<{ claimId: string }>();
  const id = Number(claimId);
  const { t } = useTranslation("postsale");
  const { t: tc } = useTranslation("common");
  const navigate = useNavigate();

  const claim = useClaim(Number.isFinite(id) ? id : undefined);
  const policy = usePolicy(claim.data?.policy_id ?? undefined);
  const canClose = useCan("Claims", "Approve");

  const [closeOpen, setCloseOpen] = React.useState(false);
  const [finalPayload, setFinalPayload] = React.useState<Record<string, unknown> | null>(null);

  if (claim.isLoading) {
    return (
      <div className="flex flex-col gap-4">
        <Skeleton className="h-9 w-72" />
        <Skeleton className="h-40 w-full" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }

  if (claim.isError || !claim.data) {
    return (
      <>
        <PageHeader title={t("claim.title")} />
        <ErrorBanner error={claim.error ?? t("claim.detail.notFound")} />
        <Button variant="secondary" size="sm" onClick={() => navigate("/claims")}>
          <ArrowLeft className="h-4 w-4" />
          {tc("actions.back")}
        </Button>
      </>
    );
  }

  const c = claim.data;
  const noticeLag = daysBetween(
    c.occurred_at ?? c.event_date,
    c.reported_at ?? c.reported_date,
  );
  const onTime =
    noticeLag !== null && c.notice_deadline_days !== null
      ? noticeLag <= c.notice_deadline_days
      : null;
  const closed = c.status === "closed";

  return (
    <>
      <PageHeader
        eyebrow={
          <Link to="/claims" className="inline-flex items-center gap-1.5 no-underline">
            <ArrowLeft className="h-3.5 w-3.5" />
            {t("claim.title")}
          </Link>
        }
        title={
          c.claim_number
            ? t("claim.detail.heading", { number: c.claim_number })
            : t("claim.detail.untitled")
        }
        subtitle={
          <span className="flex flex-wrap items-center gap-2">
            <PostsaleBadge value={c.status} label={t(`claim.status.${c.status}`)} />
            <PostsaleBadge
              value={c.coverage_ruling}
              label={t(`claim.ruling.${c.coverage_ruling}`)}
            />
            {c.policy_number ? <MonoChip>{c.policy_number}</MonoChip> : null}
            {onTime === true ? (
              <Badge variant="success">{t("claim.noticeOnTime")}</Badge>
            ) : onTime === false ? (
              <Badge variant="danger">{t("claim.noticeLate")}</Badge>
            ) : null}
          </span>
        }
        actions={
          <>
            {c.policy_id ? (
              <Button asChild variant="secondary" size="sm">
                <Link to={`/policies/${c.policy_id}`}>
                  <ArrowLeft className="h-4 w-4" />
                  {policy.data?.policy_number ?? t("claim.actions.backToPolicy")}
                </Link>
              </Button>
            ) : null}
            {c.case_file_id ? (
              <Button asChild variant="secondary" size="sm">
                <Link to={`/cases/${c.case_file_id}`}>
                  <FolderOpen className="h-4 w-4" />
                  {t("claim.actions.openCase")}
                </Link>
              </Button>
            ) : null}
            <DisabledHint
              hint={
                closed
                  ? t("claim.status.closed")
                  : canClose.allowed
                    ? null
                    : t("claim.noPermission")
              }
            >
              <Button size="sm" disabled={!canClose.allowed || closed} onClick={() => setCloseOpen(true)}>
                <CheckCircle2 className="h-4 w-4" />
                {t("claim.actions.close")}
              </Button>
            </DisabledHint>
          </>
        }
      />

      <CloseDialog claim={c} open={closeOpen} onOpenChange={setCloseOpen} />

      <FadeUp>
        <Section title={t("claim.detail.timeline")} description={t("claim.hourHint")}>
          <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
            <KeyValue
              label={t("claim.fields.occurredAt")}
              value={<DateTimeValue value={c.occurred_at} fallbackDate={c.event_date} />}
            />
            <KeyValue
              label={t("claim.fields.reportedAt")}
              value={<DateTimeValue value={c.reported_at} fallbackDate={c.reported_date} />}
            />
            <KeyValue
              label={t("claim.fields.noticeDeadline")}
              value={
                c.notice_deadline_days === null
                  ? "—"
                  : t("shared.days", { count: c.notice_deadline_days })
              }
            />
            <KeyValue
              label={t("claim.fields.noticeLag")}
              value={
                noticeLag === null ? (
                  "—"
                ) : (
                  <span className="inline-flex items-center gap-1.5">
                    <Clock className="h-3.5 w-3.5" />
                    {t("shared.days", { count: noticeLag })}
                  </span>
                )
              }
              tone={onTime === false ? "danger" : "default"}
            />
          </div>

          {c.description ? (
            <div className="mt-4">
              <div className="text-caption font-medium text-ink-3">
                {t("claim.detail.description")}
              </div>
              <p className="mt-1 whitespace-pre-line text-body text-text-secondary">
                {c.description}
              </p>
            </div>
          ) : null}
        </Section>
      </FadeUp>

      <FadeUp delay={0.04}>
        <Section title={t("claim.detail.amounts")}>
          <div className="grid grid-cols-2 gap-4 md:grid-cols-4 lg:grid-cols-6">
            <KeyValue label={t("claim.fields.estimated")} value={uf(c.estimated_amount_uf)} />
            <KeyValue label={t("claim.fields.settled")} value={uf(c.settled_amount_uf)} />
            <KeyValue label={t("claim.fields.paid")} value={uf(c.paid_amount_uf)} />
            <KeyValue label={t("claim.fields.deductible")} value={uf(c.deductible_uf)} />
            <KeyValue label={t("claim.fields.recovery")} value={uf(c.recovery_uf)} />
            <KeyValue label={t("claim.fields.reserve")} value={uf(c.reserve_uf)} />
          </div>
          <div className="mt-4 grid grid-cols-2 gap-4 md:grid-cols-4">
            <KeyValue label={t("claim.fields.cost")} value={uf(c.cost_uf)} />
            <KeyValue
              label={t("claim.fields.lossRatio")}
              value={c.loss_ratio_pct ? `${c.loss_ratio_pct} %` : "—"}
            />
            <KeyValue label={t("claim.fields.kind")} value={c.kind ?? "—"} />
            <KeyValue
              label={t("claim.columns.reported")}
              value={formatDate(c.reported_date ?? c.reported_at)}
            />
          </div>
        </Section>
      </FadeUp>

      <FadeUp delay={0.08}>
        <ClaimItemsTable claimId={c.id} />
      </FadeUp>

      <FadeUp delay={0.12}>
        <AdjusterReportPanel claim={c} onFinalPayload={setFinalPayload} />
      </FadeUp>

      <FadeUp delay={0.16}>
        <CounterfactualCard claim={c} payload={finalPayload} />
      </FadeUp>
    </>
  );
}
