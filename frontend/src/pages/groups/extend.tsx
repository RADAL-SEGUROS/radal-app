/**
 * `/groups/:groupId/policies/extend` — la prórroga.
 *
 * Rule 3 of the meeting: a prórroga is a MANUAL multi-select. Nothing is
 * pre-selected, ever — not the policies expiring soonest, not the ones the URL
 * mentions. The server takes explicit `policy_ids[]`, all inside one group, and
 * fans out one `endorsement(kind=period_extension)` plus one
 * `case_file(kind=endorsement)` per policy, sharing one `batch_key`.
 *
 * And it moves `policy.end_date` ONLY. Extending a policy is not the same act
 * as changing a vigencia: that one opens a folder
 * (`POST /case-files/{id}/reperiod`). The scope note on screen says so, because
 * the two are one wrong click apart.
 *
 * Two steps, because the server has two: CREATE writes the endorsements as
 * drafts and hands back the batch key; ISSUE applies the new end date to every
 * policy. Between them the broker can still walk away — nothing has moved.
 */
import * as React from "react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { Check, ShieldCheck, TimerReset } from "lucide-react";

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
} from "@/pages/proposals/shared";
import {
  GroupCrumbs,
  accountErrorMessage,
  addYears,
  periodDates,
  useGroupId,
} from "@/pages/groups/shared";
import { useAccountGroup, useGroupTree } from "@/api/accountGroups";
import { useBatchEndorsements, useIssueBatch } from "@/api/endorsements";
import { useCan } from "@/lib/permissions";
import { formatDate } from "@/lib/format";
import type {
  EndorsementBatchResponse,
  GroupTree,
  TreePolicyNode,
} from "@/api/types";

/** One row of the multi-select: a policy plus where it lives in the group. */
interface Candidate {
  policy: TreePolicyNode;
  periodLabel: string;
  lineName: string;
  isLatest: boolean;
}

function candidates(tree: GroupTree | undefined): Candidate[] {
  const rows: Candidate[] = [];
  for (const period of tree?.periods ?? []) {
    for (const line of period.lines) {
      for (const policy of line.policies) {
        rows.push({
          policy,
          periodLabel: period.label,
          lineName: line.name,
          isLatest: period.is_latest,
        });
      }
    }
  }
  return rows;
}

/** `2026-10-15` -> `2026-10-15T12:00` — the Chilean contractual noon. */
function noonOf(iso: string): string {
  return iso ? `${iso}T12:00` : "";
}

