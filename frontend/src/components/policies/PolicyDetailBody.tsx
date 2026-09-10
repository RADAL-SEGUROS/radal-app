/**
 * Policy detail BODY — the post-sale hub, extracted verbatim from
 * `pages/policies/detail.tsx` so `pages/groups/policy.tsx` can render the same
 * policy inside `GroupShell` while the legacy `/policies/:policyId` route keeps
 * working. The page is now a thin wrapper; nothing here changed behaviourally.
 *
 *
 * Four things live here, each backed by a real endpoint:
 *   1. the contract itself (period as DATETIME, money with the house rule);
 *   2. the versioned post-sale sub-funnel  -> GET /policies/{id}/case-files;
 *   3. the mirror validation                -> GET /policies/{id}/mirror-diff;
 *   4. the warranty tracker                 -> GET /policies/{id}/warranties.
 *
 * The premium block re-derives `net = taxable + exempt`, `vat = 0.19 * taxable`
 * and `total = net + vat` on the client purely to SHOW a mismatch. It never
 * corrects the stored figures — the server owns that and answers 422.
 */
import * as React from "react";
import { Link, useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import {
  AlertTriangle,
  ArrowLeft,
  ArrowRight,
  CheckCircle2,
  FileText,
  FolderOpen,
  Plus,
  Receipt,
  Siren,
} from "lucide-react";

import { PageHeader } from "@/components/common/PageHeader";
import { FadeUp } from "@/components/common/motion";
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
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import { formatDate } from "@/lib/format";
import { useCan } from "@/lib/permissions";
import { usePolicy } from "@/api/policies";
import { useCreateEndorsement, useEndorsements } from "@/api/endorsements";
import { useCollectionPlans } from "@/api/collections";
import { useClaims, useCreateClaim } from "@/api/claims";
import {
  ENDORSEMENT_KINDS,
  num,
  type EndorsementKind,
  type Policy,
} from "@/api/types";
import {
  DisabledHint,
  EmptyState,
  ErrorBanner,
  KeyValue,
  MonoChip,
  Section,
  SoonButton,
  apiError,
  permille,
  pct,
  uf,
} from "@/pages/proposals/shared";
import {
  DateTimeValue,
  PostsaleBadge,
  daysBetween,
  offBy,
  renderValue,
} from "@/pages/policies/shared";
import { SubFunnel } from "@/pages/policies/SubFunnel";
import { MirrorDiffPanel } from "@/pages/policies/MirrorDiffPanel";
import { WarrantyTracker } from "@/pages/policies/WarrantyTracker";
import {
  OpenSourceLink,
  PolicyPayloadPanel,
} from "@/components/policies/PolicyPayloadPanel";

/** An `<input type="datetime-local">` value -> the ISO instant the API stores. */
function toIso(value: string): string | null {
  return value ? new Date(value).toISOString() : null;
}

// =============================================================================
// Money
// =============================================================================

function MoneyBlock({ policy }: { policy: Policy }) {
  const { t } = useTranslation("postsale");

  const taxable = num(policy.taxable_premium_uf);
  const exempt = num(policy.exempt_premium_uf);
  const net = num(policy.net_premium_uf);
  const vat = num(policy.vat_uf);
  const gross = num(policy.total_premium_uf);

  const derivedNet = (taxable ?? 0) + (exempt ?? 0);
  const derivedVat = (taxable ?? 0) * 0.19;
  const derivedGross = derivedNet + derivedVat;

  const broken =
    (taxable !== null || exempt !== null) &&
    (offBy(net, derivedNet) || offBy(vat, derivedVat) || offBy(gross, derivedGross));

  return (
    <Section
      title={t("policy.detail.money")}
      description={t("policy.money.rule")}
      actions={
        broken ? (
          <Badge variant="danger" className="gap-1">
            <AlertTriangle className="h-3 w-3" />
            {t("policy.money.mismatch")}
          </Badge>
        ) : (
          <Badge variant="success" className="gap-1">
            <CheckCircle2 className="h-3 w-3" />
            {t("policy.money.checks")}
          </Badge>
        )
      }
    >
      <div className="grid grid-cols-2 gap-4 md:grid-cols-3 lg:grid-cols-6">
        <KeyValue label={t("policy.money.taxable")} value={uf(policy.taxable_premium_uf)} />
        <KeyValue label={t("policy.money.exempt")} value={uf(policy.exempt_premium_uf)} />
        <KeyValue
          label={t("policy.money.net")}
          value={uf(policy.net_premium_uf)}
          tone={offBy(net, derivedNet) ? "danger" : "default"}
        />
        <KeyValue
          label={t("policy.money.vat")}
          value={uf(policy.vat_uf)}
          tone={offBy(vat, derivedVat) ? "danger" : "default"}
        />
        <KeyValue
          label={t("policy.money.gross")}
          value={uf(policy.total_premium_uf)}
          tone={offBy(gross, derivedGross) ? "danger" : "default"}
        />
        <KeyValue label={t("policy.money.commission")} value={pct(policy.commission_pct)} />
      </div>
      <p className="mt-3 text-caption text-text-muted">{t("policy.money.vatHint")}</p>
    </Section>
  );
}

// =============================================================================
// Create dialogs — both wired, both permission-gated
// =============================================================================

function NewEndorsementDialog({
  policyId,
  open,
  onOpenChange,
}: {
  policyId: number;
  open: boolean;
  onOpenChange: (value: boolean) => void;
}) {
  const { t } = useTranslation("postsale");
  const { t: tc } = useTranslation("common");
  const navigate = useNavigate();
  const create = useCreateEndorsement();

  const [kind, setKind] = React.useState<EndorsementKind>("other");
  const [motive, setMotive] = React.useState("");
  const [effectiveAt, setEffectiveAt] = React.useState("");

  const submit = async () => {
    try {
      const created = await create.mutateAsync({
        policy_id: policyId,
        kind,
        status: "draft",
        motive: motive.trim() || null,
        effective_at: effectiveAt ? toIso(effectiveAt) : null,
      });
      onOpenChange(false);
      setMotive("");
      navigate(`/endorsements/${created.id}`);
    } catch (error) {
      toast.error(apiError(error, t("shared.notFound")));
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t("policy.actions.newEndorsement")}</DialogTitle>
          <DialogDescription>{t("endorsement.actions.issueHint")}</DialogDescription>
        </DialogHeader>

        <div className="flex flex-col gap-3">
          <div className="flex flex-col gap-1.5">
            <Label>{t("endorsement.fields.kind")}</Label>
            <Select value={kind} onValueChange={(v) => setKind(v as EndorsementKind)}>
              <SelectTrigger className="h-9">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {ENDORSEMENT_KINDS.map((k) => (
                  <SelectItem key={k} value={k}>
                    {t(`endorsement.kind.${k}`)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="flex flex-col gap-1.5">
            <Label>{t("endorsement.fields.effectiveAt")}</Label>
            <Input
              type="datetime-local"
              value={effectiveAt}
              onChange={(e) => setEffectiveAt(e.target.value)}
            />
            <span className="text-caption text-text-muted">{t("policy.noonHint")}</span>
          </div>

          <div className="flex flex-col gap-1.5">
            <Label>{t("endorsement.detail.motive")}</Label>
            <Textarea
              rows={3}
              value={motive}
              onChange={(e) => setMotive(e.target.value)}
            />
          </div>
        </div>

        <DialogFooter>
          <Button variant="secondary" onClick={() => onOpenChange(false)}>
            {tc("actions.cancel")}
          </Button>
          <Button disabled={create.isPending} onClick={() => void submit()}>
            {create.isPending ? tc("actions.loading") : tc("actions.create")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function NewClaimDialog({
  policyId,
  open,
  onOpenChange,
}: {
  policyId: number;
  open: boolean;
  onOpenChange: (value: boolean) => void;
}) {
  const { t } = useTranslation("postsale");
  const { t: tc } = useTranslation("common");
  const navigate = useNavigate();
  const create = useCreateClaim();

  const [number, setNumber] = React.useState("");
  const [occurredAt, setOccurredAt] = React.useState("");
  const [reportedAt, setReportedAt] = React.useState("");
  const [description, setDescription] = React.useState("");

  const submit = async () => {
    try {
      const created = await create.mutateAsync({
        policy_id: policyId,
        claim_number: number.trim() || null,
        occurred_at: occurredAt ? toIso(occurredAt) : null,
        reported_at: reportedAt ? toIso(reportedAt) : null,
        description: description.trim() || null,
        status: "reported",
      });
      onOpenChange(false);
      navigate(`/claims/${created.id}`);
    } catch (error) {
      toast.error(apiError(error, t("shared.notFound")));
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t("policy.actions.newClaim")}</DialogTitle>
          <DialogDescription>{t("claim.hourHint")}</DialogDescription>
        </DialogHeader>

        <div className="flex flex-col gap-3">
          <div className="flex flex-col gap-1.5">
            <Label>{t("claim.fields.claimNumber")}</Label>
            <Input value={number} onChange={(e) => setNumber(e.target.value)} />
          </div>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <div className="flex flex-col gap-1.5">
              <Label>{t("claim.fields.occurredAt")}</Label>
              <Input
                type="datetime-local"
                value={occurredAt}
                onChange={(e) => setOccurredAt(e.target.value)}
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label>{t("claim.fields.reportedAt")}</Label>
              <Input
                type="datetime-local"
                value={reportedAt}
                onChange={(e) => setReportedAt(e.target.value)}
              />
            </div>
          </div>
          <div className="flex flex-col gap-1.5">
            <Label>{t("claim.detail.description")}</Label>
            <Textarea
              rows={3}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
            />
          </div>
        </div>

        <DialogFooter>
          <Button variant="secondary" onClick={() => onOpenChange(false)}>
            {tc("actions.cancel")}
          </Button>
          <Button disabled={create.isPending} onClick={() => void submit()}>
            {create.isPending ? tc("actions.loading") : tc("actions.create")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// =============================================================================
// The related post-sale records, as links into their own pages
// =============================================================================

function RelatedPanels({ policy }: { policy: Policy }) {
  const { t } = useTranslation("postsale");
  const endorsements = useEndorsements({ policy_id: policy.id, limit: 50 });
  const plans = useCollectionPlans({ policy_id: policy.id, limit: 20 });
  const claims = useClaims({ policy_id: policy.id, limit: 50 });

  return (
    <div className="grid grid-cols-1 gap-[18px] lg:grid-cols-3">
      <Section title={t("policy.detail.endorsements")}>
        {(endorsements.data?.items ?? []).length === 0 ? (
          <p className="text-caption text-text-muted">{t("policy.detail.noEndorsements")}</p>
        ) : (
          <ul className="flex flex-col gap-2">
            {(endorsements.data?.items ?? []).map((e) => (
              <li key={e.id}>
                <Link
                  to={`/endorsements/${e.id}`}
                  className="flex items-center justify-between gap-2 rounded-lg border border-line px-3 py-2 no-underline"
                >
                  <span className="min-w-0 truncate">
                    <span className="text-caption tabular-nums text-text-tertiary">
                      E{e.sequence_no}
                    </span>{" "}
                    {t(`endorsement.kind.${e.kind}`)}
                  </span>
                  <PostsaleBadge value={e.status} label={t(`endorsement.status.${e.status}`)} />
                </Link>
              </li>
            ))}
          </ul>
        )}
      </Section>

      <Section title={t("policy.detail.collection")}>
        {(plans.data?.items ?? []).length === 0 ? (
          <p className="text-caption text-text-muted">{t("policy.detail.noCollection")}</p>
        ) : (
          <ul className="flex flex-col gap-2">
            {(plans.data?.items ?? []).map((plan) => (
              <li key={plan.id}>
                <Link
                  to={`/collections/${plan.id}`}
                  className="flex items-center justify-between gap-2 rounded-lg border border-line px-3 py-2 no-underline"
                >
                  <span className="min-w-0 truncate">
                    {plan.plan_number ?? t("collection.detail.untitled")}
                    <span className="ml-1.5 text-caption text-text-muted">
                      {plan.installment_count ?? plan.installments.length}×
                    </span>
                  </span>
                  <PostsaleBadge
                    value={plan.status}
                    label={t(`collection.status.${plan.status}`)}
                  />
                </Link>
              </li>
            ))}
          </ul>
        )}
      </Section>

      <Section title={t("policy.detail.claims")}>
        {(claims.data?.items ?? []).length === 0 ? (
          <p className="text-caption text-text-muted">{t("policy.detail.noClaims")}</p>
        ) : (
          <ul className="flex flex-col gap-2">
            {(claims.data?.items ?? []).map((claim) => (
              <li key={claim.id}>
                <Link
                  to={`/claims/${claim.id}`}
                  className="flex items-center justify-between gap-2 rounded-lg border border-line px-3 py-2 no-underline"
                >
                  <span className="min-w-0 truncate">
                    {claim.claim_number ?? `#${claim.id}`}
                    <span className="ml-1.5 text-caption text-text-muted">
                      {formatDate(claim.event_date ?? claim.occurred_at)}
                    </span>
                  </span>
                  <PostsaleBadge value={claim.status} label={t(`claim.status.${claim.status}`)} />
                </Link>
              </li>
            ))}
          </ul>
        )}
      </Section>
    </div>
  );
}

// =============================================================================
// Page
// =============================================================================

export function PolicyDetailBody({ policyId }: { policyId: number }) {
  const id = policyId;
  const { t } = useTranslation("postsale");
  const { t: tc } = useTranslation("common");
  const navigate = useNavigate();

  const policy = usePolicy(Number.isFinite(id) ? id : undefined);
  const canEndorse = useCan("Endorsements", "Create");
  const canClaim = useCan("Claims", "Create");

  const [endorsementOpen, setEndorsementOpen] = React.useState(false);
  const [claimOpen, setClaimOpen] = React.useState(false);

  if (policy.isLoading) {
    return (
      <div className="flex flex-col gap-4">
        <Skeleton className="h-9 w-72" />
        <Skeleton className="h-40 w-full" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }

  if (policy.isError || !policy.data) {
    return (
      <>
        <PageHeader title={t("policy.title")} />
        <ErrorBanner error={policy.error ?? t("policy.detail.notFound")} />
        <Button variant="secondary" size="sm" onClick={() => navigate("/policies")}>
          <ArrowLeft className="h-4 w-4" />
          {tc("actions.back")}
        </Button>
      </>
    );
  }

  const p = policy.data;
  const remaining = daysBetween(new Date().toISOString(), p.period_end_at ?? p.end_date);

  return (
    <>
      <PageHeader
        eyebrow={
          <Link to="/policies" className="inline-flex items-center gap-1.5 no-underline">
            <ArrowLeft className="h-3.5 w-3.5" />
            {t("policy.title")}
          </Link>
        }
        title={t("policy.detail.heading", { number: p.policy_number })}
        subtitle={
          <span className="flex flex-wrap items-center gap-2">
            <PostsaleBadge value={p.status} label={t(`policy.status.${p.status}`)} />
            {p.insurer_name ? <Badge variant="brand">{p.insurer_name}</Badge> : null}
            {p.client_legal_name ? (
              <span className="text-text-muted">{p.client_legal_name}</span>
            ) : null}
            {p.cmf_policy_code ? <MonoChip>{p.cmf_policy_code}</MonoChip> : null}
          </span>
        }
        actions={
          <>
            {p.case_file_id ? (
              <Button asChild variant="secondary" size="sm">
                <Link to={`/cases/${p.case_file_id}`}>
                  <FolderOpen className="h-4 w-4" />
                  {t("policy.actions.openCase")}
                </Link>
              </Button>
            ) : null}

            <OpenSourceLink documentId={p.source_document_id} />

            <DisabledHint hint={canEndorse.allowed ? null : t("endorsement.noPermission")}>
              <Button
                size="sm"
                disabled={!canEndorse.allowed}
                onClick={() => setEndorsementOpen(true)}
              >
                <Plus className="h-4 w-4" />
                {t("policy.actions.newEndorsement")}
              </Button>
            </DisabledHint>

            <DisabledHint hint={canClaim.allowed ? null : t("claim.noPermission")}>
              <Button
                variant="secondary"
                size="sm"
                disabled={!canClaim.allowed}
                onClick={() => setClaimOpen(true)}
              >
                <Siren className="h-4 w-4" />
                {t("policy.actions.newClaim")}
              </Button>
            </DisabledHint>

            {/* Renewal case creation is API-only in this pass. */}
            <SoonButton reason={t("shared.soonRenewal")}>{t("policy.actions.renew")}</SoonButton>
          </>
        }
      />

      <NewEndorsementDialog
        policyId={p.id}
        open={endorsementOpen}
        onOpenChange={setEndorsementOpen}
      />
      <NewClaimDialog policyId={p.id} open={claimOpen} onOpenChange={setClaimOpen} />

      <Tabs defaultValue="summary">
        <TabsList variant="underline" className="flex-wrap">
          <TabsTrigger value="summary">{t("policy.detail.identity")}</TabsTrigger>
          <TabsTrigger value="postsale">{t("policy.detail.postSale")}</TabsTrigger>
          <TabsTrigger value="mirror">{t("mirror.title")}</TabsTrigger>
          <TabsTrigger value="warranties">{t("warranty.title")}</TabsTrigger>
        </TabsList>

        <TabsContent value="summary" className="mt-4 flex flex-col gap-[18px]">
          <FadeUp>
            <Section title={t("policy.detail.identity")} description={t("policy.noonHint")}>
              <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
                <KeyValue label={t("policy.fields.policyNumber")} value={p.policy_number} mono />
                <KeyValue label={t("policy.fields.insurer")} value={p.insurer_name ?? "—"} />
                <KeyValue label={t("policy.fields.client")} value={p.client_legal_name ?? "—"} />
                <KeyValue label={t("policy.fields.coverMode")} value={p.cover_mode ?? "—"} />
                <KeyValue
                  label={t("policy.fields.periodStart")}
                  value={
                    <DateTimeValue value={p.period_start_at} fallbackDate={p.start_date} />
                  }
                />
                <KeyValue
                  label={t("policy.fields.periodEnd")}
                  value={<DateTimeValue value={p.period_end_at} fallbackDate={p.end_date} />}
                />
                <KeyValue label={t("policy.fields.issuedAt")} value={formatDate(p.issued_at)} />
                <KeyValue
                  label={t("policy.fields.daysToExpiry")}
                  value={remaining === null ? "—" : t("shared.days", { count: remaining })}
                  tone={remaining !== null && remaining <= 30 ? "warn" : "default"}
                />
              </div>
            </Section>
          </FadeUp>

          <FadeUp delay={0.04}>
            <MoneyBlock policy={p} />
          </FadeUp>

          <FadeUp delay={0.08}>
            <Section title={t("policy.detail.terms")}>
              <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
                <KeyValue
                  label={t("policy.fields.insuredAmount")}
                  value={uf(p.insured_amount_uf, 0)}
                />
                <KeyValue
                  label={t("policy.fields.insuredAmountSemantics")}
                  value={p.insured_amount_semantics ?? "—"}
                />
                <KeyValue
                  label={t("policy.fields.aggregateLimit")}
                  value={uf(p.aggregate_limit_uf, 0)}
                />
                <KeyValue
                  label={t("policy.fields.averageRate")}
                  value={permille(p.average_rate_permille)}
                />
                <KeyValue
                  label={t("policy.fields.indemnityLimit")}
                  value={p.indemnity_limit ?? "—"}
                />
                <KeyValue
                  label={t("policy.fields.renewsPolicy")}
                  value={
                    p.renews_policy_id ? (
                      <Link to={`/policies/${p.renews_policy_id}`}>#{p.renews_policy_id}</Link>
                    ) : (
                      "—"
                    )
                  }
                />
                <KeyValue
                  label={t("policy.fields.sourceDocument")}
                  value={
                    p.source_document_id ? (
                      <span className="inline-flex items-center gap-1.5">
                        <FileText className="h-3.5 w-3.5" />#{p.source_document_id}
                      </span>
                    ) : (
                      "—"
                    )
                  }
                />
                <KeyValue
                  label={t("policy.fields.caseFile")}
                  value={
                    p.case_file_id ? (
                      <Link to={`/cases/${p.case_file_id}`}>#{p.case_file_id}</Link>
                    ) : (
                      "—"
                    )
                  }
                />
              </div>

              {p.deductibles ? (
                <div className="mt-4">
                  <div className="text-caption font-medium text-text-muted">
                    {t("policy.fields.deductibles")}
                  </div>
                  <p className="mt-1 text-body text-text-secondary">
                    {renderValue(p.deductibles)}
                  </p>
                </div>
              ) : null}

              {p.notes ? (
                <div className="mt-4">
                  <div className="text-caption font-medium text-text-muted">
                    {t("policy.fields.notes")}
                  </div>
                  <p className="mt-1 whitespace-pre-line text-body text-text-secondary">
                    {p.notes}
                  </p>
                </div>
              ) : null}
            </Section>
          </FadeUp>

          {/* v8 validate-then-dynamic: the FULL confirmed parse + core verdict. */}
          {p.payload || p.is_core_valid != null || p.source_document_id ? (
            <FadeUp delay={0.12}>
              <PolicyPayloadPanel policy={p} />
            </FadeUp>
          ) : null}

          <FadeUp delay={0.16}>
            <RelatedPanels policy={p} />
          </FadeUp>
        </TabsContent>

        <TabsContent value="postsale" className="mt-4 flex flex-col gap-[18px]">
          <SubFunnel policyId={p.id} />
          <RelatedPanels policy={p} />
        </TabsContent>

        <TabsContent value="mirror" className="mt-4">
          <MirrorDiffPanel policyId={p.id} />
        </TabsContent>

        <TabsContent value="warranties" className="mt-4">
          <WarrantyTracker policyId={p.id} />
        </TabsContent>
      </Tabs>

      {p.endorsements_count === 0 && p.claims_count === 0 ? (
        <Card>
          <EmptyState
            title={t("subFunnel.empty")}
            hint={t("subFunnel.emptyHint")}
            icon={<Receipt className="h-6 w-6" />}
            action={
              <Button asChild variant="ghost" size="sm">
                <Link to="/cases">
                  {t("policy.actions.openCase")}
                  <ArrowRight className="h-3.5 w-3.5" />
                </Link>
              </Button>
            }
          />
        </Card>
      ) : null}
    </>
  );
}
