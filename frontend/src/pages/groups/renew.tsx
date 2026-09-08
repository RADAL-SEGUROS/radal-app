/**
 * `/groups/:groupId/accounts/:caseId/renew` — open the next vigencia.
 *
 * Renewal is managed at RAMO-VIGENCIA level (rule 4): it is invoked on the
 * account folder, never on a policy, and it opens a SIBLING folder in the next
 * period — `kind=renewal, origin=renewal, origin_case_file_id=<this>` — with
 * every placement cloned as a draft. The source folder is left exactly as it
 * is; a renewal is not an edit of the year that ended.
 *
 * The history panel (`GET /case-files/{id}/history`) is context, never a
 * constraint: it shows the origin chain and the prior vigencia's antecedentes,
 * policies, claims and loss ratio so the broker can argue the renewal, and it
 * pre-fills nothing except the obvious default dates (the prior period's end,
 * plus a year). A folder with no prior renders "Sin vigencia anterior" rather
 * than an empty table pretending to be data.
 *
 * `copy_sections` copies documents as NEW rows on NEW storage keys — never two
 * rows on one key — which is why it is opt-in per section and defaults to the
 * antecedentes the next underwriter actually needs.
 */
import * as React from "react";
import { Link, useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { ArrowLeft, Check, FileText, RefreshCw, ShieldCheck, TrendingUp } from "lucide-react";

import { PageHeader } from "@/components/common/PageHeader";
import { FadeUp } from "@/components/common/motion";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";
import {
  DisabledHint,
  EmptyState,
  ErrorBanner,
  KeyValue,
  MonoChip,
  Section,
  pct,
  uf,
} from "@/pages/proposals/shared";
import {
  GroupCrumbs,
  OriginChip,
  StageBadge,
  accountErrorMessage,
  addYears,
  derivePeriodLabel,
  periodDates,
  todayIso,
  useCaseId,
  useGroupId,
} from "@/pages/groups/shared";
import { useAccountGroup } from "@/api/accountGroups";
import { useCaseFile, useCaseHistory, useRenewCase } from "@/api/caseFiles";
import { useCan } from "@/lib/permissions";
import { formatDate } from "@/lib/format";
import type { CaseHistory, CaseSection } from "@/api/types";

/** The sections a renewal can carry forward, in the order the folder is filed. */
const COPYABLE_SECTIONS: CaseSection[] = [
  "root_prospect",
  "submission",
  "insurer_quotes",
  "broker_proposal",
  "policy_file",
];

export default function RenewAccountPage() {
  const { t } = useTranslation("accounts");
  const { t: tc } = useTranslation("common");
  const groupId = useGroupId();
  const caseId = useCaseId();
  const navigate = useNavigate();

  const group = useAccountGroup(groupId);
  const source = useCaseFile(caseId);
  const history = useCaseHistory(caseId);
  const renew = useRenewCase(caseId);
  const canCreate = useCan("CaseFiles", "Create");

  const [start, setStart] = React.useState("");
  const [end, setEnd] = React.useState("");
  const [labelTouched, setLabelTouched] = React.useState(false);
  const [label, setLabel] = React.useState("");
  const [sections, setSections] = React.useState<CaseSection[]>(["root_prospect"]);
  const [touched, setTouched] = React.useState(false);

  // Default: the new vigencia starts the day the old one ends.
  React.useEffect(() => {
    if (touched) return;
    const c = source.data;
    if (!c) return;
    const nextStart = c.period_end ?? todayIso();
    setStart(nextStart);
    setEnd(addYears(nextStart, 1));
  }, [source.data, touched]);

  React.useEffect(() => {
    if (!labelTouched) setLabel(derivePeriodLabel(start, end));
  }, [start, end, labelTouched]);

  const toggleSection = (value: CaseSection) =>
    setSections((current) =>
      current.includes(value)
        ? current.filter((item) => item !== value)
        : [...current, value],
    );

  const datesValid = !!start && !!end && start < end;
  const canSubmit = canCreate.allowed && datesValid && !renew.isPending;

  const submit = () => {
    if (!canSubmit) return;
    renew.mutate(
      {
        period_start: start,
        period_end: end,
        period_label: label || null,
        copy_sections: sections,
      },
      {
        onSuccess: (created) =>
          navigate(`/groups/${groupId}/accounts/${created.id}`, { replace: true }),
      },
    );
  };

  const c = source.data;

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        eyebrow={
          <GroupCrumbs
            items={[
              { label: t("group.title"), to: "/groups" },
              { label: group.data?.name ?? t("group.one"), to: `/groups/${groupId}` },
              {
                label: c?.insurance_line_name ?? t("account.one"),
                to: `/groups/${groupId}/accounts/${caseId}`,
              },
              { label: t("renew.title") },
            ]}
          />
        }
        title={t("renew.title")}
        subtitle={t("renew.subtitle")}
        actions={
          <Button
            variant="secondary"
            size="sm"
            onClick={() => navigate(`/groups/${groupId}/accounts/${caseId}`)}
          >
            <ArrowLeft className="h-4 w-4" />
            {tc("actions.back")}
          </Button>
        }
      />

      {source.isError ? <ErrorBanner error={source.error} /> : null}
      {renew.isError ? (
        <ErrorBanner error={accountErrorMessage(renew.error, (k, o) => t(k, o))} />
      ) : null}

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        {/* --- The form ------------------------------------------------- */}
        <FadeUp>
          <Card className="flex flex-col gap-5 p-5">
            <div>
              <Label>{t("renew.source")}</Label>
              {source.isLoading ? (
                <Skeleton className="mt-1.5 h-6 w-56" />
              ) : c ? (
                <div className="mt-1.5 flex flex-wrap items-center gap-2">
                  <span className="text-body text-text-primary">
                    {c.insurance_line_name ?? t("tree.line")}
                  </span>
                  <MonoChip>{periodDates(c.period_start, c.period_end)}</MonoChip>
                  <OriginChip origin={c.origin} t={t} />
                  <StageBadge stage={c.stage} />
                </div>
              ) : null}
            </div>

            <div className="grid gap-4 sm:grid-cols-3">
              <div>
                <Label htmlFor="renew-start">{t("renew.periodStart")}</Label>
                <Input
                  id="renew-start"
                  type="date"
                  value={start}
                  onChange={(e) => {
                    setTouched(true);
                    setStart(e.target.value);
                  }}
                  className="mt-1.5"
                />
              </div>
              <div>
                <Label htmlFor="renew-end">{t("renew.periodEnd")}</Label>
                <Input
                  id="renew-end"
                  type="date"
                  value={end}
                  onChange={(e) => {
                    setTouched(true);
                    setEnd(e.target.value);
                  }}
                  className="mt-1.5"
                />
              </div>
              <div>
                <Label htmlFor="renew-label">{t("renew.periodLabel")}</Label>
                <Input
                  id="renew-label"
                  value={label}
                  onChange={(e) => {
                    setLabelTouched(true);
                    setLabel(e.target.value);
                  }}
                  className="mt-1.5"
                />
              </div>
            </div>
            <p
              className={cn(
                "text-caption",
                datesValid ? "text-text-muted" : "text-signal-danger",
              )}
            >
              {datesValid ? t("renew.periodLabelHint") : t("account.create.datesInvalid")}
            </p>

            <div>
              <Label>{t("renew.copySections")}</Label>
              <p className="mt-1 text-caption text-text-muted">
                {t("renew.copySectionsHint")}
              </p>
              <div className="mt-2 flex flex-wrap gap-2">
                {COPYABLE_SECTIONS.map((value) => {
                  const selected = sections.includes(value);
                  return (
                    <button
                      key={value}
                      type="button"
                      onClick={() => toggleSection(value)}
                      className={cn(
                        "flex items-center gap-1.5 rounded-lg border px-3 py-2 text-caption transition-colors",
                        selected
                          ? "border-brand-line bg-brand-soft text-brand-deep"
                          : "border-line text-text-secondary hover:bg-bg-recessed",
                      )}
                    >
                      {selected ? <Check className="h-3.5 w-3.5" /> : null}
                      {t(`renew.sections.${value}`)}
                    </button>
                  );
                })}
              </div>
            </div>

            <p className="text-caption text-text-muted">{t("renew.clientsHint")}</p>

            <div className="flex items-center justify-end gap-2 border-t border-line pt-4">
              <Button
                variant="secondary"
                size="sm"
                disabled={renew.isPending}
                onClick={() => navigate(`/groups/${groupId}/accounts/${caseId}`)}
              >
                {tc("actions.cancel")}
              </Button>
              <DisabledHint hint={canCreate.allowed ? null : t("account.noCreatePermission")}>
                <Button size="sm" onClick={submit} disabled={!canSubmit}>
                  <RefreshCw className="h-4 w-4" />
                  {renew.isPending ? tc("actions.loading") : t("renew.submit")}
                </Button>
              </DisabledHint>
            </div>
          </Card>
        </FadeUp>

        {/* --- The history it argues from ------------------------------- */}
        <HistoryPanel history={history.data} isLoading={history.isLoading} error={history.error} />
      </div>
    </div>
  );
}