export default function ExtendPoliciesPage() {
  const { t } = useTranslation("accounts");
  const { t: tc } = useTranslation("common");
  const groupId = useGroupId();

  const group = useAccountGroup(groupId);
  const tree = useGroupTree(groupId);
  const create = useBatchEndorsements();
  const canEndorse = useCan("Endorsements", "Create");
  const canSubmitEndorsement = useCan("Endorsements", "Submit");

  const [selected, setSelected] = React.useState<number[]>([]);
  const [newEnd, setNewEnd] = React.useState("");
  const [effective, setEffective] = React.useState("");
  const [note, setNote] = React.useState("");
  const [batch, setBatch] = React.useState<EndorsementBatchResponse | null>(null);

  const issue = useIssueBatch(batch?.batch_key ?? "");
  const [issued, setIssued] = React.useState<EndorsementBatchResponse | null>(null);

  const rows = React.useMemo(() => candidates(tree.data), [tree.data]);

  const toggle = (id: number) =>
    setSelected((current) =>
      current.includes(id) ? current.filter((item) => item !== id) : [...current, id],
    );

  // The effect starts where the earliest selected policy currently ends: the
  // prórroga is a continuation, not a gap.
  const earliestEnd = React.useMemo(() => {
    const ends = rows
      .filter((row) => selected.includes(row.policy.id))
      .map((row) => row.policy.extended_end_date ?? row.policy.end_date)
      .filter((value): value is string => !!value)
      .sort();
    return ends[0] ?? "";
  }, [rows, selected]);

  React.useEffect(() => {
    if (!earliestEnd) return;
    setEffective((current) => current || noonOf(earliestEnd));
    setNewEnd((current) => current || addYears(earliestEnd, 1));
  }, [earliestEnd]);

  const datesValid = !!newEnd && !!effective && (!earliestEnd || newEnd > earliestEnd);
  const canCreate =
    canEndorse.allowed && selected.length > 0 && datesValid && !create.isPending;

  const submit = () => {
    if (!canCreate) return;
    create.mutate(
      {
        policy_ids: selected,
        kind: "period_extension",
        new_end_date: newEnd,
        effective_at: new Date(effective).toISOString(),
        note: note.trim() || null,
      },
      { onSuccess: (result) => setBatch(result) },
    );
  };

  const applyIssue = () => {
    if (!batch || !canSubmitEndorsement.allowed || issue.isPending) return;
    issue.mutate({}, { onSuccess: (result) => setIssued(result) });
  };

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        eyebrow={
          <GroupCrumbs
            items={[
              { label: t("group.title"), to: "/groups" },
              { label: group.data?.name ?? t("group.one"), to: `/groups/${groupId}` },
              { label: t("extend.title") },
            ]}
          />
        }
        title={t("extend.title")}
        subtitle={t("extend.subtitle")}
      />

      <Card className="flex gap-3 px-4 py-3">
        <TimerReset className="mt-0.5 h-4 w-4 shrink-0 text-brand" />
        <p className="text-caption text-text-secondary">{t("extend.scopeHint")}</p>
      </Card>

      {tree.isError ? <ErrorBanner error={tree.error} /> : null}
      {create.isError ? (
        <ErrorBanner error={accountErrorMessage(create.error, (k, o) => t(k, o))} />
      ) : null}
      {issue.isError ? (
        <ErrorBanner error={accountErrorMessage(issue.error, (k, o) => t(k, o))} />
      ) : null}

      {batch ? (
        <BatchResult
          batch={issued ?? batch}
          issued={!!issued}
          rows={rows}
          groupId={groupId}
          canIssue={canSubmitEndorsement.allowed}
          isIssuing={issue.isPending}
          onIssue={applyIssue}
        />
      ) : null}

      <FadeUp>
        <Section
          title={t("extend.selectPolicies")}
          description={t("extend.selectHint")}
          actions={
            <Badge variant={selected.length ? "brand" : "outline"}>
              {t("extend.selectedCount", { count: selected.length })}
            </Badge>
          }
        >
          {tree.isLoading ? (
            <Skeleton className="h-40 w-full" />
          ) : rows.length === 0 ? (
            <EmptyState
              icon={<ShieldCheck className="h-6 w-6" />}
              title={t("empty.policies")}
            />
          ) : (
            <ul className="flex flex-col gap-1.5">
              {rows.map((row) => {
                const checked = selected.includes(row.policy.id);
                const disabled = !!batch;
                return (
                  <li key={row.policy.id}>
                    <button
                      type="button"
                      disabled={disabled}
                      onClick={() => toggle(row.policy.id)}
                      className={cn(
                        "flex w-full flex-wrap items-center gap-2.5 rounded-lg border px-3.5 py-2.5 text-left transition-colors",
                        checked
                          ? "border-brand-line bg-brand-soft"
                          : "border-line hover:bg-bg-recessed",
                        disabled ? "cursor-not-allowed opacity-60" : "",
                      )}
                    >
                      <span
                        className={cn(
                          "flex h-4 w-4 shrink-0 items-center justify-center rounded border",
                          checked ? "border-brand bg-brand text-cta-foreground" : "border-line",
                        )}
                      >
                        {checked ? <Check className="h-3 w-3" /> : null}
                      </span>
                      <span className="text-label tabular-nums text-text-primary">
                        {row.policy.policy_number}
                      </span>
                      <span className="text-caption text-text-muted">
                        {row.policy.insurer_name ?? "—"}
                      </span>
                      <MonoChip>{row.periodLabel}</MonoChip>
                      <span className="text-caption text-text-muted">{row.lineName}</span>
                      {!row.isLatest ? (
                        <Badge variant="muted">{t("tree.historic")}</Badge>
                      ) : null}
                      <span className="ml-auto text-caption text-text-muted">
                        {row.policy.extended_end_date
                          ? t("tree.extendedUntil", {
                              date: formatDate(row.policy.extended_end_date),
                            })
                          : periodDates(row.policy.start_date, row.policy.end_date)}
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
          )}

          {selected.length === 0 ? (
            <p className="mt-3 text-caption text-signal-danger">{t("extend.none")}</p>
          ) : null}
        </Section>
      </FadeUp>

      {!batch ? (
        <FadeUp>
          <Card className="flex max-w-3xl flex-col gap-5 p-5">
            <div className="grid gap-4 sm:grid-cols-2">
              <div>
                <Label htmlFor="extend-end">{t("extend.newEndDate")}</Label>
                <Input
                  id="extend-end"
                  type="date"
                  value={newEnd}
                  onChange={(e) => setNewEnd(e.target.value)}
                  className="mt-1.5"
                />
              </div>
              <div>
                <Label htmlFor="extend-effective">{t("extend.effectiveAt")}</Label>
                <Input
                  id="extend-effective"
                  type="datetime-local"
                  value={effective}
                  onChange={(e) => setEffective(e.target.value)}
                  className="mt-1.5"
                />
              </div>
            </div>
            {!datesValid ? (
              <p className="text-caption text-signal-danger">{t("extend.datesInvalid")}</p>
            ) : (
              <p className="text-caption text-text-muted">{t("extend.datesHint")}</p>
            )}

            <div>
              <Label htmlFor="extend-note">{t("extend.note")}</Label>
              <Input
                id="extend-note"
                value={note}
                onChange={(e) => setNote(e.target.value)}
                className="mt-1.5"
              />
            </div>

            <div className="flex items-center justify-end gap-2 border-t border-line pt-4">
              <Button variant="secondary" size="sm" asChild>
                <Link to={`/groups/${groupId}`}>{tc("actions.cancel")}</Link>
              </Button>
              <DisabledHint hint={canEndorse.allowed ? null : t("extend.noPermission")}>
                <Button size="sm" onClick={submit} disabled={!canCreate}>
                  <TimerReset className="h-4 w-4" />
                  {create.isPending ? tc("actions.loading") : t("extend.submit")}
                </Button>
              </DisabledHint>
            </div>
          </Card>
        </FadeUp>
      ) : null}
    </div>
  );
}

// =============================================================================
// The batch, once it exists
// =============================================================================

function BatchResult({
  batch,
  issued,
  rows,
  groupId,
  canIssue,
  isIssuing,
  onIssue,
}: {
  batch: EndorsementBatchResponse;
  issued: boolean;
  rows: Candidate[];
  groupId: number;
  canIssue: boolean;
  isIssuing: boolean;
  onIssue: () => void;
}) {
  const { t } = useTranslation("accounts");
  const { t: tc } = useTranslation("common");

  const numberOf = (policyId: number) =>
    rows.find((row) => row.policy.id === policyId)?.policy.policy_number ?? `#${policyId}`;

  return (
    <FadeUp>
      <Section
        title={issued ? t("extend.issued") : t("extend.success", { count: batch.items.length })}
        description={issued ? undefined : t("extend.pendingIssue")}
        actions={
          issued ? (
            <Button variant="secondary" size="sm" asChild>
              <Link to={`/groups/${groupId}`}>{t("group.actions.open")}</Link>
            </Button>
          ) : (
            <DisabledHint hint={canIssue ? null : t("extend.noIssuePermission")}>
              <Button size="sm" onClick={onIssue} disabled={!canIssue || isIssuing}>
                {isIssuing ? tc("actions.loading") : t("extend.issue")}
              </Button>
            </DisabledHint>
          )
        }
      >
        <div className="mb-3 grid grid-cols-2 gap-4 sm:grid-cols-3">
          <KeyValue label={t("extend.batchKey")} value={<MonoChip>{batch.batch_key}</MonoChip>} />
          <KeyValue
            label={t("extend.newEndDate")}
            value={batch.new_end_date ? formatDate(batch.new_end_date) : "—"}
          />
          <KeyValue label={t("extend.selectPolicies")} value={batch.items.length} />
        </div>

        <ul className="divide-y divide-line">
          {batch.items.map((item) => (
            <li
              key={item.endorsement_id}
              className="flex flex-wrap items-center gap-2.5 py-2.5"
            >
              <ShieldCheck className="h-4 w-4 shrink-0 text-brand" />
              <Link
                to={`/groups/${groupId}/policies/${item.policy_id}`}
                className="text-label tabular-nums text-text-primary no-underline hover:text-brand-deep"
              >
                {numberOf(item.policy_id)}
              </Link>
              <Link
                to={`/endorsements/${item.endorsement_id}`}
                className="text-caption text-text-secondary no-underline hover:text-brand-deep"
              >
                {t("tree.endorsement")} #{item.endorsement_id}
              </Link>
              {item.case_file_id ? (
                <Link
                  to={`/cases/${item.case_file_id}`}
                  className="text-caption text-text-muted no-underline hover:text-brand-deep"
                >
                  {t("account.openCase")}
                </Link>
              ) : null}
              <span className="ml-auto text-caption text-text-muted">
                {item.policy_end_date
                  ? t("tree.extendedUntil", { date: formatDate(item.policy_end_date) })
                  : t("extend.pending")}
              </span>
            </li>
          ))}
        </ul>
      </Section>
    </FadeUp>
  );
}
