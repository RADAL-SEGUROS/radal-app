/**
 * Collection plan detail.
 *
 * Three blocks, all backed by real endpoints:
 *   - the plan header            -> GET /collections/{id}
 *   - the derived dashboard      -> GET /collections/{id}/status
 *     (outstanding, overdue, compliance %, alerts and the `balances` flag)
 *   - the ledger and art. 528    -> the two components next to this file.
 *
 * The plan is where non-payment becomes a coverage problem, so the status
 * chips are not decoration: `suspended` / `terminated` mean the policy is not
 * responding right now.
 */
import { Link, useNavigate, useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { AlertTriangle, ArrowLeft, FolderOpen, Percent, Wallet } from "lucide-react";

import { PageHeader } from "@/components/common/PageHeader";
import { KpiCard } from "@/components/common/KpiCard";
import { FadeUp, Stagger } from "@/components/common/motion";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { formatDate, formatUF } from "@/lib/format";
import { useCollectionPlan, useCollectionStatus } from "@/api/collections";
import { usePolicy } from "@/api/policies";
import { num } from "@/api/types";
import {
  ErrorBanner,
  KeyValue,
  MonoChip,
  Section,
  SoonButton,
  pct,
  uf,
} from "@/pages/proposals/shared";
import { PostsaleBadge } from "@/pages/policies/shared";
import { InstallmentLedger } from "@/pages/collections/InstallmentLedger";
import { Art528Timeline } from "@/pages/collections/Art528Timeline";

export default function CollectionDetailPage() {
  const { planId } = useParams<{ planId: string }>();
  const id = Number(planId);
  const { t } = useTranslation("postsale");
  const { t: tc } = useTranslation("common");
  const navigate = useNavigate();

  const plan = useCollectionPlan(Number.isFinite(id) ? id : undefined);
  const status = useCollectionStatus(Number.isFinite(id) ? id : undefined);
  const policy = usePolicy(plan.data?.policy_id);

  if (plan.isLoading) {
    return (
      <div className="flex flex-col gap-4">
        <Skeleton className="h-9 w-72" />
        <Skeleton className="h-40 w-full" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }

  if (plan.isError || !plan.data) {
    return (
      <>
        <PageHeader title={t("collection.title")} />
        <ErrorBanner error={plan.error ?? t("collection.detail.notFound")} />
        <Button variant="secondary" size="sm" onClick={() => navigate("/policies")}>
          <ArrowLeft className="h-4 w-4" />
          {tc("actions.back")}
        </Button>
      </>
    );
  }

  const p = plan.data;
  const s = status.data;

  return (
    <>
      <PageHeader
        eyebrow={
          <Link
            to={`/policies/${p.policy_id}`}
            className="inline-flex items-center gap-1.5 no-underline"
          >
            <ArrowLeft className="h-3.5 w-3.5" />
            {policy.data?.policy_number ?? t("collection.fields.policy")}
          </Link>
        }
        title={
          p.plan_number
            ? t("collection.detail.heading", { number: p.plan_number })
            : t("collection.detail.untitled")
        }
        subtitle={
          <span className="flex flex-wrap items-center gap-2">
            <PostsaleBadge value={p.status} label={t(`collection.status.${p.status}`)} />
            <Badge variant="action">{t(`collection.paymentMode.${p.payment_mode}`)}</Badge>
            {p.as_of_date ? (
              <span className="text-text-muted">
                {t("collection.fields.asOfDate")} {formatDate(p.as_of_date)}
              </span>
            ) : null}
          </span>
        }
        actions={
          <>
            {p.case_file_id ? (
              <Button asChild variant="secondary" size="sm">
                <Link to={`/cases/${p.case_file_id}`}>
                  <FolderOpen className="h-4 w-4" />
                  {t("collection.fields.caseFile")}
                </Link>
              </Button>
            ) : null}
            <SoonButton reason={t("shared.soonExport")}>{tc("actions.export")}</SoonButton>
          </>
        }
      />

      <ErrorBanner error={status.error} />

      <Stagger className="grid grid-cols-2 gap-[18px] lg:grid-cols-5">
        <KpiCard
          label={t("collection.kpi.scheduled")}
          countTo={num(s?.total_scheduled_uf ?? null) ?? 0}
          format={(n) => formatUF(n, { decimals: 1 })}
          icon={<Wallet />}
          tone="brand"
        />
        <KpiCard
          label={t("collection.kpi.paid")}
          countTo={num(s?.paid_uf ?? null) ?? 0}
          format={(n) => formatUF(n, { decimals: 1 })}
          icon={<Wallet />}
          tone="success"
        />
        <KpiCard
          label={t("collection.kpi.outstanding")}
          countTo={num(s?.outstanding_uf ?? null) ?? 0}
          format={(n) => formatUF(n, { decimals: 1 })}
          icon={<Wallet />}
          tone="action"
        />
        <KpiCard
          label={t("collection.kpi.overdue")}
          hint={
            s ? t("collection.kpi.overdueCount", { count: s.overdue_count }) : undefined
          }
          countTo={num(s?.overdue_uf ?? null) ?? 0}
          format={(n) => formatUF(n, { decimals: 1 })}
          icon={<AlertTriangle />}
          tone="danger"
        />
        <KpiCard
          label={t("collection.kpi.compliance")}
          hint={
            s?.next_due_date
              ? `${t("collection.kpi.nextDue")}: ${formatDate(s.next_due_date)}`
              : undefined
          }
          countTo={num(s?.compliance_pct ?? null) ?? 0}
          format={(n) => `${n.toFixed(0)} %`}
          icon={<Percent />}
          tone="warn"
        />
      </Stagger>

      <FadeUp>
        <Section title={t("collection.detail.plan")}>
          <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
            <KeyValue
              label={t("collection.fields.planNumber")}
              value={p.plan_number ? <MonoChip>{p.plan_number}</MonoChip> : "—"}
            />
            <KeyValue
              label={t("collection.fields.policy")}
              value={
                <Link to={`/policies/${p.policy_id}`}>
                  {policy.data?.policy_number ?? `#${p.policy_id}`}
                </Link>
              }
            />
            <KeyValue
              label={t("collection.fields.paymentMode")}
              value={t(`collection.paymentMode.${p.payment_mode}`)}
            />
            <KeyValue
              label={t("collection.fields.installmentCount")}
              value={p.installment_count ?? p.installments.length}
            />
            <KeyValue
              label={t("collection.fields.totalPremium")}
              value={uf(p.total_premium_uf)}
            />
            <KeyValue label={t("collection.fields.bank")} value={p.bank ?? "—"} />
            <KeyValue
              label={t("collection.fields.accountNumber")}
              value={p.account_number ?? "—"}
              mono
            />
            <KeyValue
              label={t("collection.fields.interestRate")}
              value={pct(p.monthly_interest_rate_pct)}
            />
          </div>

          {p.management_note ? (
            <div className="mt-4">
              <div className="text-caption font-medium text-ink-3">
                {t("collection.detail.management")}
              </div>
              <p className="mt-1 whitespace-pre-line text-body text-text-secondary">
                {p.management_note}
              </p>
            </div>
          ) : null}
        </Section>
      </FadeUp>

      <FadeUp delay={0.04}>
        <Section title={t("collection.alerts")}>
          {(s?.alerts?.length ?? 0) === 0 ? (
            <p className="text-caption text-text-muted">{t("collection.noAlerts")}</p>
          ) : (
            <ul className="flex flex-col gap-2">
              {(s?.alerts ?? []).map((alert, index) => (
                <li
                  key={`${alert.code}-${index}`}
                  className="flex flex-wrap items-center gap-2 rounded-lg border border-line px-3 py-2"
                >
                  <PostsaleBadge
                    value={alert.severity}
                    label={t(`shared.severity.${alert.severity}`, {
                      defaultValue: alert.severity,
                    })}
                  />
                  <MonoChip>{alert.code}</MonoChip>
                  <span className="min-w-0 flex-1 text-body text-text-secondary">
                    {alert.detail}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </Section>
      </FadeUp>

      <FadeUp delay={0.08}>
        <InstallmentLedger planId={p.id} policyId={p.policy_id} />
      </FadeUp>

      <FadeUp delay={0.12}>
        <Art528Timeline plan={p} />
      </FadeUp>

      {!policy.data ? (
        <Card className="p-4 text-caption text-text-muted">{t("shared.loading")}</Card>
      ) : null}
    </>
  );
}