// =============================================================================
// History — read-only context
// =============================================================================

export function HistoryPanel({
  history,
  isLoading,
  error,
}: {
  history: CaseHistory | undefined;
  isLoading: boolean;
  error: unknown;
}) {
  const { t } = useTranslation("accounts");

  if (isLoading) return <Skeleton className="h-72 w-full" />;
  if (error) return <ErrorBanner error={error} />;

  const chain = history?.origin_chain ?? [];
  const prior = history?.prior ?? null;

  return (
    <div className="flex flex-col gap-4">
      <Section title={t("history.originChain")}>
        {chain.length === 0 ? (
          <p className="text-caption text-text-muted">{t("history.empty")}</p>
        ) : (
          <ol className="flex flex-col gap-2">
            {chain.map((entry) => (
              <li key={entry.case_file_id} className="flex flex-wrap items-center gap-2">
                <Link
                  to={`/cases/${entry.case_file_id}`}
                  className="text-label tabular-nums text-text-primary no-underline hover:text-brand-deep"
                >
                  {entry.reference ?? `#${entry.case_file_id}`}
                </Link>
                {entry.period_label ? <MonoChip>{entry.period_label}</MonoChip> : null}
                <OriginChip origin={entry.origin} t={t} />
                <StageBadge stage={entry.stage} />
              </li>
            ))}
          </ol>
        )}
      </Section>

      {!prior ? (
        <Card>
          <EmptyState
            icon={<FileText className="h-6 w-6" />}
            title={t("renew.noPrior")}
            hint={t("renew.noPriorHint")}
          />
        </Card>
      ) : (
        <>
          <Section
            title={t("history.prior")}
            actions={
              <Button variant="secondary" size="sm" asChild>
                <Link to={`/cases/${prior.case_file_id}`}>{t("account.openCase")}</Link>
              </Button>
            }
          >
            <div className="grid grid-cols-2 gap-4 sm:grid-cols-3">
              <KeyValue
                label={t("history.records")}
                value={Object.values(prior.records).reduce(
                  (total, rows) => total + rows.length,
                  0,
                )}
              />
              <KeyValue label={t("history.policies")} value={prior.policies.length} />
              <KeyValue
                label={t("history.lossRatio")}
                value={prior.loss_ratio_pct ? pct(prior.loss_ratio_pct) : "—"}
              />
            </div>
          </Section>

          {Object.keys(prior.records).length > 0 ? (
            <Section title={t("history.records")}>
              <div className="flex flex-col gap-3">
                {Object.entries(prior.records).map(([key, rows]) => (
                  <div key={key}>
                    <div className="flex items-center gap-2">
                      <Badge variant={rows.length ? "neutral" : "outline"}>
                        {t(`folders.${key}`, { defaultValue: key })}
                      </Badge>
                      <span className="text-caption tabular-nums text-text-muted">
                        {rows.length}
                      </span>
                    </div>
                    {rows.length > 0 ? (
                      <ul className="mt-1 pl-1">
                        {rows.map((doc) => (
                          <li key={doc.id} className="truncate text-caption text-text-secondary">
                            {doc.original_name}
                          </li>
                        ))}
                      </ul>
                    ) : null}
                  </div>
                ))}
              </div>
            </Section>
          ) : null}

          {prior.policies.length > 0 ? (
            <Section title={t("history.policies")}>
              <ul className="flex flex-col gap-2">
                {prior.policies.map((policy) => (
                  <li key={policy.id} className="flex flex-wrap items-center gap-2">
                    <ShieldCheck className="h-4 w-4 shrink-0 text-brand" />
                    <span className="text-label tabular-nums text-text-primary">
                      {policy.policy_number}
                    </span>
                    <span className="text-caption text-text-muted">
                      {policy.insurer_name ?? "—"}
                    </span>
                    <span className="ml-auto text-caption tabular-nums text-text-muted">
                      {uf(policy.total_premium_uf)}
                    </span>
                  </li>
                ))}
              </ul>
            </Section>
          ) : null}

          {prior.claims.length > 0 ? (
            <Section title={t("history.claims")}>
              <ul className="flex flex-col gap-2">
                {prior.claims.map((claim) => (
                  <li key={claim.id} className="flex flex-wrap items-center gap-2">
                    <TrendingUp className="h-4 w-4 shrink-0 text-warn" />
                    <span className="text-label tabular-nums text-text-primary">
                      {claim.claim_number ?? `#${claim.id}`}
                    </span>
                    <span className="text-caption text-text-muted">
                      {claim.event_date ? formatDate(claim.event_date) : "—"}
                    </span>
                    <span className="ml-auto text-caption tabular-nums text-text-muted">
                      {uf(claim.paid_amount_uf ?? claim.settled_amount_uf)}
                    </span>
                  </li>
                ))}
              </ul>
            </Section>
          ) : null}
        </>
      )}
    </div>
  );
}
