/**
 * `/groups/:groupId/accounts/:caseId/reperiod` — change the vigencia.
 *
 * Rule 1 of the meeting, and the one screen whose whole job is to say NO to an
 * edit: once an account has moved past `intake`, its dates are a folder, not a
 * field. `POST /case-files/{id}/reperiod` therefore opens a SIBLING folder
 * (`origin=period_change`, `origin_case_file_id=<this>`) with the placements,
 * the members and every antecedente copied, and CLOSES this one with
 * `meta.closed_reason="period_change"`.
 *
 * So this page is deliberately blunt: a warning that says what will happen to
 * the folder the broker is standing in, the two new dates, and a REQUIRED
 * reason — the reason is what the stage event carries, and six months later it
 * is the only thing that explains why the year has two folders.
 *
 * A folder that is not yet locked does not need this door at all; it says so
 * and links back, rather than quietly doing something heavier than asked.
 */
import * as React from "react";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { AlertTriangle, ArrowLeft, CalendarClock } from "lucide-react";

import { PageHeader } from "@/components/common/PageHeader";
import { FadeUp } from "@/components/common/motion";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { DisabledHint, ErrorBanner, MonoChip } from "@/pages/proposals/shared";
import {
  GroupCrumbs,
  OriginChip,
  StageBadge,
  accountErrorMessage,
  addYears,
  periodDates,
  todayIso,
  useCaseId,
  useGroupId,
} from "@/pages/groups/shared";
import { useAccountGroup } from "@/api/accountGroups";
import { useCaseFile, useReperiodCase } from "@/api/caseFiles";
import { useCan } from "@/lib/permissions";

export default function ReperiodAccountPage() {
  const { t } = useTranslation("accounts");
  const { t: tc } = useTranslation("common");
  const groupId = useGroupId();
  const caseId = useCaseId();
  const navigate = useNavigate();

  const group = useAccountGroup(groupId);
  const source = useCaseFile(caseId);
  const reperiod = useReperiodCase(caseId);
  const canCreate = useCan("CaseFiles", "Create");

  const [start, setStart] = React.useState("");
  const [end, setEnd] = React.useState("");
  const [reason, setReason] = React.useState("");
  const [touched, setTouched] = React.useState(false);

  React.useEffect(() => {
    if (touched) return;
    const c = source.data;
    if (!c) return;
    setStart(c.period_start ?? todayIso());
    setEnd(c.period_end ?? addYears(todayIso(), 1));
  }, [source.data, touched]);

  const c = source.data;
  const datesValid = !!start && !!end && start < end;
  const changed = !!c && (start !== (c.period_start ?? "") || end !== (c.period_end ?? ""));
  const reasonValid = reason.trim().length > 0;
  const canSubmit =
    canCreate.allowed && datesValid && changed && reasonValid && !reperiod.isPending;

  const submit = () => {
    if (!canSubmit) return;
    reperiod.mutate(
      { period_start: start, period_end: end, reason: reason.trim() },
      {
        onSuccess: (created) =>
          navigate(`/groups/${groupId}/accounts/${created.id}`, { replace: true }),
      },
    );
  };

  const back = () => navigate(`/groups/${groupId}/accounts/${caseId}`);

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
              { label: t("reperiod.title") },
            ]}
          />
        }
        title={t("reperiod.title")}
        subtitle={t("reperiod.subtitle")}
        actions={
          <Button variant="secondary" size="sm" onClick={back}>
            <ArrowLeft className="h-4 w-4" />
            {tc("actions.back")}
          </Button>
        }
      />

      {source.isError ? <ErrorBanner error={source.error} /> : null}
      {reperiod.isError ? (
        <ErrorBanner error={accountErrorMessage(reperiod.error, (k, o) => t(k, o))} />
      ) : null}

      <FadeUp>
        <Card className="flex max-w-3xl flex-col gap-5 p-5">
          <div className="flex gap-3 rounded-lg border border-warn-line bg-warn-soft px-4 py-3">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-warn-text" />
            <p className="text-caption text-text-secondary">{t("reperiod.warning")}</p>
          </div>

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
            {c && !c.period_locked ? (
              <p className="mt-2 text-caption text-text-muted">
                {t("reperiod.notLockedHint")}
              </p>
            ) : null}
          </div>

          <div className="grid gap-4 sm:grid-cols-2">
            <div>
              <Label htmlFor="reperiod-start">{t("reperiod.periodStart")}</Label>
              <Input
                id="reperiod-start"
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
              <Label htmlFor="reperiod-end">{t("reperiod.periodEnd")}</Label>
              <Input
                id="reperiod-end"
                type="date"
                value={end}
                onChange={(e) => {
                  setTouched(true);
                  setEnd(e.target.value);
                }}
                className="mt-1.5"
              />
            </div>
          </div>

          {!datesValid ? (
            <p className="text-caption text-signal-danger">
              {t("account.create.datesInvalid")}
            </p>
          ) : !changed ? (
            <p className="text-caption text-text-muted">{t("reperiod.unchanged")}</p>
          ) : null}

          <div>
            <Label htmlFor="reperiod-reason">{t("reperiod.reason")}</Label>
            <Textarea
              id="reperiod-reason"
              rows={3}
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder={t("reperiod.reasonPlaceholder")}
              className="mt-1.5"
            />
            {!reasonValid ? (
              <p className="mt-1 text-caption text-text-muted">{t("reperiod.reasonRequired")}</p>
            ) : null}
          </div>

          <div className="flex items-center justify-end gap-2 border-t border-line pt-4">
            <Button variant="secondary" size="sm" onClick={back} disabled={reperiod.isPending}>
              {tc("actions.cancel")}
            </Button>
            <DisabledHint hint={canCreate.allowed ? null : t("account.noCreatePermission")}>
              <Button size="sm" onClick={submit} disabled={!canSubmit}>
                <CalendarClock className="h-4 w-4" />
                {reperiod.isPending ? tc("actions.loading") : t("reperiod.submit")}
              </Button>
            </DisabledHint>
          </div>
        </Card>
      </FadeUp>
    </div>
  );
}
